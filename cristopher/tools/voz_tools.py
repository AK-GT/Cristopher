"""Herramientas de modo voz (Fase 6).

Permiten a CRISTOPHER entrar/salir del "modo audio" por INTENCIÓN del usuario (sin
keywords fijas). Si no está claro que el usuario quiera voz, el modelo debe preguntar
antes en vez de activarlo.

Además de mover el flag de estado.py, publican el cambio al bus para que el toggle 🔊
del HUD lo refleje (mismo acoplamiento tools→bus deliberado que tools/code_tools.py).
"""

from __future__ import annotations

from cristopher import bus, estado, voz


def activar_modo_voz() -> str:
    """Activa el modo audio: a partir de ahora las respuestas se dicen en voz alta y no
    se muestra el pensamiento, solo la respuesta. Úsalo cuando el usuario pida claramente
    hablar por voz/audio. Si no está claro, pregúntale antes en lugar de activarlo."""
    estado.activar()
    bus.set_modo_voz(True)
    return "Modo audio activado: hablaré en voz alta."


def desactivar_modo_voz() -> str:
    """Desactiva el modo audio y vuelve a solo texto. Úsalo cuando el usuario pida dejar
    de hablar en voz alta o volver al modo texto."""
    estado.desactivar()
    bus.set_modo_voz(False)
    return "Modo audio desactivado: vuelvo a solo texto."


def _formatear_voz(v: dict[str, str], activa: str) -> str:
    marca = " (activa)" if v["nombre"] == activa else ""
    return f"- {v['nombre']} — {v['region']}, calidad {v['calidad']}{marca}"


def voz_listar_voces() -> str:
    """Lista las voces Piper (local) YA instaladas (listas para usar sin descargar
    nada). Úsala cuando el usuario pregunte qué voces locales tienes o pida cambiar
    de voz Piper sin dar un nombre concreto. Para voces de ElevenLabs (en la nube),
    usa voz_listar_elevenlabs/voz_buscar_elevenlabs en su lugar."""
    instaladas = voz.voces_instaladas()
    if not instaladas:
        return "No tengo ninguna voz instalada todavía."
    activa = voz.voz_actual_nombre() if voz.proveedor_actual() == "piper" else ""
    lineas = "\n".join(_formatear_voz(v, activa) for v in instaladas)
    return "Voces Piper (local) instaladas:\n" + lineas


def voz_catalogo() -> str:
    """Lista TODAS las voces del catálogo Piper (local) conocido (instaladas o no),
    marcando cuáles ya están instaladas. Úsala cuando el usuario pregunte qué otras
    voces Piper existen o podría descargar, más allá de las que ya tiene. Para
    ElevenLabs usa voz_buscar_elevenlabs."""
    activa = voz.voz_actual_nombre() if voz.proveedor_actual() == "piper" else ""
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
    return "Catálogo de voces Piper (local):\n" + "\n".join(lineas)


def voz_elegir(nombre: str) -> str:
    """Cambia la voz activa a `nombre`, una voz PIPER (local, gratis) exacta del
    catálogo (ver voz_catalogo/voz_listar_voces). Si esa voz no está instalada, la
    descarga primero (puede tardar unos segundos por la red). Úsala cuando el usuario
    pida claramente cambiar a una voz Piper/local, dando el nombre o una descripción
    que identifique una del catálogo (p. ej. "pon la voz de Argentina"). Para una voz
    de ElevenLabs usa voz_elegir_elevenlabs en su lugar."""
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
    return f"Voz activa cambiada a '{nombre}' (Piper, local){aviso_descarga}."


def voz_usar_piper() -> str:
    """Vuelve a hablar con Piper (voz local, gratis) sin cambiar cuál era la última
    voz Piper elegida. Úsala cuando el usuario quiera volver a la voz local, por
    ejemplo tras un fallo de ElevenLabs (créditos agotados, sin red...)."""
    voz._escribir_voz_json({"proveedor": "piper"})
    return f"Vuelvo a hablar con Piper: '{voz.voz_actual_nombre()}' (local)."


def voz_listar_elevenlabs() -> str:
    """Lista las voces de ElevenLabs YA disponibles en la cuenta (listas para usar
    sin añadir nada). Requiere ELEVENLABS_API_KEY configurada. Úsala cuando el
    usuario pregunte qué voces de ElevenLabs tiene o pida cambiar sin dar un nombre
    concreto. Para explorar voces nuevas que aún no tiene, usa voz_buscar_elevenlabs."""
    try:
        voces = voz.elevenlabs_listar_voces()
    except voz.VozError as exc:
        return str(exc)
    if not voces:
        return "Tu cuenta de ElevenLabs no tiene ninguna voz todavía. Prueba voz_buscar_elevenlabs."
    activo_id, _ = voz._leer_voz_elevenlabs_activa()
    activo_id = activo_id if voz.proveedor_actual() == "elevenlabs" else None
    lineas = []
    for v_ in voces:
        marca = " (activa)" if v_["voice_id"] == activo_id else ""
        lineas.append(f"- {v_['nombre']} ({v_['categoria']}){marca}")
    return "Voces de ElevenLabs en tu cuenta:\n" + "\n".join(lineas)


def voz_buscar_elevenlabs(query: str) -> str:
    """Busca voces en la Voice Library PÚBLICA de ElevenLabs (no solo las que ya
    tienes en tu cuenta). IMPORTANTE: `query` debe ir en INGLÉS y corto (acento,
    género, tono o uso — p. ej. "male mexican narrator", "female young energetic") o
    un nombre propio: el índice de ElevenLabs solo etiqueta sus voces en inglés, una
    frase en español no encuentra nada. Si el usuario pide en español (p. ej. "una voz
    de hombre mexicano"), tradúcelo tú a esas palabras clave antes de llamar a esta
    tool. Requiere ELEVENLABS_API_KEY configurada. Las voces encontradas se pueden
    activar directamente con voz_elegir_elevenlabs — si aún no están en tu cuenta, se
    añaden automáticamente. Úsala cuando el usuario quiera explorar/probar voces de
    ElevenLabs nuevas, más allá de las que ya tiene."""
    try:
        resultados = voz.elevenlabs_buscar_voces(query)
    except voz.VozError as exc:
        return str(exc)
    if not resultados:
        return f"No encontré voces de ElevenLabs para '{query}'."
    lineas = [
        f"- {v_['nombre']} ({v_['idioma']}) — {v_['descripcion']}".rstrip(" —")
        for v_ in resultados
    ]
    return "Voces encontradas en la Voice Library de ElevenLabs:\n" + "\n".join(lineas)


def voz_elegir_elevenlabs(nombre_o_id: str) -> str:
    """Cambia la voz activa a una de ElevenLabs, identificada por nombre o voice_id
    (de tu cuenta o de una búsqueda reciente con voz_buscar_elevenlabs). Si la voz no
    está en tu cuenta todavía, la añade primero (puede tardar unos segundos). Úsala
    cuando el usuario pida claramente una voz de ElevenLabs. Para volver a Piper usa
    voz_usar_piper o voz_elegir."""
    try:
        voice_id, nombre = voz._eleven_resolver(nombre_o_id)
    except voz.VozError as exc:
        return str(exc)
    voz._escribir_voz_json({
        "proveedor": "elevenlabs",
        "voz_elevenlabs_id": voice_id,
        "voz_elevenlabs_nombre": nombre,
    })
    return f"Voz activa cambiada a '{nombre}' (ElevenLabs)."


def voz_actual() -> str:
    """Dice cuál es la voz activa ahora mismo (Piper o ElevenLabs). Úsala cuando el
    usuario pregunte qué voz tienes puesta."""
    if voz.proveedor_actual() == "elevenlabs":
        _, nombre = voz._leer_voz_elevenlabs_activa()
        if not nombre:
            return "Modo ElevenLabs activo pero sin voz elegida todavía."
        return f"Voz activa: {nombre} (ElevenLabs)"
    nombre = voz.voz_actual_nombre()
    info = next((v for v in voz.VOCES_CATALOGO if v["nombre"] == nombre), None)
    if info is None:
        return f"Voz activa: {nombre} (Piper, local)"
    return f"Voz activa: {info['nombre']} (Piper, local) — {info['region']}, calidad {info['calidad']}"
