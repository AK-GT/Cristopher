"""Herramientas de memoria: recordar y recuperar hechos (Fase 2).

Envuelven el almacén persistente `Memory`. Así CRISTOPHER puede guardar hechos que le
importan al usuario y recuperarlos en sesiones futuras.
"""

from __future__ import annotations

from cristopher.memory import get_memory


def remember(fact: str) -> str:
    """Guarda un hecho duradero en la memoria persistente para recordarlo en el futuro.

    Úsala cuando el usuario comparte algo que conviene recordar (preferencias, datos
    personales, decisiones, contexto de un proyecto).

    Args:
        fact: el hecho a recordar, en una frase clara y autocontenida.
    """
    return get_memory().remember(fact)


def recall(query: str) -> str:
    """Busca en la memoria persistente hechos relevantes para una consulta.

    Úsala cuando necesites recordar algo que el usuario te contó antes.

    Args:
        query: qué quieres recordar (tema o pregunta).
    """
    hits = get_memory().recall(query)
    if not hits:
        return "No recuerdo nada relevante sobre eso."
    return "\n".join(f"- {h}" for h in hits)
