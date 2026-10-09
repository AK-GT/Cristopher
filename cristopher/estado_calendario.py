"""Estado de runtime del Modo calendario (pantalla de agenda del HUD).

Gemelo de `estado_redes.py`/`estado_code.py`: decide si el HUD muestra la pantalla
dedicada del calendario. Comparte esa pantalla con code y redes, así que es
mutuamente excluyente con ellos — la exclusión la aplica `pantalla.py`, no este
módulo."""

from __future__ import annotations

_modo_calendario: bool = False


def activar() -> None:
    global _modo_calendario
    _modo_calendario = True


def desactivar() -> None:
    global _modo_calendario
    _modo_calendario = False


def esta_activo() -> bool:
    return _modo_calendario
