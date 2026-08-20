"""Herramientas de TikTok (redes sociales, Fase 3: lectura; fase de publicación:
subida de vídeos).

Usa la Display API de TikTok (open.tiktokapis.com/v2) vía
redes_auth.tiktok_api_request — ver redes_auth.py para el flujo OAuth (Login Kit
con PKCE).

A diferencia de YouTube/Instagram, la Display API de TikTok solo da un snapshot
ACTUAL de estadísticas de cuenta (seguidores, likes totales, nº de vídeos), no una
serie histórica por rango de fechas — para eso haría falta la Business API de
TikTok, descartada en el diseño (ver CLAUDE.md). Por eso `tiktok_metricas_cuenta` no lleva
parámetro `dias`.

`tiktok_publicar_video` (fase de publicación) usa la Content Posting API con
`source.FILE_UPLOAD` (subida directa de bytes por PUT, NO necesita el hosting
temporal que sí hace falta para Instagram). Nombres de estado exactos
(`PROCESSING_UPLOAD`/`PUBLISH_COMPLETE`/`FAILED`...) y límites de chunk (5-64MB)
tomados con confianza media de la documentación de TikTok for Developers —
verificar contra una llamada real antes de confiar en ellos a ciegas, mismo tipo
de gotcha ya pisado una vez con el PKCE hexadecimal (ver conectar_cuenta_tiktok
en redes_auth.py). Apps sin auditar (Content Posting API) están restringidas
server-side a `privacy_level=SELF_ONLY` — por eso es el default aquí, no solo una
convención de este código.
"""

from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path

from cristopher.config import (
    TIKTOK_PUBLISH_POLL_INTERVAL_SEGUNDOS,
    TIKTOK_PUBLISH_POLL_TIMEOUT_SEGUNDOS,
    TIKTOK_UPLOAD_CHUNK_SIZE_BYTES,
)
from cristopher.redes_auth import (
    RedesAuthError,
    leer_cuentas,
    tiktok_api_request,
    tiktok_subir_chunk,
)
from cristopher.tools.google_tools import confirmar_accion

_CAMPOS_VIDEO = "id,title,cover_image_url,create_time,view_count,like_count,comment_count,share_count,duration"
_CAMPOS_PERFIL = "display_name,follower_count,likes_count,video_count"
_EXTENSIONES_VIDEO_VALIDAS = {".mp4", ".mov", ".avi", ".mkv", ".webm"}
_ESTADOS_EN_CURSO = {"PROCESSING_UPLOAD", "PROCESSING_DOWNLOAD", "SEND_TO_USER_INBOX"}


def _alias_conectado(alias: str) -> None:
    if not any(c.get("alias") == alias for c in leer_cuentas("tiktok")):
        raise RedesAuthError(
            f"La cuenta '{alias}' no está conectada. Usa "
            f"redes_cuenta_conectar('tiktok', '{alias}') primero."
        )


def _datos_videos_tiktok(alias: str, n: int = 10) -> list[dict]:
    """Vídeos más recientes de una cuenta, en dicts (usado por
    `tiktok_listar_videos` para formatear a texto y por `redes_dashboard` para el
    detalle en vivo del "Modo redes" del HUD). Mismas llamadas a la API que antes."""
    n = max(1, min(int(n), 20))
    resp = tiktok_api_request(
        alias,
        "POST",
        "video/list/",
        params={"fields": _CAMPOS_VIDEO},
        json_body={"max_count": n},
    )
    videos = resp.get("data", {}).get("videos", [])

    out = []
    for v in videos:
        try:
            fecha = datetime.fromtimestamp(int(v.get("create_time", 0))).strftime("%Y-%m-%d")
        except (ValueError, OSError):
            fecha = "?"
        out.append({
            "titulo": v.get("title") or "(sin título)",
            "fecha": fecha,
            "duracion": v.get("duration", "?"),
            "vistas": v.get("view_count", 0),
            "likes": v.get("like_count", 0),
            "comentarios": v.get("comment_count", 0),
            "compartidos": v.get("share_count", 0),
        })
    return out


def tiktok_listar_videos(alias: str, n: int = 10) -> str:
    """Lista los vídeos más recientes de una cuenta de TikTok conectada, con
    vistas/likes/comentarios/compartidos de cada uno.

    Args:
        alias: alias de la cuenta TikTok conectada (ver redes_cuentas_listar).
        n: cuántos vídeos como máximo (por defecto 10, tope real de la API: 20).
    """
    try:
        _alias_conectado(alias)
        datos = _datos_videos_tiktok(alias, n)
    except RedesAuthError as exc:
        return f"ERROR de autenticación con TikTok: {exc}"
    except Exception as exc:
        return f"ERROR al listar los vídeos: {exc}"

    if not datos:
        return f"La cuenta '{alias}' todavía no tiene vídeos públicos."

    lines = [
        f"- {v['titulo']} ({v['fecha']}, {v['duracion']}s) · {v['vistas']} vistas · "
        f"{v['likes']} likes · {v['comentarios']} comentarios · {v['compartidos']} compartidos"
        for v in datos
    ]
    return "\n".join(lines)


def _datos_resumen_tiktok(alias: str) -> dict:
    """Estadísticas actuales de la cuenta (seguidores, likes totales, nº vídeos),
    en dict (usado por `tiktok_metricas_cuenta`/`_resumen_cuenta_tiktok` para
    formatear a texto y por `redes_dashboard` para las tarjetas/tips del "Modo
    redes" del HUD). Misma llamada a la API que antes.

    NOTA: a diferencia de YouTube/Instagram, este dict no trae `ultima_publicacion`
    — añadirla exigiría una llamada extra a video/list/ en cada refresco del
    dashboard (la Display API de TikTok no expone esa fecha en user/info/), así
    que el tip de "cuenta sin publicar hace N días" no se calcula para TikTok por
    ahora (ver CLAUDE.md)."""
    resp = tiktok_api_request(alias, "GET", "user/info/", params={"fields": _CAMPOS_PERFIL})
    perfil = resp.get("data", {}).get("user", {})
    return {
        "alias": alias,
        "display_name": perfil.get("display_name", alias),
        "seguidores": perfil.get("follower_count", "?"),
        "likes_totales": perfil.get("likes_count", "?"),
        "videos": perfil.get("video_count", "?"),
    }


def tiktok_metricas_cuenta(alias: str) -> str:
    """Estadísticas actuales de una cuenta de TikTok: seguidores, likes totales
    recibidos y número de vídeos. Snapshot del momento, sin rango de fechas (la
    Display API de TikTok no da series históricas).

    Args:
        alias: alias de la cuenta TikTok conectada.
    """
    try:
        _alias_conectado(alias)
        d = _datos_resumen_tiktok(alias)
    except RedesAuthError as exc:
        return f"ERROR de autenticación con TikTok: {exc}"
    except Exception as exc:
        return f"ERROR al leer las métricas de la cuenta: {exc}"

    return (
        f"TikTok '{alias}' ({d['display_name']}): "
        f"{d['seguidores']} seguidores · "
        f"{d['likes_totales']} likes totales · "
        f"{d['videos']} vídeos"
    )


def _resumen_cuenta_tiktok(alias: str) -> str:
    """Una línea de resumen de la cuenta (usada por redes_tools.redes_resumen, NO
    registrada como tool propia)."""
    d = _datos_resumen_tiktok(alias)
    return f"TikTok '{alias}' ({d['display_name']}): {d['seguidores']} seguidores · {d['videos']} vídeos"


def tiktok_publicar_video(alias: str, ruta_archivo: str, titulo: str = "", privacidad: str = "SELF_ONLY") -> str:
    """Publica un vídeo en una cuenta de TikTok conectada. PIDE CONFIRMACIÓN al
    usuario antes de subir nada. Por defecto solo lo ve el propio usuario
    (SELF_ONLY) — es el único nivel que TikTok permite mientras la app no esté
    auditada para "Content Posting API".

    Args:
        alias: alias de la cuenta TikTok conectada (ver redes_cuentas_listar).
        ruta_archivo: ruta local al archivo de vídeo.
        titulo: título/descripción del vídeo (por defecto vacío).
        privacidad: 'SELF_ONLY' (por defecto), o 'PUBLIC_TO_EVERYONE'/
            'MUTUAL_FOLLOW_FRIENDS'/'FOLLOWER_OF_CREATOR' si tu app ya está
            auditada. Si no lo está, TikTok rechazará cualquier valor que no
            sea SELF_ONLY.
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

    titulo_preview = titulo if len(titulo) <= 150 else titulo[:147] + "..."
    preview = (
        f"¿Publicar este vídeo en TikTok?\n"
        f"Cuenta: {alias}\nArchivo: {ruta.name}\nTítulo: {titulo_preview or '(vacío)'}\n"
        f"Privacidad: {privacidad}\n"
    )
    if not confirmar_accion(preview):
        return "Publicación CANCELADA por el usuario. No se subió nada."

    try:
        _alias_conectado(alias)

        creator_info = tiktok_api_request(alias, "POST", "post/publish/creator_info/query/")
        opciones = creator_info.get("data", {}).get("privacy_level_options", [])
        if opciones and privacidad not in opciones:
            return (
                f"ERROR: privacidad '{privacidad}' no está disponible para esta "
                f"cuenta/app. Opciones reales: {', '.join(opciones)}."
            )

        video_size = ruta.stat().st_size
        chunk_size = min(TIKTOK_UPLOAD_CHUNK_SIZE_BYTES, video_size)
        total_chunk_count = max(1, -(-video_size // chunk_size))  # división hacia arriba

        init = tiktok_api_request(
            alias,
            "POST",
            "post/publish/video/init/",
            json_body={
                "post_info": {"title": titulo, "privacy_level": privacidad},
                "source_info": {
                    "source": "FILE_UPLOAD",
                    "video_size": video_size,
                    "chunk_size": chunk_size,
                    "total_chunk_count": total_chunk_count,
                },
            },
        )
        publish_id = init["data"]["publish_id"]
        upload_url = init["data"]["upload_url"]

        with open(ruta, "rb") as f:
            inicio = 0
            while inicio < video_size:
                fin = min(inicio + chunk_size, video_size) - 1
                f.seek(inicio)
                chunk = f.read(fin - inicio + 1)
                tiktok_subir_chunk(upload_url, chunk, inicio, fin, video_size)
                inicio = fin + 1

        intervalo = TIKTOK_PUBLISH_POLL_INTERVAL_SEGUNDOS
        transcurrido = 0
        estado = "PROCESSING_UPLOAD"
        status = {}
        while estado in _ESTADOS_EN_CURSO:
            if transcurrido >= TIKTOK_PUBLISH_POLL_TIMEOUT_SEGUNDOS:
                return (
                    "ERROR: TikTok no terminó de procesar el vídeo en "
                    f"{TIKTOK_PUBLISH_POLL_TIMEOUT_SEGUNDOS} segundos. Vuelve a "
                    "intentarlo más tarde."
                )
            time.sleep(intervalo)
            transcurrido += intervalo
            status = tiktok_api_request(
                alias, "POST", "post/publish/status/fetch/", json_body={"publish_id": publish_id}
            )
            estado = status.get("data", {}).get("status", "FAILED")

        if estado != "PUBLISH_COMPLETE":
            motivo = status.get("data", {}).get("fail_reason", "")
            return f"ERROR: TikTok no completó la publicación (estado: {estado}" + (
                f", motivo: {motivo})." if motivo else ")."
            )
    except RedesAuthError as exc:
        return f"ERROR de autenticación con TikTok: {exc}"
    except Exception as exc:
        return f"ERROR al publicar el vídeo: {exc}"

    return f"Vídeo publicado en TikTok ('{alias}'), privacidad: {privacidad}."
