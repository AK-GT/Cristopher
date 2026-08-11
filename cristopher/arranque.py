"""Config autoeditable de la canción de arranque (2 palmadas, escucha.py).

Mismo patrón que personalidad.py/briefing.py: un JSON en data/ (gitignored) que
CRISTOPHER edita por sí mismo cuando el usuario se lo pide en conversación — nunca por
keyword, por intención (§ Personalidad/CLAUDE.md). Sin config guardada, se usa
DEFECTO ("back_in_black"), el mismo nombre que ya resolvía escucha.py antes de existir
este módulo.

El valor guardado es una consulta libre para `musica.resolver` (nombre de archivo en
data/musica, título a buscar en la web, o una URL) — igual que cualquier "pon tal
canción" del módulo de música; aquí solo se persiste cuál usar en el arranque.
"""

from __future__ import annotations

import json
import threading

from cristopher.config import DATA

_PATH = DATA / "arranque.json"
_LOCK = threading.Lock()

DEFECTO = "back_in_black"


def obtener() -> str:
    """Consulta de la canción de arranque activa (DEFECTO si nunca se cambió)."""
    with _LOCK:
        if not _PATH.exists():
            return DEFECTO
        try:
            data = json.loads(_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return DEFECTO
        cancion = str(data.get("cancion", "")).strip() if isinstance(data, dict) else ""
        return cancion or DEFECTO


def cambiar(consulta: str) -> str:
    """Fija la canción de arranque. Devuelve un mensaje de confirmación."""
    consulta = (consulta or "").strip()
    if not consulta:
        return "No dijiste qué canción poner de arranque."
    with _LOCK:
        DATA.mkdir(parents=True, exist_ok=True)
        _PATH.write_text(json.dumps({"cancion": consulta}, ensure_ascii=False, indent=2), encoding="utf-8")
    return f"Canción de arranque cambiada a: {consulta}"
