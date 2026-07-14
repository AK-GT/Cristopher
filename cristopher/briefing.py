"""Súper briefing diario bajo demanda (agenda, correos, recordatorios, noticias).

Temas de interés en `data/briefing.json` (gitignored, igual que personalidad.json):
lista plana de strings que CRISTOPHER autoedita por iniciativa propia (señal clara de
interés del usuario) o por orden explícita, igual que `personalidad.py` con el trato/
tono — no usa `memory.py` porque estos temas deben regir siempre que se genere un
briefing, no solo cuando el recuerdo semántico los considere relevantes.

`generar()` reutiliza las herramientas ya existentes de Google/búsqueda
(`google_tools.buscar_correos`, `elite_search.busqueda_elite`) y solo añade lógica
propia para la agenda de hoy (por rango de fecha, distinto de `proximo_evento`) y los
recordatorios pendientes de hoy. Cada sección degrada de forma independiente: si una
falla, las demás igual se muestran (§1 "fallos explícitos", nunca fingir éxito).
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta

from cristopher.config import DATA
from cristopher.google_auth import GoogleAuthError, get_calendar
from cristopher.recordatorios import get_recordatorios

_PATH = DATA / "briefing.json"
_LOCK = threading.Lock()

# Tope de búsquedas por tema en cada briefing (latencia: cada una es una llamada a
# Tavily/DuckDuckGo).
MAX_TEMAS_BRIEFING = 5


# --- Temas de interés (persistencia) ------------------------------------------
def _leer() -> list[str]:
    if not _PATH.exists():
        return []
    try:
        data = json.loads(_PATH.read_text(encoding="utf-8"))
        return [str(x) for x in data] if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def _escribir(temas: list[str]) -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    _PATH.write_text(json.dumps(temas, ensure_ascii=False, indent=2), encoding="utf-8")


def agregar_tema(texto: str) -> str:
    """Añade un tema de interés para el briefing. Devuelve un mensaje de confirmación."""
    texto = (texto or "").strip()
    if not texto:
        return "No había ningún tema que guardar (texto vacío)."
    with _LOCK:
        temas = _leer()
        if texto.lower() in (t.lower() for t in temas):
            return f"Ya tenía guardado ese tema: {texto}"
        temas.append(texto)
        _escribir(temas)
    return f"Tema de briefing guardado: {texto}"


def quitar_tema(fragmento: str) -> str:
    """Elimina los temas cuyo texto contenga `fragmento` (sin distinguir mayúsculas).
    Devuelve qué se quitó, o que no encontró nada."""
    fragmento = (fragmento or "").strip()
    if not fragmento:
        return "No indicaste qué tema quitar."
    with _LOCK:
        temas = _leer()
        frag_low = fragmento.lower()
        quedan = [t for t in temas if frag_low not in t.lower()]
        quitados = [t for t in temas if frag_low in t.lower()]
        if not quitados:
            return f"No encontré ningún tema que mencione: {fragmento}"
        _escribir(quedan)
    return "Quitados:\n" + "\n".join(f"- {t}" for t in quitados)


def listar_temas() -> list[str]:
    """Todos los temas de interés guardados para el briefing."""
    with _LOCK:
        return _leer()


# --- Secciones del briefing (cada una degrada de forma independiente) --------
def _seccion_agenda() -> str:
    try:
        svc = get_calendar()
        inicio = datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
        fin = inicio + timedelta(days=1)
        events = (
            svc.events()
            .list(
                calendarId="primary",
                timeMin=inicio.isoformat(),
                timeMax=fin.isoformat(),
                singleEvents=True,
                orderBy="startTime",
            )
            .execute()
            .get("items", [])
        )
    except GoogleAuthError as exc:
        return f"ERROR de autenticación con Google: {exc}"
    except Exception as exc:
        return f"ERROR al leer el calendario: {exc}"

    if not events:
        return "No tienes eventos hoy."
    lines = []
    for e in events:
        start = e.get("start", {}).get("dateTime", e.get("start", {}).get("date", "?"))
        title = e.get("summary", "(sin título)")
        where = e.get("location", "")
        lines.append(f"- {start} · {title}" + (f" · {where}" if where else ""))
    return "\n".join(lines)


def _seccion_correos() -> str:
    from cristopher.tools.google_tools import buscar_correos

    return buscar_correos(query="is:unread newer_than:1d", n=5)


def _seccion_recordatorios() -> str:
    hoy = datetime.now().date().isoformat()
    try:
        filas = get_recordatorios().listar()
    except Exception as exc:
        return f"ERROR al leer recordatorios: {exc}"
    pendientes = [
        (rid, texto, cuando_iso)
        for rid, texto, cuando_iso, hecho in filas
        if not hecho and cuando_iso.startswith(hoy)
    ]
    if not pendientes:
        return "No tienes recordatorios pendientes para hoy."
    return "\n".join(f"- {cuando_iso} — {texto}" for _, texto, cuando_iso in pendientes)


def _seccion_noticias() -> str:
    from cristopher.tools.elite_search import busqueda_elite

    partes = [busqueda_elite("noticias destacadas de hoy", max_results=3)]
    for tema in listar_temas()[:MAX_TEMAS_BRIEFING]:
        partes.append(f"\n--- {tema} ---\n" + busqueda_elite(tema, max_results=3))
    return "\n".join(partes)


def generar() -> str:
    """Arma el súper briefing diario: agenda de hoy, correos nuevos, recordatorios
    pendientes y noticias/recomendaciones (generales + según temas de interés
    guardados)."""
    return (
        "=== AGENDA DE HOY ===\n"
        f"{_seccion_agenda()}\n\n"
        "=== CORREOS NUEVOS ===\n"
        f"{_seccion_correos()}\n\n"
        "=== RECORDATORIOS ===\n"
        f"{_seccion_recordatorios()}\n\n"
        "=== NOTICIAS Y RECOMENDACIONES ===\n"
        f"{_seccion_noticias()}"
    )
