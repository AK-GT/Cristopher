"""Escucha ambiental de CRISTOPHER — despierta el sistema con 2 palmadas.

Proceso ligero e independiente: NO carga Gemini, Playwright ni el HUD; solo escucha
el micrófono y, al detectar dos palmadas seguidas, reproduce la canción de arranque y
lanza el sistema vivo (`python -m cristopher`) como subproceso.

La detección es puramente por energía del audio (sin modelos de voz): casi gratis y sin
gastar CPU en reposo. Los umbrales se calibran por variables de entorno (ver config.py).

Uso:
  python -m cristopher.escucha              # escuchar (2 palmadas para despertar)
  python -m cristopher.escucha --instalar   # arrancar solo al iniciar sesión (carpeta Inicio)
  python -m cristopher.escucha --desinstalar # quitar el arranque automático
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import numpy as np

from cristopher.config import (
    AUDIO_DIR,
    CANCION_ARRANQUE,
    HUD_PORT,
    PALMADA_COOLDOWN,
    PALMADA_FACTOR,
    PALMADA_GAP_MAX,
    PALMADA_GAP_MIN,
    PALMADA_UMBRAL,
    ROOT,
)

SR = 16000                 # tasa de muestreo del micrófono
BLOQUE = int(SR * 0.03)    # ~30 ms por bloque de análisis
_ATAJO = "CRISTOPHER Escucha.lnk"   # nombre del acceso directo en la carpeta Inicio


class EscuchaError(RuntimeError):
    """Fallo de audio que impide escuchar (p. ej. sin micrófono)."""


# --- Reproducción de la canción ----------------------------------------------
def reproducir_cancion(path: Path = CANCION_ARRANQUE) -> None:
    """Reproduce el MP3 de arranque sin abrir ventana. Falla con elegancia."""
    if not path.exists():
        print(
            f"[escucha] No suena la canción: falta {path}. "
            f"Coloca tu 'back_in_black.mp3' en {AUDIO_DIR} para oírla."
        )
        return

    def _play():
        try:
            from playsound import playsound
        except ImportError:
            print("[escucha] Falta 'playsound' (pip install playsound==1.2.2); sin música.")
            return
        try:
            playsound(str(path))
        except Exception as exc:  # nunca romper el arranque por la música
            print(f"[escucha] No pude reproducir la canción: {exc}")

    threading.Thread(target=_play, daemon=True).start()


# --- Arranque del HUD --------------------------------------------------------
def _hud_vivo() -> bool:
    """True si ya hay algo escuchando en el puerto del HUD (evita doble arranque)."""
    try:
        with socket.create_connection(("127.0.0.1", HUD_PORT), timeout=0.3):
            return True
    except OSError:
        return False


def _pythonw() -> str:
    """Ruta a pythonw.exe (Python sin consola) del mismo entorno; cae a python.exe."""
    exe = Path(sys.executable)
    pw = exe.with_name("pythonw.exe")
    return str(pw if pw.exists() else exe)


def _lanzar_hud() -> None:
    """Arranca el sistema vivo (HUD) como subproceso desacoplado del escuchador."""
    creationflags = 0
    if os.name == "nt":
        # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP: el HUD sobrevive aunque se cierre
        # el escuchador, y sin ventana de consola (arrancamos con pythonw).
        creationflags = 0x00000008 | 0x00000200
    subprocess.Popen(
        [_pythonw(), "-m", "cristopher"],
        cwd=str(ROOT),
        creationflags=creationflags,
    )


def _disparar() -> None:
    """Dos palmadas detectadas: canción + HUD (si no estaba ya abierto)."""
    if _hud_vivo():
        print("[escucha] El HUD ya está abierto; ignoro las palmadas.")
        return
    print("[escucha] ¡2 palmadas! Despertando a CRISTOPHER…")
    reproducir_cancion()
    _lanzar_hud()


# --- Bucle de escucha --------------------------------------------------------
def escuchar() -> None:
    """Escucha el micrófono en bucle y dispara al detectar dos palmadas seguidas."""
    try:
        import sounddevice as sd
    except ImportError as exc:
        raise EscuchaError("Falta sounddevice: pip install sounddevice") from exc

    AUDIO_DIR.mkdir(parents=True, exist_ok=True)  # dónde dejar back_in_black.mp3
    print("CRISTOPHER — escuchando… (2 palmadas para despertar; Ctrl+C para salir)")

    suelo = 0.01           # suelo de ruido adaptativo
    activo_prev = False    # ¿el bloque anterior superaba el umbral? (para el flanco)
    ultimo_onset = -1e9    # instante de la última palmada aislada (centinela: nunca empareja)
    hasta = 0.0            # fin de la guarda (cooldown) tras disparar

    try:
        with sd.InputStream(samplerate=SR, channels=1, dtype="float32",
                            blocksize=BLOQUE) as stream:
            while True:
                data, _ = stream.read(BLOQUE)
                pico = float(np.max(np.abs(data)))
                umbral = max(PALMADA_UMBRAL, suelo * PALMADA_FACTOR)
                ahora = time.monotonic()

                # Flanco de subida = onset (una posible palmada).
                if pico >= umbral and not activo_prev:
                    if ahora >= hasta:  # fuera de la guarda
                        dt = ahora - ultimo_onset
                        if PALMADA_GAP_MIN <= dt <= PALMADA_GAP_MAX:
                            _disparar()
                            hasta = ahora + PALMADA_COOLDOWN
                            ultimo_onset = 0.0
                        else:
                            ultimo_onset = ahora  # primera palmada (o demasiado tarde)
                    activo_prev = True
                elif pico < umbral:
                    activo_prev = False
                    suelo = 0.95 * suelo + 0.05 * pico  # solo se adapta en silencio
    except EscuchaError:
        raise
    except KeyboardInterrupt:
        print("\n[escucha] Detenido.")
    except Exception as exc:
        raise EscuchaError(f"No pude escuchar del micrófono: {exc}") from exc


# --- Autostart (carpeta Inicio de Windows) -----------------------------------
def _ruta_inicio() -> Path:
    """Carpeta shell:startup del usuario."""
    appdata = os.environ.get("APPDATA", "")
    return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


def instalar_autostart() -> None:
    """Crea el acceso directo que arranca el escuchador al iniciar sesión."""
    if os.name != "nt":
        print("[escucha] El autostart por carpeta Inicio es solo para Windows.")
        return
    destino = _ruta_inicio() / _ATAJO
    ps = (
        "$s = (New-Object -ComObject WScript.Shell).CreateShortcut('{lnk}');"
        "$s.TargetPath = '{exe}';"
        "$s.Arguments = '-m cristopher.escucha';"
        "$s.WorkingDirectory = '{cwd}';"
        "$s.Save()"
    ).format(lnk=str(destino), exe=_pythonw(), cwd=str(ROOT))
    subprocess.run(
        ["powershell", "-NoProfile", "-Command", ps],
        check=True,
    )
    print(f"[escucha] Instalado. Arrancará solo al iniciar sesión:\n  {destino}")


def desinstalar_autostart() -> None:
    """Quita el acceso directo de la carpeta Inicio."""
    destino = _ruta_inicio() / _ATAJO
    if destino.exists():
        destino.unlink()
        print(f"[escucha] Autostart quitado: {destino}")
    else:
        print("[escucha] No había autostart instalado.")


def main() -> int:
    arg = sys.argv[1] if len(sys.argv) > 1 else ""
    if arg == "--instalar":
        instalar_autostart()
        return 0
    if arg == "--desinstalar":
        desinstalar_autostart()
        return 0
    try:
        escuchar()
    except EscuchaError as exc:
        print(f"[escucha] {exc}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
