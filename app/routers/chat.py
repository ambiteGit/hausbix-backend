from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import or_, func

from .. import models, schemas
from ..database import get_db
from ..auth import usuario_actual
from ..push import enviar_push

router = APIRouter(prefix="/conversaciones", tags=["chat"])


@router.post("", response_model=schemas.ConversacionDetalleOut, status_code=201)
def crear_o_recuperar_conversacion(
    payload: schemas.ConversacionCrear,
    db: Session = Depends(get_db),
    usuario: models.Usuario = Depends(usuario_actual),
):
    """
    Se llama al pulsar "Enviar mensaje por Hausbix" en un anuncio. Si ya
    existe una conversación entre este usuario y ese inmueble, la reutiliza
    en vez de crear una nueva (evita hilos duplicados sobre el mismo anuncio).
    """
    inmueble = db.query(models.Inmueble).filter(models.Inmueble.id == payload.inmueble_id).first()
    if not inmueble:
        raise HTTPException(status_code=404, detail="Inmueble no encontrado")

    if inmueble.usuario_id == usuario.id:
        raise HTTPException(status_code=400, detail="No puedes abrir un chat contigo mismo sobre tu propio anuncio")

    conversacion = (
        db.query(models.Conversacion)
        .filter_by(inmueble_id=payload.inmueble_id, comprador_id=usuario.id)
        .first()
    )
    if not conversacion:
        conversacion = models.Conversacion(
            inmueble_id=payload.inmueble_id,
            comprador_id=usuario.id,
            vendedor_id=inmueble.usuario_id,
        )
        db.add(conversacion)
        db.commit()
        db.refresh(conversacion)

    return _detalle_conversacion(conversacion, usuario.id, db)


@router.get("", response_model=list[schemas.ConversacionResumenOut])
def listar_mis_conversaciones(db: Session = Depends(get_db), usuario: models.Usuario = Depends(usuario_actual)):
    conversaciones = (
        db.query(models.Conversacion)
        .options(
            joinedload(models.Conversacion.inmueble).joinedload(models.Inmueble.fotos),
            joinedload(models.Conversacion.comprador),
            joinedload(models.Conversacion.vendedor),
        )
        .filter(or_(models.Conversacion.comprador_id == usuario.id, models.Conversacion.vendedor_id == usuario.id))
        .all()
    )

    resultado = []
    for c in conversaciones:
        otro = c.vendedor if c.comprador_id == usuario.id else c.comprador
        ultimo = (
            db.query(models.Mensaje)
            .filter_by(conversacion_id=c.id)
            .order_by(models.Mensaje.fecha_envio.desc())
            .first()
        )
        no_leidos = (
            db.query(func.count(models.Mensaje.id))
            .filter(
                models.Mensaje.conversacion_id == c.id,
                models.Mensaje.remitente_id != usuario.id,
                models.Mensaje.leido == False,  # noqa: E712
            )
            .scalar()
        )
        resultado.append(schemas.ConversacionResumenOut(
            id=c.id,
            inmueble_id=c.inmueble_id,
            inmueble_titulo=c.inmueble.titulo if c.inmueble else "",
            inmueble_foto=c.inmueble.fotos[0].url if c.inmueble and c.inmueble.fotos else None,
            otro_usuario_nombre=otro.nombre if otro else "Usuario",
            ultimo_mensaje=ultimo.texto if ultimo else None,
            ultimo_mensaje_fecha=ultimo.fecha_envio if ultimo else None,
            no_leidos=no_leidos or 0,
        ))

    resultado.sort(key=lambda r: r.ultimo_mensaje_fecha or datetime.min, reverse=True)
    return resultado


def _verificar_participante(conversacion: models.Conversacion, usuario_id: str):
    if usuario_id not in (conversacion.comprador_id, conversacion.vendedor_id):
        raise HTTPException(status_code=403, detail="No formas parte de esta conversación")


def _detalle_conversacion(conversacion: models.Conversacion, usuario_id: str, db: Session) -> schemas.ConversacionDetalleOut:
    db.refresh(conversacion)
    inmueble = db.query(models.Inmueble).filter_by(id=conversacion.inmueble_id).first()
    otro_id = conversacion.vendedor_id if usuario_id == conversacion.comprador_id else conversacion.comprador_id
    otro = db.query(models.Usuario).filter_by(id=otro_id).first()
    return schemas.ConversacionDetalleOut(
        id=conversacion.id,
        inmueble_id=conversacion.inmueble_id,
        inmueble_titulo=inmueble.titulo if inmueble else "",
        otro_usuario_nombre=otro.nombre if otro else "Usuario",
        mensajes=[schemas.MensajeOut.model_validate(m) for m in conversacion.mensajes],
    )


@router.get("/{conversacion_id}/mensajes", response_model=schemas.ConversacionDetalleOut)
def listar_mensajes(
    conversacion_id: str,
    db: Session = Depends(get_db),
    usuario: models.Usuario = Depends(usuario_actual),
):
    conversacion = (
        db.query(models.Conversacion)
        .options(joinedload(models.Conversacion.mensajes), joinedload(models.Conversacion.comprador), joinedload(models.Conversacion.vendedor))
        .filter_by(id=conversacion_id)
        .first()
    )
    if not conversacion:
        raise HTTPException(status_code=404, detail="Conversación no encontrada")
    _verificar_participante(conversacion, usuario.id)

    # Marcar como leídos los mensajes que no envió este usuario.
    db.query(models.Mensaje).filter(
        models.Mensaje.conversacion_id == conversacion_id,
        models.Mensaje.remitente_id != usuario.id,
        models.Mensaje.leido == False,  # noqa: E712
    ).update({"leido": True})
    db.commit()
    db.refresh(conversacion)

    otro = conversacion.vendedor if conversacion.comprador_id == usuario.id else conversacion.comprador
    inmueble = db.query(models.Inmueble).filter_by(id=conversacion.inmueble_id).first()

    return schemas.ConversacionDetalleOut(
        id=conversacion.id,
        inmueble_id=conversacion.inmueble_id,
        inmueble_titulo=inmueble.titulo if inmueble else "",
        otro_usuario_nombre=otro.nombre if otro else "Usuario",
        mensajes=[schemas.MensajeOut.model_validate(m) for m in conversacion.mensajes],
    )


@router.post("/{conversacion_id}/mensajes", response_model=schemas.MensajeOut, status_code=201)
def enviar_mensaje(
    conversacion_id: str,
    payload: schemas.MensajeIn,
    db: Session = Depends(get_db),
    usuario: models.Usuario = Depends(usuario_actual),
):
    conversacion = db.query(models.Conversacion).filter_by(id=conversacion_id).first()
    if not conversacion:
        raise HTTPException(status_code=404, detail="Conversación no encontrada")
    _verificar_participante(conversacion, usuario.id)

    if not payload.texto.strip():
        raise HTTPException(status_code=400, detail="El mensaje no puede estar vacío")

    mensaje = models.Mensaje(conversacion_id=conversacion_id, remitente_id=usuario.id, texto=payload.texto.strip())
    db.add(mensaje)
    db.commit()
    db.refresh(mensaje)

    # Avisar por push al otro participante, si tiene token registrado.
    destinatario_id = conversacion.vendedor_id if usuario.id == conversacion.comprador_id else conversacion.comprador_id
    destinatario = db.query(models.Usuario).filter_by(id=destinatario_id).first()
    if destinatario:
        enviar_push(
            destinatario.push_token,
            f"Nuevo mensaje de {usuario.nombre}",
            payload.texto.strip()[:100],
            data={"conversacionId": conversacion_id},
        )

    return mensaje
