"""Atajo global de emergencia del Modo Control Total — hombre muerto real.

Mismo patrón que `escucha.py::_iniciar_atajo_cortar` (pynput.keyboard.GlobalHotKeys,
degradado con log.warning si falta pynput o falla el registro) + singleton-con-lock
como `sesiones.py::get_gestor_sesiones` (double-checked locking, atexit).

El flag (`threading.Event`) es lo único que importa desde fuera: `driver.py` lo
consulta antes de CADA primitiva, no solo al principio de un bucle exterior — así el
atajo detiene una acción en curso sin depender de que el bucle esté en un punto
cómodo para parar.

`hud/__main__.py` ya lo registra al arrancar el sistema vivo (Ctrl+F3 activo mientras
CRISTOPHER esté corriendo). Este módulo TAMBIÉN es ejecutable en solitario para que
quede escuchando desde el arranque de sesión, sin depender de que nada más esté
corriendo — mismo patrón de autostart que `escucha.py` (carpeta Inicio de Windows):

  python -m cristopher.control_total.killswitch             # escucha, sin instalar nada
  python -m cristopher.control_total.killswitch --instalar   # arrancar solo al iniciar sesión
  python -m cristopher.control_total.killswitch --desinstalar
"""

from __future__ import annotations

import atexit
import os
import subprocess
import sys
import threading
from pathlib import Path

from cristopher.config import CONTROL_TOTAL_HOTKEY
from cristopher.control_total import log

_ATAJO_INICIO = "CRISTOPHER Kill-Switch.lnk"  # nombre del acceso directo en la carpeta Inicio

_DETENIDO = threading.Event()

_listener = None
_listener_lock = threading.Lock()


def disparado() -> bool:
    """True si el atajo de emergencia saltó y aún no se ha reseteado."""
    return _DETENIDO.is_set()


def reset() -> None:
    """Limpia el flag para arrancar una sesión de control nueva."""
    _DETENIDO.clear()


def _on_disparo() -> None:
    _DETENIDO.set()
    log.warning("Kill-switch disparado (%s) — Modo Control Total detenido.", CONTROL_TOTAL_HOTKEY)


def iniciar_killswitch() -> None:
    """Registra el atajo global una sola vez por proceso (idempotente).

    Degrada con elegancia si falta pynput o si el registro falla: nunca rompe el
    arranque del resto del sistema (mismo criterio que _iniciar_atajo_cortar).
    """
    global _listener
    if _listener is not None:
        return
    with _listener_lock:
        if _listener is not None:
            return
        try:
            from pynput import keyboard
        except ImportError:
            log.warning(
                "Falta pynput: sin kill-switch (%s) para Modo Control Total.",
                CONTROL_TOTAL_HOTKEY,
            )
            return
        try:
            hk = keyboard.GlobalHotKeys({CONTROL_TOTAL_HOTKEY: _on_disparo})
            hk.daemon = True
            hk.start()
            _listener = hk
            log.info("Kill-switch activo: %s", CONTROL_TOTAL_HOTKEY)
        except Exception as exc:
            log.warning("No pude registrar el kill-switch %s: %s", CONTROL_TOTAL_HOTKEY, exc)


def parar_killswitch() -> None:
    """Detiene el listener del atajo (teardown/tests, o desactivación del modo)."""
    global _listener
    with _listener_lock:
        if _listener is not None:
            try:
                _listener.stop()
            except Exception as exc:
                log.warning("No pude detener el kill-switch limpio: %s", exc)
            _listener = None


atexit.register(parar_killswitch)


# --- Autostart en solitario (carpeta Inicio de Windows) -----------------------
# Mismo mecanismo que escucha.py::instalar_autostart/_ruta_inicio/_pythonw (duplicado
# aquí en vez de importado: son helpers de 2-3 líneas, no justifican un módulo de
# utils compartido para dos usos).
def _ruta_inicio() -> Path:
    """Carpeta shell:startup del usuario."""
    appdata = os.environ.get("APPDATA", "")
    return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


def _pythonw() -> str:
    """Ruta a pythonw.exe (Python sin consola) del mismo entorno; cae a python.exe."""
    exe = Path(sys.executable)
    pw = exe.with_name("pythonw.exe")
    return str(pw if pw.exists() else exe)


def instalar_autostart() -> None:
    """Crea el acceso directo que arranca el kill-switch EN SOLITARIO (sin el resto
    de CRISTOPHER) al iniciar sesión — Ctrl+F3 queda vivo desde el arranque de Windows."""
    if os.name != "nt":
        print("[killswitch] El autostart por carpeta Inicio es solo para Windows.")
        return
    from cristopher.config import ROOT

    destino = _ruta_inicio() / _ATAJO_INICIO
    ps = (
        "$s = (New-Object -ComObject WScript.Shell).CreateShortcut('{lnk}');"
        "$s.TargetPath = '{exe}';"
        "$s.Arguments = '-m cristopher.control_total.killswitch';"
        "$s.WorkingDirectory = '{cwd}';"
        "$s.Save()"
    ).format(lnk=str(destino), exe=_pythonw(), cwd=str(ROOT))
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], check=True)
    print(f"[killswitch] Instalado. Ctrl+F3 estará activo desde el arranque de sesión:\n  {destino}")


def desinstalar_autostart() -> None:
    """Quita el acceso directo de la carpeta Inicio."""
    destino = _ruta_inicio() / _ATAJO_INICIO
    if destino.exists():
        destino.unlink()
        print(f"[killswitch] Autostart quitado: {destino}")
    else:
        print("[killswitch] No había autostart instalado.")


def main() -> int:
    arg = sys.argv[1] if len(sys.argv) > 1 else ""
    if arg == "--instalar":
        instalar_autostart()
        return 0
    if arg == "--desinstalar":
        desinstalar_autostart()
        return 0

    reset()
    iniciar_killswitch()
    log.info("Kill-switch en solitario arrancado (sin el resto de CRISTOPHER).")
    try:
        threading.Event().wait()  # bloquea para siempre; GlobalHotKeys vive en su propio hilo daemon
    except KeyboardInterrupt:
        log.info("Kill-switch en solitario detenido (Ctrl+C).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
