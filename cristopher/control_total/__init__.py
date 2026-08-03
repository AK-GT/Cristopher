"""Modo Control Total — CRISTOPHER operando ratón/teclado de forma autónoma.

Ver PLAN_CONTROL_TOTAL_1.md (raíz del repo) para el diseño completo por fases.
Este paquete arranca aislado (Fase 1: driver.py + killswitch.py) — nada aquí se
conecta todavía a agent.py ni al registro TOOLS.

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
