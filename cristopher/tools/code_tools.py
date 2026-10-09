"""Herramientas de modo code ("Cristopher Code": gestión de sesiones de Claude Code).

Permiten a CRISTOPHER entrar/salir del "modo code" por INTENCIÓN del usuario (sin
keywords fijas). Además de mover el flag de estado_code, publican el cambio al bus del
HUD (para que la pantalla se transforme) — pequeño acoplamiento tools→bus deliberado:
a diferencia de sesiones.py, estado_code no necesita probarse aislado del HUD.
"""

from __future__ import annotations

from cristopher import pantalla


def activar_modo_code() -> str:
    """Activa el modo code: a partir de ahora eres el gestor proactivo de las sesiones
    de Claude Code — mantienes visión de todas, propones prioridades y asesoras sin
    esperar órdenes literales. Úsalo cuando detectes que el usuario se pone a gestionar
    agentes/sesiones de programación ('vamos a gestionar unas sesiones de código',
    'ponme en modo code', 'quiero programar con agentes'). Si no está claro,
    pregúntale antes en lugar de activarlo."""
    # Comparte la pantalla dedicada del HUD con redes/calendario: pantalla.py apaga
    # el que estuviera ocupándola.
    pantalla.activar("code")
    return "Modo code activado: a gestionar sesiones."


def desactivar_modo_code() -> str:
    """Desactiva el modo code y vuelve al comportamiento normal. Úsalo cuando el
    usuario dé la sesión de trabajo con agentes por terminada ('ya está', 'salgamos
    de modo code', 'volvamos a lo normal')."""
    pantalla.desactivar("code")
    return "Modo code desactivado."
