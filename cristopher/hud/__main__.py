"""Servidor del HUD de CRISTOPHER (Fase 8).

Web app LOCAL: sirve el HUD y lo conecta al estado REAL de CRISTOPHER por streaming
(SSE). El usuario escribe desde el navegador; el bucle corre en el servidor y publica
estado/trazas/respuesta al bus, que el HUD refleja en vivo.

Uso:  python -m cristopher.hud
"""

from __future__ import annotations

import json
import queue
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from cristopher import bus, estado, voz
from cristopher.agent import Cristopher, split_final
from cristopher.config import HUD_PORT
from cristopher.tools.google_tools import set_confirmer

STATIC = Path(__file__).parent / "static"

# Cola de trabajos del agente. Un ÚNICO hilo worker los procesa en serie, así el
# navegador (Playwright usa una API SÍNCRONA ligada al hilo que la creó) y el cliente
# Gemini viven siempre en el MISMO hilo. Sin esto, ThreadingHTTPServer atendería cada
# petición en un hilo distinto y la 2ª herramienta de navegador reventaría con
# "cannot switch to a different thread". Las conexiones SSE siguen en sus propios hilos.
_JOBS: "queue.Queue[str]" = queue.Queue()
_CRIS: Cristopher | None = None

_CT = {".html": "text/html", ".css": "text/css", ".js": "application/javascript",
       ".json": "application/json"}


def _on_step(kind: str, text: str) -> None:
    bus.log(kind, text)
    if kind == "tool_call" and text.startswith("delegar_a_claude"):
        bus.add_subagente("claude-code", text)


def _get_cris() -> Cristopher:
    global _CRIS
    if _CRIS is None:
        _CRIS = Cristopher(on_step=_on_step)
    return _CRIS


# --- Confirmación de acciones irreversibles por CLIC en el navegador (§9) ---------
# El worker (hilo único) queda a la espera de que el usuario pinche Confirmar/Cancelar.
# Solo hay una confirmación viva a la vez (el worker está bloqueado durante ella).
_confirm_event = threading.Event()
_confirm_result = {"ok": False}
_CONFIRM_TIMEOUT = 300  # s: si el usuario no responde, se cancela (opción conservadora)


def _hud_confirm(prompt: str) -> bool:
    """Confirmador del HUD: publica el borrador al navegador y BLOQUEA el worker hasta
    que el usuario pincha Confirmar/Cancelar. Ante silencio (timeout), NO envía (§8)."""
    _confirm_result["ok"] = False
    _confirm_event.clear()
    bus.set_estado("escuchando")
    bus.pedir_confirmacion(prompt)
    respondido = _confirm_event.wait(timeout=_CONFIRM_TIMEOUT)
    bus.set_estado("pensando")
    return bool(respondido and _confirm_result["ok"])


def _responder_confirmacion(ok: bool) -> None:
    _confirm_result["ok"] = ok
    _confirm_event.set()


def _procesar(texto: str) -> None:
    """Ejecuta un turno del agente publicando el estado al bus. Corre SIEMPRE en el
    hilo worker (nunca en el hilo de una petición HTTP), por el requisito de hilo único
    de Playwright. La respuesta llega al navegador por SSE (bus.log('answer', …))."""
    bus.set_tarea(texto)
    bus.log("user", texto)
    bus.set_estado("pensando")
    try:
        answer = _get_cris().send(texto)
    except Exception as exc:
        bus.log("error", str(exc))
        bus.set_estado("reposo")
        bus.set_tarea("")
        return
    _, respuesta = split_final(answer)
    if estado.esta_activo():
        bus.set_estado("hablando")
        try:
            voz.hablar(respuesta)
        except Exception as exc:
            bus.log("error", f"voz: {exc}")
    bus.log("answer", respuesta)
    bus.set_estado("reposo")
    bus.set_tarea("")


def _worker() -> None:
    """Hilo único que procesa los turnos del agente en serie desde la cola. Posee el
    agente (y por tanto el navegador y el cliente Gemini) en un solo hilo."""
    while True:
        texto = _JOBS.get()
        try:
            _procesar(texto)
        except Exception as exc:  # el worker nunca debe morir por un turno puntual
            bus.log("error", str(exc))
            bus.set_estado("reposo")
            bus.set_tarea("")
        finally:
            _JOBS.task_done()


# --- Módulo de música (Tanda B): estado y control desde el HUD --------------------
# El reproductor es thread-safe e independiente del worker/Playwright, así que estas
# rutas lo llaman DIRECTAMENTE desde el hilo HTTP (no se encolan en _JOBS). Import
# perezoso para no acoplar el arranque del HUD a VLC.
def _musica_estado() -> dict:
    try:
        from cristopher.musica import get_reproductor
        return get_reproductor().estado()
    except Exception as exc:
        return {"sonando": False, "error": str(exc)}


def _musica_control(accion: str, valor) -> dict:
    try:
        from cristopher.musica import get_reproductor
        r = get_reproductor()
        if accion == "pausar":
            msg = r.pausar()
        elif accion == "reanudar":
            msg = r.reanudar()
        elif accion == "siguiente":
            msg = r.siguiente()
        elif accion == "anterior":
            msg = r.anterior()
        elif accion == "volumen":
            msg = r.set_volumen(valor)
        elif accion == "seek":
            msg = r.buscar(valor)
        else:
            return {"ok": False, "error": f"acción desconocida: {accion}"}
        return {"ok": True, "msg": msg}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # silencia el log HTTP por defecto
        pass

    def handle_error(self, request, client_address):
        # El navegador aborta conexiones keep-alive/SSE al cerrar o recargar la
        # pestaña; Windows lo reporta como WinError 10053. Es ruido normal, no un
        # fallo real, así que no imprimimos el traceback para estos casos.
        exc = sys.exc_info()[1]
        if isinstance(exc, (BrokenPipeError, ConnectionResetError, ConnectionAbortedError)):
            return
        super().handle_error(request, client_address)

    # --- GET: estáticos, snapshot y SSE ---
    def do_GET(self):
        if self.path in ("/", "/index.html"):
            return self._static("index.html")
        if self.path == "/estado":
            return self._json(bus.snapshot())
        if self.path == "/eventos":
            return self._sse()
        if self.path == "/musica":
            return self._json(_musica_estado())
        # archivo estático por nombre
        nombre = self.path.lstrip("/").split("?")[0]
        if (STATIC / nombre).is_file():
            return self._static(nombre)
        self.send_error(404)

    def _static(self, nombre: str):
        p = STATIC / nombre
        if not p.is_file():
            return self.send_error(404)
        data = p.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", _CT.get(p.suffix, "application/octet-stream"))
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _json(self, obj):
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _sse(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        q = bus.suscribir()
        try:
            # Envía el snapshot inicial como primer evento.
            self.wfile.write(bus.sse({"tipo": "snapshot", "datos": bus.snapshot()}).encode("utf-8"))
            self.wfile.flush()
            while True:
                msg = q.get()
                self.wfile.write(bus.sse(msg).encode("utf-8"))
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
            pass
        finally:
            bus.desuscribir(q)

    # --- POST: entrada del usuario y confirmaciones ---
    def do_POST(self):
        if self.path == "/enviar":
            return self._post_enviar()
        if self.path == "/confirmar":
            return self._post_confirmar()
        if self.path == "/musica/control":
            return self._post_musica()
        return self.send_error(404)

    def _post_enviar(self):
        n = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(n) if n else b"{}"
        try:
            texto = (json.loads(body).get("texto") or "").strip()
        except Exception:
            texto = ""
        # Encola el turno para el hilo worker y responde de inmediato (no bloquea el
        # hilo HTTP): la respuesta del agente llega al navegador por SSE.
        if texto:
            _JOBS.put(texto)
        self._json({"queued": bool(texto)})

    def _post_confirmar(self):
        n = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(n) if n else b"{}"
        try:
            ok = bool(json.loads(body).get("ok"))
        except Exception:
            ok = False
        _responder_confirmacion(ok)
        self._json({"ok": ok})

    def _post_musica(self):
        n = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(n) if n else b"{}"
        try:
            data = json.loads(body)
            accion = str(data.get("accion") or "")
            valor = data.get("valor")
        except Exception:
            accion, valor = "", None
        self._json(_musica_control(accion, valor))


def _muestreo_metricas():
    try:
        import psutil
    except Exception:
        return
    psutil.cpu_percent()  # primer sondeo (calienta)
    while True:
        try:
            cpu = psutil.cpu_percent(interval=1.0)
            ram = psutil.virtual_memory().percent
            bus.set_metricas(cpu, ram)
        except Exception:
            return


def _demonio_proactivo():
    try:
        from cristopher.proactivo import Demonio
        Demonio(on_aviso=lambda n, m: bus.add_alerta(n, m)).run()
    except Exception:
        pass  # degrada con elegancia si no hay Google/permiso


def main() -> int:
    # En el HUD, la confirmación de acciones irreversibles (§9) es por clic, no por
    # stdin: el usuario escribe desde el navegador y no vería un input() del servidor.
    set_confirmer(_hud_confirm)
    threading.Thread(target=_worker, daemon=True).start()
    threading.Thread(target=_muestreo_metricas, daemon=True).start()
    threading.Thread(target=_demonio_proactivo, daemon=True).start()
    srv = ThreadingHTTPServer(("127.0.0.1", HUD_PORT), Handler)
    url = f"http://localhost:{HUD_PORT}"
    print(f"CRISTOPHER HUD en {url}  (Ctrl+C para salir)")
    try:
        webbrowser.open(url)
    except Exception:
        pass
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nHUD detenido.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
