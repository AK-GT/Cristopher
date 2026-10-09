"""Acceso único a Google Calendar (calendario principal).

Antes había tres copias del mismo `events.list` (google_tools, briefing, proactivo),
cada una con su forma de leer fechas. Aquí vive la lógica una sola vez: leer por
rango, buscar huecos libres, detectar solapes y crear/modificar/borrar. Las tools de
`tools/google_tools.py` ponen encima la confirmación (§9) y el formato para el modelo;
la pantalla de calendario del HUD (Fase B) reutilizará estas mismas funciones.

Todas las fechas que salen de aquí son `datetime` CON zona, convertidas a la hora
local del equipo. Los eventos de día completo se representan de medianoche local a
medianoche local (fin exclusivo, como los da Google).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Callable, Optional

from cristopher.google_auth import get_calendar

CAL_ID = "primary"
_DIAS = ("lun", "mar", "mié", "jue", "vie", "sáb", "dom")

# Callback opcional tras crear/modificar/borrar (mismo patrón que sesiones.on_evento):
# la pantalla del HUD lo engancha para refrescarse al instante. Nunca debe tumbar la
# operación que ya se hizo en Google.
_on_cambio: Optional[Callable[[], None]] = None


def set_on_cambio(fn: Optional[Callable[[], None]]) -> None:
    global _on_cambio
    _on_cambio = fn


def _notificar_cambio() -> None:
    if _on_cambio is not None:
        try:
            _on_cambio()
        except Exception:
            pass


# --- Fechas -------------------------------------------------------------------
def ahora() -> datetime:
    return datetime.now().astimezone()


def es_solo_fecha(s: str) -> bool:
    return len(s.strip()) == 10


def parse_dt(s: str) -> datetime:
    """ISO 8601 → datetime local con zona. Si no trae offset se interpreta como hora
    local DE ESA FECHA: `naive.astimezone()` aplica el offset correcto según el horario
    de verano de ese día, no el de hoy (el bug del antiguo `_con_zona`). Lanza
    ValueError con un mensaje legible si no es ISO."""
    try:
        return datetime.fromisoformat(s.strip()).astimezone()
    except ValueError:
        raise ValueError(f"fecha no válida '{s}' (se espera ISO 8601, p. ej. '2026-07-17T10:00:00')")


def inicio_de_dia(d: date) -> datetime:
    return datetime(d.year, d.month, d.day).astimezone()


def a_las(d: date, hora: int) -> datetime:
    return datetime(d.year, d.month, d.day, hora).astimezone()


def fmt_evento(e: dict) -> str:
    """Una línea legible para el modelo y el usuario, con el id para poder
    modificar/borrar después."""
    ini, fin = e["inicio"], e["fin"]
    dia = f"{_DIAS[ini.weekday()]} {ini:%d/%m/%Y}"
    if e["todo_el_dia"]:
        ultimo = fin - timedelta(days=1)
        cuando = f"{dia} (todo el día)" if ultimo.date() == ini.date() else (
            f"{dia} → {_DIAS[ultimo.weekday()]} {ultimo:%d/%m} (todo el día)"
        )
    elif fin.date() == ini.date():
        cuando = f"{dia} {ini:%H:%M}–{fin:%H:%M}"
    else:
        cuando = f"{dia} {ini:%H:%M} → {_DIAS[fin.weekday()]} {fin:%d/%m %H:%M}"
    linea = f"- [id:{e['id']}] {cuando} · {e['titulo']}"
    return linea + (f" · {e['ubicacion']}" if e["ubicacion"] else "")


def fmt_hueco(ini: datetime, fin: datetime) -> str:
    return f"- {_DIAS[ini.weekday()]} {ini:%d/%m} {ini:%H:%M}–{fin:%H:%M}"


def a_json(e: dict) -> dict:
    """Evento normalizado → dict serializable para la pantalla del HUD."""
    return {**e, "inicio": e["inicio"].isoformat(), "fin": e["fin"].isoformat()}


# --- Lectura ------------------------------------------------------------------
def _normalizar(raw: dict) -> dict:
    s, f = raw.get("start", {}), raw.get("end", {})
    todo_el_dia = "dateTime" not in s
    if todo_el_dia:
        inicio = inicio_de_dia(date.fromisoformat(s["date"]))
        fin = inicio_de_dia(date.fromisoformat(f.get("date", s["date"])))
    else:
        inicio = parse_dt(s["dateTime"])
        fin = parse_dt(f.get("dateTime", s["dateTime"]))
    return {
        "id": raw.get("id", ""),
        "titulo": raw.get("summary", "(sin título)"),
        "inicio": inicio,
        "fin": fin,
        "todo_el_dia": todo_el_dia,
        "ubicacion": raw.get("location", ""),
        "descripcion": raw.get("description", ""),
        # Un evento de día completo (cumpleaños, festivo) o marcado como "libre" en
        # Google no bloquea huecos ni cuenta como solape.
        "ocupa": not todo_el_dia and raw.get("transparency") != "transparent",
    }


def _listar(**params) -> list[dict]:
    items = (
        get_calendar().events()
        .list(calendarId=CAL_ID, singleEvents=True, orderBy="startTime", **params)
        .execute()
        .get("items", [])
    )
    return [_normalizar(e) for e in items]


def eventos_rango(desde: datetime, hasta: datetime, maximo: int = 250) -> list[dict]:
    """Eventos que se solapan con [desde, hasta)."""
    return _listar(timeMin=desde.isoformat(), timeMax=hasta.isoformat(), maxResults=maximo)


def proximos(n: int) -> list[dict]:
    return _listar(timeMin=ahora().isoformat(), maxResults=max(1, min(int(n), 50)))


def obtener(evento_id: str) -> dict:
    return _normalizar(get_calendar().events().get(calendarId=CAL_ID, eventId=evento_id).execute())


def solapes(inicio: datetime, fin: datetime, excluir_id: str = "") -> list[dict]:
    return [
        e for e in eventos_rango(inicio, fin)
        if e["ocupa"] and e["id"] != excluir_id and e["inicio"] < fin and e["fin"] > inicio
    ]


def huecos_libres(
    desde: datetime, hasta: datetime, duracion_min: int, franja: tuple[int, int] = (8, 22),
    eventos: Optional[list[dict]] = None,
) -> list[tuple[datetime, datetime]]:
    """Huecos de al menos `duracion_min` minutos dentro de la franja horaria de cada
    día, sin contar el pasado. Se calcula sobre events.list (no freebusy, que exigiría
    un scope OAuth nuevo y re-consentir). `eventos` evita releer de Google si quien
    llama ya los tiene (la pantalla del HUD)."""
    desde = max(desde, ahora())
    if eventos is None:
        eventos = eventos_rango(desde, hasta)
    ocupado = sorted((e["inicio"], e["fin"]) for e in eventos if e["ocupa"])
    duracion = timedelta(minutes=max(1, int(duracion_min)))
    huecos: list[tuple[datetime, datetime]] = []
    dia = desde.date()
    while dia <= hasta.date():
        cursor = max(a_las(dia, franja[0]), desde)
        tope = min(a_las(dia, franja[1]), hasta)
        for ini, fin in ocupado:
            if fin <= cursor or ini >= tope:
                continue
            if ini - cursor >= duracion:
                huecos.append((cursor, ini))
            cursor = max(cursor, fin)
        if tope - cursor >= duracion:
            huecos.append((cursor, tope))
        dia += timedelta(days=1)
    return huecos


# --- Escritura (SIN confirmación: la pone la capa de tools) -------------------
def cuerpo_fechas(inicio: datetime, fin: datetime, todo_el_dia: bool) -> dict:
    if todo_el_dia:
        return {"start": {"date": inicio.date().isoformat()}, "end": {"date": fin.date().isoformat()}}
    return {"start": {"dateTime": inicio.isoformat()}, "end": {"dateTime": fin.isoformat()}}


def crear(titulo: str, inicio: datetime, fin: datetime, todo_el_dia: bool = False,
          descripcion: str = "", ubicacion: str = "") -> dict:
    body = {"summary": titulo, **cuerpo_fechas(inicio, fin, todo_el_dia)}
    if descripcion:
        body["description"] = descripcion
    if ubicacion:
        body["location"] = ubicacion
    creado = get_calendar().events().insert(calendarId=CAL_ID, body=body).execute()
    _notificar_cambio()
    return _normalizar(creado)


def modificar(evento_id: str, cambios: dict) -> dict:
    """`cambios` es un cuerpo parcial de la API (patch): solo se tocan esos campos."""
    nuevo = get_calendar().events().patch(calendarId=CAL_ID, eventId=evento_id, body=cambios).execute()
    _notificar_cambio()
    return _normalizar(nuevo)


def borrar(evento_id: str) -> None:
    get_calendar().events().delete(calendarId=CAL_ID, eventId=evento_id).execute()
    _notificar_cambio()
