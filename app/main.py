from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .database import Base, engine
from .routers import inmuebles, auth_router, favoritos, mis_inmuebles

# En producción, gestiona las tablas con Alembic en vez de create_all.
Base.metadata.create_all(bind=engine)

app = FastAPI(title="Hausbix API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # restringir al dominio real cuando publiquemos
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(inmuebles.router)
app.include_router(auth_router.router)
app.include_router(favoritos.router)
app.include_router(mis_inmuebles.router)


@app.get("/")
def estado():
    return {"servicio": "Hausbix API", "estado": "ok"}
