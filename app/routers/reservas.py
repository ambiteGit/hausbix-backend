import os
from datetime import datetime, timedelta, date as date_cls

import stripe
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import or_

from .. import models, schemas
from ..database import get_db
from ..auth import usuario_actual
from ..push import enviar_push

router = APIRouter(tags=["reservas"])

stripe.api_key = os.environ.get("STRIPE_SECRET_KEY")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET")
COMISION_HAUSBIX_USD = 10.0


def _verificar_stripe_configurado():
    if not stripe.api_key:
        raise HTTPException(
            status_code=503,
            detail="Los pagos todavía no están configurados en el servidor (falta STRIPE_SECRET_KEY).",
        )


def _obtener_precio_noche(inmueble: models.Inmueble, fecha: date_cls) -> float:
    """Precio de una noche concreta: el que tenga puesto ese día en el
    calendario de disponibilidad si existe, si no el precio base de la
    operación de alquiler_temporal."""
    dia = next((d for d in inmueble.disponibilidad if d.fecha == fecha), None)
    if dia and dia.precio is not None:
        return dia.precio
    operacion = next((o for o in inmueble.operaciones if o.tipo_operacion == models.TipoOperacion.alquiler_temporal), None)
    if not operacion or operacion.precio is None:
        raise HTTPException(status_code=400, detail="Este inmueble no tiene precio de alquiler por fechas configurado")
    return operacion.precio


def _a_reserva_out(r: models.Reserva, usuario_id: str) -> schemas.ReservaOut:
    otro = r.propietario if usuario_id == r.huesped_id else r.huesped
    return schemas.ReservaOut(
        id=r.id, inmueble_id=r.inmueble_id, huesped_id=r.huesped_id, propietario_id=r.propietario_id,
        fecha_entrada=r.fecha_entrada, fecha_salida=r.fecha_salida, precio_noche=r.precio_noche,
        comision_hausbix_usd=r.comision_hausbix_usd, estado=r.estado.value,
        fecha_creacion=r.fecha_creacion, fecha_resolucion=r.fecha_resolucion,
        inmueble_titulo=r.inmueble.titulo if r.inmueble else None,
        inmueble_foto=r.inmueble.fotos[0].url if r.inmueble and r.inmueble.fotos else None,
        otro_nombre=otro.nombre if otro else None,
    )


def _cargo_de_payment_intent(payment_intent_id: str) -> str:
    """El Transfer necesita el ID del Charge (cobro), no del PaymentIntent."""
    intent = stripe.PaymentIntent.retrieve(payment_intent_id)
    return intent.latest_charge


def _marcar_disponibilidad(db: Session, inmueble_id: str, fecha: date_cls, estado: models.EstadoDisponibilidad):
    dia = db.query(models.Disponibilidad).filter_by(inmueble_id=inmueble_id, fecha=fecha).first()
    if dia:
        dia.estado = estado
    else:
        db.add(models.Disponibilidad(inmueble_id=inmueble_id, fecha=fecha, estado=estado))


@router.post("/reservas", response_model=schemas.ReservaPagoOut, status_code=201)
def crear_reserva(
    payload: schemas.ReservaCrear,
    db: Session = Depends(get_db),
    usuario: models.Usuario = Depends(usuario_actual),
):
    """
    Crea la reserva y un PaymentIntent de Stripe por el precio de la noche
    (importe único — los 10$ de gestión van incluidos dentro, no aparte).
    El dinero entra en la cuenta de Stripe de Hausbix; todavía NO se
    transfiere nada al propietario hasta que confirme (ver /confirmar).
    """
    _verificar_stripe_configurado()

    inmueble = (
        db.query(models.Inmueble)
        .options(joinedload(models.Inmueble.operaciones), joinedload(models.Inmueble.disponibilidad), joinedload(models.Inmueble.fotos))
        .filter_by(id=payload.inmueble_id)
        .first()
    )
    if not inmueble:
        raise HTTPException(status_code=404, detail="Inmueble no encontrado")
    if inmueble.usuario_id == usuario.id:
        raise HTTPException(status_code=400, detail="No puedes reservar tu propio anuncio")

    ocupado = any(
        d.fecha == payload.fecha_entrada and d.estado == models.EstadoDisponibilidad.ocupado
        for d in inmueble.disponibilidad
    )
    if ocupado:
        raise HTTPException(status_code=409, detail="Esa fecha ya no está disponible")

    precio_noche = _obtener_precio_noche(inmueble, payload.fecha_entrada)
    if precio_noche <= COMISION_HAUSBIX_USD:
        raise HTTPException(status_code=400, detail="El precio de la noche es demasiado bajo para cubrir los gastos de gestión")

    reserva = models.Reserva(
        inmueble_id=inmueble.id,
        huesped_id=usuario.id,
        propietario_id=inmueble.usuario_id,
        fecha_entrada=payload.fecha_entrada,
        fecha_salida=payload.fecha_salida,
        precio_noche=precio_noche,
        comision_hausbix_usd=COMISION_HAUSBIX_USD,
    )
    db.add(reserva)
    db.flush()

    intent = stripe.PaymentIntent.create(
        amount=round(precio_noche * 100),  # Stripe usa céntimos
        currency="usd",
        metadata={"reserva_id": reserva.id},
        description=f"Reserva Hausbix — {inmueble.titulo} ({payload.fecha_entrada})",
    )
    reserva.stripe_payment_intent_id = intent.id
    db.commit()
    db.refresh(reserva)

    return schemas.ReservaPagoOut(reserva=_a_reserva_out(reserva, usuario.id), client_secret=intent.client_secret)


@router.get("/reservas", response_model=list[schemas.ReservaOut])
def listar_mis_reservas(db: Session = Depends(get_db), usuario: models.Usuario = Depends(usuario_actual)):
    reservas = (
        db.query(models.Reserva)
        .options(
            joinedload(models.Reserva.inmueble).joinedload(models.Inmueble.fotos),
            joinedload(models.Reserva.huesped),
            joinedload(models.Reserva.propietario),
        )
        .filter(or_(models.Reserva.huesped_id == usuario.id, models.Reserva.propietario_id == usuario.id))
        .order_by(models.Reserva.fecha_creacion.desc())
        .all()
    )
    return [_a_reserva_out(r, usuario.id) for r in reservas]


@router.post("/reservas/{reserva_id}/confirmar", response_model=schemas.ReservaOut)
def confirmar_reserva(reserva_id: str, db: Session = Depends(get_db), usuario: models.Usuario = Depends(usuario_actual)):
    _verificar_stripe_configurado()
    reserva = db.query(models.Reserva).filter_by(id=reserva_id).first()
    if not reserva:
        raise HTTPException(status_code=404, detail="Reserva no encontrada")
    if reserva.propietario_id != usuario.id:
        raise HTTPException(status_code=403, detail="Solo el propietario puede confirmar esta reserva")
    if reserva.estado != models.EstadoReserva.pendiente_confirmacion:
        raise HTTPException(status_code=400, detail="Esta reserva no está pendiente de confirmación")
    if not usuario.stripe_account_id or not usuario.stripe_onboarding_completo:
        raise HTTPException(status_code=400, detail="Antes de confirmar, completa el registro de cobros (Stripe) en tu perfil")

    monto_propietario = round((reserva.precio_noche - reserva.comision_hausbix_usd) * 100)
    transferencia = stripe.Transfer.create(
        amount=monto_propietario,
        currency="usd",
        destination=usuario.stripe_account_id,
        source_transaction=_cargo_de_payment_intent(reserva.stripe_payment_intent_id),
        metadata={"reserva_id": reserva.id},
    )
    reserva.stripe_transfer_id = transferencia.id
    reserva.estado = models.EstadoReserva.confirmada
    reserva.fecha_resolucion = datetime.utcnow()
    db.commit()
    db.refresh(reserva)

    enviar_push(reserva.huesped.push_token, "¡Reserva confirmada!", f"Tu reserva en {reserva.inmueble.titulo} ha sido confirmada.")
    return _a_reserva_out(reserva, usuario.id)


@router.post("/reservas/{reserva_id}/rechazar", response_model=schemas.ReservaOut)
def rechazar_reserva(reserva_id: str, db: Session = Depends(get_db), usuario: models.Usuario = Depends(usuario_actual)):
    _verificar_stripe_configurado()
    reserva = db.query(models.Reserva).filter_by(id=reserva_id).first()
    if not reserva:
        raise HTTPException(status_code=404, detail="Reserva no encontrada")
    if reserva.propietario_id != usuario.id:
        raise HTTPException(status_code=403, detail="Solo el propietario puede rechazar esta reserva")
    if reserva.estado != models.EstadoReserva.pendiente_confirmacion:
        raise HTTPException(status_code=400, detail="Esta reserva no está pendiente de confirmación")

    reembolso = stripe.Refund.create(
        payment_intent=reserva.stripe_payment_intent_id,
        amount=round(reserva.precio_noche * 100),  # rechazo del propietario → reembolso ÍNTEGRO, no es culpa del huésped
    )
    reserva.stripe_refund_id = reembolso.id
    reserva.estado = models.EstadoReserva.rechazada
    reserva.fecha_resolucion = datetime.utcnow()
    _marcar_disponibilidad(db, reserva.inmueble_id, reserva.fecha_entrada, models.EstadoDisponibilidad.libre)
    db.commit()
    db.refresh(reserva)

    enviar_push(reserva.huesped.push_token, "Reserva rechazada", f"Tu reserva en {reserva.inmueble.titulo} no fue aceptada. Se te ha reembolsado el importe completo.")
    return _a_reserva_out(reserva, usuario.id)


@router.post("/reservas/{reserva_id}/cancelar", response_model=schemas.ReservaOut)
def cancelar_reserva(reserva_id: str, db: Session = Depends(get_db), usuario: models.Usuario = Depends(usuario_actual)):
    """
    Cancela el huésped. Con 24h o más de antelación sobre la fecha de
    entrada, se le devuelve la noche (menos los 10$ de gestión, que nunca
    se devuelven). Con menos de 24h, o si ya pasó la fecha, no hay
    reembolso de nada — el propietario recibe el importe íntegro (ya con
    los 10$ descontados) como compensación.
    """
    _verificar_stripe_configurado()
    reserva = db.query(models.Reserva).filter_by(id=reserva_id).first()
    if not reserva:
        raise HTTPException(status_code=404, detail="Reserva no encontrada")
    if reserva.huesped_id != usuario.id:
        raise HTTPException(status_code=403, detail="Solo quien reservó puede cancelar")
    if reserva.estado != models.EstadoReserva.pendiente_confirmacion:
        raise HTTPException(status_code=400, detail="Esta reserva no se puede cancelar en su estado actual")

    limite_cancelacion = datetime.combine(reserva.fecha_entrada, datetime.min.time()) - timedelta(hours=24)
    con_reembolso = datetime.utcnow() < limite_cancelacion

    if con_reembolso:
        reembolso = stripe.Refund.create(
            payment_intent=reserva.stripe_payment_intent_id,
            amount=round((reserva.precio_noche - reserva.comision_hausbix_usd) * 100),
        )
        reserva.stripe_refund_id = reembolso.id
        reserva.estado = models.EstadoReserva.cancelada_con_reembolso
        _marcar_disponibilidad(db, reserva.inmueble_id, reserva.fecha_entrada, models.EstadoDisponibilidad.libre)
    else:
        propietario = db.query(models.Usuario).filter_by(id=reserva.propietario_id).first()
        if propietario and propietario.stripe_account_id and propietario.stripe_onboarding_completo:
            transferencia = stripe.Transfer.create(
                amount=round((reserva.precio_noche - reserva.comision_hausbix_usd) * 100),
                currency="usd",
                destination=propietario.stripe_account_id,
                source_transaction=_cargo_de_payment_intent(reserva.stripe_payment_intent_id),
                metadata={"reserva_id": reserva.id},
            )
            reserva.stripe_transfer_id = transferencia.id
        reserva.estado = models.EstadoReserva.cancelada_sin_reembolso
        # No se libera la fecha — se canceló demasiado tarde para volver a alquilarla con normalidad.

    reserva.fecha_resolucion = datetime.utcnow()
    db.commit()
    db.refresh(reserva)
    return _a_reserva_out(reserva, usuario.id)


@router.post("/stripe/webhook", include_in_schema=False)
async def stripe_webhook(request: Request, db: Session = Depends(get_db)):
    """
    Recibe la confirmación de pago de Stripe. Configúralo en el dashboard
    de Stripe apuntando a https://tu-backend/stripe/webhook, evento
    payment_intent.succeeded. STRIPE_WEBHOOK_SECRET debe coincidir con el
    "Signing secret" que te da Stripe para ese endpoint.
    """
    payload = await request.body()
    firma = request.headers.get("stripe-signature")
    try:
        evento = stripe.Webhook.construct_event(payload, firma, STRIPE_WEBHOOK_SECRET)
    except (ValueError, stripe.SignatureVerificationError):
        raise HTTPException(status_code=400, detail="Firma de webhook inválida")

    if evento["type"] == "payment_intent.succeeded":
        intent = evento["data"]["object"]
        reserva_id = intent.get("metadata", {}).get("reserva_id")
        reserva = db.query(models.Reserva).filter_by(id=reserva_id).first() if reserva_id else None
        if reserva and reserva.estado == models.EstadoReserva.pendiente_pago:
            reserva.estado = models.EstadoReserva.pendiente_confirmacion
            _marcar_disponibilidad(db, reserva.inmueble_id, reserva.fecha_entrada, models.EstadoDisponibilidad.ocupado)
            db.commit()

            propietario = db.query(models.Usuario).filter_by(id=reserva.propietario_id).first()
            if propietario:
                enviar_push(propietario.push_token, "Nueva reserva pendiente", "Tienes una reserva esperando tu confirmación.")

    return {"ok": True}


@router.post("/stripe/onboarding-link", response_model=schemas.StripeOnboardingOut)
def crear_enlace_onboarding(db: Session = Depends(get_db), usuario: models.Usuario = Depends(usuario_actual)):
    """
    Genera el enlace de onboarding de Stripe Connect Express para que el
    propietario configure cómo cobrar sus reservas. Necesario antes de
    poder confirmar ninguna reserva.
    """
    _verificar_stripe_configurado()

    if not usuario.stripe_account_id:
        cuenta = stripe.Account.create(type="express", email=usuario.email)
        usuario.stripe_account_id = cuenta.id
        db.commit()

    frontend_url = os.environ.get("FRONTEND_URL", "https://hausbix.com")
    enlace = stripe.AccountLink.create(
        account=usuario.stripe_account_id,
        refresh_url=f"{frontend_url}/stripe-onboarding-refresh",
        return_url=f"{frontend_url}/stripe-onboarding-listo",
        type="account_onboarding",
    )
    return schemas.StripeOnboardingOut(url=enlace.url)


@router.get("/stripe/estado-onboarding")
def estado_onboarding(db: Session = Depends(get_db), usuario: models.Usuario = Depends(usuario_actual)):
    _verificar_stripe_configurado()
    if not usuario.stripe_account_id:
        return {"completo": False}

    cuenta = stripe.Account.retrieve(usuario.stripe_account_id)
    completo = bool(cuenta.charges_enabled and cuenta.payouts_enabled)
    if completo != usuario.stripe_onboarding_completo:
        usuario.stripe_onboarding_completo = completo
        db.commit()
    return {"completo": completo}
