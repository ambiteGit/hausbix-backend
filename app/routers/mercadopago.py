import os

import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import models, schemas
from ..database import get_db
from ..auth import usuario_actual

router = APIRouter(prefix="/mercadopago", tags=["mercadopago"])

MP_APP_ID = os.environ.get("MERCADOPAGO_APP_ID")
MP_CLIENT_SECRET = os.environ.get("MERCADOPAGO_CLIENT_SECRET")


def _verificar_mercadopago_configurado():
    if not MP_APP_ID or not MP_CLIENT_SECRET:
        raise HTTPException(
            status_code=503,
            detail="Mercado Pago todavía no está configurado en el servidor (falta MERCADOPAGO_APP_ID / MERCADOPAGO_CLIENT_SECRET).",
        )


@router.post("/onboarding-link", response_model=schemas.StripeOnboardingOut)
def crear_enlace_onboarding_mercadopago():
    """
    Enlace de autorización OAuth de Mercado Pago — el propietario inicia
    sesión con SU cuenta de Mercado Pago y autoriza a Hausbix a cobrar en
    su nombre. Al volver, `/mercadopago/oauth-callback` recoge el código y
    lo cambia por un token de acceso — mismo concepto que el onboarding de
    Stripe Connect, con otro nombre.
    """
    _verificar_mercadopago_configurado()
    frontend_url = os.environ.get("FRONTEND_URL", "https://hausbix.com")
    redirect_uri = f"{frontend_url}/mercadopago-oauth-callback.html"
    url = (
        "https://auth.mercadopago.com/authorization"
        f"?client_id={MP_APP_ID}&response_type=code&platform_id=mp&redirect_uri={redirect_uri}"
    )
    return schemas.StripeOnboardingOut(url=url)


@router.post("/oauth-callback")
def procesar_callback_oauth(
    codigo: str,
    db: Session = Depends(get_db),
    usuario: models.Usuario = Depends(usuario_actual),
):
    """Cambia el código de autorización por el token de acceso de la
    cuenta de Mercado Pago del propietario, y lo guarda."""
    _verificar_mercadopago_configurado()
    frontend_url = os.environ.get("FRONTEND_URL", "https://hausbix.com")
    redirect_uri = f"{frontend_url}/mercadopago-oauth-callback.html"

    resp = httpx.post(
        "https://api.mercadopago.com/oauth/token",
        json={
            "client_id": MP_APP_ID,
            "client_secret": MP_CLIENT_SECRET,
            "grant_type": "authorization_code",
            "code": codigo,
            "redirect_uri": redirect_uri,
        },
        timeout=10.0,
    )
    if resp.status_code != 200:
        raise HTTPException(status_code=400, detail="No se pudo completar la conexión con Mercado Pago")

    datos = resp.json()
    usuario.mercadopago_user_id = str(datos.get("user_id"))
    usuario.mercadopago_access_token = datos.get("access_token")
    usuario.mercadopago_refresh_token = datos.get("refresh_token")
    db.commit()
    return {"ok": True}


@router.get("/estado-onboarding")
def estado_onboarding_mercadopago(usuario: models.Usuario = Depends(usuario_actual)):
    return {"completo": bool(usuario.mercadopago_access_token)}


@router.delete("/desconectar")
def desconectar_mercadopago(db: Session = Depends(get_db), usuario: models.Usuario = Depends(usuario_actual)):
    usuario.mercadopago_user_id = None
    usuario.mercadopago_access_token = None
    usuario.mercadopago_refresh_token = None
    db.commit()
    return {"ok": True}
