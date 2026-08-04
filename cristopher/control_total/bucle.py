"""Bucle percepción→decisión→acción del Modo Control Total (Fase 2).

Por vuelta: captura la pantalla (y la ventana activa, para el título), le pide a
Gemini UNA acción concreta del driver (`vision.decidir_accion`, function-calling
forzado — nunca texto libre), la pasa por `guardarraíles.evaluar` (veto EN CÓDIGO,
nunca saltable) y, si procede, la ejecuta vía `control_total.driver`. Repite hasta
terminar, agotar el límite de pasos/tiempo (Fase 1: `CONTROL_TOTAL_SESION_MAX_PASOS`/
`CONTROL_TOTAL_SESION_TIMEOUT`), o que salte el kill-switch.

Alcance de esta fase (decisión tomada con el usuario): igual que `sesiones.py`
construyó su núcleo de "modo agentes" desacoplado de HUD/TOOLS en su propia Fase 1
("se prueba solo desde el REPL"), este módulo NO se conecta a `agent.py` ni al
registro `TOOLS` todavía — eso es Fase 4. Se prueba con el demo de este mismo
archivo: `python -m cristopher.control_total.bucle "<objetivo>"`.

Un único bucle activo a la vez (singleton de módulo) — Control Total es un modo, no
varias sesiones paralelas con nombre como `sesiones.py`.
"""

from __future__ import annotations

import copy
import threading
import time
from collections import deque
from datetime import datetime
from typing import Any, Callable, Optional

from google.genai import types

from cristopher import vision
from cristopher.config import (
    CONTROL_TOTAL_LOG_MAXLEN,
    CONTROL_TOTAL_SESION_MAX_PASOS,
    CONTROL_TOTAL_SESION_TIMEOUT,
)
from cristopher.control_total import driver, guardarraíles, killswitch, log
from cristopher.tools.pantalla_tools import _png_de_region, _rect_ventana_activa

ESTADOS_FINALES = ("terminado", "error")

# Enum de riesgo que el modelo debe autoinformar en CADA acción — ver guardarraíles.py.
_RIESGOS = ["ninguno", "password", "pago", "borrado_permanente", "captcha"]


class BucleError(RuntimeError):
    """El Modo Control Total no pudo completar la operación pedida."""


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _to_gemini_type(node: Any) -> Any:
    """Convierte un JSON Schema estándar (types en minúscula) al formato que espera
    Gemini (enum Type en mayúscula), recursivamente. Duplicado de
    `agent.py::_to_gemini_type` a propósito: importar `agent.py` arrastraría todo
    `tools/__init__.py` (125 herramientas, WhatsApp, Google auth...) solo por esta
    función pura de 10 líneas — mismo criterio que `killswitch.py` ya usó al
    duplicar `_ruta_inicio`/`_pythonw` de `escucha.py`."""
    if isinstance(node, dict):
        out = {}
        for k, v in node.items():
            if k == "type" and isinstance(v, str):
                out[k] = v.upper()
            else:
                out[k] = _to_gemini_type(v)
        return out
    if isinstance(node, list):
        return [_to_gemini_type(x) for x in node]
    return node


# --- Declaración de las 5 primitivas del driver como tools de Gemini --------------
_ACCIONES: list[dict] = [
    {
        "name": "mover_raton",
        "description": "Mueve el cursor a una posición absoluta de la pantalla.",
        "parameters": {
            "type": "object",
            "properties": {
                "x": {"type": "integer", "description": "Coordenada X absoluta."},
                "y": {"type": "integer", "description": "Coordenada Y absoluta."},
                "riesgo": {"type": "string", "enum": _RIESGOS},
                "razon": {"type": "string", "description": "Por qué esta acción concreta avanza el objetivo AHORA, en una frase."},
            },
            "required": ["x", "y", "riesgo", "razon"],
        },
    },
    {
        "name": "clic",
        "description": "Hace clic con el ratón en la posición actual del cursor.",
        "parameters": {
            "type": "object",
            "properties": {
                "boton": {"type": "string", "enum": ["izquierdo", "derecho", "centro"]},
                "doble": {"type": "boolean", "description": "True para doble clic."},
                "riesgo": {"type": "string", "enum": _RIESGOS},
                "razon": {"type": "string", "description": "Por qué esta acción concreta avanza el objetivo AHORA, en una frase."},
            },
            "required": ["boton", "riesgo", "razon"],
        },
    },
    {
        "name": "escribir",
        "description": "Escribe texto en el campo con foco actual.",
        "parameters": {
            "type": "object",
            "properties": {
                "texto": {"type": "string"},
                "riesgo": {"type": "string", "enum": _RIESGOS},
                "razon": {"type": "string", "description": "Por qué esta acción concreta avanza el objetivo AHORA, en una frase."},
            },
            "required": ["texto", "riesgo", "razon"],
        },
    },
    {
        "name": "pulsar_tecla",
        "description": "Pulsa una combinación de teclas, formato '<ctrl>+<alt>+t'.",
        "parameters": {
            "type": "object",
            "properties": {
                "combo": {"type": "string"},
                "riesgo": {"type": "string", "enum": _RIESGOS},
                "razon": {"type": "string", "description": "Por qué esta acción concreta avanza el objetivo AHORA, en una frase."},
            },
            "required": ["combo", "riesgo", "razon"],
        },
    },
    {
        "name": "scroll",
        "description": "Desplaza la rueda del ratón (positivo arriba, negativo abajo).",
        "parameters": {
            "type": "object",
            "properties": {
                "cantidad": {"type": "integer"},
                "riesgo": {"type": "string", "enum": _RIESGOS},
                "razon": {"type": "string", "description": "Por qué esta acción concreta avanza el objetivo AHORA, en una frase."},
            },
            "required": ["cantidad", "riesgo", "razon"],
        },
    },
]


def _build_tool() -> types.Tool:
    declarations = []
    for a in _ACCIONES:
        params = _to_gemini_type(copy.deepcopy(a["parameters"]))
        declarations.append(
            types.FunctionDeclaration(
                name=a["name"], description=a["description"], parameters=params,
            )
        )
    return types.Tool(function_declarations=declarations)


_TOOL = _build_tool()


# --- Estado de una sesión de control ----------------------------------------------
class Bucle:
    """Estado en vivo de una sesión del Modo Control Total (mirror de `Sesion` en
    sesiones.py, simplificado: aquí solo hay UNA a la vez)."""

    def __init__(self, objetivo: str):
        self.objetivo = objetivo
        self.estado = "arrancando"
        self.transcript: "deque[dict]" = deque(maxlen=CONTROL_TOTAL_LOG_MAXLEN)
        self.pasos = 0
        self.inicio = time.time()
        self.lock = threading.Lock()


OnEvento = Callable[[dict], None]

_BUCLE: Optional[Bucle] = None
_BUCLE_LOCK = threading.Lock()
_on_evento: Optional[OnEvento] = None


def set_on_evento(callback: Optional[OnEvento]) -> None:
    """Fase 3 (widget) registra aquí un callback para republicar eventos en vivo."""
    global _on_evento
    _on_evento = callback


def _emitir(bucle: Bucle, rol: str, texto: str) -> None:
    with bucle.lock:
        bucle.transcript.append({"rol": rol, "texto": texto, "ts": _now()})
    log.info("[%s] %s: %s", bucle.objetivo[:40], rol, texto)
    if _on_evento is not None:
        try:
            _on_evento({"estado": bucle.estado, "entrada": {"rol": rol, "texto": texto}})
        except Exception:
            pass  # un suscriptor roto nunca debe tumbar el bucle


def iniciar(objetivo: str) -> str:
    """Arranca una sesión del Modo Control Total. NO bloquea: lanza el hilo de
    fondo y devuelve de inmediato (mismo patrón no bloqueante que
    `sesiones.py::GestorSesiones.crear`)."""
    global _BUCLE
    objetivo = (objetivo or "").strip()
    if not objetivo:
        raise BucleError("Falta el objetivo del Modo Control Total.")
    with _BUCLE_LOCK:
        if _BUCLE is not None and _BUCLE.estado not in ESTADOS_FINALES:
            raise BucleError(
                f"Ya hay un bucle de control activo (estado: {_BUCLE.estado}). "
                "Párelo primero con parar()."
            )
        killswitch.reset()
        bucle = Bucle(objetivo)
        _BUCLE = bucle
    threading.Thread(target=_ciclo, args=(bucle,), daemon=True).start()
    return f"Modo Control Total iniciado: {objetivo}"


def estado_actual() -> dict:
    """Lectura pura, nunca bloquea (mismo criterio que `GestorSesiones.listar`)."""
    bucle = _BUCLE
    if bucle is None:
        return {"estado": "inactivo"}
    with bucle.lock:
        return {
            "objetivo": bucle.objetivo,
            "estado": bucle.estado,
            "pasos": bucle.pasos,
            "transcurrido": round(time.time() - bucle.inicio, 1),
        }


def leer(n: int = 20) -> list[dict]:
    bucle = _BUCLE
    if bucle is None:
        return []
    with bucle.lock:
        return list(bucle.transcript)[-n:]


def parar() -> str:
    bucle = _BUCLE
    if bucle is None or bucle.estado in ESTADOS_FINALES:
        return "No hay ningún bucle de control activo."
    with bucle.lock:
        bucle.estado = "terminado"
    return "Parando el Modo Control Total…"


# --- El propio bucle ---------------------------------------------------------------
def _prompt_decision(bucle: Bucle, titulo_ventana: str) -> str:
    ultimas = list(bucle.transcript)[-8:]
    historial = "\n".join(f"- {e['rol']}: {e['texto']}" for e in ultimas) or "(sin acciones previas)"
    return (
        f"Estás operando el ratón y el teclado de un ordenador Windows para lograr "
        f"este objetivo: {bucle.objetivo!r}.\n\n"
        f"Ventana activa: {titulo_ventana or '(desconocida)'}.\n\n"
        f"Últimas acciones (con tu propio razonamiento de entonces):\n{historial}\n\n"
        "Mira la captura de pantalla adjunta y elige EXACTAMENTE UNA acción concreta "
        "para avanzar hacia el objetivo (una primitiva de bajo nivel, no un plan "
        "largo). Sigue esta disciplina paso a paso, es MUY importante:\n"
        "- Si tu ÚLTIMA acción abrió un cuadro de diálogo, buscador o barra de "
        "direcciones (p. ej. Win+R, el menú Inicio, Ctrl+L de un navegador), tu "
        "SIGUIENTE acción casi siempre debe ser ESCRIBIR ahí el texto que quieres "
        "buscar/ejecutar — NUNCA pulses Enter ni des el paso por hecho antes de "
        "haber escrito ese texto y verlo en la captura.\n"
        "- No pulses Enter sobre un campo que en la captura sigue vacío o sin el "
        "texto correcto.\n"
        "- Si tus últimas 2-3 acciones son iguales entre sí (mismas coordenadas, "
        "mismo texto) y la ventana activa no ha cambiado, te has quedado atascado: "
        "prueba algo DISTINTO en vez de repetir la misma acción.\n"
        "- Antes de dar el objetivo por cumplido, comprueba en la captura que de "
        "verdad se ve conseguido (p. ej. el texto ya escrito en la ventana correcta).\n"
        "- NUNCA dejes una acción a medias esperando que el usuario la termine a "
        "mano (p. ej. escribir el nombre de una app y parar ahí sin pulsar Enter o "
        "sin hacer clic en el resultado) — complétala tú mismo, ese es el sentido "
        "de este modo. Solo debes parar y esperar cuando el propio sistema te lo "
        "exija de verdad (una confirmación de pago/borrado/contraseña/CAPTCHA, que "
        "ya gestiona una capa aparte).\n"
        "- Para ABRIR UNA APLICACIÓN, usa el buscador de Windows: pulsa SOLO "
        "'<cmd>' (nunca '<cmd>+r', el diálogo Ejecutar necesita el nombre EXACTO "
        "del ejecutable en inglés y no encuentra nombres como 'bloc de notas'), "
        "escribe el nombre de la app tal como la conoces (funciona en español: "
        "'bloc de notas', 'calculadora'...) y pulsa '<enter>' para abrir el primer "
        "resultado — de principio a fin, sin parar a mitad.\n\n"
        "Rellena SIEMPRE 'razon' (una frase: qué esperas que cambie en pantalla con "
        "ESTA acción concreta) y 'riesgo': 'password' si vas a escribir o hacer clic "
        "sobre un campo de contraseña/inicio de sesión, 'pago' si hay un formulario "
        "de pago/compra, 'borrado_permanente' si vas a confirmar un borrado "
        "irreversible, 'captcha' si hay un CAPTCHA en pantalla, o 'ninguno' si no "
        "aplica ninguno de esos casos.\n\n"
        "Para 'pulsar_tecla', usa el formato '<tecla1>+<tecla2>+letra' con los "
        "nombres EXACTOS de pynput — la tecla Windows se llama '<cmd>', NUNCA "
        "'<win>' (ejemplo correcto: '<ctrl>+c' para copiar). Modificadores "
        "válidos: <ctrl>, <alt>, <shift>, <cmd>, <tab>, <esc>, <enter>, <f1>..<f24>."
    )


def _vuelta(bucle: Bucle) -> None:
    info = _rect_ventana_activa()
    titulo = info[1] if info else ""
    region = info[0] if info else None
    png = _png_de_region(region)

    prompt = _prompt_decision(bucle, titulo)
    try:
        fc = vision.decidir_accion(png, prompt, _TOOL)
    except Exception as exc:
        # Un hipo de red/API en ESTA vuelta (p. ej. "Server disconnected") no debe
        # tirar una sesión de varios minutos — se registra como recuperable y la
        # siguiente vuelta vuelve a capturar y reintentar, mismo criterio que ya
        # se aplica a un fallo de primitiva más abajo.
        _emitir(bucle, "error_vision", f"Fallo al decidir la acción: {exc}")
        return
    if fc is None:
        _emitir(bucle, "sistema", "El modelo no propuso ninguna acción esta vuelta.")
        return

    args = dict(fc.args or {})
    riesgo_declarado = str(args.pop("riesgo", "ninguno"))
    razon = str(args.pop("razon", "")).strip()
    _emitir(
        bucle, "accion_propuesta",
        f"{fc.name}({args}) riesgo={riesgo_declarado}" + (f" — razón: {razon}" if razon else ""),
    )

    veredicto, accion_final, args_finales = guardarraíles.evaluar(
        fc.name, args, riesgo_declarado, titulo,
    )
    with bucle.lock:
        bucle.pasos += 1
    if veredicto == "omitir":
        _emitir(bucle, "guardarraíl", "Acción omitida esta vuelta.")
        return

    primitiva = getattr(driver, accion_final, None)
    if primitiva is None:
        _emitir(bucle, "error_accion", f"Primitiva desconocida: {accion_final}")
        return
    try:
        primitiva(**args_finales)
    except driver.ControlDetenido:
        raise  # kill-switch real: SÍ debe terminar el bucle, no es recuperable
    except Exception as exc:
        # Un fallo de UNA primitiva (argumento inválido, combo de teclas mal
        # formado...) es una observación para la siguiente vuelta, no un motivo
        # para tirar toda la sesión — mismo criterio que un tool_call fallido en
        # el bucle ReAct normal (agent.py): se informa y se sigue.
        _emitir(bucle, "error_accion", f"{accion_final}({args_finales}) falló: {exc}")
        return
    _emitir(bucle, "accion_ejecutada", f"{accion_final}({args_finales})")


def _ciclo(bucle: Bucle) -> None:
    with bucle.lock:
        bucle.estado = "trabajando"
    _emitir(bucle, "sistema", f"Objetivo: {bucle.objetivo}")
    while True:
        with bucle.lock:
            if bucle.estado in ESTADOS_FINALES:
                return
            if bucle.pasos >= CONTROL_TOTAL_SESION_MAX_PASOS:
                bucle.estado = "terminado"
                fin = "Tope de pasos alcanzado."
            elif time.time() - bucle.inicio >= CONTROL_TOTAL_SESION_TIMEOUT:
                bucle.estado = "terminado"
                fin = "Tiempo máximo de sesión agotado."
            else:
                fin = None
        if fin:
            _emitir(bucle, "sistema", fin)
            return
        if killswitch.disparado():
            with bucle.lock:
                bucle.estado = "terminado"
            _emitir(bucle, "sistema", "Kill-switch disparado; bucle detenido.")
            return

        try:
            _vuelta(bucle)
        except driver.ControlDetenido as exc:
            with bucle.lock:
                bucle.estado = "terminado"
            _emitir(bucle, "sistema", f"Detenido por el kill-switch: {exc}")
            return
        except Exception as exc:
            with bucle.lock:
                bucle.estado = "error"
            _emitir(bucle, "sistema", f"Error inesperado: {exc}")
            return

        time.sleep(1.0)


# --- Demo manual (criterio de aceptación de esta fase, sin tocar agent.py) --------
def _demo() -> None:
    import sys

    objetivo = " ".join(sys.argv[1:]).strip()
    if not objetivo:
        print('Uso: python -m cristopher.control_total.bucle "<objetivo>"')
        return
    print(iniciar(objetivo))
    visto = 0
    try:
        while True:
            time.sleep(1.0)
            entradas = leer(600)  # por encima del maxlen: en la práctica, todas
            for e in entradas[visto:]:
                print(f"[{e['ts']}] {e['rol']}: {e['texto']}")
            visto = len(entradas)
            st = estado_actual()
            if st.get("estado") in ESTADOS_FINALES:
                print(f"--- Terminado (estado={st.get('estado')}) ---")
                break
    except KeyboardInterrupt:
        print("\nParando…")
        parar()


if __name__ == "__main__":
    _demo()
