"""Borde de pantalla del Modo Control Total (Fase 3) — ventana transparente,
click-through, con un degradado azul en los bordes que respira según el estado
real. Sin dependencias nuevas: `tkinter` (stdlib) + `ctypes` (extiende el idiom ya
establecido en `pantalla_tools.py`/`control_pc_tools.py`/`system_apps.py`).

Vive en su PROPIO proceso (mismo motivo que `widget_flotante.py`: el bucle de
eventos de Tk bloquea el hilo que lo llama). Precisamente POR eso (Fase 4,
corrección de arquitectura real): la señal de "trabajando" no puede leerse
importando `cristopher.control_total.bucle` directamente — ese import crearía un
`_BUCLE` propio y vacío en ESTE proceso, no el bucle real que corre dentro del
proceso del HUD. Se consulta por HTTP (`GET /estado`), igual que el widget ya usa
SSE — mismo criterio, mismo servidor. El kill-switch local (Ctrl+F3) SÍ se queda
por import directo: cada proceso reacciona a la misma tecla física de forma
independiente, eso es correcto por diseño (Fase 1).

Receta de click-through (verificada en la práctica: renderizado, click-through y
apagado por kill-switch confirmados a ojo y a golpe de ratón real): fondo a un
color clave ("-transparentcolor") que Windows ya trata como invisible/no-clicable
vía capas (`WS_EX_LAYERED`, que Tk activa solo por usar ese atributo) +
`WS_EX_TRANSPARENT` añadido a mano para que TAMBIÉN el degradado dibujado (no solo
el fondo) deje pasar el clic.

Uso:
  python -m cristopher.hud.control_overlay
"""

from __future__ import annotations

import ctypes
import json
import math
import os
import socket
import subprocess
import sys
import threading
import time
import tkinter as tk
import urllib.request
from ctypes import wintypes
from pathlib import Path

from cristopher.config import (
    CONTROL_TOTAL_OVERLAY_COLOR,
    CONTROL_TOTAL_OVERLAY_PID,
    HUD_PORT,
    ROOT,
)
from cristopher.control_total import killswitch

GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
COLOR_CLAVE = "black"       # invisible vía -transparentcolor; NUNCA se usa para dibujar
_COLOR_CLAVE_HEX = "#000000"  # mismo color, en hex, para el lerp de _dibujar

# Grosor del degradado y nº de bandas (barato: rectángulos, no por-píxel).
GROSOR = 26
BANDAS = 10

# (brillo objetivo 0-1, período del pulso en segundos) por estado.
TARGETS = {
    "reposo": (0.35, 3.2),
    "trabajando": (0.95, 0.9),
}

# Sondeos consecutivos fallidos (a 0.5s cada uno, ~5s) antes de darse por
# vencido con el HUD y cerrarse solo — un HUD caído no debe dejar el borde azul
# pegado en pantalla para siempre.
FALLOS_MAX = 10


class OverlayError(RuntimeError):
    """El borde de pantalla no pudo arrancar."""


def _hud_vivo() -> bool:
    try:
        with socket.create_connection(("127.0.0.1", HUD_PORT), timeout=0.5):
            return True
    except OSError:
        return False


def _dpi_aware() -> None:
    """Per-monitor DPI awareness: sin esto, en pantallas con distinto escalado las
    coordenadas de Tk pueden no coincidir con los píxeles físicos."""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        pass  # versión de Windows sin shcore, o ya fijado por manifiesto: no crítico


def _rect_virtual() -> "tuple[int, int, int, int]":
    """Unión de TODOS los monitores (x, y, ancho, alto), vía GetSystemMetrics."""
    user32 = ctypes.windll.user32
    x = user32.GetSystemMetrics(76)   # SM_XVIRTUALSCREEN
    y = user32.GetSystemMetrics(77)   # SM_YVIRTUALSCREEN
    w = user32.GetSystemMetrics(78)   # SM_CXVIRTUALSCREEN
    h = user32.GetSystemMetrics(79)   # SM_CYVIRTUALSCREEN
    return x, y, w, h


SWP_NOMOVE, SWP_NOSIZE, SWP_NOZORDER, SWP_FRAMECHANGED = 0x0002, 0x0001, 0x0004, 0x0020


def _hacer_click_through(root: tk.Tk) -> None:
    """Verificado en la práctica (spike propio, con confirmación visual y de clic
    del usuario) — dos gotchas reales de esta receta, no hipotéticos:
    1. `root.winfo_id()` en un Toplevel `overrideredirect` NO devuelve el HWND de
       primer nivel real que gestiona Windows, sino el de un widget interno —
       `GetParent()` sí da el correcto (confirmado con los dos valores distintos).
    2. Tras `SetWindowLongW`, Windows necesita `SetWindowPos(..., SWP_FRAMECHANGED)`
       para aplicar de verdad el nuevo estilo extendido — sin esto la ventana se
       queda invisible pese a que la llamada "tiene éxito" sin error."""
    user32 = ctypes.windll.user32
    user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.GetWindowLongW.restype = ctypes.c_long
    user32.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_long]
    user32.SetWindowLongW.restype = ctypes.c_long
    user32.GetParent.argtypes = [wintypes.HWND]
    user32.GetParent.restype = wintypes.HWND
    user32.SetWindowPos.argtypes = [
        wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
        ctypes.c_int, ctypes.c_int, ctypes.c_uint,
    ]

    hwnd_widget = wintypes.HWND(root.winfo_id())
    hwnd_padre = user32.GetParent(hwnd_widget)
    hwnd = wintypes.HWND(hwnd_padre) if hwnd_padre else hwnd_widget

    estilos = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    user32.SetWindowLongW(hwnd, GWL_EXSTYLE, estilos | WS_EX_LAYERED | WS_EX_TRANSPARENT)
    user32.SetWindowPos(
        hwnd, None, 0, 0, 0, 0,
        SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_FRAMECHANGED,
    )


def _lerp_color(c1: str, c2: str, t: float) -> str:
    t = max(0.0, min(1.0, t))
    r1, g1, b1 = int(c1[1:3], 16), int(c1[3:5], 16), int(c1[5:7], 16)
    r2, g2, b2 = int(c2[1:3], 16), int(c2[3:5], 16), int(c2[5:7], 16)
    r = round(r1 + (r2 - r1) * t)
    g = round(g1 + (g2 - g1) * t)
    b = round(b1 + (b2 - b1) * t)
    return f"#{r:02x}{g:02x}{b:02x}"


def _sondear_estado(overlay: "Overlay") -> None:
    """Hilo de fondo: GET /estado cada 0.5s. Nunca bloquea el bucle de animación
    de Tk con I/O de red — solo escribe en `overlay._remoto` bajo lock."""
    fallos = 0
    while not overlay._detenido:
        try:
            with urllib.request.urlopen(
                f"http://localhost:{HUD_PORT}/estado", timeout=1.0
            ) as resp:
                data = json.loads(resp.read())
            with overlay._remoto_lock:
                overlay._remoto["control_total"] = bool(data.get("control_total", False))
                overlay._remoto["actividad"] = data.get("control_total_actividad", "reposo")
                overlay._remoto["hud_caido"] = False
            fallos = 0
        except Exception:
            fallos += 1
            if fallos >= FALLOS_MAX:
                with overlay._remoto_lock:
                    overlay._remoto["hud_caido"] = True
        time.sleep(0.5)


class Overlay:
    def __init__(self) -> None:
        if not _hud_vivo():
            raise OverlayError(
                f"El HUD no está corriendo en el puerto {HUD_PORT}. "
                "Arráncalo primero: python -m cristopher"
            )
        _dpi_aware()
        self.x, self.y, self.w, self.h = _rect_virtual()

        self.root = tk.Tk()
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.configure(bg=COLOR_CLAVE)
        self.root.wm_attributes("-transparentcolor", COLOR_CLAVE)
        self.root.geometry(f"{self.w}x{self.h}+{self.x}+{self.y}")

        self.canvas = tk.Canvas(
            self.root, width=self.w, height=self.h,
            bg=COLOR_CLAVE, highlightthickness=0,
        )
        self.canvas.pack(fill="both", expand=True)

        self.root.update_idletasks()
        _hacer_click_through(self.root)

        self.cur = TARGETS["reposo"][0]
        self.target = TARGETS["reposo"][0]
        self._detenido = False

        # Kill-switch LOCAL a este proceso (Fase 4, hueco real: el chequeo de
        # killswitch.disparado() en _tick() ya existía, pero nada arrancaba el
        # listener aquí, así que nunca podía dispararse por sí solo — el cierre
        # dependía por completo del roundtrip HTTP de /estado. Con esto, Ctrl+F3
        # cierra el borde de inmediato (siguiente tick, ~16ms) además de por el
        # camino lento del polling si por lo que sea este listener no arrancara.
        killswitch.iniciar_killswitch()

        # Arranca optimista: recién lanzado por activar_control_total, el modo YA
        # está activo aunque el primer sondeo tarde su medio segundo en confirmarlo.
        self._remoto_lock = threading.Lock()
        self._remoto = {"control_total": True, "actividad": "reposo", "hud_caido": False}
        threading.Thread(target=_sondear_estado, args=(self,), daemon=True).start()

        self._dibujar()
        self._tick()

    def _dibujar(self) -> None:
        self.canvas.delete("all")
        for i in range(BANDAS):
            t = i / (BANDAS - 1)
            color = _lerp_color(CONTROL_TOTAL_OVERLAY_COLOR, _COLOR_CLAVE_HEX, t)
            grosor_banda = GROSOR * (1 - t) * self.cur
            if grosor_banda < 0.5:
                continue
            self.canvas.create_rectangle(
                0, 0, self.w, grosor_banda, outline="", fill=color,
            )
            self.canvas.create_rectangle(
                0, self.h - grosor_banda, self.w, self.h, outline="", fill=color,
            )
            self.canvas.create_rectangle(
                0, 0, grosor_banda, self.h, outline="", fill=color,
            )
            self.canvas.create_rectangle(
                self.w - grosor_banda, 0, self.w, self.h, outline="", fill=color,
            )

    def _tick(self) -> None:
        if self._detenido:
            return
        if killswitch.disparado():
            self.ocultar()
            return

        with self._remoto_lock:
            remoto = dict(self._remoto)
        if remoto["hud_caido"] or not remoto["control_total"]:
            self.ocultar()
            return

        ahora = time.monotonic()
        brillo_obj, _periodo = TARGETS[
            "trabajando" if remoto["actividad"] == "trabajando" else "reposo"
        ]
        self.target = brillo_obj

        # Mismo patrón de interpolación que TARGETS/app.js (cur += (target-cur)*f),
        # con un pulso senoidal encima para que "respire" en vez de quedarse fijo.
        self.cur += (self.target - self.cur) * 0.05
        pulso = 0.15 * (0.5 + 0.5 * math.sin(ahora * 2.5))
        self.cur = max(0.0, min(1.0, self.cur + pulso - 0.075))

        self._dibujar()
        self.root.after(16, self._tick)

    def ocultar(self) -> None:
        self._detenido = True
        try:
            self.root.destroy()
        except Exception:
            pass

    def mainloop(self) -> None:
        self.root.mainloop()


def mostrar() -> Overlay:
    return Overlay()


# --- PID-guard + lanzamiento como proceso propio (Fase 4) -------------------------
def _pid_guardado() -> "int | None":
    try:
        return int(CONTROL_TOTAL_OVERLAY_PID.read_text(encoding="utf-8").strip())
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
        CONTROL_TOTAL_OVERLAY_PID.parent.mkdir(parents=True, exist_ok=True)
        CONTROL_TOTAL_OVERLAY_PID.write_text(str(pid), encoding="utf-8")
    except OSError:
        pass


def _borrar_pid() -> None:
    try:
        CONTROL_TOTAL_OVERLAY_PID.unlink(missing_ok=True)
    except OSError:
        pass


def _pythonw() -> str:
    exe = Path(sys.executable)
    pw = exe.with_name("pythonw.exe")
    return str(pw if pw.exists() else exe)


def lanzar_proceso() -> None:
    """Lanza el borde como proceso propio y desacoplado (mismo patrón que
    `widget_flotante.lanzar_proceso`/`escucha.py::_lanzar_hud`). PID-guard: si ya
    hay un borde vivo, no abre uno segundo."""
    pid = _pid_guardado()
    if pid is not None and _proceso_vivo(pid):
        return
    creationflags = 0
    if os.name == "nt":
        # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
        creationflags = 0x00000008 | 0x00000200
    proc = subprocess.Popen(
        [_pythonw(), "-m", "cristopher.hud.control_overlay"],
        cwd=str(ROOT), creationflags=creationflags,
    )
    _guardar_pid(proc.pid)


def main() -> int:
    try:
        overlay = mostrar()
    except OverlayError as exc:
        print(f"[control_overlay] {exc}")
        return 1
    try:
        overlay.mainloop()
    except KeyboardInterrupt:
        overlay.ocultar()
    finally:
        _borrar_pid()
    return 0


if __name__ == "__main__":
    sys.exit(main())
