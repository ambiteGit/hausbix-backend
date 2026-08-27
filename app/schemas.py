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
    m2: float
    habitaciones: int = 0
    banos: int = 0
    telefono_contacto: str
    operaciones: list[OperacionIn]  # al menos una
    fotos: list[str] = []  # URLs ya subidas a Cloudinary desde el cliente


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
    habitaciones: int
    banos: int
    telefono_contacto: str
    destacado: bool
    activo: bool
    fecha_creacion: datetime
    fotos: list[FotoOut] = []
    operaciones: list[OperacionOut] = []
    disponibilidad: list[DisponibilidadOut] = []


class InmuebleUpdate(BaseModel):
    """Todos los campos opcionales — PATCH solo actualiza lo que se envía."""
    tipo_inmueble: Optional[TipoInmueble] = None
    titulo: Optional[str] = None
    descripcion: Optional[str] = None
    direccion: Optional[str] = None
    lat: Optional[float] = None
    lng: Optional[float] = None
    m2: Optional[float] = None
    habitaciones: Optional[int] = None
    banos: Optional[int] = None
    telefono_contacto: Optional[str] = None
    operaciones: Optional[list[OperacionIn]] = None  # si se envía, sustituye la combinación entera
    fotos_nuevas: list[str] = []  # URLs de Cloudinary a añadir a las ya existentes
    orden_fotos: list[str] = []  # IDs de fotos existentes, en el nuevo orden deseado


class UsuarioRegistro(BaseModel):
    nombre: str
    email: EmailStr
    password: str


class UsuarioLogin(BaseModel):
    email: EmailStr
    password: str


class UsuarioOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    nombre: str
    email: EmailStr
    telefono: Optional[str]
    telefono_verificado: bool
    metodo_login: str
    fecha_registro: datetime


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    usuario: UsuarioOut


class SolicitarCodigoIn(BaseModel):
    telefono: str


class VerificarCodigoIn(BaseModel):
    telefono: str
    codigo: str


class PushTokenIn(BaseModel):
    token: Optional[str] = None  # null para desactivar las notificaciones de este dispositivo


class GoogleLoginIn(BaseModel):
    id_token: str
