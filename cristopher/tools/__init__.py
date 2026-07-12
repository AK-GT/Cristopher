"""Registro declarativo de herramientas de CRISTOPHER.

`TOOLS` es la ÚNICA fuente de verdad: de aquí sale tanto la declaración que se le
pasa a Gemini (function calling) como el dispatch por nombre. Añadir una herramienta
= añadir una entrada aquí. Esto prepara el terreno para el "registro auto-generado"
de la Fase 2, para que CRISTOPHER nunca mienta sobre lo que puede hacer.

Cada entrada:
  - name:        nombre que ve el modelo.
  - description: qué hace y cuándo usarla.
  - parameters:  JSON Schema (tipo OpenAPI) de los argumentos.
  - fn:          callable de Python que la ejecuta.
"""

from __future__ import annotations

from typing import Any, Callable

from cristopher.tools.delegate import delegar_a_claude
from cristopher.tools.elite_search import busqueda_elite
from cristopher.tools.google_tools import buscar_correos, enviar_correo, proximo_evento
from cristopher.tools.memory_tools import recall, remember
from cristopher.tools.read_file import read_file
from cristopher.tools.shell import run_shell
from cristopher.tools.web_search import web_search

TOOLS: list[dict[str, Any]] = [
    {
        "name": "web_search",
        "description": (
            "Busca información en la web (DuckDuckGo). Úsala para datos actuales, "
            "documentación o para localizar recursos. Devuelve título, URL y resumen."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Términos de búsqueda."},
                "max_results": {
                    "type": "integer",
                    "description": "Número de resultados (1-10). Por defecto 5.",
                },
            },
            "required": ["query"],
        },
        "fn": web_search,
    },
    {
        "name": "run_shell",
        "description": (
            "Ejecuta un comando de shell en el directorio de trabajo y devuelve "
            "stdout, stderr y el código de salida. Úsala para 'git clone', listar "
            "archivos, ejecutar scripts de Python, etc. Herramienta potente."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "Comando a ejecutar, p. ej. 'git clone <url> repo'.",
                },
                "timeout": {
                    "type": "integer",
                    "description": "Segundos máximos antes de abortar. Por defecto 120.",
                },
            },
            "required": ["command"],
        },
        "fn": run_shell,
    },
    {
        "name": "read_file",
        "description": (
            "Lee un archivo de texto del disco y devuelve su contenido. Rutas "
            "relativas se resuelven dentro del directorio de trabajo (workspace/). "
            "Úsala para inspeccionar README, código fuente o archivos de config."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Ruta del archivo a leer."},
                "max_bytes": {
                    "type": "integer",
                    "description": "Máximo de bytes a leer. Por defecto 40000.",
                },
            },
            "required": ["path"],
        },
        "fn": read_file,
    },
    {
        "name": "remember",
        "description": (
            "Guarda un hecho duradero en la memoria persistente para recordarlo en "
            "sesiones futuras. Úsala cuando el usuario comparte preferencias, datos "
            "personales, decisiones o contexto que conviene no olvidar."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "fact": {
                    "type": "string",
                    "description": "El hecho a recordar, en una frase autocontenida.",
                },
            },
            "required": ["fact"],
        },
        "fn": remember,
    },
    {
        "name": "recall",
        "description": (
            "Busca en la memoria persistente hechos relevantes para una consulta. "
            "Úsala cuando necesites recordar algo que el usuario te contó antes."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Qué quieres recordar (tema o pregunta).",
                },
            },
            "required": ["query"],
        },
        "fn": recall,
    },
    {
        "name": "delegar_a_claude",
        "description": (
            "Delega una tarea de código a un sub-agente Claude Code que trabaja de "
            "forma autónoma dentro de una carpeta aislada (puede crear/editar archivos "
            "y ejecutar comandos ahí). Úsala para tareas de código acotadas que puedes "
            "encargar en paralelo. Después revisa e integra el resultado con "
            "read_file/run_shell sobre la carpeta que devuelve."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "tarea": {
                    "type": "string",
                    "description": "Instrucción acotada y clara para el sub-agente.",
                },
                "carpeta": {
                    "type": "string",
                    "description": "Nombre de la carpeta de trabajo aislada (opcional).",
                },
            },
            "required": ["tarea"],
        },
        "fn": delegar_a_claude,
    },
    {
        "name": "busqueda_elite",
        "description": (
            "Búsqueda web de élite con síntesis y fuentes (Tavily; cae a DuckDuckGo si "
            "no hay key). Úsala cuando necesites investigar un tema y dar un resumen "
            "con enlaces a las fuentes. Mejor que web_search para preguntas de fondo."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Tema o pregunta a investigar."},
                "max_results": {"type": "integer", "description": "Nº de fuentes (1-10)."},
            },
            "required": ["query"],
        },
        "fn": busqueda_elite,
    },
    {
        "name": "proximo_evento",
        "description": (
            "Lee los próximos eventos del Google Calendar del usuario (fecha/hora, "
            "título, lugar). Úsala para saber qué tiene agendado."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "n": {"type": "integer", "description": "Cuántos eventos próximos (por defecto 1)."},
            },
            "required": [],
        },
        "fn": proximo_evento,
    },
    {
        "name": "buscar_correos",
        "description": (
            "Lee correos de Gmail: recientes o los que casan una consulta estilo Gmail "
            "(p. ej. 'is:unread from:x@y.com'). Devuelve remitente, asunto, fecha y "
            "resumen. Solo lectura."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Consulta Gmail; vacío = recientes."},
                "n": {"type": "integer", "description": "Nº de correos (por defecto 5)."},
            },
            "required": [],
        },
        "fn": buscar_correos,
    },
    {
        "name": "enviar_correo",
        "description": (
            "Envía un correo por Gmail. SIEMPRE pide confirmación del usuario antes de "
            "enviar (acción irreversible). Redacta el borrador y úsala; el usuario "
            "confirmará o cancelará el envío."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "to": {"type": "string", "description": "Destinatario."},
                "subject": {"type": "string", "description": "Asunto."},
                "body": {"type": "string", "description": "Cuerpo del mensaje."},
            },
            "required": ["to", "subject", "body"],
        },
        "fn": enviar_correo,
    },
]

# Índice nombre -> callable, para el dispatch del bucle.
_BY_NAME: dict[str, Callable[..., str]] = {t["name"]: t["fn"] for t in TOOLS}


def call_tool(name: str, args: dict[str, Any]) -> str:
    """Ejecuta la herramienta `name` con `args`. Errores se devuelven como texto
    (observación para el modelo), nunca se ocultan (§1 "fallos explícitos")."""
    fn = _BY_NAME.get(name)
    if fn is None:
        return f"ERROR: herramienta desconocida {name!r}."
    try:
        return str(fn(**args))
    except TypeError as exc:
        return f"ERROR: argumentos inválidos para {name}: {exc}"
    except Exception as exc:
        return f"ERROR ejecutando {name}: {exc}"
