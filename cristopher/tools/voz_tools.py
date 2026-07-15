"""Herramientas de modo voz (Fase 6).

Permiten a CRISTOPHER entrar/salir del "modo audio" por INTENCIÓN del usuario (sin
keywords fijas). Si no está claro que el usuario quiera voz, el modelo debe preguntar
antes en vez de activarlo.
"""

from __future__ import annotations

from cristopher import estado, voz


def activar_modo_voz() -> str:
    """Activa el modo audio: a partir de ahora las respuestas se dicen en voz alta y no
    se muestra el pensamiento, solo la respuesta. Úsalo cuando el usuario pida claramente
    hablar por voz/audio. Si no está claro, pregúntale antes en lugar de activarlo."""
    estado.activar()
    return "Modo audio activado: hablaré en voz alta."


def desactivar_modo_voz() -> str:
    """Desactiva el modo audio y vuelve a solo texto. Úsalo cuando el usuario pida dejar
    de hablar en voz alta o volver al modo texto."""
    estado.desactivar()
    return "Modo audio desactivado: vuelvo a solo texto."


def _formatear_voz(v: dict[str, str], activa: str) -> str:
    marca = " (activa)" if v["nombre"] == activa else ""
    return f"- {v['nombre']} — {v['region']}, calidad {v['calidad']}{marca}"


def voz_listar_voces() -> str:
    """Lista las voces Piper YA instaladas (listas para usar sin descargar nada).
    Úsala cuando el usuario pregunte qué voces tienes o pida cambiar de voz sin dar
    un nombre concreto."""
    instaladas = voz.voces_instaladas()
    if not instaladas:
        return "No tengo ninguna voz instalada todavía."
    activa = voz.voz_actual_nombre()
    lineas = "\n".join(_formatear_voz(v, activa) for v in instaladas)
    return "Voces instaladas:\n" + lineas


def voz_catalogo() -> str:
    """Lista TODAS las voces del catálogo conocido (instaladas o no), marcando cuáles
    ya están instaladas. Úsala cuando el usuario pregunte qué otras voces existen o
    podría descargar, más allá de las que ya tiene."""
    activa = voz.voz_actual_nombre()
    instaladas_nombres = {v["nombre"] for v in voz.voces_instaladas()}
    lineas = []
    for v_ in voz.VOCES_CATALOGO:
        if v_["nombre"] == activa:
            marca = " (activa)"
        elif v_["nombre"] in instaladas_nombres:
            marca = " (instalada)"
        else:
            marca = ""
        lineas.append(f"- {v_['nombre']} — {v_['region']}, calidad {v_['calidad']}{marca}")
    return "Catálogo de voces:\n" + "\n".join(lineas)


def voz_elegir(nombre: str) -> str:
    """Cambia la voz activa a `nombre` (debe ser un nombre exacto del catálogo, ver
    voz_catalogo/voz_listar_voces). Si esa voz no está instalada, la descarga primero
    (puede tardar unos segundos por la red). Úsala cuando el usuario pida claramente
    cambiar de voz, dando el nombre o una descripción que identifique una del
    catálogo (p. ej. "pon la voz de Argentina")."""
    nombre = (nombre or "").strip()
    if nombre not in voz._nombres_catalogo():
        return (
            f"'{nombre}' no es una voz que conozca. Usa voz_catalogo para ver los "
            "nombres válidos."
        )
    ya_instalada = voz._ruta_voz(nombre).exists()
    if not ya_instalada:
        try:
            voz.descargar_voz(nombre)
        except voz.VozError as exc:
            return f"No pude cambiar a '{nombre}': {exc}"
    voz._guardar_voz_actual(nombre)
    aviso_descarga = " (la descargué primero)" if not ya_instalada else ""
    return f"Voz activa cambiada a '{nombre}'{aviso_descarga}."


def voz_actual() -> str:
    """Dice cuál es la voz activa ahora mismo. Úsala cuando el usuario pregunte qué
    voz tienes puesta."""
    nombre = voz.voz_actual_nombre()
    info = next((v for v in voz.VOCES_CATALOGO if v["nombre"] == nombre), None)
    if info is None:
        return f"Voz activa: {nombre}"
    return f"Voz activa: {info['nombre']} — {info['region']}, calidad {info['calidad']}"
