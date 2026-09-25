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


@router.delete("/inmuebles/{inmueble_id}", status_code=204)
def eliminar_inmueble_admin(
    inmueble_id: str,
    db: Session = Depends(get_db),
    _admin: models.Usuario = Depends(usuario_admin_actual),
):
    """Borrado por moderación — no es lo mismo que el propietario borrando su
    propio anuncio; aquí basta con ser admin, sin importar de quién sea."""
    inmueble = db.query(models.Inmueble).filter_by(id=inmueble_id).first()
    if not inmueble:
        raise HTTPException(status_code=404, detail="Inmueble no encontrado")
    db.delete(inmueble)
    db.commit()


@router.get("/inmuebles", response_model=list[schemas.InmuebleOut])
def listar_todos_los_inmuebles(
    estado: models.EstadoModeracion | None = None,
    limite: int = 200,
    db: Session = Depends(get_db),
    _admin: models.Usuario = Depends(usuario_admin_actual),
):
    """Todos los anuncios (no solo los pendientes), para la pestaña
    "Anuncios" del panel — opcionalmente filtrados por estado."""
    query = db.query(models.Inmueble).options(
        joinedload(models.Inmueble.fotos),
        joinedload(models.Inmueble.operaciones),
        joinedload(models.Inmueble.usuario).joinedload(models.Usuario.empresa),
    )
    if estado is not None:
        query = query.filter(models.Inmueble.estado_moderacion == estado)
    return query.order_by(models.Inmueble.fecha_creacion.desc()).limit(limite).all()


class UsuarioAdminOut(schemas.UsuarioOut):
    cantidad_anuncios: int = 0


@router.get("/usuarios", response_model=list[UsuarioAdminOut])
def listar_usuarios(
    busqueda: str | None = None,
    limite: int = 300,
    db: Session = Depends(get_db),
    _admin: models.Usuario = Depends(usuario_admin_actual),
):
    """Todos los usuarios registrados, para la pestaña "Usuarios" del
    panel — con búsqueda opcional por nombre o email."""
    query = db.query(models.Usuario).options(joinedload(models.Usuario.empresa))
    if busqueda:
        patron = f"%{busqueda}%"
        query = query.filter(
            (models.Usuario.nombre.ilike(patron)) | (models.Usuario.email.ilike(patron))
        )
    usuarios = query.order_by(models.Usuario.fecha_registro.desc()).limit(limite).all()
    resultado = []
    for u in usuarios:
        salida = UsuarioAdminOut.model_validate(u)
        salida.cantidad_anuncios = (
            db.query(models.Inmueble).filter_by(usuario_id=u.id).count()
        )
        resultado.append(salida)
    return resultado


@router.get("/reservas", response_model=list[schemas.ReservaOut])
def listar_todas_las_reservas(
    limite: int = 300,
    db: Session = Depends(get_db),
    _admin: models.Usuario = Depends(usuario_admin_actual),
):
    """Todas las reservas de alquiler por fechas, para la pestaña
    "Reservas" del panel."""
    reservas = (
        db.query(models.Reserva)
        .options(joinedload(models.Reserva.inmueble).joinedload(models.Inmueble.fotos))
        .order_by(models.Reserva.fecha_creacion.desc())
        .limit(limite)
        .all()
    )
    resultado = []
    for r in reservas:
        salida = schemas.ReservaOut.model_validate(r)
        salida.inmueble_titulo = r.inmueble.titulo if r.inmueble else None
        salida.inmueble_foto = r.inmueble.fotos[0].url if r.inmueble and r.inmueble.fotos else None
        huesped = db.query(models.Usuario).filter_by(id=r.huesped_id).first()
        propietario = db.query(models.Usuario).filter_by(id=r.propietario_id).first()
        salida.otro_nombre = f"{huesped.nombre if huesped else '?'} → {propietario.nombre if propietario else '?'}"
        resultado.append(salida)
    return resultado


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
