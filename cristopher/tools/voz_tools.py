"""Herramientas de modo voz (Fase 6).

Permiten a CRISTOPHER entrar/salir del "modo audio" por INTENCIÓN del usuario (sin
keywords fijas). Si no está claro que el usuario quiera voz, el modelo debe preguntar
antes en vez de activarlo.
"""

from __future__ import annotations

from cristopher import estado


def activar_modo_voz() -> str:
    """Activa el modo audio: a partir de ahora las respuestas se dicen en voz alta y no
    se muestra el pensamiento, solo la respuesta. Úsalo cuando el usuario pida claramente
    hablar por voz/audio. Si no está claro, pregúntale antes en lugar de activarlo."""
    estado.activar()
    return "Modo audio activado: hablaré en voz alta."


def desactivar_modo_voz() -> str:
    """Desactiva el modo audio y vuelve a solo texto. Úsalo cuando el usuario pida dejar
    de hablar en voz alta o volver al modo texto."""
    estado.desactivar()
    return "Modo audio desactivado: vuelvo a solo texto."
