"""Widget flotante del Modo Control Total (Fase 3) — ventana propia (pywebview)
apuntando a `/widget` del HUD que ya esté corriendo. Mismo backend/SSE/`/enviar`
que el HUD grande (ver `hud/static/widget.js`): cero estado duplicado.

Vive en su PROPIO proceso, igual que hoy Chrome es un proceso propio del HUD grande
(`hud/__main__.py::main`) — nunca dentro del servidor HTTP: `webview.start()`
bloquea el hilo que lo llama, igual que `srv.serve_forever()` bloquea el suyo.

Uso:
  python -m cristopher.hud.widget_flotante
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

from cristopher.config import CONTROL_TOTAL_WIDGET_PID, CONTROL_TOTAL_WIDGET_POS, HUD_PORT, ROOT
from cristopher.control_total import killswitch

ANCHO, ALTO = 340, 420
MARGEN = 24

# Sondeos consecutivos fallidos (a 0.5s cada uno, ~5s) antes de darse por vencido
# con el HUD y cerrarse solo — mismo umbral que control_overlay.py::FALLOS_MAX.
FALLOS_MAX = 10


class WidgetError(RuntimeError):
    """El widget no pudo arrancar."""


def _hud_vivo() -> bool:
    try:
        with socket.create_connection(("127.0.0.1", HUD_PORT), timeout=0.5):
            return True
    except OSError:
        return False


def _posicion_por_defecto() -> "tuple[int, int]":
    """Esquina inferior derecha de la pantalla principal, vía ctypes (mismo idiom
    que el resto del proyecto — sin pywin32)."""
    import ctypes

    user32 = ctypes.windll.user32
    ancho_pantalla = user32.GetSystemMetrics(0)
    alto_pantalla = user32.GetSystemMetrics(1)
    return (ancho_pantalla - ANCHO - MARGEN, alto_pantalla - ALTO - MARGEN)


def _cargar_posicion() -> "tuple[int, int]":
    try:
        data = json.loads(CONTROL_TOTAL_WIDGET_POS.read_text(encoding="utf-8"))
        return int(data["x"]), int(data["y"])
    except (OSError, ValueError, KeyError, TypeError):
        return _posicion_por_defecto()


def _guardar_posicion(x: int, y: int) -> None:
    try:
        CONTROL_TOTAL_WIDGET_POS.parent.mkdir(parents=True, exist_ok=True)
        CONTROL_TOTAL_WIDGET_POS.write_text(
            json.dumps({"x": int(x), "y": int(y)}), encoding="utf-8",
        )
    except OSError:
        pass


# --- Cierre robusto: kill-switch local + caída del HUD (Fase 4) ------------------
# control_overlay.py ya tenía esto (kill-switch local + auto-cierre si el HUD deja
# de responder); el widget solo cerraba de forma reactiva vía SSE, lo que dejaba la
# ventana flotando para siempre si el HUD moría o si Ctrl+F3 saltaba sin que el
# roundtrip HTTP llegara a tiempo.
def _sondear_cierre(window, detenido: threading.Event) -> None:
    fallos = 0
    while not detenido.is_set():
        time.sleep(0.5)
        if detenido.is_set():
            return
        if killswitch.disparado():
            break
        if _hud_vivo():
            fallos = 0
        else:
            fallos += 1
            if fallos >= FALLOS_MAX:
                break
    try:
        window.destroy()
    except Exception:
        pass


# --- PID-guard: no abrir un segundo widget si ya hay uno vivo (Fase 4) ------------
def _pid_guardado() -> "int | None":
    try:
        return int(CONTROL_TOTAL_WIDGET_PID.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def _proceso_vivo(pid: int) -> bool:
    try:
        import psutil
        return psutil.pid_exists(pid)
    except Exception:
        return False


def _guardar_pid(pid: int) -> None:
    try:
        CONTROL_TOTAL_WIDGET_PID.parent.mkdir(parents=True, exist_ok=True)
        CONTROL_TOTAL_WIDGET_PID.write_text(str(pid), encoding="utf-8")
    except OSError:
        pass


def _borrar_pid() -> None:
    try:
        CONTROL_TOTAL_WIDGET_PID.unlink(missing_ok=True)
    except OSError:
        pass


class _Api:
    """Puente JS→Python (pywebview `js_api`): botones propios de minimizar/
    restaurar/cerrar, ya que una ventana `frameless` no trae los de Windows.
    `window` se fija DESPUÉS de crear la ventana (orden obligado: la ventana no
    existe todavía cuando se instancia el objeto que se le pasa a create_window)."""

    def __init__(self) -> None:
        self.window = None

    def minimizar(self) -> None:
        if self.window:
            self.window.minimize()

    def restaurar(self) -> None:
        if self.window:
            self.window.restore()

    def cerrar(self) -> None:
        if self.window:
            self.window.destroy()


def lanzar() -> None:
    """Abre el widget contra el HUD ya en marcha. BLOQUEA (`webview.start()`) —
    llamar solo desde un proceso propio, nunca desde el servidor HTTP."""
    if not _hud_vivo():
        raise WidgetError(
            f"El HUD no está corriendo en el puerto {HUD_PORT}. "
            "Arráncalo primero: python -m cristopher"
        )
    import webview

    # Por defecto, easy_drag arrastra la ventana con CUALQUIER mousedown en toda
    # la página (verificado en el código fuente de pywebview: sin esto, hasta un
    # clic en los botones o en el input de texto inicia un arrastre). Con esta
    # opción, solo arrastran los elementos marcados con la clase
    # "pywebview-drag-region" en widget.html (la franja del header, no los
    # botones ni el input).
    webview.settings["DRAG_REGION_DIRECT_TARGET_ONLY"] = True

    x, y = _cargar_posicion()
    api = _Api()
    window = webview.create_window(
        "CRISTOPHER", url=f"http://localhost:{HUD_PORT}/widget",
        width=ANCHO, height=ALTO, x=x, y=y,
        frameless=True, on_top=True, transparent=True, easy_drag=True,
        js_api=api,
    )
    api.window = window

    # `moved` se dispara muchas veces durante un arrastre — solo se cachea en
    # memoria (barato); se escribe a disco UNA vez al cerrar, con la última
    # posición conocida (leer window.x/window.y en `closed` no es fiable: el
    # backend de Windows ya puede haber destruido la ventana nativa).
    ultima_pos = [x, y]

    def _on_moved(x_, y_):
        try:
            ultima_pos[0], ultima_pos[1] = x_, y_
        except Exception:
            pass  # un callback roto NUNCA debe interrumpir el cierre de pywebview

    detenido = threading.Event()

    def _on_closed():
        # Verificado real: una excepción sin capturar aquí puede cortar a media
        # el propio cierre interno de pywebview (Application.Exit() no llega a
        # ejecutarse) y el proceso se queda colgado aunque la ventana ya no se
        # vea — por eso el try/except es total, no solo OSError.
        try:
            detenido.set()
            _guardar_posicion(*ultima_pos)
            _borrar_pid()
        except Exception:
            pass

    window.events.moved += _on_moved
    window.events.closed += _on_closed

    # Kill-switch LOCAL a este proceso + auto-cierre si el HUD cae, igual que
    # control_overlay.py — sin esto, Ctrl+F3 no cierra el widget cuando el HUD no
    # puede avisarle por SSE (caído, o el evento se pierde en el reconecte).
    killswitch.iniciar_killswitch()
    threading.Thread(target=_sondear_cierre, args=(window, detenido), daemon=True).start()

    webview.start()


def _pythonw() -> str:
    exe = Path(sys.executable)
    pw = exe.with_name("pythonw.exe")
    return str(pw if pw.exists() else exe)


def lanzar_proceso() -> None:
    """Lanza el widget como proceso propio y desacoplado (mismo patrón que
    `escucha.py::_lanzar_hud`): sobrevive aunque el proceso que llama a esto
    termine. PID-guard: si ya hay un widget vivo (p. ej. el usuario lo abrió a
    mano, o una activación anterior no lo cerró), no abre una segunda ventana."""
    pid = _pid_guardado()
    if pid is not None and _proceso_vivo(pid):
        return
    creationflags = 0
    if os.name == "nt":
        # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
        creationflags = 0x00000008 | 0x00000200
    proc = subprocess.Popen(
        [_pythonw(), "-m", "cristopher.hud.widget_flotante"],
        cwd=str(ROOT), creationflags=creationflags,
    )
    _guardar_pid(proc.pid)


def main() -> int:
    try:
        lanzar()
    except WidgetError as exc:
        print(f"[widget] {exc}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
