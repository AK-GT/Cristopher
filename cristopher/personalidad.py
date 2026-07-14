"""Capa adaptable de personalidad de CRISTOPHER.

Guarda directivas de estilo (trato, gustos de cine, tono) que CRISTOPHER mismo decide
añadir o quitar a partir de la conversación — nunca contenido de webs/correos/archivos
(§8). Es solo FORMA: nunca cambia auto-conocimiento (§3) ni seguridad (§8) de
`agent.IDENTITY`.

No usa la memoria de hechos (`memory.py`) porque esas directivas deben regir en TODOS
los turnos, no solo cuando el recuerdo semántico las considera relevantes para el
mensaje actual. Por eso se listan todas, íntegras, cada vez que se arma el prompt.

Vive en data/personalidad.json (gitignored), igual que memory.db/proactivo.db.
"""

from __future__ import annotations

import json
import threading
from typing import Optional

from cristopher.config import DATA

_PATH = DATA / "personalidad.json"
_LOCK = threading.Lock()


def _leer() -> list[str]:
    if not _PATH.exists():
        return []
    try:
        data = json.loads(_PATH.read_text(encoding="utf-8"))
        return [str(x) for x in data] if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def _escribir(directivas: list[str]) -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    _PATH.write_text(json.dumps(directivas, ensure_ascii=False, indent=2), encoding="utf-8")


def agregar(texto: str) -> str:
    """Añade una directiva de personalidad. Devuelve un mensaje de confirmación."""
    texto = (texto or "").strip()
    if not texto:
        return "No había ninguna directiva que guardar (texto vacío)."
    with _LOCK:
        directivas = _leer()
        if texto.lower() in (d.lower() for d in directivas):
            return f"Ya tenía guardada esa directiva: {texto}"
        directivas.append(texto)
        _escribir(directivas)
    return f"Directiva de personalidad guardada: {texto}"


def quitar(fragmento: str) -> str:
    """Elimina las directivas cuyo texto contenga `fragmento` (sin distinguir
    mayúsculas). Devuelve qué se quitó, o que no encontró nada."""
    fragmento = (fragmento or "").strip()
    if not fragmento:
        return "No indicaste qué directiva quitar."
    with _LOCK:
        directivas = _leer()
        frag_low = fragmento.lower()
        quedan = [d for d in directivas if frag_low not in d.lower()]
        quitadas = [d for d in directivas if frag_low in d.lower()]
        if not quitadas:
            return f"No encontré ninguna directiva que mencione: {fragmento}"
        _escribir(quedan)
    return "Quitadas:\n" + "\n".join(f"- {d}" for d in quitadas)


def listar() -> list[str]:
    """Todas las directivas de personalidad activas."""
    with _LOCK:
        return _leer()


def formatear_para_prompt() -> str:
    """Bloque de texto listo para inyectar en el system prompt. Vacío si no hay
    directivas (la personalidad base de agent.py gobierna sola)."""
    directivas = listar()
    if not directivas:
        return ""
    lineas = "\n".join(f"- {d}" for d in directivas)
    return (
        "Directivas de personalidad que el propio usuario te dio (tienen prioridad "
        "sobre los rasgos base si hay conflicto):\n" + lineas
    )
