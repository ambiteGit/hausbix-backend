import httpx
import os
import json

EXPO_PUSH_URL = "https://exp.host/--/api/v2/push/send"

VAPID_PRIVATE_KEY = os.environ.get("VAPID_PRIVATE_KEY")
VAPID_CLAIM_EMAIL = os.environ.get("VAPID_CLAIM_EMAIL", "mailto:soporte@hausbix.com")


def enviar_push(token: str | None, titulo: str, cuerpo: str, data: dict | None = None) -> None:
    """
    Envía una notificación push a través del servicio gratuito de Expo.
    No requiere cuenta de pago ni configuración adicional — funciona con
    cualquier proyecto Expo. Si el usuario no tiene push_token guardado
    (no ha dado permiso o no ha abierto la app desde el registro), no hace nada.
    """
    if not token or not token.startswith("ExponentPushToken"):
        return

    try:
        httpx.post(
            EXPO_PUSH_URL,
            json={"to": token, "title": titulo, "body": cuerpo, "data": data or {}, "sound": "default"},
            headers={"Content-Type": "application/json"},
            timeout=5.0,
        )
    except httpx.HTTPError:
        # Una notificación fallida no debe romper el flujo principal
        # (p. ej. registrar un contacto por WhatsApp sigue funcionando igual).
        pass


def enviar_web_push(suscripcion, titulo: str, cuerpo: str, data: dict | None = None) -> bool:
    """
    Envía una notificación push a un navegador suscrito (Web Push, con las
    claves VAPID). Devuelve False si la suscripción ya no es válida (el
    navegador la canceló, se borraron los datos, etc.) — el llamador debe
    borrarla de la base de datos en ese caso, para no seguir intentando en vano.
    """
    if not VAPID_PRIVATE_KEY:
        return True  # sin configurar todavía: no es un fallo de la suscripción en sí

    from pywebpush import webpush, WebPushException

    try:
        webpush(
            subscription_info={
                "endpoint": suscripcion.endpoint,
                "keys": {"p256dh": suscripcion.clave_p256dh, "auth": suscripcion.clave_auth},
            },
            data=json.dumps({"title": titulo, "body": cuerpo, "data": data or {}}),
            vapid_private_key=VAPID_PRIVATE_KEY,
            vapid_claims={"sub": VAPID_CLAIM_EMAIL},
            timeout=5,
        )
        return True
    except WebPushException as exc:
        codigo = getattr(exc.response, "status_code", None)
        if codigo in (404, 410):  # el navegador ya no reconoce esta suscripción
            return False
        return True  # otro tipo de fallo (red, etc.) — no borrar la suscripción por esto
    except Exception:
        return True
