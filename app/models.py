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
    atico = "atico"


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
    apple = "apple"


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
    # Solo cuando el propietario activa `anticipo_obligatorio` y la estancia
    # supera 3 noches: se paga por adelantado el 50% de la estancia, sin
    # descuento y con la comisión habitual (9,99 USD); el otro 50% se paga
    # directamente al propietario al llegar.
    anticipo_50 = "anticipo_50"


class PasarelaPago(str, enum.Enum):
    """Con cuál de las dos se procesa el cobro — el huésped elige entre
    las que el propietario tenga conectadas (puede tener una, la otra, o
    las dos)."""
    stripe = "stripe"
    mercadopago = "mercadopago"
    paypal = "paypal"


class EstadoReserva(str, enum.Enum):
    pendiente_aprobacion_propietario = "pendiente_aprobacion_propietario"  # solo si la operación exige aprobación — el huésped mandó un mensaje, sin pago, esperando a que el propietario apruebe o rechace
    pendiente_pago = "pendiente_pago"  # PaymentIntent creado, esperando a que se complete el pago
    pendiente_confirmacion = "pendiente_confirmacion"  # pagado, esperando que el propietario confirme
    confirmada = "confirmada"  # propietario confirmó, transferencia hecha
    rechazada = "rechazada"
    cancelada_con_reembolso = "cancelada_con_reembolso"
    cancelada_sin_reembolso = "cancelada_sin_reembolso"


class Empresa(Base):
    """Cuenta de inmobiliaria — varios usuarios (agentes) pueden pertenecer
    a la misma empresa y publicar bajo su nombre y logo."""
    __tablename__ = "empresas"

    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    nombre = Column(String, nullable=False)
    cif = Column(String, nullable=True)  # opcional — es CIF/NIF en España, RUT en Uruguay, CUIT en Argentina... y ninguno lo exige por ley solo para publicar
    logo_url = Column(String, nullable=True)
    descripcion = Column(String, nullable=True)  # breve presentación de la inmobiliaria, visible en sus anuncios
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
    # Sign in with Apple: "sub" estable de la cuenta de Apple (el email y el
    # nombre solo llegan la primera vez, y el email puede ser un alias
    # privado) y refresh token, necesario para revocar el acceso al borrar la
    # cuenta (guía 5.1.1(v) de la App Store).
    apple_sub = Column(String, unique=True, index=True, nullable=True)
    apple_refresh_token = Column(String, nullable=True)
    # Tokens de sesión emitidos antes de esta fecha dejan de valer (se rellena
    # al restablecer la contraseña olvidada).
    password_cambiada_en = Column(DateTime, nullable=True)
    push_token = Column(String, nullable=True)
    fecha_registro = Column(DateTime, default=datetime.utcnow)
    foto_url = Column(String, nullable=True)  # foto de perfil — para particulares, sobre todo
    descripcion = Column(String, nullable=True)  # breve texto de presentación, visible en sus anuncios

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
    paypal_merchant_id = Column(String, nullable=True)  # Merchant ID de la cuenta PayPal del propietario, tras el onboarding
    paypal_onboarding_completo = Column(Boolean, default=False)
    mercadopago_refresh_token = Column(String, nullable=True)  # el access_token de MP caduca — este sirve para renovarlo

    # Bloqueo por moderación — lo activa un admin desde el panel. Un
    # usuario bloqueado no puede iniciar sesión, y si ya tenía una sesión
    # abierta (token válido), se le rechaza en cuanto intente usar la API
    # (ver usuario_actual en auth.py) — no hace falta esperar a que caduque el token.
    bloqueado = Column(Boolean, default=False)
    motivo_bloqueo = Column(String, nullable=True)

    @property
    def tiene_password(self) -> bool:
        """Para que el perfil sepa si debe pedir la contraseña actual al
        cambiarla, o dejar ponerla directamente (cuentas que solo entraron
        alguna vez con Google y nunca llegaron a tener una)."""
        return self.password_hash is not None

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

    # División administrativa, extraída automáticamente de los
    # "address_components" que devuelve Google al elegir la dirección
    # (autocompletado o geocodificación) — no se piden a mano. El nombre
    # que se les da en cada país es distinto (Departamento/Barrio en
    # Uruguay, Provincia/Municipio en España...) pero el dato es
    # conceptualmente el mismo en todos: región de primer nivel y
    # localidad/zona de segundo nivel dentro de ella.
    admin_area_1 = Column(String, nullable=True, index=True)  # Departamento (UY), Provincia (ES/AR)...
    admin_area_2 = Column(String, nullable=True, index=True)  # Barrio (UY), Municipio (ES), localidad...

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
    servicio_piso_radiante = Column(Boolean, default=False)
    servicio_garaje_estacionamiento = Column(Boolean, default=False)  # garaje propio
    servicio_lugar_estacionamiento_gratuito = Column(Boolean, default=False)  # buen lugar cercano gratuito para estacionar (no es un garaje)
    servicio_parrillero = Column(Boolean, default=False)  # parrillero/BBQ portátil
    servicio_secadora = Column(Boolean, default=False)  # secadora de ropa — "secarropa" en Uruguay/Argentina
    servicio_plancha = Column(Boolean, default=False)  # plancha y tabla de planchar
    servicio_articulos_bano = Column(Boolean, default=False)  # gel, champú y jabón
    servicio_sauna = Column(Boolean, default=False)
    servicio_gimnasio = Column(Boolean, default=False)
    servicio_playero = Column(Boolean, default=False)  # elementos de playa (reposeras, sombrilla...) — Uruguay/Argentina
    servicio_playero_atencion = Column(Boolean, default=False)  # playero: persona que coloca las hamacas y sombrillas incluidas del edificio — Uruguay/Argentina
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
    destacado_orden = Column(Integer, nullable=True)  # posición (0 = primero) que el admin da a los anuncios destacados
    activo = Column(Boolean, default=True)
    estado_moderacion = Column(Enum(EstadoModeracion), default=EstadoModeracion.pendiente)
    motivo_rechazo = Column(Text, nullable=True)  # solo si estado_moderacion == rechazado
    # True cuando este "pendiente" viene de editar un anuncio que ya había
    # pasado por moderación antes (aprobado o rechazado) — así el panel de
    # admin puede mostrarlo como "Modificado" en vez de como un anuncio
    # nuevo. Se limpia al aprobar o rechazar de nuevo.
    es_edicion_pendiente = Column(Boolean, default=False, nullable=False)
    contactos_recibidos = Column(Integer, default=0)
    fecha_creacion = Column(DateTime, default=datetime.utcnow)

    # Resumen de opiniones de huéspedes (alquiler por fechas) — se
    # recalculan en el momento en que se crea una reseña nueva (ver
    # routers/resenas.py), no en cada lectura del anuncio, para que abrir
    # una ficha no dependa de volver a llamar a la IA cada vez.
    resena_media = Column(Float, nullable=True)
    resena_total = Column(Integer, default=0, nullable=False)
    resena_resumen_ia = Column(Text, nullable=True)

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
    resenas = relationship(
        "Resena", back_populates="inmueble", cascade="all, delete-orphan", passive_deletes=True
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
    capacidad_maxima = Column(Integer, nullable=True)  # nº máximo de huéspedes (adultos + niños) — null = sin límite
    precio_persona_extra = Column(Float, nullable=True)  # recargo por noche por cada persona a partir de la 3ª (adultos + niños cuentan)

    # Solo alquiler_temporal — si está activo, reservar no cobra directamente:
    # el huésped manda una solicitud con un mensaje y el propietario debe
    # aprobarla antes de que se le pueda cobrar nada (ver EstadoReserva.pendiente_aprobacion_propietario).
    requiere_aprobacion_propietario = Column(Boolean, default=False)

    # Solo alquiler_temporal — si está activo, en estancias de MÁS de 3 noches
    # el huésped no puede pagar solo la primera noche: como mínimo debe
    # adelantar el 50% de la estancia (o pagarla entera con el descuento).
    anticipo_obligatorio = Column(Boolean, default=False)

    # Solo alquiler_temporal — check-in/check-out. Las horas se guardan como
    # texto "HH:MM" (hora local del alojamiento, no un instante UTC) y no
    # como Time — es solo una indicación para el huésped, no algo que haya
    # que operar como fecha/hora real. Un mismo par desde/hasta cubre los
    # cuatro casos que puede elegir el propietario:
    #   - hora fija:      desde == hasta
    #   - "desde las X":  desde con valor, hasta en null
    #   - "hasta las X":  hasta con valor, desde en null
    #   - tramo/franja:   desde y hasta con valores distintos
    # Si checkin/checkout_a_acordar está activo, las horas no se usan — se
    # acuerda directamente con el huésped tras la reserva.
    tiene_auto_checkin = Column(Boolean, default=False)
    checkin_a_acordar = Column(Boolean, default=False)
    checkin_hora_desde = Column(String, nullable=True)
    checkin_hora_hasta = Column(String, nullable=True)
    checkout_a_acordar = Column(Boolean, default=False)
    checkout_hora_desde = Column(String, nullable=True)
    checkout_hora_hasta = Column(String, nullable=True)

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
    - Si el propietario RECHAZA la reserva (ya pagada), se reembolsa el
      importe íntegro — no es culpa del huésped.
    - Si el huésped cancela con ≥24h de antelación sobre `fecha_entrada`
      → reembolso de (monto_cobrado - comisión). La comisión no se
      devuelve en cancelaciones por decisión del huésped.
    - Si el huésped cancela con <24h, o no se presenta → no hay
      reembolso; el propietario ya tiene el dinero (pago directo), así
      que no hace falta ninguna acción adicional en Stripe.

    Excepción — alojamientos con `requiere_aprobacion_propietario` activo
    (ver InmuebleOperacion): no se cobra nada al crear la reserva. Queda en
    `pendiente_aprobacion_propietario` con el mensaje del huésped en
    `mensaje_huesped`. Si el propietario aprueba, pasa a `pendiente_pago`
    (sin PaymentIntent/preferencia todavía) y el huésped completa el
    pago con /reservas/{id}/iniciar-pago — desde ahí sigue el mismo
    modelo económico de arriba. Si rechaza, pasa a `rechazada` sin que
    haya habido ningún cobro que reembolsar.
    """
    __tablename__ = "reservas"

    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    inmueble_id = Column(UUID(as_uuid=False), ForeignKey("inmuebles.id", ondelete="CASCADE"), nullable=False)
    huesped_id = Column(UUID(as_uuid=False), ForeignKey("usuarios.id", ondelete="CASCADE"), nullable=False)
    propietario_id = Column(UUID(as_uuid=False), ForeignKey("usuarios.id", ondelete="CASCADE"), nullable=False)

    fecha_entrada = Column(Date, nullable=False)
    fecha_salida = Column(Date, nullable=False)
    plan_pago = Column(Enum(PlanPago), nullable=False, default=PlanPago.primera_noche)
    precio_noche = Column(Float, nullable=False)  # precio base por noche, snapshot al reservar — sin descuento, sin el recargo por huéspedes extra
    recargo_persona_extra_noche = Column(Float, nullable=False, default=0)  # recargo por noche por huéspedes a partir del 3º, snapshot al reservar
    precio_total_estancia = Column(Float, nullable=False)  # (precio_noche + recargo_persona_extra_noche) × nº de noches, sin descuento — para poder mostrar "te queda X por pagar al llegar"
    monto_cobrado = Column(Float, nullable=False)  # lo que realmente se cobra ahora (1 noche, o el total con el 10% si es plan completo)
    comision_hausbix_usd = Column(Float, default=9.99)

    # Huéspedes de la reserva — el propietario puede limitar la capacidad
    # y/o cobrar un recargo por noche a partir de la 3ª persona (ver
    # capacidad_maxima/precio_persona_extra en InmuebleOperacion).
    num_adultos = Column(Integer, nullable=False, default=1)
    num_ninos = Column(Integer, nullable=False, default=0)
    lleva_mascotas = Column(Boolean, nullable=False, default=False)

    # Solo si la operación exige aprobación del propietario — el mensaje de
    # solicitud del huésped, visible para el propietario al decidir.
    mensaje_huesped = Column(Text, nullable=True)

    estado = Column(Enum(EstadoReserva), default=EstadoReserva.pendiente_pago)

    pasarela_pago = Column(Enum(PasarelaPago), nullable=False, default=PasarelaPago.stripe)
    stripe_payment_intent_id = Column(String, nullable=True)
    mercadopago_payment_id = Column(String, nullable=True)
    paypal_order_id = Column(String, nullable=True)
    paypal_capture_id = Column(String, nullable=True)
    stripe_refund_id = Column(String, nullable=True)

    fecha_creacion = Column(DateTime, default=datetime.utcnow)
    fecha_resolucion = Column(DateTime, nullable=True)  # cuándo se confirmó/rechazó/canceló

    inmueble = relationship("Inmueble")
    huesped = relationship("Usuario", foreign_keys=[huesped_id])
    propietario = relationship("Usuario", foreign_keys=[propietario_id])


class Resena(Base):
    """
    Opinión de un huésped sobre una estancia ya realizada (alquiler por
    fechas). Solo puede dejarla quien tenga una Reserva propia en estado
    `confirmada` y con `fecha_salida` ya pasada — así Hausbix tiene
    constancia real de que se alojó ahí, y no cualquiera puede opinar sobre
    cualquier anuncio. Una reseña por reserva (constraint `unique` en
    reserva_id): si la misma persona vuelve a alojarse otra vez, esa
    segunda estancia es otra reserva y puede dejar otra reseña aparte.
    """
    __tablename__ = "resenas"

    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    inmueble_id = Column(UUID(as_uuid=False), ForeignKey("inmuebles.id", ondelete="CASCADE"), nullable=False, index=True)
    reserva_id = Column(UUID(as_uuid=False), ForeignKey("reservas.id", ondelete="CASCADE"), nullable=False, unique=True)
    huesped_id = Column(UUID(as_uuid=False), ForeignKey("usuarios.id", ondelete="CASCADE"), nullable=False)

    puntuacion = Column(Integer, nullable=False)  # 1 a 5
    comentario = Column(Text, nullable=True)
    fecha_creacion = Column(DateTime, default=datetime.utcnow)

    inmueble = relationship("Inmueble", back_populates="resenas")
    reserva = relationship("Reserva")
    huesped = relationship("Usuario")


# ---------------------------------------------------------------------------
# Moderación de contenido: reportes y bloqueos entre usuarios.
# Exigido por la guía 1.2 de la App Store (contenido generado por usuarios):
# un mecanismo para reportar contenido ofensivo, para bloquear a usuarios
# abusivos, y que los reportes se atiendan en un plazo razonable (24 h).
# ---------------------------------------------------------------------------

class MotivoReporte(str, enum.Enum):
    spam = "spam"                          # publicidad, duplicados, contenido irrelevante
    contenido_ofensivo = "contenido_ofensivo"  # ofensivo, inapropiado o ilegal
    estafa = "estafa"                      # sospecha de fraude
    informacion_falsa = "informacion_falsa"    # anuncio engañoso / datos falsos
    acoso = "acoso"                        # acoso o comportamiento abusivo
    otro = "otro"


class TipoObjetivoReporte(str, enum.Enum):
    inmueble = "inmueble"
    mensaje = "mensaje"
    resena = "resena"
    usuario = "usuario"


class EstadoReporte(str, enum.Enum):
    pendiente = "pendiente"
    resuelto = "resuelto"      # el admin actuó (borró contenido y/o bloqueó al autor)
    descartado = "descartado"  # el admin lo revisó y no encontró infracción


class Reporte(Base):
    """
    Denuncia de un usuario sobre un anuncio, un mensaje de chat, una reseña
    u otro usuario. `objetivo_id` es polimórfico (apunta a una tabla u otra
    según `tipo_objetivo`), así que no lleva foreign key; para que el admin
    pueda revisar el reporte aunque el contenido se borre después, se
    guarda además un `extracto` (copia del texto/título en el momento de
    denunciar) y el autor denunciado (`usuario_reportado_id`).
    """
    __tablename__ = "reportes"

    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    reportante_id = Column(UUID(as_uuid=False), ForeignKey("usuarios.id", ondelete="CASCADE"), nullable=False)
    tipo_objetivo = Column(Enum(TipoObjetivoReporte), nullable=False)
    objetivo_id = Column(String, nullable=False)
    usuario_reportado_id = Column(UUID(as_uuid=False), ForeignKey("usuarios.id", ondelete="SET NULL"), nullable=True)
    motivo = Column(Enum(MotivoReporte), nullable=False)
    comentario = Column(Text, nullable=True)
    extracto = Column(Text, nullable=True)
    estado = Column(Enum(EstadoReporte), default=EstadoReporte.pendiente, nullable=False, index=True)
    nota_resolucion = Column(Text, nullable=True)
    fecha_creacion = Column(DateTime, default=datetime.utcnow)
    fecha_resolucion = Column(DateTime, nullable=True)

    reportante = relationship("Usuario", foreign_keys=[reportante_id])
    usuario_reportado = relationship("Usuario", foreign_keys=[usuario_reportado_id])

    __table_args__ = (
        UniqueConstraint("reportante_id", "tipo_objetivo", "objetivo_id", name="uq_reporte_unico_por_usuario_y_objetivo"),
    )


class BloqueoUsuario(Base):
    """El usuario `bloqueador` no quiere ver nada del usuario `bloqueado`
    (ni sus anuncios en los resultados, ni sus reseñas, ni conversar con él)."""
    __tablename__ = "bloqueos_usuario"

    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    bloqueador_id = Column(UUID(as_uuid=False), ForeignKey("usuarios.id", ondelete="CASCADE"), nullable=False, index=True)
    bloqueado_id = Column(UUID(as_uuid=False), ForeignKey("usuarios.id", ondelete="CASCADE"), nullable=False, index=True)
    fecha_creacion = Column(DateTime, default=datetime.utcnow)

    bloqueado = relationship("Usuario", foreign_keys=[bloqueado_id])

    __table_args__ = (UniqueConstraint("bloqueador_id", "bloqueado_id", name="uq_bloqueo_usuario_par"),)
