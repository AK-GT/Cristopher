"""Herramientas de navegador (Fase 5 + correcciones).

- `navegar_leer(url)`: lectura RÁPIDA y headless de una URL concreta.
- Sesión interactiva VISIBLE: `buscar_en_google`, `navegador_ir`, `navegador_click`,
  `navegador_scroll`, `navegador_leer`, `navegador_captura`, `navegador_cerrar`. Una
  ventana real que el usuario ve y que CRISTOPHER dirige paso a paso para indagar hasta
  un resultado sólido (leyendo HTML y, si hace falta, capturas para guiarse).
"""

from __future__ import annotations

from cristopher.browser import BrowserError, get_browser
from cristopher.vision import preguntar_sobre_imagen


def _wrap(fn, *a, **k) -> str:
    try:
        return fn(*a, **k)
    except BrowserError as exc:
        return f"ERROR de navegador: {exc}"
    except Exception as exc:
        return f"ERROR: {exc}"


# --- Lectura rápida headless --------------------------------------------------
def navegar_leer(url: str) -> str:
    """Abre una página web (rápido, sin ventana) y devuelve su título y texto legible.
    Úsala para LEER una URL concreta que ya conoces.

    Args:
        url: dirección completa (con http/https).
    """
    return _wrap(get_browser().leer, url)


# --- Sesión interactiva visible ----------------------------------------------
def buscar_en_google(query: str) -> str:
    """Abre una ventana de navegador VISIBLE, busca en Google y devuelve los resultados
    numerados (título + URL). Úsala cuando convenga BUSCAR y EXPLORAR resultados en
    vivo (para luego pinchar, desplazarte o abrir enlaces). Indaga hasta tener un
    resultado sólido: no te quedes con el primero si no basta.

    Args:
        query: qué buscar en Google.
    """
    return _wrap(get_browser().buscar_google, query)


def navegador_ir(url: str) -> str:
    """Navega a una URL en la ventana visible de la sesión y devuelve su texto.

    Args:
        url: dirección completa (con http/https).
    """
    return _wrap(get_browser().ir, url)


def navegador_click(objetivo: str) -> str:
    """Pincha un resultado o enlace en la ventana visible: por número (índice del
    resultado de Google, p. ej. '2') o por texto del enlace. Devuelve la página nueva.

    Args:
        objetivo: número de resultado o texto del enlace a pinchar.
    """
    return _wrap(get_browser().click, objetivo)


def navegador_scroll(cantidad: str = "abajo") -> str:
    """Desplaza la página actual de la ventana visible.

    Args:
        cantidad: 'abajo', 'arriba' o un número de píxeles.
    """
    return _wrap(get_browser().scroll, cantidad)


def navegador_leer() -> str:
    """Devuelve el título y texto de la página ACTUAL de la ventana visible."""
    return _wrap(get_browser().leer_actual)


def navegador_captura(pregunta: str = "¿Qué se ve en la pantalla?") -> str:
    """Hace una captura de la página ACTUAL de la ventana visible y usa visión para
    responder una pregunta sobre ella. Úsala para GUIARTE cuando el texto no basta
    (elementos visuales, diseño, dónde pinchar).

    Args:
        pregunta: qué quieres saber de lo que se ve.
    """
    try:
        png, title = get_browser().captura_actual()
    except BrowserError as exc:
        return f"ERROR de navegador: {exc}"
    except Exception as exc:
        return f"ERROR al capturar: {exc}"
    try:
        respuesta = preguntar_sobre_imagen(png, pregunta)
    except Exception as exc:
        return f"Captura hecha de «{title}» (workspace/last_capture.png), pero la visión falló: {exc}"
    return f"[Captura de «{title}» analizada por visión]\n{respuesta}"


def navegador_cerrar() -> str:
    """Cierra la ventana visible del navegador."""
    return _wrap(get_browser().cerrar)
