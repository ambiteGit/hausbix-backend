import os
import time
import hashlib
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from .. import models, schemas
from ..database import get_db
from ..auth import hash_password, verificar_password, crear_token, usuario_actual
from ..ratelimit import limitar, ip_cliente

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
def registro(request: Request, payload: schemas.UsuarioRegistro, db: Session = Depends(get_db)):
    limitar("registro", ip_cliente(request), 10, 3600)
    existente = db.query(models.Usuario).filter(models.Usuario.email == payload.email).first()
    if existente:
        raise HTTPException(status_code=409, detail="Ya existe una cuenta con ese email")

    if payload.tipo_cuenta == "inmobiliaria" and not payload.nombre_empresa:
        raise HTTPException(status_code=400, detail="Falta el nombre de la empresa")

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
def login(request: Request, payload: schemas.UsuarioLogin, db: Session = Depends(get_db)):
    limitar("login-ip", ip_cliente(request), 30, 900)
    limitar("login-email", payload.email.lower(), 10, 900)
    usuario = db.query(models.Usuario).filter(models.Usuario.email == payload.email).first()
    if not usuario or not usuario.password_hash or not verificar_password(payload.password, usuario.password_hash):
        raise HTTPException(status_code=401, detail="Email o contraseña incorrectos")
    if usuario.bloqueado:
        raise HTTPException(status_code=403, detail="Tu cuenta ha sido bloqueada. Contacta con soporte si crees que es un error.")

    return schemas.TokenOut(access_token=crear_token(usuario.id), usuario=usuario)


@router.post("/telefono/solicitar-codigo")
def solicitar_codigo(payload: schemas.SolicitarCodigoIn, usuario: models.Usuario = Depends(usuario_actual)):
    limitar("sms-envio", usuario.id, 5, 3600)  # cada SMS cuesta dinero
    if _TWILIO_CONFIGURADO:
        cliente = _twilio_client()
        try:
            cliente.verify.v2.services(TWILIO_VERIFY_SERVICE_SID).verifications.create(
                to=payload.telefono, channel="sms"
            )
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"No se pudo enviar el código: {exc}")
        return {"ok": True, "mensaje": "Código enviado por SMS"}

    # Modo desarrollo sin Twilio configurado — imprime el código en logs.
    import random
    codigo = f"{random.randint(0, 999999):06d}"
    _codigos_dev[payload.telefono] = codigo
    print(f"[DEV — Twilio no configurado] Código para {payload.telefono}: {codigo}")
    return {"ok": True, "mensaje": "Código enviado (modo desarrollo, revisa los logs)"}


@router.post("/telefono/verificar", response_model=schemas.UsuarioOut)
def verificar_codigo(
    payload: schemas.VerificarCodigoIn,
    db: Session = Depends(get_db),
    usuario: models.Usuario = Depends(usuario_actual),
):
    limitar("sms-verif", usuario.id, 10, 900)  # un código de 6 cifras no se puede probar a ciegas
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


@router.post("/email/solicitar-codigo")
def solicitar_codigo_email(usuario: models.Usuario = Depends(usuario_actual)):
    limitar("email-envio", usuario.id, 5, 3600)
    if _TWILIO_CONFIGURADO:
        cliente = _twilio_client()
        try:
            cliente.verify.v2.services(TWILIO_VERIFY_SERVICE_SID).verifications.create(
                to=usuario.email, channel="email"
            )
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"No se pudo enviar el código: {exc}")
        return {"ok": True, "mensaje": "Código enviado por email"}

    # Modo desarrollo sin Twilio configurado — imprime el código en logs.
    import random
    codigo = f"{random.randint(0, 999999):06d}"
    _codigos_dev[usuario.email] = codigo
    print(f"[DEV — Twilio no configurado] Código para {usuario.email}: {codigo}")
    return {"ok": True, "mensaje": "Código enviado (modo desarrollo, revisa los logs)"}


@router.post("/email/verificar", response_model=schemas.UsuarioOut)
def verificar_codigo_email(
    payload: schemas.VerificarCodigoEmailIn,
    db: Session = Depends(get_db),
    usuario: models.Usuario = Depends(usuario_actual),
):
    limitar("email-verif", usuario.id, 10, 900)
    if _TWILIO_CONFIGURADO:
        cliente = _twilio_client()
        try:
            resultado = cliente.verify.v2.services(TWILIO_VERIFY_SERVICE_SID).verification_checks.create(
                to=usuario.email, code=payload.codigo
            )
        except Exception:
            raise HTTPException(status_code=400, detail="Código incorrecto o caducado")
        if resultado.status != "approved":
            raise HTTPException(status_code=400, detail="Código incorrecto o caducado")
    else:
        codigo_esperado = _codigos_dev.get(usuario.email)
        if codigo_esperado is None or codigo_esperado != payload.codigo:
            raise HTTPException(status_code=400, detail="Código incorrecto o caducado")
        _codigos_dev.pop(usuario.email, None)

    usuario.email_verificado = True
    db.commit()
    db.refresh(usuario)
    return usuario


@router.get("/me", response_model=schemas.UsuarioOut)
def mi_perfil(usuario: models.Usuario = Depends(usuario_actual)):
    return usuario


# Fallback solo para desarrollo local sin cuenta de Twilio — igual que
# _codigos_dev, pero en su propio diccionario para no pisarse con los
# códigos de verificación de email si se piden los dos a la vez.
_codigos_reset_dev: dict[str, str] = {}


@router.post("/recuperar-password/solicitar")
def solicitar_recuperar_password(request: Request, payload: schemas.RecuperarPasswordSolicitarIn, db: Session = Depends(get_db)):
    limitar("reset-envio-ip", ip_cliente(request), 10, 3600)
    limitar("reset-envio-email", payload.email.lower(), 3, 3600)
    """
    No requiere sesión — es precisamente para quien no puede entrar. Por
    seguridad, responde siempre el mismo mensaje exista o no esa cuenta,
    para no dejar averiguar desde fuera qué emails están registrados; el
    código solo se envía de verdad si la cuenta existe.
    """
    mensaje = {"ok": True, "mensaje": "Si existe una cuenta con ese email, te hemos enviado un código para recuperar la contraseña."}
    usuario = db.query(models.Usuario).filter(models.Usuario.email == payload.email).first()
    if not usuario:
        return mensaje

    if _TWILIO_CONFIGURADO:
        cliente = _twilio_client()
        try:
            cliente.verify.v2.services(TWILIO_VERIFY_SERVICE_SID).verifications.create(
                to=payload.email, channel="email"
            )
        except Exception:
            # No se filtra el error al cliente — mismo motivo que arriba,
            # no dar pistas de si la cuenta existe o de por qué falló.
            pass
        return mensaje

    import random
    codigo = f"{random.randint(0, 999999):06d}"
    _codigos_reset_dev[payload.email] = codigo
    print(f"[DEV — Twilio no configurado] Código de recuperación para {payload.email}: {codigo}")
    return mensaje


@router.post("/recuperar-password/confirmar", response_model=schemas.TokenOut)
def confirmar_recuperar_password(request: Request, payload: schemas.RecuperarPasswordConfirmarIn, db: Session = Depends(get_db)):
    limitar("reset-conf-ip", ip_cliente(request), 20, 900)
    limitar("reset-conf-email", payload.email.lower(), 8, 900)
    if _TWILIO_CONFIGURADO:
        cliente = _twilio_client()
        try:
            resultado = cliente.verify.v2.services(TWILIO_VERIFY_SERVICE_SID).verification_checks.create(
                to=payload.email, code=payload.codigo
            )
        except Exception:
            raise HTTPException(status_code=400, detail="Código incorrecto o caducado")
        if resultado.status != "approved":
            raise HTTPException(status_code=400, detail="Código incorrecto o caducado")
    else:
        codigo_esperado = _codigos_reset_dev.get(payload.email)
        if codigo_esperado is None or codigo_esperado != payload.codigo:
            raise HTTPException(status_code=400, detail="Código incorrecto o caducado")
        _codigos_reset_dev.pop(payload.email, None)

    usuario = db.query(models.Usuario).filter(models.Usuario.email == payload.email).first()
    if not usuario:
        raise HTTPException(status_code=404, detail="No existe ninguna cuenta con ese email")

    usuario.password_hash = hash_password(payload.password_nueva)
    usuario.password_cambiada_en = datetime.utcnow()  # invalida las sesiones anteriores
    db.commit()
    db.refresh(usuario)

    return schemas.TokenOut(access_token=crear_token(usuario.id), usuario=usuario)


@router.patch("/mi-password", response_model=schemas.UsuarioOut)
def cambiar_mi_password(
    payload: schemas.CambiarPasswordIn,
    db: Session = Depends(get_db),
    usuario: models.Usuario = Depends(usuario_actual),
):
    """Desde el perfil, con sesión iniciada. Si la cuenta todavía no tiene
    ninguna contraseña (solo entró alguna vez con Google), no hace falta
    la actual — en cualquier otro caso, sí, para que nadie con acceso al
    teléfono desbloqueado pueda cambiarla sin saber la de verdad."""
    if usuario.password_hash is not None:
        if not payload.contrasena_actual or not verificar_password(payload.contrasena_actual, usuario.password_hash):
            raise HTTPException(status_code=400, detail="La contraseña actual no es correcta")

    usuario.password_hash = hash_password(payload.password_nueva)
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


@router.patch("/mi-perfil", response_model=schemas.UsuarioOut)
def actualizar_mi_perfil(
    payload: schemas.UsuarioActualizar,
    db: Session = Depends(get_db),
    usuario: models.Usuario = Depends(usuario_actual),
):
    """Foto de perfil y breve descripción — visibles en sus anuncios, para
    dar más confianza a quien los mira. Vale tanto para particulares como
    para agentes de una inmobiliaria (el logo/descripción de la propia
    inmobiliaria es aparte, ver /empresas/mi-empresa)."""
    datos = payload.model_dump(exclude_unset=True)
    for campo, valor in datos.items():
        setattr(usuario, campo, valor)
    db.commit()
    db.refresh(usuario)
    return usuario


GOOGLE_CLIENT_IDS = [c.strip() for c in os.environ.get("GOOGLE_CLIENT_IDS", "").split(",") if c.strip()]


# ── Sign in with Apple ──────────────────────────────────────────────────────
# APPLE_CLIENT_IDS: audiencias aceptadas del identity token (en la app nativa
# es el bundle id). APPLE_TEAM_ID, APPLE_KEY_ID y APPLE_PRIVATE_KEY (el
# contenido del .p8, con los saltos de línea como \n) son OPCIONALES: solo
# hacen falta para poder revocar el acceso de Apple al borrar la cuenta.
APPLE_ISSUER = "https://appleid.apple.com"
APPLE_CLIENT_IDS = [c.strip() for c in os.environ.get("APPLE_CLIENT_IDS", "com.hausbix.app").split(",") if c.strip()]
APPLE_TEAM_ID = os.environ.get("APPLE_TEAM_ID")
APPLE_KEY_ID = os.environ.get("APPLE_KEY_ID")
APPLE_PRIVATE_KEY = (os.environ.get("APPLE_PRIVATE_KEY") or "").replace("\\n", "\n")
_APPLE_REVOCACION_CONFIGURADA = all([APPLE_TEAM_ID, APPLE_KEY_ID, APPLE_PRIVATE_KEY])

_apple_jwks_cache: dict = {"claves": None, "hasta": 0.0}


def _apple_claves_publicas(forzar: bool = False) -> list[dict]:
    import httpx
    if forzar or not _apple_jwks_cache["claves"] or time.time() > _apple_jwks_cache["hasta"]:
        resp = httpx.get(f"{APPLE_ISSUER}/auth/keys", timeout=10)
        resp.raise_for_status()
        _apple_jwks_cache["claves"] = resp.json()["keys"]
        _apple_jwks_cache["hasta"] = time.time() + 3600
    return _apple_jwks_cache["claves"]


def _verificar_identity_token_apple(identity_token: str) -> dict:
    from jose import jwt, JWTError
    try:
        kid = jwt.get_unverified_header(identity_token).get("kid")
        clave = next((k for k in _apple_claves_publicas() if k["kid"] == kid), None)
        if clave is None:  # Apple rota sus claves: se vuelve a pedir la lista una vez
            clave = next((k for k in _apple_claves_publicas(forzar=True) if k["kid"] == kid), None)
        if clave is None:
            raise HTTPException(status_code=401, detail="Token de Apple inválido")
        return jwt.decode(
            identity_token, clave, algorithms=["RS256"], audience=APPLE_CLIENT_IDS,
            issuer=APPLE_ISSUER, options={"verify_at_hash": False},
        )
    except HTTPException:
        raise
    except JWTError:
        raise HTTPException(status_code=401, detail="Token de Apple inválido")
    except Exception:
        raise HTTPException(status_code=503, detail="No se pudo verificar el token con Apple")


def _apple_client_secret() -> str:
    from jose import jwt
    ahora = int(time.time())
    return jwt.encode(
        {"iss": APPLE_TEAM_ID, "iat": ahora, "exp": ahora + 300, "aud": APPLE_ISSUER, "sub": APPLE_CLIENT_IDS[0]},
        APPLE_PRIVATE_KEY, algorithm="ES256", headers={"kid": APPLE_KEY_ID},
    )


def _apple_canjear_codigo(codigo: str) -> str | None:
    """Canjea el authorization code por un refresh token (guardarlo permite
    revocar el acceso al borrar la cuenta). Mejor esfuerzo: si falla, el
    login sigue funcionando."""
    if not _APPLE_REVOCACION_CONFIGURADA:
        return None
    try:
        import httpx
        resp = httpx.post(f"{APPLE_ISSUER}/auth/token", data={
            "client_id": APPLE_CLIENT_IDS[0], "client_secret": _apple_client_secret(),
            "code": codigo, "grant_type": "authorization_code",
        }, timeout=10)
        return resp.json().get("refresh_token") if resp.status_code == 200 else None
    except Exception as error:
        print(f"[apple] no se pudo canjear el authorization code: {error}")
        return None


def _apple_revocar(refresh_token: str) -> None:
    if not _APPLE_REVOCACION_CONFIGURADA:
        return
    try:
        import httpx
        httpx.post(f"{APPLE_ISSUER}/auth/revoke", data={
            "client_id": APPLE_CLIENT_IDS[0], "client_secret": _apple_client_secret(),
            "token": refresh_token, "token_type_hint": "refresh_token",
        }, timeout=10)
    except Exception as error:
        print(f"[apple] no se pudo revocar el token al borrar la cuenta: {error}")


@router.post("/apple", response_model=schemas.TokenOut)
def login_apple(request: Request, payload: schemas.AppleLoginIn, db: Session = Depends(get_db)):
    limitar("login-social", ip_cliente(request), 30, 600)
    """
    Sign in with Apple (guía 4.8 de la App Store). Verifica el identity token
    contra las claves públicas de Apple y crea o recupera la cuenta. Apple
    solo entrega nombre y email la PRIMERA vez, y el email puede ser un alias
    privado (privaterelay.appleid.com), así que la cuenta se reconoce por el
    "sub" estable y no por el email.
    """
    datos = _verificar_identity_token_apple(payload.identity_token)

    if payload.nonce is not None:
        esperado = hashlib.sha256(payload.nonce.encode()).hexdigest()
        if datos.get("nonce") != esperado:
            raise HTTPException(status_code=401, detail="Token de Apple inválido")

    sub = datos["sub"]
    email = (datos.get("email") or payload.email or "").strip().lower() or None
    email_verificado_por_apple = str(datos.get("email_verified", "")).lower() == "true"

    usuario = db.query(models.Usuario).filter(models.Usuario.apple_sub == sub).first()
    if not usuario and email and email_verificado_por_apple:
        # Alguien que ya tenía cuenta con ese mismo email: se vincula.
        usuario = db.query(models.Usuario).filter(models.Usuario.email == email).first()
        if usuario:
            usuario.apple_sub = sub
    if not usuario:
        if not email:
            raise HTTPException(status_code=400, detail="Apple no ha facilitado un email para crear la cuenta")
        if db.query(models.Usuario).filter(models.Usuario.email == email).first():
            raise HTTPException(status_code=409, detail="Ya existe una cuenta con ese email; entra con tu método habitual")
        nombre = (payload.nombre or "").strip() or email.split("@")[0]
        usuario = models.Usuario(nombre=nombre, email=email, apple_sub=sub, metodo_login=models.MetodoLogin.apple)
        db.add(usuario)

    if payload.authorization_code:
        refresh = _apple_canjear_codigo(payload.authorization_code)
        if refresh:
            usuario.apple_refresh_token = refresh

    db.commit()
    db.refresh(usuario)
    return schemas.TokenOut(access_token=crear_token(usuario.id), usuario=usuario)


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
    refresh_apple = usuario.apple_refresh_token
    db.delete(usuario)
    db.commit()
    if refresh_apple:
        _apple_revocar(refresh_apple)


@router.post("/google", response_model=schemas.TokenOut)
def login_google(request: Request, payload: schemas.GoogleLoginIn, db: Session = Depends(get_db)):
    limitar("login-social", ip_cliente(request), 30, 600)
    """
    Recibe el id_token que devuelve Google tras el login en el cliente
    (expo-auth-session) y lo verifica contra los servidores de Google antes
    de crear/recuperar la cuenta. Requiere GOOGLE_CLIENT_IDS configurado —
    una lista separada por comas, porque el token trae una "audiencia"
    distinta según desde dónde se inició sesión (iOS, Android o la web
    usan Client ID distintos) y hay que aceptar cualquiera de los tres,
    no solo uno.
    """
    if not GOOGLE_CLIENT_IDS:
        raise HTTPException(status_code=501, detail="Login con Google no configurado en el servidor")

    from google.oauth2 import id_token as google_id_token
    from google.auth.transport import requests as google_requests

    try:
        datos = google_id_token.verify_oauth2_token(
            payload.id_token, google_requests.Request(), GOOGLE_CLIENT_IDS
        )
    except ValueError:
        raise HTTPException(status_code=401, detail="Token de Google inválido")

    if not datos.get("email_verified"):
        raise HTTPException(status_code=401, detail="Tu email de Google no está verificado")
    email = datos["email"].strip().lower()
    nombre = datos.get("name", email.split("@")[0])

    usuario = db.query(models.Usuario).filter(models.Usuario.email == email).first()
    if not usuario:
        usuario = models.Usuario(nombre=nombre, email=email, metodo_login=models.MetodoLogin.google)
        db.add(usuario)
        db.commit()
        db.refresh(usuario)

    return schemas.TokenOut(access_token=crear_token(usuario.id), usuario=usuario)
