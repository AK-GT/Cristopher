"""Bucle de conversación por voz de CRISTOPHER (Fase 6).

Push-to-talk: mantén ESPACIO mientras hablas, suéltalo al terminar. CRISTOPHER
transcribe, razona (sin verbalizar el pensamiento) y responde en voz alta.

Uso:  python -m cristopher.voz_repl
"""

from __future__ import annotations

import re
import sys

from cristopher import estado, voz
from cristopher.agent import Cristopher, split_final
from cristopher.config import ConfigError
from cristopher.tools.google_tools import set_confirmer

CYAN = "\033[36m"
DIM = "\033[2m"
BOLD = "\033[1m"
AMBER = "\033[33m"
RESET = "\033[0m"

# Palabras (tokens completos) que cuentan como "sí" / "no" al confirmar por voz.
_AFIRMATIVAS = {"sí", "si", "vale", "confirmo", "confirmado", "adelante", "claro",
                "envía", "envia", "enviar", "envíalo", "envialo", "ok", "okay",
                "hazlo", "correcto", "afirmativo"}
_NEGATIVAS = {"no", "nunca", "jamás", "jamas", "cancela", "cancelar", "negativo",
              "para", "detente"}


def _es_afirmativo(resp: str) -> bool:
    """True si la transcripción es un 'sí' claro. La negación MANDA (si aparece un 'no',
    no se envía aunque también haya un verbo afirmativo, p. ej. 'no lo envíes')."""
    tokens = set(re.findall(r"\w+", resp.lower()))
    if tokens & _NEGATIVAS:
        return False
    return bool(tokens & _AFIRMATIVAS)


def _voz_confirm(prompt: str) -> bool:
    """Confirmador por VOZ (§9): lee el borrador en pantalla, lo pregunta en voz alta y
    espera un 'sí/no' hablado (push-to-talk). Ante duda o silencio, NO envía."""
    print(f"{AMBER}[CONFIRMAR]{RESET} {prompt}")
    try:
        voz.hablar("Voy a enviar un correo. ¿Lo confirmo? Mantén espacio y di sí o no.")
        audio = voz.grabar_push_to_talk()
        if audio is None or len(audio) == 0:
            return False
        resp = voz.transcribir(audio).strip()
    except voz.VozError as exc:
        print(f"{AMBER}[VOZ] {exc}{RESET}")
        return False
    print(f"{DIM}(respuesta: {resp!r}){RESET}")
    return _es_afirmativo(resp)


def main() -> int:
    print(f"{CYAN}{BOLD}CRISTOPHER — modo voz{RESET}")
    try:
        # on_step vacío: en voz no se muestran trazas, solo la respuesta.
        cris = Cristopher()
    except ConfigError as exc:
        print(f"{AMBER}[CONFIG] {exc}{RESET}")
        return 1

    set_confirmer(_voz_confirm)  # confirmación hablada en vez de input() (§9)
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
