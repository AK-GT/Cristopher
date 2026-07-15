"""Herramientas: delegar tareas de código a un sub-agente headless (Fase 3) y analizar
un proyecto real en disco (Módulo análisis de proyecto).

CRISTOPHER delega una tarea acotada a Claude Code (`claude -p`) que trabaja de forma
autónoma, y luego CRISTOPHER integra el resultado en su bucle (leyendo/ejecutando lo
que el sub-agente creó).

Aislamiento (§5/§9): `delegar_a_claude` corre confinado a workspace/subagents/<carpeta>.
`analizar_proyecto` es la excepción deliberada: apunta a una carpeta REAL del usuario
(decisión explícita del usuario, no re-litigar sin avisarle) con permisos de
edición/ejecución libres ahí — por eso su entrada en TOOLS exige avisar y pedir OK
antes de lanzarla.
Fallos explícitos (§1): si `claude` no está o la ejecución falla, se reporta con
contexto, nunca se finge éxito.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from cristopher.config import PROJECT_ANALYSIS_TIMEOUT, SUBAGENT_TIMEOUT, SUBAGENTS

# Tope de caracteres devueltos al bucle (reutiliza el criterio de shell.run_shell).
MAX_OUTPUT = 12_000


def _entorno_sub_agente() -> dict[str, str]:
    """Entorno del sub-proceso `claude`, sin variables CLAUDE_*/CLAUDECODE de sesión.

    Si CRISTOPHER se ejecuta él mismo dentro de una sesión de Claude Code (p. ej.
    lanzado desde su terminal), esas variables se heredan por defecto y el
    sub-agente se "engancha" a la sesión padre en vez de arrancar una sesión nueva
    que de verdad explore la carpeta indicada. Se filtran (no ANTHROPIC_*: ahí
    puede vivir la credencial que el CLI necesita para autenticarse) para
    garantizar aislamiento real, no solo nominal (§5/§9).
    """
    return {k: v for k, v in os.environ.items() if not k.startswith("CLAUDE")}


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
            env=_entorno_sub_agente(),
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


def analizar_proyecto(ruta: str, pregunta: str = "") -> str:
    """Analiza un proyecto de código REAL en disco con un sub-agente Claude Code.

    A diferencia de `delegar_a_claude`, el sub-agente trabaja directamente sobre
    `ruta` (la carpeta real del proyecto, no una aislada bajo workspace/) y con
    permisos de edición/ejecución libres ahí dentro — decisión explícita del
    usuario. Piensa y explora como un programador de élite: arquitectura, calidad,
    riesgos, deuda técnica y qué mejoraría.

    Args:
        ruta: carpeta del proyecto a analizar (ruta absoluta o relativa al cwd).
        pregunta: qué quiere saber el usuario en particular. Vacío = análisis general.
    """
    ruta = (ruta or "").strip()
    if not ruta:
        return "ERROR: falta la ruta del proyecto a analizar."

    proyecto = Path(ruta).expanduser().resolve()
    if not proyecto.exists():
        return f"ERROR: no encuentro esa carpeta de proyecto: {proyecto}"
    if not proyecto.is_dir():
        return f"ERROR: {proyecto} no es una carpeta."

    exe = shutil.which("claude")
    if not exe:
        return (
            "ERROR: no encuentro el ejecutable 'claude' en el PATH. Instala Claude "
            "Code o revisa la instalación antes de analizar."
        )

    pregunta = (pregunta or "").strip()
    tarea = (
        f"El proyecto a analizar es EXACTAMENTE tu directorio de trabajo actual: "
        f"{proyecto}. No exploras, lees ni referencias nada fuera de esa carpeta "
        "(ni el sistema, ni otros proyectos del disco), aunque el proyecto te "
        "parezca pequeño o incompleto: analiza lo que hay ahí, tal cual está.\n\n"
        "Actúa como un programador de élite haciendo una revisión de punta a punta "
        "de este proyecto. Explora su estructura, lee el código y la documentación "
        "que haga falta, y responde en español con un análisis honesto y concreto: "
        "arquitectura, calidad, bugs o riesgos que veas, deuda técnica y qué "
        "mejorarías tú. No hace falta que edites nada salvo que la pregunta del "
        "usuario lo pida explícitamente.\n\n"
        f"Pregunta del usuario: {pregunta or '(ninguna en particular — análisis general del proyecto)'}"
    )

    cmd = [exe, "-p", tarea, "--permission-mode", "bypassPermissions"]
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(proyecto),
            capture_output=True,
            text=True,
            timeout=PROJECT_ANALYSIS_TIMEOUT,
            env=_entorno_sub_agente(),
        )
    except subprocess.TimeoutExpired:
        return (
            f"ERROR: el análisis superó el timeout de {PROJECT_ANALYSIS_TIMEOUT}s y "
            f"fue abortado. Proyecto: {proyecto}"
        )
    except Exception as exc:
        return f"ERROR al lanzar el análisis: {exc}"

    out = (proc.stdout or "").strip()
    err = (proc.stderr or "").strip()
    parts = [
        f"[análisis de proyecto · carpeta: {proyecto}]",
        f"[exit code: {proc.returncode}]",
    ]
    if out:
        parts.append(f"[análisis]\n{out}")
    if err:
        parts.append(f"[stderr]\n{err}")
    result = "\n".join(parts)
    if len(result) > MAX_OUTPUT:
        result = result[:MAX_OUTPUT] + "\n… (salida truncada)"
    return result
