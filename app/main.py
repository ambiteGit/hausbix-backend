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
