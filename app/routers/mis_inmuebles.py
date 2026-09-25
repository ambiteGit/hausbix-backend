from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, joinedload

from .. import models, schemas
from ..database import get_db
from ..auth import usuario_actual
from .inmuebles import _validar_operaciones

router = APIRouter(prefix="/mis-inmuebles", tags=["mis-inmuebles"])


@router.get("", response_model=list[schemas.InmuebleOut])
def listar_mis_inmuebles(db: Session = Depends(get_db), usuario: models.Usuario = Depends(usuario_actual)):
    return (
        db.query(models.Inmueble)
        .options(joinedload(models.Inmueble.fotos), joinedload(models.Inmueble.operaciones))
        .filter(models.Inmueble.usuario_id == usuario.id)
        .order_by(models.Inmueble.fecha_creacion.desc())
        .all()
    )


@router.get("/estadisticas")
def mis_estadisticas(db: Session = Depends(get_db), usuario: models.Usuario = Depends(usuario_actual)):
    inmuebles = db.query(models.Inmueble).filter(models.Inmueble.usuario_id == usuario.id).all()
    guardados = db.query(models.Favorito).filter(models.Favorito.usuario_id == usuario.id).count()

    return {
        "anuncios_activos": sum(1 for i in inmuebles if i.activo),
        "guardados": guardados,
        "contactos_recibidos": sum(i.contactos_recibidos for i in inmuebles),
    }


@router.get("/{inmueble_id}", response_model=schemas.InmuebleOut)
def obtener_mi_inmueble(inmueble_id: str, db: Session = Depends(get_db), usuario: models.Usuario = Depends(usuario_actual)):
    inmueble = (
        db.query(models.Inmueble)
        .options(joinedload(models.Inmueble.fotos), joinedload(models.Inmueble.operaciones), joinedload(models.Inmueble.disponibilidad))
        .filter_by(id=inmueble_id, usuario_id=usuario.id)
        .first()
    )
    if not inmueble:
        raise HTTPException(status_code=404, detail="Anuncio no encontrado")
    return inmueble


@router.patch("/{inmueble_id}", response_model=schemas.InmuebleOut)
def editar_inmueble(
    inmueble_id: str,
    payload: schemas.InmuebleUpdate,
    db: Session = Depends(get_db),
    usuario: models.Usuario = Depends(usuario_actual),
):
    """Actualiza solo los campos enviados — el resto se queda como estaba."""
    inmueble = db.query(models.Inmueble).filter_by(id=inmueble_id, usuario_id=usuario.id).first()
    if not inmueble:
        raise HTTPException(status_code=404, detail="Anuncio no encontrado")

    tenia_temporal_antes = any(o.tipo_operacion == models.TipoOperacion.alquiler_temporal for o in inmueble.operaciones)

    cambios = payload.model_dump(exclude_unset=True, exclude={"fotos_nuevas", "orden_fotos", "operaciones"})
    for campo, valor in cambios.items():
        setattr(inmueble, campo, valor)

    # Si se envía la lista de operaciones, sustituye la combinación entera
    # (más simple y predecible que intentar hacer un "merge" parcial).
    if payload.operaciones is not None:
        _validar_operaciones(payload.operaciones)
        db.query(models.InmuebleOperacion).filter_by(inmueble_id=inmueble_id).delete()
        for op in payload.operaciones:
            db.add(models.InmuebleOperacion(inmueble_id=inmueble_id, **op.model_dump()))

        tiene_temporal_ahora = any(o.tipo_operacion == models.TipoOperacion.alquiler_temporal for o in payload.operaciones)
        if tenia_temporal_antes and not tiene_temporal_ahora:
            # Ya no es alquiler temporal -> el calendario deja de tener sentido
            db.query(models.Disponibilidad).filter_by(inmueble_id=inmueble_id).delete()

    if payload.fotos_nuevas:
        orden_inicial = len(inmueble.fotos)
        for i, url in enumerate(payload.fotos_nuevas):
            db.add(models.Foto(inmueble_id=inmueble.id, url=url, orden=orden_inicial + i))

    if payload.orden_fotos:
        fotos_por_id = {f.id: f for f in inmueble.fotos}
        for nuevo_orden, foto_id in enumerate(payload.orden_fotos):
            if foto_id in fotos_por_id:
                fotos_por_id[foto_id].orden = nuevo_orden

    db.commit()
    db.refresh(inmueble)
    return inmueble


@router.delete("/{inmueble_id}/fotos/{foto_id}", status_code=204)
def borrar_foto(
    inmueble_id: str,
    foto_id: str,
    db: Session = Depends(get_db),
    usuario: models.Usuario = Depends(usuario_actual),
):
    inmueble = db.query(models.Inmueble).filter_by(id=inmueble_id, usuario_id=usuario.id).first()
    if not inmueble:
        raise HTTPException(status_code=404, detail="Anuncio no encontrado")
    db.query(models.Foto).filter_by(id=foto_id, inmueble_id=inmueble_id).delete()
    db.commit()


@router.patch("/{inmueble_id}/activo")
def cambiar_estado(
    inmueble_id: str,
    activo: bool,
    db: Session = Depends(get_db),
    usuario: models.Usuario = Depends(usuario_actual),
):
    """Despublicar/republicar un anuncio sin borrarlo (mantiene fotos y disponibilidad)."""
    inmueble = db.query(models.Inmueble).filter_by(id=inmueble_id, usuario_id=usuario.id).first()
    if not inmueble:
        raise HTTPException(status_code=404, detail="Anuncio no encontrado")
    inmueble.activo = activo
    db.commit()
    return {"ok": True, "activo": inmueble.activo}


@router.put("/{inmueble_id}/disponibilidad")
def actualizar_disponibilidad(
    inmueble_id: str,
    dias: list[schemas.DisponibilidadIn],
    db: Session = Depends(get_db),
    usuario: models.Usuario = Depends(usuario_actual),
):
    """
    Reemplaza el calendario de disponibilidad del inmueble (solo aplica a
    inmuebles con la operación alquiler_temporal). Cada día enviado incluye
    su propio precio opcional — si no se especifica, la app usará el precio
    base de la operación temporal para ese día.
    """
    inmueble = (
        db.query(models.Inmueble)
        .options(joinedload(models.Inmueble.operaciones))
        .filter_by(id=inmueble_id, usuario_id=usuario.id)
        .first()
    )
    if not inmueble:
        raise HTTPException(status_code=404, detail="Anuncio no encontrado")

    tiene_temporal = any(o.tipo_operacion == models.TipoOperacion.alquiler_temporal for o in inmueble.operaciones)
    if not tiene_temporal:
        raise HTTPException(status_code=400, detail="Este anuncio no tiene activada la modalidad de alquiler temporal")

    db.query(models.Disponibilidad).filter_by(inmueble_id=inmueble_id).delete()
    for dia in dias:
        db.add(models.Disponibilidad(
            inmueble_id=inmueble_id, fecha=dia.fecha, estado=dia.estado, precio=dia.precio,
        ))
    db.commit()
    return {"ok": True, "dias_guardados": len(dias)}
