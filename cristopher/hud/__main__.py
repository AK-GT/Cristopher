"""Servidor del HUD de CRISTOPHER (Fase 8).

Web app LOCAL: sirve el HUD y lo conecta al estado REAL de CRISTOPHER por streaming
(SSE). El usuario escribe desde el navegador; el bucle corre en el servidor y publica
estado/trazas/respuesta al bus, que el HUD refleja en vivo.

Uso:  python -m cristopher.hud
"""

from __future__ import annotations

import base64
import json
import os
import queue
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from cristopher import bus, estado, voz
from cristopher.agent import Cristopher, split_final
from cristopher.config import (
    HUD_PORT,
    HUD_UPLOADS_DIR,
    POLL_WHATSAPP_COMANDOS,
    WHATSAPP_HEARTBEAT_SEGUNDOS,
)
from cristopher.tools.google_tools import set_confirmer, set_confirmer_texto

STATIC = Path(__file__).parent / "static"

# Cola de trabajos del agente. Un ÚNICO hilo worker los procesa en serie, así el
# navegador (Playwright usa una API SÍNCRONA ligada al hilo que la creó) y el cliente
# Gemini viven siempre en el MISMO hilo. Sin esto, ThreadingHTTPServer atendería cada
# petición en un hilo distinto y la 2ª herramienta de navegador reventaría con
# "cannot switch to a different thread". Las conexiones SSE siguen en sus propios hilos.
# `origen` es None para un mensaje tecleado/programado (comportamiento de siempre) o
# {"tipo": "whatsapp", "chat_id": ...} cuando lo disparó whatsapp_comandos.py — en ese
# caso _procesar responde por WhatsApp en vez de (además de) la UI del HUD.
# `media` es None o una lista de (bytes, mime_type) — imágenes adjuntadas en el HUD
# (ver `_post_enviar_media`) que se pasan a `Cristopher.send(..., imagenes=media)`.
_JOBS: "queue.Queue[tuple[str, int | None, dict | None, list[tuple[bytes, str]] | None]]" = queue.Queue()
_CRIS: Cristopher | None = None

_CT = {".html": "text/html", ".css": "text/css", ".js": "application/javascript",
       ".json": "application/json"}


def _guardar_imagen_adjunta(raw: bytes, mime: str) -> str | None:
    """Guarda en disco una imagen adjuntada por `/enviar_media` y devuelve su ruta
    absoluta, o None si falla. Solo escribe el archivo — quién lo mencione en el
    turno (para que el modelo pueda pasárselo a una tool como huella_buscar_cara) es
    cosa de quien llama a esta función."""
    try:
        ext = (mime.split(";")[0].split("/")[-1] or "png") if "/" in mime else "png"
        ext = "".join(c for c in ext if c.isalnum()) or "png"
        HUD_UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
        ruta = HUD_UPLOADS_DIR / f"{uuid.uuid4().hex}.{ext}"
        ruta.write_bytes(raw)
        return str(ruta)
    except OSError as exc:
        bus.log("error", f"adjunto: no se pudo guardar la imagen en disco ({exc})")
        return None


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
_confirm_result = {"ok": False, "texto": ""}
_CONFIRM_TIMEOUT = 300  # s: si el usuario no responde, se cancela (opción conservadora)


def _hud_confirm(prompt: str) -> bool:
    """Confirmador del HUD (bool puro) para acciones irreversibles genéricas
    (enviar correo, crear evento, parar sesión colgada): el modal no muestra campo
    de texto libre para estas — solo Confirmar/Cancelar."""
    ok, _texto = _hud_confirm_texto(prompt, permite_texto=False)
    return ok


def _hud_confirm_texto(prompt: str, permite_texto: bool = True) -> "tuple[bool, str]":
    """Confirmador del HUD con texto libre: publica el borrador al
    navegador y BLOQUEA el worker hasta que el usuario pincha Confirmar/Cancelar (o
    escribe su propia respuesta y la envía). Ante silencio (timeout), NO envía (§8)."""
    _confirm_result["ok"] = False
    _confirm_result["texto"] = ""
    _confirm_event.clear()
    bus.set_estado("escuchando")
    bus.pedir_confirmacion(prompt, permite_texto)
    respondido = _confirm_event.wait(timeout=_CONFIRM_TIMEOUT)
    bus.set_estado("pensando")
    if not respondido:
        return False, ""
    return bool(_confirm_result["ok"]), _confirm_result["texto"]


def _responder_confirmacion(ok: bool, texto: str = "") -> None:
    _confirm_result["ok"] = ok
    _confirm_result["texto"] = texto
    _confirm_event.set()


# --- Confirmación de acciones irreversibles cuando el turno viene de WhatsApp -----
# Si nadie está delante del HUD (el turno lo disparó un mensaje desde el móvil), pedir
# confirmación por el propio WhatsApp en vez de esperar un clic que nadie va a dar.
# _procesar() intercambia el confirmador global a estas funciones solo durante ese
# turno (el worker procesa un turno a la vez, así que el swap es seguro) y lo
# restaura a _hud_confirm/_hud_confirm_texto al terminar.
def _whatsapp_confirm_texto(prompt: str, chat_id: str, permite_texto: bool = True) -> "tuple[bool, str]":
    from cristopher import whatsapp_client
    from cristopher.recordatorios import get_recordatorios

    try:
        whatsapp_client.send(chat_id, f"{prompt}\n\n(Responde sí/no, o escribe tu propia respuesta.)")
    except Exception as exc:
        bus.log("error", f"whatsapp: {exc}")
        return False, ""

    rec = get_recordatorios()
    limite = time.monotonic() + _CONFIRM_TIMEOUT
    while time.monotonic() < limite:
        time.sleep(POLL_WHATSAPP_COMANDOS)
        try:
            data = whatsapp_client.check_new()
        except Exception:
            continue
        chat = next((c for c in data.get("chats", []) if c["chat_id"] == chat_id), None)
        if not chat:
            continue
        clave = f"wa_confirm:{chat['ultimo_id']}"
        if rec.ya_visto(clave):
            continue
        rec.marcar_visto(clave)
        respuesta = chat["ultimo_texto"].strip()
        bajo = respuesta.lower()
        if bajo in {"si", "sí", "s", "yes", "y"}:
            return True, ""
        if bajo in {"no", "n"}:
            return False, ""
        return (True, respuesta) if permite_texto else (False, "")
    return False, ""  # silencio = no (§8), igual que _hud_confirm_texto


def _whatsapp_confirm(prompt: str, chat_id: str) -> bool:
    ok, _texto = _whatsapp_confirm_texto(prompt, chat_id, permite_texto=False)
    return ok


def _whatsapp_heartbeat(chat_id: str) -> None:
    """Disparo único de un threading.Timer (ver _procesar): si el turno sigue en
    marcha pasados WHATSAPP_HEARTBEAT_SEGUNDOS, manda un aviso de que sigue
    trabajando — sin esto, una tool lenta (huella_investigar, p. ej.) se siente
    como que CRISTOPHER se colgó, porque desde el móvil no se ve el HUD."""
    try:
        from cristopher import whatsapp_client
        whatsapp_client.send(chat_id, "Sigo en ello, dame un momento más...")
    except Exception as exc:
        bus.log("error", f"whatsapp heartbeat: {exc}")


def _procesar(
    texto: str,
    prog_id: int | None = None,
    origen: dict | None = None,
    media: list[tuple[bytes, str]] | None = None,
) -> None:
    """Ejecuta un turno del agente publicando el estado al bus. Corre SIEMPRE en el
    hilo worker (nunca en el hilo de una petición HTTP), por el requisito de hilo único
    de Playwright. La respuesta llega al navegador por SSE (bus.log('answer', …)).
    `prog_id` viene puesto cuando este turno lo disparó el programador de tareas
    (cristopher.programador) en vez de un mensaje tecleado — al terminar, marca esa
    fila como hecha/error para que el demonio no la vuelva a lanzar. `origen` viene
    puesto cuando lo disparó whatsapp_comandos.py — mientras dure el turno, cualquier
    confirmación de acción irreversible se pide por WhatsApp (nadie ve el HUD desde el
    móvil) y la respuesta final se manda también por WhatsApp, no solo por SSE. `media`
    viene puesto cuando el usuario adjuntó una imagen (ver `_post_enviar_media`)."""
    es_whatsapp = bool(origen and origen.get("tipo") == "whatsapp")
    chat_id = origen.get("chat_id") if es_whatsapp else None
    if es_whatsapp:
        set_confirmer(lambda p: _whatsapp_confirm(p, chat_id))
        set_confirmer_texto(lambda p: _whatsapp_confirm_texto(p, chat_id))

    bus.set_tarea(texto)
    bus.log("user", texto)
    bus.set_estado("pensando")

    # Aviso de "sigo en ello" si el turno tarda (ver WHATSAPP_HEARTBEAT_SEGUNDOS):
    # disparo único, se cancela en el finally tanto si el turno termina antes como
    # si falla. Solo por WhatsApp — el HUD ya muestra el estado "pensando" en vivo.
    heartbeat = None
    if es_whatsapp:
        heartbeat = threading.Timer(WHATSAPP_HEARTBEAT_SEGUNDOS, _whatsapp_heartbeat, args=(chat_id,))
        heartbeat.daemon = True
        heartbeat.start()
    try:
        try:
            answer = _get_cris().send(texto, imagenes=media)
        except Exception as exc:
            bus.log("error", str(exc))
            if es_whatsapp:
                # Sin esto, un fallo del turno (p. ej. un documento con un mimetype
                # que Gemini rechaza) se quedaba solo en el log del HUD — silencio
                # total en el móvil. Best-effort: si hasta el aviso de error falla,
                # no debe tumbar nada más.
                try:
                    from cristopher import whatsapp_client
                    whatsapp_client.send(chat_id, f"Algo falló: {exc}")
                except Exception as exc2:
                    bus.log("error", f"whatsapp: {exc2}")
            bus.set_estado("reposo")
            bus.set_tarea("")
            if prog_id is not None:
                from cristopher.programador import get_programador
                get_programador().marcar_error(prog_id, str(exc))
            return
        _, respuesta = split_final(answer)
        if estado.esta_activo():
            bus.set_estado("hablando")
            try:
                voz.hablar(respuesta)
            except Exception as exc:
                bus.log("error", f"voz: {exc}")
        bus.log("answer", respuesta)
        if es_whatsapp:
            try:
                from cristopher import whatsapp_client
                whatsapp_client.send(chat_id, respuesta)
            except Exception as exc:
                bus.log("error", f"whatsapp: {exc}")
        bus.set_estado("reposo")
        bus.set_tarea("")
        if prog_id is not None:
            from cristopher.programador import get_programador
            get_programador().marcar_hecha(prog_id)
    finally:
        if heartbeat:
            heartbeat.cancel()
        if es_whatsapp:
            set_confirmer(_hud_confirm)
            set_confirmer_texto(_hud_confirm_texto)


def _worker() -> None:
    """Hilo único que procesa los turnos del agente en serie desde la cola. Posee el
    agente (y por tanto el navegador y el cliente Gemini) en un solo hilo."""
    while True:
        texto, prog_id, origen, media = _JOBS.get()
        try:
            _procesar(texto, prog_id, origen, media)
        except Exception as exc:  # el worker nunca debe morir por un turno puntual
            bus.log("error", str(exc))
            bus.set_estado("reposo")
            bus.set_tarea("")
            if prog_id is not None:
                from cristopher.programador import get_programador
                get_programador().marcar_error(prog_id, str(exc))
        finally:
            _JOBS.task_done()


def _on_tarea_lista(prog_id: int, texto: str) -> None:
    """Callback del demonio proactivo (cristopher.proactivo) cuando una tarea
    programada/encadenada está lista para dispararse: la encola igual que un mensaje
    tecleado, pero con su id para poder marcarla hecha/error al terminar."""
    _JOBS.put((texto, prog_id, None, None))


def _on_whatsapp_comando(
    texto: str, chat_id: str, media: list[tuple[bytes, str]] | None = None
) -> None:
    """Callback de whatsapp_comandos.py cuando el usuario se manda a sí mismo un
    mensaje con el prefijo de comando (o una nota de voz — ver whatsapp_comandos.py).
    Manda un ack inmediato (el bucle ReAct puede tardar) y encola el turno con su
    origen para que _procesar sepa responder por WhatsApp y pedir confirmaciones por
    ese mismo canal. `media` trae los bytes de una imagen adjunta (foto con caption
    `/Cristopher`), mismo campo que usa `_post_enviar_media` para el HUD."""
    try:
        from cristopher import whatsapp_client
        whatsapp_client.send(chat_id, "Recibido, lo reviso...")
    except Exception as exc:
        bus.log("error", f"whatsapp: {exc}")
    _JOBS.put((texto, None, {"tipo": "whatsapp", "chat_id": chat_id}, media))


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


# --- Notas (utilidades, Tanda A): panel discreto en el HUD ------------------------
# El almacén de notas es thread-safe (su propio lock), así que se consulta DIRECTAMENTE
# desde el hilo HTTP (no toca el worker/Playwright). Import perezoso para no acoplar el
# arranque del HUD. El frontend sondea /notas cada pocos segundos.
def _concentracion_estado() -> dict:
    try:
        from cristopher import concentracion
        return concentracion.estado_actual()
    except Exception as exc:
        return {"activo": False, "error": str(exc)}


def _notas_recientes(n: int = 8) -> dict:
    try:
        from cristopher.notas import get_notas
        rows = get_notas().listar()[:n]
        return {"notas": [{"id": r[0], "texto": r[1], "creado": r[2]} for r in rows]}
    except Exception as exc:
        return {"notas": [], "error": str(exc)}


# --- Modo agentes (Fase 2): sesiones vivas de Claude Code -------------------------
# El gestor es thread-safe (su propio lock), así que estas rutas lo llaman
# DIRECTAMENTE desde el hilo HTTP, igual que música/notas — nunca pasan por _JOBS
# (si lo hicieran, escribir a una sesión esperaría detrás del turno en curso de
# CRISTOPHER). Import perezoso para no acoplar el arranque del HUD.
def _on_evento_sesion(nombre: str, evento: dict) -> None:
    """Callback del gestor (cristopher.sesiones): republica al bus del HUD."""
    from cristopher.sesiones import get_gestor_sesiones
    try:
        bus.set_sesiones(get_gestor_sesiones().listar())
        entrada = evento.get("entrada")
        if entrada:
            bus.add_sesion_entrada(nombre, entrada)
    except Exception:
        pass


def _sesiones_listar(origen: str = "") -> dict:
    from cristopher.sesiones import get_gestor_sesiones
    sesiones = get_gestor_sesiones().listar()
    if origen:
        sesiones = [s for s in sesiones if s.get("origen") == origen]
    return {"sesiones": sesiones}


def _sesiones_leer(nombre: str, n: int) -> dict:
    from cristopher.sesiones import SesionesError, get_gestor_sesiones
    try:
        return {"transcript": get_gestor_sesiones().leer(nombre, n)}
    except SesionesError as exc:
        return {"error": str(exc)}


def _sesiones_crear(
    nombre: str, ruta: str, tarea: str, aislamiento: str = "directo",
    tipo: str = "code", modelo: str = "sonnet", quality_gate: str = "",
) -> dict:
    from cristopher.sesiones import SesionesError, get_gestor_sesiones
    try:
        msg = get_gestor_sesiones().crear(
            nombre, ruta, tarea, aislamiento, tipo, modelo, quality_gate=quality_gate,
        )
        return {"ok": True, "msg": msg}
    except SesionesError as exc:
        return {"ok": False, "error": str(exc)}


def _sesiones_diff(nombre: str) -> dict:
    from cristopher.sesiones import SesionesError, get_gestor_sesiones
    try:
        return {"diff": get_gestor_sesiones().diff(nombre)}
    except SesionesError as exc:
        return {"error": str(exc)}


def _sesiones_conflictos() -> dict:
    from cristopher.sesiones import get_gestor_sesiones
    return {"conflictos": get_gestor_sesiones().conflictos()}


def _sesiones_limpiar_worktree(nombre: str) -> dict:
    from cristopher.sesiones import SesionesError, get_gestor_sesiones
    try:
        msg = get_gestor_sesiones().limpiar_worktree(nombre)
        return {"ok": True, "msg": msg}
    except SesionesError as exc:
        return {"ok": False, "error": str(exc)}


def _sesiones_escribir(nombre: str, mensaje: str) -> dict:
    from cristopher.sesiones import SesionesError, get_gestor_sesiones
    try:
        msg = get_gestor_sesiones().escribir(nombre, mensaje)
        return {"ok": True, "msg": msg}
    except SesionesError as exc:
        return {"ok": False, "error": str(exc)}


def _sesiones_parar(nombre: str) -> dict:
    from cristopher.sesiones import SesionesError, get_gestor_sesiones
    try:
        msg = get_gestor_sesiones().parar(nombre)
        return {"ok": True, "msg": msg}
    except SesionesError as exc:
        return {"ok": False, "error": str(exc)}


# --- Modo code (Fase 1): entrar/salir por botón + tareas para el widget -----------
# `estado_code` es un flag sin lock propio (como estado.py/estado_diario.py), así que
# es seguro leerlo/mutarlo directamente desde el hilo HTTP, igual que música/notas.
def _modo_code_set(activo: bool) -> dict:
    from cristopher import estado_code
    if activo:
        estado_code.activar()
    else:
        estado_code.desactivar()
    bus.set_modo_code(activo)
    return {"ok": True, "activo": activo}


def _tareas_listar() -> dict:
    try:
        from cristopher.tareas import get_tareas
        rows = get_tareas().listar()
        return {"tareas": [
            {"id": r[0], "texto": r[1], "estado": r[2], "creado": r[3]} for r in rows
        ]}
    except Exception as exc:
        return {"tareas": [], "error": str(exc)}


def _tareas_actualizar_estado(id_: int, estado: str) -> dict:
    """Cambia el estado de una tarea desde el tablero (drag & drop, Fase 2). Mismo
    estado libre (TEXT) que tareas.py ya acepta; 'hecho' la elimina."""
    try:
        from cristopher.tareas import get_tareas
        texto = get_tareas().actualizar_estado(id_, estado)
        if texto is None:
            return {"ok": False, "error": f"No hay ninguna tarea con id #{id_}."}
        return {"ok": True}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


# --- Programador de tareas: panel de solo listar/cancelar (Fase 3) ----------------
# Programador es thread-safe (su propio lock, igual que Recordatorios/Tareas), así
# que estas rutas lo llaman DIRECTAMENTE desde el hilo HTTP, sin pasar por _JOBS
# (el disparo real de las tareas listas lo hace el demonio vía _on_tarea_lista).
def _programadas_listar() -> dict:
    try:
        from cristopher.programador import get_programador
        return {"programadas": get_programador().listar()}
    except Exception as exc:
        return {"programadas": [], "error": str(exc)}


def _programadas_cancelar(id_: int) -> dict:
    try:
        from cristopher.programador import get_programador
        texto = get_programador().cancelar(id_)
        if texto is None:
            return {"ok": False, "error": f"No hay ninguna tarea programada con id #{id_}."}
        return {"ok": True}
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
        if self.path == "/notas":
            return self._json(_notas_recientes())
        if self.path == "/programadas":
            return self._json(_programadas_listar())
        if self.path == "/concentracion":
            return self._json(_concentracion_estado())
        if self.path == "/sesiones" or self.path.startswith("/sesiones?"):
            qs = parse_qs(urlparse(self.path).query)
            origen = (qs.get("origen") or [""])[0]
            return self._json(_sesiones_listar(origen))
        if self.path.startswith("/sesiones/leer"):
            qs = parse_qs(urlparse(self.path).query)
            nombre = (qs.get("nombre") or [""])[0]
            n = int((qs.get("n") or ["20"])[0])
            return self._json(_sesiones_leer(nombre, n))
        if self.path.startswith("/sesiones/diff"):
            qs = parse_qs(urlparse(self.path).query)
            nombre = (qs.get("nombre") or [""])[0]
            return self._json(_sesiones_diff(nombre))
        if self.path == "/sesiones/conflictos":
            return self._json(_sesiones_conflictos())
        if self.path == "/tareas":
            return self._json(_tareas_listar())
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
        if self.path == "/enviar_media":
            return self._post_enviar_media()
        if self.path == "/confirmar":
            return self._post_confirmar()
        if self.path == "/musica/control":
            return self._post_musica()
        if self.path == "/sesiones/crear":
            return self._post_sesiones_crear()
        if self.path == "/sesiones/escribir":
            return self._post_sesiones_escribir()
        if self.path == "/sesiones/parar":
            return self._post_sesiones_parar()
        if self.path == "/sesiones/limpiar_worktree":
            return self._post_sesiones_limpiar_worktree()
        if self.path == "/tareas/estado":
            return self._post_tareas_estado()
        if self.path == "/programadas/cancelar":
            return self._post_programadas_cancelar()
        if self.path == "/modo_code":
            return self._post_modo_code()
        return self.send_error(404)

    def _post_enviar(self):
        n = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(n) if n else b"{}"
        try:
            texto = (json.loads(body).get("texto") or "").strip()
        except Exception:
            texto = ""
        # Barge-in: un mensaje nuevo interrumpe lo que se esté hablando ahora mismo.
        # Corre en EL HILO HTTP (no el worker, que puede estar bloqueado dentro de
        # `voz.hablar()` -> `sd.wait()`); `sd.stop()` desbloquea ese wait desde
        # fuera y no hace nada si no había audio sonando.
        if texto:
            voz.detener_habla()
        # Encola el turno para el hilo worker y responde de inmediato (no bloquea el
        # hilo HTTP): la respuesta del agente llega al navegador por SSE.
        if texto:
            _JOBS.put((texto, None, None, None))
        self._json({"queued": bool(texto)})

    def _post_enviar_media(self):
        """Adjunto del HUD (imagen o audio, ver A.3/A.4 del plan de entrada multimodal).
        El frontend manda el archivo en base64 dentro del JSON (mismo patrón que el
        resto del HUD — sin multipart, ver `_body_json`). Una imagen se encola con sus
        bytes para que `Cristopher.send(..., imagenes=...)` "la vea" dentro del turno
        normal; un audio se transcribe en ESTE hilo HTTP (no toca Playwright ni el
        estado del agente, es seguro) y entra a la cola como si fuera texto tecleado."""
        n = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(n) if n else b"{}"
        try:
            data = json.loads(body)
            tipo = str(data.get("tipo") or "")
            mime = str(data.get("mime") or "")
            texto = (data.get("texto") or "").strip()
            raw = base64.b64decode(data.get("datos_b64") or "")
        except Exception as exc:
            bus.log("error", f"adjunto: petición inválida ({exc})")
            return self._json({"queued": False, "error": "petición inválida"})

        if tipo not in ("imagen", "audio") or not raw:
            bus.log("error", "adjunto: falta el archivo o el tipo no es válido")
            return self._json({"queued": False, "error": "falta el archivo o el tipo no es válido"})

        # Barge-in, igual que /enviar.
        voz.detener_habla()

        if tipo == "audio":
            sufijo = "." + mime.split(";")[0].split("/")[-1] if "/" in mime else ".audio"
            ruta = None
            try:
                with tempfile.NamedTemporaryFile(suffix=sufijo, delete=False) as f:
                    f.write(raw)
                    ruta = f.name
                transcrito = voz.transcribir_archivo(ruta)
            except Exception as exc:
                bus.log("error", f"transcripción de audio: {exc}")
                return self._json({"queued": False, "error": str(exc)})
            finally:
                if ruta:
                    try:
                        os.remove(ruta)
                    except OSError:
                        pass
            texto_final = f"{texto}\n{transcrito}".strip() if texto else transcrito
            if not texto_final:
                bus.log("error", "adjunto: no se entendió el audio (transcripción vacía)")
                return self._json({"queued": False, "error": "no se entendió el audio"})
            _JOBS.put((texto_final, None, None, None))
            return self._json({"queued": True})

        # tipo == "imagen"
        texto_final = texto or "(Imagen adjunta, sin texto adicional.)"
        ruta = _guardar_imagen_adjunta(raw, mime)
        if ruta:
            # Los bytes solos bastan para que el modelo "vea" la imagen (Part de
            # visión, ver Cristopher.send), pero tools como huella_buscar_cara
            # necesitan un archivo real — sin decirle la ruta aquí no tendría cómo
            # invocarlas sobre esta foto en concreto.
            texto_final += f"\n\n[Imagen adjunta guardada en disco en: {ruta}]"
        _JOBS.put((texto_final, None, None, [(raw, mime or "image/png")]))
        self._json({"queued": True})

    def _post_confirmar(self):
        n = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(n) if n else b"{}"
        try:
            data = json.loads(body)
            ok = bool(data.get("ok"))
            texto = (data.get("texto") or "").strip()
        except Exception:
            ok, texto = False, ""
        _responder_confirmacion(ok, texto)
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

    def _body_json(self) -> dict:
        n = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(n) if n else b"{}"
        try:
            return json.loads(body)
        except Exception:
            return {}

    def _post_sesiones_crear(self):
        data = self._body_json()
        self._json(_sesiones_crear(
            str(data.get("nombre") or ""),
            str(data.get("ruta") or ""),
            str(data.get("tarea") or ""),
            str(data.get("aislamiento") or "directo"),
            str(data.get("tipo") or "code"),
            str(data.get("modelo") or "sonnet"),
            str(data.get("quality_gate") or ""),
        ))

    def _post_sesiones_escribir(self):
        data = self._body_json()
        self._json(_sesiones_escribir(
            str(data.get("nombre") or ""),
            str(data.get("mensaje") or ""),
        ))

    def _post_sesiones_parar(self):
        data = self._body_json()
        self._json(_sesiones_parar(str(data.get("nombre") or "")))

    def _post_sesiones_limpiar_worktree(self):
        data = self._body_json()
        self._json(_sesiones_limpiar_worktree(str(data.get("nombre") or "")))

    def _post_tareas_estado(self):
        data = self._body_json()
        try:
            id_ = int(data.get("id"))
        except (TypeError, ValueError):
            return self._json({"ok": False, "error": "id inválido"})
        self._json(_tareas_actualizar_estado(id_, str(data.get("estado") or "")))

    def _post_programadas_cancelar(self):
        data = self._body_json()
        try:
            id_ = int(data.get("id"))
        except (TypeError, ValueError):
            return self._json({"ok": False, "error": "id inválido"})
        self._json(_programadas_cancelar(id_))

    def _post_modo_code(self):
        data = self._body_json()
        self._json(_modo_code_set(bool(data.get("activo"))))


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
        Demonio(
            on_aviso=lambda n, m: bus.add_alerta(n, m),
            on_tarea_lista=_on_tarea_lista,
        ).run()
    except Exception:
        pass  # degrada con elegancia si no hay Google/permiso


def _whatsapp_comandos_watcher():
    try:
        from cristopher import whatsapp_comandos
        whatsapp_comandos.vigilar(_on_whatsapp_comando)
    except Exception:
        pass  # degrada con elegancia si no hay Node/WhatsApp vinculado


def _chrome_path() -> str | None:
    """Ruta a chrome.exe si está instalado en una ubicación estándar; si no, None."""
    candidatos = [
        Path(os.environ.get("ProgramFiles", "")) / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("ProgramFiles(x86)", "")) / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("LocalAppData", "")) / "Google/Chrome/Application/chrome.exe",
    ]
    for c in candidatos:
        if c.exists():
            return str(c)
    return None


def main() -> int:
    # En el HUD, la confirmación de acciones irreversibles (§9) es por clic, no por
    # stdin: el usuario escribe desde el navegador y no vería un input() del servidor.
    set_confirmer(_hud_confirm)
    set_confirmer_texto(_hud_confirm_texto)
    from cristopher.sesiones import get_gestor_sesiones
    get_gestor_sesiones().set_on_evento(_on_evento_sesion)
    from cristopher.programador import get_programador
    get_programador().recuperar_huerfanas()
    threading.Thread(target=_worker, daemon=True).start()
    threading.Thread(target=_muestreo_metricas, daemon=True).start()
    threading.Thread(target=_demonio_proactivo, daemon=True).start()
    threading.Thread(target=_whatsapp_comandos_watcher, daemon=True).start()
    srv = ThreadingHTTPServer(("127.0.0.1", HUD_PORT), Handler)
    url = f"http://localhost:{HUD_PORT}"
    print(f"CRISTOPHER HUD en {url}  (Ctrl+C para salir)")
    try:
        # Ventana propia en el lado izquierdo: la canción de arranque (escucha.py)
        # abre la suya a la derecha (--window-position=960,0); con webbrowser.open()
        # normal, Windows las metía como pestañas de la misma ventana y se tapaban.
        chrome = _chrome_path()
        if chrome:
            subprocess.Popen([chrome, "--new-window", "--window-position=0,0",
                               "--window-size=960,1040", url])
        else:
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
