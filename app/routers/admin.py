from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session, joinedload

from .. import models, schemas
from ..database import get_db
from ..auth import usuario_actual, usuario_admin_actual, _emails_admin
from ..push import enviar_push

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/soy-admin")
def soy_admin(usuario: models.Usuario = Depends(usuario_actual)):
    """
    Para que el frontend sepa si debe mostrar el enlace al panel de admin,
    sin obligar a intentar entrar y toparse con un 403. No es la
    comprobación de seguridad en sí (esa la hace usuario_admin_actual en
    cada endpoint real de abajo) — esto es solo para la interfaz.
    """
    return {"es_admin": usuario.email.lower() in _emails_admin()}


@router.get("/pendientes", response_model=list[schemas.InmuebleOut])
def listar_pendientes(
    db: Session = Depends(get_db),
    _admin: models.Usuario = Depends(usuario_admin_actual),
):
    return (
        db.query(models.Inmueble)
        .options(
            joinedload(models.Inmueble.fotos),
            joinedload(models.Inmueble.operaciones),
            joinedload(models.Inmueble.usuario).joinedload(models.Usuario.empresa),
        )
        .filter(models.Inmueble.estado_moderacion == models.EstadoModeracion.pendiente)
        .order_by(models.Inmueble.fecha_creacion.asc())  # los más antiguos esperando, primero
        .all()
    )


@router.post("/inmuebles/{inmueble_id}/aprobar", response_model=schemas.InmuebleOut)
def aprobar_inmueble(
    inmueble_id: str,
    db: Session = Depends(get_db),
    _admin: models.Usuario = Depends(usuario_admin_actual),
):
    inmueble = db.query(models.Inmueble).filter_by(id=inmueble_id).first()
    if not inmueble:
        raise HTTPException(status_code=404, detail="Inmueble no encontrado")

    inmueble.estado_moderacion = models.EstadoModeracion.aprobado
    inmueble.motivo_rechazo = None
    db.commit()
    db.refresh(inmueble)

    dueno = db.query(models.Usuario).filter_by(id=inmueble.usuario_id).first()
    if dueno:
        enviar_push(dueno.push_token, "¡Anuncio aprobado!", f'Tu anuncio "{inmueble.titulo}" ya es visible en Hausbix.')

    return inmueble


class RechazarIn(BaseModel):
    motivo: str


@router.post("/inmuebles/{inmueble_id}/rechazar", response_model=schemas.InmuebleOut)
def rechazar_inmueble(
    inmueble_id: str,
    payload: RechazarIn,
    db: Session = Depends(get_db),
    _admin: models.Usuario = Depends(usuario_admin_actual),
):
    inmueble = db.query(models.Inmueble).filter_by(id=inmueble_id).first()
    if not inmueble:
        raise HTTPException(status_code=404, detail="Inmueble no encontrado")

    inmueble.estado_moderacion = models.EstadoModeracion.rechazado
    inmueble.motivo_rechazo = payload.motivo
    db.commit()
    db.refresh(inmueble)

    dueno = db.query(models.Usuario).filter_by(id=inmueble.usuario_id).first()
    if dueno:
        enviar_push(dueno.push_token, "Anuncio no aprobado", f'Tu anuncio "{inmueble.titulo}" necesita cambios antes de publicarse.')

    return inmueble
