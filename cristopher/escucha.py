"""Escucha ambiental de CRISTOPHER — despierta el sistema con 2 palmadas.

Proceso ligero e independiente: NO carga Gemini ni Playwright; solo escucha el
micrófono y, al detectar dos palmadas seguidas, reproduce la canción de arranque y
lanza el sistema vivo (`python -m cristopher`) como subproceso.

La detección es puramente por energía del audio (sin modelos de voz): casi gratis y sin
gastar CPU en reposo. Los umbrales se calibran por variables de entorno (ver config.py).

En modo normal no imprime nada (el autostart lo lanza con pythonw, sin consola);
todo el log va a data/escucha.log. Para desactivar la escucha sin desinstalar el
autostart, crea el archivo data/escucha.desactivada (borrarlo la reactiva).

La "canción de arranque" suena con el motor de música propio de CRISTOPHER (VLC local,
`cristopher.musica`, Tanda A) desde el archivo en data/musica/ — se descartó abrirla en
una pestaña de YouTube: además de competir por la misma ventana de Chrome que el HUD
("se tapan"), los graves de la canción sonando por los altavoces realimentaban el
propio micrófono y disparaban nuevas "palmadas" falsas en bucle.

Uso:
  python -m cristopher.escucha               # escuchar (2 palmadas para despertar; sin consola)
  python -m cristopher.escucha --calibrar    # ver nivel/umbral en vivo, sin disparar nada
  python -m cristopher.escucha --visible     # igual que el modo normal (dispara de verdad),
                                              # pero con el nivel en vivo en la consola
  python -m cristopher.escucha --instalar    # arrancar solo al iniciar sesión (carpeta Inicio)
  python -m cristopher.escucha --desinstalar # quitar el arranque automático
"""

from __future__ import annotations

import logging
import os
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

import numpy as np

from cristopher.config import (
    DATA,
    ESCUCHA_FLAG_DESACTIVAR,
    ESCUCHA_LOG,
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
CHEQUEO_FLAG_SEGUNDOS = 1.0  # cada cuánto relee el archivo bandera dentro del bucle
_ATAJO = "CRISTOPHER Escucha.lnk"   # nombre del acceso directo en la carpeta Inicio
# Nombre (sin extensión) del archivo en data/musica/ que resuelve la canción de arranque
# (ver resolver.buscar_local: hace match por substring contra el nombre de archivo).
CANCION_ARCHIVO = "back_in_black"
CANCION_DURACION_MAX = 20   # s: corte automático de la canción de arranque
CANCION_ATAJO_CORTAR = "<ctrl>+m"   # atajo global para cortarla a mano

log = logging.getLogger("cristopher.escucha")


def _configurar_logging(consola: bool = False) -> None:
    """Logging a archivo siempre. El modo normal corre bajo pythonw (sin consola) y
    escribir en sys.stdout ahí revienta con AttributeError (bpo-13120), así que la
    consola solo se activa a petición (--visible), donde sí hay una consola real."""
    DATA.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(ESCUCHA_LOG, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    log.addHandler(handler)
    if consola:
        consola_handler = logging.StreamHandler(sys.stdout)
        consola_handler.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
        log.addHandler(consola_handler)
    log.setLevel(logging.INFO)


class EscuchaError(RuntimeError):
    """Fallo de audio que impide escuchar (p. ej. sin micrófono)."""


def _importar_sounddevice():
    try:
        import sounddevice as sd
    except ImportError as exc:
        raise EscuchaError("Falta sounddevice: pip install sounddevice") from exc
    return sd


# --- Reproducción de la canción (motor de música local, VLC) ------------------
def _detener_cancion(motivo: str) -> None:
    """Corta la canción de arranque si sigue sonando (temporizador de 20s o Ctrl+M).
    No hace nada si ya se detuvo sola o si nunca llegó a sonar."""
    try:
        from cristopher.musica import get_reproductor
        r = get_reproductor()
        if r.estado()["sonando"]:
            r.vaciar()
            log.info("Canción cortada (%s).", motivo)
    except Exception as exc:
        log.warning("No pude cortar la canción: %s", exc)


def _iniciar_atajo_cortar() -> None:
    """Registra el atajo global Ctrl+M para cortar la canción a mano, además del corte
    automático a los CANCION_DURACION_MAX segundos. Se registra una sola vez por
    proceso; si falta pynput, degrada con elegancia (solo queda el corte por tiempo)."""
    try:
        from pynput import keyboard
    except ImportError:
        log.warning("Falta pynput: sin atajo %s para cortar la canción (sigue el corte a los %ss).",
                    CANCION_ATAJO_CORTAR, CANCION_DURACION_MAX)
        return
    try:
        hk = keyboard.GlobalHotKeys({CANCION_ATAJO_CORTAR: lambda: _detener_cancion(f"atajo {CANCION_ATAJO_CORTAR}")})
        hk.daemon = True
        hk.start()
    except Exception as exc:
        log.warning("No pude registrar el atajo %s: %s", CANCION_ATAJO_CORTAR, exc)


def sonar_cancion() -> None:
    """Suena la canción de arranque con el motor de música propio de CRISTOPHER
    (`cristopher.musica`, VLC local sobre el archivo en data/musica/), y la corta sola
    a los CANCION_DURACION_MAX segundos (o antes, a mano, con Ctrl+M).

    Sin ventana ni pestaña de navegador que gestionar: nada que tapar al HUD, y el
    audio no vuelve a entrar por el micro con la fuerza de unos altavoces reproduciendo
    desde el propio Chrome (la causa del bucle de falsas "palmadas" que retriggeraba
    con la versión anterior por YouTube).
    """
    try:
        from cristopher.musica import get_reproductor
        get_reproductor().reproducir(CANCION_ARCHIVO)
        log.info("Sonando canción de arranque (%s).", CANCION_ARCHIVO)
        threading.Timer(CANCION_DURACION_MAX, _detener_cancion, args=(f"{CANCION_DURACION_MAX}s",)).start()
    except Exception as exc:  # nunca romper el arranque por la música
        log.warning("No pude sonar la canción de arranque: %s", exc)


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
    """Dos palmadas detectadas: la canción suena SIEMPRE (es la confirmación audible
    de que detectó el disparo). Si el proceso del HUD sigue vivo (p. ej. solo cerraste
    la pestaña del navegador, el servidor sigue corriendo) reabre esa pestaña; si no
    hay ningún proceso, lanza uno nuevo. Nunca duplica el proceso del HUD."""
    log.info("¡2 palmadas!")
    sonar_cancion()
    if _hud_vivo():
        log.info("El HUD ya está corriendo; reabro la pestaña del navegador.")
        try:
            webbrowser.open(f"http://localhost:{HUD_PORT}")
        except Exception as exc:
            log.warning("No pude reabrir el navegador: %s", exc)
    else:
        log.info("Despertando a CRISTOPHER…")
        _lanzar_hud()


# --- Bucle de escucha --------------------------------------------------------
def escuchar(visible: bool = False) -> None:
    """Escucha el micrófono en bucle y dispara (de verdad) al detectar dos palmadas.

    Por defecto sin salida por consola (corre bajo pythonw vía autostart): todo va
    al log. Con visible=True (flag --visible) además imprime el nivel en vivo en la
    consola, para verlo funcionar mientras se prueba — pero dispara igual de verdad
    (a diferencia de --calibrar, que nunca dispara).
    """
    if ESCUCHA_FLAG_DESACTIVAR.exists():
        log.info("Desactivado por archivo bandera (%s); no arranco.", ESCUCHA_FLAG_DESACTIVAR)
        return

    sd = _importar_sounddevice()
    _iniciar_atajo_cortar()

    log.info("Escuchando… (2 palmadas para despertar; archivo bandera para desactivar)")
    if visible:
        print("CRISTOPHER — escuchando en vivo (2 palmadas disparan de verdad; Ctrl+C para salir)")
        print(f"  Umbral: PALMADA_UMBRAL={PALMADA_UMBRAL}  PALMADA_FACTOR={PALMADA_FACTOR}\n")

    suelo = 0.01           # suelo de ruido adaptativo
    activo_prev = False    # ¿el bloque anterior superaba el umbral? (para el flanco)
    ultimo_onset = -1e9    # instante de la última palmada aislada (centinela: nunca empareja)
    hasta = 0.0            # fin de la guarda (cooldown) tras disparar
    ultimo_chequeo_flag = 0.0

    try:
        with sd.InputStream(samplerate=SR, channels=1, dtype="float32",
                            blocksize=BLOQUE) as stream:
            while True:
                data, _ = stream.read(BLOQUE)
                pico = float(np.max(np.abs(data)))
                umbral = max(PALMADA_UMBRAL, suelo * PALMADA_FACTOR)
                ahora = time.monotonic()
                marca = ""

                # Reconsulta el archivo bandera sin martillear el disco cada 30ms.
                if ahora - ultimo_chequeo_flag >= CHEQUEO_FLAG_SEGUNDOS:
                    ultimo_chequeo_flag = ahora
                    if ESCUCHA_FLAG_DESACTIVAR.exists():
                        log.info("Archivo bandera detectado; dejo de escuchar.")
                        return

                # Flanco de subida = onset (una posible palmada).
                if pico >= umbral and not activo_prev:
                    if ahora >= hasta:  # fuera de la guarda
                        dt = ahora - ultimo_onset
                        if PALMADA_GAP_MIN <= dt <= PALMADA_GAP_MAX:
                            marca = "  <<< 2 PALMADAS: DISPARO >>>"
                            _disparar()
                            hasta = ahora + PALMADA_COOLDOWN
                            ultimo_onset = 0.0
                        else:
                            marca = "  <- palmada"
                            ultimo_onset = ahora  # primera palmada (o demasiado tarde)
                    activo_prev = True
                elif pico < umbral:
                    activo_prev = False
                    suelo = 0.95 * suelo + 0.05 * pico  # solo se adapta en silencio

                if visible:
                    db = 20 * np.log10(max(pico, 1e-6))
                    linea = f"nivel={pico:6.3f} ({db:6.1f} dB)  umbral={umbral:6.3f}{marca}"
                    print("\r" + linea.ljust(70), end="", flush=True)
    except EscuchaError:
        raise
    except KeyboardInterrupt:
        log.info("Detenido (Ctrl+C).")
        if visible:
            print("\n[visible] Detenido.")
    except Exception as exc:
        raise EscuchaError(f"No pude escuchar del micrófono: {exc}") from exc


# --- Modo calibración (interactivo, por consola) ------------------------------
def calibrar() -> None:
    """Muestra en vivo el nivel captado, el umbral y las palmadas detectadas.

    Nunca dispara el agente ni el audio — es solo para ajustar los umbrales de
    config.py contra el ruido real de la sala antes de fiarse del modo normal.
    """
    sd = _importar_sounddevice()

    print("CRISTOPHER — calibración (Ctrl+C para salir; esto NO lanza el agente)")
    print(f"  Umbral: PALMADA_UMBRAL={PALMADA_UMBRAL}  PALMADA_FACTOR={PALMADA_FACTOR}")
    print(f"  Ventana entre palmadas: {PALMADA_GAP_MIN}s – {PALMADA_GAP_MAX}s\n")

    suelo = 0.01
    activo_prev = False
    ultimo_onset = -1e9

    with sd.InputStream(samplerate=SR, channels=1, dtype="float32", blocksize=BLOQUE) as stream:
        while True:
            data, _ = stream.read(BLOQUE)
            pico = float(np.max(np.abs(data)))
            umbral = max(PALMADA_UMBRAL, suelo * PALMADA_FACTOR)
            db = 20 * np.log10(max(pico, 1e-6))
            ahora = time.monotonic()

            marca = ""
            if pico >= umbral and not activo_prev:
                dt = ahora - ultimo_onset
                if PALMADA_GAP_MIN <= dt <= PALMADA_GAP_MAX:
                    marca = "  <<< 2 PALMADAS: TRIGGER >>>"
                    ultimo_onset = -1e9
                else:
                    marca = "  <- palmada"
                    ultimo_onset = ahora
                activo_prev = True
            elif pico < umbral:
                activo_prev = False
                suelo = 0.95 * suelo + 0.05 * pico

            linea = f"nivel={pico:6.3f} ({db:6.1f} dB)  umbral={umbral:6.3f}{marca}"
            print("\r" + linea.ljust(70), end="", flush=True)


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
    if arg == "--calibrar":
        try:
            calibrar()
        except EscuchaError as exc:
            print(f"\n[calibrar] {exc}")
            return 1
        except KeyboardInterrupt:
            print("\n[calibrar] Detenido.")
        return 0
    if arg == "--visible":
        _configurar_logging(consola=True)
        try:
            escuchar(visible=True)
        except EscuchaError as exc:
            print(f"\n[visible] {exc}")
            return 1
        return 0

    # Modo normal (el que lanza el Programador de tareas / carpeta Inicio): nunca
    # imprime nada — pythonw no tiene consola y sys.stdout ahí es None.
    _configurar_logging()
    try:
        escuchar()
    except EscuchaError as exc:
        log.error(str(exc))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
