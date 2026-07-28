"""Tool de "auto-arreglos": CRISTOPHER lanza una sesión de Claude Code (tipo 'code'
sobre su propio proyecto, o tipo 'chat' para solo consultar) cuando detectó un límite
real y el usuario decidió investigarlo. Reutiliza `GestorSesiones.crear` tal cual
(etiquetada con origen='auto_arreglo') y el mismo gate de confirmación con texto
libre que ya usa el patrón "cofounder" de `proactivo.py` — nunca crea la sesión sin
mostrar antes el prompt exacto y esperar el visto bueno del usuario.
"""

from __future__ import annotations

import time

from cristopher.config import ROOT
from cristopher.sesiones import SesionesError, get_gestor_sesiones
from cristopher.tools.google_tools import confirmar_accion_con_texto

_TIPOS_VALIDOS = ("code", "chat")


def auto_arreglo_lanzar_sesion(problema: str, prompt: str, tipo: str, ruta: str = "") -> str:
    """Lanza una sesión de investigación para un límite propio detectado en
    conversación, PIDIENDO CONFIRMACIÓN antes con el prompt exacto que se enviaría.

    Args:
        problema: descripción corta de qué no pudiste hacer y por qué.
        prompt: instrucción exacta que le mandarías a la sesión como primer turno.
        tipo: 'code' (investiga/arregla un proyecto real, por defecto el tuyo
            propio) o 'chat' (solo consulta el enfoque, sin tocar disco).
        ruta: carpeta del proyecto para tipo='code'. Vacía = tu propio proyecto
            (auto-reparación); solo pon otra si el usuario mencionó una carpeta
            distinta. Se ignora con tipo='chat'.
    """
    problema = (problema or "").strip()
    prompt = (prompt or "").strip()
    tipo = (tipo or "").strip().lower()
    if not problema:
        return "ERROR: falta describir el problema."
    if not prompt:
        return "ERROR: falta el prompt exacto que le mandarías a la sesión."
    if tipo not in _TIPOS_VALIDOS:
        return f"ERROR: tipo {tipo!r} no válido. Usa 'code' o 'chat'."

    ruta = (ruta or "").strip()
    if tipo == "code" and not ruta:
        ruta = str(ROOT)

    nombre = f"autoarreglo-{int(time.time())}"
    etiqueta = "código" if tipo == "code" else "chat"
    cuerpo = (
        f"Voy a lanzar una sesión de investigación ({etiqueta}) para: {problema}\n\n"
        f"Esto es lo que le voy a escribir:\n\n« {prompt} »\n\n"
        "¿Confirmas, prefieres reescribirlo, o lo cancelo?"
    )
    aprobado, texto_custom = confirmar_accion_con_texto(cuerpo)
    if not aprobado:
        return "Cancelado: no se lanzó ninguna sesión."

    tarea = texto_custom.strip() or prompt
    try:
        get_gestor_sesiones().crear(
            nombre, ruta, tarea, "directo", tipo, "sonnet", "", "", "auto_arreglo",
        )
    except SesionesError as exc:
        return f"ERROR: {exc}"
    return f"Sesión de auto-arreglo '{nombre}' lanzada ({etiqueta})."
