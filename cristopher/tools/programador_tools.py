"""Herramientas del sistema de programación de tareas.

A diferencia de crear_recordatorio (que solo genera un AVISO de texto),
programar_tarea encola una instrucción que se EJECUTA de verdad con el turno
completo del agente (todas sus tools) cuando toca — a una hora, encadenada tras
otra tarea del mismo grupo, o esperando a que una sesión de Claude Code termine.
"""

from __future__ import annotations

from cristopher.programador import get_programador
from cristopher.tools.recordatorio_tools import parse_cuando_a_datetime


def programar_tarea(texto: str, cuando: str = "", grupo: str = "", espera_sesion: str = "") -> str:
    """Programa una instrucción para que CRISTOPHER la EJECUTE de verdad más tarde,
    con todas sus herramientas (WhatsApp, correo, navegador, sesiones de código...).

    A diferencia de crear_recordatorio (que solo avisa con texto), esto ejecuta la
    instrucción como si el usuario la hubiera escrito en ese momento.

    Para encadenar varias fases de un plan ("haz la fase 1, y al terminar la fase 2,
    etc., sin que te lo pida"), llama a este tool una vez por fase usando el MISMO
    `grupo`: cada llamada se encola como el siguiente paso y no se dispara hasta que
    el paso anterior de ese grupo termine.

    Para "lanza la sesión de Claude Code X y no sigas hasta que termine", el paso que
    lanza la sesión (vía sesion_crear) debe ir seguido de otro paso (mismo grupo o
    suelto) con `espera_sesion` puesto al nombre de esa sesión.

    Args:
        texto: instrucción completa a ejecutar, tal cual se la darías en el chat.
        cuando: opcional — 'HH:MM', 'en N minutos', 'en N horas' o ISO. Vacío = no
            depende de una hora concreta (se dispara en cuanto se cumplan las demás
            condiciones, o de inmediato si no hay ninguna).
        grupo: opcional — nombre del plan/cadena. Repite el mismo nombre en llamadas
            sucesivas para encadenar fases en orden.
        espera_sesion: opcional — nombre de una sesión de Claude Code (sesion_crear)
            que debe llegar a 'terminado' o 'error' antes de disparar este paso.
    """
    texto = (texto or "").strip()
    if not texto:
        return "No hay ninguna instrucción que programar (texto vacío)."
    cuando_iso = None
    if cuando.strip():
        try:
            cuando_iso = parse_cuando_a_datetime(cuando).isoformat(timespec="seconds")
        except ValueError as exc:
            return f"ERROR: {exc}"
    pid = get_programador().crear(
        texto,
        cuando_iso=cuando_iso,
        grupo=grupo.strip() or None,
        espera_sesion=espera_sesion.strip() or None,
    )
    detalle = []
    if cuando_iso:
        detalle.append(f"se disparará a partir de {cuando_iso.replace('T', ' ')}")
    if grupo.strip():
        detalle.append(f"paso del grupo '{grupo.strip()}'")
    if espera_sesion.strip():
        detalle.append(f"espera a que termine la sesión '{espera_sesion.strip()}'")
    extra = f" ({', '.join(detalle)})" if detalle else " (se disparará en cuanto el demonio la recoja)"
    return f"Tarea #{pid} programada{extra}: {texto}"


def listar_programadas() -> str:
    """Lista las tareas programadas/encadenadas (pendientes, lanzadas, hechas, con error o canceladas)."""
    filas = get_programador().listar()
    if not filas:
        return "No hay tareas programadas."
    out = []
    for f in filas:
        partes = [f"#{f['id']}"]
        if f["grupo"]:
            partes.append(f"grupo '{f['grupo']}' paso {f['orden']}")
        if f["dispara_en_iso"]:
            partes.append(f"a las {f['dispara_en_iso'].replace('T', ' ')}")
        if f["espera_sesion"]:
            partes.append(f"espera sesión '{f['espera_sesion']}'")
        partes.append(f"[{f['estado']}]")
        out.append(f"{' · '.join(partes)} — {f['texto']}")
    return "\n".join(out)


def cancelar_programada(id: int) -> str:
    """Cancela una tarea programada por su número.

    Si es un paso intermedio de una cadena, los pasos siguientes de ese mismo grupo
    quedarán bloqueados esperándolo para siempre — avisa de eso si es el caso.

    Args:
        id: número de la tarea (el #N que aparece en listar_programadas).
    """
    prog = get_programador()
    filas = {f["id"]: f for f in prog.listar()}
    fila = filas.get(id)
    texto = prog.cancelar(id)
    if texto is None:
        return f"No encontré ninguna tarea programada #{id}."
    aviso = ""
    if fila and fila["grupo"]:
        hay_siguientes = any(
            f["grupo"] == fila["grupo"] and f["orden"] and fila["orden"] and f["orden"] > fila["orden"]
            and f["estado"] == "pendiente"
            for f in filas.values()
        )
        if hay_siguientes:
            aviso = f" Ojo: quedan pasos pendientes en el grupo '{fila['grupo']}' que esperaban a este — quedarán bloqueados."
    return f"Tarea #{id} cancelada: {texto}.{aviso}"
