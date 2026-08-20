"""Estado de runtime del modo redes (dashboard de YouTube/Instagram/TikTok).

`modo_redes` decide si el HUD muestra la pantalla dedicada de "Modo redes" (ver
CLAUDE.md en la raíz del repo, y `estado_code.py` para el mecanismo
gemelo de Modo Code). Mutable en caliente: el usuario puede activarlo/desactivarlo
por intención (tools `activar_modo_redes`/`desactivar_modo_redes`) o por el botón
del HUD, sin reiniciar. Flag independiente de estado.py (modo_voz) y
estado_diario.py — pero mutuamente excluyente con estado_code.py: activar uno
desactiva el otro (lo aplica `hud/__main__.py`, no este módulo)."""

from __future__ import annotations

_modo_redes: bool = False


def activar() -> None:
    global _modo_redes
    _modo_redes = True


def desactivar() -> None:
    global _modo_redes
    _modo_redes = False


def esta_activo() -> bool:
    return _modo_redes
