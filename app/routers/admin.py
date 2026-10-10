from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session, joinedload

from .. import models, schemas
from ..database import get_db
from ..auth import usuario_actual, usuario_admin_actual, _emails_admin
from ..push import enviar_push

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/estado-pagos")
def estado_pagos(_admin: models.Usuario = Depends(usuario_admin_actual)):
    """
    Comprueba, sin revelar ninguna clave, si las variables de entorno de
    Stripe / Mercado Pago / PayPal llegaron bien a Render, y hace una
    llamada real a Stripe para ver si la clave funciona y en qué modo está.
    """
    import os

    def _hay(nombre):
        return bool(os.environ.get(nombre))

    clave = os.environ.get("STRIPE_SECRET_KEY") or ""
    stripe_conexion = {"ok": False, "detalle": "Falta STRIPE_SECRET_KEY."}
    if clave:
        try:
            import stripe as _stripe
            _stripe.api_key = clave
            cuenta = _stripe.Account.retrieve()
            stripe_conexion = {
                "ok": True,
                "pais": getattr(cuenta, "country", None),
                "cobros_activos": bool(getattr(cuenta, "charges_enabled", False)),
                "detalle": "La clave funciona.",
            }
        except Exception as e:  # noqa: BLE001 — queremos ver el motivo exacto
            stripe_conexion = {"ok": False, "detalle": getattr(e, "user_message", None) or str(e)}

    return {
        "stripe": {
            "STRIPE_SECRET_KEY": _hay("STRIPE_SECRET_KEY"),
            "STRIPE_WEBHOOK_SECRET": _hay("STRIPE_WEBHOOK_SECRET"),
        },
        "stripe_modo": "live" if clave.startswith(("sk_live_", "rk_live_")) else ("prueba" if clave else None),
        "stripe_conexion": stripe_conexion,
        "mercadopago": {
            "MERCADOPAGO_APP_ID": _hay("MERCADOPAGO_APP_ID"),
            "MERCADOPAGO_CLIENT_SECRET": _hay("MERCADOPAGO_CLIENT_SECRET"),
            "MERCADOPAGO_ACCESS_TOKEN": _hay("MERCADOPAGO_ACCESS_TOKEN"),
        },
        "paypal": {
            "PAYPAL_CLIENT_ID": _hay("PAYPAL_CLIENT_ID"),
            "PAYPAL_CLIENT_SECRET": _hay("PAYPAL_CLIENT_SECRET"),
            "PAYPAL_PARTNER_MERCHANT_ID": _hay("PAYPAL_PARTNER_MERCHANT_ID"),
            "PAYPAL_WEBHOOK_ID": _hay("PAYPAL_WEBHOOK_ID"),
        },
        "paypal_entorno": os.environ.get("PAYPAL_ENV") or "(sin definir)",
        "frontend_url": os.environ.get("FRONTEND_URL") or "(sin definir — se usa https://hausbix.com)",
    }


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
    inmueble.es_edicion_pendiente = False
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
    # Pasarelas de cobro que el usuario tiene listas para cobrar, y las que
    # empezó a conectar pero no terminó (útil para saber en qué se atascan).
    pasarelas: list[str] = []
    pasarelas_pendientes: list[str] = []


def _pasarelas_de(u: "models.Usuario") -> tuple[list[str], list[str]]:
    listas, pendientes = [], []
    if u.stripe_account_id:
        (listas if u.stripe_onboarding_completo else pendientes).append("Stripe")
    if u.mercadopago_access_token:
        listas.append("Mercado Pago")
    if u.paypal_merchant_id:
        (listas if u.paypal_onboarding_completo else pendientes).append("PayPal")
    return listas, pendientes


# ── Anuncios destacados (los elige el admin) ────────────────────────────────
# En la web, las dos primeras filas de "Destacados" (6 por fila en pantalla
# grande) son estos, en este orden; el resto son los más recientes. Si no
# hay ninguno elegido, todos son los más recientes.
MAX_DESTACADOS = 12


class DestacadosIn(BaseModel):
    ids: list[str]


@router.get("/destacados", response_model=list[schemas.InmuebleOut])
def listar_destacados_admin(
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
        .filter(models.Inmueble.destacado == True)  # noqa: E712
        .order_by(models.Inmueble.destacado_orden.asc(), models.Inmueble.fecha_creacion.desc())
        .all()
    )


@router.put("/destacados", response_model=list[schemas.InmuebleOut])
def fijar_destacados(
    payload: DestacadosIn,
    db: Session = Depends(get_db),
    _admin: models.Usuario = Depends(usuario_admin_actual),
):
    """Sustituye la lista de destacados por la recibida (en ese orden)."""
    ids = list(dict.fromkeys(payload.ids))
    if len(ids) > MAX_DESTACADOS:
        raise HTTPException(status_code=400, detail=f"Como máximo {MAX_DESTACADOS} destacados (las dos primeras filas).")
    nuevos = db.query(models.Inmueble).filter(models.Inmueble.id.in_(ids)).all() if ids else []
    por_id = {i.id: i for i in nuevos}
    for id_ in ids:
        inm = por_id.get(id_)
        if inm is None:
            raise HTTPException(status_code=404, detail="Alguno de los anuncios ya no existe.")
        if not inm.activo or inm.estado_moderacion != models.EstadoModeracion.aprobado:
            raise HTTPException(status_code=400, detail=f"«{inm.titulo}» no está aprobado y activo, no puede destacarse.")
    for actual in db.query(models.Inmueble).filter(models.Inmueble.destacado == True).all():  # noqa: E712
        actual.destacado = False
        actual.destacado_orden = None
    for posicion, id_ in enumerate(ids):
        por_id[id_].destacado = True
        por_id[id_].destacado_orden = posicion
    db.commit()
    return listar_destacados_admin(db=db, _admin=_admin)


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
        salida.pasarelas, salida.pasarelas_pendientes = _pasarelas_de(u)
        resultado.append(salida)
    return resultado


class BloquearUsuarioIn(BaseModel):
    motivo: str | None = None


@router.post("/usuarios/{usuario_id}/bloquear", response_model=UsuarioAdminOut)
def bloquear_usuario(
    usuario_id: str,
    payload: BloquearUsuarioIn,
    db: Session = Depends(get_db),
    admin: models.Usuario = Depends(usuario_admin_actual),
):
    """
    Bloquea la cuenta — no podrá iniciar sesión, y si ya tenía una sesión
    abierta se le rechaza en cuanto intente usar la API (ver usuario_actual
    en auth.py). No borra nada — a diferencia de eliminar_usuario_admin,
    es reversible con /desbloquear.
    """
    if usuario_id == admin.id:
        raise HTTPException(status_code=400, detail="No puedes bloquear tu propia cuenta")
    usuario = db.query(models.Usuario).filter_by(id=usuario_id).first()
    if not usuario:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")

    usuario.bloqueado = True
    usuario.motivo_bloqueo = payload.motivo
    db.commit()
    db.refresh(usuario)

    salida = UsuarioAdminOut.model_validate(usuario)
    salida.cantidad_anuncios = db.query(models.Inmueble).filter_by(usuario_id=usuario.id).count()
    return salida


@router.post("/usuarios/{usuario_id}/desbloquear", response_model=UsuarioAdminOut)
def desbloquear_usuario(
    usuario_id: str,
    db: Session = Depends(get_db),
    _admin: models.Usuario = Depends(usuario_admin_actual),
):
    usuario = db.query(models.Usuario).filter_by(id=usuario_id).first()
    if not usuario:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")

    usuario.bloqueado = False
    usuario.motivo_bloqueo = None
    db.commit()
    db.refresh(usuario)

    salida = UsuarioAdminOut.model_validate(usuario)
    salida.cantidad_anuncios = db.query(models.Inmueble).filter_by(usuario_id=usuario.id).count()
    return salida


@router.delete("/usuarios/{usuario_id}", status_code=204)
def eliminar_usuario_admin(
    usuario_id: str,
    db: Session = Depends(get_db),
    admin: models.Usuario = Depends(usuario_admin_actual),
):
    """
    Elimina la cuenta por moderación — igual que /auth/mi-cuenta (el propio
    usuario borrándose), pero decidido por un admin sobre la cuenta de
    cualquier otro. Arrastra sus anuncios, favoritos, etc. vía cascade,
    igual que el borrado propio.
    """
    if usuario_id == admin.id:
        raise HTTPException(status_code=400, detail="No puedes eliminar tu propia cuenta desde aquí")
    usuario = db.query(models.Usuario).filter_by(id=usuario_id).first()
    if not usuario:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
    db.delete(usuario)
    try:
        db.commit()
    except Exception:
        # Salvaguarda: esto depende de que las foreign keys hacia esta
        # cuenta (favoritos, conversaciones, mensajes, reservas...) tengan
        # ON DELETE CASCADE en la base de datos real — la migración de
        # arranque en main.py se encarga de eso, pero si por lo que sea
        # esa cuenta concreta sigue teniendo algo que lo impide, mejor un
        # mensaje claro que un error 500 sin explicación.
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="No se pudo eliminar — probablemente tiene reservas, conversaciones u otros datos vinculados que lo impiden.",
        )


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
    inmueble.es_edicion_pendiente = False
    db.commit()
    db.refresh(inmueble)

    dueno = db.query(models.Usuario).filter_by(id=inmueble.usuario_id).first()
    if dueno:
        enviar_push(dueno.push_token, "Anuncio no aprobado", f'Tu anuncio "{inmueble.titulo}" necesita cambios antes de publicarse.')

    return inmueble


# --- Reportes de contenido (moderación, guía 1.2 de la App Store) ------------

def _a_reporte_admin(r: models.Reporte) -> schemas.ReporteAdminOut:
    reportado = r.usuario_reportado
    return schemas.ReporteAdminOut(
        id=r.id, tipo_objetivo=r.tipo_objetivo.value, objetivo_id=r.objetivo_id, motivo=r.motivo.value,
        estado=r.estado.value, fecha_creacion=r.fecha_creacion, comentario=r.comentario, extracto=r.extracto,
        reportante_nombre=r.reportante.nombre if r.reportante else None,
        usuario_reportado_id=r.usuario_reportado_id,
        usuario_reportado_nombre=reportado.nombre if reportado else None,
        usuario_reportado_bloqueado=bool(reportado.bloqueado) if reportado else False,
        nota_resolucion=r.nota_resolucion, fecha_resolucion=r.fecha_resolucion,
    )


@router.get("/reportes", response_model=list[schemas.ReporteAdminOut])
def listar_reportes(
    estado: models.EstadoReporte | None = models.EstadoReporte.pendiente,
    limite: int = 300,
    db: Session = Depends(get_db),
    _admin: models.Usuario = Depends(usuario_admin_actual),
):
    """Reportes de usuarios sobre anuncios, mensajes, reseñas u otros
    usuarios. Por defecto solo los pendientes (los más antiguos primero, para
    atenderlos dentro de las 24 h prometidas); `estado` para ver los demás."""
    query = db.query(models.Reporte).options(joinedload(models.Reporte.reportante), joinedload(models.Reporte.usuario_reportado))
    if estado is not None:
        query = query.filter(models.Reporte.estado == estado)
    orden = models.Reporte.fecha_creacion.asc() if estado == models.EstadoReporte.pendiente else models.Reporte.fecha_creacion.desc()
    return [_a_reporte_admin(r) for r in query.order_by(orden).limit(limite).all()]


class ResolverReporteIn(BaseModel):
    accion: Literal["descartar", "eliminar_contenido", "bloquear_usuario", "eliminar_y_bloquear"]
    nota: str | None = None


def _eliminar_contenido_reportado(db: Session, reporte: models.Reporte) -> None:
    if reporte.tipo_objetivo == models.TipoObjetivoReporte.inmueble:
        obj = db.query(models.Inmueble).filter_by(id=reporte.objetivo_id).first()
        if obj:
            db.delete(obj)
    elif reporte.tipo_objetivo == models.TipoObjetivoReporte.mensaje:
        obj = db.query(models.Mensaje).filter_by(id=reporte.objetivo_id).first()
        if obj:
            db.delete(obj)
    elif reporte.tipo_objetivo == models.TipoObjetivoReporte.resena:
        from .resenas import _recalcular_resumen_inmueble
        obj = db.query(models.Resena).filter_by(id=reporte.objetivo_id).first()
        if obj:
            inmueble = db.query(models.Inmueble).filter_by(id=obj.inmueble_id).first()
            db.delete(obj)
            db.flush()
            if inmueble:
                _recalcular_resumen_inmueble(db, inmueble)  # media/total/resumen sin esta reseña
    else:
        raise HTTPException(status_code=400, detail="Un reporte sobre un usuario no tiene contenido que eliminar; usa «bloquear usuario»")


@router.post("/reportes/{reporte_id}/resolver", response_model=schemas.ReporteAdminOut)
def resolver_reporte(
    reporte_id: str,
    payload: ResolverReporteIn,
    db: Session = Depends(get_db),
    admin: models.Usuario = Depends(usuario_admin_actual),
):
    reporte = (
        db.query(models.Reporte)
        .options(joinedload(models.Reporte.reportante), joinedload(models.Reporte.usuario_reportado))
        .filter_by(id=reporte_id)
        .first()
    )
    if not reporte:
        raise HTTPException(status_code=404, detail="Reporte no encontrado")

    if payload.accion in ("eliminar_contenido", "eliminar_y_bloquear"):
        _eliminar_contenido_reportado(db, reporte)
    if payload.accion in ("bloquear_usuario", "eliminar_y_bloquear"):
        reportado = reporte.usuario_reportado
        if reportado is None:
            raise HTTPException(status_code=400, detail="El autor ya no existe")
        if reportado.id == admin.id:
            raise HTTPException(status_code=400, detail="No puedes bloquear tu propia cuenta")
        reportado.bloqueado = True
        reportado.motivo_bloqueo = payload.nota or f"Contenido reportado ({reporte.motivo.value})"

    # Se cierran también el resto de reportes pendientes sobre el mismo
    # objetivo — mismo contenido, ya revisado.
    nuevo_estado = models.EstadoReporte.descartado if payload.accion == "descartar" else models.EstadoReporte.resuelto
    pendientes = (
        db.query(models.Reporte)
        .filter_by(tipo_objetivo=reporte.tipo_objetivo, objetivo_id=reporte.objetivo_id, estado=models.EstadoReporte.pendiente)
        .all()
    )
    ahora = datetime.utcnow()
    for r in {reporte, *pendientes}:
        r.estado = nuevo_estado
        r.nota_resolucion = payload.nota
        r.fecha_resolucion = ahora
    db.commit()
    db.refresh(reporte)
    return _a_reporte_admin(reporte)
