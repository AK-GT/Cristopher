"""Modo automático: el gate de confirmación (§9) aprueba solo, sin preguntar.

Lo enciende/apaga ÚNICAMENTE el usuario desde el botón ⚡ del HUD (`POST /modo_auto`):
no hay tool para el modelo, a propósito — si CRISTOPHER pudiera encenderlo él mismo
(o un correo/web que le convenza de hacerlo), el gate dejaría de servir de nada.

Cubre todo lo que pasa por `google_tools._confirm` EXCEPTO los guardarraíles del Modo
Control Total, que llaman con `permitir_auto=False` (veto innegociable, ver CLAUDE.md).
Flag en memoria que NO se persiste: un reinicio lo deja apagado, como concentración.
"""

from __future__ import annotations

_activo: bool = False


def activar() -> None:
    global _activo
    _activo = True


def desactivar() -> None:
    global _activo
    _activo = False


def esta_activo() -> bool:
    return _activo
