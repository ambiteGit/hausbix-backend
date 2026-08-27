import uuid
import enum
from datetime import datetime

from sqlalchemy import (
    Column, String, Float, Integer, Boolean, DateTime, ForeignKey, Enum, Date, Text, UniqueConstraint,
)
from sqlalchemy.orm import relationship
from sqlalchemy.dialects.postgresql import UUID

from .database import Base


def gen_uuid():
    return str(uuid.uuid4())


class TipoOperacion(str, enum.Enum):
    venta = "venta"
    alquiler_anual = "alquiler_anual"
    alquiler_invernal = "alquiler_invernal"  # temporada larga con fechas fijas (ej. octubre-mayo)
    alquiler_temporal = "alquiler_temporal"  # por noches, precio libre día a día


class TipoInmueble(str, enum.Enum):
    piso = "piso"
    casa = "casa"
    local = "local"
    terreno = "terreno"
    garaje = "garaje"
    oficina = "oficina"


class MetodoLogin(str, enum.Enum):
    email = "email"
    google = "google"


class EstadoDisponibilidad(str, enum.Enum):
    libre = "libre"
    ocupado = "ocupado"


class Usuario(Base):
    __tablename__ = "usuarios"

    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    nombre = Column(String, nullable=False)
    email = Column(String, unique=True, nullable=False, index=True)
    password_hash = Column(String, nullable=True)  # null si login solo con Google
    telefono = Column(String, nullable=True)
    telefono_verificado = Column(Boolean, default=False)
    metodo_login = Column(Enum(MetodoLogin), default=MetodoLogin.email)
    push_token = Column(String, nullable=True)
    fecha_registro = Column(DateTime, default=datetime.utcnow)

    inmuebles = relationship(
        "Inmueble", back_populates="usuario", cascade="all, delete-orphan", passive_deletes=True
    )


class Inmueble(Base):
    __tablename__ = "inmuebles"

    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    usuario_id = Column(UUID(as_uuid=False), ForeignKey("usuarios.id", ondelete="CASCADE"), nullable=False)

    tipo_inmueble = Column(Enum(TipoInmueble), nullable=False)

    titulo = Column(String, nullable=False)
    descripcion = Column(Text, nullable=False)

    lat = Column(Float, nullable=False, index=True)
    lng = Column(Float, nullable=False, index=True)
    direccion = Column(String, nullable=False)

    m2 = Column(Float, nullable=False)
    habitaciones = Column(Integer, default=0)
    banos = Column(Integer, default=0)

    telefono_contacto = Column(String, nullable=False)
    destacado = Column(Boolean, default=False)
    activo = Column(Boolean, default=True)
    contactos_recibidos = Column(Integer, default=0)
    fecha_creacion = Column(DateTime, default=datetime.utcnow)

    usuario = relationship("Usuario", back_populates="inmuebles")
    fotos = relationship(
        "Foto", back_populates="inmueble", cascade="all, delete-orphan",
        passive_deletes=True, order_by="Foto.orden",
    )
    disponibilidad = relationship(
        "Disponibilidad", back_populates="inmueble", cascade="all, delete-orphan", passive_deletes=True
    )
    operaciones = relationship(
        "InmuebleOperacion", back_populates="inmueble", cascade="all, delete-orphan", passive_deletes=True
    )


class InmuebleOperacion(Base):
    """
    Cada fila representa UNA modalidad activa de un inmueble (venta, alquiler
    anual, invernal o temporal). Un inmueble puede tener varias filas a la vez
    — por eso el precio y las fechas viven aquí y no en Inmueble.
    """
    __tablename__ = "inmueble_operaciones"

    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    inmueble_id = Column(UUID(as_uuid=False), ForeignKey("inmuebles.id", ondelete="CASCADE"), nullable=False)
    tipo_operacion = Column(Enum(TipoOperacion), nullable=False)

    # venta: precio total · alquiler_anual: €/mes · alquiler_invernal: precio
    # de toda la temporada · alquiler_temporal: precio base por noche (se usa
    # como valor por defecto en los días del calendario que no tengan uno propio)
    precio = Column(Float, nullable=True)

    # Solo alquiler_invernal: rango fijo de fechas de la temporada
    fecha_inicio = Column(Date, nullable=True)
    fecha_fin = Column(Date, nullable=True)

    # Solo alquiler_temporal
    noches_minimas = Column(Integer, nullable=True)

    inmueble = relationship("Inmueble", back_populates="operaciones")

    __table_args__ = (UniqueConstraint("inmueble_id", "tipo_operacion", name="uq_inmueble_tipo_operacion"),)


class Foto(Base):
    __tablename__ = "fotos"

    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    inmueble_id = Column(UUID(as_uuid=False), ForeignKey("inmuebles.id", ondelete="CASCADE"), nullable=False)
    url = Column(String, nullable=False)
    orden = Column(Integer, default=0)

    inmueble = relationship("Inmueble", back_populates="fotos")


class Disponibilidad(Base):
    """
    Solo aplica a inmuebles con una operación de tipo alquiler_temporal.
    Cada día puede tener su propio precio (para fines de semana, temporada
    alta, etc.) — si `precio` es null, la app usa el precio base de
    InmuebleOperacion.precio para ese día.
    """
    __tablename__ = "disponibilidad"

    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    inmueble_id = Column(UUID(as_uuid=False), ForeignKey("inmuebles.id", ondelete="CASCADE"), nullable=False)
    fecha = Column(Date, nullable=False)
    estado = Column(Enum(EstadoDisponibilidad), default=EstadoDisponibilidad.libre)
    precio = Column(Float, nullable=True)

    inmueble = relationship("Inmueble", back_populates="disponibilidad")

    __table_args__ = (UniqueConstraint("inmueble_id", "fecha", name="uq_inmueble_fecha"),)


class Favorito(Base):
    __tablename__ = "favoritos"

    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    usuario_id = Column(UUID(as_uuid=False), ForeignKey("usuarios.id", ondelete="CASCADE"), nullable=False)
    inmueble_id = Column(UUID(as_uuid=False), ForeignKey("inmuebles.id", ondelete="CASCADE"), nullable=False)
    fecha_guardado = Column(DateTime, default=datetime.utcnow)

    inmueble = relationship("Inmueble")

    __table_args__ = (UniqueConstraint("usuario_id", "inmueble_id", name="uq_usuario_inmueble_favorito"),)
