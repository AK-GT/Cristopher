"""Herramientas del Modo calendario (pantalla de agenda del HUD).

Calco de `redes_modo_tools.py`. Además de las dos tools explícitas, `mostrar()` es lo
que usan las tools de calendario de `google_tools.py` para abrir la pantalla ELLAS
MISMAS en la fecha que tocan: el modelo elige la tool por intención y la pantalla
viene incluida, sin gastar un paso más del bucle (cuota) en llamar a
activar_modo_calendario.
"""

from __future__ import annotations

from datetime import date, datetime

from cristopher import bus, pantalla


def mostrar(fecha: date | None = None, borrador: dict | None = None) -> None:
    """Abre la pantalla de calendario en `fecha` (hoy si no se da). Nunca debe tumbar
    la tool que la llama: si el HUD no está, el bus simplemente no tiene oyentes."""
    try:
        pantalla.activar("calendario")
        bus.calendario_foco((fecha or date.today()).isoformat(), borrador)
    except Exception:
        pass


def activar_modo_calendario(fecha: str = "") -> str:
    """Abre en el HUD la pantalla de calendario (semana/mes, huecos libres). Úsalo
    cuando el usuario quiera VER su calendario o agenda ('enséñame el calendario',
    'abre la agenda', 'quiero ver mi semana') sin pedir un dato concreto. Si pregunta
    algo concreto ('¿qué tengo el jueves?'), usa ver_agenda, que ya abre la pantalla.

    Args:
        fecha: día en que centrar la vista ('YYYY-MM-DD'). Vacío = hoy.
    """
    try:
        dia = datetime.fromisoformat(fecha.strip()[:10]).date() if fecha.strip() else None
    except ValueError:
        return f"ERROR: fecha no válida '{fecha}' (se espera 'YYYY-MM-DD')."
    mostrar(dia)
    return "Pantalla de calendario abierta en el HUD."


def desactivar_modo_calendario() -> str:
    """Cierra la pantalla de calendario y vuelve al HUD normal. Úsalo cuando el usuario
    dé la consulta de agenda por terminada ('cierra el calendario', 'ya está')."""
    pantalla.desactivar("calendario")
    return "Pantalla de calendario cerrada."
