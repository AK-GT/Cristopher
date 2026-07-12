"""REPL de consola de CRISTOPHER (Fase 1).

Muestra las trazas del bucle ReAct (pensamiento, llamada a herramienta, observación)
y la respuesta final. Aborta con mensaje claro si falta la GEMINI_API_KEY.

Uso:  python -m cristopher.main
"""

from __future__ import annotations

import sys

from cristopher.agent import Cristopher, split_final
from cristopher.config import ConfigError, MOSTRAR_PENSAMIENTO

# --- Colores ANSI mínimos (se degradan a nada si la consola no los soporta) ---
CYAN = "\033[36m"
DIM = "\033[2m"
AMBER = "\033[33m"
BOLD = "\033[1m"
RESET = "\033[0m"

BANNER = f"""{CYAN}{BOLD}
  ██████ ██████  ██ ███████ ████████  ██████  ██████  ██   ██ ███████ ██████
 ██      ██   ██ ██ ██         ██    ██    ██ ██   ██ ██   ██ ██      ██   ██
 ██      ██████  ██ ███████    ██    ██    ██ ██████  ███████ █████   ██████
 ██      ██   ██ ██      ██    ██    ██    ██ ██      ██   ██ ██      ██   ██
  ██████ ██   ██ ██ ███████    ██     ██████  ██      ██   ██ ███████ ██   ██
{RESET}{DIM} Fase 1 — MVP del bucle · cerebro: Gemini · llega a la solución.{RESET}
"""

HELP = (
    f"{DIM}Escribe tu petición y pulsa Enter. "
    f"Comandos: 'salir'/'exit' para terminar.{RESET}"
)


def _print_step(kind: str, text: str) -> None:
    """Callback de trazas del bucle ReAct."""
    text = (text or "").rstrip()
    if not text:
        return
    # En modo voz (Fase 6) no se muestran/verbalizan las trazas: solo la respuesta final.
    if not MOSTRAR_PENSAMIENTO:
        return
    if kind == "thought":
        print(f"{DIM}  · {text}{RESET}")
    elif kind == "tool_call":
        print(f"{CYAN}  → {text}{RESET}")
    elif kind == "observation":
        # Recorta observaciones largas en la traza (el modelo sí ve el total).
        preview = text if len(text) <= 500 else text[:500] + " …"
        indented = preview.replace("\n", "\n    ")
        print(f"{DIM}    {indented}{RESET}")


def main() -> int:
    print(BANNER)
    try:
        cris = Cristopher(on_step=_print_step)
    except ConfigError as exc:
        print(f"{AMBER}[CONFIG] {exc}{RESET}")
        return 1

    print(HELP)
    while True:
        try:
            user = input(f"\n{BOLD}tú ›{RESET} ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nHasta luego.")
            return 0

        if not user:
            continue
        if user.lower() in {"salir", "exit", "quit"}:
            print("Hasta luego.")
            return 0

        try:
            answer = cris.send(user)
        except Exception as exc:  # fallo explícito, nunca fingir éxito (§1)
            print(f"{AMBER}[ERROR] {exc}{RESET}")
            continue

        # Separa el razonamiento (segundo plano, atenuado) de la respuesta al usuario.
        razonamiento, respuesta = split_final(answer)
        if razonamiento and MOSTRAR_PENSAMIENTO:
            indent = razonamiento.replace("\n", "\n  ")
            print(f"{DIM}  ⋯ {indent}{RESET}")
        print(f"\n{CYAN}{BOLD}CRISTOPHER ›{RESET} {respuesta}")


if __name__ == "__main__":
    sys.exit(main())
