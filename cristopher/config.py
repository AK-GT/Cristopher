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

# Modelo del cerebro. El build prompt pedía gemini-2.5-flash, pero Google lo
# retiró para cuentas nuevas (404). Usamos el alias `gemini-flash-latest`, que
# siempre apunta al flash vigente del free tier y evita futuras deprecaciones.
# Sobrescribible con la variable de entorno CRISTOPHER_MODEL.
MODEL = os.getenv("CRISTOPHER_MODEL", "gemini-flash-latest")

# Directorio de trabajo donde CRISTOPHER clona repos / crea archivos temporales.
WORKSPACE = ROOT / "workspace"

# Almacén local de memoria persistente (SQLite). No se versiona (.gitignore).
DATA = ROOT / "data"

# Carpeta donde cada sub-agente delegado trabaja aislado (bajo workspace/).
SUBAGENTS = WORKSPACE / "subagents"
# Timeout amplio: las tareas de código delegadas pueden tardar.
SUBAGENT_TIMEOUT = 600

# Modelo de embeddings para el recuerdo semántico (free tier, dim 3072).
EMBED_MODEL = os.getenv("CRISTOPHER_EMBED_MODEL", "gemini-embedding-001")


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
