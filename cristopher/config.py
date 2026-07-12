"""Configuración central de CRISTOPHER.

Carga el entorno desde .env y expone la clave y el modelo. Falla de forma
explícita si falta la clave (esencia §1: "fallos explícitos", nunca fingir éxito).
El secreto vive SOLO en el entorno, jamás en el código (build prompt §0/§9).
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# Raíz del proyecto = carpeta que contiene el paquete `cristopher`.
ROOT = Path(__file__).resolve().parent.parent

# Carga .env de la raíz si existe (no pisa variables ya definidas en el sistema).
load_dotenv(ROOT / ".env")

# Modelo del cerebro. Decidido en el build prompt: gemini-2.5-flash (free tier).
MODEL = os.getenv("CRISTOPHER_MODEL", "gemini-2.5-flash")

# Directorio de trabajo donde CRISTOPHER clona repos / crea archivos temporales.
WORKSPACE = ROOT / "workspace"


class ConfigError(RuntimeError):
    """Fallo de configuración que impide arrancar (p. ej. falta la API key)."""


def get_api_key() -> str:
    """Devuelve la GEMINI_API_KEY o lanza ConfigError con instrucciones claras."""
    key = os.getenv("GEMINI_API_KEY", "").strip()
    if not key:
        raise ConfigError(
            "Falta GEMINI_API_KEY.\n"
            "  1) Consíguela gratis en https://aistudio.google.com/apikey\n"
            "  2) Copia .env.example a .env y pega la clave ahí, o exporta\n"
            "     la variable de entorno GEMINI_API_KEY antes de arrancar."
        )
    return key
