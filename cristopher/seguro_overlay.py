"""Modo Seguro — overlay de bloqueo (Fase 1). Ventana `pywebview` fullscreen,
sin marco, siempre encima, cubriendo TODOS los monitores, con cara enfadada + campo
de contraseña.

Vive en su PROPIO proceso, mismo motivo que `hud/widget_flotante.py` y
`hud/control_overlay.py`: `webview.start()` bloquea el hilo que lo llama. A
diferencia de esos dos, corre en paralelo a `LockWorkStation()` — Windows ya cambia
al escritorio seguro de Winlogon, donde este proceso NO es visible ni interactivo
(esperado: el overlay es la personalidad, la seguridad real la da Windows). Cuando el
usuario vuelve al escritorio normal tras su contraseña de Windows, este overlay sigue
ahí encima pidiendo la contraseña PROPIA de CRISTOPHER (data/seguro.json), que es la
que de verdad mueve la FSM de BLOQUEADO a GRACIA.

Como corre en otro proceso, no puede llamar a funciones de `cristopher.seguro`
directamente (ese estado vive en el proceso del HUD) — habla con él por HTTP local
(`POST /seguro/desbloquear`), mismo idiom que `control_overlay.py::_sondear_estado`
habla con `/estado`.

Uso:
  python -m cristopher.seguro_overlay
"""

from __future__ import annotations

import ctypes
import json
import logging
import os
import socket
import subprocess
import sys
import threading
import time
import traceback
import urllib.request
from pathlib import Path

from cristopher.config import (
    HUD_PORT,
    ROOT,
    SEGURO_OVERLAY_LOG,
    SEGURO_OVERLAY_PID,
    SEGURO_WEBVIEW_CACHE,
)

TITULO = "CRISTOPHER — MODO SEGURO"

# Sondeos consecutivos fallidos (a 0.5s cada uno, ~5s) antes de darse por vencido con
# el HUD y cerrarse solo — mismo umbral que widget_flotante.py/control_overlay.py. Si
# el HUD ha caído, Windows ya tiene la sesión bloqueada de verdad (§3.6 del diseño):
# que el overlay se cierre solo en ese caso es un pase, no un fallo de seguridad.
FALLOS_MAX = 10


class OverlayError(RuntimeError):
    """El overlay no pudo arrancar."""


class _WebView2Fallido(RuntimeError):
    """La ventana SÍ se crea pero WebView2 nunca inicializa (evento interno
    de pywebview `on_webview_ready` con `IsSuccess=False`, típicamente
    `E_ABORT`/0x80004004 en `CreateCoreWebView2ControllerAsync`). pywebview
    se limita a loguearlo y seguir — no re-lanza, no cierra la ventana — así
    que sin esto quedaría una ventana en blanco, siempre-encima, atascada:
    lo único que el usuario ve es un "crash". Visto en vivo: intermitente,
    el mismo comando repetido sin cambios arranca bien la siguiente vez."""


class _VigiaFalloWebView2(logging.Handler):
    """Escucha el logger propio de pywebview ('pywebview') para detectar el
    fallo de inicialización que la librería no expone de otra forma (ni
    excepción, ni callback, ni valor de retorno)."""

    def __init__(self, evento: threading.Event) -> None:
        super().__init__()
        self._evento = evento

    def emit(self, record: logging.LogRecord) -> None:
        try:
            if "WebView2 initialization failed" in record.getMessage():
                self._evento.set()
        except Exception:
            pass


def _vigilar_fallo_webview2(window, evento: threading.Event) -> None:
    """Si el fallo llega, fuerza el cierre de la ventana en blanco para que
    `webview.start()` devuelva el control y `lanzar()` pueda convertirlo en
    una excepción real (`main()` ya sabe reintentar ante eso)."""
    if evento.wait(timeout=8.0):
        try:
            window.destroy()
        except Exception:
            pass


def _hud_vivo() -> bool:
    try:
        with socket.create_connection(("127.0.0.1", HUD_PORT), timeout=0.5):
            return True
    except OSError:
        return False


def _rect_virtual() -> "tuple[int, int, int, int]":
    """Unión de TODOS los monitores (x, y, ancho, alto), vía GetSystemMetrics.
    Duplicado a propósito de hud/control_overlay.py::_rect_virtual — el repo prefiere
    duplicar helpers de 2-3 líneas entre módulos de features distintas antes que
    compartirlos vía un util común."""
    user32 = ctypes.windll.user32
    x = user32.GetSystemMetrics(76)   # SM_XVIRTUALSCREEN
    y = user32.GetSystemMetrics(77)   # SM_YVIRTUALSCREEN
    w = user32.GetSystemMetrics(78)   # SM_CXVIRTUALSCREEN
    h = user32.GetSystemMetrics(79)   # SM_CYVIRTUALSCREEN
    return x, y, w, h


def _html() -> str:
    return """
<!doctype html>
<html><head><meta charset="utf-8">
<style>
  html, body { margin:0; padding:0; width:100%; height:100%; background:#1a0505;
               color:#f2e6e6; font-family: 'Segoe UI', sans-serif;
               display:flex; align-items:center; justify-content:center;
               flex-direction:column; user-select:none; }
  .cara { font-size:96px; margin-bottom:16px; }
  h1 { font-size:22px; font-weight:600; margin:0 0 6px 0; }
  p { opacity:0.75; margin:0 0 24px 0; font-size:14px; }
  input { font-size:18px; padding:10px 14px; border-radius:8px; border:1px solid #663;
          background:#2a0d0d; color:#fff; width:280px; text-align:center; }
  input:focus { outline: 2px solid #c33; }
  #msg { min-height:20px; margin-top:14px; font-size:13px; color:#ff8080; }
</style></head>
<body>
  <div class="cara">&#128544;</div>
  <h1>MODO SEGURO — CRISTOPHER</h1>
  <p>Se detectó una cara no autorizada mirando la pantalla. Introduce la contraseña.</p>
  <input id="pw" type="password" autofocus placeholder="Contraseña">
  <div id="msg"></div>
  <script>
    const pw = document.getElementById('pw');
    const msg = document.getElementById('msg');
    pw.focus();
    async function intentar() {
      const valor = pw.value;
      pw.value = '';
      msg.textContent = 'Comprobando...';
      try {
        const r = await window.pywebview.api.enviar_password(valor);
        if (r && r.ok) { msg.textContent = 'Desbloqueado.'; }
        else { msg.textContent = (r && r.mensaje) || 'Contraseña incorrecta.'; pw.focus(); }
      } catch (e) { msg.textContent = 'No pude hablar con CRISTOPHER: ' + e; pw.focus(); }
    }
    pw.addEventListener('keydown', (e) => { if (e.key === 'Enter') intentar(); });
    setInterval(() => pw.focus(), 1500);
  </script>
</body></html>
"""


class _Api:
    """Puente JS→Python: el campo de contraseña llama aquí, que habla por HTTP con
    el proceso del HUD (self-locking en cristopher.seguro, mismo criterio que
    /concentracion o /modo_code)."""

    def __init__(self) -> None:
        self.window = None

    def enviar_password(self, password: str) -> dict:
        try:
            body = json.dumps({"password": password}).encode("utf-8")
            req = urllib.request.Request(
                f"http://127.0.0.1:{HUD_PORT}/seguro/desbloquear",
                data=body, method="POST",
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=3.0) as resp:
                data = json.loads(resp.read())
        except Exception as exc:
            return {"ok": False, "mensaje": f"No pude hablar con CRISTOPHER: {exc}"}
        if data.get("ok") and self.window is not None:
            try:
                self.window.destroy()
            except Exception:
                pass
        return data


def _robar_foco() -> None:
    """Sin precedente exacto en el repo (ni widget_flotante.py ni control_overlay.py
    fuerzan el foco). Windows puede negar SetForegroundWindow según qué proceso
    tuviera el foco justo antes ("foreground lock") — si en la prueba real esto no
    funciona, la vía de escape es AttachThreadInput al hilo de la ventana en primer
    plano antes de llamar; no se implementa de entrada porque esta versión más simple
    puede bastar."""
    time.sleep(0.4)
    user32 = ctypes.windll.user32
    for _ in range(10):
        hwnd = user32.FindWindowW(None, TITULO)
        if hwnd:
            try:
                user32.SetForegroundWindow(hwnd)
            except Exception:
                pass
            return
        time.sleep(0.3)


def _sondear_cierre(window, detenido: threading.Event) -> None:
    """Cierra el overlay cuando: (a) la contraseña correcta ya llevó la FSM a GRACIA
    (fase != BLOQUEADO), o (b) el modo se desactivó remotamente (activo=False). Si el
    HUD cae, se cierra tras FALLOS_MAX fallos — Windows ya tiene la sesión bloqueada
    de verdad, es un pase (ver docstring del módulo)."""
    fallos = 0
    while not detenido.is_set():
        time.sleep(0.5)
        if detenido.is_set():
            return
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{HUD_PORT}/seguro/estado", timeout=1.0
            ) as resp:
                data = json.loads(resp.read())
            fallos = 0
            if data.get("fase") != "BLOQUEADO" or not data.get("activo"):
                break
        except Exception:
            fallos += 1
            if fallos >= FALLOS_MAX:
                break
    try:
        window.destroy()
    except Exception:
        pass


def lanzar() -> None:
    """BLOQUEA (`webview.start()`) — llamar solo desde un proceso propio."""
    if not _hud_vivo():
        raise OverlayError(
            f"El HUD no está corriendo en el puerto {HUD_PORT}. "
            "Arráncalo primero: python -m cristopher"
        )
    import webview

    x, y, w, h = _rect_virtual()
    api = _Api()
    window = webview.create_window(
        TITULO, html=_html(),
        width=w, height=h, x=x, y=y,
        frameless=True, on_top=True, js_api=api,
    )
    api.window = window

    detenido = threading.Event()

    def _on_closed():
        try:
            detenido.set()
            _borrar_pid()
        except Exception:
            pass

    window.events.closed += _on_closed

    fallo_webview2 = threading.Event()
    vigia = _VigiaFalloWebView2(fallo_webview2)
    logging.getLogger("pywebview").addHandler(vigia)

    threading.Thread(target=_robar_foco, daemon=True).start()
    threading.Thread(target=_sondear_cierre, args=(window, detenido), daemon=True).start()
    threading.Thread(
        target=_vigilar_fallo_webview2, args=(window, fallo_webview2), daemon=True
    ).start()

    try:
        # Causa raíz real del E_ABORT en CreateCoreWebView2ControllerAsync
        # (visto en vivo, reproducido en consola): con private_mode=True (el
        # default de pywebview) el user-data-folder de WebView2 sale de
        # `tempfile.TemporaryDirectory().name` sin guardar el objeto en
        # ninguna variable — su refcount cae a 0 en la misma línea y CPython
        # lo borra del disco casi en el acto, ANTES de que WebView2 termine
        # de inicializar sobre esa carpeta. storage_path fijo evita esa rama
        # por completo (mismo cache_dir estable en cada arranque; no hace
        # falta persistencia real, es una pantalla de contraseña estática).
        SEGURO_WEBVIEW_CACHE.mkdir(parents=True, exist_ok=True)
        webview.start(storage_path=str(SEGURO_WEBVIEW_CACHE))
    finally:
        logging.getLogger("pywebview").removeHandler(vigia)

    if fallo_webview2.is_set():
        raise _WebView2Fallido("WebView2 no inicializó (ver seguro_overlay.log)")


# --- PID-guard: no abrir un segundo overlay si ya hay uno vivo -------------------
def _pid_guardado() -> "int | None":
    try:
        return int(SEGURO_OVERLAY_PID.read_text(encoding="utf-8").strip())
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
        SEGURO_OVERLAY_PID.parent.mkdir(parents=True, exist_ok=True)
        SEGURO_OVERLAY_PID.write_text(str(pid), encoding="utf-8")
    except OSError:
        pass


def _borrar_pid() -> None:
    try:
        SEGURO_OVERLAY_PID.unlink(missing_ok=True)
    except OSError:
        pass


def _pythonw() -> str:
    exe = Path(sys.executable)
    pw = exe.with_name("pythonw.exe")
    return str(pw if pw.exists() else exe)


def lanzar_proceso() -> None:
    """Lanza el overlay como proceso propio y desacoplado (mismo patrón que
    widget_flotante.lanzar_proceso). PID-guard: si ya hay uno vivo (dos caras
    seguidas disparando antes de que el primero se cierre), no abre un segundo."""
    pid = _pid_guardado()
    if pid is not None and _proceso_vivo(pid):
        return
    creationflags = 0
    if os.name == "nt":
        # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
        creationflags = 0x00000008 | 0x00000200
    proc = subprocess.Popen(
        [_pythonw(), "-m", "cristopher.seguro_overlay"],
        cwd=str(ROOT), creationflags=creationflags,
    )
    _guardar_pid(proc.pid)


def _log_crash(mensaje: str) -> None:
    """Bajo pythonw, sys.stdout/sys.stderr son `None` (bpo-13120): un
    traceback no capturado revienta al intentar escribirse y el proceso
    muere sin dejar rastro de por qué "crasheó". Todo fallo real se escribe
    aquí en vez de a consola."""
    try:
        SEGURO_OVERLAY_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(SEGURO_OVERLAY_LOG, "a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {mensaje}\n")
    except OSError:
        pass


_ENV_REINTENTO = "CRISTOPHER_SEGURO_OVERLAY_REINTENTO"


def _reintentar_en_proceso_nuevo() -> None:
    """Relanza el overlay en un proceso completamente nuevo en vez de volver
    a llamar a `lanzar()` en este mismo proceso: `webview.start()` ya dejó
    WinForms/el CLR en un estado a medio inicializar tras el fallo, así que
    reintentar in-process es terreno movedizo. Un proceso limpio no arrastra
    ese estado. Marca `_ENV_REINTENTO` para que ese proceso, si también
    falla, no vuelva a reintentar (como mucho dos intentos en total)."""
    creationflags = 0
    if os.name == "nt":
        creationflags = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    env = dict(os.environ)
    env[_ENV_REINTENTO] = "1"
    proc = subprocess.Popen(
        [_pythonw(), "-m", "cristopher.seguro_overlay"],
        cwd=str(ROOT), creationflags=creationflags, env=env,
    )
    _guardar_pid(proc.pid)  # el pid-guard pasa a apuntar al proceso nuevo


def main() -> int:
    """El fallo real visto en vivo (WebView2 `E_ABORT` en
    `CreateCoreWebView2ControllerAsync`, dentro de `webview.start()`) salió
    intermitente, no determinista — el mismo comando, lanzado otra vez sin
    cambiar nada, arrancó bien. De ahí el único reintento (en proceso nuevo,
    ver `_reintentar_en_proceso_nuevo`) antes de darse por vencido."""
    ya_reintentado = os.environ.get(_ENV_REINTENTO) == "1"
    try:
        lanzar()
    except OverlayError as exc:
        _log_crash(f"HUD no disponible: {exc}")
        _borrar_pid()
        return 1
    except Exception:
        _log_crash(f"fallo (reintento={ya_reintentado}):\n{traceback.format_exc()}")
        if ya_reintentado:
            _borrar_pid()
            return 1
        time.sleep(1.0)
        try:
            _reintentar_en_proceso_nuevo()
        except Exception:
            _log_crash(f"no pude relanzar el reintento:\n{traceback.format_exc()}")
            _borrar_pid()
        return 1
    else:
        _borrar_pid()
        return 0


if __name__ == "__main__":
    sys.exit(main())
