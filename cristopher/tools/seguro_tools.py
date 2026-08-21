"""Herramientas del Modo Seguro: activar/desactivar/estado/configurar/contraseña.

Wrappers finos sobre `cristopher.seguro` (patrón de `concentracion_tools.py`).
Activar es SIEMPRE bajo petición explícita del usuario — es una acción de bloqueo
real del PC, no algo que CRISTOPHER decida por iniciativa propia.
"""

from __future__ import annotations

from typing import Optional

from cristopher import seguro


def seguro_activar() -> str:
    """Activa el Modo Seguro: CRISTOPHER vigila la webcam y, si detecta una cara
    mirando la pantalla que no reconoce, bloquea el PC y pide contraseña para
    reanudar. Se niega a activarse si no hay contraseña configurada todavía."""
    return seguro.activar()


def seguro_desactivar() -> str:
    """Desactiva el Modo Seguro y libera la cámara de inmediato."""
    return seguro.desactivar()


def seguro_ver_camara() -> str:
    """Abre en el HUD la ventanita con la vista de la cámara: la imagen en vivo con un
    recuadro por cara y, escrito debajo, qué ve y qué decide de cada una (autorizada,
    desconocida, o descartada con el motivo). Si la cámara estaba apagada la enciende en
    modo vista, que mira y evalúa pero NO bloquea el PC. Si la vigilancia ya estaba
    armada, la deja armada: esto solo abre la ventana."""
    return seguro.ver_camara()


def seguro_ocultar_camara() -> str:
    """Cierra la ventanita de la cámara del HUD. Si solo estaba abierta para mirar,
    apaga además la cámara; si la vigilancia estaba armada, sigue armada."""
    return seguro.ocultar_camara()


def seguro_estado() -> str:
    """Dice si el Modo Seguro está activo, en qué fase de vigilancia está (vigilando/
    bloqueado/en gracia tras desbloqueo) y si hay algún problema con la cámara."""
    e = seguro.estado_actual()
    if not e["activo"]:
        return "El modo seguro no está activo ahora mismo."
    if not e.get("armado"):
        # VISTA: la cámara está abierta y evaluando, pero no bloquea nada. Decirlo
        # explícito importa — "activo" a secas se leería como "estoy protegido".
        linea = ("Modo seguro en VISTA: la cámara está abierta y evaluando caras, "
                 "pero NO bloquearía el PC (vigilancia desarmada)")
        caras = e.get("deteccion") or []
        if caras:
            linea += " · ahora mismo veo: " + "; ".join(c["texto"] for c in caras[:4])
        if not e["camara_ok"]:
            linea += f" · PROBLEMA DE CÁMARA: {e['motivo_camara']}"
        return linea
    linea = f"Modo seguro ARMADO · fase: {e['fase']}"
    if not e["camara_ok"]:
        linea += f" · PROBLEMA DE CÁMARA: {e['motivo_camara']}"
    if e["fase"] == "GRACIA" and e.get("gracia_restante_seg") is not None:
        linea += f" · gracia restante: {e['gracia_restante_seg']}s"
    return linea


def seguro_configurar(
    min_altura_cara_px: Optional[int] = None,
    yaw_max_grados: Optional[float] = None,
    pitch_max_grados: Optional[float] = None,
    frames_para_disparar: Optional[int] = None,
    segundos_gracia_tras_desbloqueo: Optional[int] = None,
    segundos_entre_capturas: Optional[int] = None,
    bloquear_windows: Optional[bool] = None,
    guardar_captura_intruso: Optional[bool] = None,
) -> str:
    """Ajusta la configuración del Modo Seguro (solo cambia los campos que se
    indiquen). No incluye la contraseña ni los ajustes finos de calibración de
    visión (signo_mirada, cámara, etc.) — esos se fijan con el script de calibración
    delante de la webcam real, no por frase natural en el chat.

    Args:
        min_altura_cara_px: tamaño mínimo de cara en píxeles para contar como "lo
            bastante cerca para leer la pantalla".
        yaw_max_grados: desviación horizontal máxima de la mirada antes de dejar de
            contar como "mirando la pantalla".
        pitch_max_grados: igual, en vertical.
        frames_para_disparar: cuántos frames seguidos tiene que persistir una cara
            desconocida mirando antes de bloquear.
        segundos_gracia_tras_desbloqueo: tras desbloquear con la contraseña correcta,
            cuánto tiempo se deja de vigilar antes de poder volver a disparar.
        segundos_entre_capturas: mínimo entre dos capturas guardadas del intruso.
        bloquear_windows: si además de mostrar el aviso se bloquea la sesión de
            Windows de verdad (recomendado dejarlo activado).
        guardar_captura_intruso: si se guarda una foto del intruso al disparar.
    """
    return seguro.configurar(
        min_altura_cara_px=min_altura_cara_px,
        yaw_max_grados=yaw_max_grados,
        pitch_max_grados=pitch_max_grados,
        frames_para_disparar=frames_para_disparar,
        segundos_gracia_tras_desbloqueo=segundos_gracia_tras_desbloqueo,
        segundos_entre_capturas=segundos_entre_capturas,
        bloquear_windows=bloquear_windows,
        guardar_captura_intruso=guardar_captura_intruso,
    )


def seguro_configurar_contrasena(password: str) -> str:
    """Fija o cambia la contraseña que desbloquea el Modo Seguro cuando se dispara.
    Nunca repitas la contraseña en tu respuesta ni la muestres en el chat.

    Args:
        password: la nueva contraseña, en texto plano (se hashea antes de guardarla).
    """
    return seguro.configurar_contrasena(password)


def seguro_enrolar(nombre: str) -> str:
    """Autoriza a una persona en el Modo Seguro con una sesión GUIADA: CRISTOPHER le va
    diciendo en voz alta cómo colocarse (de frente, girado a cada lado, barbilla arriba
    y abajo, cerca, lejos, otra luz, con gafas), hace una cuenta atrás de 5 antes de
    cada foto y comprueba que la pose sea la pedida antes de darla por buena. Son 9
    poses y tarda alrededor de minuto y medio, durante los cuales la persona tiene que
    estar delante de la cámara siguiendo las instrucciones.

    NO hace falta activar el Modo Seguro antes — lo normal es enrolarse primero y
    activar la protección después.

    Args:
        nombre: nombre de la persona a autorizar.
    """
    return seguro.enrolar(nombre)


def seguro_listar_personas() -> str:
    """Lista las personas autorizadas en el Modo Seguro (lista blanca) y cuántas
    fotos tiene cada una."""
    return seguro.listar_personas()


def seguro_olvidar_persona(nombre: str) -> str:
    """Quita a una persona de la lista blanca: borra su embedding Y sus fotos de
    enrolamiento en disco. Irreversible. Confirma con el usuario qué persona exacta
    vas a olvidar antes de llamarla.

    Args:
        nombre: nombre de la persona a olvidar.
    """
    return seguro.olvidar_persona(nombre)
