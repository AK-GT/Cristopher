"""Herramientas de modo diario (reflexión diaria conversacional).

Permiten a CRISTOPHER entrar/salir del "modo diario" por INTENCIÓN del usuario (sin
keywords fijas), y categorizar lo que el usuario cuenta en su reflexión del día.
Persistencia en el SQLite existente vía `cristopher.diario`. Funciones finas que
devuelven texto legible (observación para el bucle ReAct); los errores se devuelven
como texto, nunca se ocultan.
"""

from __future__ import annotations

from cristopher import estado_diario
from cristopher.diario import CATEGORIAS, get_diario

_LABELS = {
    "evento_social": "Eventos sociales",
    "dilema": "Dilemas",
    "hecho_destacado": "Hechos destacados",
    "tarea_futura": "Tareas a futuro",
    "libre": "Libre",
}


def _fecha(creado: str) -> str:
    """'2026-07-15T09:30:00' -> '2026-07-15 09:30' (tolera formatos raros)."""
    return (creado or "").replace("T", " ")[:16]


def activar_modo_diario() -> str:
    """Activa el modo diario: a partir de ahora te comportas como un oyente activo y
    humano en una conversación de reflexión diaria — preguntas, indagas, das tu
    opinión, no solo transcribes. Úsalo cuando detectes que el usuario quiere hacer
    su diario de hoy ('quiero hacer mi diario', 'hablemos de mi día', 'te cuento cómo
    me fue'). Si no está claro, pregúntale antes en lugar de activarlo."""
    estado_diario.activar()
    return "Modo diario activado: te escucho."


def desactivar_modo_diario() -> str:
    """Desactiva el modo diario y vuelve al comportamiento normal. Úsalo cuando el
    usuario dé la conversación de diario por terminada ('ya terminé por hoy', 'eso es
    todo', 'gracias por escuchar')."""
    estado_diario.desactivar()
    return "Modo diario desactivado."


def diario_agregar(categoria: str, texto: str) -> str:
    """Guarda algo que el usuario contó en su diario de hoy, bajo una categoría.

    Categorías válidas:
    - evento_social: algo social que vivió (quedó con alguien, una fiesta, una llamada...).
    - dilema: algo que le cuesta decidir o le preocupa.
    - hecho_destacado: algo que pasó y quiere que quede anotado (bueno o malo).
    - tarea_futura: algo pendiente que mencionó de pasada. IMPORTANTE: antes de
      llamar a esta función con esta categoría, pregúntale si además quiere que lo
      añadas a su lista real de tareas pendientes; solo si dice que sí, llama también
      a tarea_crear con ese texto — nunca la llames sin que el usuario lo confirme.
    - libre: cualquier otra cosa que quiera contar y no encaje en las anteriores.

    Args:
        categoria: una de evento_social, dilema, hecho_destacado, tarea_futura, libre.
        texto: qué contó el usuario, en una o dos frases.
    """
    categoria = (categoria or "").strip().lower()
    if categoria not in CATEGORIAS:
        return (
            f"ERROR: categoría {categoria!r} no válida. Usa una de: "
            + ", ".join(CATEGORIAS)
        )
    texto = (texto or "").strip()
    if not texto:
        return "No hay nada que apuntar (texto vacío)."
    eid = get_diario().agregar(categoria, texto)
    return f"Entrada #{eid} apuntada en '{_LABELS[categoria]}': {texto}"


def diario_ver_hoy() -> str:
    """Muestra lo que el usuario ha contado hoy en su diario, agrupado por categoría.
    Úsala si pregunta qué ha apuntado hoy o quieres repasar antes de cerrar la
    conversación."""
    from datetime import date

    hoy = date.today().isoformat()
    rows = get_diario().entradas_de_fecha(hoy)
    if not rows:
        return "Aún no has hecho tu diario de hoy."

    por_categoria: dict[str, list[tuple[str, str]]] = {c: [] for c in CATEGORIAS}
    for _id, categoria, texto, creado in rows:
        por_categoria.setdefault(categoria, []).append((texto, creado))

    bloques = []
    for categoria in CATEGORIAS:
        entradas = por_categoria.get(categoria) or []
        if not entradas:
            continue
        lineas = "\n".join(f"  - {texto} ({_fecha(creado)})" for texto, creado in entradas)
        bloques.append(f"{_LABELS[categoria]}:\n{lineas}")
    return "\n\n".join(bloques)
