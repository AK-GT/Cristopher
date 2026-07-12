"""Herramienta: búsqueda de élite con síntesis y fuentes (Fase 4).

Usa Tavily (síntesis + fuentes de calidad) si hay TAVILY_API_KEY; si no, o si falla,
cae al `web_search` de DuckDuckGo (§8: degrada con elegancia). Cumple el criterio de
la Fase 4: búsqueda con síntesis y fuentes.
"""

from __future__ import annotations

import requests

from cristopher.config import TAVILY_API_KEY
from cristopher.tools.web_search import web_search

TAVILY_URL = "https://api.tavily.com/search"


def busqueda_elite(query: str, max_results: int = 5) -> str:
    """Busca en la web con síntesis y fuentes. Tavily si hay key; si no, DuckDuckGo.

    Args:
        query: términos o pregunta a investigar.
        max_results: número de fuentes a devolver.
    """
    query = (query or "").strip()
    if not query:
        return "ERROR: consulta vacía."

    if not TAVILY_API_KEY:
        return "[Fuente: DuckDuckGo — sin TAVILY_API_KEY]\n" + web_search(
            query, max_results
        )

    try:
        resp = requests.post(
            TAVILY_URL,
            json={
                "api_key": TAVILY_API_KEY,
                "query": query,
                "max_results": max(1, min(int(max_results), 10)),
                "include_answer": True,
            },
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        # Degrada con elegancia: si Tavily falla, tira de DuckDuckGo.
        return (
            f"[Tavily falló ({exc}); uso DuckDuckGo de respaldo]\n"
            + web_search(query, max_results)
        )

    parts = ["[Fuente: Tavily]"]
    answer = (data.get("answer") or "").strip()
    if answer:
        parts.append(f"Síntesis: {answer}")
    results = data.get("results", [])
    if results:
        parts.append("Fuentes:")
        for i, r in enumerate(results, 1):
            title = r.get("title", "(sin título)")
            url = r.get("url", "")
            content = (r.get("content", "") or "").strip()
            parts.append(f"{i}. {title}\n   {url}\n   {content}")
    return "\n".join(parts)
