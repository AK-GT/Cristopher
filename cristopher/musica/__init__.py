"""Módulo de música de CRISTOPHER (Tanda A: "que suene").

Motor de reproducción propio: VLC (python-vlc) + yt-dlp para "cualquier canción" de la
web, con cola, siguiente/anterior y auto-avance. Sin apps externas ni servicios de pago.
"""

from __future__ import annotations

from cristopher.musica.reproductor import Reproductor, get_reproductor
from cristopher.musica.resolver import MusicaError

__all__ = ["get_reproductor", "Reproductor", "MusicaError"]
