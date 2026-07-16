"""Estado de runtime del modo diario.

`modo_diario` decide si el system prompt incluye el bloque de instrucciones de
reflexión diaria (ver agent.py::_diario_block). Es mutable en caliente: el usuario
puede activarlo/desactivarlo por intención (herramientas activar_modo_diario /
desactivar_modo_diario) sin reiniciar. Flag independiente de estado.py (modo_voz):
ambos modos pueden coexistir.
"""

from __future__ import annotations

_modo_diario: bool = False


def activar() -> None:
    global _modo_diario
    _modo_diario = True


def desactivar() -> None:
    global _modo_diario
    _modo_diario = False


def esta_activo() -> bool:
    return _modo_diario
