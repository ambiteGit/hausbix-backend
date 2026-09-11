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


class TipoAlojamiento(str, enum.Enum):
    """Solo aplica a alquiler_temporal — igual que Airbnb distingue entre
    alquilar una habitación dentro de una casa habitada o el alojamiento
    completo para el huésped solo."""
    habitacion = "habitacion"
    alojamiento_entero = "alojamiento_entero"


class TipoInmueble(str, enum.Enum):
    piso = "piso"
    casa = "casa"
    local = "local"
    terreno = "terreno"
    garaje = "garaje"
    oficina = "oficina"
    chacra = "chacra"  # tipo regional (Uruguay, Argentina) — finca/parcela rural
    monoambiente = "monoambiente"  # tipo regional (Uruguay, Argentina) — estudio/una sola pieza


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


class PlanPago(str, enum.Enum):
    """El huésped elige cómo paga una reserva de alquiler por fechas:
    solo la primera noche ahora (el resto se paga directamente al
    propietario al llegar, fuera de Hausbix) o el importe completo de la
    estancia ahora, con un 10% de descuento por adelantarlo todo."""
    primera_noche = "primera_noche"
    completo = "completo"


class PasarelaPago(str, enum.Enum):
    """Con cuál de las dos se procesa el cobro — el huésped elige entre
    las que el propietario tenga conectadas (puede tener una, la otra, o
    las dos)."""
    stripe = "stripe"
    mercadopago = "mercadopago"


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
    email_verificado = Column(Boolean, default=False)
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
    mercadopago_user_id = Column(String, nullable=True)  # id de la cuenta de Mercado Pago del propietario, tras el OAuth
    mercadopago_access_token = Column(String, nullable=True)  # token de esa cuenta — necesario para cobrar a su nombre
    mercadopago_refresh_token = Column(String, nullable=True)  # el access_token de MP caduca — este sirve para renovarlo

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

    lat = Column(Float, nullable=False, index=True)  # ubicación real — nunca se expone directamente si mostrar_ubicacion_exacta es False
    lng = Column(Float, nullable=False, index=True)

    # Privacidad de ubicación: el propietario puede optar por no mostrar
    # el punto exacto. Dos formas — un radio (círculo difuso, con centro
    # ligeramente aleatorizado para que ni el centro delate la dirección
    # real) o un pin aproximado puesto a mano en otro punto del mapa.
    mostrar_ubicacion_exacta = Column(Boolean, default=True, nullable=False)
    radio_privacidad_metros = Column(Integer, nullable=True)  # solo si se eligió el método de radio
    lat_aproximada = Column(Float, nullable=True)  # coordenada a mostrar cuando la exacta está oculta
    lng_aproximada = Column(Float, nullable=True)
    direccion = Column(String, nullable=False)

    m2 = Column(Float, nullable=False)  # metros construidos
    m2_utiles = Column(Float, nullable=True)  # metros de superficie útil/total (opcional)
    m2_terreno = Column(Float, nullable=True)  # superficie del terreno/parcela (opcional, casas/chacras)
    pais = Column(String, default="ES")  # ISO-2: ES, DE, GB, UY, AR, BR — decide moneda y tipos disponibles
    moneda = Column(Enum(Moneda), default=Moneda.EUR)
    habitaciones = Column(Integer, default=0)
    banos = Column(Integer, default=0)

    # Gastos e impuestos habituales al vender en algunos mercados (p. ej.
    # Uruguay: gastos comunes, contribución inmobiliaria, Primaria) — opcionales,
    # no todos los mercados los usan.
    gastos_comunes = Column(Float, nullable=True)
    contribucion_inmobiliaria = Column(Float, nullable=True)
    impuesto_primaria = Column(Float, nullable=True)

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

    # Servicios — categoría propia para alquiler por fechas (distinta de
    # las "características" de arriba, que aplican a cualquier operación).
    # El propietario está obligado a indicarlos si publica alquiler por
    # fechas (se valida en el endpoint de creación).
    servicio_wifi = Column(Boolean, default=False)
    servicio_tv = Column(Boolean, default=False)
    servicio_secador_pelo = Column(Boolean, default=False)
    servicio_jacuzzi = Column(Boolean, default=False)
    servicio_lavadora = Column(Boolean, default=False)
    servicio_cocina_equipada = Column(Boolean, default=False)
    servicio_calefaccion = Column(Boolean, default=False)
    # Para lo que no tiene sentido como filtro (toallas, sábanas, productos
    # de baño...) — texto libre, informativo, no se puede buscar por él.
    servicios_adicionales_texto = Column(String, nullable=True)

    # Registro de vivienda para alquiler de corta duración — exigido por
    # el Reglamento (UE) 2024/1028 en municipios con ordenanza propia
    # (en Alemania: Aachen, Colonia y otras ciudades de NRW, entre otras).
    # Se llama "Wohnraum-ID" en Alemania; el nombre del campo se deja
    # genérico por si en el futuro hace falta para otro país.
    numero_registro_vivienda = Column(String, nullable=True)
    tipo_alojamiento = Column(Enum(TipoAlojamiento), nullable=True)  # solo para alquiler_temporal

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


class SuscripcionWebPush(Base):
    """
    Un navegador suscrito a notificaciones push — un mismo usuario puede
    tener varias (distintos navegadores/dispositivos), a diferencia del
    push_token de la app (uno solo, para Expo). endpoint/p256dh/auth son
    los tres datos que da el navegador al suscribirse (pushManager.subscribe),
    necesarios para poder enviarle una notificación después.
    """
    __tablename__ = "suscripciones_web_push"

    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    usuario_id = Column(UUID(as_uuid=False), ForeignKey("usuarios.id", ondelete="CASCADE"), nullable=False)
    endpoint = Column(String, nullable=False, unique=True)
    clave_p256dh = Column(String, nullable=False)
    clave_auth = Column(String, nullable=False)
    fecha_creacion = Column(DateTime, default=datetime.utcnow)


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
    Reserva de un alquiler por fechas (alquiler_temporal).

    Modelo económico (pago directo, sin retención de Hausbix):
    - El huésped elige un `plan_pago`:
        · primera_noche: paga solo el precio de la primera noche ahora;
          el resto de la estancia se paga directamente al propietario al
          llegar (en efectivo o como acuerden entre ellos — Hausbix no
          interviene en ese resto).
        · completo: paga el importe íntegro de la estancia ahora, con un
          10% de descuento por adelantarlo todo.
    - El dinero va DIRECTO a la cuenta de Stripe del propietario en el
      momento del cobro (cargo con destino, no hay un paso posterior de
      "transferir" — Stripe lo hace todo en el mismo cargo).
    - Hausbix se queda con `comision_hausbix_usd` de cada reserva, como
      comisión de gestión — el resto es siempre para el propietario. El
      importe de la comisión depende del plan de pago elegido (9,99 si
      es solo la primera noche, 11,99 si se paga la estancia completa por
      adelantado) — no depende del número de noches en ningún caso.
    - Si el propietario RECHAZA la reserva → reembolso ÍNTEGRO al huésped
      (recupera también la comisión) — no es culpa suya que el propietario
      no acepte.
    - Si el huésped cancela con ≥24h de antelación sobre `fecha_entrada` →
      reembolso de (monto_cobrado - comisión). La comisión no se devuelve
      en cancelaciones por decisión del huésped.
    - Si el huésped cancela con <24h, o no se presenta → no hay reembolso;
      el propietario ya tiene el dinero (pago directo), así que no hace
      falta ninguna acción adicional en Stripe.
    """
    __tablename__ = "reservas"

    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    inmueble_id = Column(UUID(as_uuid=False), ForeignKey("inmuebles.id", ondelete="CASCADE"), nullable=False)
    huesped_id = Column(UUID(as_uuid=False), ForeignKey("usuarios.id", ondelete="CASCADE"), nullable=False)
    propietario_id = Column(UUID(as_uuid=False), ForeignKey("usuarios.id", ondelete="CASCADE"), nullable=False)

    fecha_entrada = Column(Date, nullable=False)
    fecha_salida = Column(Date, nullable=False)
    plan_pago = Column(Enum(PlanPago), nullable=False, default=PlanPago.primera_noche)
    precio_noche = Column(Float, nullable=False)  # precio por noche, snapshot al reservar — sin descuento
    precio_total_estancia = Column(Float, nullable=False)  # precio_noche × nº de noches, sin descuento — para poder mostrar "te queda X por pagar al llegar"
    monto_cobrado = Column(Float, nullable=False)  # lo que realmente se cobra ahora (1 noche, o el total con el 10% si es plan completo)
    comision_hausbix_usd = Column(Float, default=9.99)

    estado = Column(Enum(EstadoReserva), default=EstadoReserva.pendiente_pago)

    pasarela_pago = Column(Enum(PasarelaPago), nullable=False, default=PasarelaPago.stripe)
    stripe_payment_intent_id = Column(String, nullable=True)
    mercadopago_payment_id = Column(String, nullable=True)
    stripe_refund_id = Column(String, nullable=True)

    fecha_creacion = Column(DateTime, default=datetime.utcnow)
    fecha_resolucion = Column(DateTime, nullable=True)  # cuándo se confirmó/rechazó/canceló

    inmueble = relationship("Inmueble")
    huesped = relationship("Usuario", foreign_keys=[huesped_id])
    propietario = relationship("Usuario", foreign_keys=[propietario_id])
