"""Estado de runtime del modo code ("Cristopher Code").

`modo_code` decide si el system prompt incluye el bloque de instrucciones de gestor
de sesiones (ver agent.py::_code_block) y qué buffer de conversación usa el bucle
(_contents_normal vs _contents_code). Es mutable en caliente: el usuario puede
activarlo/desactivarlo por intención (herramientas activar_modo_code /
desactivar_modo_code) o por el botón del HUD, sin reiniciar. Flag independiente de
estado.py (modo_voz) y estado_diario.py: los tres coexisten.
"""

from __future__ import annotations

_modo_code: bool = False


def activar() -> None:
    global _modo_code
    _modo_code = True


def desactivar() -> None:
    global _modo_code
    _modo_code = False


def esta_activo() -> bool:
    return _modo_code
