import os
from datetime import datetime, timedelta
from typing import Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import jwt, JWTError
from passlib.context import CryptContext
from sqlalchemy.orm import Session

from .database import get_db
from . import models

_SECRET_POR_DEFECTO = "cambia-esto-en-produccion"
SECRET_KEY = os.environ.get("JWT_SECRET", _SECRET_POR_DEFECTO)
# Con el secreto por defecto (o uno corto), cualquiera podría fabricar un token
# válido —incluido el de administrador— sin conocer ninguna contraseña. En
# Render (variable RENDER presente) el servidor se niega a arrancar así.
if os.environ.get("RENDER") and (SECRET_KEY == _SECRET_POR_DEFECTO or len(SECRET_KEY) < 32):
    raise RuntimeError("JWT_SECRET no está configurado o es demasiado corto (mínimo 32 caracteres). Genera uno con: python -c \"import secrets; print(secrets.token_urlsafe(64))\"")
ALGORITHM = "HS256"
EXPIRE_MINUTES = 60 * 24 * 30  # 30 días

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
bearer_scheme = HTTPBearer()


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verificar_password(password: str, password_hash: str) -> bool:
    return pwd_context.verify(password, password_hash)


def crear_token(usuario_id: str) -> str:
    ahora = datetime.utcnow()
    expira = ahora + timedelta(minutes=EXPIRE_MINUTES)
    # iat permite invalidar los tokens anteriores a un cambio de contraseña.
    return jwt.encode({"sub": usuario_id, "exp": expira, "iat": int(ahora.timestamp())}, SECRET_KEY, algorithm=ALGORITHM)


def usuario_actual(
    credenciales: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> models.Usuario:
    try:
        payload = jwt.decode(credenciales.credentials, SECRET_KEY, algorithms=[ALGORITHM])
        usuario_id: Optional[str] = payload.get("sub")
        if usuario_id is None:
            raise ValueError
    except (JWTError, ValueError):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token inválido")

    usuario = db.query(models.Usuario).filter(models.Usuario.id == usuario_id).first()
    if usuario is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Usuario no encontrado")
    if usuario.password_cambiada_en and int(payload.get("iat", 0)) < int(usuario.password_cambiada_en.timestamp()):
        # La contraseña se restableció después de emitir este token: si
        # alguien lo había robado, deja de servir.
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Sesión caducada. Inicia sesión de nuevo.")
    if usuario.bloqueado:
        # Se rechaza aquí (no solo en el login) para que el bloqueo tenga
        # efecto inmediato aunque el usuario ya tuviera un token válido
        # de antes — no hace falta esperar a que caduque.
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Tu cuenta ha sido bloqueada. Contacta con soporte si crees que es un error.")
    return usuario


def _emails_admin() -> set[str]:
    crudo = os.environ.get("ADMIN_EMAILS", "")
    return {e.strip().lower() for e in crudo.split(",") if e.strip()}


bearer_scheme_opcional = HTTPBearer(auto_error=False)


def usuario_opcional(
    credenciales: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme_opcional),
    db: Session = Depends(get_db),
) -> Optional[models.Usuario]:
    """Como usuario_actual, pero devuelve None en vez de fallar si no hay
    token — para endpoints públicos que se comportan distinto si hay
    sesión (ej. ver el propio anuncio pendiente de moderar)."""
    if not credenciales:
        return None
    try:
        payload = jwt.decode(credenciales.credentials, SECRET_KEY, algorithms=[ALGORITHM])
        usuario_id: Optional[str] = payload.get("sub")
        if usuario_id is None:
            return None
    except JWTError:
        return None
    usuario = db.query(models.Usuario).filter(models.Usuario.id == usuario_id).first()
    if usuario and usuario.bloqueado:
        return None  # se trata como anónimo — este endpoint es opcional, no hace falta un 403 duro aquí
    return usuario


def usuario_admin_actual(usuario: models.Usuario = Depends(usuario_actual)) -> models.Usuario:
    """
    Comprueba que el usuario autenticado sea administrador — se decide por
    una lista de emails en la variable de entorno ADMIN_EMAILS (separados
    por comas), no por una columna en la base de datos. Así se pueden
    añadir o quitar administradores sin tocar datos ni desplegar código
    nuevo, solo cambiando esa variable en Render.
    """
    if usuario.email.lower() not in _emails_admin():
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No tienes permisos de administrador")
    # El email de la cuenta tiene que estar comprobado: o bien lo verificó
    # por código, o bien entró con Google/Apple, que ya lo verifican ellos.
    # Así nadie puede hacerse admin registrando el email sin ser su dueño.
    if not usuario.email_verificado and usuario.metodo_login not in (models.MetodoLogin.google, models.MetodoLogin.apple):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Verifica tu email para acceder al panel de administración")
    return usuario
