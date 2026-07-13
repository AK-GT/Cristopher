"""Bus de estado de CRISTOPHER para el HUD (Fase 8).

Estado thread-safe + pub/sub. El bucle (on_step), la voz, las métricas y el demonio
PUBLICAN aquí; las conexiones SSE del HUD SE SUSCRIBEN. Es la fuente única para que el
HUD refleje estado REAL y nunca invente datos.
"""

from __future__ import annotations

import json
import queue
import threading
from collections import deque
from datetime import datetime
from typing import Any

_LOCK = threading.Lock()

# Estado actual (snapshot para la carga inicial).
_estado: dict[str, Any] = {
    "estado": "reposo",          # reposo | pensando | hablando | escuchando
    "identidad": "CRISTOPHER",
    "tarea": "",                 # tarea/petición en curso
    "subagentes": [],            # roster: [{nombre, carpeta}]
    "metricas": {"cpu": 0.0, "ram": 0.0},
    "alertas": [],               # [{nivel, texto, hora}]
}
_log: deque = deque(maxlen=200)   # trazas recientes: [{kind, text, hora}]
_subs: list[queue.Queue] = []     # colas de suscriptores SSE


def _now() -> str:
    return datetime.now().strftime("%H:%M:%S")


def _emit(tipo: str, datos: Any) -> None:
    """Empuja un evento a todos los suscriptores (formato SSE-friendly)."""
    msg = {"tipo": tipo, "datos": datos}
    with _LOCK:
        muertos = []
        for q in _subs:
            try:
                q.put_nowait(msg)
            except queue.Full:
                muertos.append(q)
        for q in muertos:
            _subs.remove(q)


# --- Publicadores -------------------------------------------------------------
def set_estado(estado: str) -> None:
    with _LOCK:
        _estado["estado"] = estado
    _emit("estado", estado)


def set_tarea(texto: str) -> None:
    with _LOCK:
        _estado["tarea"] = texto
    _emit("tarea", texto)


def log(kind: str, text: str) -> None:
    entry = {"kind": kind, "text": text, "hora": _now()}
    with _LOCK:
        _log.append(entry)
    _emit("log", entry)


def set_metricas(cpu: float, ram: float) -> None:
    with _LOCK:
        _estado["metricas"] = {"cpu": cpu, "ram": ram}
    _emit("metricas", {"cpu": cpu, "ram": ram})


def add_subagente(nombre: str, carpeta: str) -> None:
    item = {"nombre": nombre, "carpeta": carpeta, "hora": _now()}
    with _LOCK:
        _estado["subagentes"] = ([item] + _estado["subagentes"])[:8]
    _emit("subagentes", _estado["subagentes"])


def add_alerta(nivel: int, texto: str) -> None:
    item = {"nivel": nivel, "texto": texto, "hora": _now()}
    with _LOCK:
        _estado["alertas"] = ([item] + _estado["alertas"])[:12]
    _emit("alerta", item)


# --- Suscripción SSE ----------------------------------------------------------
def snapshot() -> dict:
    with _LOCK:
        snap = dict(_estado)
        snap["log"] = list(_log)
    return snap


def suscribir() -> queue.Queue:
    q: queue.Queue = queue.Queue(maxsize=500)
    with _LOCK:
        _subs.append(q)
    return q


def desuscribir(q: queue.Queue) -> None:
    with _LOCK:
        if q in _subs:
            _subs.remove(q)


def sse(msg: dict) -> str:
    """Serializa un evento como bloque SSE."""
    return f"data: {json.dumps(msg, ensure_ascii=False)}\n\n"
