import os
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import models, schemas
from ..database import get_db
from ..auth import hash_password, verificar_password, crear_token, usuario_actual

router = APIRouter(prefix="/auth", tags=["auth"])

TWILIO_ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN")
TWILIO_VERIFY_SERVICE_SID = os.environ.get("TWILIO_VERIFY_SERVICE_SID")

_TWILIO_CONFIGURADO = all([TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_VERIFY_SERVICE_SID])

# Fallback solo para desarrollo local sin cuenta de Twilio — nunca se usa si
# las variables de entorno de Twilio están configuradas.
_codigos_dev: dict[str, str] = {}


def _twilio_client():
    from twilio.rest import Client
    return Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)


@router.post("/registro", response_model=schemas.TokenOut, status_code=201)
def registro(payload: schemas.UsuarioRegistro, db: Session = Depends(get_db)):
    existente = db.query(models.Usuario).filter(models.Usuario.email == payload.email).first()
    if existente:
        raise HTTPException(status_code=409, detail="Ya existe una cuenta con ese email")

    if payload.tipo_cuenta == "inmobiliaria" and not (payload.nombre_empresa and payload.cif):
        raise HTTPException(status_code=400, detail="Falta el nombre de la empresa o el CIF")

    usuario = models.Usuario(
        nombre=payload.nombre,
        email=payload.email,
        password_hash=hash_password(payload.password),
        metodo_login=models.MetodoLogin.email,
        tipo_cuenta=payload.tipo_cuenta,
    )

    if payload.tipo_cuenta == "inmobiliaria":
        empresa = models.Empresa(nombre=payload.nombre_empresa, cif=payload.cif)
        db.add(empresa)
        db.flush()  # para tener empresa.id antes de asignarlo al usuario
        usuario.empresa_id = empresa.id
        usuario.rol_empresa = models.RolEmpresa.propietario

    db.add(usuario)
    db.commit()
    db.refresh(usuario)

    return schemas.TokenOut(access_token=crear_token(usuario.id), usuario=usuario)


@router.post("/login", response_model=schemas.TokenOut)
def login(payload: schemas.UsuarioLogin, db: Session = Depends(get_db)):
    usuario = db.query(models.Usuario).filter(models.Usuario.email == payload.email).first()
    if not usuario or not usuario.password_hash or not verificar_password(payload.password, usuario.password_hash):
        raise HTTPException(status_code=401, detail="Email o contraseña incorrectos")

    return schemas.TokenOut(access_token=crear_token(usuario.id), usuario=usuario)


@router.post("/telefono/solicitar-codigo")
def solicitar_codigo(payload: schemas.SolicitarCodigoIn, usuario: models.Usuario = Depends(usuario_actual)):
    if _TWILIO_CONFIGURADO:
        cliente = _twilio_client()
        try:
            cliente.verify.v2.services(TWILIO_VERIFY_SERVICE_SID).verifications.create(
                to=payload.telefono, channel="whatsapp"
            )
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"No se pudo enviar el código: {exc}")
        return {"ok": True, "mensaje": "Código enviado por WhatsApp"}

    # Modo desarrollo sin Twilio configurado — imprime el código en logs.
    import random
    codigo = f"{random.randint(0, 9999):04d}"
    _codigos_dev[payload.telefono] = codigo
    print(f"[DEV — Twilio no configurado] Código para {payload.telefono}: {codigo}")
    return {"ok": True, "mensaje": "Código enviado (modo desarrollo, revisa los logs)"}


@router.post("/telefono/verificar", response_model=schemas.UsuarioOut)
def verificar_codigo(
    payload: schemas.VerificarCodigoIn,
    db: Session = Depends(get_db),
    usuario: models.Usuario = Depends(usuario_actual),
):
    if _TWILIO_CONFIGURADO:
        cliente = _twilio_client()
        try:
            resultado = cliente.verify.v2.services(TWILIO_VERIFY_SERVICE_SID).verification_checks.create(
                to=payload.telefono, code=payload.codigo
            )
        except Exception:
            raise HTTPException(status_code=400, detail="Código incorrecto o caducado")
        if resultado.status != "approved":
            raise HTTPException(status_code=400, detail="Código incorrecto o caducado")
    else:
        codigo_esperado = _codigos_dev.get(payload.telefono)
        if codigo_esperado is None or codigo_esperado != payload.codigo:
            raise HTTPException(status_code=400, detail="Código incorrecto o caducado")
        _codigos_dev.pop(payload.telefono, None)

    usuario.telefono = payload.telefono
    usuario.telefono_verificado = True
    db.commit()
    db.refresh(usuario)
    return usuario


@router.post("/push-token")
def guardar_push_token(
    payload: schemas.PushTokenIn,
    db: Session = Depends(get_db),
    usuario: models.Usuario = Depends(usuario_actual),
):
    usuario.push_token = payload.token
    db.commit()
    return {"ok": True}


GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID")


@router.delete("/mi-cuenta", status_code=204)
def borrar_mi_cuenta(db: Session = Depends(get_db), usuario: models.Usuario = Depends(usuario_actual)):
    """
    Borra la cuenta y todo lo asociado: anuncios (con sus fotos, operaciones
    y disponibilidad), y los favoritos que ha dado o que otros le hayan dado
    a sus anuncios. Todo se resuelve mediante `db.delete(usuario)` + el cascade
    a nivel de base de datos (ON DELETE CASCADE) definido en los modelos —
    importante usar `db.delete()` y no un `.delete()` masivo por consulta,
    porque ese último salta el cascade de SQLAlchemy y rompería con un error
    de clave foránea en cuanto el usuario tuviera algún anuncio publicado.
    """
    db.delete(usuario)
    db.commit()


@router.post("/google", response_model=schemas.TokenOut)
def login_google(payload: schemas.GoogleLoginIn, db: Session = Depends(get_db)):
    """
    Recibe el id_token que devuelve Google tras el login en el cliente
    (expo-auth-session) y lo verifica contra los servidores de Google antes
    de crear/recuperar la cuenta. Requiere GOOGLE_CLIENT_ID configurado.
    """
    if not GOOGLE_CLIENT_ID:
        raise HTTPException(status_code=501, detail="Login con Google no configurado en el servidor")

    from google.oauth2 import id_token as google_id_token
    from google.auth.transport import requests as google_requests

    try:
        datos = google_id_token.verify_oauth2_token(
            payload.id_token, google_requests.Request(), GOOGLE_CLIENT_ID
        )
    except ValueError:
        raise HTTPException(status_code=401, detail="Token de Google inválido")

    email = datos["email"]
    nombre = datos.get("name", email.split("@")[0])

    usuario = db.query(models.Usuario).filter(models.Usuario.email == email).first()
    if not usuario:
        usuario = models.Usuario(nombre=nombre, email=email, metodo_login=models.MetodoLogin.google)
        db.add(usuario)
        db.commit()
        db.refresh(usuario)

    return schemas.TokenOut(access_token=crear_token(usuario.id), usuario=usuario)
