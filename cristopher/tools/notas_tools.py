"""Herramientas de notas rápidas (Módulo D — utilidades, Tanda A).

Apuntar cosas al vuelo y consultarlas/borrarlas. Persistencia en el SQLite existente
vía `cristopher.notas`. Funciones finas que devuelven texto legible (observación para
el bucle ReAct); los errores se devuelven como texto, nunca se ocultan.
"""

from __future__ import annotations

from cristopher.notas import get_notas


def _fecha(creado: str) -> str:
    """'2026-07-15T09:30:00' -> '2026-07-15 09:30' (tolera formatos raros)."""
    return (creado or "").replace("T", " ")[:16]


def apuntar(texto: str) -> str:
    """Apunta una nota rápida.

    Args:
        texto: qué apuntar, en una frase.
    """
    texto = (texto or "").strip()
    if not texto:
        return "No hay nada que apuntar (texto vacío)."
    nid = get_notas().apuntar(texto)
    return f"Nota #{nid} apuntada: {texto}"


def listar_notas() -> str:
    """Lista todas las notas (más recientes primero)."""
    rows = get_notas().listar()
    if not rows:
        return "No hay notas."
    return "\n".join(f"#{nid} · {_fecha(creado)} — {texto}" for nid, texto, creado in rows)


def buscar_nota(consulta: str) -> str:
    """Busca notas que contengan un texto.

    Args:
        consulta: palabra o frase a buscar en las notas.
    """
    consulta = (consulta or "").strip()
    if not consulta:
        return "Dime qué buscar en las notas."
    rows = get_notas().buscar(consulta)
    if not rows:
        return f"Sin notas que casen con «{consulta}»."
    return "\n".join(f"#{nid} · {_fecha(creado)} — {texto}" for nid, texto, creado in rows)


def borrar_nota(id: int) -> str:
    """Borra una nota por su id.

    Args:
        id: id de la nota a borrar (el que aparece al listar).
    """
    texto = get_notas().borrar(id)
    if texto is None:
        return f"No hay ninguna nota con id #{id}."
    return f"Borrada la nota #{id}: {texto}"
