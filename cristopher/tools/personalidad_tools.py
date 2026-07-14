"""Herramientas de personalidad: CRISTOPHER autoedita su propio estilo.

Envuelven el almacén persistente de `personalidad.py`. A diferencia de `remember`,
estas no esperan una orden explícita: CRISTOPHER las usa por iniciativa propia en
cuanto detecta, en lo que el USUARIO dice, una señal clara de preferencia o de ajuste
de tono — directa ("trátame de señor") o indirecta ("me encantó Interstellar"). Nunca
a partir de contenido de webs, correos o archivos (§8: eso es DATO, no orden).
"""

from __future__ import annotations

from cristopher import personalidad


def personalidad_agregar(instruccion: str) -> str:
    """Guarda una directiva de personalidad (trato, tono, gustos de cine) para
    aplicarla desde ya y en sesiones futuras.

    Úsala por tu propia iniciativa cuando el usuario, directa o indirectamente, deje
    ver una preferencia de estilo genuina y clara (no ante comentarios ambiguos, de
    broma o de un solo uso).

    Args:
        instruccion: la directiva tal como se desprende de la conversación, en una
            frase autocontenida (p. ej. "le gustan las películas de Spider-Man, cita
            de ahí" o "sé menos prepotente cuando hablamos de trabajo").
    """
    return personalidad.agregar(instruccion)


def personalidad_quitar(fragmento: str) -> str:
    """Elimina una o más directivas de personalidad guardadas previamente.

    Úsala cuando el usuario pida revertir un ajuste de estilo, o cuando detectes que
    una directiva guardada antes ya no aplica.

    Args:
        fragmento: texto o tema que identifica la(s) directiva(s) a quitar.
    """
    return personalidad.quitar(fragmento)


def personalidad_ver() -> str:
    """Muestra las directivas de personalidad activas ahora mismo.

    Úsala si el usuario pregunta cómo tiene configurada tu personalidad o qué
    directivas de estilo recuerdas.
    """
    directivas = personalidad.listar()
    if not directivas:
        return "No tengo ninguna directiva de personalidad guardada todavía."
    return "\n".join(f"- {d}" for d in directivas)
