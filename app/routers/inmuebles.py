from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, joinedload

from .. import models, schemas
from ..database import get_db
from ..auth import usuario_actual, usuario_opcional, _emails_admin
from ..push import enviar_push

router = APIRouter(prefix="/inmuebles", tags=["inmuebles"])


@router.get("", response_model=list[schemas.InmuebleOut])
def listar_inmuebles(
    tipo_operacion: models.TipoOperacion,
    sw_lat: Optional[float] = None,
    sw_lng: Optional[float] = None,
    ne_lat: Optional[float] = None,
    ne_lng: Optional[float] = None,
    precio_min: Optional[float] = None,
    precio_max: Optional[float] = None,
    m2_min: Optional[float] = None,
    m2_utiles_min: Optional[float] = None,
    habitaciones_min: Optional[int] = None,
    banos_min: Optional[int] = None,
    tipo_inmueble: Optional[models.TipoInmueble] = None,
    ascensor: Optional[bool] = None,
    terraza: Optional[bool] = None,
    garaje: Optional[bool] = None,
    trastero: Optional[bool] = None,
    aire_acondicionado: Optional[bool] = None,
    exterior: Optional[bool] = None,
    amueblado: Optional[bool] = None,
    mascotas_permitidas: Optional[bool] = None,
    piscina: Optional[bool] = None,
    urbanizacion_privada: Optional[bool] = None,
    db: Session = Depends(get_db),
):
    """
    Devuelve los inmuebles activos que tengan ACTIVA la operación pedida
    (un inmueble puede tener varias a la vez: venta + alquiler_temporal, etc.).
    El precio_min/precio_max filtra sobre el precio de ESA operación concreta,
    no sobre las demás que pueda tener el inmueble.

    Los filtros de características (ascensor, terraza...) solo se aplican si
    se envían explícitamente a `true` — no filtran nada si no se pasan, así
    que un inmueble sin ascensor no queda excluido a menos que el usuario
    pida expresamente "con ascensor".

    Nota de rendimiento: con PostGIS real, el filtro de zona debería ser
    espacial (ST_Within / índice GiST) en vez de comparar lat/lng sueltos.
    """
    query = (
        db.query(models.Inmueble)
        .join(models.InmuebleOperacion)
        .options(joinedload(models.Inmueble.fotos), joinedload(models.Inmueble.operaciones), joinedload(models.Inmueble.usuario).joinedload(models.Usuario.empresa))
        .filter(
            models.Inmueble.activo == True,  # noqa: E712
            models.Inmueble.estado_moderacion == models.EstadoModeracion.aprobado,
            models.InmuebleOperacion.tipo_operacion == tipo_operacion,
        )
    )

    if None not in (sw_lat, sw_lng, ne_lat, ne_lng):
        query = query.filter(
            models.Inmueble.lat.between(sw_lat, ne_lat),
            models.Inmueble.lng.between(sw_lng, ne_lng),
        )
    if precio_min is not None:
        query = query.filter(models.InmuebleOperacion.precio >= precio_min)
    if precio_max is not None:
        query = query.filter(models.InmuebleOperacion.precio <= precio_max)
    if m2_min is not None:
        query = query.filter(models.Inmueble.m2 >= m2_min)
    if m2_utiles_min is not None:
        query = query.filter(models.Inmueble.m2_utiles >= m2_utiles_min)
    if habitaciones_min is not None:
        query = query.filter(models.Inmueble.habitaciones >= habitaciones_min)
    if tipo_inmueble is not None:
        query = query.filter(models.Inmueble.tipo_inmueble == tipo_inmueble)

    caracteristicas = {
        "ascensor": ascensor,
        "terraza": terraza,
        "garaje": garaje,
        "trastero": trastero,
        "aire_acondicionado": aire_acondicionado,
        "exterior": exterior,
        "amueblado": amueblado,
        "mascotas_permitidas": mascotas_permitidas,
        "piscina": piscina,
        "urbanizacion_privada": urbanizacion_privada,
    }
    for nombre_columna, valor in caracteristicas.items():
        if valor is True:
            query = query.filter(getattr(models.Inmueble, nombre_columna) == True)  # noqa: E712
    if banos_min is not None:
        query = query.filter(models.Inmueble.banos >= banos_min)

    return query.order_by(models.Inmueble.destacado.desc(), models.Inmueble.fecha_creacion.desc()).limit(200).all()


@router.get("/{inmueble_id}", response_model=schemas.InmuebleOut)
def obtener_inmueble(inmueble_id: str, db: Session = Depends(get_db), usuario: Optional[models.Usuario] = Depends(usuario_opcional)):
    inmueble = db.query(models.Inmueble).options(
        joinedload(models.Inmueble.fotos),
        joinedload(models.Inmueble.operaciones),
        joinedload(models.Inmueble.disponibilidad),
        joinedload(models.Inmueble.usuario).joinedload(models.Usuario.empresa),
    ).filter(models.Inmueble.id == inmueble_id).first()

    if not inmueble:
        raise HTTPException(status_code=404, detail="Inmueble no encontrado")

    if inmueble.estado_moderacion != models.EstadoModeracion.aprobado:
        es_dueno = usuario is not None and usuario.id == inmueble.usuario_id
        es_admin = usuario is not None and usuario.email.lower() in _emails_admin()
        if not (es_dueno or es_admin):
            raise HTTPException(status_code=404, detail="Inmueble no encontrado")

    return inmueble


@router.post("/{inmueble_id}/contacto", status_code=204)
def registrar_contacto(inmueble_id: str, db: Session = Depends(get_db)):
    """
    Se llama justo antes de abrir WhatsApp (público, sin login). Contador
    simple, no guarda quién contacta.
    """
    inmueble = db.query(models.Inmueble).filter(models.Inmueble.id == inmueble_id).first()
    if not inmueble:
        raise HTTPException(status_code=404, detail="Inmueble no encontrado")
    inmueble.contactos_recibidos += 1
    db.commit()

    propietario = db.query(models.Usuario).filter(models.Usuario.id == inmueble.usuario_id).first()
    if propietario:
        enviar_push(
            propietario.push_token,
            "Nuevo contacto en Hausbix",
            f'Alguien se ha interesado por "{inmueble.titulo}" y va a escribirte por WhatsApp.',
            data={"inmuebleId": inmueble.id},
        )


def _validar_operaciones(operaciones: list[schemas.OperacionIn]):
    if not operaciones:
        raise HTTPException(status_code=400, detail="Selecciona al menos un tipo de operación")

    tipos_vistos = set()
    for op in operaciones:
        if op.tipo_operacion in tipos_vistos:
            raise HTTPException(status_code=400, detail=f"Operación duplicada: {op.tipo_operacion}")
        tipos_vistos.add(op.tipo_operacion)

        if op.tipo_operacion == models.TipoOperacion.alquiler_invernal:
            if not op.fecha_inicio or not op.fecha_fin:
                raise HTTPException(status_code=400, detail="El alquiler invernal necesita fecha de inicio y fin")
            if op.fecha_fin <= op.fecha_inicio:
                raise HTTPException(status_code=400, detail="La fecha de fin debe ser posterior a la de inicio")
        if op.precio is None and op.tipo_operacion != models.TipoOperacion.alquiler_temporal:
            raise HTTPException(status_code=400, detail=f"Falta el precio para {op.tipo_operacion}")


@router.post("", response_model=schemas.InmuebleOut, status_code=201)
def crear_inmueble(
    payload: schemas.InmuebleCreate,
    db: Session = Depends(get_db),
    usuario: models.Usuario = Depends(usuario_actual),
):
    if not usuario.telefono_verificado:
        raise HTTPException(status_code=403, detail="Debes verificar tu número de teléfono antes de publicar")

    _validar_operaciones(payload.operaciones)

    datos = payload.model_dump(exclude={"fotos", "operaciones"})
    inmueble = models.Inmueble(usuario_id=usuario.id, **datos)
    db.add(inmueble)
    db.flush()  # para tener inmueble.id antes del commit

    for op in payload.operaciones:
        db.add(models.InmuebleOperacion(inmueble_id=inmueble.id, **op.model_dump()))

    for orden, url in enumerate(payload.fotos):
        db.add(models.Foto(inmueble_id=inmueble.id, url=url, orden=orden))

    db.commit()
    db.refresh(inmueble)
    return inmueble
