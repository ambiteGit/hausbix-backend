"""
Tipo de cambio entre monedas, para poder comparar y mostrar precios de
anuncios publicados en monedas distintas (USD, UYU, ARS, EUR...) sin
excluir ninguno de las búsquedas solo por estar en otra moneda.

Se consulta una API externa gratuita (sin necesidad de clave) y se
guarda en caché durante unas horas — no hace falta una tasa exacta al
segundo para este uso, y así se evita depender de que esa API esté
disponible en cada búsqueda. Si la consulta falla (sin red, la API
caída, etc.), se usan las últimas tasas que se llegaron a obtener con
éxito, o si nunca se consiguió ninguna, unas tasas de respaldo
aproximadas — mejor una conversión aproximada que dejar de mostrar
resultados por completo.
"""
import time
import httpx

_cache: dict = {"tasas": None, "actualizado_en": 0.0}
_DURACION_CACHE_SEGUNDOS = 6 * 60 * 60  # 6 horas

# Tasas de respaldo (unidades de cada moneda por 1 USD) — aproximadas,
# solo para cuando todavía no se ha conseguido ninguna tasa real y la
# API externa no responde. Se actualizan solas en cuanto la API vuelva
# a estar disponible.
_TASAS_RESPALDO = {"USD": 1.0, "UYU": 40.0, "ARS": 1000.0, "EUR": 0.92, "GBP": 0.79, "BRL": 5.3}


def obtener_tasas_usd() -> dict:
    """Devuelve cuántas unidades de cada moneda equivalen a 1 USD."""
    ahora = time.time()
    if _cache["tasas"] and (ahora - _cache["actualizado_en"] < _DURACION_CACHE_SEGUNDOS):
        return _cache["tasas"]
    try:
        resp = httpx.get("https://open.er-api.com/v6/latest/USD", timeout=5)
        datos = resp.json()
        tasas = datos.get("rates")
        if tasas:
            _cache["tasas"] = tasas
            _cache["actualizado_en"] = ahora
            return tasas
    except Exception:
        pass  # se sigue con lo que hubiera en caché, o el respaldo — nunca se relanza el error
    return _cache["tasas"] or _TASAS_RESPALDO


def convertir(monto: float, moneda_origen: str, moneda_destino: str) -> float:
    """Convierte un monto de una moneda a otra con la tasa actual (cacheada)."""
    if moneda_origen == moneda_destino:
        return monto
    tasas = obtener_tasas_usd()
    tasa_origen = tasas.get(moneda_origen, 1.0)
    tasa_destino = tasas.get(moneda_destino, 1.0)
    monto_en_usd = monto / tasa_origen
    return monto_en_usd * tasa_destino
