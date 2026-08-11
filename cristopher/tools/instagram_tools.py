"""Herramientas de Instagram (redes sociales, Fase 2: lectura; fase de
publicación: subida de Reels).

Usa "Instagram API with Instagram Login" (graph.instagram.com), vía
redes_auth.instagram_graph_get/instagram_graph_post — ver redes_auth.py para el
flujo OAuth.

Aviso honesto: los nombres de métricas de Insights de Meta cambian entre versiones
de la API (p. ej. 'impressions'/'profile_views' quedaron deprecados a favor de
'views'/'reach'/'follower_count'/'reposts' en versiones recientes). Se usa el set
más estable verificado contra la documentación actual; si Meta rechaza alguna
métrica, el error de la API se propaga tal cual (nunca se inventa un dato) y toca
ajustar el nombre aquí.

`instagram_publicar_reel` (fase de publicación) sube el vídeo a un hosting
temporal (redes_hosting_temporal.py, Cloud Storage — Content Publishing exige
un video_url público que Meta pueda descargar, no admite bytes locales), crea
el contenedor de media, espera a que Meta termine de procesarlo (polling) y lo
publica. El objeto temporal se borra siempre al terminar, éxito o error.
Gateado por confirmar_accion (§9 CLAUDE.md), igual que enviar_correo.
"""

from __future__ import annotations

import time
from datetime import date, datetime, timedelta
from pathlib import Path

from cristopher.config import (
    INSTAGRAM_PUBLISH_POLL_INTERVAL_SEGUNDOS,
    INSTAGRAM_PUBLISH_POLL_MAX_INTERVALO_SEGUNDOS,
    INSTAGRAM_PUBLISH_POLL_TIMEOUT_SEGUNDOS,
)
from cristopher.redes_auth import (
    RedesAuthError,
    instagram_graph_get,
    instagram_graph_post,
    leer_cuentas,
)
from cristopher.redes_hosting_temporal import (
    HostingTemporalError,
    borrar_temporal,
    subir_temporal,
)
from cristopher.tools.google_tools import confirmar_accion

_EXTENSIONES_VIDEO_VALIDAS = {".mp4", ".mov", ".avi", ".mkv", ".webm"}


def _ig_user_id(alias: str) -> str:
    """ig_user_id de la entrada del alias en cuentas.json. RedesAuthError si el
    alias no está conectado en Instagram."""
    for c in leer_cuentas("instagram"):
        if c.get("alias") == alias:
            return c["ig_user_id"]
    raise RedesAuthError(
        f"La cuenta '{alias}' no está conectada. Usa "
        f"redes_cuenta_conectar('instagram', '{alias}') primero."
    )


def _datos_publicaciones_instagram(alias: str, n: int = 10) -> list[dict]:
    """Publicaciones más recientes de una cuenta, en dicts (usado por
    `instagram_listar_publicaciones` para formatear a texto y por
    `redes_dashboard` para el detalle en vivo del "Modo redes" del HUD). Mismas
    llamadas a la API que antes."""
    n = max(1, min(int(n), 25))
    ig_user_id = _ig_user_id(alias)
    media = instagram_graph_get(
        alias,
        f"{ig_user_id}/media",
        {
            "fields": "id,caption,media_type,timestamp,like_count,comments_count,permalink",
            "limit": n,
        },
    ).get("data", [])

    out = []
    for post in media:
        caption = (post.get("caption") or "(sin descripción)").strip().replace("\n", " ")
        if len(caption) > 80:
            caption = caption[:77] + "..."
        reach = guardados = "?"
        try:
            insights = {
                m["name"]: m["values"][0]["value"]
                for m in instagram_graph_get(
                    alias, f"{post['id']}/insights", {"metric": "reach,saved"}
                ).get("data", [])
            }
            reach = insights.get("reach", "?")
            guardados = insights.get("saved", "?")
        except Exception:
            pass  # insights por post es un extra; sin ellos igual se muestra el resto
        out.append({
            "caption": caption,
            "fecha": post.get("timestamp", "?")[:10],
            "likes": post.get("like_count", 0),
            "comentarios": post.get("comments_count", 0),
            "alcance": reach,
            "guardados": guardados,
            "permalink": post.get("permalink", ""),
        })
    return out


def instagram_listar_publicaciones(alias: str, n: int = 10) -> str:
    """Lista las publicaciones más recientes de una cuenta de Instagram conectada,
    con likes/comentarios/alcance/guardados de cada una.

    Args:
        alias: alias de la cuenta Instagram conectada (ver redes_cuentas_listar).
        n: cuántas publicaciones como máximo (por defecto 10).
    """
    try:
        datos = _datos_publicaciones_instagram(alias, n)
    except RedesAuthError as exc:
        return f"ERROR de autenticación con Instagram: {exc}"
    except Exception as exc:
        return f"ERROR al listar las publicaciones: {exc}"

    if not datos:
        return f"La cuenta '{alias}' todavía no tiene publicaciones."

    lines = [
        f"- {p['caption']} ({p['fecha']}) · {p['likes']} likes · {p['comentarios']} comentarios · "
        f"{p['alcance']} alcance · {p['guardados']} guardados · {p['permalink']}"
        for p in datos
    ]
    return "\n".join(lines)


def instagram_metricas_cuenta(alias: str, dias: int = 28) -> str:
    """Métricas de la cuenta de Instagram en los últimos `dias` días: alcance y
    suscriptores actuales.

    Args:
        alias: alias de la cuenta Instagram conectada.
        dias: tamaño de la ventana en días hacia atrás desde hoy (por defecto 28).
    """
    dias = max(1, min(int(dias), 365))
    end = date.today()
    start = end - timedelta(days=dias)
    try:
        ig_user_id = _ig_user_id(alias)
        since = int(datetime.combine(start, datetime.min.time()).timestamp())
        until = int(datetime.combine(end, datetime.min.time()).timestamp())
        resp = instagram_graph_get(
            alias,
            f"{ig_user_id}/insights",
            {"metric": "reach", "period": "day", "since": since, "until": until},
        )
        valores = resp.get("data", [])
        reach_total = sum(
            punto.get("value", 0)
            for serie in valores
            for punto in serie.get("values", [])
        )

        perfil = instagram_graph_get(alias, str(ig_user_id), {"fields": "followers_count,username"})
    except RedesAuthError as exc:
        return f"ERROR de autenticación con Instagram: {exc}"
    except Exception as exc:
        return f"ERROR al leer las métricas de la cuenta: {exc}"

    return (
        f"Instagram '{alias}' (@{perfil.get('username', alias)}) — últimos {dias} días "
        f"({start} a {end}):\n"
        f"Alcance: {reach_total} · Seguidores actuales: {perfil.get('followers_count', '?')}"
    )


def _datos_resumen_instagram(alias: str) -> dict:
    """Seguidores + alcance de los últimos 7 días, en dict (usado por
    `_resumen_cuenta_instagram` para formatear a texto y por `redes_dashboard`
    para las tarjetas/tips del "Modo redes" del HUD). Mismas llamadas a la API
    que antes."""
    ig_user_id = _ig_user_id(alias)
    perfil = instagram_graph_get(alias, str(ig_user_id), {"fields": "followers_count,username"})
    seguidores = perfil.get("followers_count", "?")
    username = perfil.get("username", alias)

    end = date.today()
    start = end - timedelta(days=7)
    since = int(datetime.combine(start, datetime.min.time()).timestamp())
    until = int(datetime.combine(end, datetime.min.time()).timestamp())
    resp = instagram_graph_get(
        alias, f"{ig_user_id}/insights", {"metric": "reach", "period": "day", "since": since, "until": until}
    )
    reach_7d = sum(
        punto.get("value", 0) for serie in resp.get("data", []) for punto in serie.get("values", [])
    )

    return {"alias": alias, "username": username, "seguidores": seguidores, "reach_7d": reach_7d}


def _resumen_cuenta_instagram(alias: str) -> str:
    """Una línea de resumen de la cuenta (usada por redes_tools.redes_resumen, NO
    registrada como tool propia): seguidores + alcance de los últimos 7 días."""
    d = _datos_resumen_instagram(alias)
    return f"Instagram '{alias}' (@{d['username']}): {d['seguidores']} seguidores · {d['reach_7d']} alcance (7 días)"


def instagram_publicar_reel(alias: str, ruta_archivo: str, caption: str = "") -> str:
    """Publica un Reel en una cuenta de Instagram conectada. PIDE CONFIRMACIÓN al
    usuario antes de tocar nada. Puede tardar varios minutos (Meta procesa el
    vídeo antes de poder publicarlo).

    Args:
        alias: alias de la cuenta Instagram conectada (ver redes_cuentas_listar).
        ruta_archivo: ruta local al archivo de vídeo.
        caption: descripción del Reel (por defecto vacía).
    """
    ruta = Path(ruta_archivo)
    if not ruta.exists() or not ruta.is_file():
        return f"ERROR: no encuentro el archivo '{ruta_archivo}'."
    if ruta.suffix.lower() not in _EXTENSIONES_VIDEO_VALIDAS:
        return (
            f"ERROR: '{ruta.suffix}' no parece un archivo de vídeo válido. "
            f"Extensiones soportadas: {', '.join(sorted(_EXTENSIONES_VIDEO_VALIDAS))}."
        )
    if ruta.stat().st_size == 0:
        return f"ERROR: '{ruta.name}' está vacío (0 bytes)."

    caption_preview = caption if len(caption) <= 200 else caption[:197] + "..."
    preview = (
        f"¿Publicar este Reel en Instagram?\n"
        f"Cuenta: {alias}\nArchivo: {ruta.name}\nCaption: {caption_preview or '(vacío)'}\n"
    )
    if not confirmar_accion(preview):
        return "Publicación CANCELADA por el usuario. No se subió nada."

    try:
        ig_user_id = _ig_user_id(alias)
    except RedesAuthError as exc:
        return f"ERROR de autenticación con Instagram: {exc}"

    try:
        url_publica, blob_name = subir_temporal(str(ruta), prefijo="instagram")
    except HostingTemporalError as exc:
        return f"ERROR de hosting temporal: {exc}"

    try:
        container = instagram_graph_post(
            alias,
            f"{ig_user_id}/media",
            {"media_type": "REELS", "video_url": url_publica, "caption": caption},
        )
        container_id = container["id"]

        intervalo = INSTAGRAM_PUBLISH_POLL_INTERVAL_SEGUNDOS
        transcurrido = 0
        status_code = "IN_PROGRESS"
        while status_code == "IN_PROGRESS":
            if transcurrido >= INSTAGRAM_PUBLISH_POLL_TIMEOUT_SEGUNDOS:
                return (
                    "ERROR: Meta no terminó de procesar el Reel en "
                    f"{INSTAGRAM_PUBLISH_POLL_TIMEOUT_SEGUNDOS // 60} minutos. "
                    "Vuelve a intentarlo más tarde."
                )
            time.sleep(intervalo)
            transcurrido += intervalo
            intervalo = min(intervalo * 2, INSTAGRAM_PUBLISH_POLL_MAX_INTERVALO_SEGUNDOS)
            estado = instagram_graph_get(alias, container_id, {"fields": "status_code"})
            status_code = estado.get("status_code", "ERROR")

        if status_code != "FINISHED":
            return f"ERROR: Instagram no pudo procesar el Reel (estado: {status_code})."

        publicado = instagram_graph_post(alias, f"{ig_user_id}/media_publish", {"creation_id": container_id})
        media_id = publicado.get("id", "")
        try:
            permalink = instagram_graph_get(alias, media_id, {"fields": "permalink"}).get("permalink", "")
        except Exception:
            permalink = ""
    except RedesAuthError as exc:
        return f"ERROR de autenticación con Instagram: {exc}"
    except Exception as exc:
        return f"ERROR al publicar el Reel: {exc}"
    finally:
        borrar_temporal(blob_name)

    return f"Reel publicado en Instagram ('{alias}'): {permalink or media_id}"
