"""Demonio de proactividad de CRISTOPHER (Fase 7).

Vigila en segundo plano calendario, correo y recordatorios, y AVISA él por su cuenta.
Cada aviso se clasifica con un mini-modelo en 3 niveles de prioridad; el nivel decide la
insistencia: 1 = terminal atenuado, 2 = terminal destacado, 3 = terminal + voz (TTS).

Uso:  python -m cristopher.proactivo
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Callable, Optional

from cristopher.config import (
    CLASSIFIER_MODEL,
    DIARIO_HORA_AVISO,
    FALLBACK_MODEL,
    GMAIL_QUERY_PROACTIVO,
    LEAD_MINUTOS,
    POLL_SEGUNDOS,
    SESION_COLGADA_SEGUNDOS,
    SESION_CONTEXTO_AVISO_PCT,
)
from cristopher.recordatorios import get_recordatorios

CYAN = "\033[36m"
DIM = "\033[2m"
AMBER = "\033[33m"
RED = "\033[31m"
BOLD = "\033[1m"
RESET = "\033[0m"


def clasificar(texto: str) -> int:
    """Clasifica la urgencia del aviso en 1 (informativo), 2 (normal) o 3 (urgente).
    Usa un mini-modelo con fallback; si todo falla, devuelve 2 (§8 degrada con elegancia)."""
    from google import genai
    from google.genai import errors

    from cristopher.config import get_api_key

    prompt = (
        "Clasifica la URGENCIA de este aviso para el usuario en un dígito: "
        "1 = informativo/poco urgente, 2 = normal, 3 = urgente (requiere atención ya). "
        "Responde SOLO el dígito.\n\nAviso: " + texto
    )
    try:
        client = genai.Client(api_key=get_api_key())
    except Exception:
        return 2
    for model in [CLASSIFIER_MODEL, FALLBACK_MODEL]:
        if not model:
            continue
        try:
            r = client.models.generate_content(model=model, contents=prompt)
            for ch in (r.text or ""):
                if ch in "123":
                    return int(ch)
            return 2
        except errors.APIError as exc:
            if getattr(exc, "code", None) == 429:
                continue  # sin cuota: prueba el siguiente modelo
            return 2
        except Exception:
            return 2
    return 2


# Marcador PROPIO (no RESPONSE_MARKER de agent.py): build_system_prompt() ya
# incluye una instrucción MUY fuerte ("formato OBLIGATORIO") sobre qué significa
# ===RESPUESTA=== en el chat normal (razonamiento antes / respuesta al usuario
# después). Reusar ese mismo marcador con un significado distinto aquí generó,
# probado contra la API real, que el modelo ignorase esta redefinición puntual
# y siguiera el hábito global: puso el marcador al principio y trató TODO lo de
# después como "la respuesta para el usuario", mezclando narración y propuesta.
# Un marcador sin ese significado previo elimina la ambigüedad.
PROPUESTA_SESION_MARKER = "===PROPUESTA_PARA_LA_SESION==="


def _redactar_respuesta_sesion(sesion: dict, transcript: list[dict]) -> tuple[str, str]:
    """Redacta una propuesta para una sesión de Claude Code en estado 'pregunta',
    en DOS partes separadas por PROPUESTA_SESION_MARKER: (narración para el
    usuario — de qué sesión hablamos, qué contexto hay, por qué esta respuesta
    —, texto literal que se le mandaría a la sesión).

    No reusa el `Cristopher`/agent.py EN VIVO (vive en un único hilo worker por
    Playwright — ver `hud/__main__.py`; llamarlo desde este hilo del demonio
    podría corromper una conversación en curso). En su lugar, mismo patrón
    thread-safe que `clasificar()`: un `genai.Client` suelto, pero usando
    `agent.build_system_prompt()` como `system_instruction` (función pura, sin
    tocar Playwright ni estado compartido) para que la propuesta suene a
    CRISTOPHER de verdad."""
    from google import genai
    from google.genai import errors, types

    from cristopher.agent import build_system_prompt
    from cristopher.config import get_api_key

    convo = "\n".join(f"{e['rol']}: {e['texto']}" for e in transcript)
    prompt = (
        f"Una sesión de Claude Code llamada '{sesion['nombre']}' que delegaste "
        f"sobre un proyecto real se ha quedado esperando respuesta tuya. Carpeta "
        f"del proyecto: {sesion['ruta']}.\n\n"
        f"Transcript reciente de la sesión:\n{convo}\n\n"
        "Para ESTA tarea en concreto, IGNORA el formato habitual de "
        "===RESPUESTA=== de tu modo de chat normal: NO escribas esa línea en "
        "absoluto, aquí no aplica y no pinta nada. En su lugar, escribe EN DOS "
        f"PARTES separadas por la línea EXACTA {PROPUESTA_SESION_MARKER}:\n\n"
        "PARTE 1 (antes del marcador) — para el USUARIO, no para la sesión: "
        "explícale de qué sesión hablamos, qué te preguntó o en qué se quedó "
        "esperando, qué contexto tienes del proyecto, y POR QUÉ vas a responder "
        "lo que vas a responder (tu razonamiento de programador de élite). Habla "
        "con tu voz normal, como si se lo contaras al usuario antes de pedirle "
        "su OK.\n\n"
        f"PARTE 2 (después de {PROPUESTA_SESION_MARKER}) — el texto LITERAL que "
        "se le mandaría a la SESIÓN por su entrada estándar: nada de "
        "explicaciones, comillas ni menciones al usuario, es exactamente lo que "
        "la sesión recibiría."
    )
    try:
        client = genai.Client(api_key=get_api_key())
    except Exception as exc:
        return "", f"(no pude redactar una propuesta: {exc})"
    system_instruction = build_system_prompt()
    for model in [CLASSIFIER_MODEL, FALLBACK_MODEL]:
        if not model:
            continue
        try:
            r = client.models.generate_content(
                model=model, contents=prompt,
                config=types.GenerateContentConfig(system_instruction=system_instruction),
            )
            texto = (r.text or "").strip()
            if texto:
                # El modelo, por el hábito MUY reforzado del formato de chat
                # normal (build_system_prompt § "formato OBLIGATORIO"), a veces
                # cuela un ===RESPUESTA=== suelto pese a que se le pide no
                # hacerlo. Se limpia programáticamente en vez de depender solo
                # del prompt (probado contra la API real: el prompt solo no basta).
                from cristopher.agent import RESPONSE_MARKER
                texto = texto.replace(RESPONSE_MARKER, "").strip()
                if PROPUESTA_SESION_MARKER in texto:
                    narracion, _, propuesta = texto.partition(PROPUESTA_SESION_MARKER)
                    return narracion.strip(), propuesta.strip()
                return "", texto  # degrada con elegancia: sin marcador, todo es la propuesta
        except errors.APIError as exc:
            if getattr(exc, "code", None) == 429:
                continue  # sin cuota: prueba el siguiente modelo
            return "", f"(no pude redactar una propuesta: {exc})"
        except Exception as exc:
            return "", f"(no pude redactar una propuesta: {exc})"
    return "", "(no pude redactar una propuesta: todos los modelos agotaron su cuota)"


class Demonio:
    """Reúne avisos de las fuentes, los clasifica y los entrega según su nivel."""

    def __init__(
        self,
        hablar_fn: Optional[Callable[[str], None]] = None,
        clasificar_fn: Optional[Callable[[str], int]] = None,
        on_aviso: Optional[Callable[[int, str], None]] = None,
    ) -> None:
        self._hablar = hablar_fn
        self._clasificar = clasificar_fn or clasificar
        self._on_aviso = on_aviso  # callback opcional (p. ej. el HUD) por cada aviso
        self._rec = get_recordatorios()
        self._correo_base: Optional[set] = None  # ids de correo ya presentes al arrancar

    # --- Fuentes de avisos: devuelven [(clave, mensaje), ...] ------------------
    def _avisos_calendario(self) -> list[tuple[str, str]]:
        try:
            from cristopher.google_auth import get_calendar

            svc = get_calendar()
            now = datetime.now(timezone.utc)
            events = (
                svc.events()
                .list(
                    calendarId="primary", timeMin=now.isoformat(), singleEvents=True,
                    orderBy="startTime", maxResults=10,
                )
                .execute()
                .get("items", [])
            )
        except Exception:
            return []
        avisos = []
        for e in events:
            start = e.get("start", {}).get("dateTime")
            if not start:
                continue  # eventos de día completo: no son avisos "inminentes"
            try:
                inicio = datetime.fromisoformat(start)
            except ValueError:
                continue
            mins = (inicio - datetime.now(timezone.utc)).total_seconds() / 60
            if 0 <= mins <= LEAD_MINUTOS:
                titulo = e.get("summary", "(sin título)")
                hora = inicio.astimezone().strftime("%H:%M")
                avisos.append(
                    (f"cal:{e.get('id')}", f"Tienes «{titulo}» a las {hora} (en {int(mins)} min).")
                )
        return avisos

    def _avisos_correo(self) -> list[tuple[str, str]]:
        try:
            from cristopher.google_auth import get_gmail

            svc = get_gmail()
            msgs = (
                svc.users().messages()
                .list(userId="me", q=GMAIL_QUERY_PROACTIVO, maxResults=10)
                .execute()
                .get("messages", [])
            )
            ids = [m["id"] for m in msgs]
            if self._correo_base is None:
                # Primer arranque: memoriza lo ya existente, no avisa de lo viejo.
                self._correo_base = set(ids)
                return []
            avisos = []
            for mid in ids:
                if mid in self._correo_base:
                    continue
                full = (
                    svc.users().messages()
                    .get(userId="me", id=mid, format="metadata",
                         metadataHeaders=["From", "Subject"])
                    .execute()
                )
                h = {x["name"]: x["value"] for x in full.get("payload", {}).get("headers", [])}
                avisos.append(
                    (f"mail:{mid}",
                     f"Correo de {h.get('From','?')}: {h.get('Subject','(sin asunto)')}")
                )
            return avisos
        except Exception:
            return []

    def _avisos_recordatorios(self) -> list[tuple[str, str]]:
        now_iso = datetime.now().isoformat(timespec="seconds")
        avisos = []
        for rid, texto, _cuando in self._rec.pendientes(now_iso):
            avisos.append((f"rec:{rid}", f"Recordatorio: {texto}"))
        return avisos

    def _avisos_whatsapp(self) -> list[tuple[str, str]]:
        try:
            from cristopher import whatsapp_client

            data = whatsapp_client.check_new()
        except Exception:
            return []
        avisos = []
        if data.get("estado") in ("logged_out", "qr_required"):
            # Como mucho un aviso al día mientras siga desconectado (la clave se
            # "auto-limpia" sola en cuanto cambia la fecha, sin necesidad de "unseen").
            hoy = datetime.now().date().isoformat()
            avisos.append((
                f"wa:logged_out:{hoy}",
                "WhatsApp desconectado: hace falta volver a escanear el QR "
                "(ejecuta 'node whatsapp/setup_qr.js').",
            ))
        for c in data.get("chats", []):
            avisos.append((
                f"wa:{c['chat_id']}:{c['ultimo_id']}",
                f"Te han llegado {c['n_nuevos']} mensaje(s) de WhatsApp de "
                f"{c['nombre']}: «{c['ultimo_texto']}»",
            ))
        return avisos

    def _avisos_diario(self) -> list[tuple[str, str]]:
        try:
            from cristopher.diario import get_diario

            ahora = datetime.now()
            if ahora.hour < DIARIO_HORA_AVISO:
                return []
            hoy = ahora.date().isoformat()
            if get_diario().hay_entradas_hoy(hoy):
                return []
        except Exception:
            return []
        return [(
            f"diario:{hoy}",
            "Aún no has hecho tu diario de hoy — cuando quieras te escucho, sin prisa.",
        )]

    def _avisos_sesiones(self) -> list[tuple[str, str]]:
        """Sesiones de Claude Code (modo agentes, Fase 1-2) esperando/con una
        pregunta sin atender: alerta por el mismo canal que calendario/correo."""
        try:
            from cristopher.sesiones import get_gestor_sesiones

            filas = get_gestor_sesiones().listar()
        except Exception:
            return []
        avisos = []
        for f in filas:
            if f["estado"] not in ("esperando", "pregunta"):
                continue
            motivo = "tiene una pregunta pendiente" if f["estado"] == "pregunta" else "está esperando"
            avisos.append((
                f"sesion:{f['nombre']}:{f['ultima_actividad']}",
                f"La sesión '{f['nombre']}' {motivo} sin atender: {f['ruta']}",
            ))
        return avisos

    # --- Modo agentes: propone respuesta a una sesión con pregunta (Fase 3) ----
    def _revisar_preguntas_sesiones(self) -> None:
        try:
            from cristopher.sesiones import get_gestor_sesiones

            gestor = get_gestor_sesiones()
            filas = [f for f in gestor.listar() if f["estado"] == "pregunta"]
        except Exception:
            return
        for f in filas:
            clave = f"sesion_pregunta:{f['nombre']}:{f['ultima_actividad']}"
            if self._rec.ya_visto(clave):
                continue
            try:
                transcript = gestor.leer(f["nombre"], 15)
                narracion, propuesta = _redactar_respuesta_sesion(f, transcript)
                ultima_pregunta = next(
                    (e["texto"] for e in reversed(transcript) if e["rol"] == "asistente"), ""
                )
                from cristopher.tools.google_tools import confirmar_accion

                if narracion:
                    # El modelo narró bien: de qué sesión hablamos, el contexto y
                    # el porqué ya vienen en su propia voz.
                    cuerpo = (
                        f"{narracion}\n\n"
                        f"Esto es lo que le mandaría a '{f['nombre']}':\n\n« {propuesta} »"
                    )
                else:
                    # Degradado con elegancia: el modelo no separó narración de
                    # propuesta (o la redacción falló) — vuelve al formato mínimo.
                    cuerpo = (
                        f"CRISTOPHER propone responder a la sesión '{f['nombre']}':\n\n"
                        f"« {ultima_pregunta} »\n\nCon esto:\n\n« {propuesta} »"
                    )
                aprobado = confirmar_accion(
                    f"{cuerpo}\n\n¿Se la envío, o prefieres escribirle tú mismo desde el panel?"
                )
                if aprobado:
                    gestor.escribir(f["nombre"], propuesta)
            except Exception:
                pass
            finally:
                self._rec.marcar_visto(clave)

    # --- Cristopher Code (Fase 3): vigilancia EXCLUSIVA del modo code -----------
    def _anunciar_code(self, texto: str) -> None:
        """Publica un aviso no solicitado en el chat de código. `bus.log` ya
        etiqueta cada entrada con el modo activo en ese momento (Fase 1); como
        esto solo se llama con estado_code activo, llega a #code-log, nunca al
        chat normal. No pasa por _entregar/clasificar (eso es el panel de
        alertas de calendario/correo, un canal distinto) ni se añade al
        historial de _contents_code del modelo — es una notificación del
        demonio, no un turno que el LLM en vivo haya generado (mismo criterio
        que los avisos de calendario/correo de siempre)."""
        from cristopher import bus
        bus.log("answer", texto)

    def _vigilar_sesiones_code(self) -> None:
        """Vigilancia NUEVA y exclusiva de 'Cristopher Code' (modo code): a
        diferencia de _revisar_preguntas_sesiones (que sirve al overlay 🤖 del
        modo agentes original y sigue corriendo siempre, sin gate, para no
        romperlo), esto solo hace algo con estado_code activo. Detecta 3
        eventos que _avisos_sesiones/_revisar_preguntas_sesiones no cubren:
        sesión terminada, sesión colgada y conflicto de archivos nuevo."""
        from cristopher import estado_code

        if not estado_code.esta_activo():
            return
        try:
            from cristopher.sesiones import ESTADOS_ACTIVOS, get_gestor_sesiones

            gestor = get_gestor_sesiones()
            filas = gestor.listar()
        except Exception:
            return

        # 1) Sesiones que acaban de terminar (limpio o con error): informativo.
        for f in filas:
            if f["estado"] not in ("terminado", "error"):
                continue
            clave = f"code_fin:{f['nombre']}:{f['ultima_actividad']}"
            if self._rec.ya_visto(clave):
                continue
            if f["estado"] == "error":
                texto = (
                    f"Señor, la sesión '{f['nombre']}' terminó con un error — "
                    "conviene revisar qué pasó."
                )
            else:
                texto = (
                    f"Señor, la sesión '{f['nombre']}' ha terminado — lista para "
                    "revisar el diff y decidir si mergea."
                )
            self._anunciar_code(texto)
            self._rec.marcar_visto(clave)

        # 2) Sesiones coladas: 'trabajando' sin actividad más allá del umbral.
        ahora_ts = datetime.now().timestamp()
        for f in filas:
            if f["estado"] != "trabajando":
                continue
            try:
                ts = datetime.fromisoformat(f["ultima_actividad"]).timestamp()
            except ValueError:
                continue
            if ahora_ts - ts < SESION_COLGADA_SEGUNDOS:
                continue
            clave = f"code_colgada:{f['nombre']}:{f['ultima_actividad']}"
            if self._rec.ya_visto(clave):
                continue
            try:
                mins = int((ahora_ts - ts) / 60)
                from cristopher.tools.google_tools import confirmar_accion

                aprobado = confirmar_accion(
                    f"Señor, la sesión '{f['nombre']}' lleva {mins} min sin dar "
                    "señales de vida (parece colgada). ¿La paro?"
                )
                if aprobado:
                    gestor.parar(f["nombre"])
                    self._anunciar_code(f"Sesión '{f['nombre']}' parada por colgarse.")
            except Exception:
                pass
            finally:
                self._rec.marcar_visto(clave)

        # 3) Conflictos de archivos recién detectados (Fase 2): informativo.
        try:
            conflictos = gestor.conflictos()
        except Exception:
            conflictos = []
        for c in conflictos:
            clave = (
                "code_conflicto:"
                + "|".join(sorted([c["sesion_a"], c["sesion_b"]]))
                + ":" + ",".join(c["archivos"])
            )
            if self._rec.ya_visto(clave):
                continue
            texto = (
                f"Señor, ojo: '{c['sesion_a']}' y '{c['sesion_b']}' están tocando "
                f"los mismos archivos ({', '.join(c['archivos'])}) — conviene "
                "resolverlo antes de mergear."
            )
            self._anunciar_code(texto)
            self._rec.marcar_visto(clave)

        # 4) Uso de contexto alto (Fase 4a): activa, sin repetir aviso por el mismo
        # % (redondeado a 5 puntos, para no spamear en cada turno mientras se queda
        # estable arriba del umbral). /compact SÍ funciona de verdad por stdin en
        # modo headless (verificado en Paso 0), así que CRISTOPHER puede ejecutarlo
        # tras tu confirmación, no solo sugerirlo.
        for f in filas:
            if f["estado"] not in ESTADOS_ACTIVOS:
                continue
            pct = f.get("uso_contexto_pct")
            if pct is None or pct < SESION_CONTEXTO_AVISO_PCT:
                continue
            bucket = int(pct * 20)  # escalones de 5 puntos porcentuales
            clave = f"code_contexto:{f['nombre']}:{bucket}"
            if self._rec.ya_visto(clave):
                continue
            try:
                from cristopher.tools.google_tools import confirmar_accion

                aprobado = confirmar_accion(
                    f"Señor, la sesión '{f['nombre']}' va por el {pct * 100:.0f}% de "
                    "su ventana de contexto — a partir de aquí la calidad baja. "
                    "¿Le mando /compact?"
                )
                if aprobado:
                    gestor.escribir(f["nombre"], "/compact")
                    self._anunciar_code(f"Compactado el contexto de '{f['nombre']}'.")
            except Exception:
                pass
            finally:
                self._rec.marcar_visto(clave)

    # --- Entrega ---------------------------------------------------------------
    def _entregar(self, nivel: int, mensaje: str) -> None:
        from cristopher import concentracion
        if concentracion.esta_silenciando(nivel):
            concentracion.encolar_aviso(nivel, mensaje)
            return  # modo concentración activo: se encola, no se entrega ahora

        marca = datetime.now().strftime("%H:%M")
        if self._on_aviso is not None:
            try:
                self._on_aviso(nivel, mensaje)
            except Exception:
                pass
        if nivel >= 3:
            print(f"{RED}{BOLD}[{marca}] ⚠ URGENTE ›{RESET} {BOLD}{mensaje}{RESET}")
            hablar = self._hablar
            if hablar is None:
                from cristopher import voz
                hablar = voz.hablar
            try:
                hablar(mensaje)
                hablar(mensaje)  # nivel 3: más insistente (repite el aviso hablado)
            except Exception as exc:
                print(f"{AMBER}[VOZ] {exc}{RESET}")
            try:
                from cristopher.notificar_remoto import notificar
                notificar(mensaje)
            except Exception as exc:
                print(f"{AMBER}[REMOTO] {exc}{RESET}")
        elif nivel == 2:
            print(f"{CYAN}[{marca}] ›{RESET} {mensaje}")
        else:
            print(f"{DIM}[{marca}] · {mensaje}{RESET}")

    def revisar_una_vez(self) -> list[tuple[int, str]]:
        """Una pasada: reúne avisos nuevos, los clasifica y entrega. Devuelve
        [(nivel, mensaje)] de lo entregado (para inspección/tests)."""
        entregados = []
        avisos = (
            self._avisos_calendario()
            + self._avisos_correo()
            + self._avisos_recordatorios()
            + self._avisos_whatsapp()
            + self._avisos_diario()
            + self._avisos_sesiones()
        )
        for clave, mensaje in avisos:
            if self._rec.ya_visto(clave):
                continue
            nivel = self._clasificar(mensaje)
            self._entregar(nivel, mensaje)
            self._rec.marcar_visto(clave)
            if clave.startswith("rec:"):
                self._rec.marcar_hecho(int(clave.split(":")[1]))
            entregados.append((nivel, mensaje))
        return entregados

    def _revisar_concentracion(self) -> None:
        """Sondea si toca terminar/cambiar de fase el modo concentración (fin por
        tiempo o transición foco↔descanso). Reutiliza este mismo hilo de sondeo, sin
        hilo ni busy-loop nuevo (§ arquitectura: cambio mínimo y localizado)."""
        from cristopher import concentracion
        mensaje = concentracion.tick()
        if mensaje:
            self._entregar(2, mensaje)

    def run(self) -> None:
        print(f"{CYAN}{BOLD}CRISTOPHER — demonio proactivo{RESET} "
              f"{DIM}(vigilando cada {POLL_SEGUNDOS}s; Ctrl+C para salir){RESET}")
        while True:
            try:
                self.revisar_una_vez()
                self._revisar_concentracion()
                self._revisar_preguntas_sesiones()
                self._vigilar_sesiones_code()
            except Exception as exc:  # el demonio nunca debe morir por un fallo puntual
                print(f"{AMBER}[demonio] error en la revisión: {exc}{RESET}")
            time.sleep(POLL_SEGUNDOS)


def main() -> int:
    try:
        Demonio().run()
    except (KeyboardInterrupt, EOFError):
        print("\nDemonio detenido.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
