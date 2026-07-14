"""Capa de resolución del módulo de música (Tanda A).

`resolver(consulta)` traduce lo que pide el usuario ("pon algo de AC/DC", el nombre de
un archivo, una URL) a una PISTA reproducible por VLC:

1. Primero la BIBLIOTECA LOCAL (data/musica): coincidencia por nombre de archivo.
2. Si no hay match local, `yt-dlp` resuelve y extrae el stream de audio de la web.

Degrada con elegancia (§2/§8 del spec): si algo falla (no encontrada, red caída, yt-dlp
roto o VLC/ffmpeg ausente) lanza `MusicaError` con un mensaje claro en español; el tool
lo convierte en observación de texto para el modelo — nunca crashea el agente.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from cristopher.config import MUSICA_DIR, YTDLP_FORMAT

# Extensiones de audio que consideramos "biblioteca local".
_EXTS = {".mp3", ".m4a", ".flac", ".wav", ".ogg", ".opus", ".aac", ".wma"}


class MusicaError(RuntimeError):
    """Fallo al resolver o reproducir una pista (mensaje claro para el usuario)."""


# Track = dict simple (MVP): título, artista, fuente ('local'|'web'), ubicación
# (ruta de archivo o URL de stream) y la consulta original (para resolución perezosa).
Track = dict[str, Any]


def _es_url(consulta: str) -> bool:
    c = consulta.strip().lower()
    return c.startswith("http://") or c.startswith("https://")


def buscar_local(consulta: str) -> Optional[Track]:
    """Busca en data/musica un archivo cuyo nombre contenga la consulta (sin distinguir
    mayúsculas). Devuelve la primera coincidencia o None."""
    try:
        MUSICA_DIR.mkdir(parents=True, exist_ok=True)
    except Exception:
        return None
    q = consulta.strip().lower()
    if not q:
        return None
    candidatos: list[Path] = []
    for p in MUSICA_DIR.rglob("*"):
        if p.is_file() and p.suffix.lower() in _EXTS and q in p.stem.lower():
            candidatos.append(p)
    if not candidatos:
        return None
    # La coincidencia más corta suele ser la más específica ("back in black" antes que
    # "back in black - live remaster").
    mejor = min(candidatos, key=lambda p: len(p.stem))
    return {
        "titulo": mejor.stem,
        "artista": "",
        "fuente": "local",
        "ubicacion": str(mejor),
        "consulta": consulta,
    }


def _resolver_web(consulta: str) -> Track:
    """Resuelve con yt-dlp: si es URL la usa directa; si no, busca en YouTube (ytsearch1)
    y extrae el stream de audio. No descarga: entrega la URL de stream a VLC."""
    try:
        from yt_dlp import YoutubeDL
    except ImportError as exc:  # dependencia ausente: error claro, no traceback opaco
        raise MusicaError(
            "Falta yt-dlp. Instala: pip install yt-dlp"
        ) from exc

    # ytsearch1: convierte texto libre en una búsqueda de YouTube y toma el primer
    # resultado. Una URL directa se resuelve tal cual (default_search se ignora).
    opts = {
        "format": YTDLP_FORMAT,
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "default_search": "ytsearch1",
        "skip_download": True,
    }
    try:
        with YoutubeDL(opts) as ydl:
            info = ydl.extract_info(consulta, download=False)
    except Exception as exc:
        raise MusicaError(
            f"No pude encontrar «{consulta}» en la web ({exc.__class__.__name__}). "
            "¿Hay conexión? ¿El título es correcto?"
        ) from exc

    if info is None:
        raise MusicaError(f"No encontré nada para «{consulta}».")
    # Una búsqueda devuelve un contenedor con 'entries'; una URL directa, la pista.
    if "entries" in info:
        entries = [e for e in (info.get("entries") or []) if e]
        if not entries:
            raise MusicaError(f"No encontré resultados para «{consulta}».")
        info = entries[0]

    url = info.get("url")
    if not url:
        raise MusicaError(
            f"Encontré «{info.get('title', consulta)}» pero no pude extraer el audio "
            "(formato no disponible)."
        )
    return {
        "titulo": info.get("title") or consulta,
        "artista": info.get("uploader") or info.get("artist") or "",
        "fuente": "web",
        "ubicacion": url,
        "consulta": consulta,
    }


def resolver(consulta: str) -> Track:
    """Resuelve una consulta a una pista reproducible. Local primero, luego web.

    Una URL explícita (http/https) va directa a yt-dlp (soporta YouTube y muchas más).
    Lanza `MusicaError` si no se puede resolver."""
    consulta = (consulta or "").strip()
    if not consulta:
        raise MusicaError("No dijiste qué poner.")
    if not _es_url(consulta):
        local = buscar_local(consulta)
        if local is not None:
            return local
    return _resolver_web(consulta)
