"""
Genera un resumen breve, en lenguaje natural, del conjunto de opiniones de
un anuncio — se llama cada vez que se crea una reseña nueva (ver
routers/resenas.py), no en cada lectura del anuncio, así que el coste de
la llamada a la IA no se paga en cada visita.

Usa la API de Mensajes de Anthropic directamente por HTTP (con httpx, que
ya es dependencia del proyecto) en vez de añadir el SDK completo como
dependencia nueva solo para esto.

Si ANTHROPIC_API_KEY no está configurada, o la llamada falla por lo que
sea (red, límite de la API, timeout...), se devuelve None sin levantar
excepción — el anuncio se queda sin resumen por IA (o con el último que
tuviera) pero la reseña en sí se guarda igualmente; nunca debe bloquear
la creación de una reseña por un fallo de la IA.
"""
import os
import httpx

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_RESENAS_MODEL", "claude-haiku-4-5-20251001")

MAX_RESENAS_EN_PROMPT = 60  # de sobra para un resumen fiable, sin mandar un historial interminable en cada llamada


def generar_resumen_opiniones(titulo_inmueble: str, resenas: list[tuple[int, str | None]]) -> str | None:
    """
    `resenas` es una lista de (puntuacion, comentario) de TODAS las
    reseñas del anuncio (comentario puede ser None si el huésped solo
    puntuó sin escribir nada). Devuelve el resumen en español, pensado
    para mostrarse tal cual en la ficha del anuncio, o None si no se pudo
    generar (sin API key configurada, sin comentarios con texto, o fallo
    de la llamada).
    """
    if not ANTHROPIC_API_KEY:
        return None

    con_texto = [(p, c.strip()) for p, c in resenas if c and c.strip()]
    if not con_texto:
        return None  # solo hay puntuaciones sin comentario — no hay nada que resumir en palabras

    bloque_resenas = "\n".join(
        f"- {puntuacion}/5 estrellas: \"{comentario}\""
        for puntuacion, comentario in con_texto[-MAX_RESENAS_EN_PROMPT:]
    )

    prompt = f"""Estas son las opiniones de huéspedes que se han alojado en "{titulo_inmueble}", un alojamiento publicado en Hausbix:

{bloque_resenas}

Escribe un resumen breve (2-3 frases, máximo 60 palabras) en español neutro, en tono natural y objetivo, de lo que opinan en conjunto los huéspedes. Menciona los aspectos que se repiten (tanto positivos como negativos si los hay), sin inventar nada que no esté en las opiniones y sin citar literalmente a ningún huésped. No empieces con frases como "Los huéspedes dicen que" — ve directo al contenido. Devuelve solo el texto del resumen, sin comillas ni explicaciones adicionales."""

    try:
        resp = httpx.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": ANTHROPIC_MODEL,
                "max_tokens": 200,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=20.0,
        )
        resp.raise_for_status()
        data = resp.json()
        texto = "".join(bloque.get("text", "") for bloque in data.get("content", []) if bloque.get("type") == "text")
        texto = texto.strip()
        return texto or None
    except Exception:
        # Red caída, límite de la API, respuesta inesperada... lo que sea —
        # no es motivo para que falle la creación de la reseña.
        return None
