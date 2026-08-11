"""Herramientas transversales de redes sociales: alta de cuentas, listado del
índice y resumen agregado. Fase 1-3: YouTube, Instagram y TikTok tienen datos
reales.
"""

from __future__ import annotations

from cristopher.redes_auth import (
    RedesAuthError,
    conectar_cuenta_instagram,
    conectar_cuenta_tiktok,
    conectar_cuenta_youtube,
    indice_completo,
    leer_cuentas,
)
from cristopher.tools.instagram_tools import _resumen_cuenta_instagram
from cristopher.tools.tiktok_tools import _resumen_cuenta_tiktok
from cristopher.tools.youtube_tools import _resumen_canal_youtube

_PLATAFORMAS_SOPORTADAS = {"youtube", "instagram", "tiktok"}


def redes_cuenta_conectar(plataforma: str, alias: str) -> str:
    """Conecta una cuenta nueva de una red social (lanza el consentimiento en el
    navegador). NO pide confirmación aparte: la propia pantalla de consentimiento
    (de Google o de Instagram), donde el usuario elige la cuenta, ES la
    confirmación.

    Args:
        plataforma: 'youtube', 'instagram' o 'tiktok'.
        alias: nombre corto que tú eliges para identificar esta cuenta
            (p. ej. 'principal', 'humor'). Solo letras/números/guiones.
    """
    plataforma = (plataforma or "").strip().lower()
    if plataforma == "youtube":
        try:
            return conectar_cuenta_youtube(alias)
        except RedesAuthError as exc:
            return f"ERROR de autenticación con YouTube: {exc}"
        except Exception as exc:
            return f"ERROR al conectar la cuenta de YouTube: {exc}"
    if plataforma == "instagram":
        try:
            return conectar_cuenta_instagram(alias)
        except RedesAuthError as exc:
            return f"ERROR de autenticación con Instagram: {exc}"
        except Exception as exc:
            return f"ERROR al conectar la cuenta de Instagram: {exc}"
    if plataforma == "tiktok":
        try:
            return conectar_cuenta_tiktok(alias)
        except RedesAuthError as exc:
            return f"ERROR de autenticación con TikTok: {exc}"
        except Exception as exc:
            return f"ERROR al conectar la cuenta de TikTok: {exc}"
    return (
        f"ERROR: plataforma desconocida '{plataforma}'. Soportadas ahora: "
        "youtube, instagram, tiktok."
    )


def redes_cuentas_listar() -> str:
    """Muestra qué cuentas de redes sociales están conectadas, por plataforma."""
    indice = indice_completo()
    lines = []
    for plataforma in ("youtube", "instagram", "tiktok"):
        cuentas = indice.get(plataforma, [])
        titulo = plataforma.capitalize()
        if not cuentas:
            lines.append(f"{titulo}: (ninguna cuenta conectada todavía)")
            continue
        lines.append(f"{titulo}:")
        for c in cuentas:
            nombre = (
                c.get("canal_titulo")
                or c.get("username")
                or c.get("display_name")
                or c.get("alias")
            )
            lines.append(f"- {c.get('alias')} ({nombre})")
    return "\n".join(lines)


def redes_resumen() -> str:
    """Panorama rápido de TODAS las cuentas conectadas: YouTube, Instagram y
    TikTok."""
    cuentas_yt = leer_cuentas("youtube")
    cuentas_ig = leer_cuentas("instagram")
    cuentas_tk = leer_cuentas("tiktok")
    if not cuentas_yt and not cuentas_ig and not cuentas_tk:
        return (
            "No tienes ninguna cuenta conectada todavía. Usa redes_cuenta_conectar "
            "para añadir tu primer canal de YouTube, cuenta de Instagram o TikTok."
        )
    lines = []
    for c in cuentas_yt:
        alias = c.get("alias")
        try:
            lines.append(_resumen_canal_youtube(alias))
        except RedesAuthError as exc:
            lines.append(f"YouTube '{alias}': ERROR de autenticación ({exc})")
        except Exception as exc:
            lines.append(f"YouTube '{alias}': ERROR al leer el resumen ({exc})")
    for c in cuentas_ig:
        alias = c.get("alias")
        try:
            lines.append(_resumen_cuenta_instagram(alias))
        except RedesAuthError as exc:
            lines.append(f"Instagram '{alias}': ERROR de autenticación ({exc})")
        except Exception as exc:
            lines.append(f"Instagram '{alias}': ERROR al leer el resumen ({exc})")
    for c in cuentas_tk:
        alias = c.get("alias")
        try:
            lines.append(_resumen_cuenta_tiktok(alias))
        except RedesAuthError as exc:
            lines.append(f"TikTok '{alias}': ERROR de autenticación ({exc})")
        except Exception as exc:
            lines.append(f"TikTok '{alias}': ERROR al leer el resumen ({exc})")
    return "\n".join(lines)
