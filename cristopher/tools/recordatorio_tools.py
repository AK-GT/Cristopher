"""Herramientas de recordatorios (Fase 7).

Permiten al usuario pedir "avísame a las 17:00 de X" o "en 30 minutos recuérdame Y".
El demonio proactivo (cristopher.proactivo) los dispara a su hora.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta

from cristopher.recordatorios import get_recordatorios


def _parse_cuando(cuando: str) -> datetime:
    """Resuelve `cuando` a un datetime absoluto. Admite:
    - ISO 8601 ('2026-07-13T17:00')
    - 'HH:MM' (hoy; si ya pasó, mañana)
    - 'en N minutos' / 'en N horas'
    Se resuelve con la hora actual, así el modelo no necesita saber la hora."""
    c = (cuando or "").strip().lower()
    now = datetime.now()

    m = re.match(r"en\s+(\d+)\s*(min|minuto|minutos|h|hora|horas)", c)
    if m:
        n = int(m.group(1))
        unidad = m.group(2)
        delta = timedelta(hours=n) if unidad.startswith("h") else timedelta(minutes=n)
        return now + delta

    m = re.match(r"^(\d{1,2}):(\d{2})$", c)
    if m:
        h, mi = int(m.group(1)), int(m.group(2))
        cand = now.replace(hour=h, minute=mi, second=0, microsecond=0)
        if cand <= now:
            cand += timedelta(days=1)
        return cand

    # ISO (admite espacio en vez de 'T')
    try:
        return datetime.fromisoformat(cuando.strip().replace(" ", "T"))
    except ValueError as exc:
        raise ValueError(
            f"No entiendo la hora {cuando!r}. Usa 'HH:MM', 'en N minutos/horas' o ISO."
        ) from exc


def crear_recordatorio(texto: str, cuando: str) -> str:
    """Programa un recordatorio para una hora futura. El demonio te avisará entonces.

    Args:
        texto: qué recordar.
        cuando: cuándo — 'HH:MM', 'en N minutos', 'en N horas' o fecha/hora ISO.
    """
    texto = (texto or "").strip()
    if not texto:
        return "No hay nada que recordar (texto vacío)."
    try:
        cuando_dt = _parse_cuando(cuando)
    except ValueError as exc:
        return f"ERROR: {exc}"
    rid = get_recordatorios().crear(texto, cuando_dt.isoformat(timespec="seconds"))
    return f"Recordatorio #{rid} creado para {cuando_dt.strftime('%Y-%m-%d %H:%M')}: {texto}"


def borrar_recordatorio(rid: int) -> str:
    """Borra un recordatorio programado por su número.

    Si no sabes el número, llama antes a listar_recordatorios para verlo.

    Args:
        rid: número del recordatorio (el #N que aparece al listar).
    """
    texto = get_recordatorios().borrar(rid)
    if texto is None:
        return f"No encontré ningún recordatorio #{rid}."
    return f"Recordatorio #{rid} borrado: {texto}"


def listar_recordatorios() -> str:
    """Lista los recordatorios programados (pendientes y hechos)."""
    rows = get_recordatorios().listar()
    if not rows:
        return "No hay recordatorios."
    out = []
    for rid, texto, cuando_iso, hecho in rows:
        estado = "✓" if hecho else "⏰"
        out.append(f"{estado} #{rid} {cuando_iso} — {texto}")
    return "\n".join(out)
