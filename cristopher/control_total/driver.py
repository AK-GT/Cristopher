"""Primitivas de bajo nivel del Modo Control Total — mover, clicar, teclear, scroll.

Todas auditables: cada llamada queda registrada (qué, dónde, cuándo, con qué
resultado) en un deque en memoria (`auditoria()`) y en CONTROL_TOTAL_LOG en disco,
mismo shape que `Sesion._log` en sesiones.py.

Contrato de seguridad (Fase 1, ver PLAN_CONTROL_TOTAL_1.md): CADA primitiva consulta
el kill-switch (`killswitch.disparado()`) ANTES de ejecutar, no solo al principio de
un bucle exterior — es el hombre muerto real. Si está disparado, la primitiva no hace
nada y lanza ControlDetenido.

CAPTCHA y credenciales NO se resuelven aquí (eso vive en guardarraíles.py, Fase 2):
`escribir`/`pulsar_tecla` son primitivas genéricas, el veto por contenido es de una
capa posterior.

Uso Windows-first vía pynput (ya dependencia del proyecto — sin librerías nuevas).
"""

from __future__ import annotations

from collections import deque
from datetime import datetime

from cristopher.config import CONTROL_TOTAL_LOG_MAXLEN
from cristopher.control_total import killswitch, log

_AUDIT: deque[dict] = deque(maxlen=CONTROL_TOTAL_LOG_MAXLEN)

_BOTONES = {}  # se rellena en _mouse_controller() una vez pynput esté disponible
_mouse = None
_teclado = None


class ControlDetenido(RuntimeError):
    """Una primitiva se llamó con el kill-switch disparado; no se ejecutó nada."""


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def auditoria() -> list[dict]:
    """Snapshot de las últimas CONTROL_TOTAL_LOG_MAXLEN acciones (no acoplarse al deque)."""
    return list(_AUDIT)


def _registrar(accion: str, detalle: dict, resultado: str) -> None:
    entrada = {"accion": accion, "detalle": detalle, "resultado": resultado, "ts": _now()}
    _AUDIT.append(entrada)
    log.info("%s %s -> %s", accion, detalle, resultado)


def _verificar(accion: str, detalle: dict) -> None:
    if killswitch.disparado():
        _registrar(accion, detalle, "bloqueado: kill-switch disparado")
        raise ControlDetenido(
            f"Kill-switch disparado: '{accion}' no se ejecutó."
        )


def _controllers():
    """Import perezoso de pynput: mismo criterio que el resto del proyecto (degradar
    con un ImportError claro en vez de exigir la librería solo para importar el
    módulo)."""
    global _mouse, _teclado, _BOTONES
    if _mouse is None or _teclado is None:
        try:
            from pynput import keyboard, mouse
        except ImportError as exc:
            raise RuntimeError("Falta pynput: pip install pynput") from exc
        _mouse = mouse.Controller()
        _teclado = keyboard.Controller()
        _BOTONES = {
            "izquierdo": mouse.Button.left,
            "derecho": mouse.Button.right,
            "centro": mouse.Button.middle,
        }
    return _mouse, _teclado


def mover_raton(x: int, y: int) -> None:
    """Posición absoluta del cursor (coordenadas de pantalla virtual completa)."""
    detalle = {"x": x, "y": y}
    _verificar("mover_raton", detalle)
    mouse_ctl, _ = _controllers()
    try:
        mouse_ctl.position = (x, y)
    except Exception as exc:
        _registrar("mover_raton", detalle, f"error: {exc}")
        raise
    _registrar("mover_raton", detalle, "ok")


def posicion_raton() -> tuple[int, int]:
    """Consulta, no acción: no pasa por el kill-switch ni queda en la auditoría."""
    mouse_ctl, _ = _controllers()
    return tuple(mouse_ctl.position)


def clic(boton: str = "izquierdo", doble: bool = False) -> None:
    detalle = {"boton": boton, "doble": doble}
    _verificar("clic", detalle)
    mouse_ctl, _ = _controllers()
    btn = _BOTONES.get(boton)
    if btn is None:
        raise ValueError(f"Botón no válido: {boton!r} (usa izquierdo/derecho/centro)")
    try:
        mouse_ctl.click(btn, 2 if doble else 1)
    except Exception as exc:
        _registrar("clic", detalle, f"error: {exc}")
        raise
    _registrar("clic", detalle, "ok")


def escribir(texto: str) -> None:
    detalle = {"longitud": len(texto)}  # nunca el texto literal en el log de auditoría
    _verificar("escribir", detalle)
    _, teclado_ctl = _controllers()
    try:
        teclado_ctl.type(texto)
    except Exception as exc:
        _registrar("escribir", detalle, f"error: {exc}")
        raise
    _registrar("escribir", detalle, "ok")


def pulsar_tecla(combo: str) -> None:
    """Combo en el mismo formato que los atajos globales del proyecto (p. ej.
    '<ctrl>+<alt>+t'), parseado con pynput.keyboard.HotKey.parse."""
    detalle = {"combo": combo}
    _verificar("pulsar_tecla", detalle)
    from pynput.keyboard import HotKey

    _, teclado_ctl = _controllers()
    try:
        teclas = HotKey.parse(combo)
        for tecla in teclas:
            teclado_ctl.press(tecla)
        for tecla in reversed(teclas):
            teclado_ctl.release(tecla)
    except Exception as exc:
        _registrar("pulsar_tecla", detalle, f"error: {exc}")
        raise
    _registrar("pulsar_tecla", detalle, "ok")


def scroll(cantidad: int) -> None:
    detalle = {"cantidad": cantidad}
    _verificar("scroll", detalle)
    mouse_ctl, _ = _controllers()
    try:
        mouse_ctl.scroll(0, cantidad)
    except Exception as exc:
        _registrar("scroll", detalle, f"error: {exc}")
        raise
    _registrar("scroll", detalle, "ok")


def _demo() -> None:
    """Criterio de aceptación manual de la Fase 1: mueve el ratón en bucle entre dos
    puntos hasta que el kill-switch lo detenga. Ejecutar:
      python -m cristopher.control_total.driver
    y pulsar el atajo de emergencia (CONTROL_TOTAL_HOTKEY) mientras corre."""
    import time

    from cristopher.config import CONTROL_TOTAL_HOTKEY

    killswitch.reset()
    killswitch.iniciar_killswitch()
    print(f"Moviendo el ratón en bucle. Pulsa {CONTROL_TOTAL_HOTKEY} para detenerlo (Ctrl+C también funciona).")
    x0, y0 = posicion_raton()
    puntos = [(x0, y0), (x0 + 200, y0 + 200)]
    i = 0
    try:
        while True:
            x, y = puntos[i % 2]
            mover_raton(x, y)
            i += 1
            time.sleep(0.3)
    except ControlDetenido as exc:
        print(f"\nDetenido por el kill-switch: {exc}")
    except KeyboardInterrupt:
        print("\nDetenido (Ctrl+C).")
    finally:
        killswitch.parar_killswitch()


if __name__ == "__main__":
    _demo()
