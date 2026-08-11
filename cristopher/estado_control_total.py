"""Estado de runtime del Modo Control Total (Fase 4).

`modo_control_total` decide si el widget/borde y el teñido azul del HUD deben
estar en pantalla. Mutable en caliente: el usuario lo activa/desactiva por
intención (herramientas `activar_control_total`/`desactivar_control_total`) o el
kill-switch lo fuerza a desactivar. Flag independiente de estado.py (modo_voz),
estado_code.py y estado_diario.py: los cuatro coexisten.
"""

from __future__ import annotations

_modo_control_total: bool = False


def activar() -> None:
    global _modo_control_total
    _modo_control_total = True


def desactivar() -> None:
    global _modo_control_total
    _modo_control_total = False


def esta_activo() -> bool:
    return _modo_control_total
