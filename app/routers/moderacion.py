"""
Moderación de contenido generado por usuarios (guía 1.2 de la App Store):
reportar anuncios, mensajes, reseñas y usuarios, y bloquear a otros usuarios.

Los reportes los revisa un admin desde el panel (ver admin.py,
/admin/reportes). Al llegar un reporte nuevo se avisa por push a los admins
que tengan token registrado, para poder responder en el plazo de 24 h que
prometen los términos de uso.
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import or_
from sqlalchemy.orm import Session, joinedload

from .. import models, schemas
from ..database import get_db
from ..auth import usuario_actual, _emails_admin
from ..ratelimit import limitar
from ..push import enviar_push

router = APIRouter(tags=["moderacion"])


# --- Utilidades compartidas (las usan chat.py, inmuebles.py y resenas.py) ----

def ids_bloqueados_por(db: Session, usuario_id: str) -> set[str]:
    """Usuarios que ESTE usuario ha bloqueado."""
    return {f[0] for f in db.query(models.BloqueoUsuario.bloqueado_id).filter_by(bloqueador_id=usuario_id).all()}


def ids_con_bloqueo(db: Session, usuario_id: str) -> set[str]:
    """Usuarios con los que hay un bloqueo en cualquiera de los dos sentidos
    (los que yo bloqueé + los que me bloquearon a mí): con ninguno de ellos
    se ven anuncios, reseñas ni conversaciones."""
    filas = (
        db.query(models.BloqueoUsuario.bloqueador_id, models.BloqueoUsuario.bloqueado_id)
        .filter(or_(models.BloqueoUsuario.bloqueador_id == usuario_id, models.BloqueoUsuario.bloqueado_id == usuario_id))
        .all()
    )
    resultado: set[str] = set()
    for bloqueador, bloqueado in filas:
        resultado.add(bloqueado if bloqueador == usuario_id else bloqueador)
    return resultado


def hay_bloqueo_entre(db: Session, a: str, b: str) -> bool:
    return (
        db.query(models.BloqueoUsuario.id)
        .filter(
            or_(
                (models.BloqueoUsuario.bloqueador_id == a) & (models.BloqueoUsuario.bloqueado_id == b),
                (models.BloqueoUsuario.bloqueador_id == b) & (models.BloqueoUsuario.bloqueado_id == a),
            )
        )
        .first()
        is not None
    )


# --- Reportes -----------------------------------------------------------------

def _resolver_objetivo(db: Session, tipo: str, objetivo_id: str, reportante: models.Usuario) -> tuple[str | None, str | None]:
    """Devuelve (id del autor denunciado, extracto del contenido) y valida que
    el objetivo exista y que se pueda denunciar (no el propio contenido)."""
    if tipo == "inmueble":
        inmueble = db.query(models.Inmueble).filter_by(id=objetivo_id).first()
        if not inmueble:
            raise HTTPException(status_code=404, detail="Anuncio no encontrado")
        autor, extracto = inmueble.usuario_id, inmueble.titulo
    elif tipo == "mensaje":
        mensaje = db.query(models.Mensaje).options(joinedload(models.Mensaje.conversacion)).filter_by(id=objetivo_id).first()
        if not mensaje:
            raise HTTPException(status_code=404, detail="Mensaje no encontrado")
        conv = mensaje.conversacion
        if reportante.id not in (conv.comprador_id, conv.vendedor_id):
            raise HTTPException(status_code=403, detail="No formas parte de esta conversación")
        autor, extracto = mensaje.remitente_id, (mensaje.texto or "")[:500]
    elif tipo == "resena":
        resena = db.query(models.Resena).filter_by(id=objetivo_id).first()
        if not resena:
            raise HTTPException(status_code=404, detail="Reseña no encontrada")
        autor = resena.huesped_id
        extracto = f"{resena.puntuacion}/5 — {(resena.comentario or '(sin comentario)')[:500]}"
    else:  # usuario
        usuario = db.query(models.Usuario).filter_by(id=objetivo_id).first()
        if not usuario:
            raise HTTPException(status_code=404, detail="Usuario no encontrado")
        autor, extracto = usuario.id, usuario.nombre

    if autor == reportante.id:
        raise HTTPException(status_code=400, detail="No puedes reportar tu propio contenido")
    return autor, extracto


@router.post("/reportes", response_model=schemas.ReporteOut, status_code=201)
def crear_reporte(
    payload: schemas.ReporteCrear,
    db: Session = Depends(get_db),
    usuario: models.Usuario = Depends(usuario_actual),
):
    limitar("reportes", usuario.id, 30, 3600)
    autor_id, extracto = _resolver_objetivo(db, payload.tipo_objetivo, payload.objetivo_id, usuario)

    # Idempotente: si ya lo denunció, se devuelve el reporte existente en vez
    # de fallar o duplicarlo (un doble toque no debe dar error).
    existente = (
        db.query(models.Reporte)
        .filter_by(reportante_id=usuario.id, tipo_objetivo=payload.tipo_objetivo, objetivo_id=payload.objetivo_id)
        .first()
    )
    if existente:
        return existente

    reporte = models.Reporte(
        reportante_id=usuario.id,
        tipo_objetivo=payload.tipo_objetivo,
        objetivo_id=payload.objetivo_id,
        usuario_reportado_id=autor_id,
        motivo=payload.motivo,
        comentario=payload.comentario,
        extracto=extracto,
    )
    db.add(reporte)
    db.commit()
    db.refresh(reporte)

    # Aviso a los admins — best-effort, un fallo de push nunca debe impedir
    # que el reporte quede guardado.
    try:
        emails = _emails_admin()
        if emails:
            admins = db.query(models.Usuario).filter(models.Usuario.email.in_(list(emails))).all()
            for admin in admins:
                enviar_push(admin.push_token, "Nuevo reporte en Hausbix", f"Motivo: {payload.motivo.replace('_', ' ')}")
    except Exception:
        pass

    return reporte


# --- Bloqueos -----------------------------------------------------------------

@router.get("/bloqueos", response_model=list[schemas.BloqueoOut])
def listar_bloqueos(db: Session = Depends(get_db), usuario: models.Usuario = Depends(usuario_actual)):
    bloqueos = (
        db.query(models.BloqueoUsuario)
        .options(joinedload(models.BloqueoUsuario.bloqueado))
        .filter_by(bloqueador_id=usuario.id)
        .order_by(models.BloqueoUsuario.fecha_creacion.desc())
        .all()
    )
    return [
        schemas.BloqueoOut(
            usuario_id=b.bloqueado_id,
            nombre=b.bloqueado.nombre if b.bloqueado else "Usuario",
            foto_url=b.bloqueado.foto_url if b.bloqueado else None,
            fecha_creacion=b.fecha_creacion,
        )
        for b in bloqueos
    ]


@router.post("/bloqueos", response_model=schemas.BloqueoOut, status_code=201)
def bloquear_usuario(
    payload: schemas.BloqueoCrear,
    db: Session = Depends(get_db),
    usuario: models.Usuario = Depends(usuario_actual),
):
    if payload.usuario_id == usuario.id:
        raise HTTPException(status_code=400, detail="No puedes bloquearte a ti mismo")
    otro = db.query(models.Usuario).filter_by(id=payload.usuario_id).first()
    if not otro:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")

    bloqueo = db.query(models.BloqueoUsuario).filter_by(bloqueador_id=usuario.id, bloqueado_id=otro.id).first()
    if not bloqueo:
        bloqueo = models.BloqueoUsuario(bloqueador_id=usuario.id, bloqueado_id=otro.id)
        db.add(bloqueo)
        db.commit()
        db.refresh(bloqueo)

    return schemas.BloqueoOut(
        usuario_id=otro.id, nombre=otro.nombre, foto_url=otro.foto_url, fecha_creacion=bloqueo.fecha_creacion,
    )


@router.delete("/bloqueos/{usuario_id}", status_code=204)
def desbloquear_usuario(
    usuario_id: str,
    db: Session = Depends(get_db),
    usuario: models.Usuario = Depends(usuario_actual),
):
    db.query(models.BloqueoUsuario).filter_by(bloqueador_id=usuario.id, bloqueado_id=usuario_id).delete()
    db.commit()
