"""Herramientas de Modo redes (dashboard YouTube/Instagram/TikTok, ver PLAN "Modo
redes"). Calco de `code_tools.py`: permiten a CRISTOPHER entrar/salir del modo por
INTENCIÓN del usuario (sin keywords fijas), además de mover el flag de
estado_redes, publican el cambio al bus del HUD (para que la pantalla se
transforme) — mismo acoplamiento tools→bus ya aceptado en Modo code.

Modo redes y Modo code comparten la misma pantalla dedicada del HUD y son
mutuamente excluyentes (ver style.css body[data-modo]): activar uno aquí también
desactiva el otro, igual que ya hace el botón del HUD (hud/__main__.py). Esta
misma exclusión se replica en code_tools.activar_modo_code para que activarlo por
voz/texto tenga el mismo efecto que el botón, sin depender de qué camino se use.
"""

from __future__ import annotations

from cristopher import bus, estado_code, estado_redes


def activar_modo_redes() -> str:
    """Activa el Modo redes: abre en el HUD el dashboard de YouTube/Instagram/
    TikTok (cuentas conectadas, métricas y tips). Úsalo cuando detectes que el
    usuario quiere ver el panorama de sus redes sociales ('ponme en modo redes',
    'quiero ver mis estadísticas de redes', 'abre el dashboard de redes'). Si no
    está claro, pregúntale antes en lugar de activarlo."""
    estado_redes.activar()
    estado_code.desactivar()
    bus.set_modo_code(False)
    bus.set_modo_redes(True)
    return "Modo redes activado: dashboard de YouTube/Instagram/TikTok en el HUD."


def desactivar_modo_redes() -> str:
    """Desactiva el Modo redes y vuelve al HUD normal. Úsalo cuando el usuario dé
    por terminada la consulta de sus redes ('ya está', 'salgamos de modo redes',
    'volvamos a lo normal')."""
    estado_redes.desactivar()
    bus.set_modo_redes(False)
    return "Modo redes desactivado."
