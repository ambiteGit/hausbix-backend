from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, joinedload

from .. import models, schemas
from ..database import get_db
from ..auth import usuario_actual, usuario_opcional, _emails_admin
from ..push import enviar_push

router = APIRouter(prefix="/inmuebles", tags=["inmuebles"])


def _a_salida_publica(inmueble: models.Inmueble, mostrar_exacta_siempre: bool = False) -> schemas.InmuebleOut:
    """Construye la salida pública de un inmueble, sustituyendo lat/lng por
    la ubicación aproximada cuando el propietario ha optado por ocultar la
    exacta. mostrar_exacta_siempre se usa para el propio dueño/admin, que sí
    debe ver la ubicación real (p. ej. para poder editarla)."""
    salida = schemas.InmuebleOut.model_validate(inmueble)
    if not mostrar_exacta_siempre and not inmueble.mostrar_ubicacion_exacta and inmueble.lat_aproximada is not None:
        salida.lat = inmueble.lat_aproximada
        salida.lng = inmueble.lng_aproximada
    return salida


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
    m2_terreno_min: Optional[float] = None,
    habitaciones_min: Optional[int] = None,
    banos_min: Optional[int] = None,
    tipo_inmueble: Optional[models.TipoInmueble] = None,
    moneda: Optional[models.Moneda] = None,
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
    servicio_wifi: Optional[bool] = None,
    servicio_tv: Optional[bool] = None,
    servicio_secador_pelo: Optional[bool] = None,
    servicio_jacuzzi: Optional[bool] = None,
    servicio_lavadora: Optional[bool] = None,
    servicio_cocina_equipada: Optional[bool] = None,
    servicio_calefaccion: Optional[bool] = None,
    tipo_alojamiento: Optional[models.TipoAlojamiento] = None,
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
    if m2_terreno_min is not None:
        query = query.filter(models.Inmueble.m2_terreno >= m2_terreno_min)
    if habitaciones_min is not None:
        query = query.filter(models.Inmueble.habitaciones >= habitaciones_min)
    if tipo_inmueble is not None:
        query = query.filter(models.Inmueble.tipo_inmueble == tipo_inmueble)
    if moneda is not None:
        query = query.filter(models.Inmueble.moneda == moneda)
    if tipo_alojamiento is not None:
        query = query.filter(models.Inmueble.tipo_alojamiento == tipo_alojamiento)

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
        "servicio_wifi": servicio_wifi,
        "servicio_tv": servicio_tv,
        "servicio_secador_pelo": servicio_secador_pelo,
        "servicio_jacuzzi": servicio_jacuzzi,
        "servicio_lavadora": servicio_lavadora,
        "servicio_cocina_equipada": servicio_cocina_equipada,
        "servicio_calefaccion": servicio_calefaccion,
    }
    for nombre_columna, valor in caracteristicas.items():
        if valor is True:
            query = query.filter(getattr(models.Inmueble, nombre_columna) == True)  # noqa: E712
    if banos_min is not None:
        query = query.filter(models.Inmueble.banos >= banos_min)

    inmuebles = query.order_by(models.Inmueble.destacado.desc(), models.Inmueble.fecha_creacion.desc()).limit(200).all()
    return [_a_salida_publica(inm) for inm in inmuebles]


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

    es_dueno = usuario is not None and usuario.id == inmueble.usuario_id
    es_admin = usuario is not None and usuario.email.lower() in _emails_admin()

    if inmueble.estado_moderacion != models.EstadoModeracion.aprobado:
        if not (es_dueno or es_admin):
            raise HTTPException(status_code=404, detail="Inmueble no encontrado")

    return _a_salida_publica(inmueble, mostrar_exacta_siempre=(es_dueno or es_admin))


@router.get("/{inmueble_id}/pasarelas-disponibles")
def pasarelas_disponibles(inmueble_id: str, db: Session = Depends(get_db)):
    """Qué pasarelas de pago tiene conectadas el propietario de este
    inmueble — lo necesita el huésped para saber cuáles puede elegir al
    reservar. No expone ningún otro dato del propietario."""
    inmueble = db.query(models.Inmueble).filter_by(id=inmueble_id).first()
    if not inmueble:
        raise HTTPException(status_code=404, detail="Inmueble no encontrado")
    propietario = db.query(models.Usuario).filter_by(id=inmueble.usuario_id).first()
    if not propietario:
        return {"stripe": False, "mercadopago": False}
    return {
        "stripe": bool(propietario.stripe_account_id and propietario.stripe_onboarding_completo),
        "mercadopago": bool(propietario.mercadopago_access_token),
    }


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


def _validar_servicios_alquiler_temporal(payload: schemas.InmuebleCreate):
    """El alquiler por fechas es una estancia, no solo un inmueble — quien
    va a alojarse necesita saber de antemano qué servicios tiene (wifi,
    lavadora...), así que si se publica esta modalidad es obligatorio
    indicar algo aquí, no dejarlo todo en blanco."""
    es_alquiler_temporal = any(op.tipo_operacion == models.TipoOperacion.alquiler_temporal for op in payload.operaciones)
    if not es_alquiler_temporal:
        return
    algun_servicio = any([
        payload.servicio_wifi, payload.servicio_tv, payload.servicio_secador_pelo,
        payload.servicio_jacuzzi, payload.servicio_lavadora, payload.servicio_cocina_equipada,
        payload.servicio_calefaccion,
    ])
    algo_en_texto_libre = bool(payload.servicios_adicionales_texto and payload.servicios_adicionales_texto.strip())
    if not algun_servicio and not algo_en_texto_libre:
        raise HTTPException(
            status_code=400,
            detail="Indica los servicios de tu alojamiento (wifi, lavadora, etc.) — es obligatorio para alquiler por fechas",
        )


def _calcular_ubicacion_aproximada(lat: float, lng: float, radio_metros: int) -> tuple[float, float]:
    """Desplaza aleatoriamente el punto dentro de un radio menor que el
    círculo que se va a dibujar — si el centro del círculo fuera la
    dirección real, conocer el radio delataría la ubicación exacta igual."""
    import random
    import math
    radio_jitter = radio_metros * 0.6  # desplazamiento menor que el radio mostrado, para que el punto real siga dentro del círculo
    angulo = random.uniform(0, 2 * math.pi)
    distancia = random.uniform(0, radio_jitter)
    delta_lat = (distancia * math.cos(angulo)) / 111_320  # metros → grados de latitud, aprox.
    delta_lng = (distancia * math.sin(angulo)) / (111_320 * math.cos(math.radians(lat)) or 1)
    return lat + delta_lat, lng + delta_lng


def _validar_registro_vivienda_alemania(payload: schemas.InmuebleCreate):
    """Reglamento (UE) 2024/1028 — municipios con ordenanza propia (en
    Alemania: Aachen, Colonia y otras ciudades de NRW) exigen mostrar el
    número de registro de la vivienda en cualquier anuncio de corta
    duración. No se valida por ciudad exacta (no tenemos ese dato
    estructurado todavía) — se exige para toda Alemania por precaución,
    ya que es mejor pedir un dato de más que publicar sin él donde sí
    hace falta."""
    if payload.pais != "DE":
        return
    es_alquiler_temporal = any(op.tipo_operacion == models.TipoOperacion.alquiler_temporal for op in payload.operaciones)
    if not es_alquiler_temporal:
        return
    if not payload.numero_registro_vivienda or not payload.numero_registro_vivienda.strip():
        raise HTTPException(
            status_code=400,
            detail="Indica el número de registro de la vivienda (Wohnraum-ID) — obligatorio para alquiler por fechas en Alemania",
        )


def _validar_fotos_minimas(payload: schemas.InmuebleCreate):
    """Un terreno no siempre tiene mucho que fotografiar (a veces es solo
    una parcela vacía) — el resto de tipos sí necesita un mínimo para dar
    confianza a quien mira el anuncio."""
    if payload.tipo_inmueble == models.TipoInmueble.terreno:
        return
    if len(payload.fotos) < 4:
        raise HTTPException(status_code=400, detail="Sube al menos 4 fotos del inmueble")


@router.post("", response_model=schemas.InmuebleOut, status_code=201)
def crear_inmueble(
    payload: schemas.InmuebleCreate,
    db: Session = Depends(get_db),
    usuario: models.Usuario = Depends(usuario_actual),
):
    if not usuario.telefono_verificado:
        raise HTTPException(status_code=403, detail="Debes verificar tu número de teléfono antes de publicar")

    _validar_operaciones(payload.operaciones)
    _validar_servicios_alquiler_temporal(payload)
    _validar_registro_vivienda_alemania(payload)
    _validar_fotos_minimas(payload)

    datos = payload.model_dump(exclude={"fotos", "operaciones"})

    # Método de radio: si se pidió ocultar la ubicación exacta con un radio
    # y no se envió ya un punto aproximado (p. ej. porque el cliente usó el
    # método de pin manual), se calcula aquí uno aleatorio — una sola vez,
    # para que no "salte" de sitio en cada visita.
    if not payload.mostrar_ubicacion_exacta and payload.radio_privacidad_metros and not payload.lat_aproximada:
        lat_aprox, lng_aprox = _calcular_ubicacion_aproximada(payload.lat, payload.lng, payload.radio_privacidad_metros)
        datos["lat_aproximada"] = lat_aprox
        datos["lng_aproximada"] = lng_aprox

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
