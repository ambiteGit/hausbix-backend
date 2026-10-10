from datetime import date as date_cls

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, joinedload

from .. import models, schemas
from ..database import get_db
from ..auth import usuario_actual, usuario_opcional
from .moderacion import ids_con_bloqueo
from ..resenas_ia import generar_resumen_opiniones

router = APIRouter(tags=["resenas"])


def _a_resena_out(r: models.Resena) -> schemas.ResenaOut:
    return schemas.ResenaOut(
        id=r.id, inmueble_id=r.inmueble_id, puntuacion=r.puntuacion, comentario=r.comentario,
        fecha_creacion=r.fecha_creacion, huesped_id=r.huesped_id,
        huesped_nombre=r.huesped.nombre if r.huesped else "Huésped de Hausbix",
        huesped_foto_url=r.huesped.foto_url if r.huesped else None,
    )


def _recalcular_resumen_inmueble(db: Session, inmueble: models.Inmueble) -> None:
    """Recalcula la media/total guardados en el propio anuncio, y vuelve a
    generar el resumen por IA a partir de TODAS sus reseñas. Se llama justo
    después de insertar una reseña nueva — así la ficha del anuncio puede
    leer estos campos directamente sin tener que recalcular nada ni volver
    a llamar a la IA en cada visita."""
    todas = db.query(models.Resena).filter(models.Resena.inmueble_id == inmueble.id).all()
    inmueble.resena_total = len(todas)
    inmueble.resena_media = round(sum(r.puntuacion for r in todas) / len(todas), 2) if todas else None
    # Best-effort: si falla la IA (sin API key, red caída...) se deja el
    # resumen anterior tal cual en vez de borrarlo — mejor un resumen algo
    # desactualizado que ninguno.
    nuevo_resumen = generar_resumen_opiniones(inmueble.titulo, [(r.puntuacion, r.comentario) for r in todas])
    if nuevo_resumen:
        inmueble.resena_resumen_ia = nuevo_resumen


@router.post("/resenas", response_model=schemas.ResenaOut, status_code=201)
def crear_resena(payload: schemas.ResenaCrear, db: Session = Depends(get_db), usuario: models.Usuario = Depends(usuario_actual)):
    reserva = db.query(models.Reserva).filter_by(id=payload.reserva_id).first()
    if not reserva:
        raise HTTPException(status_code=404, detail="Reserva no encontrada")
    if reserva.huesped_id != usuario.id:
        raise HTTPException(status_code=403, detail="Solo quien hizo la reserva puede opinar sobre esta estancia")
    if reserva.estado != models.EstadoReserva.confirmada:
        raise HTTPException(status_code=400, detail="Solo se puede opinar sobre una estancia confirmada")
    if reserva.fecha_salida >= date_cls.today():
        raise HTTPException(status_code=400, detail="Todavía no has completado esta estancia")

    ya_existe = db.query(models.Resena).filter_by(reserva_id=reserva.id).first()
    if ya_existe:
        raise HTTPException(status_code=400, detail="Ya has dejado una opinión sobre esta estancia")

    resena = models.Resena(
        inmueble_id=reserva.inmueble_id, reserva_id=reserva.id, huesped_id=usuario.id,
        puntuacion=payload.puntuacion, comentario=payload.comentario,
    )
    db.add(resena)
    db.flush()  # para que resena.id y resena.fecha_creacion ya estén asignados antes de refrescar

    inmueble = db.query(models.Inmueble).filter_by(id=reserva.inmueble_id).first()
    if inmueble:
        _recalcular_resumen_inmueble(db, inmueble)

    db.commit()
    db.refresh(resena)
    resena.huesped = usuario
    return _a_resena_out(resena)


@router.get("/inmuebles/{inmueble_id}/resenas", response_model=schemas.ResenasInmuebleOut)
def listar_resenas_inmueble(
    inmueble_id: str,
    db: Session = Depends(get_db),
    usuario: models.Usuario | None = Depends(usuario_opcional),
):
    inmueble = db.query(models.Inmueble).filter_by(id=inmueble_id).first()
    if not inmueble:
        raise HTTPException(status_code=404, detail="Inmueble no encontrado")

    resenas = (
        db.query(models.Resena)
        .options(joinedload(models.Resena.huesped))
        .filter(models.Resena.inmueble_id == inmueble_id)
        .order_by(models.Resena.fecha_creacion.desc())
        .all()
    )
    # Reseñas de usuarios bloqueados (en cualquiera de los dos sentidos): se
    # ocultan solo en la lista; la media y el total son los globales del anuncio.
    if usuario is not None:
        bloqueados = ids_con_bloqueo(db, usuario.id)
        if bloqueados:
            resenas = [r for r in resenas if r.huesped_id not in bloqueados]
    return schemas.ResenasInmuebleOut(
        media=inmueble.resena_media,
        total=inmueble.resena_total,
        resumen_ia=inmueble.resena_resumen_ia,
        resenas=[_a_resena_out(r) for r in resenas],
    )
