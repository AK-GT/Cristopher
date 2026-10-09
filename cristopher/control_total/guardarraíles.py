"""Guardarraíles del Modo Control Total — veto EN CÓDIGO antes de ejecutar cualquier
primitiva del driver, no una instrucción más de prompt (ver CLAUDE.md).
`bucle.py` llama a `evaluar()` antes de CADA primitiva, sin
excepción — el modelo no puede "decidir" saltárselo.

El riesgo final es el más severo entre lo que el modelo autoinforma (parámetro
`riesgo`, obligatorio en cada FunctionCall que declara `bucle.py`) y una heurística de
palabras clave sobre el TÍTULO de la ventana activa: que el modelo diga "ninguno" no
basta para saltarse la heurística (defensa en profundidad).

Comportamientos:
- pago / borrado_permanente: para y pide confirmación explícita
  (`google_tools.confirmar_accion`); confirmada, se ejecuta la acción propuesta tal
  cual. Sin confirmación (denegada o timeout — silencio = no), se omite.
- password: NUNCA ejecuta `escribir`/`pulsar_tecla` propuesto por el modelo. Sin
  gestor de contraseñas configurado (`CONTROL_TOTAL_GESTOR_CONTRASENAS_HOTKEY` vacío),
  pide que se rellene a mano y confirma para seguir. Con gestor configurado, confirma
  antes de disparar su atajo de autocompletado.
- captcha: NUNCA lo resuelve por su cuenta — pide que se resuelva a mano y confirma
  para seguir.
"""

from __future__ import annotations

from cristopher.control_total import log

# De menor a mayor severidad: el índice más alto gana al combinar autoinforme +
# heurística. password va al final porque ninguna otra categoría debe poder
# "tapar" una contraseña detectada.
PRIORIDAD = ["ninguno", "pago", "borrado_permanente", "captcha", "password"]

_PALABRAS: dict[str, list[str]] = {
    "password": [
        "contraseña", "contrasena", "password", "iniciar sesión", "iniciar sesion",
        "log in", "sign in", "verificación en dos pasos", "two-factor",
    ],
    "pago": [
        "pago", "checkout", "payment", "tarjeta de crédito", "tarjeta de credito",
        "credit card", "carrito", "cart", "compra", "purchase", "paypal", "stripe",
    ],
    "borrado_permanente": [
        "eliminar permanentemente", "borrar definitivamente", "delete permanently",
        "delete forever", "vaciar papelera", "empty trash", "no se puede deshacer",
        "cannot be undone",
    ],
    "captcha": ["captcha", "recaptcha", "no soy un robot", "not a robot"],
}


def _detectar_por_titulo(titulo: str) -> str:
    """Heurística barata y siempre-activa: ¿el título de la ventana activa sugiere
    alguna categoría de riesgo? Devuelve la categoría o "ninguno"."""
    bajo = (titulo or "").lower()
    for categoria in ("password", "captcha", "borrado_permanente", "pago"):
        if any(palabra in bajo for palabra in _PALABRAS[categoria]):
            return categoria
    return "ninguno"


def _mas_severo(a: str, b: str) -> str:
    ia = PRIORIDAD.index(a) if a in PRIORIDAD else 0
    ib = PRIORIDAD.index(b) if b in PRIORIDAD else 0
    return a if ia >= ib else b


def evaluar(
    accion: str, args: dict, riesgo_declarado: str, titulo_ventana: str,
) -> "tuple[str, str, dict]":
    """Decide qué hacer con la acción que el modelo propuso.

    Devuelve (veredicto, accion_final, args_finales):
      - ("ejecutar", accion, args): sin riesgo, ejecuta lo propuesto tal cual.
      - ("ejecutar_alternativa", "pulsar_tecla", {"combo": ...}): solo cuando hay
        gestor de contraseñas configurado y confirmado — la acción a ejecutar NO es
        la que propuso el modelo.
      - ("omitir", "", {}): no se ejecuta nada esta vuelta (denegado, timeout, o
        riesgo que exige resolución manual); el bucle re-captura y sigue.
    """
    from cristopher.tools.google_tools import confirmar_accion

    heuristica = _detectar_por_titulo(titulo_ventana)
    riesgo = _mas_severo(
        riesgo_declarado if riesgo_declarado in PRIORIDAD else "ninguno", heuristica
    )

    if riesgo == "ninguno":
        return "ejecutar", accion, args

    if riesgo in ("pago", "borrado_permanente"):
        prompt = (
            f"CRISTOPHER (Modo Control Total) quiere ejecutar {accion}({args}) — "
            f"parece un caso de {riesgo.replace('_', ' ')}. ¿Confirmas?"
        )
        aprobado = confirmar_accion(prompt, permitir_auto=False)
        log.info(
            "guardarraíl %s -> %s (%s%s)",
            riesgo, "ejecutar" if aprobado else "omitir", accion, args,
        )
        return ("ejecutar", accion, args) if aprobado else ("omitir", "", {})

    if riesgo == "password":
        from cristopher.config import CONTROL_TOTAL_GESTOR_CONTRASENAS_HOTKEY

        if CONTROL_TOTAL_GESTOR_CONTRASENAS_HOTKEY:
            aprobado = confirmar_accion(
                "Veo un campo de contraseña. ¿Disparo el atajo de tu gestor de "
                "contraseñas para rellenarla?",
                permitir_auto=False,
            )
            log.info(
                "guardarraíl password -> %s (atajo del gestor)",
                "ejecutar_alternativa" if aprobado else "omitir",
            )
            if aprobado:
                return (
                    "ejecutar_alternativa", "pulsar_tecla",
                    {"combo": CONTROL_TOTAL_GESTOR_CONTRASENAS_HOTKEY},
                )
            return "omitir", "", {}
        confirmar_accion(
            "Veo un campo de contraseña. Rellénalo tú a mano y confirma para seguir.",
            permitir_auto=False,
        )
        log.info("guardarraíl password -> omitir (sin gestor configurado, rellenar a mano)")
        return "omitir", "", {}

    # captcha
    confirmar_accion(
        "Veo lo que parece un CAPTCHA. Resuélvelo tú a mano y confirma para seguir.",
        permitir_auto=False,
    )
    log.info("guardarraíl captcha -> omitir (resolver a mano)")
    return "omitir", "", {}
