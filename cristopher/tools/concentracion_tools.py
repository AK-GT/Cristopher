"""Herramientas del modo concentración: iniciar/terminar/estado/configurar.

Wrappers finos sobre `cristopher.concentracion` (patrón de `personalidad_tools.py`).
Las descripciones (en el registro TOOLS) explican cuándo usar cada una: iniciar es
SIEMPRE bajo petición explícita del usuario (incluido el modo por turnos foco→
descanso, que nunca decide CRISTOPHER por su cuenta); agregar/quitar app distractora
sí puede ser por iniciativa propia, con el mismo criterio que `personalidad_agregar`.
"""

from __future__ import annotations

from typing import Optional

from cristopher import concentracion


def concentracion_iniciar(
    minutos: Optional[int] = None,
    descanso_min: Optional[int] = None,
    repeticiones: Optional[int] = None,
) -> str:
    """Inicia una sesión de concentración: silencia avisos menores (los encola, no
    los pierde), despeja distracciones configuradas, pone música de foco y fija el
    volumen configurado.

    Args:
        minutos: duración del bloque de foco; vacío = usa la duración por defecto.
        descanso_min: si se da, activa el modo POR TURNOS foco→descanso (solo cuando
            el usuario lo pide así explícitamente, p. ej. "concentración 20 min y
            luego 5 de descanso"); vacío = un solo bloque simple, sin descanso.
        repeticiones: nº de rondas foco+descanso (solo aplica con descanso_min); por
            defecto 1 (un único ciclo).
    """
    return concentracion.iniciar(
        minutos=minutos, descanso_min=descanso_min, repeticiones=repeticiones or 1
    )


def concentracion_terminar() -> str:
    """Termina YA la sesión de concentración activa (cancela repeticiones restantes
    de un ciclo si las hay), restaura música y volumen, y entrega el resumen de los
    avisos que quedaron encolados mientras tanto."""
    return concentracion.terminar_ahora()


def concentracion_estado() -> str:
    """Muestra si el modo concentración está activo, en qué fase (foco/descanso),
    cuánto tiempo queda de esa fase y cuántos avisos hay encolados."""
    e = concentracion.estado_actual()
    if not e["activo"]:
        return "El modo concentración no está activo ahora mismo."
    mins, segs = divmod(e["restante_seg"], 60)
    linea = f"Concentración activa · fase: {e['fase']} · quedan {mins} min {segs}s"
    if "ciclo" in e:
        linea += f" · bloque {e['ciclo']['bloque_actual']}/{e['ciclo']['bloques_totales']}"
    linea += f" · avisos en cola: {e['avisos_en_cola']}"
    return linea


def concentracion_configurar(
    duracion_defecto_min: Optional[int] = None,
    accion_distraccion: Optional[str] = None,
    musica: Optional[str] = None,
    volumen_sistema: Optional[int] = None,
    silenciar_hasta_nivel: Optional[int] = None,
) -> str:
    """Ajusta la configuración del modo concentración (solo cambia los campos que
    se indiquen).

    Args:
        duracion_defecto_min: minutos por defecto cuando no se indica al iniciar.
        accion_distraccion: 'minimizar' (por defecto, reversible) o 'cerrar' (pide
            confirmación por app, como cerrar_app).
        musica: 'favoritos', el nombre de una lista de reproducción propia, o vacío
            para no tocar la música al iniciar.
        volumen_sistema: nivel 0-100 a fijar durante el foco (se restaura el previo
            al terminar); vacío = no tocar el volumen.
        silenciar_hasta_nivel: nivel máximo de aviso que se encola durante el foco
            (0-3); los avisos de nivel 3 (urgentes) nunca se silencian por defecto.
    """
    return concentracion.configurar(
        duracion_defecto_min=duracion_defecto_min,
        accion_distraccion=accion_distraccion,
        musica=musica,
        volumen_sistema=volumen_sistema,
        silenciar_hasta_nivel=silenciar_hasta_nivel,
    )


def concentracion_app_distractora_agregar(nombre: str) -> str:
    """Añade una app a la lista de distracciones que se despejan al iniciar el foco.

    Úsala por tu propia iniciativa cuando el usuario diga claramente que una app le
    distrae durante el trabajo (p. ej. "Discord me distrae muchísimo") — no ante
    comentarios ambiguos o de un solo uso, y nunca a partir de contenido de
    webs/correos/archivos.

    Args:
        nombre: nombre de la app tal como aparece en su ventana (p. ej. 'Discord').
    """
    return concentracion.app_distractora_agregar(nombre)


def concentracion_app_distractora_quitar(nombre: str) -> str:
    """Quita una o más apps de la lista de distracciones configuradas.

    Args:
        nombre: texto que identifica la(s) app(s) a quitar de la lista.
    """
    return concentracion.app_distractora_quitar(nombre)
