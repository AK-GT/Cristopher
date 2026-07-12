"""Autenticación con Google (Calendar + Gmail) para CRISTOPHER (Fase 4).

Flujo OAuth de app de escritorio: el usuario descarga `credentials.json` de Google
Cloud (una vez), y el primer uso abre el navegador para consentir; el token resultante
se guarda en `token.json` y se renueva solo. Credenciales y token viven bajo data/,
que está en .gitignore (§9: nunca se versionan secretos).

Errores explícitos y accionables si falta algo (§1: no fingir éxito).
"""

from __future__ import annotations

from typing import Optional

from cristopher.config import (
    GOOGLE_CREDENTIALS,
    GOOGLE_DIR,
    GOOGLE_SCOPES,
    GOOGLE_TOKEN,
)


class GoogleAuthError(RuntimeError):
    """Falta de credenciales o fallo de autenticación con Google."""


# Servicios cacheados (build perezoso).
_SERVICES: dict[str, object] = {}


def _load_credentials():
    """Devuelve credenciales OAuth válidas, refrescándolas o lanzando el flujo de
    consentimiento si hace falta. Lanza GoogleAuthError con instrucciones si falta
    el archivo de credenciales."""
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow

    creds: Optional[Credentials] = None

    if GOOGLE_TOKEN.exists():
        creds = Credentials.from_authorized_user_file(str(GOOGLE_TOKEN), GOOGLE_SCOPES)

    if creds and creds.valid:
        return creds

    # Token expirado pero con refresh_token: renovar sin molestar al usuario.
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            _save_token(creds)
            return creds
        except Exception:
            creds = None  # cae al flujo de consentimiento

    # No hay token válido: necesita el archivo de credenciales para consentir.
    if not GOOGLE_CREDENTIALS.exists():
        raise GoogleAuthError(
            "Falta el archivo de credenciales de Google.\n"
            f"  Colócalo en: {GOOGLE_CREDENTIALS}\n"
            "  Cómo obtenerlo: Google Cloud Console → habilita Calendar API y Gmail\n"
            "  API → pantalla de consentimiento OAuth → crea credenciales tipo\n"
            "  'App de escritorio' → descarga el JSON."
        )

    GOOGLE_DIR.mkdir(parents=True, exist_ok=True)
    flow = InstalledAppFlow.from_client_secrets_file(
        str(GOOGLE_CREDENTIALS), GOOGLE_SCOPES
    )
    # Abre el navegador para login/consentimiento (paso interactivo, una vez).
    creds = flow.run_local_server(port=0)
    _save_token(creds)
    return creds


def _save_token(creds) -> None:
    GOOGLE_DIR.mkdir(parents=True, exist_ok=True)
    GOOGLE_TOKEN.write_text(creds.to_json(), encoding="utf-8")


def _get_service(name: str, version: str):
    """Construye (y cachea) un servicio autorizado de la API de Google."""
    key = f"{name}:{version}"
    if key not in _SERVICES:
        from googleapiclient.discovery import build

        creds = _load_credentials()
        _SERVICES[key] = build(name, version, credentials=creds, cache_discovery=False)
    return _SERVICES[key]


def get_calendar():
    """Servicio autorizado de Google Calendar (v3)."""
    return _get_service("calendar", "v3")


def get_gmail():
    """Servicio autorizado de Gmail (v1)."""
    return _get_service("gmail", "v1")


def authorize() -> str:
    """Fuerza el flujo de consentimiento (útil para el setup inicial). Devuelve un
    mensaje de estado."""
    _load_credentials()
    return f"Autenticación con Google lista. Token guardado en {GOOGLE_TOKEN}."
