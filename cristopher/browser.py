"""Navegador de CRISTOPHER (Fase 5) — Playwright, Chromium headless.

Vía primaria: leer el texto/estructura de la página (barato, fiable). Vía secundaria:
capturar la pantalla para que la visión lo interprete cuando el HTML no basta.

El contenido de las webs es DATO, no instrucciones (§9). El agente no introduce
credenciales en formularios: eso lo hace el usuario.
"""

from __future__ import annotations

import threading
from typing import Optional

from cristopher.config import WORKSPACE

# Límite de texto devuelto para no inundar el contexto del modelo.
MAX_TEXT = 12_000
NAV_TIMEOUT = 30_000  # ms


class BrowserError(RuntimeError):
    """Playwright/Chromium no disponible o fallo de navegación."""


class _Browser:
    """Envuelve un Chromium headless perezoso y reutilizable (un solo proceso)."""

    def __init__(self) -> None:
        self._pw = None
        self._browser = None
        self._lock = threading.Lock()

    def _ensure(self):
        if self._browser is not None:
            return
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise BrowserError(
                "Falta Playwright. Instala con: pip install playwright && "
                "playwright install chromium"
            ) from exc
        try:
            self._pw = sync_playwright().start()
            self._browser = self._pw.chromium.launch(headless=True)
        except Exception as exc:
            raise BrowserError(
                f"No pude lanzar Chromium ({exc}). ¿Ejecutaste 'playwright install "
                "chromium'?"
            ) from exc

    def _new_page(self, url: str):
        self._ensure()
        page = self._browser.new_page()
        try:
            page.goto(url, timeout=NAV_TIMEOUT, wait_until="domcontentloaded")
        except Exception as exc:
            page.close()
            raise BrowserError(f"No pude abrir {url}: {exc}") from exc
        return page

    def leer(self, url: str) -> str:
        """Navega y devuelve el título + texto visible de la página (truncado)."""
        with self._lock:
            page = self._new_page(url)
            try:
                title = page.title()
                try:
                    body = page.inner_text("body")
                except Exception:
                    body = page.content()  # respaldo: HTML crudo
            finally:
                page.close()
        text = f"[{title}] — {url}\n{body}".strip()
        if len(text) > MAX_TEXT:
            text = text[:MAX_TEXT] + "\n… (texto truncado)"
        return text

    def capturar(self, url: str) -> tuple[bytes, str]:
        """Navega y devuelve (png_bytes, título). Guarda la última captura en workspace."""
        with self._lock:
            page = self._new_page(url)
            try:
                title = page.title()
                png = page.screenshot(full_page=True)
            finally:
                page.close()
        WORKSPACE.mkdir(parents=True, exist_ok=True)
        (WORKSPACE / "last_capture.png").write_bytes(png)
        return png, title

    def close(self) -> None:
        with self._lock:
            if self._browser is not None:
                try:
                    self._browser.close()
                    self._pw.stop()
                finally:
                    self._browser = None
                    self._pw = None


# --- Singleton perezoso -------------------------------------------------------
_BROWSER: Optional[_Browser] = None
_BROWSER_LOCK = threading.Lock()


def get_browser() -> _Browser:
    global _BROWSER
    if _BROWSER is None:
        with _BROWSER_LOCK:
            if _BROWSER is None:
                _BROWSER = _Browser()
    return _BROWSER
