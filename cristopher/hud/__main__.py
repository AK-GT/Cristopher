"""Servidor del HUD de CRISTOPHER (Fase 8).

Web app LOCAL: sirve el HUD y lo conecta al estado REAL de CRISTOPHER por streaming
(SSE). El usuario escribe desde el navegador; el bucle corre en el servidor y publica
estado/trazas/respuesta al bus, que el HUD refleja en vivo.

Uso:  python -m cristopher.hud
"""

from __future__ import annotations

import json
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from cristopher import bus, estado, voz
from cristopher.agent import Cristopher, split_final
from cristopher.config import HUD_PORT

STATIC = Path(__file__).parent / "static"
_SEND_LOCK = threading.Lock()
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


def _procesar(texto: str) -> str:
    """Ejecuta un turno del agente publicando el estado al bus (serializado)."""
    with _SEND_LOCK:
        bus.set_tarea(texto)
        bus.log("user", texto)
        bus.set_estado("pensando")
        try:
            answer = _get_cris().send(texto)
        except Exception as exc:
            bus.log("error", str(exc))
            bus.set_estado("reposo")
            bus.set_tarea("")
            return f"[error] {exc}"
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
        return respuesta


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # silencia el log HTTP por defecto
        pass

    # --- GET: estáticos, snapshot y SSE ---
    def do_GET(self):
        if self.path in ("/", "/index.html"):
            return self._static("index.html")
        if self.path == "/estado":
            return self._json(bus.snapshot())
        if self.path == "/eventos":
            return self._sse()
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

    # --- POST: entrada del usuario ---
    def do_POST(self):
        if self.path != "/enviar":
            return self.send_error(404)
        n = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(n) if n else b"{}"
        try:
            texto = (json.loads(body).get("texto") or "").strip()
        except Exception:
            texto = ""
        if not texto:
            return self._json({"respuesta": ""})
        respuesta = _procesar(texto)
        self._json({"respuesta": respuesta})


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
