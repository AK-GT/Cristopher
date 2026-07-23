"""Gestor de sesiones vivas de Claude Code (Fase 1 del "modo agentes").

Cada `Sesion` es un proceso `claude -p` de larga duración (stream-json de entrada Y
salida, ver PLAN_GESTOR_SESIONES.md §1.0) que trabaja de forma autónoma
(`--permission-mode bypassPermissions`) sobre una carpeta REAL del disco
(`--add-dir`), confinada a ella. Un hilo lector por sesión consume su stdout y
mantiene `estado`/`transcript` al día; ninguna operación del gestor
(crear/escribir/listar/leer) espera a que la sesión termine su trabajo — solo
`parar` bloquea, acotada a `SESION_STOP_TIMEOUT` segundos.

Desacoplado a propósito de `cristopher.bus`/HUD: expone un callback opcional
`on_evento(nombre, evento)` que la Fase 2 registrará para republicar al HUD. Así
este núcleo se prueba solo desde el REPL.

Reutiliza `delegate._entorno_sub_agente()` (mismo saneado de entorno que ya usan
`delegar_a_claude`/`analizar_proyecto`: filtra CLAUDE*/CLAUDECODE, conserva
ANTHROPIC_*) — no se duplica esa lógica.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import threading
import time
import uuid
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from cristopher.config import (
    SESION_CHAT_PERMISSION_MODE,
    SESION_CONTEXTO_AVISO_PCT,
    SESION_STOP_TIMEOUT,
    SESION_TRANSCRIPT_MAXLEN,
    SUBAGENTS,
)
from cristopher.tools.delegate import _entorno_sub_agente

ESTADOS_ACTIVOS = ("arrancando", "trabajando", "esperando", "pregunta")
ESTADOS_FINALES = ("terminado", "error")

# {nombre, evento} -> None. Fase 2 lo registra para republicar al bus del HUD.
OnEvento = Callable[[str, dict], None]


class SesionesError(RuntimeError):
    """El gestor de sesiones no pudo completar la operación pedida."""


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _resumen_contenido(bloque: dict) -> Optional[tuple[str, str]]:
    """De un bloque de `message.content` de un evento `assistant`, extrae
    (rol, texto) legible, o None si el bloque no aporta nada mostrable."""
    tipo = bloque.get("type")
    if tipo == "text":
        texto = (bloque.get("text") or "").strip()
        return ("asistente", texto) if texto else None
    if tipo == "tool_use":
        nombre = bloque.get("name", "?")
        entrada = json.dumps(bloque.get("input", {}), ensure_ascii=False)
        if len(entrada) > 200:
            entrada = entrada[:200] + "…"
        return ("asistente", f"🔧 {nombre}({entrada})")
    return None


class Sesion:
    """Una sesión viva de Claude Code sobre una carpeta real del disco."""

    def __init__(
        self, nombre: str, ruta: Path, proc: subprocess.Popen, session_id: str,
        proyecto_base: Optional[Path] = None, worktree_branch: Optional[str] = None,
        tipo: str = "code", modelo: str = "",
    ):
        self.nombre = nombre
        self.ruta = ruta
        # Carpeta ORIGINAL del proyecto: igual a `ruta` en modo directo; en modo
        # worktree, el repo real (ruta es el worktree aislado). Permite comparar
        # conflictos entre sesiones que trabajan en worktrees distintos del mismo
        # proyecto (Fase 2, ver GestorSesiones.conflictos).
        self.proyecto_base = proyecto_base or ruta
        self.worktree_branch = worktree_branch
        self.tipo = tipo  # "code" (proyecto real/worktree) o "chat" (idear, Fase 4a)
        self.modelo = modelo  # alias pedido al crear (p. ej. "sonnet"); informativo
        self.proc = proc
        self.session_id = session_id
        self.estado = "arrancando"
        self.transcript: deque[dict] = deque(maxlen=SESION_TRANSCRIPT_MAXLEN)
        self.ultima_actividad = time.time()
        self._stdin_lock = threading.Lock()
        # Vigilancia de contexto (Fase 4a): modelo real resuelto (evento system/init,
        # puede diferir del alias pedido) y % de uso de la ÚLTIMA llamada, calculado
        # con el contextWindow que la propia API reporta en el evento `result`
        # (modelUsage.<modelo>.contextWindow) — sin tabla propia que se desincronice.
        self.modelo_resuelto: Optional[str] = None
        self.uso_contexto_pct: Optional[float] = None

    def _log(self, rol: str, texto: str) -> None:
        if not texto:
            return
        # El evento `result` repite el último texto que ya vino en un `assistant`
        # (mismo turno): evita el duplicado consecutivo en el transcript.
        if self.transcript and self.transcript[-1]["rol"] == rol and self.transcript[-1]["texto"] == texto:
            return
        self.transcript.append({"rol": rol, "texto": texto, "ts": _now()})
        self.ultima_actividad = time.time()

    def enviar(self, mensaje: str) -> None:
        """Escribe un turno por stdin. No espera la respuesta (no bloquea)."""
        if self.estado in ESTADOS_FINALES:
            raise SesionesError(
                f"La sesión '{self.nombre}' ya terminó (estado: {self.estado}); "
                "no se le puede escribir."
            )
        payload = {
            "type": "user",
            "message": {"role": "user", "content": [{"type": "text", "text": mensaje}]},
        }
        linea = json.dumps(payload, ensure_ascii=False) + "\n"
        with self._stdin_lock:
            try:
                self.proc.stdin.write(linea)
                self.proc.stdin.flush()
            except (BrokenPipeError, OSError) as exc:
                self.estado = "error"
                self._log("sistema", f"No pude escribir a la sesión: {exc}")
                raise SesionesError(
                    f"La sesión '{self.nombre}' no acepta más entrada (proceso muerto): {exc}"
                ) from exc
        self.estado = "trabajando"
        self._log("usuario", mensaje)


def _leer_stdout(sesion: Sesion, on_evento: Optional[OnEvento]) -> None:
    """Hilo lector: consume stdout NDJSON de la sesión y actualiza su estado."""
    proc = sesion.proc
    try:
        for linea in proc.stdout:
            linea = linea.strip()
            if not linea:
                continue
            try:
                evento = json.loads(linea)
            except json.JSONDecodeError:
                sesion._log("sistema", f"[línea cruda] {linea[:300]}")
                continue

            antes = len(sesion.transcript)
            tipo = evento.get("type")
            if tipo == "system" and evento.get("subtype") == "init":
                sesion.modelo_resuelto = evento.get("model") or sesion.modelo_resuelto
                if sesion.estado not in ESTADOS_FINALES:
                    sesion.estado = "trabajando"
            elif tipo == "assistant":
                for bloque in evento.get("message", {}).get("content", []):
                    resumen = _resumen_contenido(bloque)
                    if resumen:
                        sesion._log(*resumen)
            elif tipo == "result":
                texto = (evento.get("result") or "").strip()
                if texto:
                    sesion._log("asistente", texto)
                # Uso de contexto real (Fase 4a): el propio evento trae el tamaño de
                # ventana del modelo que de verdad respondió — nada que mantener a mano.
                info_modelo = (evento.get("modelUsage") or {}).get(sesion.modelo_resuelto)
                if info_modelo and info_modelo.get("contextWindow"):
                    usage = evento.get("usage") or {}
                    tokens_turno = (
                        usage.get("input_tokens", 0)
                        + usage.get("cache_creation_input_tokens", 0)
                        + usage.get("cache_read_input_tokens", 0)
                    )
                    sesion.uso_contexto_pct = tokens_turno / info_modelo["contextWindow"]
                if sesion.estado not in ESTADOS_FINALES:
                    # is_error (p. ej. modelo inválido, 404) es un fallo real de la
                    # sesión, no una simple espera — se distingue de "?" en el texto.
                    if evento.get("is_error"):
                        sesion.estado = "error"
                    else:
                        sesion.estado = "pregunta" if "?" in texto else "esperando"

            if on_evento is not None:
                # Se pasa un resumen ya útil (estado + última entrada nueva del
                # transcript, si la hubo), no el evento NDJSON crudo: quien escuche
                # (el HUD, Fase 2) no tiene que reimplementar este parseo.
                nueva = sesion.transcript[-1] if len(sesion.transcript) > antes else None
                try:
                    on_evento(sesion.nombre, {"estado": sesion.estado, "entrada": nueva})
                except Exception:
                    pass  # el callback de la Fase 2 nunca debe tumbar el lector.
    finally:
        codigo = proc.poll()
        if sesion.estado != "terminado":
            # El proceso murió sin que lo pidiéramos nosotros: fallo explícito.
            sesion.estado = "error"
            stderr_rest = ""
            try:
                if proc.stderr is not None:
                    stderr_rest = proc.stderr.read().strip()
            except Exception:
                pass
            msg = f"La sesión terminó sola (código {codigo})."
            if stderr_rest:
                msg += f" stderr: {stderr_rest[:500]}"
            sesion._log("sistema", msg)


def _crear_worktree(carpeta: Path, nombre: str) -> tuple[Path, str]:
    """Crea un git worktree aislado para la sesión `nombre` sobre `carpeta` (el repo
    real). Devuelve (ruta_worktree, rama). Falla explícito (§1) si `carpeta` no es un
    repo git o si `git worktree add` falla (p. ej. rama duplicada)."""
    comprobar = subprocess.run(
        ["git", "-C", str(carpeta), "rev-parse", "--is-inside-work-tree"],
        capture_output=True, text=True,
    )
    if comprobar.returncode != 0:
        raise SesionesError(
            f"{carpeta} no es un repositorio git; no puedo crear un worktree ahí."
        )

    rama = f"cristopher/{nombre}"
    destino = SUBAGENTS / f"worktree-{nombre}"
    if destino.exists():
        raise SesionesError(
            f"Ya existe una carpeta de worktree en {destino}; elige otro nombre de "
            "sesión o límpiala a mano."
        )
    destino.parent.mkdir(parents=True, exist_ok=True)
    resultado = subprocess.run(
        ["git", "-C", str(carpeta), "worktree", "add", str(destino), "-b", rama],
        capture_output=True, text=True,
    )
    if resultado.returncode != 0:
        raise SesionesError(
            "No pude crear el worktree: "
            f"{resultado.stderr.strip() or resultado.stdout.strip()}"
        )
    return destino, rama


class GestorSesiones:
    """Crea, alimenta, observa y para sesiones vivas de Claude Code."""

    def __init__(self) -> None:
        self._sesiones: dict[str, Sesion] = {}
        self._lock = threading.Lock()
        self._on_evento: Optional[OnEvento] = None

    def set_on_evento(self, callback: Optional[OnEvento]) -> None:
        """La Fase 2 registra aquí un callback para republicar eventos al HUD."""
        self._on_evento = callback

    def crear(
        self, nombre: str, ruta: str, tarea: str, aislamiento: str = "directo",
        tipo: str = "code", modelo: str = "sonnet", effort: str = "",
        quality_gate: str = "",
    ) -> str:
        """Crea una sesión. `tipo`:
        - "code" (por defecto): sobre un proyecto real (`ruta`), directo o en
          worktree — comportamiento de Fases 1-2, sin cambios.
        - "chat" (Fase 4a): para idear/conversar, sin proyecto. Ignora `ruta` y
          `aislamiento`; trabaja en una carpeta scratch aislada bajo
          workspace/subagents/, con permission-mode seguro (confirmado en Paso 0
          de la Fase 4a: NO toca disco, no se cuelga esperando una aprobación
          imposible en modo headless).
        `modelo` ("" = el que decida `claude` por defecto) y `effort` ("" = sin
        forzar) se pasan tal cual al CLI (verificados en Paso 0: --model acepta
        alias o nombre completo; --effort acepta low/medium/high/xhigh/max).
        `quality_gate` (solo tiene sentido con tipo="code"): comando de
        lint/typecheck propio del proyecto de ESA sesión; si no está vacío, se
        instala como hook PostToolUse (Write/Edit) exclusivo de esta sesión.
        """
        nombre = (nombre or "").strip()
        if not nombre:
            raise SesionesError("Falta el nombre de la sesión.")
        tarea = (tarea or "").strip()
        if not tarea:
            raise SesionesError("Falta la tarea a encargarle a la sesión.")
        tipo = (tipo or "code").strip().lower()
        if tipo not in ("code", "chat"):
            raise SesionesError(f"tipo {tipo!r} no válido. Usa 'code' o 'chat'.")
        aislamiento = (aislamiento or "directo").strip().lower()
        if aislamiento not in ("directo", "worktree"):
            raise SesionesError(
                f"aislamiento {aislamiento!r} no válido. Usa 'directo' o 'worktree'."
            )

        proyecto_base: Path
        if tipo == "chat":
            # Sin proyecto real: carpeta scratch propia, mismo patrón de aislamiento
            # que delegate.py::delegar_a_claude. `ruta`/`aislamiento` no aplican.
            proyecto_base = SUBAGENTS / f"chat-{nombre}"
            proyecto_base.mkdir(parents=True, exist_ok=True)
        else:
            proyecto_base = Path(ruta or "").expanduser().resolve()
            if not proyecto_base.exists():
                raise SesionesError(f"No encuentro esa carpeta de proyecto: {proyecto_base}")
            if not proyecto_base.is_dir():
                raise SesionesError(f"{proyecto_base} no es una carpeta.")

        exe = shutil.which("claude")
        if not exe:
            raise SesionesError(
                "No encuentro el ejecutable 'claude' en el PATH. Instala Claude Code "
                "o revisa la instalación antes de crear sesiones."
            )

        with self._lock:
            existente = self._sesiones.get(nombre)
            if existente is not None and existente.estado not in ESTADOS_FINALES:
                raise SesionesError(
                    f"Ya hay una sesión activa llamada '{nombre}' (estado: "
                    f"{existente.estado}). Usa otro nombre o párala primero."
                )

            worktree_branch = None
            carpeta = proyecto_base
            if tipo == "code" and aislamiento == "worktree":
                carpeta, worktree_branch = _crear_worktree(proyecto_base, nombre)

            session_id = str(uuid.uuid4())
            permission_mode = "bypassPermissions" if tipo == "code" else SESION_CHAT_PERMISSION_MODE
            cmd = [
                exe, "-p",
                "--output-format", "stream-json",
                "--input-format", "stream-json",
                "--verbose",
                "--permission-mode", permission_mode,
                "--add-dir", str(carpeta),
                "--session-id", session_id,
            ]
            if modelo:
                cmd += ["--model", modelo]
            if effort:
                cmd += ["--effort", effort]
            if quality_gate and tipo == "code":
                # El JSON de hooks vive en SUBAGENTS (nunca dentro de `carpeta`: en
                # aislamiento "directo" esa es la carpeta REAL del usuario, y no hay
                # que ensuciarla con un archivo de configuración nuestro).
                settings_path = SUBAGENTS / f"hooks-{nombre}.json"
                settings_path.parent.mkdir(parents=True, exist_ok=True)
                settings_path.write_text(
                    json.dumps({
                        "hooks": {
                            "PostToolUse": [{
                                "matcher": "Write|Edit",
                                "hooks": [{"type": "command", "command": quality_gate}],
                            }]
                        }
                    }, ensure_ascii=False),
                    encoding="utf-8",
                )
                cmd += ["--settings", str(settings_path)]
            try:
                proc = subprocess.Popen(
                    cmd,
                    cwd=str(carpeta),
                    env=_entorno_sub_agente(),
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    bufsize=1,
                )
            except Exception as exc:
                raise SesionesError(f"No pude lanzar la sesión: {exc}") from exc

            sesion = Sesion(
                nombre, carpeta, proc, session_id,
                proyecto_base=proyecto_base, worktree_branch=worktree_branch,
                tipo=tipo, modelo=modelo,
            )
            self._sesiones[nombre] = sesion

            hilo = threading.Thread(
                target=_leer_stdout, args=(sesion, self._on_evento), daemon=True
            )
            hilo.start()

            if worktree_branch:
                sesion._log(
                    "sistema",
                    f"Worktree aislado en {carpeta} (rama {worktree_branch}). Lo "
                    "gitignorado (.venv/node_modules/...) no viaja aquí: instala "
                    "dependencias si la tarea las necesita. Si levanta un servidor, "
                    "vigila conflictos de puerto con otras sesiones. Al parar la "
                    "sesión el worktree NO se borra solo — usa sesion_limpiar_worktree "
                    "cuando quieras borrarlo.",
                )

            sesion.enviar(tarea)

        return f"Sesión '{nombre}' creada sobre {carpeta} (session_id {session_id})."

    def escribir(self, nombre: str, mensaje: str) -> str:
        sesion = self._get(nombre)
        mensaje = (mensaje or "").strip()
        if not mensaje:
            raise SesionesError("El mensaje a enviar está vacío.")
        sesion.enviar(mensaje)
        return f"Mensaje enviado a la sesión '{nombre}'."

    def listar(self) -> list[dict]:
        with self._lock:
            sesiones = list(self._sesiones.values())
        return [
            {
                "nombre": s.nombre,
                "ruta": str(s.ruta),
                "estado": s.estado,
                "ultima_actividad": datetime.fromtimestamp(
                    s.ultima_actividad
                ).isoformat(timespec="seconds"),
                "tipo": s.tipo,
                "modelo": s.modelo_resuelto or s.modelo,
                "uso_contexto_pct": s.uso_contexto_pct,
            }
            for s in sesiones
        ]

    def leer(self, nombre: str, n: int = 20) -> list[dict]:
        sesion = self._get(nombre)
        n = max(1, int(n))
        return list(sesion.transcript)[-n:]

    def parar(self, nombre: str) -> str:
        sesion = self._get(nombre)
        if sesion.estado in ESTADOS_FINALES:
            return f"La sesión '{nombre}' ya estaba {sesion.estado}."
        sesion.estado = "terminado"
        try:
            sesion.proc.terminate()
            sesion.proc.wait(timeout=SESION_STOP_TIMEOUT)
        except subprocess.TimeoutExpired:
            sesion.proc.kill()
            sesion.proc.wait(timeout=SESION_STOP_TIMEOUT)
        except Exception as exc:
            sesion._log("sistema", f"Error al parar: {exc}")
        if sesion.worktree_branch:
            # Nunca se borra solo (protocolo de seguridad: nunca tirar cambios sin
            # commitear sin que el usuario lo pida explícito). Ver limpiar_worktree.
            sesion._log(
                "sistema",
                f"Worktree conservado en {sesion.ruta} (rama {sesion.worktree_branch}). "
                "No se borra solo; usa sesion_limpiar_worktree si quieres eliminarlo.",
            )
        return f"Sesión '{nombre}' terminada."

    def diff(self, nombre: str) -> str:
        """Diff (git diff, árbol de trabajo vs HEAD) de lo que ha cambiado la sesión
        en su carpeta. Es DATO para que CRISTOPHER o el usuario lo revisen — nunca una
        orden (§8). Falla explícito si la carpeta no es un repo git (§1)."""
        sesion = self._get(nombre)
        resultado = subprocess.run(
            ["git", "diff"], cwd=str(sesion.ruta), capture_output=True, text=True,
        )
        if resultado.returncode != 0:
            raise SesionesError(
                f"No pude obtener el diff de '{nombre}': "
                f"{resultado.stderr.strip() or 'no es un repositorio git'}"
            )
        return resultado.stdout

    def archivos_tocados(self, nombre: str) -> list[str]:
        """Rutas (relativas) que la sesión ha tocado, vía `git diff --name-only`.
        Tolerante (lista vacía si la carpeta no es un repo git o no hay cambios): a
        diferencia de `diff()`, esto solo alimenta la detección de conflictos, no es
        una operación crítica que deba fallar ruidosamente."""
        sesion = self._get(nombre)
        resultado = subprocess.run(
            ["git", "diff", "--name-only"],
            cwd=str(sesion.ruta), capture_output=True, text=True,
        )
        if resultado.returncode != 0:
            return []
        return [linea for linea in resultado.stdout.splitlines() if linea.strip()]

    def conflictos(self) -> list[dict]:
        """Pares de sesiones ACTIVAS que comparten proyecto_base y tocan al menos un
        archivo en común — aviso antes de mergear. Calculado (sin LLM)."""
        with self._lock:
            activas = [s for s in self._sesiones.values() if s.estado in ESTADOS_ACTIVOS]
        archivos_por_sesion = {
            s.nombre: set(self.archivos_tocados(s.nombre)) for s in activas
        }
        resultado = []
        for i, a in enumerate(activas):
            for b in activas[i + 1:]:
                if a.proyecto_base != b.proyecto_base:
                    continue
                comunes = archivos_por_sesion[a.nombre] & archivos_por_sesion[b.nombre]
                if comunes:
                    resultado.append({
                        "sesion_a": a.nombre, "sesion_b": b.nombre,
                        "archivos": sorted(comunes),
                    })
        return resultado

    def limpiar_worktree(self, nombre: str) -> str:
        """Borra el checkout del worktree de una sesión YA PARADA. Nunca borra la
        rama (podría tener commits que el usuario quiera revisar/mergear) y nunca
        fuerza el borrado: si git detecta cambios sin commitear se niega, y ese
        mensaje se propaga tal cual (§1, protocolo de seguridad)."""
        sesion = self._get(nombre)
        if not sesion.worktree_branch:
            raise SesionesError(f"La sesión '{nombre}' no usa un worktree aislado.")
        if sesion.estado not in ESTADOS_FINALES:
            raise SesionesError(
                f"La sesión '{nombre}' sigue activa (estado: {sesion.estado}); "
                "párala antes de limpiar su worktree."
            )
        resultado = subprocess.run(
            ["git", "-C", str(sesion.proyecto_base), "worktree", "remove", str(sesion.ruta)],
            capture_output=True, text=True,
        )
        if resultado.returncode != 0:
            raise SesionesError(
                f"No pude borrar el worktree de '{nombre}' (¿cambios sin commitear?): "
                f"{resultado.stderr.strip() or resultado.stdout.strip()}"
            )
        return (
            f"Worktree de '{nombre}' borrado ({sesion.ruta}). La rama "
            f"'{sesion.worktree_branch}' se conserva por si quieres revisarla o mergearla."
        )

    def _get(self, nombre: str) -> Sesion:
        with self._lock:
            sesion = self._sesiones.get(nombre)
        if sesion is None:
            raise SesionesError(f"No existe ninguna sesión llamada '{nombre}'.")
        return sesion

    def _terminar_todas(self) -> None:
        with self._lock:
            sesiones = list(self._sesiones.values())
        for s in sesiones:
            if s.estado not in ESTADOS_FINALES:
                try:
                    s.proc.terminate()
                except Exception:
                    pass


# --- Singleton perezoso -------------------------------------------------------
_GESTOR: Optional[GestorSesiones] = None
_GESTOR_LOCK = threading.Lock()


def get_gestor_sesiones() -> GestorSesiones:
    global _GESTOR
    if _GESTOR is None:
        with _GESTOR_LOCK:
            if _GESTOR is None:
                _GESTOR = GestorSesiones()
                import atexit
                atexit.register(_GESTOR._terminar_todas)
    return _GESTOR
