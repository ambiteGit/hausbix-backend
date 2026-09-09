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
    chacra = "chacra"  # tipo regional (Uruguay, Argentina) — finca/parcela rural


class Moneda(str, enum.Enum):
    EUR = "EUR"
    USD = "USD"
    GBP = "GBP"
    BRL = "BRL"
    UYU = "UYU"  # peso uruguayo — en Uruguay se puede elegir entre este y USD
    ARS = "ARS"  # peso argentino — en Argentina se puede elegir entre este y USD


class EstadoModeracion(str, enum.Enum):
    pendiente = "pendiente"  # recién publicado, esperando revisión
    aprobado = "aprobado"    # visible públicamente
    rechazado = "rechazado"  # no visible; el propio dueño ve el motivo


class MetodoLogin(str, enum.Enum):
    email = "email"
    google = "google"


class EstadoDisponibilidad(str, enum.Enum):
    libre = "libre"
    ocupado = "ocupado"


class TipoCuenta(str, enum.Enum):
    particular = "particular"
    inmobiliaria = "inmobiliaria"


class RolEmpresa(str, enum.Enum):
    propietario = "propietario"  # creó la cuenta de empresa, puede invitar agentes
    agente = "agente"


class EstadoReserva(str, enum.Enum):
    pendiente_pago = "pendiente_pago"  # PaymentIntent creado, esperando a que se complete el pago
    pendiente_confirmacion = "pendiente_confirmacion"  # pagado, esperando que el propietario confirme
    confirmada = "confirmada"  # propietario confirmó, transferencia hecha
    rechazada = "rechazada"  # propietario rechazó, reembolso completo (salvo los 10$ de gestión)
    cancelada_con_reembolso = "cancelada_con_reembolso"  # huésped canceló con ≥24h, se le devuelve la noche
    cancelada_sin_reembolso = "cancelada_sin_reembolso"  # huésped canceló con <24h o no se presentó


class Empresa(Base):
    """Cuenta de inmobiliaria — varios usuarios (agentes) pueden pertenecer
    a la misma empresa y publicar bajo su nombre y logo."""
    __tablename__ = "empresas"

    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    nombre = Column(String, nullable=False)
    cif = Column(String, nullable=False)
    logo_url = Column(String, nullable=True)
    fecha_creacion = Column(DateTime, default=datetime.utcnow)

    agentes = relationship("Usuario", back_populates="empresa")


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

    # Particular vs. inmobiliaria (varios agentes bajo la misma empresa)
    tipo_cuenta = Column(Enum(TipoCuenta), default=TipoCuenta.particular)
    empresa_id = Column(UUID(as_uuid=False), ForeignKey("empresas.id", ondelete="SET NULL"), nullable=True)
    rol_empresa = Column(Enum(RolEmpresa), nullable=True)

    # Para poder recibir el pago de una reserva (alquiler por fechas) —
    # requiere completar el onboarding de Stripe Connect Express.
    stripe_account_id = Column(String, nullable=True)
    stripe_onboarding_completo = Column(Boolean, default=False)

    inmuebles = relationship(
        "Inmueble", back_populates="usuario", cascade="all, delete-orphan", passive_deletes=True
    )
    empresa = relationship("Empresa", back_populates="agentes")


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

    m2 = Column(Float, nullable=False)  # metros construidos
    m2_utiles = Column(Float, nullable=True)  # metros de superficie útil/total (opcional)
    pais = Column(String, default="ES")  # ISO-2: ES, DE, GB, UY, AR, BR — decide moneda y tipos disponibles
    moneda = Column(Enum(Moneda), default=Moneda.EUR)
    habitaciones = Column(Integer, default=0)
    banos = Column(Integer, default=0)

    # Características — las típicas de cualquier portal inmobiliario. Viven en
    # Inmueble (no en InmuebleOperacion) porque son del inmueble físico, no
    # cambian según si se vende o se alquila.
    ascensor = Column(Boolean, default=False)
    terraza = Column(Boolean, default=False)
    garaje = Column(Boolean, default=False)
    trastero = Column(Boolean, default=False)
    aire_acondicionado = Column(Boolean, default=False)
    exterior = Column(Boolean, default=False)
    amueblado = Column(Boolean, default=False)
    mascotas_permitidas = Column(Boolean, default=False)
    piscina = Column(Boolean, default=False)
    urbanizacion_privada = Column(Boolean, default=False)  # "urbanización privada" (España), "barrio cerrado" (Uruguay/Argentina)

    telefono_contacto = Column(String, nullable=False)
    destacado = Column(Boolean, default=False)
    activo = Column(Boolean, default=True)
    estado_moderacion = Column(Enum(EstadoModeracion), default=EstadoModeracion.pendiente)
    motivo_rechazo = Column(Text, nullable=True)  # solo si estado_moderacion == rechazado
    contactos_recibidos = Column(Integer, default=0)
    fecha_creacion = Column(DateTime, default=datetime.utcnow)

    usuario = relationship("Usuario", back_populates="inmuebles")

    @property
    def anunciante(self):
        """Se calcula al vuelo a partir de self.usuario — no es una columna
        propia. Pydantic (InmuebleOut, from_attributes=True) la lee igual
        que cualquier otro atributo."""
        if not self.usuario:
            return None
        return {
            "nombre": self.usuario.nombre,
            "tipo_cuenta": self.usuario.tipo_cuenta.value if self.usuario.tipo_cuenta else "particular",
            "empresa_nombre": self.usuario.empresa.nombre if self.usuario.empresa else None,
            "empresa_logo_url": self.usuario.empresa.logo_url if self.usuario.empresa else None,
        }
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


class Conversacion(Base):
    """
    Un hilo de chat entre un comprador/inquilino interesado y el propietario
    de un inmueble. Se crea (o se reutiliza si ya existe) la primera vez que
    alguien pulsa "Enviar mensaje" en un anuncio — por eso hay una restricción
    única (inmueble_id, comprador_id): la misma persona no abre dos hilos
    distintos sobre el mismo anuncio.
    """
    __tablename__ = "conversaciones"

    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    inmueble_id = Column(UUID(as_uuid=False), ForeignKey("inmuebles.id", ondelete="CASCADE"), nullable=False)
    comprador_id = Column(UUID(as_uuid=False), ForeignKey("usuarios.id", ondelete="CASCADE"), nullable=False)
    vendedor_id = Column(UUID(as_uuid=False), ForeignKey("usuarios.id", ondelete="CASCADE"), nullable=False)
    fecha_creacion = Column(DateTime, default=datetime.utcnow)

    inmueble = relationship("Inmueble")
    comprador = relationship("Usuario", foreign_keys=[comprador_id])
    vendedor = relationship("Usuario", foreign_keys=[vendedor_id])
    mensajes = relationship(
        "Mensaje", back_populates="conversacion", cascade="all, delete-orphan",
        passive_deletes=True, order_by="Mensaje.fecha_envio",
    )

    __table_args__ = (UniqueConstraint("inmueble_id", "comprador_id", name="uq_inmueble_comprador_conversacion"),)


class Mensaje(Base):
    __tablename__ = "mensajes"

    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    conversacion_id = Column(UUID(as_uuid=False), ForeignKey("conversaciones.id", ondelete="CASCADE"), nullable=False)
    remitente_id = Column(UUID(as_uuid=False), ForeignKey("usuarios.id", ondelete="CASCADE"), nullable=False)
    texto = Column(Text, nullable=False)
    fecha_envio = Column(DateTime, default=datetime.utcnow)
    leido = Column(Boolean, default=False)

    conversacion = relationship("Conversacion", back_populates="mensajes")
    remitente = relationship("Usuario")


class Reserva(Base):
    """
    Reserva de un alquiler por fechas (alquiler_temporal) con pago por
    adelantado retenido hasta que el propietario confirme.

    Modelo económico: se cobra al huésped UN ÚNICO importe (`precio_noche`,
    snapshot del precio de esa noche) — los 10$ de gestión NO son un cargo
    aparte, van incluidos dentro de ese mismo importe.

    - Si el propietario confirma → transferencia de (precio_noche - 10$) al
      propietario. Los 10$ se los queda Hausbix.
    - Si el propietario RECHAZA → reembolso ÍNTEGRO al huésped (el precio
      completo, incluidos los 10$) — no es culpa del huésped que el
      propietario no acepte, así que no se le penaliza en absoluto.
    - Si el huésped cancela con ≥24h de antelación sobre `fecha_entrada` →
      reembolso de (precio_noche - 10$). Aquí sí se queda Hausbix con los
      10$, al ser una cancelación por decisión del propio huésped.
    - Si el huésped cancela con <24h o no se presenta → no hay reembolso de
      nada: el propietario recibe el importe completo (precio_noche, con
      los 10$ ya descontados para Hausbix) como compensación.

    Resumen: los 10$ de gestión solo se pierden cuando la cancelación es
    decisión del huésped (tarde o pronto) — si quien echa para atrás es el
    propietario, el huésped no pierde nada.
    """
    __tablename__ = "reservas"

    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    inmueble_id = Column(UUID(as_uuid=False), ForeignKey("inmuebles.id", ondelete="CASCADE"), nullable=False)
    huesped_id = Column(UUID(as_uuid=False), ForeignKey("usuarios.id", ondelete="CASCADE"), nullable=False)
    propietario_id = Column(UUID(as_uuid=False), ForeignKey("usuarios.id", ondelete="CASCADE"), nullable=False)

    fecha_entrada = Column(Date, nullable=False)
    fecha_salida = Column(Date, nullable=False)
    precio_noche = Column(Float, nullable=False)  # snapshot del precio en el momento de reservar — importe TOTAL cobrado, ya incluye los 10$
    comision_hausbix_usd = Column(Float, default=10.0)

    estado = Column(Enum(EstadoReserva), default=EstadoReserva.pendiente_pago)

    stripe_payment_intent_id = Column(String, nullable=True)
    stripe_transfer_id = Column(String, nullable=True)
    stripe_refund_id = Column(String, nullable=True)

    fecha_creacion = Column(DateTime, default=datetime.utcnow)
    fecha_resolucion = Column(DateTime, nullable=True)  # cuándo se confirmó/rechazó/canceló

    inmueble = relationship("Inmueble")
    huesped = relationship("Usuario", foreign_keys=[huesped_id])
    propietario = relationship("Usuario", foreign_keys=[propietario_id])
