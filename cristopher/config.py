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
# retiró para cuentas nuevas (404). Fijamos gemini-3.5-flash (verificado: function
# calling + multimodal). Sobrescribible con la variable de entorno CRISTOPHER_MODEL.
MODEL = os.getenv("CRISTOPHER_MODEL", "gemini-3.5-flash")

# Cerebro de respaldo: si el principal agota su cuota (429), CRISTOPHER cae a este y
# sigue funcionando (§8 "degrada con elegancia"). Gemma 4 tiene un bucket de cuota
# gratuito aparte y soporta function calling + visión. Poner "" para desactivarlo.
FALLBACK_MODEL = os.getenv("CRISTOPHER_FALLBACK_MODEL", "gemma-4-31b-it")

# Modo de salida: "texto" (REPL) o "voz" (Fase 6). En voz, el pensamiento intermedio
# NO se verbaliza: solo se habla la respuesta final. El flag deja esto preparado.
MODO_SALIDA = os.getenv("CRISTOPHER_MODO_SALIDA", "texto").strip().lower()
MOSTRAR_PENSAMIENTO = MODO_SALIDA != "voz"

# Directorio de trabajo donde CRISTOPHER clona repos / crea archivos temporales.
WORKSPACE = ROOT / "workspace"

# Almacén local de memoria persistente (SQLite). No se versiona (.gitignore).
DATA = ROOT / "data"

# Carpeta donde cada sub-agente delegado trabaja aislado (bajo workspace/).
SUBAGENTS = WORKSPACE / "subagents"
# Timeout amplio: las tareas de código delegadas pueden tardar.
SUBAGENT_TIMEOUT = 600

# --- Fase 4: integraciones Google + búsqueda de élite ------------------------
# Credenciales OAuth y token persistente (bajo data/, gitignored; §9).
GOOGLE_DIR = DATA / "google"
GOOGLE_CREDENTIALS = GOOGLE_DIR / "credentials.json"  # lo descarga el usuario
GOOGLE_TOKEN = GOOGLE_DIR / "token.json"              # se genera en el consentimiento

# Scopes: lectura de Calendar y Gmail + envío de correo (gateado por confirmación).
GOOGLE_SCOPES = [
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
]

# Búsqueda de élite (opcional). Sin ella, la búsqueda cae a DuckDuckGo.
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "").strip()

# --- Fase 8: HUD -------------------------------------------------------------
HUD_PORT = int(os.getenv("CRISTOPHER_HUD_PORT", "8765"))

# --- Fase 7: proactividad ----------------------------------------------------
PROACTIVO_DB = DATA / "proactivo.db"          # recordatorios + dedup de avisos
POLL_SEGUNDOS = int(os.getenv("CRISTOPHER_POLL", "60"))   # cada cuánto revisa el demonio
LEAD_MINUTOS = int(os.getenv("CRISTOPHER_LEAD", "15"))    # antelación para avisar de eventos
# Mini-modelo que clasifica la prioridad (1-3) de cada aviso. Fallback: FALLBACK_MODEL.
CLASSIFIER_MODEL = os.getenv("CRISTOPHER_CLASSIFIER_MODEL", "gemini-flash-lite-latest")
GMAIL_QUERY_PROACTIVO = os.getenv("CRISTOPHER_GMAIL_QUERY", "is:unread")

# --- Fase 6: voz -------------------------------------------------------------
VOICE_DIR = DATA / "voice"
WHISPER_DIR = VOICE_DIR / "whisper"          # caché del modelo faster-whisper
STT_MODEL = os.getenv("CRISTOPHER_STT_MODEL", "small")  # tamaño de faster-whisper
STT_LANG = "es"
PIPER_VOICE = VOICE_DIR / "piper" / "es_ES-davefx-medium.onnx"  # voz TTS

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
