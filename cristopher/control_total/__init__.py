"""Modo Control Total — CRISTOPHER operando ratón/teclado de forma autónoma.

Ver CLAUDE.md ("Modo Control Total") para el contrato de seguridad y el porqué de
cada pieza. Se construyó por fases: driver.py + killswitch.py (cimientos), bucle.py +
guardarraíles.py (percepción→decisión→acción) y la integración con agent.py/TOOLS/HUD.

Logging centralizado aquí (una sola vez, al importar el paquete) porque driver.py y
killswitch.py comparten el mismo logger "cristopher.control_total": cualquiera de
los dos que se importe primero dispara la carga de este __init__ y deja el
FileHandler listo para ambos, mismo estilo que escucha.py::_configurar_logging.
"""

from __future__ import annotations

import logging

from cristopher.config import CONTROL_TOTAL_LOG

log = logging.getLogger("cristopher.control_total")

if not any(isinstance(h, logging.FileHandler) for h in log.handlers):
    CONTROL_TOTAL_LOG.parent.mkdir(parents=True, exist_ok=True)
    _handler = logging.FileHandler(CONTROL_TOTAL_LOG, encoding="utf-8")
    _handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    log.addHandler(_handler)
    log.setLevel(logging.INFO)
