"""Puesta en vivo de CRISTOPHER (Fase final).

Punto de entrada canónico: `python -m cristopher` imprime el saludo de arranque
(mega prompt §10 — se presenta y enumera sus herramientas reales) y levanta el SISTEMA
VIVO completo: el HUD command-center, que ya cablea el agente + el demonio proactivo +
las métricas de sistema + la voz.

Superficies alternativas:
  python -m cristopher.main      → REPL de texto
  python -m cristopher.voz_repl  → conversación por voz (push-to-talk)

Uso:  python -m cristopher
"""

from __future__ import annotations

import sys

from cristopher.agent import saludo_arranque
from cristopher.config import HUD_PORT

CYAN = "\033[36m"
DIM = "\033[2m"
BOLD = "\033[1m"
RESET = "\033[0m"

BANNER = f"""{CYAN}{BOLD}
  ██████ ██████  ██ ███████ ████████  ██████  ██████  ██   ██ ███████ ██████
 ██      ██   ██ ██ ██         ██    ██    ██ ██   ██ ██   ██ ██      ██   ██
 ██      ██████  ██ ███████    ██    ██    ██ ██████  ███████ █████   ██████
 ██      ██   ██ ██      ██    ██    ██    ██ ██      ██   ██ ██      ██   ██
  ██████ ██   ██ ██ ███████    ██     ██████  ██      ██   ██ ███████ ██   ██
{RESET}{DIM} EN VIVO · orquestador tipo Jarvis · llega a la solución.{RESET}
"""


def main() -> int:
    print(BANNER)
    print(f"{CYAN}{BOLD}CRISTOPHER ›{RESET} {saludo_arranque()}\n")
    print(f"{DIM}Levantando el command-center (HUD) en http://localhost:{HUD_PORT} …{RESET}")
    # El HUD es el sistema vivo completo: agente + demonio proactivo + métricas + voz.
    from cristopher.hud.__main__ import main as hud_main

    return hud_main()


if __name__ == "__main__":
    sys.exit(main())
