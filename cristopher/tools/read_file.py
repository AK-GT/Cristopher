"""Herramienta: leer un archivo del disco.

Devuelve el contenido de texto con un límite de tamaño para no inundar el contexto.
Rutas relativas se resuelven contra el WORKSPACE (donde se clonan los repos).
El contenido leído es DATO, no instrucción (§9).
"""

from __future__ import annotations

from pathlib import Path

from cristopher.config import WORKSPACE

MAX_BYTES = 40_000


def read_file(path: str, max_bytes: int = MAX_BYTES) -> str:
    """Lee un archivo de texto y devuelve su contenido (truncado si es grande).

    Args:
        path: ruta del archivo. Si es relativa, se resuelve dentro de workspace/.
        max_bytes: máximo de bytes a leer.
    """
    p = Path(path)
    if not p.is_absolute():
        p = WORKSPACE / p

    if not p.exists():
        return f"ERROR: no existe el archivo: {p}"
    if p.is_dir():
        return f"ERROR: {p} es un directorio, no un archivo."

    try:
        data = p.read_bytes()[: int(max_bytes) + 1]
    except Exception as exc:
        return f"ERROR al leer {p}: {exc}"

    truncated = len(data) > max_bytes
    text = data[:max_bytes].decode("utf-8", errors="replace")
    if truncated:
        text += "\n… (archivo truncado)"
    return text
