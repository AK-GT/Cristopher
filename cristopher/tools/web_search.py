"""Herramienta: búsqueda web vía DuckDuckGo (paquete `ddgs`).

Gratis y sin API key — encaja con el stack gratuito por defecto (§8). En la Fase 4
se añadirá búsqueda de élite (Tavily/SearXNG) con esto como respaldo.
"""

from __future__ import annotations


def web_search(query: str, max_results: int = 5) -> str:
    """Busca en la web y devuelve una lista de resultados con título, URL y resumen.

    Args:
        query: términos de búsqueda.
        max_results: número máximo de resultados (1-10).
    """
    try:
        from ddgs import DDGS
    except ImportError:
        return "ERROR: falta el paquete 'ddgs'. Instala con: pip install ddgs"

    max_results = max(1, min(int(max_results), 10))
    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=max_results))
    except Exception as exc:  # red, rate-limit, etc. — se reporta, no se oculta.
        return f"ERROR en la búsqueda: {exc}"

    if not results:
        return f"Sin resultados para: {query!r}"

    lines = []
    for i, r in enumerate(results, 1):
        title = r.get("title", "(sin título)")
        url = r.get("href", r.get("url", ""))
        body = r.get("body", "").strip()
        lines.append(f"{i}. {title}\n   {url}\n   {body}")
    return "\n".join(lines)
