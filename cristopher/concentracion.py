"""Modo Concentración: orquesta piezas que CRISTOPHER ya controla para defender un
bloque de deep work.

Al iniciar una sesión: silencia los avisos de baja prioridad del demonio proactivo
(sin perderlos, se encolan), despeja las distracciones configuradas (minimizar por
defecto, cerrar solo si se pidió), pone música de foco y fija un volumen de sistema.
Al terminar (a mano o por tiempo): restaura música/volumen y entrega un resumen de lo
que se acumuló mientras tanto.

Soporta también un modo por TURNOS (foco → descanso → foco…) cuando el propio usuario
lo pide explícitamente en la instrucción (p. ej. "concentración 20 min y luego 5 de
descanso"); CRISTOPHER nunca encadena bloques por iniciativa propia.

Config autoeditable en data/concentracion.json (gitignored, mismo patrón que
personalidad.py/briefing.py). Estado runtime en memoria (dict + Lock): lo tocan dos
hilos (el worker del agente vía las tools, y el demonio proactivo en su sondeo), nunca
se persiste — no tiene sentido sobrevivir a un reinicio del proceso.
"""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime
from typing import Optional

from cristopher.config import DATA

_PATH = DATA / "concentracion.json"
_CFG_LOCK = threading.Lock()

_DEFAULTS: dict = {
    "apps_distractoras": [],
    "accion_distraccion": "minimizar",  # "minimizar" | "cerrar"
    "musica": "",                        # "" | "favoritos" | nombre de una lista
    "volumen_sistema": None,             # None = no tocar el volumen
    "duracion_defecto_min": 25,
    "silenciar_hasta_nivel": 2,
}

_ACCIONES_VALIDAS = {"minimizar", "cerrar"}


def _leer_config() -> dict:
    if not _PATH.exists():
        return dict(_DEFAULTS)
    try:
        data = json.loads(_PATH.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return dict(_DEFAULTS)
        return {**_DEFAULTS, **data}
    except (json.JSONDecodeError, OSError):
        return dict(_DEFAULTS)


def _escribir_config(cfg: dict) -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    _PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


# --- Configuración autoeditable ------------------------------------------------
def configurar(
    duracion_defecto_min: Optional[int] = None,
    accion_distraccion: Optional[str] = None,
    musica: Optional[str] = None,
    volumen_sistema: Optional[int] = None,
    silenciar_hasta_nivel: Optional[int] = None,
) -> str:
    """Actualiza los campos indicados de la config (los que se omitan no cambian)."""
    cambios = []
    with _CFG_LOCK:
        cfg = _leer_config()
        if duracion_defecto_min is not None:
            cfg["duracion_defecto_min"] = max(1, int(duracion_defecto_min))
            cambios.append(f"duración por defecto: {cfg['duracion_defecto_min']} min")
        if accion_distraccion is not None:
            accion = accion_distraccion.strip().lower()
            if accion not in _ACCIONES_VALIDAS:
                return "accion_distraccion debe ser 'minimizar' o 'cerrar'."
            cfg["accion_distraccion"] = accion
            cambios.append(f"acción ante distracciones: {accion}")
        if musica is not None:
            cfg["musica"] = musica.strip()
            cambios.append(f"música de foco: {cfg['musica'] or '(ninguna)'}")
        if volumen_sistema is not None:
            cfg["volumen_sistema"] = max(0, min(100, int(volumen_sistema)))
            cambios.append(f"volumen de foco: {cfg['volumen_sistema']}%")
        if silenciar_hasta_nivel is not None:
            cfg["silenciar_hasta_nivel"] = max(0, min(3, int(silenciar_hasta_nivel)))
            cambios.append(f"silencia avisos de nivel <= {cfg['silenciar_hasta_nivel']}")
        if not cambios:
            return "No indicaste ningún cambio de configuración."
        _escribir_config(cfg)
    return "Configuración de concentración actualizada: " + "; ".join(cambios) + "."


def app_distractora_agregar(nombre: str) -> str:
    """Añade UNA app a la lista de distracciones a despejar al iniciar el foco."""
    nombre = (nombre or "").strip()
    if not nombre:
        return "No indicaste qué app añadir."
    with _CFG_LOCK:
        cfg = _leer_config()
        apps = cfg["apps_distractoras"]
        if nombre.lower() in (a.lower() for a in apps):
            return f"«{nombre}» ya estaba en la lista de distracciones."
        apps.append(nombre)
        _escribir_config(cfg)
    return f"Añadida a distracciones: «{nombre}»."


def app_distractora_quitar(nombre: str) -> str:
    """Quita las apps de la lista de distracciones cuyo nombre contenga `nombre`."""
    nombre = (nombre or "").strip()
    if not nombre:
        return "No indicaste qué app quitar."
    with _CFG_LOCK:
        cfg = _leer_config()
        frag = nombre.lower()
        apps = cfg["apps_distractoras"]
        quedan = [a for a in apps if frag not in a.lower()]
        quitadas = [a for a in apps if frag in a.lower()]
        if not quitadas:
            return f"No encontré ninguna distracción guardada que mencione: {nombre}"
        cfg["apps_distractoras"] = quedan
        _escribir_config(cfg)
    return "Quitadas de distracciones: " + ", ".join(quitadas) + "."


# --- Estado runtime compartido (worker del agente + hilo del demonio) --------
_LOCK = threading.RLock()
_estado: dict = {
    "activo": False,
    "fase": None,           # "foco" | "descanso" | None
    "until": None,           # epoch seconds: fin de la fase actual
    "volumen_previo": None,  # nivel 0-100 guardado para restaurar
    "avisos_cola": [],       # [{"nivel": int, "mensaje": str, "hora": str}]
    "ciclo": None,           # {"minutos_foco","minutos_descanso","reps_totales","reps_hechas"}
}


def _publicar_bus() -> None:
    try:
        from cristopher import bus
        bus.set_concentracion(estado_actual())
    except Exception:
        pass  # el HUD es opcional: nunca debe romper la orquestación


def esta_silenciando(nivel: int) -> bool:
    """True si el demonio debe ENCOLAR (no entregar) un aviso de este nivel ahora mismo."""
    with _LOCK:
        if not _estado["activo"] or _estado["fase"] != "foco":
            return False
    cfg = _leer_config()
    return nivel <= cfg["silenciar_hasta_nivel"]


def encolar_aviso(nivel: int, mensaje: str) -> None:
    entrada = {"nivel": nivel, "mensaje": mensaje, "hora": datetime.now().strftime("%H:%M")}
    with _LOCK:
        _estado["avisos_cola"].append(entrada)
    _publicar_bus()


def _flush_avisos() -> str:
    with _LOCK:
        cola = _estado["avisos_cola"]
        _estado["avisos_cola"] = []
    if not cola:
        return ""
    lineas = "\n".join(f"- [{a['hora']}] {a['mensaje']}" for a in cola)
    return f"Mientras te concentrabas llegaron {len(cola)} aviso(s):\n{lineas}"


# --- Aplicar / restaurar un bloque de foco -----------------------------------
def _aplicar_bloque(cfg: dict) -> list[str]:
    """Despeja distracciones, pone música de foco y fija el volumen. Devuelve notas
    (avisos de lo que no se pudo hacer) para incluir en la respuesta al usuario."""
    notas: list[str] = []

    apps = cfg.get("apps_distractoras") or []
    if apps:
        if cfg.get("accion_distraccion") == "cerrar":
            from cristopher.tools.system_apps import cerrar_app
            for nombre in apps:
                notas.append(cerrar_app(nombre))
        else:
            from cristopher.tools.system_apps import minimizar_por_nombre
            notas.append(minimizar_por_nombre(apps))

    musica = (cfg.get("musica") or "").strip()
    if musica:
        from cristopher.tools import musica_tools
        if musica.lower() == "favoritos":
            notas.append(musica_tools.reproducir_favoritos())
        else:
            notas.append(musica_tools.reproducir_lista(musica))

    nivel = cfg.get("volumen_sistema")
    if nivel is not None:
        from cristopher.tools.control_pc_tools import _volumen_actual, volumen_sistema
        with _LOCK:
            if _estado["volumen_previo"] is None:
                try:
                    _estado["volumen_previo"] = _volumen_actual()
                except Exception:
                    _estado["volumen_previo"] = None
        volumen_sistema("fijar", nivel=nivel)

    return notas


def _restaurar_bloque() -> None:
    """Para la música de foco y restaura el volumen previo si se había guardado."""
    try:
        from cristopher.tools import musica_tools
        musica_tools.pausar()
    except Exception:
        pass
    with _LOCK:
        previo = _estado["volumen_previo"]
        _estado["volumen_previo"] = None
    if previo is not None:
        try:
            from cristopher.tools.control_pc_tools import volumen_sistema
            volumen_sistema("fijar", nivel=previo)
        except Exception:
            pass


# --- API pública: iniciar / terminar / estado / tick -------------------------
def iniciar(
    minutos: Optional[int] = None,
    descanso_min: Optional[int] = None,
    repeticiones: int = 1,
) -> str:
    """Arranca una sesión de concentración. Si ya hay una activa, no hace nada."""
    with _LOCK:
        if _estado["activo"]:
            return (
                "Ya hay una sesión de concentración activa. Usa concentracion_terminar "
                "si quieres cortarla antes."
            )
        cfg = _leer_config()
        minutos_reales = int(minutos) if minutos else cfg["duracion_defecto_min"]
        if minutos_reales <= 0:
            minutos_reales = cfg["duracion_defecto_min"]

        ciclo = None
        if descanso_min:
            reps = max(1, int(repeticiones or 1))
            ciclo = {
                "minutos_foco": minutos_reales,
                "minutos_descanso": int(descanso_min),
                "reps_totales": reps,
                "reps_hechas": 0,
            }

        _estado["activo"] = True
        _estado["fase"] = "foco"
        _estado["until"] = time.time() + minutos_reales * 60
        _estado["volumen_previo"] = None
        _estado["avisos_cola"] = []
        _estado["ciclo"] = ciclo

    notas = _aplicar_bloque(cfg)
    _publicar_bus()

    msg = f"Modo concentración activado: {minutos_reales} min de foco"
    if ciclo:
        msg += f", luego {ciclo['minutos_descanso']} min de descanso"
        if ciclo["reps_totales"] > 1:
            msg += f" ({ciclo['reps_totales']} rondas)"
    msg += ". Aviso: el fin de bloque puede tardar hasta un sondeo del demonio en notarse."
    if notas:
        msg += "\n" + "\n".join(n for n in notas if n)
    return msg


def terminar_ahora() -> str:
    """Aborta YA la sesión completa (cancela repeticiones restantes de un ciclo si
    las hay), restaura música/volumen y entrega el resumen de avisos acumulados."""
    with _LOCK:
        if not _estado["activo"]:
            return "No hay ninguna sesión de concentración activa."
        fase = _estado["fase"]
        ciclo = _estado["ciclo"]

    if fase == "foco":
        _restaurar_bloque()
    # Si fase == "descanso", el bloque de foco ya se restauró al entrar en descanso.

    faltan = 0
    if ciclo:
        faltan = ciclo["reps_totales"] - ciclo["reps_hechas"] - (1 if fase == "foco" else 0)
        faltan = max(0, faltan)

    resumen = _flush_avisos()
    with _LOCK:
        _estado["activo"] = False
        _estado["fase"] = None
        _estado["until"] = None
        _estado["ciclo"] = None
    _publicar_bus()

    partes = ["Modo concentración terminado."]
    if faltan:
        partes.append(f"Se canceló el ciclo con {faltan} bloque(s) pendientes.")
    if resumen:
        partes.append(resumen)
    return "\n".join(partes)


def _finalizar(resumen_previo: str = "") -> str:
    resumen = _flush_avisos()
    with _LOCK:
        _estado["activo"] = False
        _estado["fase"] = None
        _estado["until"] = None
        _estado["ciclo"] = None
    _publicar_bus()
    partes = ["Modo concentración terminado."]
    if resumen_previo:
        partes.append(resumen_previo)
    if resumen:
        partes.append(resumen)
    return "\n".join(partes)


def tick() -> Optional[str]:
    """Llamado por el demonio en cada sondeo. Devuelve un mensaje si algo cambió
    (fin de bloque/descanso, o fin de sesión), o None si no toca todavía."""
    with _LOCK:
        if not _estado["activo"] or _estado["until"] is None:
            return None
        if time.time() < _estado["until"]:
            return None
        fase = _estado["fase"]
        ciclo = _estado["ciclo"]

    if fase == "foco":
        _restaurar_bloque()
        resumen = _flush_avisos()
        if ciclo:
            with _LOCK:
                _estado["fase"] = "descanso"
                _estado["until"] = time.time() + ciclo["minutos_descanso"] * 60
            _publicar_bus()
            msg = (
                f"Bloque de concentración terminado ({ciclo['minutos_foco']} min). "
                f"Descanso de {ciclo['minutos_descanso']} min."
            )
            return msg + ("\n" + resumen if resumen else "")
        return _finalizar(resumen)

    # fase == "descanso"
    if ciclo:
        with _LOCK:
            ciclo["reps_hechas"] += 1
            terminado = ciclo["reps_hechas"] >= ciclo["reps_totales"]
        if not terminado:
            cfg = _leer_config()
            with _LOCK:
                _estado["fase"] = "foco"
                _estado["until"] = time.time() + ciclo["minutos_foco"] * 60
            notas = _aplicar_bloque(cfg)
            _publicar_bus()
            msg = (
                f"Descanso terminado. Bloque {ciclo['reps_hechas'] + 1}/"
                f"{ciclo['reps_totales']} de concentración ({ciclo['minutos_foco']} min)."
            )
            if notas:
                msg += "\n" + "\n".join(n for n in notas if n)
            return msg
        return _finalizar()
    return _finalizar()


def estado_actual() -> dict:
    """Snapshot para concentracion_estado() y el panel del HUD."""
    with _LOCK:
        activo = _estado["activo"]
        fase = _estado["fase"]
        until = _estado["until"]
        n_cola = len(_estado["avisos_cola"])
        ciclo = dict(_estado["ciclo"]) if _estado["ciclo"] else None
    restante_seg = max(0, int(until - time.time())) if (activo and until) else 0
    snap = {
        "activo": activo,
        "fase": fase,
        "restante_seg": restante_seg,
        "avisos_en_cola": n_cola,
    }
    if ciclo:
        snap["ciclo"] = {
            "bloque_actual": ciclo["reps_hechas"] + 1,
            "bloques_totales": ciclo["reps_totales"],
        }
    return snap
