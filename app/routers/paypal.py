import os
import json
import base64
import time

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from .. import models, schemas
from ..database import get_db
from ..auth import usuario_actual
from ..push import enviar_push

router = APIRouter(prefix="/paypal", tags=["paypal"])

# PayPal Commerce Platform (pagos multiparte): el huésped paga con PayPal,
# el dinero entra directamente en la cuenta PayPal del propietario y
# `platform_fees` separa la comisión de Hausbix en el mismo cobro — el
# mismo modelo que Stripe Connect (destination charge) y Mercado Pago
# (marketplace_fee).
#
# Variables de entorno (Render):
#   PAYPAL_CLIENT_ID, PAYPAL_CLIENT_SECRET   credenciales REST de la app de Hausbix
#   PAYPAL_PARTNER_MERCHANT_ID               "Merchant ID" (payer id) de la cuenta de Hausbix
#   PAYPAL_ENV                               "live" o "sandbox" (por defecto sandbox)
#   PAYPAL_WEBHOOK_ID                        id del webhook (opcional pero recomendado)
#   PAYPAL_BN_CODE                           BN code de partner (opcional)
PAYPAL_CLIENT_ID = os.environ.get("PAYPAL_CLIENT_ID")
PAYPAL_CLIENT_SECRET = os.environ.get("PAYPAL_CLIENT_SECRET")
PAYPAL_PARTNER_MERCHANT_ID = os.environ.get("PAYPAL_PARTNER_MERCHANT_ID")
PAYPAL_WEBHOOK_ID = os.environ.get("PAYPAL_WEBHOOK_ID")
PAYPAL_BN_CODE = os.environ.get("PAYPAL_BN_CODE")
PAYPAL_EN_PRODUCCION = os.environ.get("PAYPAL_ENV", "sandbox").lower() == "live"
PAYPAL_API = "https://api-m.paypal.com" if PAYPAL_EN_PRODUCCION else "https://api-m.sandbox.paypal.com"

_token_cache: dict = {"token": None, "caduca": 0.0}


def _verificar_paypal_configurado():
    if not (PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET and PAYPAL_PARTNER_MERCHANT_ID):
        raise HTTPException(
            status_code=503,
            detail="PayPal todavía no está configurado en el servidor (falta PAYPAL_CLIENT_ID / PAYPAL_CLIENT_SECRET / PAYPAL_PARTNER_MERCHANT_ID).",
        )


def _token_app() -> str:
    if _token_cache["token"] and time.time() < _token_cache["caduca"] - 60:
        return _token_cache["token"]
    resp = httpx.post(
        f"{PAYPAL_API}/v1/oauth2/token",
        data={"grant_type": "client_credentials"},
        auth=(PAYPAL_CLIENT_ID, PAYPAL_CLIENT_SECRET),
        timeout=15.0,
    )
    if resp.status_code != 200:
        raise HTTPException(status_code=502, detail="No se pudo autenticar con PayPal")
    datos = resp.json()
    _token_cache["token"] = datos["access_token"]
    _token_cache["caduca"] = time.time() + int(datos.get("expires_in", 300))
    return _token_cache["token"]


def _auth_assertion(merchant_id: str) -> str:
    """Cabecera PayPal-Auth-Assertion: permite que Hausbix actúe en nombre
    de la cuenta del propietario (JWT sin firmar, así lo exige PayPal)."""
    def b64(d: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")
    return f"{b64({'alg': 'none'})}.{b64({'iss': PAYPAL_CLIENT_ID, 'payer_id': merchant_id})}."


def _llamar(metodo: str, ruta: str, cuerpo: dict | None = None, merchant_id: str | None = None,
            request_id: str | None = None, permitir: tuple = ()) -> dict:
    cabeceras = {"Authorization": f"Bearer {_token_app()}", "Content-Type": "application/json"}
    if merchant_id:
        cabeceras["PayPal-Auth-Assertion"] = _auth_assertion(merchant_id)
    if PAYPAL_BN_CODE:
        cabeceras["PayPal-Partner-Attribution-Id"] = PAYPAL_BN_CODE
    if request_id:
        cabeceras["PayPal-Request-Id"] = request_id
    resp = httpx.request(metodo, f"{PAYPAL_API}{ruta}", json=cuerpo, headers=cabeceras, timeout=20.0)
    if resp.status_code in permitir:
        try:
            return {"_status": resp.status_code, **resp.json()}
        except ValueError:
            return {"_status": resp.status_code}
    if resp.status_code >= 300:
        print(f"[paypal] {metodo} {ruta} -> {resp.status_code}: {resp.text[:500]}")
        raise HTTPException(status_code=502, detail="PayPal ha rechazado la operación")
    return resp.json() if resp.content else {}


def _enlace(datos: dict, *relaciones: str) -> str | None:
    for rel in relaciones:
        for l in datos.get("links", []):
            if l.get("rel") == rel:
                return l.get("href")
    return None


# ---------------------------------------------------------------- pagos

def crear_orden_paypal(reserva: models.Reserva, titulo: str, merchant_id: str, frontend_url: str) -> tuple[str, str]:
    """Crea el pedido de PayPal con destino la cuenta del propietario y la
    comisión de Hausbix como platform_fee. Devuelve (order_id, url de pago).
    Los importes están en USD, igual que con Stripe."""
    _verificar_paypal_configurado()
    datos = _llamar("POST", "/v2/checkout/orders", {
        "intent": "CAPTURE",
        "purchase_units": [{
            "reference_id": reserva.id,
            "custom_id": reserva.id,
            "description": f"Reserva Hausbix — {titulo}"[:127],
            "amount": {"currency_code": "USD", "value": f"{reserva.monto_cobrado:.2f}"},
            "payee": {"merchant_id": merchant_id},
            "payment_instruction": {
                "disbursement_mode": "INSTANT",
                "platform_fees": [{"amount": {"currency_code": "USD", "value": f"{reserva.comision_hausbix_usd:.2f}"}}],
            },
        }],
        "payment_source": {"paypal": {"experience_context": {
            "brand_name": "Hausbix",
            "user_action": "PAY_NOW",
            "payment_method_preference": "IMMEDIATE_PAYMENT_REQUIRED",
            "return_url": f"{frontend_url}/paypal-retorno.html",
            "cancel_url": f"{frontend_url}/mis-reservas.html",
        }}},
    }, merchant_id=merchant_id, request_id=f"hausbix-{reserva.id}")
    url = _enlace(datos, "payer-action", "approve")
    if not datos.get("id") or not url:
        raise HTTPException(status_code=502, detail="No se pudo iniciar el pago con PayPal")
    return datos["id"], url


def reembolsar_paypal(reserva: models.Reserva, monto_usd: float) -> str:
    _verificar_paypal_configurado()
    if not reserva.paypal_capture_id:
        raise HTTPException(status_code=400, detail="Esta reserva no tiene un cobro de PayPal que reembolsar")
    datos = _llamar(
        "POST", f"/v2/payments/captures/{reserva.paypal_capture_id}/refund",
        {"amount": {"value": f"{monto_usd:.2f}", "currency_code": "USD"}},
        merchant_id=reserva.propietario.paypal_merchant_id,
        request_id=f"hausbix-refund-{reserva.id}-{int(monto_usd * 100)}",
    )
    return str(datos.get("id", ""))


def _capturar(order_id: str, merchant_id: str) -> str | None:
    """Captura el pedido ya aprobado por el huésped. Idempotente: si ya
    estaba capturado, recupera el id de la captura existente."""
    datos = _llamar("POST", f"/v2/checkout/orders/{order_id}/capture", {}, merchant_id=merchant_id,
                    request_id=f"hausbix-capture-{order_id}", permitir=(422,))
    if datos.get("_status") == 422:
        datos = _llamar("GET", f"/v2/checkout/orders/{order_id}", merchant_id=merchant_id)
    if datos.get("status") != "COMPLETED":
        return None
    try:
        return datos["purchase_units"][0]["payments"]["captures"][0]["id"]
    except (KeyError, IndexError):
        return None


def finalizar_orden_paypal(db: Session, order_id: str) -> models.Reserva | None:
    """Captura el pedido y deja la reserva en pendiente_confirmacion. La
    llaman tanto la página de retorno como el webhook; es idempotente."""
    reserva = db.query(models.Reserva).filter_by(paypal_order_id=order_id).first()
    if not reserva:
        return None
    if reserva.estado != models.EstadoReserva.pendiente_pago:
        return reserva
    propietario = db.query(models.Usuario).filter_by(id=reserva.propietario_id).first()
    if not propietario or not propietario.paypal_merchant_id:
        return reserva
    captura = _capturar(order_id, propietario.paypal_merchant_id)
    if not captura:
        return reserva  # el huésped aún no ha aprobado, o el pago no se completó
    reserva.paypal_capture_id = captura
    reserva.estado = models.EstadoReserva.pendiente_confirmacion
    dia = db.query(models.Disponibilidad).filter_by(inmueble_id=reserva.inmueble_id, fecha=reserva.fecha_entrada).first()
    if dia:
        dia.estado = models.EstadoDisponibilidad.ocupado
    else:
        db.add(models.Disponibilidad(inmueble_id=reserva.inmueble_id, fecha=reserva.fecha_entrada, estado=models.EstadoDisponibilidad.ocupado))
    db.commit()
    db.refresh(reserva)
    enviar_push(propietario.push_token, "Nueva reserva", f"Tienes una nueva reserva en {reserva.inmueble.titulo}. Confírmala para completarla.")
    return reserva


@router.post("/capturar")
def capturar_pago(order_id: str, db: Session = Depends(get_db)):
    """Lo llama la página de retorno de PayPal tras aprobar el huésped.
    No requiere sesión (en la app el retorno ocurre en el navegador): el
    order_id es un identificador de PayPal que solo conoce quien pagó, y
    capturar un pedido que el huésped no aprobó simplemente no hace nada."""
    _verificar_paypal_configurado()
    reserva = finalizar_orden_paypal(db, order_id)
    if not reserva:
        raise HTTPException(status_code=404, detail="Pedido no encontrado")
    return {"ok": reserva.estado != models.EstadoReserva.pendiente_pago, "estado": reserva.estado.value}


def _webhook_valido(cabeceras, evento: dict) -> bool:
    if not PAYPAL_WEBHOOK_ID:
        return False
    try:
        r = _llamar("POST", "/v1/notifications/verify-webhook-signature", {
            "auth_algo": cabeceras.get("paypal-auth-algo"),
            "cert_url": cabeceras.get("paypal-cert-url"),
            "transmission_id": cabeceras.get("paypal-transmission-id"),
            "transmission_sig": cabeceras.get("paypal-transmission-sig"),
            "transmission_time": cabeceras.get("paypal-transmission-time"),
            "webhook_id": PAYPAL_WEBHOOK_ID,
            "webhook_event": evento,
        })
        return r.get("verification_status") == "SUCCESS"
    except HTTPException:
        return False


@router.post("/webhook", include_in_schema=False)
async def paypal_webhook(request: Request, db: Session = Depends(get_db)):
    """Configúralo en PayPal Developer → Webhooks apuntando a
    https://tu-backend/paypal/webhook, con los eventos
    CHECKOUT.ORDER.APPROVED y PAYMENT.CAPTURE.COMPLETED. Es la red de
    seguridad por si el huésped cierra el navegador antes de volver."""
    evento = await request.json()
    if not _webhook_valido(request.headers, evento):
        raise HTTPException(status_code=400, detail="Firma de webhook inválida")
    recurso = evento.get("resource", {})
    tipo = evento.get("event_type")
    order_id = None
    if tipo == "CHECKOUT.ORDER.APPROVED":
        order_id = recurso.get("id")
    elif tipo == "PAYMENT.CAPTURE.COMPLETED":
        order_id = recurso.get("supplementary_data", {}).get("related_ids", {}).get("order_id")
    if order_id:
        finalizar_orden_paypal(db, order_id)
    return {"ok": True}


# ----------------------------------------------------------- onboarding

@router.post("/onboarding-link", response_model=schemas.StripeOnboardingOut)
def crear_enlace_onboarding_paypal(usuario: models.Usuario = Depends(usuario_actual)):
    """Enlace de PayPal Partner Referrals: el propietario inicia sesión con
    SU cuenta de PayPal y autoriza a Hausbix a cobrar en su nombre. Al
    volver, `/paypal/oauth-callback` comprueba que la cuenta puede recibir
    pagos y guarda su Merchant ID."""
    _verificar_paypal_configurado()
    frontend_url = os.environ.get("FRONTEND_URL", "https://hausbix.com")
    datos = _llamar("POST", "/v2/customer/partner-referrals", {
        "tracking_id": usuario.id,
        "partner_config_override": {
            "return_url": f"{frontend_url}/paypal-oauth-callback.html",
            "return_url_description": "Volver a Hausbix",
        },
        "operations": [{
            "operation": "API_INTEGRATION",
            "api_integration_preference": {"rest_api_integration": {
                "integration_method": "PAYPAL",
                "integration_type": "THIRD_PARTY",
                "third_party_details": {"features": ["PAYMENT", "REFUND", "PARTNER_FEE"]},
            }},
        }],
        "products": ["EXPRESS_CHECKOUT"],
        "legal_consents": [{"type": "SHARE_DATA_CONSENT", "granted": True}],
    })
    url = _enlace(datos, "action_url")
    if not url:
        raise HTTPException(status_code=502, detail="No se pudo generar el enlace de PayPal")
    return schemas.StripeOnboardingOut(url=url)


@router.post("/oauth-callback")
def procesar_callback_paypal(
    merchant_id: str,
    db: Session = Depends(get_db),
    usuario: models.Usuario = Depends(usuario_actual),
):
    """Recibe el merchantIdInPayPal con el que vuelve el propietario,
    comprueba con PayPal que es de verdad quien inició el enlace
    (tracking_id) y que su cuenta puede recibir pagos, y lo guarda."""
    _verificar_paypal_configurado()
    estado = _llamar("GET", f"/v1/customer/partners/{PAYPAL_PARTNER_MERCHANT_ID}/merchant-integrations/{merchant_id}")
    if estado.get("tracking_id") != usuario.id:
        raise HTTPException(status_code=403, detail="Esta cuenta de PayPal no corresponde a tu sesión")
    usuario.paypal_merchant_id = merchant_id
    usuario.paypal_onboarding_completo = bool(estado.get("payments_receivable") and estado.get("primary_email_confirmed"))
    db.commit()
    return {"ok": True, "completo": usuario.paypal_onboarding_completo}


@router.get("/estado-onboarding")
def estado_onboarding_paypal(db: Session = Depends(get_db), usuario: models.Usuario = Depends(usuario_actual)):
    if not usuario.paypal_merchant_id:
        return {"completo": False}
    if not usuario.paypal_onboarding_completo and PAYPAL_CLIENT_ID and PAYPAL_PARTNER_MERCHANT_ID:
        # Puede que haya confirmado el email o activado los cobros después de volver.
        try:
            estado = _llamar("GET", f"/v1/customer/partners/{PAYPAL_PARTNER_MERCHANT_ID}/merchant-integrations/{usuario.paypal_merchant_id}")
            usuario.paypal_onboarding_completo = bool(estado.get("payments_receivable") and estado.get("primary_email_confirmed"))
            db.commit()
        except HTTPException:
            pass
    return {"completo": bool(usuario.paypal_onboarding_completo)}


@router.delete("/desconectar")
def desconectar_paypal(db: Session = Depends(get_db), usuario: models.Usuario = Depends(usuario_actual)):
    usuario.paypal_merchant_id = None
    usuario.paypal_onboarding_completo = False
    db.commit()
    return {"ok": True}
