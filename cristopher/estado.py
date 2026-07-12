"""Estado de runtime de CRISTOPHER (Fase 6).

`modo_voz` decide si la respuesta se habla (TTS) y si las trazas de pensamiento se
ocultan. Es mutable en caliente: el usuario puede activarlo/desactivarlo por intención
(herramientas activar_modo_voz / desactivar_modo_voz) sin reiniciar.
"""

from __future__ import annotations

from cristopher.config import MODO_SALIDA

_modo_voz: bool = MODO_SALIDA == "voz"


def activar() -> None:
    global _modo_voz
    _modo_voz = True


def desactivar() -> None:
    global _modo_voz
    _modo_voz = False


def esta_activo() -> bool:
    return _modo_voz
