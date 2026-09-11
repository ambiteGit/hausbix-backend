from datetime import datetime, date
from typing import Optional
from pydantic import BaseModel, EmailStr, ConfigDict

from .models import TipoOperacion, TipoInmueble, EstadoDisponibilidad


class FotoOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    url: str
    orden: int


class DisponibilidadIn(BaseModel):
    fecha: date
    estado: EstadoDisponibilidad = EstadoDisponibilidad.libre
    precio: Optional[float] = None  # si es None, se usa el precio base de la operación temporal


class DisponibilidadOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    fecha: date
    estado: EstadoDisponibilidad
    precio: Optional[float] = None


class OperacionIn(BaseModel):
    """
    Una modalidad activa del anuncio. Un inmueble puede enviar varias a la vez
    (ej. venta + alquiler_temporal) y el usuario elige la combinación en el
    paso 1 de publicar.
    """
    tipo_operacion: TipoOperacion
    precio: Optional[float] = None
    fecha_inicio: Optional[date] = None  # solo alquiler_invernal
    fecha_fin: Optional[date] = None  # solo alquiler_invernal
    noches_minimas: Optional[int] = None  # solo alquiler_temporal


class OperacionOut(OperacionIn):
    model_config = ConfigDict(from_attributes=True)
    id: str


class InmuebleCreate(BaseModel):
    tipo_inmueble: TipoInmueble
    titulo: str
    descripcion: str
    lat: float
    lng: float
    direccion: str
    m2: float  # metros construidos
    m2_utiles: Optional[float] = None  # superficie útil/total, opcional
    m2_terreno: Optional[float] = None  # superficie del terreno/parcela, opcional
    gastos_comunes: Optional[float] = None
    contribucion_inmobiliaria: Optional[float] = None
    impuesto_primaria: Optional[float] = None
    pais: str = "ES"
    moneda: str = "EUR"
    habitaciones: int = 0
    banos: int = 0
    ascensor: bool = False
    terraza: bool = False
    garaje: bool = False
    trastero: bool = False
    aire_acondicionado: bool = False
    exterior: bool = False
    amueblado: bool = False
    mascotas_permitidas: bool = False
    piscina: bool = False
    urbanizacion_privada: bool = False
    mostrar_ubicacion_exacta: bool = True
    radio_privacidad_metros: Optional[int] = None
    lat_aproximada: Optional[float] = None
    lng_aproximada: Optional[float] = None
    servicio_wifi: bool = False
    servicio_tv: bool = False
    servicio_secador_pelo: bool = False
    servicio_jacuzzi: bool = False
    servicio_lavadora: bool = False
    servicio_cocina_equipada: bool = False
    servicio_calefaccion: bool = False
    servicios_adicionales_texto: Optional[str] = None
    numero_registro_vivienda: Optional[str] = None
    tipo_alojamiento: Optional[models.TipoAlojamiento] = None
    telefono_contacto: str
    operaciones: list[OperacionIn]  # al menos una
    fotos: list[str] = []  # URLs ya subidas a Cloudinary desde el cliente


class AnuncianteOut(BaseModel):
    """Quién publica el anuncio — particular, o inmobiliaria con su nombre/logo."""
    nombre: str
    tipo_cuenta: str = "particular"
    empresa_nombre: Optional[str] = None
    empresa_logo_url: Optional[str] = None


class InmuebleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    usuario_id: str
    tipo_inmueble: TipoInmueble
    titulo: str
    descripcion: str
    lat: float
    lng: float
    direccion: str
    m2: float
    m2_utiles: Optional[float] = None
    m2_terreno: Optional[float] = None
    gastos_comunes: Optional[float] = None
    contribucion_inmobiliaria: Optional[float] = None
    impuesto_primaria: Optional[float] = None
    pais: str = "ES"
    moneda: str = "EUR"
    habitaciones: int
    banos: int
    ascensor: bool
    terraza: bool
    garaje: bool
    trastero: bool
    aire_acondicionado: bool
    exterior: bool
    amueblado: bool
    mascotas_permitidas: bool
    piscina: bool
    urbanizacion_privada: bool = False
    mostrar_ubicacion_exacta: bool = True
    radio_privacidad_metros: Optional[int] = None
    servicio_wifi: bool = False
    servicio_tv: bool = False
    servicio_secador_pelo: bool = False
    servicio_jacuzzi: bool = False
    servicio_lavadora: bool = False
    servicio_cocina_equipada: bool = False
    servicio_calefaccion: bool = False
    servicios_adicionales_texto: Optional[str] = None
    numero_registro_vivienda: Optional[str] = None
    tipo_alojamiento: Optional[models.TipoAlojamiento] = None
    estado_moderacion: str = "pendiente"
    motivo_rechazo: Optional[str] = None
    telefono_contacto: str
    destacado: bool
    activo: bool
    fecha_creacion: datetime
    fotos: list[FotoOut] = []
    operaciones: list[OperacionOut] = []
    disponibilidad: list[DisponibilidadOut] = []
    anunciante: Optional[AnuncianteOut] = None


class InmuebleUpdate(BaseModel):
    """Todos los campos opcionales — PATCH solo actualiza lo que se envía."""
    tipo_inmueble: Optional[TipoInmueble] = None
    titulo: Optional[str] = None
    descripcion: Optional[str] = None
    direccion: Optional[str] = None
    lat: Optional[float] = None
    lng: Optional[float] = None
    m2: Optional[float] = None
    m2_utiles: Optional[float] = None
    m2_terreno: Optional[float] = None
    gastos_comunes: Optional[float] = None
    contribucion_inmobiliaria: Optional[float] = None
    impuesto_primaria: Optional[float] = None
    pais: Optional[str] = None
    moneda: Optional[str] = None
    habitaciones: Optional[int] = None
    banos: Optional[int] = None
    ascensor: Optional[bool] = None
    terraza: Optional[bool] = None
    garaje: Optional[bool] = None
    trastero: Optional[bool] = None
    aire_acondicionado: Optional[bool] = None
    exterior: Optional[bool] = None
    amueblado: Optional[bool] = None
    mascotas_permitidas: Optional[bool] = None
    piscina: Optional[bool] = None
    urbanizacion_privada: Optional[bool] = None
    telefono_contacto: Optional[str] = None
    operaciones: Optional[list[OperacionIn]] = None  # si se envía, sustituye la combinación entera
    fotos_nuevas: list[str] = []  # URLs de Cloudinary a añadir a las ya existentes
    orden_fotos: list[str] = []  # IDs de fotos existentes, en el nuevo orden deseado


class UsuarioRegistro(BaseModel):
    nombre: str
    email: EmailStr
    password: str
    tipo_cuenta: str = "particular"  # "particular" o "inmobiliaria"
    nombre_empresa: Optional[str] = None  # requerido si tipo_cuenta == "inmobiliaria"
    cif: Optional[str] = None  # requerido si tipo_cuenta == "inmobiliaria"


class UsuarioLogin(BaseModel):
    email: EmailStr
    password: str


class EmpresaOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    nombre: str
    cif: str
    logo_url: Optional[str] = None


class UsuarioOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    nombre: str
    email: EmailStr
    telefono: Optional[str]
    telefono_verificado: bool
    email_verificado: bool
    metodo_login: str
    fecha_registro: datetime
    tipo_cuenta: str = "particular"
    rol_empresa: Optional[str] = None
    empresa: Optional[EmpresaOut] = None
    stripe_onboarding_completo: bool = False


class InvitarAgenteIn(BaseModel):
    email: EmailStr


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    usuario: UsuarioOut


class SolicitarCodigoIn(BaseModel):
    telefono: str


class VerificarCodigoIn(BaseModel):
    telefono: str
    codigo: str


class VerificarCodigoEmailIn(BaseModel):
    codigo: str


class PushTokenIn(BaseModel):
    token: Optional[str] = None  # null para desactivar las notificaciones de este dispositivo


class GoogleLoginIn(BaseModel):
    id_token: str


class ConversacionCrear(BaseModel):
    inmueble_id: str


class MensajeIn(BaseModel):
    texto: str


class MensajeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    conversacion_id: str
    remitente_id: str
    texto: str
    fecha_envio: datetime
    leido: bool


class ConversacionResumenOut(BaseModel):
    """Para la lista de conversaciones — incluye lo mínimo del inmueble y del
    otro participante para pintar la fila sin tener que hacer otra petición."""
    id: str
    inmueble_id: str
    inmueble_titulo: str
    inmueble_foto: Optional[str] = None
    otro_usuario_nombre: str
    ultimo_mensaje: Optional[str] = None
    ultimo_mensaje_fecha: Optional[datetime] = None
    no_leidos: int


class ConversacionDetalleOut(BaseModel):
    id: str
    inmueble_id: str
    inmueble_titulo: str
    otro_usuario_nombre: str
    mensajes: list[MensajeOut]


class ReservaCrear(BaseModel):
    inmueble_id: str
    fecha_entrada: date
    fecha_salida: date
    plan_pago: models.PlanPago = models.PlanPago.primera_noche
    pasarela_pago: models.PasarelaPago = models.PasarelaPago.stripe


class ReservaOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    inmueble_id: str
    huesped_id: str
    propietario_id: str
    fecha_entrada: date
    fecha_salida: date
    plan_pago: str
    pasarela_pago: str
    precio_noche: float
    precio_total_estancia: float
    monto_cobrado: float
    saldo_pendiente_en_destino: float = 0  # lo que queda por pagar directo al propietario al llegar (0 si el plan es "completo")
    comision_hausbix_usd: float
    estado: str
    fecha_creacion: datetime
    fecha_resolucion: Optional[datetime] = None
    # Datos añadidos para no obligar al cliente a pedir el inmueble aparte
    inmueble_titulo: Optional[str] = None
    inmueble_foto: Optional[str] = None
    otro_nombre: Optional[str] = None  # el huésped ve el nombre del propietario, y viceversa


class ReservaPagoOut(BaseModel):
    reserva: ReservaOut
    client_secret: Optional[str] = None  # Stripe — para completar el pago con su SDK en el cliente
    checkout_url: Optional[str] = None  # Mercado Pago — a esta URL se redirige al huésped para pagar


class StripeOnboardingOut(BaseModel):
    url: str


class SuscripcionWebPushIn(BaseModel):
    endpoint: str
    clave_p256dh: str
    clave_auth: str
