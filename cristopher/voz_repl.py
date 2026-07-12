"""Bucle de conversación por voz de CRISTOPHER (Fase 6).

Push-to-talk: mantén ESPACIO mientras hablas, suéltalo al terminar. CRISTOPHER
transcribe, razona (sin verbalizar el pensamiento) y responde en voz alta.

Uso:  python -m cristopher.voz_repl
"""

from __future__ import annotations

import sys

from cristopher import estado, voz
from cristopher.agent import Cristopher, split_final
from cristopher.config import ConfigError

CYAN = "\033[36m"
DIM = "\033[2m"
BOLD = "\033[1m"
AMBER = "\033[33m"
RESET = "\033[0m"


def main() -> int:
    print(f"{CYAN}{BOLD}CRISTOPHER — modo voz{RESET}")
    try:
        # on_step vacío: en voz no se muestran trazas, solo la respuesta.
        cris = Cristopher()
    except ConfigError as exc:
        print(f"{AMBER}[CONFIG] {exc}{RESET}")
        return 1

    estado.activar()  # el bucle de voz siempre habla la respuesta
    print(f"{DIM}Mantén ESPACIO para hablar; suéltalo al terminar. Di 'salir' para terminar.{RESET}")

    while True:
        try:
            audio = voz.grabar_push_to_talk()
        except (KeyboardInterrupt, EOFError):
            print("\nHasta luego.")
            return 0
        except voz.VozError as exc:
            print(f"{AMBER}[VOZ] {exc}{RESET}")
            return 1

        if audio is None or len(audio) == 0:
            continue

        try:
            texto = voz.transcribir(audio)
        except voz.VozError as exc:
            print(f"{AMBER}[VOZ] {exc}{RESET}")
            continue

        if not texto:
            print(f"{DIM}(no te he entendido, repite)"+RESET)
            continue
        print(f"{BOLD}tú (voz) ›{RESET} {texto}")

        if texto.strip().lower().rstrip(".!?") in {"salir", "adiós", "adios", "termina"}:
            voz.hablar("Hasta luego.")
            print("Hasta luego.")
            return 0

        try:
            answer = cris.send(texto)
        except Exception as exc:  # fallo explícito (§1)
            print(f"{AMBER}[ERROR] {exc}{RESET}")
            continue

        _, respuesta = split_final(answer)
        print(f"{CYAN}{BOLD}CRISTOPHER ›{RESET} {respuesta}")
        try:
            voz.hablar(respuesta)
        except voz.VozError as exc:
            print(f"{AMBER}[VOZ] {exc}{RESET}")


if __name__ == "__main__":
    sys.exit(main())
