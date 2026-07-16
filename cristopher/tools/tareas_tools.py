"""Herramientas de tareas pendientes (pendiente / en_proceso / hecho).

Crear, listar, cambiar de estado y borrar tareas del usuario. Persistencia en el
SQLite existente vía `cristopher.tareas`. Funciones finas que devuelven texto
legible (observación para el bucle ReAct); los errores se devuelven como texto,
nunca se ocultan.
"""

from __future__ import annotations

from cristopher.tareas import get_tareas

_ESTADOS_VALIDOS = ("pendiente", "en_proceso", "hecho")
_ICONOS = {"pendiente": "⏳", "en_proceso": "🔧"}


def _fecha(creado: str) -> str:
    """'2026-07-15T09:30:00' -> '2026-07-15 09:30' (tolera formatos raros)."""
    return (creado or "").replace("T", " ")[:16]


def tarea_crear(texto: str) -> str:
    """Apunta una tarea pendiente del usuario.

    Args:
        texto: qué hay que hacer, en una frase.
    """
    texto = (texto or "").strip()
    if not texto:
        return "No hay ninguna tarea que apuntar (texto vacío)."
    tid = get_tareas().crear(texto)
    return f"Tarea #{tid} apuntada (pendiente): {texto}"


def tarea_listar() -> str:
    """Lista las tareas activas (pendientes y en proceso)."""
    rows = get_tareas().listar()
    if not rows:
        return "No hay tareas pendientes."
    return "\n".join(
        f"{_ICONOS.get(estado, '•')} #{tid} [{estado}] · {_fecha(creado)} — {texto}"
        for tid, texto, estado, creado in rows
    )


def tarea_actualizar_estado(id: int, estado: str) -> str:
    """Cambia el estado de una tarea. Marcarla 'hecho' la elimina de la lista.

    Args:
        id: id de la tarea (el que aparece al listar).
        estado: nuevo estado — 'pendiente', 'en_proceso' o 'hecho'.
    """
    estado = (estado or "").strip().lower()
    if estado not in _ESTADOS_VALIDOS:
        return (
            f"ERROR: estado {estado!r} no válido. Usa uno de: "
            + ", ".join(_ESTADOS_VALIDOS)
        )
    texto = get_tareas().actualizar_estado(id, estado)
    if texto is None:
        return f"No hay ninguna tarea con id #{id}."
    if estado == "hecho":
        return f"Tarea #{id} completada y eliminada de la lista: {texto}"
    return f"Tarea #{id} pasó a '{estado}': {texto}"


def tarea_borrar(id: int) -> str:
    """Borra una tarea directamente por su id, sin marcarla como hecha.

    Args:
        id: id de la tarea a borrar (el que aparece al listar).
    """
    texto = get_tareas().borrar(id)
    if texto is None:
        return f"No hay ninguna tarea con id #{id}."
    return f"Tarea #{id} borrada: {texto}"
