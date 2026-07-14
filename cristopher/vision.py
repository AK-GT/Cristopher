"""Visión de CRISTOPHER (Fase 5) — pregunta sobre una imagen con Gemini.

Una llamada multimodal de un solo turno, con la MISMA cadena de modelos principal→
respaldo que el bucle (429 → FALLBACK_MODEL, otro Gemini — nunca Gemma). Devuelve
texto para que encaje en el bucle ReAct (solo texto). Degrada con elegancia: si
ningún modelo responde, mensaje claro.
"""

from __future__ import annotations

from google import genai
from google.genai import errors, types

from cristopher.config import FALLBACK_MODEL, MODEL, get_api_key

_client: genai.Client | None = None


def _models() -> list[str]:
    models = [MODEL]
    if FALLBACK_MODEL and FALLBACK_MODEL != MODEL:
        models.append(FALLBACK_MODEL)
    return models


def preguntar_sobre_imagen(png_bytes: bytes, pregunta: str) -> str:
    """Devuelve la respuesta del modelo a `pregunta` sobre la imagen PNG dada.

    Prueba el modelo principal y, si agota cuota (429), cae al de respaldo (§8)."""
    global _client
    if _client is None:
        _client = genai.Client(api_key=get_api_key())

    image = types.Part.from_bytes(data=png_bytes, mime_type="image/png")
    contents = [pregunta, image]

    last_exc: Exception | None = None
    for model in _models():
        try:
            resp = _client.models.generate_content(model=model, contents=contents)
            return (resp.text or "").strip() or "(la visión no devolvió texto)"
        except errors.APIError as exc:
            last_exc = exc
            if getattr(exc, "code", None) == 429:
                continue  # sin cuota en este modelo: prueba el siguiente
            raise
    raise RuntimeError(
        f"La visión no está disponible ahora mismo (cuota agotada). Detalle: {last_exc}"
    )
