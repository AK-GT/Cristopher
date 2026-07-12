"""Herramienta: delegar tareas de código a un sub-agente headless (Fase 3).

CRISTOPHER delega una tarea acotada a Claude Code (`claude -p`) que trabaja de forma
autónoma DENTRO de una carpeta aislada, y luego CRISTOPHER integra el resultado en su
bucle (leyendo/ejecutando lo que el sub-agente creó).

Aislamiento (§5/§9): cada sub-agente corre confinado a workspace/subagents/<carpeta>.
Fallos explícitos (§1): si `claude` no está o la ejecución falla, se reporta con
contexto, nunca se finge éxito.
"""

from __future__ import annotations

import shutil
import subprocess

from cristopher.config import SUBAGENT_TIMEOUT, SUBAGENTS

# Tope de caracteres devueltos al bucle (reutiliza el criterio de shell.run_shell).
MAX_OUTPUT = 12_000


def delegar_a_claude(tarea: str, carpeta: str = "tarea") -> str:
    """Delega una tarea de código a un sub-agente Claude Code que trabaja aislado.

    El sub-agente puede crear/editar archivos y ejecutar comandos, pero solo dentro
    de su carpeta dedicada. Al terminar, usa read_file/run_shell sobre esa carpeta
    para revisar e integrar lo que produjo.

    Args:
        tarea: instrucción acotada y clara para el sub-agente.
        carpeta: nombre de la carpeta de trabajo aislada (bajo workspace/subagents/).
    """
    tarea = (tarea or "").strip()
    if not tarea:
        return "ERROR: la tarea a delegar está vacía."

    exe = shutil.which("claude")
    if not exe:
        return (
            "ERROR: no encuentro el ejecutable 'claude' en el PATH. Instala Claude "
            "Code o revisa la instalación antes de delegar."
        )

    # Carpeta aislada del sub-agente. Solo el nombre base para no escapar del sandbox.
    safe = (carpeta or "tarea").strip().replace("\\", "/").split("/")[-1] or "tarea"
    work = SUBAGENTS / safe
    work.mkdir(parents=True, exist_ok=True)

    # Args en lista (shell=False): el texto de la tarea puede llevar comillas/saltos.
    cmd = [
        exe,
        "-p",
        tarea,
        "--permission-mode",
        "bypassPermissions",
        "--add-dir",
        str(work),
    ]
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(work),
            capture_output=True,
            text=True,
            timeout=SUBAGENT_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return (
            f"ERROR: el sub-agente superó el timeout de {SUBAGENT_TIMEOUT}s y fue "
            f"abortado. Carpeta de trabajo: {work}"
        )
    except Exception as exc:
        return f"ERROR al lanzar el sub-agente: {exc}"

    out = (proc.stdout or "").strip()
    err = (proc.stderr or "").strip()
    parts = [
        f"[sub-agente claude · carpeta: {work}]",
        f"[exit code: {proc.returncode}]",
    ]
    if out:
        parts.append(f"[salida del sub-agente]\n{out}")
    if err:
        parts.append(f"[stderr]\n{err}")
    parts.append(
        "Revisa e integra: usa read_file/run_shell sobre la carpeta indicada para "
        "verificar lo que creó el sub-agente."
    )
    result = "\n".join(parts)
    if len(result) > MAX_OUTPUT:
        result = result[:MAX_OUTPUT] + "\n… (salida truncada)"
    return result
