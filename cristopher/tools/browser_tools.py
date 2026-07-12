"""Herramientas de navegador (Fase 5).

`navegar_leer` es la vía PRIMARIA (texto/estructura, barata y fiable).
`navegar_capturar` es el recurso SECUNDARIO: solo cuando el HTML no basta (contenido
en canvas, imágenes o render JS que no sale como texto).
"""

from __future__ import annotations

from cristopher.browser import BrowserError, get_browser
from cristopher.vision import preguntar_sobre_imagen


def navegar_leer(url: str) -> str:
    """Abre una página web y devuelve su título y texto legible. VÍA PRIMARIA para
    extraer información de una web.

    Args:
        url: dirección completa (con http/https).
    """
    try:
        return get_browser().leer(url)
    except BrowserError as exc:
        return f"ERROR de navegador: {exc}"
    except Exception as exc:
        return f"ERROR al leer la página: {exc}"


def navegar_capturar(url: str, pregunta: str = "¿Qué muestra esta página?") -> str:
    """Hace una CAPTURA de la página y usa visión para responder una pregunta sobre
    ella. Úsala SOLO cuando `navegar_leer` no da la información (contenido no textual,
    gráficos, render dinámico).

    Args:
        url: dirección completa (con http/https).
        pregunta: qué quieres saber de lo que se ve en la página.
    """
    try:
        png, title = get_browser().capturar(url)
    except BrowserError as exc:
        return f"ERROR de navegador: {exc}"
    except Exception as exc:
        return f"ERROR al capturar la página: {exc}"
    try:
        respuesta = preguntar_sobre_imagen(png, pregunta)
    except Exception as exc:
        return (
            f"Captura hecha de «{title}» (guardada en workspace/last_capture.png), "
            f"pero la visión falló: {exc}"
        )
    return f"[Captura de «{title}» analizada por visión]\n{respuesta}"
