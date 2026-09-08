from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, joinedload

from .. import models, schemas
from ..database import get_db
from ..auth import usuario_actual

router = APIRouter(prefix="/favoritos", tags=["favoritos"])


@router.get("", response_model=list[schemas.InmuebleOut])
def listar_favoritos(db: Session = Depends(get_db), usuario: models.Usuario = Depends(usuario_actual)):
    favoritos = (
        db.query(models.Favorito)
        .options(
            joinedload(models.Favorito.inmueble).joinedload(models.Inmueble.fotos),
            joinedload(models.Favorito.inmueble).joinedload(models.Inmueble.operaciones),
        )
        .filter(models.Favorito.usuario_id == usuario.id)
        .all()
    )
    return [f.inmueble for f in favoritos]


@router.post("/{inmueble_id}", status_code=201)
def guardar_favorito(inmueble_id: str, db: Session = Depends(get_db), usuario: models.Usuario = Depends(usuario_actual)):
    existente = db.query(models.Favorito).filter_by(usuario_id=usuario.id, inmueble_id=inmueble_id).first()
    if existente:
        return {"ok": True}

    inmueble = db.query(models.Inmueble).filter(models.Inmueble.id == inmueble_id).first()
    if not inmueble:
        raise HTTPException(status_code=404, detail="Inmueble no encontrado")

    db.add(models.Favorito(usuario_id=usuario.id, inmueble_id=inmueble_id))
    db.commit()
    return {"ok": True}


@router.delete("/{inmueble_id}", status_code=204)
def quitar_favorito(inmueble_id: str, db: Session = Depends(get_db), usuario: models.Usuario = Depends(usuario_actual)):
    db.query(models.Favorito).filter_by(usuario_id=usuario.id, inmueble_id=inmueble_id).delete()
    db.commit()
