import os

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from .. import models, schemas
from ..database import get_db
from ..auth import usuario_actual

router = APIRouter(prefix="/notificaciones", tags=["notificaciones"])


@router.get("/vapid-public-key")
def obtener_clave_publica_vapid():
    """La web la necesita para suscribirse (pushManager.subscribe) — es
    pública por diseño, no hace falta autenticación para pedirla."""
    return {"clave_publica": os.environ.get("VAPID_PUBLIC_KEY")}


@router.post("/suscribir-web", status_code=201)
def suscribir_web_push(
    payload: schemas.SuscripcionWebPushIn,
    db: Session = Depends(get_db),
    usuario: models.Usuario = Depends(usuario_actual),
):
    ya_existe = db.query(models.SuscripcionWebPush).filter_by(endpoint=payload.endpoint).first()
    if ya_existe:
        # Mismo navegador volviendo a suscribirse (p. ej. tras borrar caché) —
        # se actualiza el dueño y las claves en vez de duplicar la fila.
        ya_existe.usuario_id = usuario.id
        ya_existe.clave_p256dh = payload.clave_p256dh
        ya_existe.clave_auth = payload.clave_auth
    else:
        db.add(models.SuscripcionWebPush(
            usuario_id=usuario.id,
            endpoint=payload.endpoint,
            clave_p256dh=payload.clave_p256dh,
            clave_auth=payload.clave_auth,
        ))
    db.commit()
    return {"ok": True}


@router.delete("/suscribir-web", status_code=204)
def desuscribir_web_push(
    endpoint: str,
    db: Session = Depends(get_db),
    usuario: models.Usuario = Depends(usuario_actual),
):
    db.query(models.SuscripcionWebPush).filter_by(endpoint=endpoint, usuario_id=usuario.id).delete()
    db.commit()
