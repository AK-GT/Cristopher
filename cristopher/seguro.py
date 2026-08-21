"""Modo Seguro — único módulo con estado global: config, cámara, hilo, FSM, disparo.
Vigila la webcam y, si detecta una cara mirando la pantalla que persiste
`frames_para_disparar` frames, bloquea el PC (Windows real + overlay propio) y exige
contraseña para reanudar.

Fase 2 añade identidad: si la galería tiene gente enrolada y los umbrales están
calibrados (`scripts/calibrar_seguro.py`), una cara reconocida NUNCA dispara — solo
las que la galería no reconoce. Sin calibrar o con galería vacía, cae al comportamiento
de Fase 1 (cualquier cara dispara), nunca al revés: ante la duda, DESCONOCIDO.

Config autoeditable en data/seguro.json (patrón exacto de concentracion.py). Estado
runtime en memoria (dict + RLock), igual que concentracion.py: lo tocan el hilo
vigilante (dueño único de la cámara) y el hilo HTTP del HUD (al leer /seguro/estado o
resolver /seguro/desbloquear) — y ahora también el hilo del agente, al enrolar/olvidar/
listar personas por chat. No se persiste — no tiene sentido sobrevivir a un reinicio
del proceso; solo la config y la galería (en disco) sobreviven.
"""

from __future__ import annotations

import ctypes
import hashlib
import hmac
import json
import os
import shutil
import threading
import time
from datetime import datetime
from typing import Optional

from cristopher.config import DATA

_PATH = DATA / "seguro.json"
_CFG_LOCK = threading.Lock()

_DEFAULTS: dict = {
    "fps_vigilancia": 4,
    "camara_indice": 0,
    "min_altura_cara_px": 90,
    "yaw_max_grados": 35,
    "pitch_max_grados": 25,
    "usar_mirada": True,
    "signo_mirada": 1,            # lo fija el usuario con scripts/probar_seguro_vision.py
    "grados_mirada_max": 35,
    "frames_para_disparar": 6,
    "segundos_cuenta_atras": 5,   # antes de cada foto del enrolamiento guiado
    "max_votos": 5,
    "segundos_gracia_tras_desbloqueo": 60,
    "segundos_entre_capturas": 30,
    "bloquear_windows": True,
    "guardar_captura_intruso": True,
    "modelo_embedding": "auraface-v1",
    "umbral_alto": None,          # los escribe calibrar_seguro.py (Fase 2)
    "umbral_bajo": None,
    "password_hash": "",
    "password_salt": "",
}

# Reintento de apertura de cámara cuando está ocupada (Zoom/Teams/Meet la tienen
# cogida en exclusiva en Windows) — constante interna, no forma parte de la config
# porque no es algo que el usuario deba tocar por chat.
_REINTENTO_CAMARA_SEGUNDOS = 10

# Parámetros de hashlib.scrypt — fijos en código, no en el JSON: no son configurables
# por diseño (evita una abstracción prematura para un valor que nunca cambia).
_SCRYPT_N, _SCRYPT_R, _SCRYPT_P, _SCRYPT_DKLEN = 2 ** 14, 8, 1, 64

_MODELOS_DIR = DATA / "seguro" / "modelos"
_CAPTURAS_DIR = DATA / "seguro" / "capturas"
_GALERIA_NPZ = DATA / "seguro" / "galeria.npz"
_PERSONAS_JSON = DATA / "seguro" / "personas.json"
_ENROLAMIENTO_DIR = DATA / "seguro" / "enrolamiento"
_CALIBRACION_JSON = DATA / "seguro" / "calibracion.json"
_TASK_PATH = _MODELOS_DIR / "face_landmarker.task"
_TASK_URL = (
    "https://storage.googleapis.com/mediapipe-models/face_landmarker/"
    "face_landmarker/float16/1/face_landmarker.task"
)

# Reintento de carga/descarga del modelo de identidad (AuraFace) si falla — mismo
# criterio que _REINTENTO_CAMARA_SEGUNDOS: nunca martillear la red a 4fps.
_EMBEDDER_REINTENTO_SEGUNDOS = 60

# Frescura máxima del frame cacheado para enrolar (evita enrolar contra un frame de
# cuando el modo estaba en BLOQUEADO/GRACIA, donde el bucle deja de detectar).
_ENROLAR_FRESCURA_SEGUNDOS = 5.0

# Sesión guiada de enrolamiento (ver _sesion_guiada). Dos intentos por pose porque
# fallar una es normal — te has girado de más, has parpadeado — y repetir la sesión
# entera por eso sería absurdo; la ventana de captura da margen tras el cero para no
# exigir el instante exacto.
_CUENTA_ATRAS_DEFECTO = 5
_ENROLAR_INTENTOS_POR_POSE = 2
_ENROLAR_VENTANA_CAPTURA_SEGUNDOS = 4.0
# Una sesión guiada a la vez: son ~90s hablando y moviéndote delante de la cámara, y dos
# a la vez darían instrucciones contradictorias a la misma persona.
_SESION_ENROLAMIENTO = threading.Semaphore(1)

# Umbral de aviso de recalibración: la galería creció un 50% desde la última calibración.
_RECALIBRAR_CRECIMIENTO = 1.5


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
    """Escritura ATÓMICA (tmp + os.replace, mismo patrón que la descarga de modelos).
    No es cosmético: el hilo vigilante relee este fichero en caliente y hay un segundo
    proceso que lo escribe (scripts/calibrar_seguro.py), así que sin esto se puede leer
    un JSON a medias — y `_leer_config` cae a `_DEFAULTS`, que trae `password_hash`
    vacío y umbrales None. Perder la contraseña por una escritura interrumpida deja el
    modo imposible de desbloquear."""
    DATA.mkdir(parents=True, exist_ok=True)
    tmp = _PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, _PATH)


def _firma_config() -> "tuple[int, int] | None":
    """(mtime_ns, tamaño) de data/seguro.json — o None si aún no existe. Es lo que deja
    releer la config SOLO cuando cambia de verdad. Tiene que ir por el fichero y no por
    un flag en memoria porque quien escribe los umbrales (scripts/calibrar_seguro.py)
    corre en OTRO proceso."""
    try:
        st = _PATH.stat()
        return (st.st_mtime_ns, st.st_size)
    except OSError:
        return None


# --- Configuración autoeditable -------------------------------------------------
def configurar(
    min_altura_cara_px: Optional[int] = None,
    yaw_max_grados: Optional[float] = None,
    pitch_max_grados: Optional[float] = None,
    frames_para_disparar: Optional[int] = None,
    segundos_gracia_tras_desbloqueo: Optional[int] = None,
    segundos_entre_capturas: Optional[int] = None,
    bloquear_windows: Optional[bool] = None,
    guardar_captura_intruso: Optional[bool] = None,
) -> str:
    """Actualiza los campos indicados de data/seguro.json (los que se omitan no
    cambian). Nunca toca password_hash/salt/umbrales — esos van por su propia función
    o los escribe calibrar_seguro.py en Fase 2."""
    # TOPES POR ARRIBA, no solo por abajo. Sin ellos, un solo `frames_para_disparar=9999`
    # o `min_altura_cara_px=5000` deja el modo encendido y en verde pero completamente
    # inerte — no hace falta apagar la protección para anularla, basta con subir un
    # número. Es la superficie que aprovecharía una inyección desde una web o un correo.
    cambios = []
    avisos = []
    with _CFG_LOCK:
        cfg = _leer_config()
        if min_altura_cara_px is not None:
            v = int(min_altura_cara_px)
            cfg["min_altura_cara_px"] = max(20, min(400, v))
            if v != cfg["min_altura_cara_px"]:
                avisos.append(f"altura mínima acotada a {cfg['min_altura_cara_px']}px (pediste {v})")
            cambios.append(f"altura mínima de cara: {cfg['min_altura_cara_px']}px")
        if yaw_max_grados is not None:
            v = float(yaw_max_grados)
            cfg["yaw_max_grados"] = max(5.0, min(90.0, v))
            if v != cfg["yaw_max_grados"]:
                avisos.append(f"yaw acotado a {cfg['yaw_max_grados']:.0f}° (pediste {v:.0f}°)")
            cambios.append(f"yaw máximo: {cfg['yaw_max_grados']:.0f}°")
        if pitch_max_grados is not None:
            v = float(pitch_max_grados)
            cfg["pitch_max_grados"] = max(5.0, min(90.0, v))
            if v != cfg["pitch_max_grados"]:
                avisos.append(f"pitch acotado a {cfg['pitch_max_grados']:.0f}° (pediste {v:.0f}°)")
            cambios.append(f"pitch máximo: {cfg['pitch_max_grados']:.0f}°")
        if frames_para_disparar is not None:
            v = int(frames_para_disparar)
            # 60 frames a 4 fps son 15 segundos mirando la pantalla: más que eso ya no
            # es "persistencia", es desactivar el disparo por la puerta de atrás.
            cfg["frames_para_disparar"] = max(1, min(60, v))
            if v != cfg["frames_para_disparar"]:
                avisos.append(f"frames acotados a {cfg['frames_para_disparar']} (pediste {v})")
            cambios.append(f"frames para disparar: {cfg['frames_para_disparar']}")
        if segundos_gracia_tras_desbloqueo is not None:
            # Media hora de gracia es dejar la vigilancia apagada, no "dar un margen".
            cfg["segundos_gracia_tras_desbloqueo"] = max(0, min(1800, int(segundos_gracia_tras_desbloqueo)))
            cambios.append(f"gracia tras desbloqueo: {cfg['segundos_gracia_tras_desbloqueo']}s")
        if segundos_entre_capturas is not None:
            cfg["segundos_entre_capturas"] = max(0, min(3600, int(segundos_entre_capturas)))
            cambios.append(f"mínimo entre capturas: {cfg['segundos_entre_capturas']}s")
        if bloquear_windows is not None:
            cfg["bloquear_windows"] = bool(bloquear_windows)
            cambios.append(f"bloquear Windows: {'sí' if cfg['bloquear_windows'] else 'no'}")
            if not cfg["bloquear_windows"]:
                # Sin el bloqueo real de Windows solo queda el overlay, que se esquiva
                # con Alt+F4 o Win+D. Que quede dicho en la respuesta, no enterrado.
                avisos.append(
                    "OJO: sin bloquear Windows, el modo seguro solo muestra un aviso en "
                    "pantalla que cualquiera puede cerrar — deja de proteger de verdad"
                )
        if guardar_captura_intruso is not None:
            cfg["guardar_captura_intruso"] = bool(guardar_captura_intruso)
            cambios.append(f"guardar captura del intruso: {'sí' if cfg['guardar_captura_intruso'] else 'no'}")
        if not cambios:
            return "No indicaste ningún cambio de configuración."
        _escribir_config(cfg)
    texto = "Configuración del modo seguro actualizada: " + "; ".join(cambios) + "."
    if avisos:
        texto += " " + ". ".join(avisos) + "."
    return texto


def configurar_contrasena(password: str) -> str:
    """Fija/cambia la contraseña que desbloquea el modo seguro. Nunca se loguea ni se
    devuelve en claro."""
    password = (password or "").strip()
    if not password:
        return "No indicaste ninguna contraseña."
    salt = os.urandom(16)
    h = hashlib.scrypt(
        password.encode("utf-8"), salt=salt,
        n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=_SCRYPT_DKLEN,
    )
    with _CFG_LOCK:
        cfg = _leer_config()
        cfg["password_salt"] = salt.hex()
        cfg["password_hash"] = h.hex()
        _escribir_config(cfg)
    return "Contraseña del modo seguro actualizada."


def _verificar_password(password: str, cfg: dict) -> bool:
    if not cfg.get("password_hash") or not cfg.get("password_salt"):
        return False
    try:
        salt = bytes.fromhex(cfg["password_salt"])
        esperado = bytes.fromhex(cfg["password_hash"])
    except ValueError:
        return False
    h = hashlib.scrypt(
        (password or "").encode("utf-8"), salt=salt,
        n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=_SCRYPT_DKLEN,
    )
    return hmac.compare_digest(h, esperado)


# --- Estado runtime (hilo vigilante + hilo HTTP del HUD) -------------------------
_LOCK = threading.RLock()
_activo_event = threading.Event()   # set = vigilando; clear = cámara cerrada/hilo dormido
# Set mientras una llamada a enrolar() está en curso Y el modo ya estaba vigilando:
# el bucle sigue detectando (enrolar() necesita frames frescos) pero SUSPENDE el
# disparo — sin esto, enrolar a alguien que todavía no está autorizado con el modo ya
# activo lo bloquearía a él mismo antes de terminar de enrolarse (bug real: activar
# para enrolar disparaba casi al instante contra la propia cara sin autorizar, dejando
# un bucle de bloqueo real hasta desactivar). Ver enrolar()/_ciclo_vigilancia.
_enrolando_event = threading.Event()
_estado: dict = {
    "activo": False,
    # VISTA es cámara + detección corriendo con el disparo DESARMADO. No es un adorno:
    # sin ella no puedes mirar lo que ve el modo sin que te bloquee la sesión, que es
    # justo lo que hace falta para calibrar umbrales (y lo que pasaba de verdad con la
    # galería sin calibrar: toda cara es DESCONOCIDA, incluida la del dueño).
    "fase": "INACTIVO",           # INACTIVO | VISTA | VIGILANDO | BLOQUEADO | GRACIA
    "armado": False,              # False = ve y evalúa, pero NUNCA bloquea
    "camara_ok": True,
    "motivo_camara": "",
    "gracia_hasta_ts": None,
    "ultimo_disparo_ts": None,    # cooldown de segundos_entre_capturas
    "ultimo_evento": "",
    # Veredicto por cara del último frame, para que el panel enseñe POR QUÉ decide lo
    # que decide. Se REEMPLAZA entera cada frame (nunca se muta en sitio), que es lo
    # que hace seguro el dict(_estado) superficial de estado_actual().
    "deteccion": [],
    # Sesión guiada en curso (pose pedida + cuenta atrás), o None. Es lo que deja al
    # panel del HUD pintar la instrucción en grande mientras estás de pie ante la cámara.
    "enrolamiento": None,
}

# Rótulo que el hilo vigilante debe pintar en el preview durante una sesión guiada. Vive
# aquí y no en `_estado` porque lo lee el bucle en CADA vuelta y no tiene por qué pasar
# por el RLock general.
_BANNER_LOCK = threading.Lock()
_banner_enrolamiento: "str | None" = None


def _publicar_banner_enrolamiento(texto: "str | None") -> None:
    global _banner_enrolamiento
    with _BANNER_LOCK:
        _banner_enrolamiento = texto


def _obtener_banner_enrolamiento() -> "str | None":
    with _BANNER_LOCK:
        return _banner_enrolamiento


def _publicar_bus() -> None:
    try:
        from cristopher import bus
        bus.set_seguro(estado_actual())
    except Exception:
        pass  # el HUD es opcional: nunca debe romper la vigilancia


def estado_actual() -> dict:
    """Snapshot para seguro_estado(), el bus/HUD y la ruta /seguro/estado."""
    with _LOCK:
        snap = dict(_estado)
    if snap["gracia_hasta_ts"]:
        snap["gracia_restante_seg"] = max(0, int(snap["gracia_hasta_ts"] - time.time()))
    return snap


def activar(armado: bool = True) -> str:
    """`armado=True` es la protección de verdad. `armado=False` abre la cámara en VISTA:
    detecta y evalúa igual, pero no bloquea nada — es lo que usa el panel del HUD para
    que puedas verte sin echarte de tu propia sesión. La contraseña solo se exige para
    ARMAR: en vista no hay nada que desbloquear."""
    cfg = _leer_config()
    if armado and not cfg.get("password_hash"):
        return (
            "El modo seguro necesita una contraseña configurada antes de armarse "
            "(seguro_configurar_contrasena) — bloquear algo que no puedes desbloquear "
            "es una trampa."
        )
    with _LOCK:
        if _estado["fase"] in ("BLOQUEADO", "GRACIA"):
            return "Hay un bloqueo en curso: desbloquéalo antes de cambiar el modo."
        if _estado["activo"] and _estado["armado"] == bool(armado):
            return ("El modo seguro ya está vigilando." if armado
                    else "La cámara ya está abierta en modo vista.")
        _estado["activo"] = True
        _estado["armado"] = bool(armado)
        # La transición INACTIVO→VIGILANDO de la FSM la hace ESTA función, no el bucle:
        # nadie la hacía y el estado se quedaba en "activo · fase INACTIVO", que es una
        # contradicción y encima la única señal de "estoy protegido" que ve el usuario.
        # Un problema de cámara no cambia la fase — para eso están camara_ok/motivo_camara.
        _estado["fase"] = "VIGILANDO" if armado else "VISTA"
        _estado["ultimo_evento"] = "vigilancia armada" if armado else "cámara en vista (no dispara)"
    _activo_event.set()
    _publicar_bus()
    return ("Modo seguro ARMADO: bloquearé el PC ante una cara que no reconozca."
            if armado else
            "Cámara abierta en modo vista: veo y evalúo, pero no bloqueo nada.")


def desactivar() -> str:
    with _LOCK:
        if not _estado["activo"]:
            return "El modo seguro no estaba activo."
        _estado["activo"] = False
        # Simétrico a activar(): la fase se cierra aquí y no un frame después, en el
        # finally del ciclo. Importa si se desactiva estando BLOQUEADO — así el overlay
        # (que sondea `fase != BLOQUEADO`) se cierra ya, sin esperar a la próxima vuelta.
        _estado["fase"] = "INACTIVO"
        _estado["armado"] = False
        _estado["gracia_hasta_ts"] = None
        _estado["deteccion"] = []
        _estado["ultimo_evento"] = "desactivado"
    _activo_event.clear()   # el hilo lo nota en su próxima vuelta y libera la cámara
    _publicar_bus()
    return "Modo seguro desactivado. La cámara se libera en un instante."


def _publicar_panel(abierto: bool) -> None:
    try:
        from cristopher import bus
        bus.set_seguro_panel(abierto)
    except Exception:
        pass  # el HUD es opcional: nunca debe romper nada de esto


def ver_camara() -> str:
    """Abre el panel de cámara del HUD sin tocar el ratón, y enciende la cámara en VISTA
    si estaba apagada.

    Clave: si el modo YA está armado, NO lo desarma — solo abre la ventana. Enseñar la
    imagen y bajar la guardia son dos cosas distintas, y confundirlas aquí sería la
    forma más tonta de desactivar la protección sin querer."""
    with _LOCK:
        ya_activo = _estado["activo"]
        armado = _estado["armado"]
    mensaje = "" if ya_activo else activar(armado=False)
    _publicar_panel(True)
    if not ya_activo:
        return f"Abro la vista de la cámara. {mensaje}"
    if armado:
        return ("Abro la vista de la cámara. Sigue ARMADO: bloquearé el PC ante una "
                "cara que no reconozca.")
    return "Abro la vista de la cámara. Sigue en vista: veo y evalúo, pero no bloqueo."


def ocultar_camara() -> str:
    """Cierra el panel. Igual que el aspa del propio panel: si solo estaba en VISTA,
    apaga también la cámara (nadie mira, nada vigila); si está ARMADO, la vigilancia
    sigue — cerrar una ventana no puede desproteger el equipo."""
    _publicar_panel(False)
    with _LOCK:
        activo = _estado["activo"]
        armado = _estado["armado"]
    if activo and not armado:
        desactivar()
        return "Cierro la vista y apago la cámara."
    if armado:
        return "Cierro la vista. La vigilancia sigue armada."
    return "Cierro la vista de la cámara."


def armar(armado: bool = True) -> str:
    """Pasa de VISTA a VIGILANDO y al revés SIN cerrar la cámara (el panel del HUD lo
    usa para armar/desarmar en caliente). Se niega mientras hay un bloqueo en curso:
    desarmar no puede ser jamás un atajo para saltarse la pantalla de contraseña."""
    with _LOCK:
        if not _estado["activo"]:
            return "El modo seguro no está activo: ábrelo primero."
        if _estado["fase"] in ("BLOQUEADO", "GRACIA"):
            return "Hay un bloqueo en curso: desbloquéalo con la contraseña antes."
    return activar(armado=armado)


def intentar_desbloqueo(password: str) -> dict:
    """Llamado desde el hilo HTTP del HUD (POST /seguro/desbloquear), nunca desde el
    hilo vigilante. Verifica la contraseña y, si es correcta, pasa a GRACIA."""
    cfg = _leer_config()
    with _LOCK:
        if _estado["fase"] != "BLOQUEADO":
            return {"ok": False, "mensaje": "No hay nada que desbloquear ahora mismo."}
        if not _verificar_password(password, cfg):
            return {"ok": False, "mensaje": "Contraseña incorrecta."}
        _estado["fase"] = "GRACIA"
        _estado["gracia_hasta_ts"] = time.time() + cfg["segundos_gracia_tras_desbloqueo"]
        _estado["ultimo_evento"] = "desbloqueado con contraseña correcta"
    with _TRACKER_LOCK:
        if _tracker is not None:
            _tracker.reiniciar()   # evita re-disparo inmediato con frames_ok heredado
    _publicar_bus()
    return {"ok": True, "mensaje": "Desbloqueado."}


def _marcar_camara_mal(motivo: str) -> None:
    with _LOCK:
        _estado["camara_ok"] = False
        _estado["motivo_camara"] = motivo
    _publicar_bus()
    try:
        from cristopher import bus
        bus.log("error", f"seguro: cámara no disponible ({motivo})")
    except Exception:
        pass


def _marcar_camara_bien() -> None:
    """Se llama tras CADA frame leído con éxito, así que todo lo que hay aquí dentro
    tiene que ser mudo salvo en la transición mal→bien. Publicar el bus y loguear
    incondicionalmente costaba 4 eventos SSE y 4 líneas de log por segundo, y el deque
    de `bus._log` (tope 200) se vaciaba entero cada ~50s."""
    with _LOCK:
        if _estado["camara_ok"]:
            return
        _estado["camara_ok"] = True
        _estado["motivo_camara"] = ""
    _publicar_bus()
    try:
        from cristopher import bus
        bus.log("info", "seguro: cámara recuperada")
    except Exception:
        pass


# --- Disparo -----------------------------------------------------------------
def _notificar_remoto_si_procede(texto: str) -> None:
    """Fragmento nivel-3 de proactivo.Demonio._entregar, duplicado a propósito (no es
    reutilizable tal cual desde fuera de una instancia de Demonio, y duplicar 4 líneas
    es más simple que inventar una abstracción compartida). A propósito SIN las dos
    llamadas a voz.hablar() del original: anunciar en voz alta delataría al intruso
    que se le ha detectado."""
    try:
        from cristopher.notificar_remoto import notificar
        notificar(texto)
    except Exception as exc:
        try:
            from cristopher import bus
            bus.log("error", f"seguro: notificación remota falló ({exc})")
        except Exception:
            pass


def _escribir_jpg(ruta, imagen) -> bool:
    """cv2.imwrite NO sabe escribir en rutas con caracteres no ASCII en Windows: usa la
    API ANSI y devuelve False en silencio (no lanza), así que un `except` no se entera.
    Ya nos costó las fotos de enrolamiento de «dueño», que se perdieron enteras sin un
    solo mensaje. Se codifica en memoria y escribe Python, que sí habla Unicode."""
    import cv2

    ok, buf = cv2.imencode(".jpg", imagen)
    if not ok:
        return False
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_bytes(buf.tobytes())
    return True


def _guardar_captura(frame_limpio) -> None:
    try:
        # Milisegundos en el nombre: con solo segundos, dos disparos en el mismo
        # segundo se pisaban el archivo.
        marca = time.strftime("%Y%m%d_%H%M%S") + f"_{int(time.time() * 1000) % 1000:03d}"
        if not _escribir_jpg(_CAPTURAS_DIR / f"{marca}.jpg", frame_limpio):
            raise OSError("cv2.imencode no pudo codificar el JPEG")
    except Exception as exc:
        try:
            from cristopher import bus
            bus.log("error", f"seguro: no pude guardar la captura ({exc})")
        except Exception:
            pass


def _disparar(frame_limpio) -> None:
    """La acción de seguridad (bloqueo real) NUNCA se retrasa ni se encola —
    concentracion solo gatea la NOTIFICACIÓN, jamás el bloqueo en sí."""
    with _LOCK:
        if _estado["fase"] == "BLOQUEADO":
            return  # ya se disparó para este episodio; no repetir la transición
        _estado["fase"] = "BLOQUEADO"
        _estado["ultimo_evento"] = "disparo: cara desconocida mirando la pantalla"
    _publicar_bus()

    cfg = _leer_config()

    if cfg.get("bloquear_windows", True):
        try:
            ctypes.windll.user32.LockWorkStation()
        except Exception as exc:
            try:
                from cristopher import bus
                bus.log("error", f"seguro: LockWorkStation falló ({exc})")
            except Exception:
                pass
    try:
        from cristopher import seguro_overlay
        seguro_overlay.lanzar_proceso()
    except Exception as exc:
        try:
            from cristopher import bus
            bus.log("error", f"seguro: no pude lanzar el overlay ({exc})")
        except Exception:
            pass

    # Captura por evento, respetando el cooldown (evita el bug del prototipo: 143
    # imágenes en una sesión guardando una por frame).
    ahora = time.time()
    with _LOCK:
        ultimo = _estado["ultimo_disparo_ts"]
        debe_capturar = cfg.get("guardar_captura_intruso", True) and (
            ultimo is None or (ahora - ultimo) >= cfg["segundos_entre_capturas"]
        )
        if debe_capturar:
            _estado["ultimo_disparo_ts"] = ahora
    if debe_capturar and frame_limpio is not None:
        _guardar_captura(frame_limpio)

    texto = "Modo seguro: cara desconocida mirando la pantalla. Portátil bloqueado."
    try:
        from cristopher import concentracion
        if concentracion.esta_silenciando(3):
            concentracion.encolar_aviso(3, texto)
        else:
            from cristopher import bus
            bus.add_alerta(3, texto)
            _notificar_remoto_si_procede(texto)
    except Exception:
        pass


# --- Identidad (Fase 2): galería + embedder compartidos, frame cacheado ---------
# Singletons perezosos a nivel de módulo — el hilo vigilante y las tools de
# enrolar/olvidar/listar (llamadas desde el hilo del agente) usan la MISMA instancia,
# para que enrolar a alguien surta efecto en la siguiente vuelta del bucle sin
# necesidad de desactivar/reactivar el modo.
_GALERIA_LOCK = threading.Lock()
_galeria = None   # seguro_galeria.Galeria | None

_EMBEDDER_LOCK = threading.Lock()
_embedder = None            # seguro_galeria.Embedder | None
_embedder_fallo_ts = None   # throttle de reintento si falló cargar/descargar

# Último frame limpio + caras detectadas (SIN filtrar por evaluar_cara — el enrolamiento
# usa sus propias puertas de calidad, distintas de los umbrales de seguridad), para que
# seguro_enrolar() pueda capturar "la foto de ahora mismo" sin abrir una segunda cámara
# (la webcam es de uso exclusivo en Windows y el hilo vigilante ya es su único dueño).
_FRAME_LOCK = threading.Lock()
_ultimo_frame = None       # np.ndarray | None
_ultimas_caras: list = []
_ultimo_frame_ts: "float | None" = None


def _obtener_galeria():
    global _galeria
    with _GALERIA_LOCK:
        if _galeria is None:
            from cristopher.seguro_galeria import Galeria
            _galeria = Galeria(_GALERIA_NPZ)
        return _galeria


def _obtener_embedder():
    """Perezoso, con reintento acotado si falla (descarga o carga de la sesión ONNX) —
    nunca reintenta en cada frame a varios fps si la red falla. Devuelve None si no
    hay modelo disponible: el llamador debe tratarlo como DESCONOCIDO (falla hacia la
    seguridad, nunca al revés)."""
    global _embedder, _embedder_fallo_ts
    with _EMBEDDER_LOCK:
        if _embedder is not None:
            return _embedder
        if _embedder_fallo_ts is not None and (time.time() - _embedder_fallo_ts) < _EMBEDDER_REINTENTO_SEGUNDOS:
            return None
        from cristopher import seguro_galeria
        if not seguro_galeria.descargar_modelo_embedding():
            _embedder_fallo_ts = time.time()
            try:
                from cristopher import bus
                bus.log("error", "seguro: no pude descargar el modelo de identidad (AuraFace)")
            except Exception:
                pass
            return None
        try:
            _embedder = seguro_galeria.Embedder(seguro_galeria.AURAFACE_PATH)
            _embedder_fallo_ts = None
            return _embedder
        except Exception as exc:
            _embedder_fallo_ts = time.time()
            try:
                from cristopher import bus
                bus.log("error", f"seguro: no pude cargar el modelo de identidad ({exc})")
            except Exception:
                pass
            return None


# --- Enrolamiento / gestión de personas (Fase 2, tools por chat) -----------------
def _leer_personas_json() -> dict:
    if not _PERSONAS_JSON.exists():
        return {}
    try:
        data = json.loads(_PERSONAS_JSON.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def _escribir_personas_json(data: dict) -> None:
    try:
        _PERSONAS_JSON.parent.mkdir(parents=True, exist_ok=True)
        _PERSONAS_JSON.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass


def _actualizar_personas_json(nombre: str) -> None:
    ahora = datetime.now().isoformat(timespec="seconds")
    data = _leer_personas_json()
    entrada = data.get(nombre, {"creado": ahora, "fotos": 0})
    entrada["fotos"] = int(entrada.get("fotos", 0)) + 1
    entrada["actualizado"] = ahora
    data[nombre] = entrada
    _escribir_personas_json(data)


def _quitar_de_personas_json(nombre: str) -> None:
    data = _leer_personas_json()
    if nombre in data:
        del data[nombre]
        _escribir_personas_json(data)


def _aviso_recalibracion(n_personas_actual: int) -> str:
    if not _CALIBRACION_JSON.exists():
        if n_personas_actual > 0:
            return (
                "Aún no has calibrado los umbrales (scripts/calibrar_seguro.py): "
                "por ahora, ninguna cara se reconoce como autorizada todavía y el "
                "modo se comporta como si nadie estuviera en la lista."
            )
        return ""
    try:
        calib = json.loads(_CALIBRACION_JSON.read_text(encoding="utf-8"))
        tam_calibrado = int(calib.get("tam_galeria", 0))
    except (json.JSONDecodeError, OSError, TypeError, ValueError):
        return ""
    if tam_calibrado > 0 and n_personas_actual >= tam_calibrado * _RECALIBRAR_CRECIMIENTO:
        return (
            f"La galería creció de {tam_calibrado} a {n_personas_actual} personas desde "
            "la última calibración — conviene recalibrar (scripts/calibrar_seguro.py): "
            "cuanta más gente, más probable que un desconocido se parezca a alguien."
        )
    return ""


def _guardar_recorte_enrolamiento(nombre: str, crop, ts: float) -> None:
    try:
        marca = f"{time.strftime('%Y%m%d_%H%M%S')}_{int(ts * 1000) % 1000:03d}"
        # Ojo: la carpeta lleva el NOMBRE de la persona, o sea que puede traer ñ o
        # tildes — ver _escribir_jpg, que es justo el caso que rompía cv2.imwrite.
        if not _escribir_jpg(_ENROLAMIENTO_DIR / nombre / f"{marca}.jpg", crop):
            raise OSError("cv2.imencode no pudo codificar el JPEG")
    except Exception as exc:
        try:
            from cristopher import bus
            bus.log("error", f"seguro: no pude guardar la foto de enrolamiento ({exc})")
        except Exception:
            pass


# --- Pausa del enrolamiento guiado ----------------------------------------------
# Ponerse las gafas o cambiar la luz no cabe en cinco segundos. `set` = pausado.
_pausa_event = threading.Event()
_atajo_pausa = None
_ATAJO_PAUSA_LOCK = threading.Lock()


# Antirrebote del toggle de pausa. Hace falta porque la MISMA pulsación de espacio
# puede llegar por dos caminos: el hook global de pynput (que NO suprime la tecla) y,
# si la ventana del HUD tiene el foco, el keydown del navegador. Dos toggles seguidos
# se anulan y parece que la tecla no hace nada. Cubre además el auto-repeat de Windows
# al mantener la tecla pulsada.
_PAUSA_ANTIRREBOTE_SEGUNDOS = 0.4
_ultimo_toggle_pausa = 0.0


def _alternar_pausa() -> bool:
    """Devuelve el estado (True = pausado). Ignora rebotes: ver _PAUSA_ANTIRREBOTE."""
    global _ultimo_toggle_pausa
    with _LOCK:
        ahora = time.time()
        if ahora - _ultimo_toggle_pausa < _PAUSA_ANTIRREBOTE_SEGUNDOS:
            return _pausa_event.is_set()
        _ultimo_toggle_pausa = ahora
        if _pausa_event.is_set():
            _pausa_event.clear()
            return False
        _pausa_event.set()
        return True


def pausar_enrolamiento(pausar: "bool | None" = None) -> dict:
    """Pausa/reanuda la sesión guiada. Sin argumento, alterna. Lo llaman el atajo
    global (espacio), el botón del panel y la ruta POST /seguro/enrolar/pausa."""
    with _LOCK:
        if _estado.get("enrolamiento") is None:
            return {"ok": False, "pausado": False,
                    "mensaje": "No hay ninguna sesión de enrolamiento en marcha."}
    if pausar is None:
        pausado = _alternar_pausa()
    else:
        pausado = bool(pausar)
        _pausa_event.set() if pausado else _pausa_event.clear()
    return {"ok": True, "pausado": pausado,
            "mensaje": "Sesión en pausa." if pausado else "Sigo."}


def _iniciar_atajo_pausa() -> None:
    """Engancha el ESPACIO como atajo global, y SOLO mientras dura la sesión guiada
    (mismo patrón que control_total/killswitch.py, pero al revés en el ciclo de vida:
    el kill-switch vive siempre porque es Ctrl+F3 y no estorba a nadie; el espacio es
    la tecla más común del teclado y tenerla enganchada de forma permanente sería
    intolerable). Degrada con un log si falta pynput: la sesión sigue, solo que la
    pausa habrá que darla desde el panel del HUD."""
    global _atajo_pausa
    with _ATAJO_PAUSA_LOCK:
        if _atajo_pausa is not None:
            return
        try:
            from pynput import keyboard
        except ImportError:
            try:
                from cristopher import bus
                bus.log("info", "seguro: falta pynput, la pausa del enrolamiento solo "
                                "estará en el botón del HUD")
            except Exception:
                pass
            return
        try:
            hk = keyboard.GlobalHotKeys({"<space>": _alternar_pausa})
            hk.daemon = True
            hk.start()
            _atajo_pausa = hk
        except Exception as exc:
            try:
                from cristopher import bus
                bus.log("error", f"seguro: no pude registrar el espacio como pausa ({exc})")
            except Exception:
                pass


def _parar_atajo_pausa() -> None:
    global _atajo_pausa
    with _ATAJO_PAUSA_LOCK:
        if _atajo_pausa is not None:
            try:
                _atajo_pausa.stop()
            except Exception:
                pass
            _atajo_pausa = None
    _pausa_event.clear()


# voz.hablar() NO es reentrante: usa sd.play()/sd.wait() sobre el stream global único
# de sounddevice, así que dos locuciones a la vez se pisan el stream y la primera revienta
# con PortAudioError (o se lleva el proceso por delante, que es lo que pasó de verdad).
# Este lock serializa; el detener_habla() previo corta lo que sonara para que la
# instrucción NUEVA no tenga que esperar a la vieja — importa, porque la cuenta atrás ya
# está corriendo mientras habla.
_VOZ_LOCK = threading.Lock()


def _hablar_serializado(texto: str) -> None:
    with _VOZ_LOCK:
        try:
            from cristopher import voz
            voz.hablar(texto)
        except Exception as exc:
            # Sin esto, una excepción aquí muere en un hilo daemon imprimiendo un
            # traceback suelto (y bajo pythonw, con stderr a None, peor aún).
            try:
                from cristopher import bus
                bus.log("error", f"seguro: no pude hablar la instrucción ({exc})")
            except Exception:
                pass


def _anunciar(texto: str) -> None:
    """Lo dice en voz alta SIN bloquear (hilo aparte) y lo deja también en el log del
    HUD. La voz aquí no depende del modo voz: el enrolamiento guiado es una interacción
    física — estás de pie delante de la cámara, no mirando la pantalla —, así que si se
    callara no habría instrucción que seguir. Si el TTS no está disponible, se degrada
    al canal visual sin romper la sesión."""
    try:
        from cristopher import bus
        bus.log("info", f"seguro: {texto}")
    except Exception:
        pass
    try:
        from cristopher import voz
        voz.detener_habla()
    except Exception:
        pass
    threading.Thread(target=_hablar_serializado, args=(texto,), daemon=True).start()


def _publicar_enrolamiento(datos: "dict | None") -> None:
    """Estado de la sesión guiada para el panel del HUD (`/seguro/estado`), que lo pinta
    como texto grande. Se escribe MUCHO (una vez por segundo de cuenta atrás), así que
    no publica al bus: el panel ya sondea rápido mientras está abierto."""
    with _LOCK:
        _estado["enrolamiento"] = datos


def _sesion_guiada(nombre: str, obtener_frame, publicar_vista) -> "tuple[int, list[str]]":
    """Recorre POSES_ENROLAMIENTO: anuncia la pose, cuenta atrás, y captura.

    Los dos caminos de cámara (modo activo → frame cacheado del hilo vigilante; modo
    inactivo → cámara propia) solo se diferencian en estas dos funciones, así que la
    coreografía vive escrita UNA vez:
      obtener_frame()  -> (frame, caras)  o (None, [])
      publicar_vista(frame, banner, cara, ok) -> None   (dibuja el preview, si procede)
    """
    from cristopher import seguro_galeria

    cfg = _leer_config()
    segundos = max(1, int(cfg.get("segundos_cuenta_atras", _CUENTA_ATRAS_DEFECTO)))
    embedder = _obtener_embedder()
    if embedder is None:
        return 0, ["no pude cargar el modelo de reconocimiento facial"]

    galeria = _obtener_galeria()
    ctx: dict = {}
    guardadas = 0
    motivos: list[str] = []
    poses = seguro_galeria.POSES_ENROLAMIENTO

    def estado_pose(pose, i, **extra):
        datos = {"nombre": nombre, "pose": pose["id"], "instruccion": pose["instruccion"],
                 "hecho": guardadas, "total": len(poses), "indice": i + 1,
                 "cuenta": None, "motivo": "", "pausado": _pausa_event.is_set()}
        datos.update(extra)
        return datos

    def atender_pausa(pose, i) -> bool:
        """Si está pausado, se queda aquí hasta que se reanude (manteniendo vivo el
        preview) y devuelve True para que la pose se repita desde el principio: quien
        pausa para ponerse las gafas quiere sus cinco segundos otra vez, no reanudar
        en 'dos'."""
        if not _pausa_event.is_set():
            return False
        _anunciar("En pausa. Pulsa el espacio cuando estés.")
        while _pausa_event.is_set():
            _publicar_enrolamiento(estado_pose(pose, i, pausado=True))
            frame, caras = obtener_frame()
            if frame is not None:
                cara = max(caras, key=lambda c: c["altura_px"]) if caras else None
                publicar_vista(frame, "PAUSA - pulsa espacio para seguir", cara, None)
            time.sleep(0.1)
        # A propósito NO se dice "Seguimos" aquí: el llamador lo mete como prefijo de
        # la instrucción siguiente, en UNA sola locución. Dos llamadas seguidas a
        # _anunciar con milisegundos de diferencia era justo lo que rompía el audio.
        return True

    for i, pose in enumerate(poses):
        capturada = False
        ultimo_motivo = "no veo ninguna cara"
        for intento in range(_ENROLAR_INTENTOS_POR_POSE):
            # Bucle de la pose. Una pausa la reinicia ENTERA (cuenta atrás incluida) sin
            # gastar intento: quien pausa para ponerse las gafas quiere sus cinco
            # segundos otra vez, no reanudar en "dos".
            reanudado = False
            while not capturada:
                texto = pose["instruccion"] if intento == 0 else f"Repetimos: {pose['instruccion']}"
                if reanudado:
                    texto = f"Seguimos. {texto}"
                    reanudado = False
                _anunciar(f"{texto}. {', '.join(str(n) for n in range(segundos, 0, -1))}")

                # Cuenta atrás: el reloj lo lleva ESTO, no la voz (la duración del TTS
                # es impredecible y con ElevenLabs depende hasta de la red). La voz va
                # en paralelo; lo que manda es el segundero que ve el usuario.
                pausada = False
                for queda in range(segundos, 0, -1):
                    _publicar_enrolamiento(estado_pose(pose, i, cuenta=queda))
                    fin_seg = time.time() + 1.0
                    while time.time() < fin_seg and not pausada:
                        pausada = _pausa_event.is_set()
                        frame, caras = obtener_frame()
                        if frame is not None:
                            cara = max(caras, key=lambda c: c["altura_px"]) if caras else None
                            publicar_vista(frame, f"{pose['rotulo']}  ...{queda}", cara, None)
                        time.sleep(0.08)
                    if pausada:
                        break
                if pausada:
                    reanudado = atender_pausa(pose, i)
                    continue

                # Ventana de captura: busca el PRIMER frame que pase calidad y pose. Da
                # un margen tras el cero en vez de exigir el instante exacto —
                # parpadear justo entonces no puede costarte la foto.
                _publicar_enrolamiento(estado_pose(pose, i, cuenta=0))
                fin_captura = time.time() + _ENROLAR_VENTANA_CAPTURA_SEGUNDOS
                while time.time() < fin_captura:
                    if _pausa_event.is_set():
                        pausada = True
                        break
                    frame, caras = obtener_frame()
                    if frame is None or not caras:
                        time.sleep(0.08)
                        continue
                    cara = max(caras, key=lambda c: c["altura_px"])
                    crop = embedder.alinear(frame, cara["puntos"])
                    problema = seguro_galeria.calidad_enrolamiento(cara, crop)
                    if problema:
                        ultimo_motivo = problema
                        publicar_vista(frame, f"{pose['rotulo']}  NO", cara, False)
                        time.sleep(0.08)
                        continue
                    ok, motivo = seguro_galeria.comprobar_pose(pose["id"], cara, ctx)
                    if not ok:
                        ultimo_motivo = motivo
                        publicar_vista(frame, f"{pose['rotulo']}  NO", cara, False)
                        time.sleep(0.08)
                        continue

                    emb = embedder.embedding(crop)
                    with _GALERIA_LOCK:
                        galeria.anadir(nombre, emb)
                    _guardar_recorte_enrolamiento(nombre, crop, time.time())
                    _actualizar_personas_json(nombre)
                    seguro_galeria.tras_capturar_pose(pose["id"], cara, ctx)
                    guardadas += 1
                    capturada = True
                    publicar_vista(frame, f"{pose['rotulo']}  OK", cara, True)
                    break

                if pausada:
                    reanudado = atender_pausa(pose, i)
                    continue   # pausar durante la captura tampoco gasta el intento
                break

            if capturada:
                break
            _publicar_enrolamiento(estado_pose(pose, i, motivo=ultimo_motivo))
            if intento + 1 < _ENROLAR_INTENTOS_POR_POSE:
                _anunciar(f"Esa no me vale: {ultimo_motivo}")
                time.sleep(1.0)

        if not capturada:
            motivos.append(f"{pose['instruccion']}: {ultimo_motivo}")
            if pose.get("imprescindible"):
                # La frontal fija las referencias de pitch y tamaño; sin ella las poses
                # que se miden CONTRA ella no se pueden comprobar. Seguir sería recorrer
                # ocho instrucciones más para fallarlas todas.
                _anunciar("Sin la foto de frente no puedo seguir. Revisa la luz y la "
                          "distancia, y volvemos a empezar.")
                break

    _publicar_enrolamiento(None)
    return guardadas, motivos


def _enrolar_con_modo_activo(nombre: str) -> "tuple[int, list[str]]":
    """El modo ya está vigilando: reutiliza la cámara compartida (dueño único, no se
    puede abrir una segunda) vía el frame cacheado, con el disparo SUSPENDIDO durante
    toda la sesión (ver _enrolando_event) — si no, la propia persona que se está
    autorizando dispararía contra sí misma antes de terminar de enrolarse."""
    def obtener_frame():
        with _FRAME_LOCK:
            frame, caras, ts = _ultimo_frame, list(_ultimas_caras), _ultimo_frame_ts
        if frame is None or ts is None or (time.time() - ts) > _ENROLAR_FRESCURA_SEGUNDOS:
            return None, []
        return frame, caras

    def publicar_vista(frame, banner, cara, ok):
        # Aquí NO se dibuja: el hilo vigilante ya publica el preview en su vuelta. Solo
        # se le deja el rótulo para que lo pinte él, y así no hay dos productores de
        # JPEG peleando por _ultimo_preview_jpg.
        _publicar_banner_enrolamiento(banner)

    _enrolando_event.set()
    try:
        return _sesion_guiada(nombre, obtener_frame, publicar_vista)
    finally:
        _enrolando_event.clear()
        _publicar_banner_enrolamiento(None)
        _publicar_enrolamiento(None)


def _enrolar_modo_inactivo(nombre: str) -> "tuple[int, list[str]]":
    """El modo NO está vigilando: abre la cámara ella misma solo para la sesión y la
    libera al terminar. Sin FSM de vigilancia en paralelo no hay ningún disparo posible
    mientras tanto — esta es la vía normal para enrolarse ANTES de activar la protección.
    Publica el preview por su cuenta para que el panel del HUD enseñe la cuenta atrás
    igual que en el otro camino."""
    import cv2

    from cristopher import seguro_vision

    if not _TASK_PATH.exists():
        if not _descargar_modelo_deteccion():
            return 0, ["falta el modelo de detección de rostro (face_landmarker.task)"]

    cfg = _leer_config()
    detector = seguro_vision.Detector(_TASK_PATH, num_caras=1)
    cap = cv2.VideoCapture(cfg["camara_indice"], cv2.CAP_DSHOW)
    if not cap.isOpened():
        cap.release()
        return 0, ["no se pudo abrir la webcam (¿otra app la tiene, Zoom/Teams/Meet?)"]
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
    # Buffer de 1: la camara entrega a ~30fps y aqui se lee mucho mas lento, asi que
    # sin esto cap.read() devuelve el frame MAS VIEJO de la cola y se decide (y se
    # guarda la foto del intruso) sobre imagen retrasada. DSHOW puede ignorarlo, pero
    # pedirlo no cuesta nada y donde se respeta lo arregla del todo.
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

    t0 = time.time()

    def obtener_frame():
        ok, frame = cap.read()
        if not ok:
            return None, []
        # detect_for_video exige timestamps crecientes; el reloj relativo al inicio los
        # da monótonos y las llamadas van separadas >=80ms, así que nunca se repiten.
        return frame, detector.detectar(frame, int((time.time() - t0) * 1000))

    def publicar_vista(frame, banner, cara, ok):
        if not _hay_preview_subs():
            return
        anotaciones = []
        if cara is not None:
            color = _COLOR_AMBAR if ok is None else (_COLOR_VERDE if ok else _COLOR_ROJO)
            anotaciones.append((cara["bbox"], color, ""))
        _publicar_preview(_dibujar_preview(frame, anotaciones, banner))

    # Marca la sesión aunque no haya vigilancia: es lo que permite que /seguro/preview
    # sirva el stream con el modo inactivo, para poder ver la cuenta atrás.
    _enrolando_event.set()
    try:
        return _sesion_guiada(nombre, obtener_frame, publicar_vista)
    finally:
        cap.release()
        detector.cerrar()   # el grafo de MediaPipe no se libera solo
        _enrolando_event.clear()
        _publicar_enrolamiento(None)
        with _PREVIEW_LOCK:
            global _ultimo_preview_jpg
            _ultimo_preview_jpg = None


def enrolar(nombre: str) -> str:
    """Sesión GUIADA de enrolamiento: CRISTOPHER va diciendo en voz alta cómo colocarse
    (de frente, girado a cada lado, barbilla arriba y abajo, cerca, lejos, otra luz, con
    gafas), hace una cuenta atrás antes de cada foto y comprueba que la pose es la que
    pidió antes de darla por buena. Tarda alrededor de un minuto y medio.

    NO hace falta activar el Modo Seguro para enrolar: de hecho lo normal es enrolarse
    primero y activar la protección después. Si el modo ya está vigilando, reutiliza esa
    misma cámara; si no, la abre ella sola mientras dura la sesión."""
    nombre = (nombre or "").strip()
    if not nombre:
        return "No indicaste el nombre de la persona a enrolar."

    if not _SESION_ENROLAMIENTO.acquire(blocking=False):
        return "Ya hay una sesión de enrolamiento en marcha. Espera a que termine."
    try:
        from cristopher import seguro_galeria
        total_poses = len(seguro_galeria.POSES_ENROLAMIENTO)
        _anunciar(f"Vamos a enrolar a {nombre}. Son {total_poses} poses; te voy diciendo "
                  "cómo ponerte y hago una cuenta atrás antes de cada foto. "
                  "Pulsa el espacio si necesitas parar en cualquier momento.")
        time.sleep(1.0)
        _iniciar_atajo_pausa()

        if _activo_event.is_set():
            guardadas, motivos_rechazo = _enrolar_con_modo_activo(nombre)
        else:
            guardadas, motivos_rechazo = _enrolar_modo_inactivo(nombre)
    finally:
        _parar_atajo_pausa()   # el espacio deja de estar enganchado en cuanto acaba
        _SESION_ENROLAMIENTO.release()

    if guardadas == 0:
        detalle = "; ".join(motivos_rechazo[:3]) if motivos_rechazo else "sin detalle"
        _anunciar("No pude guardar ninguna foto.")
        return (
            f"No pude guardar ninguna foto de «{nombre}» ({detalle}). Ponte bien "
            "delante de la cámara, con buena luz, y vuelve a intentarlo."
        )

    with _TRACKER_LOCK:
        if _tracker is not None:
            _tracker.reiniciar()   # reevalúa identidades con la galería ya actualizada

    galeria = _obtener_galeria()
    with _GALERIA_LOCK:
        n_total = dict(galeria.resumen())[nombre]
    _anunciar(f"Listo. Guardé {guardadas} fotos de {nombre}.")

    partes = [f"«{nombre}»: guardé {guardadas}/{total_poses} pose(s) ({n_total} fotos en total)."]
    if motivos_rechazo:
        partes.append("No salieron: " + "; ".join(motivos_rechazo) + ".")
    if n_total >= 5:
        partes.append("Ya hay suficientes para calibrar los umbrales "
                      "(scripts/calibrar_seguro.py), que es lo que falta para que el "
                      "modo reconozca a alguien en vez de tratarlo como desconocido.")
    else:
        partes.append(f"Hacen falta al menos 5 fotos para calibrar; van {n_total}.")
    return " ".join(partes)



def olvidar_persona(nombre: str) -> str:
    """Borra a `nombre` de la galería (embeddings) Y sus fotos de enrolamiento en
    disco. Irreversible: no hay copia de seguridad de lo borrado."""
    nombre = (nombre or "").strip()
    if not nombre:
        return "No indicaste qué persona olvidar."
    galeria = _obtener_galeria()
    with _GALERIA_LOCK:
        ok = galeria.borrar(nombre)
    if not ok:
        return f"No hay ninguna persona autorizada llamada «{nombre}»."
    shutil.rmtree(_ENROLAMIENTO_DIR / nombre, ignore_errors=True)
    _quitar_de_personas_json(nombre)
    with _TRACKER_LOCK:
        if _tracker is not None:
            _tracker.reiniciar()
    return f"«{nombre}» ya no está autorizado: borré sus fotos y su embedding."


def personas_detalle() -> list[dict]:
    """Snapshot combinado de Galeria (nº de fotos) + personas.json (fechas) — lo usa
    tanto seguro_listar_personas() como la ruta GET /seguro/personas del HUD, para
    que el panel del HUD y lo que dice CRISTOPHER por chat sean la MISMA fuente."""
    galeria = _obtener_galeria()
    with _GALERIA_LOCK:
        resumen = dict(galeria.resumen())
    meta = _leer_personas_json()
    salida = []
    for nombre in sorted(resumen):
        m = meta.get(nombre, {})
        salida.append({
            "nombre": nombre,
            "fotos": resumen[nombre],
            "creado": m.get("creado", ""),
            "actualizado": m.get("actualizado", ""),
        })
    return salida


def listar_personas() -> str:
    personas = personas_detalle()
    if not personas:
        return "No hay nadie autorizado todavía en el modo seguro."
    lineas = [f"- {p['nombre']}: {p['fotos']} foto(s)" for p in personas]
    texto = "Personas autorizadas en el modo seguro:\n" + "\n".join(lineas)
    aviso = _aviso_recalibracion(len(personas))
    if aviso:
        texto += f"\n\n{aviso}"
    return texto


# --- Vista de cámara en el HUD (panel de verificación, no la Fase 3 completa) ----
# Stream MJPEG bajo demanda: el hilo vigilante solo dibuja overlay + codifica JPEG
# cuando hay al menos un suscriptor conectado — panel cerrado, cero coste extra.
_PREVIEW_LOCK = threading.Lock()
_preview_subs = 0
_ultimo_preview_jpg: "bytes | None" = None

_COLOR_VERDE = (80, 220, 100)
_COLOR_ROJO = (60, 60, 240)
_COLOR_GRIS = (150, 150, 150)
_COLOR_AMBAR = (60, 210, 240)
_COLOR_BLANCO = (245, 245, 245)


def preview_conectar() -> None:
    global _preview_subs
    with _PREVIEW_LOCK:
        _preview_subs += 1


def preview_desconectar() -> None:
    global _preview_subs
    with _PREVIEW_LOCK:
        _preview_subs = max(0, _preview_subs - 1)


def _hay_preview_subs() -> bool:
    with _PREVIEW_LOCK:
        return _preview_subs > 0


def obtener_preview_jpg() -> "bytes | None":
    with _PREVIEW_LOCK:
        return _ultimo_preview_jpg


def _dibujar_preview(frame, anotaciones: list, banner: "str | None" = None):
    """Copia del frame con cajas+etiqueta por cara y un banner opcional abajo. NUNCA
    dibuja sobre el frame que usan los embeddings — recibe siempre una copia."""
    import cv2

    img = frame.copy()
    for (x1, y1, x2, y2), color, texto in anotaciones:
        cv2.rectangle(img, (x1, y1), (x2, y2), color, 2)
        org = (x1, max(14, y1 - 8))
        cv2.putText(img, texto, org, cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 0), 3, cv2.LINE_AA)
        cv2.putText(img, texto, org, cv2.FONT_HERSHEY_SIMPLEX, 0.45, color, 1, cv2.LINE_AA)
    if banner:
        h, w = img.shape[:2]
        cv2.rectangle(img, (0, h - 26), (w, h), (20, 20, 20), -1)
        cv2.putText(img, banner, (8, h - 7), cv2.FONT_HERSHEY_SIMPLEX, 0.48, _COLOR_BLANCO, 1, cv2.LINE_AA)
    return img


def _publicar_preview(frame_anotado) -> None:
    global _ultimo_preview_jpg
    import cv2

    ok, buf = cv2.imencode(".jpg", frame_anotado, [cv2.IMWRITE_JPEG_QUALITY, 70])
    if ok:
        with _PREVIEW_LOCK:
            _ultimo_preview_jpg = buf.tobytes()


# --- Hilo vigilante — dueño único de VideoCapture --------------------------------
_TRACKER_LOCK = threading.Lock()
_tracker = None   # seguro_vision.Tracker, vive mientras dura el ciclo de vigilancia


def hilo_vigilante() -> None:
    """Arrancado como daemon propio desde hud/__main__.py::main(). Nunca muere, nunca
    calla: cualquier excepción se loguea y se reintenta tras una pausa."""
    while True:
        try:
            _activo_event.wait()
            if not _activo_event.is_set():
                continue
            _ciclo_vigilancia()
        except Exception as exc:
            try:
                from cristopher import bus
                bus.log("error", f"seguro: fallo en el hilo vigilante ({exc})")
            except Exception:
                pass
            time.sleep(5)


def _ciclo_vigilancia() -> None:
    global _tracker, _ultimo_frame, _ultimas_caras, _ultimo_frame_ts, _ultimo_preview_jpg

    import cv2

    from cristopher import seguro_galeria, seguro_vision

    cap = None
    try:
        if not _TASK_PATH.exists():
            if not _descargar_modelo_deteccion():
                _marcar_camara_mal("falta el modelo de detección de rostro (face_landmarker.task)")
                # Sin el modelo no hay nada que hacer: espera a que se desactive.
                _esperar_o_salir(3600)
                return

        cfg = _leer_config()
        firma_cfg = _firma_config()
        detector = seguro_vision.Detector(_TASK_PATH, num_caras=4)
        with _TRACKER_LOCK:
            _tracker = seguro_vision.Tracker()

        # Singleton compartido con enrolar()/olvidar_persona() (hilo del agente): así
        # una foto añadida por chat la ve esta misma instancia en la vuelta siguiente,
        # sin tener que desactivar/reactivar el modo.
        galeria = _obtener_galeria()

        while _activo_event.is_set():
            # Config EN CALIENTE. Antes se leía una sola vez antes del bucle, y eso
            # hacía inútil el caso normal de uso: calibrar con el modo ya activo
            # escribía umbral_alto/umbral_bajo en disco pero el bucle seguía viéndolos
            # a None, así que TODO el mundo quedaba como DESCONOCIDO — la lista blanca
            # entera inerte, sin que nada lo dijera. Igual con seguro_configurar por
            # chat, que respondía "actualizada" sin cambiar nada. Un stat() por frame
            # (4/s) es gratis; solo se reparsea el JSON cuando el fichero cambia de
            # verdad. Ojo: `camara_indice` es la excepción, solo se aplica al ABRIR la
            # cámara — cambiarlo en caliente no reabre la que ya está en marcha.
            firma_ahora = _firma_config()
            if firma_ahora != firma_cfg:
                firma_cfg = firma_ahora
                cfg = _leer_config()
                try:
                    from cristopher import bus
                    bus.log("info", "seguro: configuración recargada en caliente")
                except Exception:
                    pass

            if cap is None or not cap.isOpened():
                cap = cv2.VideoCapture(cfg["camara_indice"], cv2.CAP_DSHOW)
                if not cap.isOpened():
                    _marcar_camara_mal("no se pudo abrir la webcam (¿otra app la tiene, "
                                        "Zoom/Teams/Meet?)")
                    cap.release()
                    cap = None
                    _esperar_o_salir(_REINTENTO_CAMARA_SEGUNDOS)
                    continue
                _marcar_camara_bien()
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
                cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)   # ver nota en _enrolar_modo_inactivo

            ok, frame = cap.read()
            if not ok:
                _marcar_camara_mal("se perdió la señal de la webcam")
                cap.release()
                cap = None
                _esperar_o_salir(_REINTENTO_CAMARA_SEGUNDOS)
                continue
            _marcar_camara_bien()

            with _LOCK:
                fase = _estado["fase"]
                armado = _estado["armado"]
                gracia_hasta = _estado["gracia_hasta_ts"]

            if fase == "GRACIA" and gracia_hasta is not None and time.time() >= gracia_hasta:
                with _LOCK:
                    _estado["fase"] = "VIGILANDO"
                    _estado["gracia_hasta_ts"] = None
                    _estado["ultimo_evento"] = "fin de gracia, vigilando de nuevo"
                _publicar_bus()
                fase = "VIGILANDO"

            if fase in ("BLOQUEADO", "GRACIA"):
                # Cámara caliente (evita el coste de reabrir), pero sin gastar
                # detección: la transición la dispara intentar_desbloqueo(), no este
                # bucle.
                if _hay_preview_subs():
                    if fase == "BLOQUEADO":
                        banner = "BLOQUEADO — esperando contraseña"
                    else:
                        with _LOCK:
                            restante = _estado.get("gracia_hasta_ts")
                        segs = max(0, int(restante - time.time())) if restante else 0
                        banner = f"EN GRACIA — {segs}s restantes"
                    _publicar_preview(_dibujar_preview(frame, [], banner))
                with _LOCK:
                    _estado["deteccion"] = []   # no se detecta nada en estas fases
                _esperar_frame(cfg["fps_vigilancia"])
                continue

            frame_limpio = frame.copy()   # los recortes/embeddings SIEMPRE de una
                                           # copia limpia, nunca del frame con dibujos
            ts_ms = int(time.time() * 1000)
            caras = detector.detectar(
                frame, ts_ms,
                signo_mirada=cfg["signo_mirada"], usar_mirada=cfg["usar_mirada"],
            )
            # Frame + caras SIN filtrar (antes de evaluar_cara) para que
            # seguro_enrolar() pueda capturar "la foto de ahora mismo" sin abrir una
            # segunda cámara — el enrolamiento usa sus propias puertas de calidad,
            # independientes de los umbrales de seguridad de más abajo.
            with _FRAME_LOCK:
                _ultimo_frame = frame_limpio
                _ultimas_caras = caras
                _ultimo_frame_ts = time.time()

            with _TRACKER_LOCK:
                pares = _tracker.actualizar(caras)

            if _enrolando_event.is_set():
                # seguro_enrolar() está en curso con el modo ya activo: sigue
                # detectando (para que tenga frames frescos) pero SUSPENDE el
                # disparo — si no, la propia persona que se está autorizando
                # dispararía contra sí misma antes de terminar de enrolarse.
                for _tid, track in pares:
                    track["frames_ok"] = 0
                if _hay_preview_subs():
                    # El rótulo lo pone la sesión guiada (pose + cuenta atrás); si no hay
                    # ninguna en marcha, cae al genérico.
                    banner = _obtener_banner_enrolamiento() or "ENROLANDO..."
                    anotaciones = [
                        (cara["bbox"], _COLOR_GRIS, "") for cara in caras
                    ]
                    _publicar_preview(_dibujar_preview(frame_limpio, anotaciones, banner))
                with _LOCK:
                    _estado["deteccion"] = [
                        {"estado": "enrolando", "texto": "enrolando...",
                         "altura_px": c["altura_px"], "mirada": round(c["yaw_mirada"], 1),
                         "sim": None, "frames_ok": 0, "tope": cfg["frames_para_disparar"]}
                        for c in caras
                    ]
                _esperar_frame(cfg["fps_vigilancia"])
                continue

            anotaciones = []   # (bbox, color, texto) — barato, se construye siempre;
                                # el dibujo/JPEG real solo corre si hay suscriptores.
            deteccion = []     # lo mismo en texto, para el panel del HUD
            tope = cfg["frames_para_disparar"]

            def _anotar(cara, color, texto, estado, track=None):
                anotaciones.append((cara["bbox"], color, texto))
                deteccion.append({
                    "estado": estado,             # descartado|identificando|autorizado|desconocido
                    "texto": texto,
                    "altura_px": cara["altura_px"],
                    "mirada": round(cara["yaw_mirada"], 1),
                    "sim": round(track["sim"], 3) if track else None,
                    "frames_ok": track["frames_ok"] if track else 0,
                    "tope": tope,
                })

            for (_tid, track), cara in zip(pares, caras):
                veredicto = seguro_vision.evaluar_cara(cara, cfg)
                if not veredicto["pasa"]:
                    track["frames_ok"] = 0
                    _anotar(cara, _COLOR_GRIS, veredicto["motivo"], "descartado", track)
                    continue

                if track["identidad"] is None:
                    if galeria.vacia():
                        # Nadie enrolado todavía: mismo comportamiento que Fase 1.
                        track["identidad"] = "DESCONOCIDO"
                    elif cfg["umbral_alto"] is None or cfg["umbral_bajo"] is None:
                        # Galería con gente pero sin calibrar (scripts/calibrar_seguro.py
                        # no se ha corrido): ante la duda, DESCONOCIDO — nunca dejar
                        # pasar sin comprobar solo porque falte el dato.
                        track["identidad"] = "DESCONOCIDO"
                    else:
                        embedder = _obtener_embedder()
                        crop = embedder.alinear(frame_limpio, cara["puntos"]) if embedder else None
                        if crop is None:
                            # Sin modelo disponible o no se pudo alinear: falla hacia
                            # la seguridad, nunca al revés.
                            track["identidad"] = "DESCONOCIDO"
                        else:
                            emb = embedder.embedding(crop)
                            with _GALERIA_LOCK:
                                seguro_galeria.decidir_identidad(track, galeria, emb, cfg)

                if track["identidad"] is None:
                    _anotar(cara, _COLOR_AMBAR, "identificando...", "identificando", track)
                elif track["identidad"] == "DESCONOCIDO":
                    # En VISTA el contador sube igual pero se queda clavado en el tope:
                    # así el panel enseña "6/6 · dispararía" (que es la información que
                    # hace falta para calibrar) sin crecer hasta números absurdos.
                    if armado or track["frames_ok"] < tope:
                        track["frames_ok"] += 1
                    etiqueta = f"DESCONOCIDO ({track['frames_ok']}/{tope})"
                    if not armado and track["frames_ok"] >= tope:
                        etiqueta = f"DESCONOCIDO — dispararía ({tope}/{tope})"
                    _anotar(cara, _COLOR_ROJO, etiqueta, "desconocido", track)
                    if armado and track["frames_ok"] >= tope:
                        _disparar(frame_limpio)
                        break
                else:
                    track["frames_ok"] = 0
                    _anotar(cara, _COLOR_VERDE,
                            f"{track['identidad']} ({track['sim']:.2f})", "autorizado", track)

            with _LOCK:
                _estado["deteccion"] = deteccion

            if _hay_preview_subs():
                banner = None if armado else "VISTA — no bloqueo nada"
                _publicar_preview(_dibujar_preview(frame_limpio, anotaciones, banner))

            _esperar_frame(cfg["fps_vigilancia"])
    finally:
        if cap is not None:
            cap.release()
        detector.cerrar()   # una activacion del modo = un FaceLandmarker que cerrar
        with _TRACKER_LOCK:
            _tracker = None
        with _FRAME_LOCK:
            # No tiene sentido enrolar contra un frame de una sesión ya cerrada.
            _ultimo_frame = None
            _ultimas_caras = []
            _ultimo_frame_ts = None
        with _PREVIEW_LOCK:
            # Un panel abierto tras desactivar no debe seguir mostrando el último
            # frame congelado.
            _ultimo_preview_jpg = None
        with _LOCK:
            _estado["fase"] = "INACTIVO"
            _estado["armado"] = False
            _estado["deteccion"] = []
            _estado["gracia_hasta_ts"] = None   # si no, al reactivar reporta una
                                                # gracia fantasma ya vencida
            _estado["camara_ok"] = True
            _estado["motivo_camara"] = ""
            _estado["ultimo_evento"] = "cámara liberada"
        _publicar_bus()


def _esperar_frame(fps: float) -> None:
    time.sleep(max(0.05, 1.0 / max(0.1, fps)))


def _esperar_o_salir(segundos: float) -> None:
    """Duerme en trozos cortos para poder salir pronto si se desactiva mientras la
    cámara está en reintento."""
    fin = time.time() + segundos
    while _activo_event.is_set() and time.time() < fin:
        time.sleep(0.5)


def _descargar_modelo_deteccion() -> bool:
    import shutil
    import urllib.request

    _MODELOS_DIR.mkdir(parents=True, exist_ok=True)
    tmp = _TASK_PATH.with_suffix(_TASK_PATH.suffix + ".parcial")
    try:
        urllib.request.urlretrieve(_TASK_URL, tmp)
        shutil.move(str(tmp), str(_TASK_PATH))
        return True
    except Exception:
        tmp.unlink(missing_ok=True)
        return False
