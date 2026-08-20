#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Prototipo del MODO SEGURO de CRISTOPHER — banco de pruebas, no producto.

Qué hace:
  - Abre tu webcam y detecta caras (MediaPipe Face Landmarker).
  - Calcula pose de cabeza (yaw/pitch/roll) y tamaño de cara en px.
  - Aplica los filtros del diseño (distancia + orientación + persistencia) y te
    ENSEÑA por qué descarta cada cara, en vez de esconderlo.
  - Sigue cada cara entre frames (tracker IoU) para no recalcular el embedding.
  - Reconoce identidad contra una galería local (lista blanca) que enrolas tú
    pulsando una tecla.
  - Te dice "DISPARARÍA" cuando el modo real bloquearía. NO bloquea nada.

Qué NO hace (a propósito):
  - No bloquea el PC, no pide contraseña, no tiene interfaz bonita.
  - No cifra nada. Todo va a ./proto_seguro_data/.

Uso:
    pip install -r requirements-prototipo.txt
    python prototipo_seguro.py

Teclas (con la ventana de vídeo enfocada):
    q / ESC   salir
    e         enrolar la cara más grande (pide el nombre en la consola)
    l         listar personas de la galería
    x         borrar una persona
    g         guardar el frame actual como jpg
    m         mostrar/ocultar la malla de landmarks
    1 / 2     min_altura_cara_px  -5 / +5
    3 / 4     yaw_max_grados      -5 / +5
    5 / 6     T_alto              -0.01 / +0.01
    7 / 8     T_bajo              -0.01 / +0.01
    r         reiniciar los tracks (olvida identidades cacheadas)
"""

from __future__ import annotations

import json
import shutil
import sys
import time
import urllib.request
from collections import deque
from pathlib import Path

import cv2
import numpy as np

# --------------------------------------------------------------------------------
# Rutas y config (todo mutable en vivo con las teclas: el objetivo es calibrar a ojo
# antes de calibrar en serio con LFW)
# --------------------------------------------------------------------------------
RAIZ = Path(__file__).resolve().parent
DATA = RAIZ / "proto_seguro_data"
MODELOS = DATA / "modelos"
FOTOS = DATA / "enrolamiento"
CAPTURAS = DATA / "capturas"
GALERIA_NPZ = DATA / "galeria.npz"

TASK_URL = ("https://storage.googleapis.com/mediapipe-models/face_landmarker/"
            "face_landmarker/float16/1/face_landmarker.task")
TASK_PATH = MODELOS / "face_landmarker.task"

# AuraFace-v1: ArcFace r100 con pesos de licencia permisiva (~250 MB, descarga única).
AURAFACE_URL = "https://huggingface.co/fal/AuraFace-v1/resolve/main/glintr100.onnx"
AURAFACE_PATH = MODELOS / "auraface_glintr100.onnx"

CFG = {
    "min_altura_cara_px": 90,   # proxy de distancia: ¿está lo bastante cerca para LEER?
    "yaw_max_grados": 35.0,     # ¿la cabeza apunta a la pantalla?
    "pitch_max_grados": 25.0,
    "frames_para_disparar": 6,  # persistencia (~1,5 s a 4 fps)
    "t_alto": 0.36,             # >= esto -> autorizado          (A OJO, sin calibrar)
    "t_bajo": 0.28,             # <= esto -> desconocido         (A OJO, sin calibrar)
    "max_votos": 5,
    "num_caras": 4,
    "usar_mirada": True,   # sumar la desviación de los OJOS al yaw de la CABEZA
    "signo_mirada": 1,     # si la corrección va al revés, ponlo a -1 (tecla 'b')
}

# Desviación máxima de los ojos dentro de la órbita, en grados, cuando el blendshape
# correspondiente vale 1.0. Aproximación: el rango fisiológico cómodo es ~±35°.
GRADOS_MIRADA_MAX = 35.0

# Plantilla de referencia ArcFace para recortes de 112x112.
REF_5PTS = np.array([
    [38.2946, 51.6963],   # ojo izquierdo
    [73.5318, 51.5014],   # ojo derecho
    [56.0252, 71.7366],   # nariz
    [41.5493, 92.3655],   # comisura izquierda
    [70.7299, 92.2041],   # comisura derecha
], dtype=np.float32)

# Índices de MediaPipe. Con iris (478 landmarks) los centros de ojo son directos; sin
# iris (468) hay que promediar las esquinas. VERIFÍCALOS con la tecla 'm': si izquierda
# y derecha están espejadas, la alineación degrada en silencio.
IDX_IRIS_IZQ, IDX_IRIS_DER = 468, 473
IDX_OJO_IZQ = (33, 133)     # esquinas externa e interna del ojo izquierdo
IDX_OJO_DER = (362, 263)
IDX_NARIZ = 1
IDX_BOCA_IZQ, IDX_BOCA_DER = 61, 291


def cinco_puntos(puntos: np.ndarray) -> np.ndarray | None:
    """Los 5 puntos que espera ArcFace: ojo izq, ojo der, nariz, comisura izq, der.

    Degrada si el modelo no trae landmarks de iris (478 vs 468) en vez de reventar con
    un IndexError — que es justo lo que hacía la primera versión de este prototipo.
    """
    n = len(puntos)
    if n > max(IDX_IRIS_IZQ, IDX_IRIS_DER):
        ojo_izq = puntos[IDX_IRIS_IZQ]
        ojo_der = puntos[IDX_IRIS_DER]
    elif n > max(*IDX_OJO_IZQ, *IDX_OJO_DER, IDX_BOCA_DER):
        ojo_izq = puntos[list(IDX_OJO_IZQ)].mean(axis=0)
        ojo_der = puntos[list(IDX_OJO_DER)].mean(axis=0)
    else:
        return None
    return np.array([
        ojo_izq, ojo_der, puntos[IDX_NARIZ],
        puntos[IDX_BOCA_IZQ], puntos[IDX_BOCA_DER],
    ], dtype=np.float32)

VERDE = (80, 220, 100)
ROJO = (60, 60, 240)
GRIS = (150, 150, 150)
AMARILLO = (60, 210, 240)
BLANCO = (245, 245, 245)


# --------------------------------------------------------------------------------
# Descarga de modelos
# --------------------------------------------------------------------------------
def descargar(url: str, destino: Path, etiqueta: str) -> bool:
    """Descarga con barra de progreso. Devuelve False y explica el fallo, sin lanzar:
    el prototipo debe poder seguir en modo solo-detección."""
    if destino.exists() and destino.stat().st_size > 0:
        return True
    destino.parent.mkdir(parents=True, exist_ok=True)
    tmp = destino.with_suffix(destino.suffix + ".parcial")
    print(f"[modelos] Descargando {etiqueta}...")
    try:
        def progreso(bloques, tam_bloque, total):
            if total <= 0:
                return
            pct = min(100, bloques * tam_bloque * 100 // total)
            print(f"\r[modelos] {etiqueta}: {pct}%", end="", flush=True)

        urllib.request.urlretrieve(url, tmp, reporthook=progreso)
        print()
        shutil.move(str(tmp), str(destino))
        return True
    except Exception as exc:
        print(f"\n[modelos] FALLÓ la descarga de {etiqueta}: {exc}")
        print(f"[modelos] Descárgalo a mano desde:\n    {url}")
        print(f"[modelos] y guárdalo en:\n    {destino}")
        tmp.unlink(missing_ok=True)
        return False


# --------------------------------------------------------------------------------
# Detector: MediaPipe Face Landmarker
# --------------------------------------------------------------------------------
class Detector:
    def __init__(self, task_path: Path, num_caras: int):
        import mediapipe as mp
        from mediapipe.tasks import python as mp_python
        from mediapipe.tasks.python import vision

        self._mp = mp
        opciones = vision.FaceLandmarkerOptions(
            base_options=mp_python.BaseOptions(model_asset_path=str(task_path)),
            running_mode=vision.RunningMode.VIDEO,
            num_faces=num_caras,
            output_face_blendshapes=True,
            output_facial_transformation_matrixes=True,
        )
        self._landmarker = vision.FaceLandmarker.create_from_options(opciones)

    def detectar(self, frame_bgr: np.ndarray, ts_ms: int) -> list[dict]:
        h, w = frame_bgr.shape[:2]
        rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        img = self._mp.Image(image_format=self._mp.ImageFormat.SRGB, data=rgb)
        res = self._landmarker.detect_for_video(img, ts_ms)

        caras = []
        for i, lms in enumerate(res.face_landmarks):
            pts = np.array([[lm.x * w, lm.y * h] for lm in lms], dtype=np.float32)
            x1, y1 = pts.min(axis=0)
            x2, y2 = pts.max(axis=0)
            bbox = (int(x1), int(y1), int(x2), int(y2))

            yaw = pitch = roll = 0.0
            if res.facial_transformation_matrixes:
                m = np.array(res.facial_transformation_matrixes[i])
                yaw, pitch, roll = euler_desde_matriz(m)

            parpadeo = 0.0
            bs: dict[str, float] = {}
            if res.face_blendshapes:
                for cat in res.face_blendshapes[i]:
                    bs[cat.category_name] = float(cat.score)
                parpadeo = max(bs.get("eyeBlinkLeft", 0.0), bs.get("eyeBlinkRight", 0.0))

            yaw_ojos, pitch_ojos = mirada_desde_blendshapes(bs)
            yaw_mirada = yaw + CFG["signo_mirada"] * yaw_ojos if CFG["usar_mirada"] else yaw
            pitch_mirada = pitch + CFG["signo_mirada"] * pitch_ojos if CFG["usar_mirada"] else pitch

            caras.append({
                "bbox": bbox,
                "puntos": pts,
                "altura_px": int(y2 - y1),
                "yaw": yaw, "pitch": pitch, "roll": roll,
                "yaw_ojos": yaw_ojos, "pitch_ojos": pitch_ojos,
                "yaw_mirada": yaw_mirada, "pitch_mirada": pitch_mirada,
                "parpadeo": parpadeo,
            })
        return caras


def mirada_desde_blendshapes(bs: dict[str, float]) -> tuple[float, float]:
    """Desviación de los OJOS respecto a la cabeza, en grados aproximados.

    MediaPipe ya devuelve `eyeLookIn/Out/Up/DownLeft/Right` entre sus 52 blendshapes:
    "in" = hacia la nariz, "out" = hacia la sien. Combinando ambos ojos sale hacia dónde
    miran DENTRO de la cara, que sumado al yaw de la cabeza da a dónde mira de verdad.

    Cero coste: estos coeficientes ya venían calculados en la misma pasada.
    """
    if not bs:
        return 0.0, 0.0
    # Ojo izquierdo del sujeto: "out" tira hacia SU izquierda; el derecho, al revés.
    h_izq = bs.get("eyeLookOutLeft", 0.0) - bs.get("eyeLookInLeft", 0.0)
    h_der = bs.get("eyeLookInRight", 0.0) - bs.get("eyeLookOutRight", 0.0)
    horizontal = (h_izq + h_der) / 2.0

    v_izq = bs.get("eyeLookUpLeft", 0.0) - bs.get("eyeLookDownLeft", 0.0)
    v_der = bs.get("eyeLookUpRight", 0.0) - bs.get("eyeLookDownRight", 0.0)
    vertical = (v_izq + v_der) / 2.0

    return horizontal * GRADOS_MIRADA_MAX, vertical * GRADOS_MIRADA_MAX


def euler_desde_matriz(m: np.ndarray) -> tuple[float, float, float]:
    """Yaw/pitch/roll en grados desde la matriz de transformación facial 4x4.

    OJO: verifica los signos girando la cabeza y mirando los números en pantalla.
    Las convenciones de eje se documentan mal y equivocarse aquí produce un modo que
    dispara justo cuando la persona DEJA de mirar.
    """
    r = m[:3, :3]
    sy = float(np.sqrt(r[2, 1] ** 2 + r[2, 2] ** 2))
    pitch = np.degrees(np.arctan2(r[2, 1], r[2, 2]))
    yaw = np.degrees(np.arctan2(-r[2, 0], sy))
    roll = np.degrees(np.arctan2(r[1, 0], r[0, 0]))
    return float(yaw), float(pitch), float(roll)


# --------------------------------------------------------------------------------
# Tracker IoU: el embedding solo se calcula en tracks nuevos, no por frame
# --------------------------------------------------------------------------------
def iou(a, b) -> float:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    if ix2 <= ix1 or iy2 <= iy1:
        return 0.0
    inter = (ix2 - ix1) * (iy2 - iy1)
    area_a = (ax2 - ax1) * (ay2 - ay1)
    area_b = (bx2 - bx1) * (by2 - by1)
    return inter / float(area_a + area_b - inter)


class Tracker:
    """Asociación por IoU. Suficiente para caras a 4 fps; DeepSORT sería sobredimensionar."""

    UMBRAL_IOU = 0.3
    MAX_PERDIDOS = 8

    def __init__(self):
        self._tracks: dict[int, dict] = {}
        self._siguiente_id = 1

    def actualizar(self, caras: list[dict]) -> list[tuple[int, dict]]:
        libres = set(self._tracks)
        salida = []
        for cara in caras:
            mejor_id, mejor_iou = None, 0.0
            for tid in libres:
                v = iou(cara["bbox"], self._tracks[tid]["bbox"])
                if v > mejor_iou:
                    mejor_id, mejor_iou = tid, v
            if mejor_id is not None and mejor_iou >= self.UMBRAL_IOU:
                libres.discard(mejor_id)
                t = self._tracks[mejor_id]
            else:
                mejor_id = self._siguiente_id
                self._siguiente_id += 1
                t = {
                    "identidad": None,     # None | "nombre" | "DESCONOCIDO"
                    "sim": 0.0,
                    "votos": [],           # similitudes de la zona indecisa
                    "frames_ok": 0,        # persistencia para el disparo
                }
                self._tracks[mejor_id] = t
            t["bbox"] = cara["bbox"]
            t["perdido"] = 0
            salida.append((mejor_id, t))

        for tid in list(libres):
            self._tracks[tid]["perdido"] = self._tracks[tid].get("perdido", 0) + 1
            if self._tracks[tid]["perdido"] > self.MAX_PERDIDOS:
                del self._tracks[tid]
        return salida

    def reiniciar(self):
        self._tracks.clear()


# --------------------------------------------------------------------------------
# Embeddings (AuraFace ONNX) + galería como lista blanca
# --------------------------------------------------------------------------------
class Embedder:
    def __init__(self, onnx_path: Path):
        import onnxruntime as ort
        self._sesion = ort.InferenceSession(
            str(onnx_path), providers=["CPUExecutionProvider"]
        )
        self._entrada = self._sesion.get_inputs()[0].name

    def alinear(self, frame_bgr: np.ndarray, puntos: np.ndarray) -> np.ndarray | None:
        src = cinco_puntos(puntos)
        if src is None:
            return None
        matriz, _ = cv2.estimateAffinePartial2D(src, REF_5PTS, method=cv2.LMEDS)
        if matriz is None:
            return None
        return cv2.warpAffine(frame_bgr, matriz, (112, 112), borderValue=0)

    def embedding(self, crop_112: np.ndarray) -> np.ndarray:
        x = cv2.cvtColor(crop_112, cv2.COLOR_BGR2RGB).astype(np.float32)
        x = (x - 127.5) / 127.5
        x = np.transpose(x, (2, 0, 1))[None, ...]
        vec = self._sesion.run(None, {self._entrada: x})[0][0]
        n = np.linalg.norm(vec)
        return vec / n if n > 0 else vec


class Galeria:
    """Lista BLANCA. Comparación por MÁXIMO sobre las muestras, nunca por la media:
    el promedio de 'con gafas' y 'sin gafas' es un vector que no se parece a ninguno."""

    def __init__(self, ruta: Path):
        self._ruta = ruta
        self._personas: dict[str, np.ndarray] = {}
        self.cargar()

    def cargar(self):
        if self._ruta.exists():
            try:
                z = np.load(self._ruta, allow_pickle=False)
                self._personas = {k: z[k] for k in z.files}
                print(f"[galeria] {len(self._personas)} persona(s): "
                      f"{', '.join(f'{k} ({v.shape[0]} fotos)' for k, v in self._personas.items())}")
            except Exception as exc:
                print(f"[galeria] no se pudo leer {self._ruta}: {exc}")

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

    def vacia(self) -> bool:
        return not self._personas

    def resumen(self) -> list[tuple[str, int]]:
        return [(k, int(v.shape[0])) for k, v in self._personas.items()]

    def buscar(self, emb: np.ndarray) -> tuple[str | None, float]:
        mejor_nombre, mejor_sim = None, -1.0
        for nombre, muestras in self._personas.items():
            sim = float(np.max(muestras @ emb))   # coseno: todo está L2-normalizado
            if sim > mejor_sim:
                mejor_nombre, mejor_sim = nombre, sim
        return mejor_nombre, mejor_sim

    def matriz_parecidos(self) -> list[str]:
        """Pares de personas de la galería que se parecen entre sí (hermanos, etc.).
        Es información que hay que VER, no un detalle interno."""
        nombres = list(self._personas)
        lineas = []
        for i, a in enumerate(nombres):
            for b in nombres[i + 1:]:
                sim = float(np.max(self._personas[a] @ self._personas[b].T))
                marca = "  <-- OJO, se confundirán" if sim >= CFG["t_alto"] else ""
                lineas.append(f"  {a} vs {b}: {sim:.3f}{marca}")
        return lineas


# --------------------------------------------------------------------------------
# Evaluación de una cara: filtros + identidad
# --------------------------------------------------------------------------------
def evaluar(cara: dict, track: dict) -> dict:
    """Aplica los filtros baratos ANTES de gastar un embedding. Devuelve el veredicto
    con el MOTIVO del descarte — eso es lo que hace ajustables los umbrales sin adivinar."""
    if cara["altura_px"] < CFG["min_altura_cara_px"]:
        return {"pasa": False, "motivo": f"muy lejos ({cara['altura_px']}px)"}
    # OJO: se filtra por la MIRADA (cabeza + ojos), no por la cabeza sola. Girar la
    # cabeza y mirar de reojo es justo el caso del cotilla, y con la cabeza sola se
    # colaba entero.
    if abs(cara["yaw_mirada"]) > CFG["yaw_max_grados"]:
        return {"pasa": False, "motivo": f"no mira (mirada {cara['yaw_mirada']:.0f}°)"}
    if abs(cara["pitch_mirada"]) > CFG["pitch_max_grados"]:
        return {"pasa": False, "motivo": f"no mira (pitch {cara['pitch_mirada']:.0f}°)"}
    return {"pasa": True, "motivo": ""}


def decidir_identidad(track: dict, galeria: Galeria, emb: np.ndarray) -> None:
    """Doble umbral + votación. Ante la duda persistente: DESCONOCIDO (lista blanca)."""
    nombre, sim = galeria.buscar(emb)
    track["sim"] = sim
    if sim >= CFG["t_alto"]:
        track["identidad"] = nombre
        return
    if sim <= CFG["t_bajo"]:
        track["identidad"] = "DESCONOCIDO"
        return
    track["votos"].append((nombre, sim))
    if len(track["votos"]) >= CFG["max_votos"]:
        buenos = [n for n, s in track["votos"] if s >= (CFG["t_alto"] + CFG["t_bajo"]) / 2]
        track["identidad"] = max(set(buenos), key=buenos.count) if buenos else "DESCONOCIDO"


# --------------------------------------------------------------------------------
# Dibujo
# --------------------------------------------------------------------------------
def texto(img, s, org, color=BLANCO, escala=0.45, grosor=1):
    cv2.putText(img, s, org, cv2.FONT_HERSHEY_SIMPLEX, escala, (0, 0, 0), grosor + 2, cv2.LINE_AA)
    cv2.putText(img, s, org, cv2.FONT_HERSHEY_SIMPLEX, escala, color, grosor, cv2.LINE_AA)


def dibujar_cara(frame, tid, cara, track, veredicto, mostrar_malla):
    x1, y1, x2, y2 = cara["bbox"]
    if not veredicto["pasa"]:
        color, etiqueta = GRIS, f"#{tid} descartado: {veredicto['motivo']}"
    elif track["identidad"] is None:
        color, etiqueta = AMARILLO, f"#{tid} identificando..."
    elif track["identidad"] == "DESCONOCIDO":
        color = ROJO
        etiqueta = f"#{tid} DESCONOCIDO (sim {track['sim']:.2f}) [{track['frames_ok']}/{CFG['frames_para_disparar']}]"
    else:
        color = VERDE
        etiqueta = f"#{tid} {track['identidad']} (sim {track['sim']:.2f})"

    cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
    texto(frame, etiqueta, (x1, max(14, y1 - 18)), color)
    texto(frame,
          f"h={cara['altura_px']}px  cabeza={cara['yaw']:+.0f}  ojos={cara['yaw_ojos']:+.0f}"
          f"  MIRADA={cara['yaw_mirada']:+.0f}",
          (x1, min(frame.shape[0] - 6, y2 + 16)), color, 0.4)

    if mostrar_malla:
        for p in cara["puntos"][::4]:
            cv2.circle(frame, (int(p[0]), int(p[1])), 1, (200, 200, 60), -1)
        # Los 5 de la alineación, en grande: verifica que no estén espejados.
        for p in cinco_puntos(cara["puntos"]) or []:
            cv2.circle(frame, (int(p[0]), int(p[1])), 4, (255, 0, 255), -1)


def dibujar_panel(frame, fps, ms_det, ms_emb, galeria, hay_embedder, dispararia, enrolando):
    y = 20
    for linea, color in [
        (f"FPS {fps:4.1f} | deteccion {ms_det:5.1f} ms | embedding {ms_emb:5.1f} ms", BLANCO),
        (f"min_altura={CFG['min_altura_cara_px']}px (1/2)  yaw_max={CFG['yaw_max_grados']:.0f} (3/4)"
         + (f"  mirada=ON signo={CFG['signo_mirada']:+d} (v/b)" if CFG["usar_mirada"]
            else "  mirada=OFF (v)"),
         BLANCO if CFG["usar_mirada"] else AMARILLO),
        (f"T_alto={CFG['t_alto']:.2f} (5/6)  T_bajo={CFG['t_bajo']:.2f} (7/8)  [SIN CALIBRAR]", BLANCO),
        (f"galeria: {len(galeria.resumen())} persona(s)"
         + ("" if hay_embedder else "  [SIN MODELO DE CARAS: solo deteccion]"),
         BLANCO if hay_embedder else AMARILLO),
        ("e=foto  n=cambiar persona  l=listar  x=borrar  m=malla  g=guardar  q=salir", GRIS),
    ]:
        texto(frame, linea, (10, y), color)
        y += 18

    if enrolando:
        n = dict(galeria.resumen()).get(enrolando, 0)
        texto(frame, f"ENROLANDO A: {enrolando}  ({n} foto(s), apunta a 5-10)",
              (10, y + 4), AMARILLO, 0.55, 2)
        texto(frame, "pulsa 'e' para cada foto: gafas si/no, luz distinta, algo girado",
              (10, y + 24), AMARILLO, 0.45)

    if dispararia:
        h, w = frame.shape[:2]
        cv2.rectangle(frame, (0, 0), (w - 1, h - 1), ROJO, 6)
        texto(frame, "DISPARARIA (el modo real bloquearia aqui)", (10, h - 14), ROJO, 0.7, 2)


# --------------------------------------------------------------------------------
# Acciones de teclado que bloquean el bucle (pedir texto por consola)
# --------------------------------------------------------------------------------
def pedir(msg: str) -> str:
    print(f"\n{msg}", end="", flush=True)
    try:
        return input().strip()
    except (EOFError, KeyboardInterrupt):
        return ""


def calidad_enrolamiento(cara: dict, crop: np.ndarray | None) -> str | None:
    """Puertas de calidad. Diez fotos variadas con un modelo mediocre baten a una foto
    perfecta con el mejor modelo del mundo: rechazar aquí importa más que el modelo."""
    if cara["altura_px"] < 100:
        return f"cara demasiado pequena ({cara['altura_px']}px, minimo 100). Acercate."
    if abs(cara["yaw"]) > 30:
        return f"cara demasiado girada (yaw {cara['yaw']:.0f}°, maximo 30)."
    if crop is not None:
        nitidez = cv2.Laplacian(cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY), cv2.CV_64F).var()
        if nitidez < 40:
            return f"foto borrosa (nitidez {nitidez:.0f}, minimo 40). Quedate quieto."
    return None


# --------------------------------------------------------------------------------
# Bucle principal
# --------------------------------------------------------------------------------
def main() -> int:
    for d in (DATA, MODELOS, FOTOS, CAPTURAS):
        d.mkdir(parents=True, exist_ok=True)

    if not descargar(TASK_URL, TASK_PATH, "face_landmarker.task (~3 MB)"):
        print("[fatal] Sin el modelo de deteccion no hay nada que probar.")
        return 1

    # --sin-caras: fuerza el modo solo-deteccion aunque el modelo este descargado
    # (util para volver a calibrar distancia/angulo sin el ruido de la identidad).
    sin_caras = "--sin-caras" in sys.argv

    # Solo pregunta si REALMENTE falta: una vez descargado, no vuelve a molestar.
    if sin_caras:
        print("[modelos] --sin-caras: reconocimiento DESACTIVADO a peticion tuya.")
    elif not AURAFACE_PATH.exists():
        print("\n[modelos] El modelo de RECONOCIMIENTO (AuraFace) pesa ~250 MB.")
        print("[modelos] Sin el, el prototipo funciona igual pero solo detecta caras")
        print("[modelos] (todas cuentan como desconocidas). Puedes probarlo asi primero.")
        if pedir("¿Descargarlo ahora? [s/N]: ").lower() in ("s", "si", "sí", "y"):
            descargar(AURAFACE_URL, AURAFACE_PATH, "AuraFace r100 (~250 MB)")
    else:
        print("[modelos] AuraFace ya estaba descargado, no hace falta bajarlo otra vez.")

    detector = Detector(TASK_PATH, CFG["num_caras"])
    galeria = Galeria(GALERIA_NPZ)
    embedder = None
    if sin_caras:
        print("[modelos] Reconocimiento facial INACTIVO (--sin-caras).")
    elif AURAFACE_PATH.exists():
        try:
            embedder = Embedder(AURAFACE_PATH)
            print("[modelos] Reconocimiento facial ACTIVO.")
        except Exception as exc:
            print(f"[modelos] No se pudo cargar AuraFace ({exc}). Sigo solo con deteccion.")
    else:
        print("[modelos] Reconocimiento facial INACTIVO (falta el .onnx).")

    # CAP_DSHOW en Windows: el backend MSMF por defecto anade ~2 s al abrir la camara.
    cap = cv2.VideoCapture(0, cv2.CAP_DSHOW if sys.platform == "win32" else 0)
    if not cap.isOpened():
        print("\n[fatal] No se pudo abrir la webcam.")
        print("[fatal] En Windows la camara es de USO EXCLUSIVO: cierra Zoom/Teams/Meet")
        print("[fatal] o cualquier pestana que la tenga cogida, y vuelve a intentarlo.")
        return 1
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)

    tracker = Tracker()
    mostrar_malla = False
    landmarks_avisado = False
    enrolando: str | None = None   # persona a la que se le están sacando fotos
    tiempos = deque(maxlen=30)
    ms_det = ms_emb = 0.0
    t0 = time.time()

    print("\n=== Prototipo MODO SEGURO en marcha. Ventana enfocada para las teclas. ===\n")

    while True:
        ok, frame = cap.read()
        if not ok:
            print("[error] Se perdio el frame (¿otra app te quito la camara?). Reintento...")
            time.sleep(0.5)
            continue

        # Copia SIN dibujos: los recortes de enrolamiento y los embeddings tienen que
        # salir del frame limpio, no del que ya lleva recuadros y texto encima.
        frame_limpio = frame.copy()

        ini = time.perf_counter()
        caras = detector.detectar(frame, int((time.time() - t0) * 1000))
        ms_det = (time.perf_counter() - ini) * 1000

        if caras and not landmarks_avisado:
            landmarks_avisado = True
            print(f"[debug] {len(caras[0]['puntos'])} landmarks por cara "
                  f"({'con' if len(caras[0]['puntos']) > 468 else 'SIN'} iris)")

        ms_emb = 0.0
        dispararia = False
        pares = tracker.actualizar(caras)

        for (tid, track), cara in zip(pares, caras):
            veredicto = evaluar(cara, track)

            if not veredicto["pasa"]:
                track["frames_ok"] = 0
            else:
                # Embedding SOLO en tracks sin identidad resuelta, nunca por frame.
                if embedder is not None and track["identidad"] is None and not galeria.vacia():
                    crop = embedder.alinear(frame_limpio, cara["puntos"])
                    if crop is not None:
                        ini = time.perf_counter()
                        emb = embedder.embedding(crop)
                        ms_emb += (time.perf_counter() - ini) * 1000
                        decidir_identidad(track, galeria, emb)
                elif embedder is None or galeria.vacia():
                    track["identidad"] = "DESCONOCIDO"

                if track["identidad"] == "DESCONOCIDO":
                    track["frames_ok"] += 1
                    if track["frames_ok"] >= CFG["frames_para_disparar"]:
                        dispararia = True
                else:
                    track["frames_ok"] = 0

            dibujar_cara(frame, tid, cara, track, veredicto, mostrar_malla)

        tiempos.append(time.time())
        fps = (len(tiempos) - 1) / (tiempos[-1] - tiempos[0]) if len(tiempos) > 1 else 0.0
        dibujar_panel(frame, fps, ms_det, ms_emb, galeria, embedder is not None,
                      dispararia, enrolando)

        if dispararia:
            ts = time.strftime("%Y%m%d_%H%M%S")
            destino = CAPTURAS / f"{ts}.jpg"
            if not destino.exists():
                cv2.imwrite(str(destino), frame)

        cv2.imshow("Prototipo MODO SEGURO", frame)
        k = cv2.waitKey(1) & 0xFF

        if k in (ord("q"), 27):
            break
        elif k == ord("m"):
            mostrar_malla = not mostrar_malla
        elif k == ord("v"):
            CFG["usar_mirada"] = not CFG["usar_mirada"]
            print(f"[mirada] correccion por ojos: {'ON' if CFG['usar_mirada'] else 'OFF'}")
        elif k == ord("b"):
            CFG["signo_mirada"] *= -1
            print(f"[mirada] signo invertido: {CFG['signo_mirada']:+d}")
        elif k == ord("r"):
            tracker.reiniciar()
            print("[tracks] reiniciados")
        elif k == ord("1"):
            CFG["min_altura_cara_px"] = max(20, CFG["min_altura_cara_px"] - 5)
        elif k == ord("2"):
            CFG["min_altura_cara_px"] += 5
        elif k == ord("3"):
            CFG["yaw_max_grados"] = max(5, CFG["yaw_max_grados"] - 5)
        elif k == ord("4"):
            CFG["yaw_max_grados"] += 5
        elif k == ord("5"):
            CFG["t_alto"] = round(CFG["t_alto"] - 0.01, 3)
        elif k == ord("6"):
            CFG["t_alto"] = round(CFG["t_alto"] + 0.01, 3)
        elif k == ord("7"):
            CFG["t_bajo"] = round(CFG["t_bajo"] - 0.01, 3)
        elif k == ord("8"):
            CFG["t_bajo"] = round(CFG["t_bajo"] + 0.01, 3)
        elif k == ord("g"):
            ruta = CAPTURAS / f"manual_{time.strftime('%Y%m%d_%H%M%S')}.jpg"
            cv2.imwrite(str(ruta), frame)
            print(f"[captura] {ruta}")
        elif k == ord("l"):
            print("\n[galeria] Personas autorizadas:")
            for nombre, n in galeria.resumen() or [("(vacia)", 0)]:
                print(f"  - {nombre}: {n} foto(s)")
            lineas = galeria.matriz_parecidos()
            if lineas:
                print("[galeria] Parecidos entre personas de la lista:")
                print("\n".join(lineas))
        elif k == ord("n"):
            enrolando = pedir("Nombre de la siguiente persona (vacio = terminar): ") or None
            print(f"[enrolar] activo: {enrolando}" if enrolando else "[enrolar] terminado")
            for _ in range(5):
                cap.read()
        elif k == ord("x"):
            nombre = pedir("Nombre a BORRAR (embeddings + fotos): ")
            if nombre and galeria.borrar(nombre):
                shutil.rmtree(FOTOS / nombre, ignore_errors=True)
                tracker.reiniciar()
                print(f"[galeria] borrado: {nombre}")
            elif nombre:
                print(f"[galeria] no existe: {nombre}")
        elif k == ord("e"):
            # Un fallo enrolando NO debe tumbar el prototipo: se explica y se sigue.
            try:
                if embedder is None:
                    print("[enrolar] No hay modelo de reconocimiento cargado.")
                    continue
                if not caras:
                    print("[enrolar] No veo ninguna cara.")
                    continue
                cara = max(caras, key=lambda c: c["altura_px"])
                crop = embedder.alinear(frame_limpio, cara["puntos"])
                if crop is None:
                    print("[enrolar] No pude alinear la cara (landmarks insuficientes).")
                    continue
                problema = calidad_enrolamiento(cara, crop)
                if problema:
                    print(f"[enrolar] RECHAZADA: {problema}")
                    continue
                # El nombre se pide UNA sola vez por persona: a partir de ahi cada 'e'
                # es instantanea. Antes se preguntaba en cada foto y el video se
                # congelaba en mitad de la sesion de enrolamiento, que es justo cuando
                # necesitas verte para cambiar de pose.
                if not enrolando:
                    enrolando = pedir("Nombre de la persona a autorizar: ")
                    for _ in range(5):   # vacia el buffer tras el input bloqueante
                        cap.read()
                    if not enrolando:
                        continue

                emb = embedder.embedding(crop)
                galeria.anadir(enrolando, emb)
                carpeta = FOTOS / enrolando
                carpeta.mkdir(parents=True, exist_ok=True)
                cv2.imwrite(str(carpeta / f"{time.strftime('%Y%m%d_%H%M%S')}.jpg"), crop)
                tracker.reiniciar()
                n = dict(galeria.resumen())[enrolando]
                print(f"[enrolar] {enrolando}: {n} foto(s).")
                if n >= 5:
                    print("[enrolar] Ya tienes suficientes. 'n' para pasar a otra persona.")
            except Exception:
                import traceback
                print("\n[enrolar] FALLO al enrolar (sigo funcionando):")
                traceback.print_exc()

    cap.release()
    cv2.destroyAllWindows()

    (DATA / "config_usada.json").write_text(
        json.dumps(CFG, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"\n[fin] Config con la que acabaste guardada en {DATA / 'config_usada.json'}")
    print("[fin] Esos numeros son el punto de partida de data/seguro.json en el modulo real.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
