"""Modo Seguro: identidad. Sin cámara, sin hilos.

`Galeria` está completa desde Fase 1 (numpy + json puro). `Embedder` (Fase 2) carga
AuraFace-v1 vía onnxruntime y alinea/embebe caras para comparar contra la galería —
descarga su propio modelo ONNX (~250MB), autocontenida en este módulo igual que
`seguro.py` descarga el modelo de detección de MediaPipe.
"""

from __future__ import annotations

import shutil
import urllib.request
from pathlib import Path

import numpy as np

from cristopher.config import DATA

# Duplicado a propósito de seguro.py::_MODELOS_DIR — mismo criterio ya usado con los
# helpers de PID-guard: este módulo es dueño de su propio modelo, no importa rutas de
# otro módulo de la misma feature.
_MODELOS_DIR = DATA / "seguro" / "modelos"
AURAFACE_PATH = _MODELOS_DIR / "auraface_glintr100.onnx"
# AuraFace-v1: ArcFace r100 con pesos de licencia permisiva (ver decisión en el .md de
# diseño: InsightFace buffalo_l/antelopev2 tienen pesos solo-investigación, descartados
# porque este repo es público).
AURAFACE_URL = "https://huggingface.co/fal/AuraFace-v1/resolve/main/glintr100.onnx"


def descargar_modelo_embedding() -> bool:
    """Descarga AuraFace a AURAFACE_PATH. Nunca pregunta interactivamente (esto corre
    dentro de un hilo daemon de fondo, no de una consola) — silenciosa pero nunca
    silenciosa sobre el FALLO: el llamador (seguro.py::_obtener_embedder) loguea si
    devuelve False."""
    if AURAFACE_PATH.exists() and AURAFACE_PATH.stat().st_size > 0:
        return True
    _MODELOS_DIR.mkdir(parents=True, exist_ok=True)
    tmp = AURAFACE_PATH.with_suffix(AURAFACE_PATH.suffix + ".parcial")
    try:
        urllib.request.urlretrieve(AURAFACE_URL, tmp)
        shutil.move(str(tmp), str(AURAFACE_PATH))
        return True
    except Exception:
        tmp.unlink(missing_ok=True)
        return False


class Embedder:
    """AuraFace-v1 (ArcFace r100) vía onnxruntime, CPU-only."""

    def __init__(self, onnx_path: Path):
        import onnxruntime as ort
        self._sesion = ort.InferenceSession(str(onnx_path), providers=["CPUExecutionProvider"])
        self._entrada = self._sesion.get_inputs()[0].name

    def alinear(self, frame_bgr: np.ndarray, puntos: np.ndarray) -> "np.ndarray | None":
        """Alinea la cara a un recorte 112x112 vía los 5 puntos de referencia ArcFace
        (seguro_vision.cinco_puntos + REF_5PTS)."""
        import cv2
        from cristopher.seguro_vision import REF_5PTS, cinco_puntos

        src = cinco_puntos(puntos)
        if src is None:
            return None
        matriz, _ = cv2.estimateAffinePartial2D(src, REF_5PTS, method=cv2.LMEDS)
        if matriz is None:
            return None
        return cv2.warpAffine(frame_bgr, matriz, (112, 112), borderValue=0)

    def embedding(self, crop_112: np.ndarray) -> np.ndarray:
        """Vector 512-d L2-normalizado a partir de un recorte 112x112 ya alineado."""
        import cv2

        x = cv2.cvtColor(crop_112, cv2.COLOR_BGR2RGB).astype(np.float32)
        x = (x - 127.5) / 127.5
        x = np.transpose(x, (2, 0, 1))[None, ...]
        vec = self._sesion.run(None, {self._entrada: x})[0][0]
        n = np.linalg.norm(vec)
        return vec / n if n > 0 else vec


def calidad_enrolamiento(cara: dict, crop: "np.ndarray | None") -> "str | None":
    """Puertas de calidad de ENROLAMIENTO — distintas de los umbrales de seguridad de
    seguro_vision.evaluar_cara. Diez fotos variadas con un modelo mediocre baten a una
    foto perfecta con el mejor modelo del mundo: rechazar aquí importa más que el
    modelo en sí."""
    if cara["altura_px"] < 100:
        return f"cara demasiado pequeña ({cara['altura_px']}px, mínimo 100). Acércate."
    if abs(cara["yaw"]) > 30:
        return f"cara demasiado girada (yaw {cara['yaw']:.0f}°, máximo 30)."
    if crop is None:
        return "no se pudo alinear la cara (landmarks insuficientes)."
    import cv2
    nitidez = cv2.Laplacian(cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY), cv2.CV_64F).var()
    if nitidez < 40:
        return f"foto borrosa (nitidez {nitidez:.0f}, mínimo 40). Quédate quieto."
    return None


# --- Guion de poses del enrolamiento guiado -------------------------------------
# Fuente ÚNICA del guion: lo usan tanto `seguro.enrolar()` (la tool de chat, que lo
# narra en voz alta con cuenta atrás) como `scripts/enrolar_guiado.py` (la versión de
# consola, con ventana de vídeo). El repo ya fija que el panel y las tools llaman a las
# MISMAS funciones; esto es lo mismo aplicado al guion.
#
# Qué mueve el embedding, y por eso qué se pide: yaw (mucho, pero ACOTADO — arriba,
# calidad_enrolamiento rechaza por encima de 30°, así que se piden giros de conversación
# y nunca perfiles), pitch (crítico cuando la webcam está por encima de la línea de
# ojos), gafas (la mayor variación intra-persona que existe si se usan a ratos), luz
# (día contra lámpara de noche son dos caras distintas para el modelo) y distancia
# (cambia la resolución efectiva del recorte 112x112). La expresión aporta poco: no se
# gasta una foto en ella.
#
# `instruccion` es lo que se dice/escribe (español de verdad); `rotulo`/`ayuda` es lo
# que va a la ventana de OpenCV, cuyas fuentes Hershey no tienen tildes ni ñ.
POSES_ENROLAMIENTO: list[dict] = [
    # `imprescindible`: fija las referencias (pitch y tamaño) contra las que se miden
    # las demás. Si esta no sale, seguir no tiene sentido — ver _sesion_guiada.
    {"id": "frontal", "imprescindible": True,
     "instruccion": "Ponte de frente, mirando a la cámara",
     "rotulo": "DE FRENTE, cara neutra",
     "ayuda": "Mira a la camara con tu luz normal. Esta foto fija la referencia."},
    {"id": "lado_a",
     "instruccion": "Gira la cabeza hacia un lado, el que quieras",
     "rotulo": "GIRA la cabeza a UN lado",
     "ayuda": "Sin pasarte: un giro comodo, de conversacion."},
    {"id": "lado_b",
     "instruccion": "Ahora gira hacia el otro lado",
     "rotulo": "Ahora al OTRO lado",
     "ayuda": "Mismo giro, direccion contraria."},
    {"id": "pitch_a",
     "instruccion": "Sube un poco la barbilla",
     "rotulo": "SUBE un poco la barbilla",
     "ayuda": "Como si miraras por encima de la pantalla."},
    {"id": "pitch_b",
     "instruccion": "Ahora baja un poco la barbilla",
     "rotulo": "BAJA un poco la barbilla",
     "ayuda": "Como si miraras el teclado, sin dejar de ver la camara."},
    {"id": "cerca",
     "instruccion": "Acércate a la cámara",
     "rotulo": "ACERCATE a la camara",
     "ayuda": "Un palmo mas cerca de lo normal."},
    {"id": "lejos",
     "instruccion": "Échate hacia atrás",
     "rotulo": "ECHATE ATRAS",
     "ayuda": "Como cuando te reclinas en la silla."},
    {"id": "luz",
     "instruccion": "Cambia la luz: enciende o apaga una lámpara, y vuelve a ponerte de frente",
     "rotulo": "CAMBIA LA LUZ",
     "ayuda": "Otra lampara u otra orientacion. Vuelve de frente."},
    {"id": "gafas",
     "instruccion": "Ponte las gafas, o quítatelas si las llevas puestas",
     "rotulo": "CON GAFAS (o sin, si las llevas)",
     "ayuda": "Si no usas gafas nunca, esta pose se salta sola."},
]


def _signo(x: float) -> int:
    return 1 if x >= 0 else -1


def comprobar_pose(pose_id: str, cara: dict, ctx: dict) -> "tuple[bool, str]":
    """¿Está la persona colocada como se le pidió? Devuelve (ok, motivo).

    Los umbrales son RELATIVOS a la foto frontal, no absolutos: el pitch en reposo
    depende de dónde esté físicamente la webcam (aquí, por encima de los ojos, da 26-32°
    con la cara de frente), así que exigir un valor absoluto no serviría en otra máquina.

    Y los signos de los ejes NO se asumen — el repo ya avisa de que las convenciones se
    documentan mal: la primera pose de giro guarda el signo que salga, y la segunda solo
    exige que sea el CONTRARIO. Así funciona con cualquier convención."""
    yaw, pitch, alt = cara["yaw"], cara["pitch"], cara["altura_px"]

    if pose_id == "frontal":
        if abs(yaw) > 12:
            return False, f"ponte más de frente (yaw {yaw:+.0f}, máximo 12)"
        return True, "listo"

    if pose_id in ("lado_a", "lado_b"):
        if abs(yaw) < 12:
            return False, f"gira más la cabeza (yaw {yaw:+.0f}, hacen falta 12)"
        if abs(yaw) > 28:
            return False, f"te has pasado de giro (yaw {yaw:+.0f}, máximo 28)"
        if pose_id == "lado_b" and ctx.get("signo_yaw") and _signo(yaw) == ctx["signo_yaw"]:
            return False, "ese es el mismo lado de antes: gira al contrario"
        return True, "listo"

    if pose_id in ("pitch_a", "pitch_b"):
        base = ctx.get("pitch0")
        if base is None:
            # Sin la frontal no hay contra qué medir. Dar la pose por buena aquí sería
            # guardar una foto cualquiera haciéndola pasar por "barbilla arriba": seis
            # frontales casi idénticas disfrazadas de variedad. Mejor decirlo.
            return False, "me falta la foto de frente de referencia: hay que repetirla"
        d = pitch - base
        if abs(d) < 8:
            return False, f"inclina más la cabeza (llevas {d:+.0f}°, hacen falta 8)"
        if abs(d) > 32:
            return False, f"demasiada inclinación ({d:+.0f}°): la cara se deforma"
        if pose_id == "pitch_b" and ctx.get("signo_pitch") and _signo(d) == ctx["signo_pitch"]:
            return False, "esa es la misma inclinación de antes: al contrario"
        return True, "listo"

    if pose_id in ("cerca", "lejos"):
        base = ctx.get("altura0")
        if base is None:
            return False, "me falta la foto de frente de referencia: hay que repetirla"
        if pose_id == "cerca" and alt < base * 1.2:
            return False, f"acércate más ({alt}px, hacen falta {int(base * 1.2)})"
        if pose_id == "lejos" and alt > base * 0.85:
            return False, f"échate más atrás ({alt}px, hacen falta menos de {int(base * 0.85)})"
        return True, "listo"

    return True, "listo"   # luz y gafas: sin geometría, solo las puertas de calidad


def tras_capturar_pose(pose_id: str, cara: dict, ctx: dict) -> None:
    """La frontal fija las referencias; las primeras de cada eje fijan el signo."""
    if pose_id == "frontal":
        ctx["pitch0"] = cara["pitch"]
        ctx["altura0"] = cara["altura_px"]
    elif pose_id == "lado_a":
        ctx["signo_yaw"] = _signo(cara["yaw"])
    elif pose_id == "pitch_a" and ctx.get("pitch0") is not None:
        ctx["signo_pitch"] = _signo(cara["pitch"] - ctx["pitch0"])


class Galeria:
    """Lista BLANCA. Comparación por MÁXIMO sobre las muestras, nunca por la media: el
    promedio de 'con gafas' y 'sin gafas' es un vector que no se parece a ninguno de
    los dos."""

    def __init__(self, ruta: Path):
        self._ruta = ruta
        self._personas: dict[str, np.ndarray] = {}
        self.cargar()

    def cargar(self):
        if self._ruta.exists():
            try:
                z = np.load(self._ruta, allow_pickle=False)
                self._personas = {k: z[k] for k in z.files}
            except Exception:
                self._personas = {}

    def guardar(self):
        self._ruta.parent.mkdir(parents=True, exist_ok=True)
        np.savez(self._ruta, **self._personas)

    def anadir(self, nombre: str, emb: np.ndarray):
        if nombre in self._personas:
            self._personas[nombre] = np.vstack([self._personas[nombre], emb[None, :]])
        else:
            self._personas[nombre] = emb[None, :]
        self.guardar()

    def borrar(self, nombre: str) -> bool:
        if nombre not in self._personas:
            return False
        del self._personas[nombre]
        self.guardar()
        return True

    def quitar_ultima(self, nombre: str) -> bool:
        """Deshace la última muestra añadida a `nombre` (la tecla 'R' del enrolamiento
        guiado: "esa foto ha salido mal, repítela"). Distinto de borrar(), que se lleva
        a la persona entera. Si era la única, la persona desaparece."""
        m = self._personas.get(nombre)
        if m is None or m.shape[0] == 0:
            return False
        if m.shape[0] == 1:
            del self._personas[nombre]
        else:
            self._personas[nombre] = m[:-1]
        self.guardar()
        return True

    def vacia(self) -> bool:
        return not self._personas

    def resumen(self) -> list[tuple[str, int]]:
        return [(k, int(v.shape[0])) for k, v in self._personas.items()]

    def muestras(self, nombre: str) -> "np.ndarray | None":
        """Array (N, 512) de embeddings de una persona, o None si no existe. Para
        cálculos que necesitan las muestras crudas (p. ej. hold-one-out en
        scripts/calibrar_seguro.py), no solo la comparación por máximo de buscar()."""
        return self._personas.get(nombre)

    def buscar(self, emb: np.ndarray) -> "tuple[str | None, float]":
        mejor_nombre, mejor_sim = None, -1.0
        for nombre, muestras in self._personas.items():
            sim = float(np.max(muestras @ emb))   # coseno: todo está L2-normalizado
            if sim > mejor_sim:
                mejor_nombre, mejor_sim = nombre, sim
        return mejor_nombre, mejor_sim

    def matriz_parecidos(self, t_alto: float) -> list[str]:
        """Pares de personas de la galería que se parecen entre sí (hermanos, etc.).
        Es información que hay que VER, no un detalle interno (Fase 2/3)."""
        nombres = list(self._personas)
        lineas = []
        for i, a in enumerate(nombres):
            for b in nombres[i + 1:]:
                sim = float(np.max(self._personas[a] @ self._personas[b].T))
                marca = "  <-- OJO, se confundirán" if sim >= t_alto else ""
                lineas.append(f"  {a} vs {b}: {sim:.3f}{marca}")
        return lineas


def decidir_identidad(track: dict, galeria: Galeria, emb: np.ndarray, cfg: dict) -> None:
    """Doble umbral + votación. Ante la duda persistente: DESCONOCIDO (lista blanca).

    Requiere `cfg["umbral_alto"]`/`cfg["umbral_bajo"]` ya calibrados (no None) — el
    llamador (seguro.py::_ciclo_vigilancia) lo garantiza antes de invocar esta función."""
    nombre, sim = galeria.buscar(emb)
    track["sim"] = sim
    if sim >= cfg["umbral_alto"]:
        track["identidad"] = nombre
        return
    if sim <= cfg["umbral_bajo"]:
        track["identidad"] = "DESCONOCIDO"
        return
    track["votos"].append((nombre, sim))
    if len(track["votos"]) >= cfg["max_votos"]:
        medio = (cfg["umbral_alto"] + cfg["umbral_bajo"]) / 2
        buenos = [n for n, s in track["votos"] if s >= medio]
        track["identidad"] = max(set(buenos), key=buenos.count) if buenos else "DESCONOCIDO"
