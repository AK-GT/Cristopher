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

from cristopher import estado_code
# Alias: `estado` a secas chocaría con el parámetro de set_estado() de más abajo.
from cristopher import estado as estado_voz

_LOCK = threading.Lock()

# Estado actual (snapshot para la carga inicial).
_estado: dict[str, Any] = {
    "estado": "reposo",          # reposo | pensando | hablando | escuchando
    "identidad": "CRISTOPHER",
    "tarea": "",                 # tarea/petición en curso
    "subagentes": [],            # roster: [{nombre, carpeta}]
    "metricas": {"cpu": 0.0, "ram": 0.0},
    "alertas": [],               # [{nivel, texto, hora}]
    "sesiones": [],              # snapshot de GestorSesiones.listar() (Fase 2 modo agentes)
    "concentracion": {"activo": False},  # snapshot de concentracion.estado_actual()
    "modo_code": False,           # snapshot de estado_code.esta_activo() (Cristopher Code)
    "modo_redes": False,          # snapshot de estado_redes.esta_activo() (Modo redes)
    "modo_calendario": False,     # snapshot de estado_calendario.esta_activo()
    # Arranca leyendo el flag real (CRISTOPHER_MODO_SALIDA=voz lo pone a True), no un
    # False fijo: el toggle del HUD debe reflejar el estado, no imponerlo.
    "modo_voz": estado_voz.esta_activo(),
    "modo_auto": False,           # snapshot de estado_auto.esta_activo() (botón ⚡)
    "control_total": False,       # snapshot de estado_control_total.esta_activo() (Fase 4)
    "control_total_actividad": "reposo",  # "reposo"|"trabajando" del bucle (Fase 2)
    "seguro": {"activo": False, "fase": "INACTIVO"},  # snapshot de seguro.estado_actual()
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
    # Etiqueta cada entrada con el modo activo en el momento de loguearla, para que
    # el HUD pueda pintar cada turno en su consola correcta (chat normal vs chat de
    # código) sin mezclar el historial de ambos modos.
    modo = "code" if estado_code.esta_activo() else "normal"
    entry = {"kind": kind, "text": text, "hora": _now(), "modo": modo}
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


def set_sesiones(snapshot: list[dict]) -> None:
    """Snapshot completo de sesiones de Claude Code (modo agentes, Fase 2)."""
    with _LOCK:
        _estado["sesiones"] = snapshot
    _emit("sesiones", snapshot)


def set_modo_code(activo: bool) -> None:
    """Flag del modo code (Cristopher Code): el HUD lo usa para transformar la
    pantalla (núcleo rojo + layout de widgets propio)."""
    with _LOCK:
        _estado["modo_code"] = activo
    _emit("modo_code", activo)


def set_modo_redes(activo: bool) -> None:
    """Flag del Modo redes (dashboard de YouTube/Instagram/TikTok): el HUD lo usa
    para transformar la pantalla (núcleo morado + layout de widgets propio)."""
    with _LOCK:
        _estado["modo_redes"] = activo
    _emit("modo_redes", activo)


def set_modo_calendario(activo: bool) -> None:
    """Flag del Modo calendario (pantalla de agenda). Lo mueve pantalla.py."""
    with _LOCK:
        _estado["modo_calendario"] = activo
    _emit("modo_calendario", activo)


def calendario_foco(fecha: str, borrador: dict | None = None) -> None:
    """ORDEN puntual a la pantalla de calendario: ir a `fecha` (YYYY-MM-DD) y, si
    hay `borrador` ({titulo, inicio, fin} en ISO), pintarlo en fantasma mientras se
    pide la confirmación. Efímera, como `set_seguro_panel`: no es estado."""
    _emit("calendario_foco", {"fecha": fecha, "borrador": borrador})


def calendario_cambio() -> None:
    """Se creó/movió/borró un evento: la pantalla recarga al momento."""
    _emit("calendario_cambio", None)


def set_modo_voz(activo: bool) -> None:
    """Flag de la respuesta hablada (estado.py). El HUD lo usa para pintar su toggle 🔊,
    y hay que publicarlo también cuando lo mueve el propio CRISTOPHER por intención
    (tools/voz_tools.py) o el toggle se quedaría mintiendo."""
    with _LOCK:
        _estado["modo_voz"] = activo
    _emit("modo_voz", activo)


def set_modo_auto(activo: bool) -> None:
    """Flag del modo automático (estado_auto.py): confirmaciones aprobadas solas."""
    with _LOCK:
        _estado["modo_auto"] = activo
    _emit("modo_auto", activo)


def set_control_total(activo: bool) -> None:
    """Flag del Modo Control Total (Fase 4): el HUD lo usa para teñir el núcleo de
    azul; el widget flotante y el borde de pantalla lo consultan (SSE/HTTP) para
    auto-cerrarse cuando pasa a False, en vez de que nadie tenga que matarlos por
    PID."""
    with _LOCK:
        _estado["control_total"] = activo
    _emit("control_total", activo)


def set_control_total_actividad(estado: str) -> None:
    """'trabajando'/'reposo' del bucle percepción-decisión-acción (Fase 2) — el
    borde de pantalla lo sondea por HTTP para decidir el brillo de su pulso."""
    with _LOCK:
        _estado["control_total_actividad"] = estado
    _emit("control_total_actividad", estado)


def add_control_total_entrada(entrada: dict) -> None:
    """Nueva entrada del transcript del bucle (Fase 2), para narrarla en vivo en
    el widget. Efímera (no se guarda en el snapshot), igual que `add_sesion_entrada`."""
    _emit("control_total_entrada", entrada)


def set_concentracion(snapshot: dict) -> None:
    """Snapshot del modo concentración (activo/fase/tiempo restante/avisos en cola)."""
    with _LOCK:
        _estado["concentracion"] = snapshot
    _emit("concentracion", snapshot)


def set_seguro(snapshot: dict) -> None:
    """Snapshot del Modo Seguro (activo/fase de la FSM/estado de la cámara)."""
    with _LOCK:
        _estado["seguro"] = snapshot
    _emit("seguro", snapshot)


def set_seguro_panel(abierto: bool) -> None:
    """Abrir/cerrar el panel de cámara del HUD desde el lado Python (tools
    seguro_ver_camara / seguro_ocultar_camara). EFÍMERO a propósito, solo `_emit` y no
    `_estado`: es una ORDEN puntual al navegador, no un estado que deba reproducirse a
    quien se reconecte más tarde — igual que `add_sesion_entrada`. Que el panel esté
    abierto o no lo sabe el navegador, no el servidor."""
    _emit("seguro_panel", {"abierto": bool(abierto)})


def add_sesion_entrada(nombre: str, entrada: dict) -> None:
    """Nueva entrada de transcript de una sesión, para pintarla en vivo en el HUD.
    Efímera (no se guarda en el snapshot), igual que `pedir_confirmacion`."""
    _emit("sesion_entrada", {"nombre": nombre, "entrada": entrada})


def answer_delta(texto: str) -> None:
    """Trozo de la respuesta final según la va escribiendo el modelo (streaming).

    EFÍMERO a propósito — solo `_emit`, nunca `_log`, igual que `add_sesion_entrada`:
    el deque `_log` tiene tope de 200 entradas y guardar aquí los fragmentos lo
    llenaría de basura, además de hacer que un navegador que se reconecta reprodujera
    la respuesta a trocitos desde `snapshot()`. La entrada canónica e histórica sigue
    siendo el `log("answer", ...)` de siempre, que se emite al terminar el turno."""
    _emit("answer_delta", texto)


def answer_reset() -> None:
    """Descarta la respuesta que se estaba pintando en vivo (el stream se cortó a
    medias y el reintento va a repintarla desde cero)."""
    _emit("answer_reset", None)


def pedir_confirmacion(texto: str, permite_texto: bool = False) -> None:
    """Publica una solicitud de confirmación (p. ej. enviar un correo) para que el HUD
    la muestre con botones Confirmar/Cancelar. No se guarda en el snapshot: es efímera.
    `permite_texto=True` (preguntas de sesión) añade además un campo libre
    para responder con un mensaje propio en vez de solo aceptar/rechazar el borrador."""
    _emit("confirmacion", {"texto": texto, "permite_texto": permite_texto})


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
