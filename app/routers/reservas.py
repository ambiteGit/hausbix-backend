import os
from datetime import datetime, timedelta, date as date_cls

import stripe
import mercadopago
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import or_

from .. import models, schemas
from ..database import get_db
from ..auth import usuario_actual
from ..push import enviar_push
from .mercadopago import _verificar_mercadopago_configurado

router = APIRouter(tags=["reservas"])

stripe.api_key = os.environ.get("STRIPE_SECRET_KEY")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET")
COMISION_PRIMERA_NOCHE_USD = 9.99  # el resto lo cobra el propietario directamente al llegar
COMISION_PAGO_COMPLETO_USD = 11.99  # se paga toda la estancia por adelantado, con 10% de descuento

# Mercado Pago exige una moneda concreta según el país de la cuenta del
# vendedor — no vale poner USD siempre. Aproximación razonable a partir
# de la moneda que ya tiene configurada el propio anuncio.
MONEDA_MERCADOPAGO = {"UYU": "UYU", "ARS": "ARS", "BRL": "BRL", "USD": "USD", "EUR": "EUR", "GBP": "USD"}


def _comision_por_plan(plan_pago: "models.PlanPago") -> float:
    return COMISION_PAGO_COMPLETO_USD if plan_pago == models.PlanPago.completo else COMISION_PRIMERA_NOCHE_USD


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
    saldo_pendiente = (r.precio_total_estancia - r.monto_cobrado) if r.plan_pago == models.PlanPago.primera_noche else 0
    return schemas.ReservaOut(
        id=r.id, inmueble_id=r.inmueble_id, huesped_id=r.huesped_id, propietario_id=r.propietario_id,
        fecha_entrada=r.fecha_entrada, fecha_salida=r.fecha_salida, plan_pago=r.plan_pago.value,
        pasarela_pago=r.pasarela_pago.value,
        precio_noche=r.precio_noche, precio_total_estancia=r.precio_total_estancia, monto_cobrado=r.monto_cobrado,
        saldo_pendiente_en_destino=round(saldo_pendiente, 2),
        comision_hausbix_usd=r.comision_hausbix_usd, estado=r.estado.value,
        fecha_creacion=r.fecha_creacion, fecha_resolucion=r.fecha_resolucion,
        inmueble_titulo=r.inmueble.titulo if r.inmueble else None,
        inmueble_foto=r.inmueble.fotos[0].url if r.inmueble and r.inmueble.fotos else None,
        otro_nombre=otro.nombre if otro else None,
    )


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
    Crea la reserva y un PaymentIntent de Stripe con destino directo a la
    cuenta del propietario (cargo con destino) — el dinero llega a su
    cuenta en el mismo cobro, sin ningún paso posterior de transferencia.
    Hausbix se queda automáticamente con la comisión correspondiente al
    plan elegido (`COMISION_PRIMERA_NOCHE_USD` o `COMISION_PAGO_COMPLETO_USD`) de cada
    cobro, vía `application_fee_amount`.
    """
    _verificar_stripe_configurado() if payload.pasarela_pago == models.PasarelaPago.stripe else _verificar_mercadopago_configurado()

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

    propietario = db.query(models.Usuario).filter_by(id=inmueble.usuario_id).first()
    if payload.pasarela_pago == models.PasarelaPago.stripe:
        if not propietario or not propietario.stripe_account_id or not propietario.stripe_onboarding_completo:
            raise HTTPException(status_code=400, detail="El propietario todavía no ha conectado Stripe — no se puede reservar con esta pasarela todavía")
    else:
        if not propietario or not propietario.mercadopago_access_token:
            raise HTTPException(status_code=400, detail="El propietario todavía no ha conectado Mercado Pago — no se puede reservar con esta pasarela todavía")

    ocupado = any(
        d.fecha == payload.fecha_entrada and d.estado == models.EstadoDisponibilidad.ocupado
        for d in inmueble.disponibilidad
    )
    if ocupado:
        raise HTTPException(status_code=409, detail="Esa fecha ya no está disponible")

    num_noches = max((payload.fecha_salida - payload.fecha_entrada).days, 1)

    operacion_temporal = next((o for o in inmueble.operaciones if o.tipo_operacion == models.TipoOperacion.alquiler_temporal), None)
    if operacion_temporal and operacion_temporal.noches_minimas and num_noches < operacion_temporal.noches_minimas:
        raise HTTPException(status_code=400, detail=f"Este alojamiento exige una estancia mínima de {operacion_temporal.noches_minimas} noches")
    precio_noche = _obtener_precio_noche(inmueble, payload.fecha_entrada)
    precio_total_estancia = round(precio_noche * num_noches, 2)
    comision = _comision_por_plan(payload.plan_pago)

    if payload.plan_pago == models.PlanPago.completo:
        monto_cobrado = round(precio_total_estancia * 0.9, 2)  # 10% de descuento por pagarlo todo por adelantado
    else:
        monto_cobrado = round(precio_noche, 2)  # solo la primera noche — el resto se paga directo al propietario al llegar

    if monto_cobrado <= comision:
        raise HTTPException(status_code=400, detail="El importe a cobrar es demasiado bajo para cubrir los gastos de gestión")

    reserva = models.Reserva(
        inmueble_id=inmueble.id,
        huesped_id=usuario.id,
        propietario_id=inmueble.usuario_id,
        fecha_entrada=payload.fecha_entrada,
        fecha_salida=payload.fecha_salida,
        plan_pago=payload.plan_pago,
        pasarela_pago=payload.pasarela_pago,
        precio_noche=precio_noche,
        precio_total_estancia=precio_total_estancia,
        monto_cobrado=monto_cobrado,
        comision_hausbix_usd=comision,
    )
    db.add(reserva)
    db.flush()

    if payload.pasarela_pago == models.PasarelaPago.stripe:
        intent = stripe.PaymentIntent.create(
            amount=round(monto_cobrado * 100),  # Stripe usa céntimos
            currency="usd",
            application_fee_amount=round(comision * 100),
            transfer_data={"destination": propietario.stripe_account_id},
            metadata={"reserva_id": reserva.id},
            description=f"Reserva Hausbix — {inmueble.titulo} ({payload.fecha_entrada})",
        )
        reserva.stripe_payment_intent_id = intent.id
        db.commit()
        db.refresh(reserva)
        return schemas.ReservaPagoOut(reserva=_a_reserva_out(reserva, usuario.id), client_secret=intent.client_secret)

    # Mercado Pago: se crea la preferencia CON el token de la propia cuenta
    # del propietario (no con el de Hausbix) — así el dinero entra
    # directamente en su cuenta, y marketplace_fee separa nuestra parte
    # en el mismo cobro, sin ningún paso posterior.
    backend_url = os.environ.get("BACKEND_URL", "https://hausbix-api.onrender.com")
    frontend_url = os.environ.get("FRONTEND_URL", "https://hausbix.com")
    sdk = mercadopago.SDK(propietario.mercadopago_access_token)
    preferencia = sdk.preference().create({
        "items": [{
            "title": f"Reserva Hausbix — {inmueble.titulo}",
            "quantity": 1,
            "unit_price": monto_cobrado,
            "currency_id": MONEDA_MERCADOPAGO.get(inmueble.moneda.value if hasattr(inmueble.moneda, "value") else inmueble.moneda, "USD"),
        }],
        "marketplace_fee": comision,
        "external_reference": reserva.id,
        "back_urls": {
            "success": f"{frontend_url}/mis-reservas.html",
            "failure": f"{frontend_url}/mis-reservas.html",
            "pending": f"{frontend_url}/mis-reservas.html",
        },
        "auto_return": "approved",
        "notification_url": f"{backend_url}/mercadopago/webhook",
    })
    respuesta = preferencia.get("response", {})
    if not respuesta.get("id"):
        raise HTTPException(status_code=502, detail="No se pudo iniciar el pago con Mercado Pago")

    reserva.mercadopago_payment_id = respuesta["id"]  # id de la preferencia — el pago real llega luego por el webhook
    db.commit()
    db.refresh(reserva)
    return schemas.ReservaPagoOut(reserva=_a_reserva_out(reserva, usuario.id), checkout_url=respuesta.get("init_point"))


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
    reserva = db.query(models.Reserva).filter_by(id=reserva_id).first()
    if not reserva:
        raise HTTPException(status_code=404, detail="Reserva no encontrada")
    if reserva.propietario_id != usuario.id:
        raise HTTPException(status_code=403, detail="Solo el propietario puede confirmar esta reserva")
    if reserva.estado != models.EstadoReserva.pendiente_confirmacion:
        raise HTTPException(status_code=400, detail="Esta reserva no está pendiente de confirmación")

    # El dinero ya está en la cuenta del propietario desde el momento del
    # cobro (cargo con destino) — confirmar solo cambia el estado, no hay
    # ninguna transferencia que hacer aquí.
    reserva.estado = models.EstadoReserva.confirmada
    reserva.fecha_resolucion = datetime.utcnow()
    db.commit()
    db.refresh(reserva)

    enviar_push(reserva.huesped.push_token, "¡Reserva confirmada!", f"Tu reserva en {reserva.inmueble.titulo} ha sido confirmada.")
    return _a_reserva_out(reserva, usuario.id)


def _reembolsar(reserva: models.Reserva, monto_usd: float) -> str:
    """Reembolsa en la pasarela que se usó para cobrar. Devuelve el id del
    reembolso, para guardarlo."""
    if reserva.pasarela_pago == models.PasarelaPago.stripe:
        reembolso = stripe.Refund.create(
            payment_intent=reserva.stripe_payment_intent_id,
            amount=round(monto_usd * 100),
        )
        return reembolso.id
    propietario = reserva.propietario
    sdk = mercadopago.SDK(propietario.mercadopago_access_token)
    resultado = sdk.refund().create(reserva.mercadopago_payment_id, {"amount": monto_usd})
    return str(resultado.get("response", {}).get("id", ""))


@router.post("/reservas/{reserva_id}/rechazar", response_model=schemas.ReservaOut)
def rechazar_reserva(reserva_id: str, db: Session = Depends(get_db), usuario: models.Usuario = Depends(usuario_actual)):
    reserva = db.query(models.Reserva).filter_by(id=reserva_id).first()
    if not reserva:
        raise HTTPException(status_code=404, detail="Reserva no encontrada")
    if reserva.propietario_id != usuario.id:
        raise HTTPException(status_code=403, detail="Solo el propietario puede rechazar esta reserva")
    if reserva.estado != models.EstadoReserva.pendiente_confirmacion:
        raise HTTPException(status_code=400, detail="Esta reserva no está pendiente de confirmación")

    reserva.stripe_refund_id = _reembolsar(reserva, reserva.monto_cobrado)  # rechazo del propietario → reembolso ÍNTEGRO, no es culpa del huésped
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
    entrada, se le devuelve lo cobrado menos la comisión de gestión (esa
    nunca se devuelve). Con menos de 24h, o si ya pasó la fecha, no hay
    reembolso de nada — el propietario ya tiene el dinero (el cobro fue
    directo a su cuenta), así que no hace falta ninguna acción en la
    pasarela de pago para este caso.
    """
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
        reserva.stripe_refund_id = _reembolsar(reserva, reserva.monto_cobrado - reserva.comision_hausbix_usd)
        reserva.estado = models.EstadoReserva.cancelada_con_reembolso
        _marcar_disponibilidad(db, reserva.inmueble_id, reserva.fecha_entrada, models.EstadoDisponibilidad.libre)
    else:
        reserva.estado = models.EstadoReserva.cancelada_sin_reembolso
        # No se libera la fecha — se canceló demasiado tarde para volver a alquilarla con normalidad.
        # El propietario ya tiene el dinero completo desde el momento del cobro — no hace falta tocar Stripe.

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


@router.post("/mercadopago/webhook", include_in_schema=False)
async def mercadopago_webhook(request: Request, db: Session = Depends(get_db)):
    """
    Recibe la notificación de pago de Mercado Pago. Configúralo en tu
    aplicación de Mercado Pago Developers apuntando a
    https://tu-backend/mercadopago/webhook. MERCADOPAGO_ACCESS_TOKEN debe
    ser el token de la propia aplicación (no el de ningún propietario) —
    con ese se puede consultar cualquier pago hecho a través de la app,
    sin importar de qué propietario sea.
    """
    datos = await request.json()
    payment_id = datos.get("data", {}).get("id") or request.query_params.get("id")
    if not payment_id:
        return {"ok": True}

    token_app = os.environ.get("MERCADOPAGO_ACCESS_TOKEN")
    if not token_app:
        return {"ok": True}  # sin token de la app no se puede consultar el pago — se ignora la notificación

    sdk = mercadopago.SDK(token_app)
    pago = sdk.payment().get(payment_id).get("response", {})
    reserva_id = pago.get("external_reference")
    reserva = db.query(models.Reserva).filter_by(id=reserva_id).first() if reserva_id else None

    if reserva and pago.get("status") == "approved" and reserva.estado == models.EstadoReserva.pendiente_pago:
        reserva.mercadopago_payment_id = str(payment_id)  # se sustituye el id de la preferencia por el del pago real, para poder reembolsar después
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
