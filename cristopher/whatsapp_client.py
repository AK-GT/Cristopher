"""Cliente Python del servicio Node de WhatsApp (Baileys).

Gestiona el proceso Node como singleton perezoso (mismo patrón que
`cristopher/browser.py` para Playwright): la primera llamada lo lanza en segundo
plano y espera a que responda /health; las siguientes reutilizan ese proceso. Si ya
hay un servicio escuchando en el puerto (p. ej. lanzado a mano con `npm start`), se
adopta sin gestionar su ciclo de vida ni matarlo al salir.

Riesgo conocido (ya asumido, no re-litigar): Baileys es una librería NO OFICIAL que
reimplementa WhatsApp Web; usarla conlleva riesgo de que Meta limite o banee el número.
"""

from __future__ import annotations

import atexit
import os
import shutil
import subprocess
import threading
import time
from typing import Optional

import requests

from cristopher.config import (
    WHATSAPP_DIR,
    WHATSAPP_HTTP_TIMEOUT,
    WHATSAPP_LOG,
    WHATSAPP_PORT,
    WHATSAPP_SESSION_DIR,
    WHATSAPP_START_TIMEOUT,
    WHATSAPP_STORE_DIR,
)

_BASE_URL = f"http://127.0.0.1:{WHATSAPP_PORT}"


class WhatsAppError(RuntimeError):
    """El servicio de WhatsApp no está disponible o devolvió un fallo."""


_PROC: Optional[subprocess.Popen] = None
_LOCK = threading.Lock()


def _healthy() -> bool:
    try:
        requests.get(f"{_BASE_URL}/health", timeout=1.5).raise_for_status()
        return True
    except requests.exceptions.RequestException:
        return False


def _spawn() -> subprocess.Popen:
    node = shutil.which("node")
    if not node:
        raise WhatsAppError(
            "Falta Node.js instalado (o no está en el PATH). Instálalo y ejecuta "
            "'npm install' dentro de la carpeta whatsapp/ antes de usar esta tool."
        )

    server_js = WHATSAPP_DIR / "server.js"
    if not server_js.exists():
        raise WhatsAppError(f"No encuentro {server_js}. ¿Está completa la carpeta whatsapp/?")

    WHATSAPP_LOG.parent.mkdir(parents=True, exist_ok=True)
    WHATSAPP_SESSION_DIR.mkdir(parents=True, exist_ok=True)
    WHATSAPP_STORE_DIR.mkdir(parents=True, exist_ok=True)

    env = dict(os.environ)
    env["CRISTOPHER_WHATSAPP_PORT"] = str(WHATSAPP_PORT)
    env["CRISTOPHER_WHATSAPP_SESSION_DIR"] = str(WHATSAPP_SESSION_DIR)
    env["CRISTOPHER_WHATSAPP_STORE_DIR"] = str(WHATSAPP_STORE_DIR)

    log = open(WHATSAPP_LOG, "a", encoding="utf-8")
    try:
        return subprocess.Popen(
            [node, str(server_js)],
            cwd=str(WHATSAPP_DIR),
            stdout=log,
            stderr=subprocess.STDOUT,
            env=env,
        )
    except Exception as exc:
        raise WhatsAppError(f"No pude lanzar el servicio de WhatsApp: {exc}") from exc


def _ensure_running() -> None:
    """Asegura que hay un servicio de WhatsApp escuchando en WHATSAPP_PORT.

    No reintenta en bucle: si el arranque falla, lanza WhatsAppError. La siguiente
    llamada (siguiente tool call o siguiente ciclo del demonio, ~60s después) lo
    intentará de nuevo — nunca reintenta dentro de esta misma llamada más de una vez.
    """
    global _PROC
    with _LOCK:
        if _PROC is not None and _PROC.poll() is None:
            return  # ya lo lanzamos nosotros y sigue vivo

        if _PROC is not None:
            _PROC = None  # el que lanzamos murió: se permite un respawn

        if _healthy():
            return  # ya hay un servicio sirviendo (lanzado a mano o de una sesión previa)

        _PROC = _spawn()

        limite = time.monotonic() + WHATSAPP_START_TIMEOUT
        while time.monotonic() < limite:
            if _PROC.poll() is not None:
                raise WhatsAppError(
                    f"El servicio de WhatsApp se cerró al arrancar (código "
                    f"{_PROC.returncode}). Revisa el log: {WHATSAPP_LOG}"
                )
            if _healthy():
                return
            time.sleep(0.5)

        raise WhatsAppError(
            f"El servicio de WhatsApp no respondió en {WHATSAPP_START_TIMEOUT}s. "
            f"Revisa el log: {WHATSAPP_LOG}"
        )


def _request(method: str, path: str, **kwargs) -> dict:
    _ensure_running()
    try:
        resp = requests.request(
            method, f"{_BASE_URL}{path}", timeout=WHATSAPP_HTTP_TIMEOUT, **kwargs
        )
    except requests.exceptions.RequestException as exc:
        raise WhatsAppError(f"No pude hablar con el servicio de WhatsApp: {exc}") from exc

    try:
        data = resp.json()
    except ValueError as exc:
        raise WhatsAppError(f"Respuesta inesperada del servicio de WhatsApp: {exc}") from exc

    if resp.status_code >= 400:
        raise WhatsAppError(data.get("error") or f"HTTP {resp.status_code}")
    return data


def estado() -> dict:
    """Estado crudo de /health: {"estado": "connecting"|"open"|"close"|"logged_out"|"qr_required"}."""
    return _request("GET", "/health")


def check_new() -> dict:
    """{"estado": ..., "chats": [{"chat_id","nombre","n_nuevos","ultimo_texto","ultimo_id"}]}."""
    return _request("GET", "/check-new")


def read(chat_id: str, n: int) -> dict:
    """{"chat_id","nombre","mensajes":[{"from","texto","ts","fromMe"}]}."""
    return _request("GET", "/read", params={"chat_id": chat_id, "n": n})


def send(chat_id: str, texto: str) -> dict:
    """Envía un mensaje. Lanza WhatsAppError si el servicio reporta fallo (ok=false)."""
    data = _request("POST", "/send", json={"chat_id": chat_id, "texto": texto})
    if not data.get("ok"):
        raise WhatsAppError(data.get("error") or "Fallo desconocido al enviar.")
    return data


@atexit.register
def _cerrar() -> None:
    """Termina el proceso Node SOLO si lo lanzamos nosotros (no uno adoptado ya
    vivo) — igual que browser.py no persiste Chromium entre reinicios."""
    if _PROC is not None and _PROC.poll() is None:
        try:
            _PROC.terminate()
        except Exception:
            pass
