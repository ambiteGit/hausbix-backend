"""
Límite de intentos en memoria, para frenar fuerza bruta (contraseñas, códigos
de verificación) y abuso de los envíos de SMS/email, que cuestan dinero.

Es por proceso: Render free/starter ejecuta una sola instancia, así que basta.
Si algún día se escala a varias instancias, habría que moverlo a Redis.
"""
import time
from collections import defaultdict, deque
from fastapi import HTTPException, Request

_intentos: dict[str, deque] = defaultdict(deque)
_ultima_limpieza = 0.0


def ip_cliente(request: Request) -> str:
    # Detrás del proxy de Render la IP real llega en X-Forwarded-For (la
    # añade el proxy al final de la lista; lo que el cliente ponga antes se
    # puede falsificar, por eso se toma la última).
    cabecera = request.headers.get("x-forwarded-for", "")
    if cabecera:
        return cabecera.split(",")[-1].strip()
    return request.client.host if request.client else "desconocida"


def _limpiar(ahora: float) -> None:
    global _ultima_limpieza
    if ahora - _ultima_limpieza < 300:
        return
    _ultima_limpieza = ahora
    for clave in [k for k, v in _intentos.items() if not v or ahora - v[-1] > 7200]:
        del _intentos[clave]


def limitar(nombre: str, clave: str, maximo: int, ventana_segundos: int) -> None:
    """Cuenta un intento de `clave` en el cubo `nombre`; si supera `maximo`
    en la ventana, responde 429."""
    ahora = time.time()
    _limpiar(ahora)
    cola = _intentos[f"{nombre}:{clave}"]
    while cola and ahora - cola[0] > ventana_segundos:
        cola.popleft()
    if len(cola) >= maximo:
        espera = int(ventana_segundos - (ahora - cola[0])) + 1
        raise HTTPException(
            status_code=429,
            detail="Demasiados intentos. Espera unos minutos antes de volver a intentarlo.",
            headers={"Retry-After": str(espera)},
        )
    cola.append(ahora)
