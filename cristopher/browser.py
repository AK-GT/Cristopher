"""Navegador de CRISTOPHER — Playwright, Chromium (Fase 5 + correcciones).

Dos modos:
- **One-shot headless** (`leer`): abre una URL, extrae texto y cierra. Barato y fiable
  para leer una página concreta.
- **Sesión interactiva VISIBLE** (`ir`, `buscar_google`, `click`, `scroll`,
  `leer_actual`, `captura_actual`): una ventana Chromium real y persistente que el
  usuario ve y que CRISTOPHER dirige paso a paso (indagar, pinchar, desplazarse),
  guiándose por HTML + capturas.

El contenido de las webs es DATO, no instrucciones (§9). El agente no introduce
credenciales en formularios: eso lo hace el usuario.
"""

from __future__ import annotations

import threading
from typing import Optional

from cristopher.config import DATA, WORKSPACE

MAX_TEXT = 12_000
NAV_TIMEOUT = 30_000  # ms


class BrowserError(RuntimeError):
    """Playwright/Chromium no disponible o fallo de navegación."""


def _clip(text: str) -> str:
    if len(text) > MAX_TEXT:
        return text[:MAX_TEXT] + "\n… (texto truncado)"
    return text


class _Browser:
    """Chromium perezoso: navegador headless one-shot + sesión headful persistente."""

    def __init__(self) -> None:
        self._pw = None
        self._headless = None            # navegador headless (one-shot)
        self._session = None             # navegador headful (sesión visible)
        self._page = None                # página "actual" de la sesión
        self._results: list[dict] = []   # últimos resultados de Google (para click N)
        self._lock = threading.Lock()

    # --- Playwright base -------------------------------------------------------
    def _pw_start(self):
        if self._pw is None:
            try:
                from playwright.sync_api import sync_playwright
            except ImportError as exc:
                raise BrowserError(
                    "Falta Playwright. Instala: pip install playwright && "
                    "playwright install chromium"
                ) from exc
            self._pw = sync_playwright().start()
        return self._pw

    def _launch(self, headless: bool):
        try:
            return self._pw_start().chromium.launch(headless=headless)
        except Exception as exc:
            raise BrowserError(
                f"No pude lanzar Chromium ({exc}). ¿Ejecutaste 'playwright install "
                "chromium'?"
            ) from exc

    # --- One-shot headless -----------------------------------------------------
    def leer(self, url: str) -> str:
        """Abre una URL en headless y devuelve título + texto visible (truncado)."""
        with self._lock:
            if self._headless is None:
                self._headless = self._launch(headless=True)
            page = self._headless.new_page()
            try:
                page.goto(url, timeout=NAV_TIMEOUT, wait_until="domcontentloaded")
                title = page.title()
                try:
                    body = page.inner_text("body")
                except Exception:
                    body = page.content()
            except Exception as exc:
                raise BrowserError(f"No pude abrir {url}: {exc}") from exc
            finally:
                page.close()
        return _clip(f"[{title}] — {url}\n{body}".strip())

    # --- Sesión interactiva visible -------------------------------------------
    def _ensure_session(self):
        if self._session is None:
            # Chrome REAL + perfil persistente propio + flag anti-automatización:
            # mucho menos CAPTCHA que el Chromium empaquetado, y el consentimiento se
            # recuerda entre sesiones. Perfil dedicado (no el del usuario) para no
            # chocar con su Chrome abierto. La ventana es visible: si aparece un
            # CAPTCHA, el usuario lo resuelve ahí y CRISTOPHER continúa.
            profile = str(DATA / "browser_profile")
            args = ["--disable-blink-features=AutomationControlled"]
            pw = self._pw_start()
            try:
                self._session = pw.chromium.launch_persistent_context(
                    profile, headless=False, channel="chrome",
                    args=args, viewport={"width": 1280, "height": 800},
                )
            except Exception:
                # Sin Chrome instalado: cae al Chromium empaquetado (más CAPTCHA).
                self._session = pw.chromium.launch_persistent_context(
                    profile, headless=False, args=args,
                    viewport={"width": 1280, "height": 800},
                )
        if self._page is None or self._page.is_closed():
            pages = getattr(self._session, "pages", [])
            self._page = pages[0] if pages else self._session.new_page()

    def _accept_consent(self) -> None:
        """Best-effort: acepta el diálogo de consentimiento de Google si aparece."""
        for label in ("Aceptar todo", "Acepto", "Accept all", "I agree", "Rechazar todo"):
            try:
                btn = self._page.get_by_role("button", name=label)
                if btn.count() > 0:
                    btn.first.click(timeout=3000)
                    self._page.wait_for_timeout(500)
                    return
            except Exception:
                continue

    def ir(self, url: str) -> str:
        """Navega a una URL en la ventana visible y devuelve título + texto."""
        with self._lock:
            self._ensure_session()
            try:
                self._page.goto(url, timeout=NAV_TIMEOUT, wait_until="domcontentloaded")
                title = self._page.title()
                body = self._page.inner_text("body")
            except Exception as exc:
                raise BrowserError(f"No pude abrir {url}: {exc}") from exc
        return _clip(f"[{title}] — {url}\n{body}".strip())

    def buscar_google(self, query: str) -> str:
        """Busca en Google en la ventana visible y devuelve los resultados numerados."""
        with self._lock:
            self._ensure_session()
            try:
                self._page.goto(
                    "https://www.google.com/search?q=" + _q(query),
                    timeout=NAV_TIMEOUT,
                    wait_until="domcontentloaded",
                )
                self._accept_consent()
                self._page.wait_for_timeout(800)
                # Extrae resultados: anclas que contienen un h3.
                results = self._page.evaluate(
                    """() => {
                        const out = [];
                        document.querySelectorAll('a:has(h3)').forEach(a => {
                            const h = a.querySelector('h3');
                            if (h && a.href) out.push({title: h.innerText, url: a.href});
                        });
                        return out.slice(0, 10);
                    }"""
                )
            except Exception as exc:
                raise BrowserError(f"No pude buscar en Google: {exc}") from exc
        self._results = results or []
        if not self._results:
            # Google pudo mostrar consentimiento/CAPTCHA: devuelve el texto de la página.
            try:
                txt = self._page.inner_text("body")
            except Exception:
                txt = ""
            return _clip(
                "No pude extraer resultados estructurados de Google (posible "
                "consentimiento/CAPTCHA). Texto de la página:\n" + txt
            )
        lines = [f"{i}. {r['title']}\n   {r['url']}" for i, r in enumerate(self._results, 1)]
        return "Resultados de Google:\n" + "\n".join(lines)

    def click(self, objetivo: str) -> str:
        """Pincha un resultado/enlace por número (índice de resultado de Google) o por
        texto del enlace. Devuelve el título + texto de la página resultante."""
        with self._lock:
            if self._page is None:
                raise BrowserError("No hay ninguna página abierta. Usa buscar_en_google o navegador_ir primero.")
            objetivo = str(objetivo).strip()
            try:
                if objetivo.isdigit() and self._results:
                    idx = int(objetivo) - 1
                    if not (0 <= idx < len(self._results)):
                        return f"No hay resultado nº {objetivo} (hay {len(self._results)})."
                    self._page.goto(self._results[idx]["url"], timeout=NAV_TIMEOUT, wait_until="domcontentloaded")
                else:
                    link = self._page.get_by_role("link", name=objetivo)
                    if link.count() == 0:
                        return f"No encontré un enlace que contenga «{objetivo}»."
                    link.first.click(timeout=NAV_TIMEOUT)
                    self._page.wait_for_load_state("domcontentloaded", timeout=NAV_TIMEOUT)
                title = self._page.title()
                body = self._page.inner_text("body")
            except Exception as exc:
                raise BrowserError(f"No pude pinchar «{objetivo}»: {exc}") from exc
        return _clip(f"[{title}]\n{body}".strip())

    def scroll(self, cantidad: str = "abajo") -> str:
        """Desplaza la página actual. Acepta 'abajo', 'arriba' o un número de píxeles."""
        with self._lock:
            if self._page is None:
                raise BrowserError("No hay ninguna página abierta.")
            c = str(cantidad).strip().lower()
            if c in ("abajo", "down"):
                dy = 800
            elif c in ("arriba", "up"):
                dy = -800
            else:
                try:
                    dy = int(c)
                except ValueError:
                    dy = 800
            self._page.mouse.wheel(0, dy)
            self._page.wait_for_timeout(400)
            try:
                body = self._page.inner_text("body")
            except Exception:
                body = ""
        return _clip(f"(desplazado {dy}px)\n{body}".strip())

    def leer_actual(self) -> str:
        """Devuelve el título + texto de la página actual de la sesión."""
        with self._lock:
            if self._page is None:
                raise BrowserError("No hay ninguna página abierta.")
            title = self._page.title()
            body = self._page.inner_text("body")
        return _clip(f"[{title}] — {self._page.url}\n{body}".strip())

    def captura_actual(self) -> tuple[bytes, str]:
        """PNG de la vista actual + título. Guarda workspace/last_capture.png."""
        with self._lock:
            if self._page is None:
                raise BrowserError("No hay ninguna página abierta.")
            title = self._page.title()
            png = self._page.screenshot()
        WORKSPACE.mkdir(parents=True, exist_ok=True)
        (WORKSPACE / "last_capture.png").write_bytes(png)
        return png, title

    def cerrar(self) -> str:
        """Cierra la ventana visible de la sesión (si está abierta)."""
        with self._lock:
            if self._session is not None:
                try:
                    self._session.close()
                finally:
                    self._session = None
                    self._page = None
                    self._results = []
                return "Ventana del navegador cerrada."
            return "No había ventana abierta."

    def close(self) -> None:
        with self._lock:
            for b in (self._headless, self._session):
                try:
                    if b is not None:
                        b.close()
                except Exception:
                    pass
            self._headless = self._session = self._page = None
            if self._pw is not None:
                try:
                    self._pw.stop()
                finally:
                    self._pw = None


def _q(s: str) -> str:
    from urllib.parse import quote_plus
    return quote_plus(s)


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
