"""Herramientas del Modo Control Total (Fase 4).

Permiten a CRISTOPHER activar/desactivar el modo por INTENCIÓN del usuario (sin
keywords fijas) — mismo patrón acoplado tools→bus que `code_tools.py` (activar
publica al bus para que el HUD grande se tiña de azul y arranca el widget/borde;
desactivar solo baja el flag y publica: widget y borde se auto-cierran solos al
verlo por SSE/HTTP, nadie los mata por PID).

Es la herramienta MÁS DELICADA del registro (control real de ratón/teclado): las
descripciones de abajo piden más cautela que `activar_modo_code` a propósito.
"""

from __future__ import annotations

from cristopher import bus, estado_control_total


def activar_control_total(objetivo: str) -> str:
    """Activa el Modo Control Total: a partir de ahora CRISTOPHER toma el ratón y
    el teclado del PC de forma autónoma y continua para conseguir el objetivo que
    le des, con guardarraíles de seguridad (para y pide confirmación ante pagos,
    borrados irreversibles, contraseñas y CAPTCHAs) y un atajo físico de emergencia
    (Ctrl+F3) siempre activo. Es la herramienta MÁS DELICADA de todas: actívala
    SOLO cuando el usuario lo pida de forma clara e inequívoca ('toma el control',
    'modo control total', 'hazlo tú con el ratón y el teclado'). Si hay la mínima
    duda sobre si es esto lo que quiere, o sobre cuál es el objetivo exacto,
    PREGÚNTALE antes — nunca la actives por iniciativa propia ni interpretando de
    más.

    Args:
        objetivo: qué debe conseguir operando el PC, en una frase concreta y accionable.
    """
    objetivo = (objetivo or "").strip()
    if not objetivo:
        return "Necesito un objetivo concreto antes de tomar el control — ¿qué quieres que haga?"

    from cristopher.control_total import bucle

    try:
        msg = bucle.iniciar(objetivo)
    except bucle.BucleError as exc:
        return f"No pude activar el Modo Control Total: {exc}"

    estado_control_total.activar()
    bus.set_control_total(True)

    from cristopher.hud import control_overlay, widget_flotante

    widget_flotante.lanzar_proceso()
    control_overlay.lanzar_proceso()
    return f"{msg} Debería verse el widget y el borde azul en un momento."


def desactivar_control_total() -> str:
    """Desactiva el Modo Control Total: para el bucle en marcha (si lo hay) y
    cierra el widget/borde azul. Úsalo cuando el usuario dé la tarea por
    terminada o pida parar ('ya está', 'para', 'suéltalo', 'sal del modo control
    total')."""
    from cristopher.control_total import bucle

    bucle.parar()
    estado_control_total.desactivar()
    bus.set_control_total(False)
    return "Modo Control Total desactivado."
