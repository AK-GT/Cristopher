"""Visión/lectura multimodal de CRISTOPHER (Fase 5) — pregunta sobre una imagen o un
documento con Gemini.

Una llamada multimodal de un solo turno, con la MISMA cadena de modelos principal→
respaldo que el bucle (429/503/500 → FALLBACK_MODEL, otro Gemini — nunca Gemma). Devuelve
texto para que encaje en el bucle ReAct (solo texto). Degrada con elegancia: si
ningún modelo responde, mensaje claro.
"""

from __future__ import annotations

from typing import Any

from google import genai
from google.genai import errors, types

from cristopher.config import FALLBACK_MODEL, MODEL, get_api_key

_client: genai.Client | None = None


def _models() -> list[str]:
    models = [MODEL]
    if FALLBACK_MODEL and FALLBACK_MODEL != MODEL:
        models.append(FALLBACK_MODEL)
    return models


def _preguntar(contents: list[Any]) -> str:
    """Lanza `contents` (texto + partes multimodales) contra la cadena de modelos.

    Prueba el principal y, si falla con 429 (sin cuota) o 503/500 (saturado), cae al de
    respaldo (§8). Antes solo se caía ante 429, así que un 503 del principal rompía la
    visión aunque el respaldo estuviera disponible."""
    global _client
    if _client is None:
        _client = genai.Client(api_key=get_api_key())

    last_exc: Exception | None = None
    for model in _models():
        try:
            resp = _client.models.generate_content(model=model, contents=contents)
            return (resp.text or "").strip() or "(el modelo no devolvió texto)"
        except errors.APIError as exc:
            last_exc = exc
            if getattr(exc, "code", None) in (429, 500, 503):
                continue  # prueba el siguiente modelo (misma política que agent._generate)
            raise
    raise RuntimeError(
        f"El modelo no está disponible ahora mismo (cuota agotada o saturado). "
        f"Detalle: {last_exc}"
    )


def preguntar_sobre_imagen(png_bytes: bytes, pregunta: str) -> str:
    """Devuelve la respuesta del modelo a `pregunta` sobre la imagen PNG dada."""
    image = types.Part.from_bytes(data=png_bytes, mime_type="image/png")
    return _preguntar([pregunta, image])


def preguntar_sobre_documento(data: bytes, mime_type: str, pregunta: str) -> str:
    """Devuelve la respuesta del modelo a `pregunta` sobre un documento (PDF, imagen…).

    Gemini es multimodal y lee PDFs/imágenes directamente. `mime_type` p. ej.
    'application/pdf', 'image/png', 'image/jpeg'."""
    parte = types.Part.from_bytes(data=data, mime_type=mime_type)
    return _preguntar([pregunta, parte])


def preguntar_texto(prompt: str) -> str:
    """Llamada de texto de un solo turno con la misma cadena principal→respaldo.

    Útil para sintetizar texto ya extraído (p. ej. resumir un .txt o .docx)."""
    return _preguntar([prompt])
