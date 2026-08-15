"""Caché + historial + tips para el "Modo redes" del HUD (dashboard de
YouTube/Instagram/TikTok).

Estado runtime en memoria (dict + `RLock`, mismo patrón que `concentracion.py`):
no se persiste el propio caché, se recalcula en cada `refrescar_todo()`. Lo único
que SÍ se persiste es un historial mínimo (`redes_historial`, tabla nueva en el
SQLite existente `data/memory.db` — mismo patrón que `tareas.py`/`notas.py`),
necesario porque ninguna de las 3 APIs (sobre todo TikTok) da series históricas:
sin guardar un punto por refresco no habría forma de detectar "seguidores a la
baja" ni comparar tendencia.

Los fetchers estructurados que se llaman aquí (`_datos_resumen_*`/`_datos_*`) son
los mismos que ya usan `youtube_tools.py`/`instagram_tools.py`/`tiktok_tools.py`
para construir sus respuestas de chat — cero llamadas nuevas a las APIs por tener
este dashboard, solo se reutiliza lo que ya se pedía.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from datetime import date
from typing import Optional

from cristopher.config import DATA
from cristopher.redes_auth import RedesAuthError, leer_cuentas

# Cuenta sin publicar hace más de este número de días -> tip de inactividad.
_DIAS_SIN_PUBLICAR_AVISO = 14
# Vídeo/post más reciente con vistas por debajo de este % de la media -> tip.
_UMBRAL_ENGAGEMENT_BAJO = 0.4

_LOCK = threading.RLock()
_cache: dict = {
    "actualizado_en": None,
    "cuentas": {"youtube": [], "instagram": [], "tiktok": []},
    "tips": [],
}

_DB_LOCK = threading.RLock()  # RLock: _historial_anterior/_guardar_historial toman
# el lock y LUEGO llaman _db(), que vuelve a tomarlo si aún no hay conexión —
# con Lock normal eso era un deadlock seguro en la primera llamada (bug real,
# encontrado en la verificación en vivo de Fase 1, ver PLAN "Modo redes").
_conn: Optional[sqlite3.Connection] = None


def _db() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        with _DB_LOCK:
            if _conn is None:
                DATA.mkdir(parents=True, exist_ok=True)
                conn = sqlite3.connect(str(DATA / "memory.db"), check_same_thread=False, timeout=5)
                with conn:
                    conn.execute(
                        """CREATE TABLE IF NOT EXISTS redes_historial (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            plataforma TEXT NOT NULL,
                            alias TEXT NOT NULL,
                            ts REAL NOT NULL,
                            seguidores INTEGER,
                            ultima_publicacion TEXT
                        )"""
                    )
                _conn = conn
    return _conn


def _guardar_historial(plataforma: str, alias: str, seguidores, ultima_publicacion: Optional[str]) -> None:
    try:
        seguidores_n = int(seguidores)
    except (TypeError, ValueError):
        seguidores_n = None
    with _DB_LOCK:
        conn = _db()
        with conn:
            conn.execute(
                "INSERT INTO redes_historial (plataforma, alias, ts, seguidores, ultima_publicacion) "
                "VALUES (?, ?, ?, ?, ?)",
                (plataforma, alias, time.time(), seguidores_n, ultima_publicacion),
            )


def _historial_anterior(plataforma: str, alias: str) -> Optional[dict]:
    """Datos del refresco ANTERIOR, o None si todavía no hay historial que comparar.

    Es la última fila de la tabla: en `refrescar_todo` esta función se llama antes
    de `_guardar_historial`, así que el refresco en curso todavía no está guardado.
    (Antes se devolvía la penúltima, lo que comparaba en realidad contra dos
    refrescos atrás y no cuadraba con el `delta` que pinta la tarjeta del HUD.)
    """
    with _DB_LOCK:
        conn = _db()
        row = conn.execute(
            "SELECT seguidores, ultima_publicacion FROM redes_historial "
            "WHERE plataforma=? AND alias=? ORDER BY id DESC LIMIT 1",
            (plataforma, alias),
        ).fetchone()
    if row is None:
        return None
    return {"seguidores": row[0], "ultima_publicacion": row[1]}


def serie_historial(plataforma: str, alias: str, n: int = 12) -> list[int]:
    """Últimos `n` valores de seguidores guardados, en orden cronológico: es lo que
    dibuja el sparkline de la tarjeta en el HUD. Sale del SQLite que ya se escribe
    en cada refresco — cero llamadas nuevas a las APIs."""
    with _DB_LOCK:
        conn = _db()
        rows = conn.execute(
            "SELECT seguidores FROM redes_historial "
            "WHERE plataforma=? AND alias=? AND seguidores IS NOT NULL "
            "ORDER BY id DESC LIMIT ?",
            (plataforma, alias, max(1, int(n))),
        ).fetchall()
    return [r[0] for r in reversed(rows)]


def _delta(seguidores, anterior: Optional[dict]) -> Optional[int]:
    """Variación de seguidores respecto al refresco anterior, o None si no hay con
    qué comparar (primera vez, o la plataforma devolvió '?')."""
    if not anterior or anterior.get("seguidores") is None or seguidores is None:
        return None
    try:
        return int(seguidores) - int(anterior["seguidores"])
    except (TypeError, ValueError):
        return None


def _fetchers_resumen() -> dict:
    from cristopher.tools.instagram_tools import _datos_resumen_instagram
    from cristopher.tools.tiktok_tools import _datos_resumen_tiktok
    from cristopher.tools.youtube_tools import _datos_resumen_youtube
    return {
        "youtube": _datos_resumen_youtube,
        "instagram": _datos_resumen_instagram,
        "tiktok": _datos_resumen_tiktok,
    }


def _fetchers_detalle() -> dict:
    from cristopher.tools.instagram_tools import _datos_publicaciones_instagram, _datos_resumen_instagram
    from cristopher.tools.tiktok_tools import _datos_resumen_tiktok, _datos_videos_tiktok
    from cristopher.tools.youtube_tools import _datos_resumen_youtube, _datos_videos_youtube
    return {
        "youtube": (_datos_resumen_youtube, _datos_videos_youtube),
        "instagram": (_datos_resumen_instagram, _datos_publicaciones_instagram),
        "tiktok": (_datos_resumen_tiktok, _datos_videos_tiktok),
    }


def _calcular_tips(plataforma: str, alias: str, datos: dict) -> list[dict]:
    """Reglas puras sobre `datos` (el dict recién obtenido) y el historial ya
    guardado — sin LLM, ver PLAN "Modo redes"."""
    tips: list[dict] = []
    seguidores = datos.get("suscriptores", datos.get("seguidores"))
    ultima_publicacion = datos.get("ultima_publicacion")
    nombre = f"{plataforma.capitalize()} '{alias}'"

    if ultima_publicacion:
        try:
            dias = (date.today() - date.fromisoformat(ultima_publicacion)).days
            if dias >= _DIAS_SIN_PUBLICAR_AVISO:
                tips.append({
                    "tipo": "inactividad", "plataforma": plataforma, "alias": alias,
                    "texto": f"{nombre} lleva {dias} días sin publicar.",
                })
        except ValueError:
            pass

    anterior = _historial_anterior(plataforma, alias)
    if anterior and anterior.get("seguidores") is not None and seguidores is not None:
        try:
            if int(seguidores) < int(anterior["seguidores"]):
                tips.append({
                    "tipo": "seguidores_baja", "plataforma": plataforma, "alias": alias,
                    "texto": f"{nombre} perdió seguidores desde el último refresco.",
                })
        except (TypeError, ValueError):
            pass

    # Solo disponible cuando el fetcher trae la lista de últimas publicaciones con
    # vistas (hoy: YouTube) — ver nota en tiktok_tools._datos_resumen_tiktok.
    videos_recientes = datos.get("videos_recientes")
    if videos_recientes:
        vistas = [v["vistas"] for v in videos_recientes if isinstance(v.get("vistas"), (int, float))]
        if len(vistas) >= 3:
            media = sum(vistas) / len(vistas)
            mas_reciente = max(videos_recientes, key=lambda v: v.get("publicado") or "")
            vr = mas_reciente.get("vistas")
            if media > 0 and isinstance(vr, (int, float)) and vr < media * _UMBRAL_ENGAGEMENT_BAJO:
                tips.append({
                    "tipo": "engagement_bajo", "plataforma": plataforma, "alias": alias,
                    "texto": f"{nombre}: '{mas_reciente.get('titulo')}' tiene muchas menos vistas "
                             f"que la media reciente.",
                })

    return tips


def refrescar_todo() -> dict:
    """Recorre todas las cuentas conectadas, pide datos reales (try/except por
    cuenta — una cuenta rota no tira el resto, mismo patrón que
    redes_tools.redes_resumen), guarda historial y recalcula tips. Actualiza y
    devuelve el caché."""
    fetchers = _fetchers_resumen()
    cuentas_out: dict = {"youtube": [], "instagram": [], "tiktok": []}
    tips: list[dict] = []

    for plataforma, fetch in fetchers.items():
        for c in leer_cuentas(plataforma):
            alias = c.get("alias")
            # "nombre" para las tarjetas del HUD: mismo fallback que
            # redes_tools.redes_cuentas_listar (canal_titulo/username/display_name
            # vienen del índice cuentas.json, no de la llamada en vivo — así la
            # tarjeta tiene nombre incluso si la cuenta da error más abajo).
            nombre = c.get("canal_titulo") or c.get("username") or c.get("display_name") or alias
            item: dict = {"plataforma": plataforma, "alias": alias, "nombre": nombre, "error": None}
            try:
                datos = fetch(alias)
            except RedesAuthError as exc:
                item["error"] = str(exc)
                tips.append({
                    "tipo": "error_auth", "plataforma": plataforma, "alias": alias,
                    "texto": f"{plataforma.capitalize()} '{alias}' necesita reconectarse ({exc}).",
                })
            except Exception as exc:
                item["error"] = str(exc)
                tips.append({
                    "tipo": "error", "plataforma": plataforma, "alias": alias,
                    "texto": f"{plataforma.capitalize()} '{alias}': error al leer datos ({exc}).",
                })
            else:
                item.update(datos)
                tips.extend(_calcular_tips(plataforma, alias, datos))
                seguidores = datos.get("suscriptores", datos.get("seguidores"))
                # El delta se calcula ANTES de guardar (si no, se compararía la fila
                # nueva consigo misma); la serie DESPUÉS, para incluir este refresco.
                anterior = _historial_anterior(plataforma, alias)
                _guardar_historial(plataforma, alias, seguidores, datos.get("ultima_publicacion"))
                item["delta"] = _delta(seguidores, anterior)
                item["serie"] = serie_historial(plataforma, alias)
            cuentas_out[plataforma].append(item)

    with _LOCK:
        _cache["actualizado_en"] = time.time()
        _cache["cuentas"] = cuentas_out
        _cache["tips"] = tips
    return cache_actual()


def cache_actual() -> dict:
    """Lectura thread-safe del caché (copia — el frontend sondea esto, nunca
    dispara una llamada a las APIs de redes por sí sola)."""
    with _LOCK:
        return {
            "actualizado_en": _cache["actualizado_en"],
            "cuentas": {k: list(v) for k, v in _cache["cuentas"].items()},
            "tips": list(_cache["tips"]),
        }


def detalle_cuenta(plataforma: str, alias: str) -> dict:
    """Fetch EN VIVO (sin caché) de una cuenta: se usa cuando el usuario pincha
    una tarjeta en el HUD, así el detalle nunca está desactualizado."""
    fetchers = _fetchers_detalle()
    if plataforma not in fetchers:
        return {"plataforma": plataforma, "alias": alias, "error": f"Plataforma desconocida: {plataforma}"}
    fetch_resumen, fetch_lista = fetchers[plataforma]
    try:
        resumen = fetch_resumen(alias)
        publicaciones = fetch_lista(alias, 10)
    except RedesAuthError as exc:
        return {"plataforma": plataforma, "alias": alias, "error": str(exc)}
    except Exception as exc:
        return {"plataforma": plataforma, "alias": alias, "error": str(exc)}
    return {
        "plataforma": plataforma, "alias": alias, "error": None,
        "resumen": resumen, "publicaciones": publicaciones,
    }
