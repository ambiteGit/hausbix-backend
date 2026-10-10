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
from .paypal import _verificar_paypal_configurado, crear_orden_paypal, reembolsar_paypal

router = APIRouter(tags=["reservas"])

stripe.api_key = os.environ.get("STRIPE_SECRET_KEY")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET")
COMISION_PRIMERA_NOCHE_USD = float(os.environ.get("COMISION_PRIMERA_NOCHE_USD", "9.99"))  # el resto lo cobra el propietario directamente al llegar
COMISION_PAGO_COMPLETO_USD = float(os.environ.get("COMISION_PAGO_COMPLETO_USD", "11.99"))  # se paga toda la estancia por adelantado, con 10% de descuento

# Mercado Pago exige una moneda concreta según el país de la cuenta del
# vendedor — no vale poner USD siempre. Aproximación razonable a partir
# de la moneda que ya tiene configurada el propio anuncio.
# Estancias de MÁS de estas noches con `anticipo_obligatorio` activo: mínimo 50% por adelantado.
NOCHES_SIN_ANTICIPO_OBLIGATORIO = 3

MONEDA_MERCADOPAGO = {"UYU": "UYU", "ARS": "ARS", "BRL": "BRL", "USD": "USD", "EUR": "EUR", "GBP": "USD"}


def _comision_por_plan(plan_pago: "models.PlanPago") -> float:
    return COMISION_PAGO_COMPLETO_USD if plan_pago == models.PlanPago.completo else COMISION_PRIMERA_NOCHE_USD


def _verificar_stripe_configurado():
    if not stripe.api_key:
        raise HTTPException(
            status_code=503,
            detail="Los pagos todavía no están configurados en el servidor (falta STRIPE_SECRET_KEY).",
        )


def _verificar_pasarela_configurada(pasarela: "models.PasarelaPago"):
    if pasarela == models.PasarelaPago.stripe:
        _verificar_stripe_configurado()
    elif pasarela == models.PasarelaPago.paypal:
        _verificar_paypal_configurado()
    else:
        _verificar_mercadopago_configurado()


def _propietario_conectado(propietario: "models.Usuario | None", pasarela: "models.PasarelaPago") -> bool:
    if not propietario:
        return False
    if pasarela == models.PasarelaPago.stripe:
        return bool(propietario.stripe_account_id and propietario.stripe_onboarding_completo)
    if pasarela == models.PasarelaPago.paypal:
        return bool(propietario.paypal_merchant_id and propietario.paypal_onboarding_completo)
    return bool(propietario.mercadopago_access_token)


_NOMBRE_PASARELA = {"stripe": "Stripe", "mercadopago": "Mercado Pago", "paypal": "PayPal"}


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


def _a_reserva_out(
    r: models.Reserva, usuario_id: str, resena_pendiente: bool = False, resena_id: str | None = None,
) -> schemas.ReservaOut:
    otro = r.propietario if usuario_id == r.huesped_id else r.huesped
    saldo_pendiente = (r.precio_total_estancia - r.monto_cobrado) if r.plan_pago != models.PlanPago.completo else 0
    return schemas.ReservaOut(
        id=r.id, inmueble_id=r.inmueble_id, huesped_id=r.huesped_id, propietario_id=r.propietario_id,
        fecha_entrada=r.fecha_entrada, fecha_salida=r.fecha_salida, plan_pago=r.plan_pago.value,
        pasarela_pago=r.pasarela_pago.value,
        precio_noche=r.precio_noche, recargo_persona_extra_noche=r.recargo_persona_extra_noche,
        precio_total_estancia=r.precio_total_estancia, monto_cobrado=r.monto_cobrado,
        saldo_pendiente_en_destino=round(saldo_pendiente, 2),
        comision_hausbix_usd=r.comision_hausbix_usd,
        num_adultos=r.num_adultos, num_ninos=r.num_ninos, lleva_mascotas=r.lleva_mascotas,
        estado=r.estado.value,
        mensaje_huesped=r.mensaje_huesped,
        pago_iniciado=bool(r.stripe_payment_intent_id or r.mercadopago_payment_id or r.paypal_order_id),
        resena_pendiente=resena_pendiente, resena_id=resena_id,
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


def _crear_pago_reserva(db: Session, reserva: models.Reserva, inmueble: models.Inmueble, propietario: models.Usuario) -> schemas.ReservaPagoOut:
    """
    Crea el PaymentIntent de Stripe (con destino directo a la cuenta del
    propietario) o la preferencia de Mercado Pago para una reserva ya
    existente, y guarda el id correspondiente. La usan tanto la reserva
    instantánea (crear_reserva) como el pago posterior a una solicitud ya
    aprobada por el propietario (iniciar_pago_reserva) — el resto del
    modelo económico es idéntico en los dos casos.
    """
    if reserva.pasarela_pago == models.PasarelaPago.stripe:
        intent = stripe.PaymentIntent.create(
            amount=round(reserva.monto_cobrado * 100),  # Stripe usa céntimos
            currency="usd",
            application_fee_amount=round(reserva.comision_hausbix_usd * 100),
            transfer_data={"destination": propietario.stripe_account_id},
            metadata={"reserva_id": reserva.id},
            description=f"Reserva Hausbix — {inmueble.titulo} ({reserva.fecha_entrada})",
        )
        reserva.stripe_payment_intent_id = intent.id
        db.commit()
        db.refresh(reserva)
        return schemas.ReservaPagoOut(reserva=_a_reserva_out(reserva, reserva.huesped_id), client_secret=intent.client_secret)

    if reserva.pasarela_pago == models.PasarelaPago.paypal:
        frontend_url = os.environ.get("FRONTEND_URL", "https://hausbix.com")
        order_id, url = crear_orden_paypal(reserva, inmueble.titulo, propietario.paypal_merchant_id, frontend_url)
        reserva.paypal_order_id = order_id
        db.commit()
        db.refresh(reserva)
        return schemas.ReservaPagoOut(reserva=_a_reserva_out(reserva, reserva.huesped_id), checkout_url=url)

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
            "unit_price": reserva.monto_cobrado,
            "currency_id": MONEDA_MERCADOPAGO.get(inmueble.moneda.value if hasattr(inmueble.moneda, "value") else inmueble.moneda, "USD"),
        }],
        "marketplace_fee": reserva.comision_hausbix_usd,
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
    return schemas.ReservaPagoOut(reserva=_a_reserva_out(reserva, reserva.huesped_id), checkout_url=respuesta.get("init_point"))


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
    cobro. Con Mercado Pago, la preferencia se crea con el token de la
    propia cuenta del propietario y `marketplace_fee` hace lo mismo.
    """
    _verificar_pasarela_configurada(payload.pasarela_pago)

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
    if not _propietario_conectado(propietario, payload.pasarela_pago):
        raise HTTPException(status_code=400, detail=f"El propietario todavía no ha conectado {_NOMBRE_PASARELA[payload.pasarela_pago.value]} — no se puede reservar con esta pasarela todavía")

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

    num_personas = payload.num_adultos + payload.num_ninos
    if operacion_temporal and operacion_temporal.capacidad_maxima and num_personas > operacion_temporal.capacidad_maxima:
        raise HTTPException(status_code=400, detail=f"Este alojamiento admite un máximo de {operacion_temporal.capacidad_maxima} huéspedes")
    if payload.lleva_mascotas and not inmueble.mascotas_permitidas:
        raise HTTPException(status_code=400, detail="Este alojamiento no admite mascotas")

    requiere_aprobacion = bool(operacion_temporal and operacion_temporal.requiere_aprobacion_propietario)
    if requiere_aprobacion and not (payload.mensaje and payload.mensaje.strip()):
        raise HTTPException(status_code=400, detail="Este alojamiento exige mandar un mensaje de solicitud al propietario")

    precio_noche = _obtener_precio_noche(inmueble, payload.fecha_entrada)
    # Recargo por noche a partir del 3er huésped (adultos + niños cuentan) —
    # solo si el propietario configuró un precio por persona extra.
    personas_extra = max(num_personas - 2, 0)
    recargo_persona_extra_noche = round(personas_extra * (operacion_temporal.precio_persona_extra or 0), 2) if operacion_temporal else 0
    precio_noche_efectivo = precio_noche + recargo_persona_extra_noche
    precio_total_estancia = round(precio_noche_efectivo * num_noches, 2)
    # Con una sola noche, "pagar todo ahora" y "pagar solo la primera
    # noche" son el mismo importe — no existe el plan completo con 10% de
    # descuento en ese caso. El frontend ya no lo ofrece, pero esto evita
    # que alguien lo consiga llamando directamente a la API.
    plan_pago = payload.plan_pago if num_noches > 1 else models.PlanPago.primera_noche
    anticipo_obligatorio = bool(
        operacion_temporal and operacion_temporal.anticipo_obligatorio and num_noches > NOCHES_SIN_ANTICIPO_OBLIGATORIO
    )
    if anticipo_obligatorio and plan_pago == models.PlanPago.primera_noche:
        plan_pago = models.PlanPago.anticipo_50  # no vale pagar solo la primera noche — mínimo el 50% (también protege a apps antiguas que aún mandan primera_noche)
    if plan_pago == models.PlanPago.anticipo_50 and not anticipo_obligatorio:
        raise HTTPException(status_code=400, detail="El anticipo del 50% no aplica a esta reserva")
    comision = _comision_por_plan(plan_pago)

    if plan_pago == models.PlanPago.completo:
        monto_cobrado = round(precio_total_estancia * 0.9, 2)  # 10% de descuento por pagarlo todo por adelantado
    elif plan_pago == models.PlanPago.anticipo_50:
        monto_cobrado = round(precio_total_estancia * 0.5, 2)  # 50% por adelantado, sin descuento — el otro 50% se paga directo al propietario al llegar
    else:
        monto_cobrado = round(precio_noche_efectivo, 2)  # solo la primera noche (con el recargo si aplica) — el resto se paga directo al propietario al llegar

    if monto_cobrado <= comision:
        raise HTTPException(status_code=400, detail="El importe a cobrar es demasiado bajo para cubrir los gastos de gestión")

    reserva = models.Reserva(
        inmueble_id=inmueble.id,
        huesped_id=usuario.id,
        propietario_id=inmueble.usuario_id,
        fecha_entrada=payload.fecha_entrada,
        fecha_salida=payload.fecha_salida,
        plan_pago=plan_pago,
        pasarela_pago=payload.pasarela_pago,
        precio_noche=precio_noche,
        recargo_persona_extra_noche=recargo_persona_extra_noche,
        precio_total_estancia=precio_total_estancia,
        monto_cobrado=monto_cobrado,
        comision_hausbix_usd=comision,
        num_adultos=payload.num_adultos,
        num_ninos=payload.num_ninos,
        lleva_mascotas=payload.lleva_mascotas,
        mensaje_huesped=payload.mensaje.strip() if payload.mensaje else None,
        estado=models.EstadoReserva.pendiente_aprobacion_propietario if requiere_aprobacion else models.EstadoReserva.pendiente_pago,
    )
    db.add(reserva)
    _marcar_disponibilidad(db, inmueble.id, payload.fecha_entrada, models.EstadoDisponibilidad.ocupado)
    db.commit()
    db.refresh(reserva)

    if requiere_aprobacion:
        # No se cobra nada todavía — el propietario tiene que aprobar la
        # solicitud primero (ver /reservas/{id}/aprobar-solicitud).
        enviar_push(propietario.push_token, "Nueva solicitud de reserva", f"Tienes una solicitud para {inmueble.titulo}. Revisa el mensaje y apruébala o recházala.")
        return schemas.ReservaPagoOut(reserva=_a_reserva_out(reserva, usuario.id))

    return _crear_pago_reserva(db, reserva, inmueble, propietario)


@router.post("/reservas/{reserva_id}/aprobar-solicitud", response_model=schemas.ReservaOut)
def aprobar_solicitud_reserva(reserva_id: str, db: Session = Depends(get_db), usuario: models.Usuario = Depends(usuario_actual)):
    """
    El propietario aprueba una solicitud de reserva (alojamiento con
    aprobación manual). Todavía no se ha cobrado nada — esto solo deja
    la reserva lista para que el huésped complete el pago con
    /reservas/{id}/iniciar-pago.
    """
    reserva = db.query(models.Reserva).filter_by(id=reserva_id).first()
    if not reserva:
        raise HTTPException(status_code=404, detail="Reserva no encontrada")
    if reserva.propietario_id != usuario.id:
        raise HTTPException(status_code=403, detail="Solo el propietario puede aprobar esta solicitud")
    if reserva.estado != models.EstadoReserva.pendiente_aprobacion_propietario:
        raise HTTPException(status_code=400, detail="Esta reserva no está pendiente de aprobación")

    reserva.estado = models.EstadoReserva.pendiente_pago
    db.commit()
    db.refresh(reserva)
    enviar_push(reserva.huesped.push_token, "Solicitud aprobada", f"Tu solicitud para {reserva.inmueble.titulo} fue aprobada — completa el pago para confirmarla.")
    return _a_reserva_out(reserva, usuario.id)


@router.post("/reservas/{reserva_id}/rechazar-solicitud", response_model=schemas.ReservaOut)
def rechazar_solicitud_reserva(reserva_id: str, db: Session = Depends(get_db), usuario: models.Usuario = Depends(usuario_actual)):
    """
    El propietario rechaza una solicitud de reserva antes de que se haya
    cobrado nada — no hace falta reembolso, solo liberar la fecha.
    """
    reserva = db.query(models.Reserva).filter_by(id=reserva_id).first()
    if not reserva:
        raise HTTPException(status_code=404, detail="Reserva no encontrada")
    if reserva.propietario_id != usuario.id:
        raise HTTPException(status_code=403, detail="Solo el propietario puede rechazar esta solicitud")
    if reserva.estado != models.EstadoReserva.pendiente_aprobacion_propietario:
        raise HTTPException(status_code=400, detail="Esta reserva no está pendiente de aprobación")

    reserva.estado = models.EstadoReserva.rechazada
    reserva.fecha_resolucion = datetime.utcnow()
    _marcar_disponibilidad(db, reserva.inmueble_id, reserva.fecha_entrada, models.EstadoDisponibilidad.libre)
    db.commit()
    db.refresh(reserva)
    enviar_push(reserva.huesped.push_token, "Solicitud rechazada", f"Tu solicitud para {reserva.inmueble.titulo} no fue aceptada.")
    return _a_reserva_out(reserva, usuario.id)


@router.post("/reservas/{reserva_id}/iniciar-pago", response_model=schemas.ReservaPagoOut)
def iniciar_pago_reserva(reserva_id: str, db: Session = Depends(get_db), usuario: models.Usuario = Depends(usuario_actual)):
    """
    El huésped completa el pago de una solicitud ya aprobada por el
    propietario. Solo aplica a reservas que pasaron por aprobación manual
    y todavía no tienen ningún intento de pago creado.
    """
    reserva = (
        db.query(models.Reserva)
        .options(joinedload(models.Reserva.inmueble))
        .filter_by(id=reserva_id)
        .first()
    )
    if not reserva:
        raise HTTPException(status_code=404, detail="Reserva no encontrada")
    if reserva.huesped_id != usuario.id:
        raise HTTPException(status_code=403, detail="Solo quien reservó puede pagar esta reserva")
    if reserva.estado != models.EstadoReserva.pendiente_pago or reserva.stripe_payment_intent_id or reserva.mercadopago_payment_id or reserva.paypal_order_id:
        raise HTTPException(status_code=400, detail="Esta reserva no tiene un pago pendiente de iniciar")

    _verificar_pasarela_configurada(reserva.pasarela_pago)
    propietario = db.query(models.Usuario).filter_by(id=reserva.propietario_id).first()
    if not _propietario_conectado(propietario, reserva.pasarela_pago):
        raise HTTPException(status_code=400, detail=f"El propietario todavía no ha conectado {_NOMBRE_PASARELA[reserva.pasarela_pago.value]} — no se puede pagar esta reserva todavía")

    return _crear_pago_reserva(db, reserva, reserva.inmueble, propietario)


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
    # Reseñas ya dejadas por este usuario, de un solo golpe (en vez de una
    # consulta por reserva) — solo le importa a quien fue huésped: si ya
    # opinó sobre una estancia, aquí está su resena_id; si no, no aparece.
    resenas_propias = {
        r.reserva_id: r.id
        for r in db.query(models.Resena).filter(models.Resena.huesped_id == usuario.id).all()
    }
    hoy = date_cls.today()

    def _salida(r: models.Reserva) -> schemas.ReservaOut:
        es_huesped = r.huesped_id == usuario.id
        resena_id = resenas_propias.get(r.id)
        resena_pendiente = (
            es_huesped and r.estado == models.EstadoReserva.confirmada
            and r.fecha_salida < hoy and resena_id is None
        )
        return _a_reserva_out(r, usuario.id, resena_pendiente=resena_pendiente, resena_id=resena_id)

    return [_salida(r) for r in reservas]


@router.post("/reservas/{reserva_id}/confirmar", response_model=schemas.ReservaOut)
def confirmar_reserva(reserva_id: str, db: Session = Depends(get_db), usuario: models.Usuario = Depends(usuario_actual)):
    """
    El propietario confirma la reserva. El pago ya se hizo (destino
    directo a su cuenta de Stripe) al momento de solicitar — confirmar
    solo cambia el estado, sin ninguna transferencia que hacer.
    """
    reserva = db.query(models.Reserva).filter_by(id=reserva_id).first()
    if not reserva:
        raise HTTPException(status_code=404, detail="Reserva no encontrada")
    if reserva.propietario_id != usuario.id:
        raise HTTPException(status_code=403, detail="Solo el propietario puede confirmar esta reserva")
    if reserva.estado != models.EstadoReserva.pendiente_confirmacion:
        raise HTTPException(status_code=400, detail="Esta reserva no está pendiente de confirmación")

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
    if reserva.pasarela_pago == models.PasarelaPago.paypal:
        return reembolsar_paypal(reserva, monto_usd)
    propietario = reserva.propietario
    sdk = mercadopago.SDK(propietario.mercadopago_access_token)
    resultado = sdk.refund().create(reserva.mercadopago_payment_id, {"amount": monto_usd})
    return str(resultado.get("response", {}).get("id", ""))


@router.post("/reservas/{reserva_id}/rechazar", response_model=schemas.ReservaOut)
def rechazar_reserva(reserva_id: str, db: Session = Depends(get_db), usuario: models.Usuario = Depends(usuario_actual)):
    """
    El propietario rechaza la reserva — el huésped ya había pagado al
    solicitar, así que rechazar implica reembolso íntegro (no es culpa
    del huésped) y liberar la fecha.
    """
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
    Cancela el huésped, ya sea antes de que se confirme el pago o de una
    reserva ya pagada:

    - pendiente_aprobacion_propietario: todavía no se ha cobrado nada
      (esperando a que el propietario apruebe) — se cancela sin más y se
      libera la fecha.
    - pendiente_pago: el PaymentIntent se creó pero el pago todavía no se
      completó — no hay nada que reembolsar, se cancela sin más y se
      libera la fecha.
    - confirmada / pendiente_confirmacion: ya se cobró. Con 24h o más de
      antelación sobre la fecha de entrada, se le devuelve lo cobrado
      menos la comisión de gestión (esa nunca se devuelve). Con menos de
      24h no hay reembolso — el propietario ya tiene el dinero (el cobro
      fue directo a su cuenta), así que no hace falta ninguna acción en
      la pasarela de pago para este caso.
    """
    reserva = db.query(models.Reserva).filter_by(id=reserva_id).first()
    if not reserva:
        raise HTTPException(status_code=404, detail="Reserva no encontrada")
    if reserva.huesped_id != usuario.id:
        raise HTTPException(status_code=403, detail="Solo quien reservó puede cancelar")

    if reserva.estado in (models.EstadoReserva.pendiente_aprobacion_propietario, models.EstadoReserva.pendiente_pago):
        reserva.estado = models.EstadoReserva.cancelada_sin_reembolso
        _marcar_disponibilidad(db, reserva.inmueble_id, reserva.fecha_entrada, models.EstadoDisponibilidad.libre)
    elif reserva.estado in (models.EstadoReserva.confirmada, models.EstadoReserva.pendiente_confirmacion):
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
    else:
        raise HTTPException(status_code=400, detail="Esta reserva no se puede cancelar en su estado actual")

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
                enviar_push(propietario.push_token, "Nueva reserva", f"Tienes una nueva reserva en {reserva.inmueble.titulo}. Confírmala para completarla.")

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
            enviar_push(propietario.push_token, "Nueva reserva", f"Tienes una nueva reserva en {reserva.inmueble.titulo}. Confírmala para completarla.")

    return {"ok": True}


@router.post("/stripe/onboarding-link", response_model=schemas.StripeOnboardingOut)
def crear_enlace_onboarding(db: Session = Depends(get_db), usuario: models.Usuario = Depends(usuario_actual)):
    """
    Genera el enlace de onboarding de Stripe Connect Express para que el
    propietario configure cómo cobrar sus reservas. Necesario antes de
    poder confirmar ninguna reserva.
    """
    _verificar_stripe_configurado()

    try:
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
    except Exception as e:  # noqa: BLE001
        db.rollback()
        motivo = getattr(e, "user_message", None) or str(e)
        print(f"[stripe] onboarding falló: {motivo}", flush=True)
        raise HTTPException(status_code=502, detail=f"No se pudo crear el enlace de Stripe: {motivo}")
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
