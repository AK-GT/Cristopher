"""Herramienta: ejecutar comandos de shell / Python.

Es la herramienta POTENTE del MVP — permite `git clone`, listar archivos, ejecutar
scripts. Captura stdout, stderr y código de salida y los devuelve como observación.
Los comandos se ejecutan dentro del WORKSPACE del proyecto (aislamiento, §5/§9).
El REPL muestra cada comando antes de correrlo para que el usuario lo vea.
"""

from __future__ import annotations

import subprocess

from cristopher.config import WORKSPACE

# Tope de segundos por comando para que un proceso colgado no bloquee el bucle.
DEFAULT_TIMEOUT = 120
# Tope de caracteres devueltos al modelo (evita inundar el contexto).
MAX_OUTPUT = 12_000


def run_shell(command: str, timeout: int = DEFAULT_TIMEOUT) -> str:
    """Ejecuta un comando de shell y devuelve stdout, stderr y el código de salida.

    El comando corre en el directorio de trabajo (workspace/) del proyecto.

    Args:
        command: comando a ejecutar (p. ej. 'git clone https://... repo').
        timeout: segundos máximos antes de abortar.
    """
    WORKSPACE.mkdir(parents=True, exist_ok=True)
    try:
        proc = subprocess.run(
            command,
            shell=True,
            cwd=str(WORKSPACE),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return f"ERROR: el comando superó el timeout de {timeout}s y fue abortado."
    except Exception as exc:
        return f"ERROR al ejecutar el comando: {exc}"

    out = (proc.stdout or "").strip()
    err = (proc.stderr or "").strip()
    parts = [f"[exit code: {proc.returncode}]"]
    if out:
        parts.append(f"[stdout]\n{out}")
    if err:
        parts.append(f"[stderr]\n{err}")
    result = "\n".join(parts)
    if len(result) > MAX_OUTPUT:
        result = result[:MAX_OUTPUT] + "\n… (salida truncada)"
    return result
