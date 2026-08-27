import httpx

EXPO_PUSH_URL = "https://exp.host/--/api/v2/push/send"


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
