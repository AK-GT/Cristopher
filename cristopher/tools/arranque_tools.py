"""Herramientas de la canción de arranque (2 palmadas, ver cristopher/escucha.py).

Envuelven el almacén persistente de `arranque.py`. A diferencia de personalidad_tools,
estas SÍ requieren orden explícita del usuario (no hay "señal indirecta" razonable para
cambiar una canción de arranque).
"""

from __future__ import annotations

import psutil

from cristopher import arranque


def cancion_arranque_cambiar(consulta: str) -> str:
    """Cambia la canción que suena al detectar las 2 palmadas de arranque.

    Úsala solo cuando el usuario lo pida explícitamente (p. ej. "pon tal canción de
    arranque" o "cambia la canción de las palmadas a X").

    Args:
        consulta: nombre de archivo en la biblioteca local (data/musica), título a
            buscar en la web, o una URL directa — igual que pedirle a poner cualquier
            canción.
    """
    return arranque.cambiar(consulta)


def cancion_arranque_ver() -> str:
    """Muestra qué canción de arranque está configurada ahora mismo.

    Úsala si el usuario pregunta qué canción suena con las 2 palmadas.
    """
    return f"La canción de arranque configurada es: {arranque.obtener()}"


def escucha_detener_sesion() -> str:
    """Mata el proceso de escucha por palmadas (cristopher.escucha) para lo que queda
    de esta sesión de Windows — deja de reaccionar a palmadas hasta que se apague/
    reinicie el ordenador. El autostart NO se toca: la próxima vez que se encienda el
    ordenador, escucha.py arranca solo con normalidad.

    Úsala solo cuando el usuario pida explícitamente que no le vuelva a despertar por
    palmadas "en esta sesión" / "hasta que reinicie" / "hasta que apague el ordenador".
    Para desactivarlo de forma más permanente ya existe el archivo bandera
    (data/escucha.desactivada, ver escucha.py) — esta tool es para la sesión actual.
    """
    matados = []
    for proc in psutil.process_iter(["pid", "cmdline"]):
        try:
            cmdline = proc.info["cmdline"] or []
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        if any("cristopher.escucha" in str(arg) for arg in cmdline):
            proc.terminate()
            matados.append(proc.pid)
    if not matados:
        return "No encontré ningún proceso de escucha por palmadas corriendo ahora mismo."
    return (
        "Escucha por palmadas detenida para lo que queda de esta sesión. "
        "Volverá a arrancar sola la próxima vez que enciendas el ordenador."
    )
