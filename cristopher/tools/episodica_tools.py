"""Herramientas de memoria episódica: registrar y consultar la línea de tiempo.

Registro selectivo (`episodio_registrar`) y consulta por periodo
(`episodios_periodo`, con parseo de rangos en lenguaje natural o ISO) y borrado
(`episodio_borrar`). Persistencia en el SQLite existente vía `cristopher.episodica`.
Funciones finas que devuelven texto legible (observación para el bucle ReAct); los
errores se devuelven como texto, nunca se ocultan. El razonamiento sobre lo devuelto
(contar, comparar, narrar) lo hace el modelo — aquí no hay agregación ni estadística
(MVP, ver PROMPT_memoria_episodica_para_claude_code.md).
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta

from cristopher.episodica import get_episodica

_RE_ULTIMOS_DIAS = re.compile(r"^ultimos?\s+(\d+)\s+dias?$")
_RE_FECHA_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_RE_RANGO_ISO = re.compile(r"^(\d{4}-\d{2}-\d{2}):(\d{4}-\d{2}-\d{2})$")

_INICIO_DIA = "00:00:00"
_FIN_DIA = "23:59:59"


def _quitar_acentos(s: str) -> str:
    tabla = str.maketrans("áéíóúñ", "aeioun")
    return s.translate(tabla)


def _dia(fecha: datetime, hora: str) -> str:
    return f"{fecha.date().isoformat()}T{hora}"


def _resolver_periodo(periodo: str) -> tuple[str, str]:
    """Convierte un periodo en lenguaje natural o ISO a (desde, hasta) en ISO.

    Lanza ValueError con mensaje claro si no reconoce el periodo.
    """
    crudo = (periodo or "").strip()
    norm = _quitar_acentos(crudo.lower())
    hoy = datetime.now()

    if norm == "hoy":
        return _dia(hoy, _INICIO_DIA), _dia(hoy, _FIN_DIA)

    if norm == "ayer":
        ayer = hoy - timedelta(days=1)
        return _dia(ayer, _INICIO_DIA), _dia(ayer, _FIN_DIA)

    if norm == "esta semana":
        lunes = hoy - timedelta(days=hoy.weekday())
        return _dia(lunes, _INICIO_DIA), _dia(hoy, _FIN_DIA)

    if norm == "semana pasada":
        lunes_esta_semana = hoy - timedelta(days=hoy.weekday())
        lunes_pasado = lunes_esta_semana - timedelta(days=7)
        domingo_pasado = lunes_pasado + timedelta(days=6)
        return _dia(lunes_pasado, _INICIO_DIA), _dia(domingo_pasado, _FIN_DIA)

    if norm == "este mes":
        primero = hoy.replace(day=1)
        return _dia(primero, _INICIO_DIA), _dia(hoy, _FIN_DIA)

    m = _RE_ULTIMOS_DIAS.match(norm)
    if m:
        n = int(m.group(1))
        desde = hoy - timedelta(days=n)
        return _dia(desde, _INICIO_DIA), _dia(hoy, _FIN_DIA)

    m = _RE_RANGO_ISO.match(crudo)
    if m:
        return f"{m.group(1)}T{_INICIO_DIA}", f"{m.group(2)}T{_FIN_DIA}"

    if _RE_FECHA_ISO.match(crudo):
        return f"{crudo}T{_INICIO_DIA}", f"{crudo}T{_FIN_DIA}"

    raise ValueError(
        f"no entendí el periodo {periodo!r}. Usa 'hoy', 'ayer', 'esta semana', "
        "'semana pasada', 'este mes', 'últimos N días', una fecha ISO "
        "(2026-07-15) o un rango ISO (2026-07-01:2026-07-15)."
    )


def _fecha_legible(ts: str) -> str:
    """'2026-07-15T09:30:00' -> '2026-07-15 09:30' (tolera formatos raros)."""
    return (ts or "").replace("T", " ")[:16]


def episodio_registrar(resumen: str, tipo: str = "evento", entidad: str = "") -> str:
    """Registra un episodio notable (algo que pasó, con hora).

    Args:
        resumen: qué pasó, en una frase.
        tipo: categoría libre ('conversacion', 'herramienta', 'aviso', 'evento'...).
        entidad: con quién o sobre qué, opcional (persona, app, tema).
    """
    resumen = (resumen or "").strip()
    if not resumen:
        return "No hay ningún episodio que registrar (resumen vacío)."
    eid = get_episodica().registrar(resumen, tipo=tipo or "evento", entidad=entidad or None)
    return f"Episodio #{eid} registrado ({tipo or 'evento'}): {resumen}"


def episodios_periodo(periodo: str, tipo: str = "", entidad: str = "") -> str:
    """Devuelve la línea de tiempo de episodios en un periodo, para responder
    preguntas temporales o de patrón ('¿qué hice esta semana?', '¿con quién he
    hablado más este mes?'). El conteo/comparación lo haces tú sobre lo devuelto.

    Args:
        periodo: 'hoy', 'ayer', 'esta semana', 'semana pasada', 'este mes',
            'últimos N días', una fecha ISO (2026-07-15) o un rango ISO
            (2026-07-01:2026-07-15).
        tipo: filtra por tipo de episodio, opcional.
        entidad: filtra por entidad (persona/app/tema), opcional.
    """
    try:
        desde, hasta = _resolver_periodo(periodo)
    except ValueError as exc:
        return f"ERROR: {exc}"

    limit = 200
    filas = get_episodica().periodo(desde, hasta, tipo=tipo or None, entidad=entidad or None, limit=limit)
    if not filas:
        return f"No hay episodios registrados en el periodo {periodo!r}."

    lineas = []
    for _id, ts, tipo_fila, entidad_fila, resumen in filas:
        etiqueta = f"[{tipo_fila}]" + (f" ({entidad_fila})" if entidad_fila else "")
        lineas.append(f"- {_fecha_legible(ts)} {etiqueta} — {resumen}")

    salida = "\n".join(lineas)
    if len(filas) == limit:
        salida += f"\n... (tope de {limit} alcanzado, acota el rango para ver más)"
    return salida


def episodio_borrar(id: int) -> str:
    """Borra un episodio directamente por su id.

    Args:
        id: id del episodio a borrar.
    """
    resumen = get_episodica().borrar(id)
    if resumen is None:
        return f"No hay ningún episodio con id #{id}."
    return f"Episodio #{id} borrado: {resumen}"
