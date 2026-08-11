"""Herramientas de YouTube (redes sociales, Fase 1: lectura; fase de publicación:
subida de vídeos).

Usa la API de datos de YouTube (playlistItems + videos, NO search.list — mucho más
barato en cuota: search.list cuesta 100 unidades por llamada frente a 1 de
playlistItems/videos, y la cuota diaria por defecto es 10.000) para listar Shorts, y
YouTube Analytics API v2 (reports.query) para métricas de canal.

Simplificación MVP deliberada: YouTube no expone un flag "es Short" en la API
pública; se infiere por duración <= 60s (ISO 8601 'PT#M#S') sobre los últimos
uploads del canal. Con canales que suben pocos Shorts entre muchos vídeos largos,
puede hacer falta pedir más uploads de los que el usuario pidió para completar N
Shorts; se acota a un máximo razonable en vez de paginar sin límite.

`youtube_publicar_video` (fase de publicación) sube un vídeo local con el mismo
servicio autorizado que las tools de lectura (`get_youtube_service`) — el scope
`youtube.upload` se añadió a `YOUTUBE_SCOPES` en config.py, así que solo hace
falta reconectar el alias una vez (`redes_cuenta_conectar`) para que el token
cubra también la subida. Gateado por `confirmar_accion` (§9 CLAUDE.md: nunca
publicar sin confirmación explícita), igual que `enviar_correo`.
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from pathlib import Path

from cristopher.redes_auth import (
    RedesAuthError,
    get_youtube_analytics_service,
    get_youtube_service,
    leer_cuentas,
)
from cristopher.tools.google_tools import confirmar_accion

_EXTENSIONES_VIDEO_VALIDAS = {".mp4", ".mov", ".avi", ".mkv", ".webm"}
_PRIVACIDADES_VALIDAS = {"private", "unlisted", "public"}

_DURACION_RE = re.compile(r"^PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?$")


def _duracion_segundos(iso: str) -> int:
    """Convierte una duración ISO 8601 ('PT1M3S') a segundos. 0 si no matchea."""
    m = _DURACION_RE.match(iso or "")
    if not m:
        return 0
    h, mnt, s = (int(g) if g else 0 for g in m.groups())
    return h * 3600 + mnt * 60 + s


def _uploads_playlist_id(alias: str) -> str:
    """uploads_playlist_id de la entrada del alias en cuentas.json. RedesAuthError
    si el alias no está en el índice de YouTube."""
    for c in leer_cuentas("youtube"):
        if c.get("alias") == alias:
            playlist_id = c.get("uploads_playlist_id", "")
            if not playlist_id:
                raise RedesAuthError(
                    f"La cuenta '{alias}' no tiene playlist de subidas registrada. "
                    f"Reconéctala con redes_cuenta_conectar('youtube', '{alias}')."
                )
            return playlist_id
    raise RedesAuthError(
        f"La cuenta '{alias}' no está conectada. Usa "
        f"redes_cuenta_conectar('youtube', '{alias}') primero."
    )


def _datos_videos_youtube(alias: str, n: int = 10) -> list[dict]:
    """Shorts (vídeos de <=60s) más recientes de un canal, en dicts (usado por
    `youtube_listar_shorts` para formatear a texto y por `redes_dashboard` para el
    detalle en vivo del "Modo redes" del HUD). Mismas llamadas a la API que antes."""
    n = max(1, min(int(n), 25))
    uploads_playlist_id = _uploads_playlist_id(alias)
    svc = get_youtube_service(alias)
    candidatos = (
        svc.playlistItems()
        .list(
            playlistId=uploads_playlist_id,
            part="snippet,contentDetails",
            maxResults=min(n * 3, 50),
        )
        .execute()
        .get("items", [])
    )
    video_ids = [c["contentDetails"]["videoId"] for c in candidatos if c.get("contentDetails")]
    if not video_ids:
        return []

    videos = (
        svc.videos()
        .list(id=",".join(video_ids), part="snippet,contentDetails,statistics")
        .execute()
        .get("items", [])
    )
    shorts = [v for v in videos if _duracion_segundos(v["contentDetails"]["duration"]) <= 60][:n]

    out = []
    for v in shorts:
        snippet = v.get("snippet", {})
        stats = v.get("statistics", {})
        out.append({
            "titulo": snippet.get("title", "(sin título)"),
            "publicado": snippet.get("publishedAt", "?")[:10],
            "vistas": stats.get("viewCount", "0"),
            "likes": stats.get("likeCount", "0"),
            "comentarios": stats.get("commentCount", "0"),
            "url": f"https://youtube.com/shorts/{v['id']}",
        })
    return out


def youtube_listar_shorts(alias: str, n: int = 10) -> str:
    """Lista los Shorts (vídeos de <=60s) más recientes de un canal conectado.

    Args:
        alias: alias de la cuenta YouTube conectada (ver redes_cuentas_listar).
        n: cuántos shorts como máximo (por defecto 10).
    """
    try:
        datos = _datos_videos_youtube(alias, n)
    except RedesAuthError as exc:
        return f"ERROR de autenticación con YouTube: {exc}"
    except Exception as exc:
        return f"ERROR al listar los shorts: {exc}"

    if not datos:
        return f"No encontré shorts recientes en el canal '{alias}'."

    lines = [
        f"- {v['titulo']} ({v['publicado']}) · {v['vistas']} vistas · {v['likes']} likes · "
        f"{v['comentarios']} comentarios · {v['url']}"
        for v in datos
    ]
    return "\n".join(lines)


def youtube_metricas_canal(alias: str, dias: int = 28) -> str:
    """Métricas del canal en los últimos `dias` días: vistas, minutos vistos,
    suscriptores ganados/perdidos, likes, comentarios y compartidos.

    Args:
        alias: alias de la cuenta YouTube conectada.
        dias: tamaño de la ventana en días hacia atrás desde hoy (por defecto 28).
    """
    dias = max(1, min(int(dias), 365))
    end = date.today()
    start = end - timedelta(days=dias)
    try:
        svc = get_youtube_analytics_service(alias)
        resp = (
            svc.reports()
            .query(
                ids="channel==MINE",
                startDate=start.isoformat(),
                endDate=end.isoformat(),
                metrics="views,estimatedMinutesWatched,subscribersGained,"
                "subscribersLost,likes,comments,shares",
            )
            .execute()
        )
    except RedesAuthError as exc:
        return f"ERROR de autenticación con YouTube: {exc}"
    except Exception as exc:
        return f"ERROR al leer las métricas del canal: {exc}"

    rows = resp.get("rows") or []
    if not rows:
        return f"No hay datos de métricas para '{alias}' entre {start} y {end}."

    headers = [h["name"] for h in resp.get("columnHeaders", [])]
    valores = dict(zip(headers, rows[0]))

    return (
        f"Canal '{alias}' — últimos {dias} días ({start} a {end}):\n"
        f"Vistas: {valores.get('views', 0)} · "
        f"Minutos vistos: {valores.get('estimatedMinutesWatched', 0)} · "
        f"Suscriptores: +{valores.get('subscribersGained', 0)} / "
        f"-{valores.get('subscribersLost', 0)} · "
        f"Likes: {valores.get('likes', 0)} · "
        f"Comentarios: {valores.get('comments', 0)} · "
        f"Compartidos: {valores.get('shares', 0)}"
    )


def _datos_resumen_youtube(alias: str) -> dict:
    """Suscriptores + vistas últimos 7 días + últimos uploads (con vistas), en dict
    (usado por `_resumen_canal_youtube` para formatear a texto y por
    `redes_dashboard` para las tarjetas/tips del "Modo redes" del HUD). Mismas
    llamadas a la API que antes — ninguna nueva."""
    svc = get_youtube_service(alias)
    uploads_playlist_id = _uploads_playlist_id(alias)

    canal = (
        svc.channels()
        .list(id=_canal_id(alias), part="statistics")
        .execute()
        .get("items", [{}])[0]
    )
    suscriptores = canal.get("statistics", {}).get("subscriberCount")

    candidatos = (
        svc.playlistItems()
        .list(playlistId=uploads_playlist_id, part="contentDetails", maxResults=10)
        .execute()
        .get("items", [])
    )
    video_ids = [c["contentDetails"]["videoId"] for c in candidatos if c.get("contentDetails")]
    videos_recientes = []
    top_titulo, top_vistas = "(sin vídeos)", 0
    ultima_publicacion = None
    if video_ids:
        videos = (
            svc.videos()
            .list(id=",".join(video_ids), part="snippet,statistics")
            .execute()
            .get("items", [])
        )
        for v in videos:
            vistas = int(v.get("statistics", {}).get("viewCount", 0))
            titulo = v.get("snippet", {}).get("title", "(sin título)")
            publicado = (v.get("snippet", {}).get("publishedAt") or "")[:10]
            videos_recientes.append({"titulo": titulo, "publicado": publicado, "vistas": vistas})
            if vistas >= top_vistas:
                top_vistas = vistas
                top_titulo = titulo
            if publicado and (ultima_publicacion is None or publicado > ultima_publicacion):
                ultima_publicacion = publicado

    analytics = get_youtube_analytics_service(alias)
    end = date.today()
    start = end - timedelta(days=7)
    resp = (
        analytics.reports()
        .query(ids="channel==MINE", startDate=start.isoformat(), endDate=end.isoformat(), metrics="views")
        .execute()
    )
    rows = resp.get("rows") or []
    vistas_7d = rows[0][0] if rows else 0

    return {
        "alias": alias,
        "suscriptores": suscriptores,
        "vistas_7d": vistas_7d,
        "top_titulo": top_titulo,
        "top_vistas": top_vistas,
        "ultima_publicacion": ultima_publicacion,
        "videos_recientes": videos_recientes,
    }


def _resumen_canal_youtube(alias: str) -> str:
    """Una línea de resumen del canal (usada por redes_tools.redes_resumen, NO
    registrada como tool propia): suscriptores totales + vistas últimos 7 días +
    título del video más visto entre los últimos uploads."""
    d = _datos_resumen_youtube(alias)
    suscriptores = d["suscriptores"] if d["suscriptores"] is not None else "?"
    return (
        f"YouTube '{alias}': {suscriptores} suscriptores · {d['vistas_7d']} vistas (7 días) "
        f"· más visto: {d['top_titulo']} ({d['top_vistas']} vistas)"
    )


def _canal_id(alias: str) -> str:
    for c in leer_cuentas("youtube"):
        if c.get("alias") == alias:
            return c.get("canal_id", "")
    raise RedesAuthError(
        f"La cuenta '{alias}' no está conectada. Usa "
        f"redes_cuenta_conectar('youtube', '{alias}') primero."
    )


def youtube_publicar_video(
    alias: str,
    ruta_archivo: str,
    titulo: str,
    descripcion: str = "",
    privacidad: str = "private",
) -> str:
    """Sube un vídeo/Short a un canal de YouTube conectado. PIDE CONFIRMACIÓN al
    usuario antes de subir nada; por defecto queda como privado.

    Args:
        alias: alias de la cuenta YouTube conectada (ver redes_cuentas_listar).
        ruta_archivo: ruta local al archivo de vídeo.
        titulo: título del vídeo.
        descripcion: descripción del vídeo (por defecto vacía).
        privacidad: 'private' (por defecto), 'unlisted' o 'public'.
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
    privacidad = (privacidad or "").strip().lower()
    if privacidad not in _PRIVACIDADES_VALIDAS:
        return (
            f"ERROR: privacidad '{privacidad}' inválida. Usa "
            f"{', '.join(sorted(_PRIVACIDADES_VALIDAS))}."
        )

    descripcion_preview = descripcion if len(descripcion) <= 200 else descripcion[:197] + "..."
    preview = (
        f"¿Subir este vídeo a YouTube?\n"
        f"Canal: {alias}\nArchivo: {ruta.name}\nTítulo: {titulo}\n"
        f"Descripción: {descripcion_preview or '(vacía)'}\nPrivacidad: {privacidad}\n"
    )
    if not confirmar_accion(preview):
        return "Subida CANCELADA por el usuario. No se subió nada."

    try:
        from googleapiclient.http import MediaFileUpload

        svc = get_youtube_service(alias)
        media = MediaFileUpload(str(ruta), chunksize=-1, resumable=True)
        request = svc.videos().insert(
            part="snippet,status",
            body={
                "snippet": {"title": titulo, "description": descripcion},
                "status": {"privacyStatus": privacidad},
            },
            media_body=media,
        )
        response = None
        while response is None:
            _status, response = request.next_chunk()
    except RedesAuthError as exc:
        return f"ERROR de autenticación con YouTube: {exc}"
    except Exception as exc:
        return f"ERROR al subir el vídeo: {exc}"

    video_id = response.get("id", "")
    return f"Vídeo subido a YouTube ('{alias}'): https://youtube.com/watch?v={video_id}"
