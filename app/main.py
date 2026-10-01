from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from .database import Base, engine
from .routers import inmuebles, auth_router, favoritos, mis_inmuebles, chat, empresas, reservas, admin, notificaciones, mercadopago

# En producción, gestiona las tablas con Alembic en vez de create_all.
Base.metadata.create_all(bind=engine)

# create_all() no añade columnas nuevas a tablas ya existentes — solo crea
# tablas que faltan. Para una columna nueva sobre una tabla que ya tenía
# datos (como email_verificado), se añade así, de forma segura e idempotente
# (no falla si ya existe, no toca los datos que ya había).
try:
    with engine.begin() as conexion:
        conexion.execute(text(
            "ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS email_verificado BOOLEAN NOT NULL DEFAULT false"
        ))
        conexion.execute(text("ALTER TABLE inmuebles ADD COLUMN IF NOT EXISTS m2_terreno DOUBLE PRECISION"))
        conexion.execute(text("ALTER TABLE inmuebles ADD COLUMN IF NOT EXISTS gastos_comunes DOUBLE PRECISION"))
        conexion.execute(text("ALTER TABLE inmuebles ADD COLUMN IF NOT EXISTS contribucion_inmobiliaria DOUBLE PRECISION"))
        conexion.execute(text("ALTER TABLE inmuebles ADD COLUMN IF NOT EXISTS impuesto_primaria DOUBLE PRECISION"))
        conexion.execute(text("ALTER TABLE inmuebles ADD COLUMN IF NOT EXISTS mostrar_ubicacion_exacta BOOLEAN NOT NULL DEFAULT true"))
        conexion.execute(text("ALTER TABLE inmuebles ADD COLUMN IF NOT EXISTS radio_privacidad_metros INTEGER"))
        conexion.execute(text("ALTER TABLE inmuebles ADD COLUMN IF NOT EXISTS lat_aproximada DOUBLE PRECISION"))
        conexion.execute(text("ALTER TABLE inmuebles ADD COLUMN IF NOT EXISTS lng_aproximada DOUBLE PRECISION"))
        conexion.execute(text("ALTER TABLE inmuebles ADD COLUMN IF NOT EXISTS servicio_wifi BOOLEAN NOT NULL DEFAULT false"))
        conexion.execute(text("ALTER TABLE inmuebles ADD COLUMN IF NOT EXISTS servicio_tv BOOLEAN NOT NULL DEFAULT false"))
        conexion.execute(text("ALTER TABLE inmuebles ADD COLUMN IF NOT EXISTS servicio_secador_pelo BOOLEAN NOT NULL DEFAULT false"))
        conexion.execute(text("ALTER TABLE inmuebles ADD COLUMN IF NOT EXISTS servicio_jacuzzi BOOLEAN NOT NULL DEFAULT false"))
        conexion.execute(text("ALTER TABLE inmuebles ADD COLUMN IF NOT EXISTS servicio_lavadora BOOLEAN NOT NULL DEFAULT false"))
        conexion.execute(text("ALTER TABLE inmuebles ADD COLUMN IF NOT EXISTS servicio_cocina_equipada BOOLEAN NOT NULL DEFAULT false"))
        conexion.execute(text("ALTER TABLE inmuebles ADD COLUMN IF NOT EXISTS servicio_calefaccion BOOLEAN NOT NULL DEFAULT false"))
        conexion.execute(text("ALTER TABLE inmuebles ADD COLUMN IF NOT EXISTS servicios_adicionales_texto VARCHAR"))
        conexion.execute(text("ALTER TABLE inmuebles ADD COLUMN IF NOT EXISTS numero_registro_vivienda VARCHAR"))
        conexion.execute(text("ALTER TABLE inmuebles ADD COLUMN IF NOT EXISTS tipo_alojamiento VARCHAR"))
        conexion.execute(text("ALTER TABLE reservas ADD COLUMN IF NOT EXISTS plan_pago VARCHAR NOT NULL DEFAULT 'primera_noche'"))
        conexion.execute(text("ALTER TABLE reservas ADD COLUMN IF NOT EXISTS precio_total_estancia DOUBLE PRECISION"))
        conexion.execute(text("ALTER TABLE reservas ADD COLUMN IF NOT EXISTS monto_cobrado DOUBLE PRECISION"))
        # Para reservas ya existentes (de antes de este cambio), lo más razonable
        # que se puede reconstruir es asumir que se cobró solo una noche.
        conexion.execute(text("UPDATE reservas SET precio_total_estancia = precio_noche WHERE precio_total_estancia IS NULL"))
        conexion.execute(text("UPDATE reservas SET monto_cobrado = precio_noche WHERE monto_cobrado IS NULL"))
        conexion.execute(text("ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS mercadopago_user_id VARCHAR"))
        conexion.execute(text("ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS mercadopago_access_token VARCHAR"))
        conexion.execute(text("ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS mercadopago_refresh_token VARCHAR"))
        conexion.execute(text("ALTER TABLE reservas ADD COLUMN IF NOT EXISTS pasarela_pago VARCHAR NOT NULL DEFAULT 'stripe'"))
        conexion.execute(text("ALTER TABLE reservas ADD COLUMN IF NOT EXISTS mercadopago_payment_id VARCHAR"))
        conexion.execute(text("ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS foto_url VARCHAR"))
        conexion.execute(text("ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS descripcion VARCHAR"))
        conexion.execute(text("ALTER TABLE empresas ADD COLUMN IF NOT EXISTS descripcion VARCHAR"))
        conexion.execute(text("ALTER TABLE inmuebles ADD COLUMN IF NOT EXISTS servicio_piso_radiante BOOLEAN DEFAULT FALSE"))
        conexion.execute(text("ALTER TABLE inmuebles ADD COLUMN IF NOT EXISTS servicio_garaje_estacionamiento BOOLEAN DEFAULT FALSE"))
        conexion.execute(text("ALTER TABLE inmuebles ADD COLUMN IF NOT EXISTS servicio_parrillero BOOLEAN DEFAULT FALSE"))
        conexion.execute(text("ALTER TABLE inmuebles ADD COLUMN IF NOT EXISTS servicio_secadora BOOLEAN DEFAULT FALSE"))
        conexion.execute(text("ALTER TABLE inmuebles ADD COLUMN IF NOT EXISTS servicio_plancha BOOLEAN DEFAULT FALSE"))
        conexion.execute(text("ALTER TABLE inmuebles ADD COLUMN IF NOT EXISTS servicio_articulos_bano BOOLEAN DEFAULT FALSE"))
        conexion.execute(text("ALTER TABLE inmuebles ADD COLUMN IF NOT EXISTS servicio_playero BOOLEAN DEFAULT FALSE"))
        conexion.execute(text("ALTER TABLE inmuebles ADD COLUMN IF NOT EXISTS admin_area_1 VARCHAR"))
        conexion.execute(text("ALTER TABLE inmuebles ADD COLUMN IF NOT EXISTS admin_area_2 VARCHAR"))
        conexion.execute(text("CREATE INDEX IF NOT EXISTS ix_inmuebles_admin_area_1 ON inmuebles (admin_area_1)"))
        conexion.execute(text("CREATE INDEX IF NOT EXISTS ix_inmuebles_admin_area_2 ON inmuebles (admin_area_2)"))
        # Esta columna ya estaba en el modelo desde hace tiempo, pero se coló
        # sin su migración correspondiente — nunca llegó a crearse en la
        # base de datos real, aunque el código ya contaba con que existiera.
        conexion.execute(text("ALTER TABLE inmuebles ADD COLUMN IF NOT EXISTS pais VARCHAR DEFAULT 'ES'"))
        conexion.execute(text("CREATE INDEX IF NOT EXISTS ix_inmuebles_pais ON inmuebles (pais)"))
        conexion.execute(text("ALTER TABLE empresas ALTER COLUMN cif DROP NOT NULL"))
        conexion.execute(text("ALTER TABLE inmueble_operaciones ADD COLUMN IF NOT EXISTS requiere_aprobacion_propietario BOOLEAN DEFAULT false"))
        conexion.execute(text("ALTER TABLE reservas ADD COLUMN IF NOT EXISTS mensaje_huesped TEXT"))
except Exception as error:
    # Igual que en sincronizar_columnas_faltantes() de más abajo: no se
    # relanza a propósito — un fallo aquí no debe impedir que arranque
    # el resto de la aplicación. Queda visible en los logs de Render.
    print(f"[migraciones de arranque] AVISO: {error}")

# El nuevo valor "pendiente_aprobacion_propietario" de EstadoReserva va en
# su propia transacción, separada de las anteriores: si `estado` resultara
# ser un enum nativo de Postgres (se creó como tal en el primer despliegue,
# a diferencia de columnas más nuevas como plan_pago que se añadieron como
# VARCHAR), Postgres no permite sumarle un valor con un ALTER TABLE — hace
# falta este ALTER TYPE aparte. Si `estado` no fuera un enum nativo (o el
# tipo no se llama como se espera), esto simplemente falla y se ignora sin
# afectar al resto de migraciones de arriba, que van en su propia transacción.
try:
    with engine.begin() as conexion:
        conexion.execute(text("ALTER TYPE estadoreserva ADD VALUE IF NOT EXISTS 'pendiente_aprobacion_propietario'"))
except Exception as error:
    print(f"[migración enum EstadoReserva] AVISO (puede ser normal si 'estado' ya es VARCHAR): {error}")

# Detectado por un bug reportado: el admin no podía eliminar NINGÚN usuario
# (ni siquiera cuentas sin ninguna actividad rara, con o sin verificar).
# La causa: estas foreign keys hacia usuarios.id/inmuebles.id ya llevan
# ondelete="CASCADE" en el modelo, pero create_all() nunca actualiza
# constraints de tablas que ya existían en la base de datos real — así que
# si la tabla se creó en algún momento en que esa columna todavía no tenía
# CASCADE (o se creó sin especificarlo), la base de datos se quedó con la
# regla por defecto (RESTRICT), y borrar un usuario con cualquier favorito,
# conversación, mensaje o reserva fallaba con un error de integridad
# referencial — visible para el admin como un fallo genérico al eliminar.
# Esto localiza el nombre real de cada constraint (puede no ser el que
# pondría SQLAlchemy por defecto) y la recrea con ON DELETE CASCADE si
# hiciera falta; cada una en su propia transacción, así que si una tabla no
# existiera todavía (ej. en un despliegue muy nuevo) no bloquea las demás.
_FKS_QUE_DEBEN_SER_CASCADE = [
    ("inmuebles", "usuario_id", "usuarios"),
    ("favoritos", "usuario_id", "usuarios"),
    ("favoritos", "inmueble_id", "inmuebles"),
    ("conversaciones", "comprador_id", "usuarios"),
    ("conversaciones", "vendedor_id", "usuarios"),
    ("conversaciones", "inmueble_id", "inmuebles"),
    ("mensajes", "remitente_id", "usuarios"),
    ("reservas", "huesped_id", "usuarios"),
    ("reservas", "propietario_id", "usuarios"),
    ("reservas", "inmueble_id", "inmuebles"),
    ("suscripciones_web_push", "usuario_id", "usuarios"),
    ("inmueble_operaciones", "inmueble_id", "inmuebles"),
    ("fotos", "inmueble_id", "inmuebles"),
    ("disponibilidad", "inmueble_id", "inmuebles"),
]
for _tabla, _columna, _tabla_referenciada in _FKS_QUE_DEBEN_SER_CASCADE:
    try:
        with engine.begin() as conexion:
            fila = conexion.execute(text(
                """
                SELECT tc.constraint_name, rc.delete_rule
                FROM information_schema.table_constraints tc
                JOIN information_schema.key_column_usage kcu
                  ON tc.constraint_name = kcu.constraint_name AND tc.table_schema = kcu.table_schema
                JOIN information_schema.referential_constraints rc
                  ON tc.constraint_name = rc.constraint_name AND tc.table_schema = rc.constraint_schema
                WHERE tc.constraint_type = 'FOREIGN KEY' AND tc.table_name = :tabla AND kcu.column_name = :columna
                """
            ), {"tabla": _tabla, "columna": _columna}).first()
            if fila is not None and fila[1] != "CASCADE":
                nombre_constraint = fila[0]
                conexion.execute(text(f'ALTER TABLE {_tabla} DROP CONSTRAINT "{nombre_constraint}"'))
                conexion.execute(text(
                    f'ALTER TABLE {_tabla} ADD CONSTRAINT "{nombre_constraint}" '
                    f'FOREIGN KEY ({_columna}) REFERENCES {_tabla_referenciada}(id) ON DELETE CASCADE'
                ))
                print(f"[migración cascade] {_tabla}.{_columna} -> ON DELETE CASCADE (antes: {fila[1]})")
    except Exception as error:
        print(f"[migración cascade] AVISO en {_tabla}.{_columna}: {error}")


def sincronizar_columnas_faltantes():
    """
    Red de seguridad contra el fallo que ya se dio con la columna "pais":
    un campo que se añade al modelo (models.py) pero cuya línea
    "ALTER TABLE ... ADD COLUMN" se olvida más arriba, y que solo se
    descubre cuando algo revienta ya en producción.

    Esto compara, para cada tabla del modelo, sus columnas contra las
    que de verdad existen en la base de datos — y añade sola cualquiera
    que falte, con un tipo de SQL razonable según el tipo de Python de
    esa columna. Ya no hace falta acordarse de escribir una línea nueva
    cada vez que se añade un campo: esto lo cubre automáticamente para
    cualquier columna futura, no solo para las que ya se han previsto
    a mano más arriba.

    Cada columna se intenta por separado, y un fallo en una no impide
    que se añadan las demás ni que arranque el resto de la aplicación
    — antes, un solo fallo en cualquier "ALTER TABLE" de este archivo
    impedía arrancar la aplicación entera, sin ninguna forma de saber
    cuál había sido sin mirar los logs con calma.
    """
    from sqlalchemy import inspect, Boolean, Integer, Float, String, Text as TextType, DateTime, Date

    inspector = inspect(engine)
    for tabla in Base.metadata.sorted_tables:
        if not inspector.has_table(tabla.name):
            continue  # tabla nueva de golpe — create_all() de arriba ya se encarga de esta
        columnas_reales = {c["name"] for c in inspector.get_columns(tabla.name)}
        for columna in tabla.columns:
            if columna.name in columnas_reales:
                continue
            try:
                if isinstance(columna.type, Boolean):
                    tipo_sql = "BOOLEAN"
                elif isinstance(columna.type, Integer):
                    tipo_sql = "INTEGER"
                elif isinstance(columna.type, Float):
                    tipo_sql = "DOUBLE PRECISION"
                elif isinstance(columna.type, (DateTime, Date)):
                    tipo_sql = "TIMESTAMP"
                elif isinstance(columna.type, (String, TextType)):
                    tipo_sql = "VARCHAR"
                else:
                    tipo_sql = "VARCHAR"  # tipo no previsto — VARCHAR es el más flexible como red de seguridad

                default_sql = ""
                if columna.default is not None and getattr(columna.default, "is_scalar", False):
                    valor = columna.default.arg
                    if isinstance(valor, bool):
                        default_sql = f" DEFAULT {str(valor).upper()}"
                    elif isinstance(valor, (int, float)):
                        default_sql = f" DEFAULT {valor}"
                    elif isinstance(valor, str):
                        default_sql = f" DEFAULT '{valor}'"

                with engine.begin() as conexion:
                    conexion.execute(text(
                        f'ALTER TABLE {tabla.name} ADD COLUMN IF NOT EXISTS "{columna.name}" {tipo_sql}{default_sql}'
                    ))
                print(f"[sincronizar_columnas_faltantes] Añadida columna que faltaba: {tabla.name}.{columna.name} ({tipo_sql})")
            except Exception as error:
                # No se relanza el error a propósito — que falte una
                # columna rara no debe impedir que el resto de la
                # aplicación arranque; queda bien visible en los logs
                # de Render para revisarla a mano si hace falta.
                print(f"[sincronizar_columnas_faltantes] AVISO: no se pudo añadir {tabla.name}.{columna.name}: {error}")


sincronizar_columnas_faltantes()

app = FastAPI(title="Hausbix API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://hausbix.com",
        "https://www.hausbix.com",
        "http://localhost:8081",   # Expo web en desarrollo (SDK 52+)
        "http://localhost:19006",  # Expo web en desarrollo (versiones anteriores)
    ],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(inmuebles.router)
app.include_router(auth_router.router)
app.include_router(favoritos.router)
app.include_router(mis_inmuebles.router)
app.include_router(chat.router)
app.include_router(empresas.router)
app.include_router(reservas.router)
app.include_router(admin.router)
app.include_router(notificaciones.router)
app.include_router(mercadopago.router)


@app.get("/")
def estado():
    return {"servicio": "Hausbix API", "estado": "ok"}


@app.get("/tipos-cambio")
def tipos_cambio():
    """
    Cuántas unidades de cada moneda equivalen a 1 USD — para que el
    frontend pueda mostrar el equivalente de un precio en otra moneda
    (p. ej. un alquiler publicado en dólares, mostrado también en pesos
    uruguayos) sin tener que duplicar aquí la lógica de conversión.
    """
    from .tipo_cambio import obtener_tasas_usd
    return obtener_tasas_usd()

