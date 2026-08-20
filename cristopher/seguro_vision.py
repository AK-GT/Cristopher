"""Modo Seguro: visión pura. Sin cámara, sin hilos, sin estado global.

Portado de `prototipo_seguro.py` (banco de pruebas ya validado con webcam real) — la
matemática y los gotchas documentados aquí vienen de ese prototipo, no se han re-derivado.
Diferencia central con el prototipo: aquí no hay un `CFG` global de módulo — todo lo que
antes se leía de `CFG` entra como parámetro explícito (`cfg`, `signo_mirada`, `usar_mirada`),
porque en producción esa config vive en `data/seguro.json` (ver `seguro.py`).
"""

from __future__ import annotations

from typing import Optional

import numpy as np

# Índices de MediaPipe. Con iris (478 landmarks) los centros de ojo son directos; sin
# iris (468) hay que promediar las esquinas. Los landmarks de iris (468/473) NO SIEMPRE
# EXISTEN: salen 468 o 478 según versión — degradar promediando esquinas de ojo, nunca
# indexar a ciegas (verificado con MediaPipe real en el prototipo).
IDX_IRIS_IZQ, IDX_IRIS_DER = 468, 473
IDX_OJO_IZQ = (33, 133)     # esquinas externa e interna del ojo izquierdo
IDX_OJO_DER = (362, 263)
IDX_NARIZ = 1
IDX_BOCA_IZQ, IDX_BOCA_DER = 61, 291

# Desviación máxima de los ojos dentro de la órbita, en grados, cuando el blendshape
# correspondiente vale 1.0. Aproximación: el rango fisiológico cómodo es ~±35°.
GRADOS_MIRADA_MAX = 35.0

# Plantilla de referencia ArcFace para recortes de 112x112 (Fase 2, se usa desde
# seguro_galeria.Embedder — vive aquí porque cinco_puntos() ya trabaja en ese espacio).
REF_5PTS = np.array([
    [38.2946, 51.6963],   # ojo izquierdo
    [73.5318, 51.5014],   # ojo derecho
    [56.0252, 71.7366],   # nariz
    [41.5493, 92.3655],   # comisura izquierda
    [70.7299, 92.2041],   # comisura derecha
], dtype=np.float32)


def cinco_puntos(puntos: np.ndarray) -> "np.ndarray | None":
    """Los 5 puntos que espera ArcFace: ojo izq, ojo der, nariz, comisura izq, der.

    Degrada si el modelo no trae landmarks de iris (478 vs 468) en vez de reventar con
    un IndexError — que es justo lo que hacía la primera versión del prototipo.
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


def euler_desde_matriz(m: np.ndarray) -> tuple[float, float, float]:
    """Yaw/pitch/roll en grados desde la matriz de transformación facial 4x4.

    OJO: verifica los signos girando la cabeza y mirando los números en pantalla (con
    scripts/probar_seguro_vision.py). Las convenciones de eje se documentan mal y
    equivocarse aquí produce un modo que dispara justo cuando la persona DEJA de mirar.
    """
    r = m[:3, :3]
    sy = float(np.sqrt(r[2, 1] ** 2 + r[2, 2] ** 2))
    pitch = np.degrees(np.arctan2(r[2, 1], r[2, 2]))
    yaw = np.degrees(np.arctan2(-r[2, 0], sy))
    roll = np.degrees(np.arctan2(r[1, 0], r[0, 0]))
    return float(yaw), float(pitch), float(roll)


def mirada_desde_blendshapes(bs: dict[str, float]) -> tuple[float, float]:
    """Desviación de los OJOS respecto a la cabeza, en grados aproximados.

    MediaPipe ya devuelve `eyeLookIn/Out/Up/DownLeft/Right` entre sus 52 blendshapes:
    "in" = hacia la nariz, "out" = hacia la sien. Combinando ambos ojos sale hacia dónde
    miran DENTRO de la cara, que sumado al yaw de la cabeza da a dónde mira de verdad.
    Cero coste extra: estos coeficientes ya venían calculados en la misma pasada, así
    que no hace falta L2CS-Net ni ningún modelo de mirada aparte.
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


class Detector:
    """Envoltorio de MediaPipe Face Landmarker: una pasada da bbox, landmarks, matriz
    de pose y blendshapes por cara."""

    def __init__(self, task_path, num_caras: int):
        # Import perezoso: si `mediapipe` no está instalado, instanciar Detector debe
        # fallar (y el llamador — seguro.hilo_vigilante — lo captura y degrada), pero
        # IMPORTAR seguro_vision.py sin mediapipe presente no debe romper nada que solo
        # necesite evaluar_cara()/euler_desde_matriz() etc. en pruebas o en el HUD.
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

    def detectar(
        self,
        frame_bgr: np.ndarray,
        ts_ms: int,
        signo_mirada: int = 1,
        usar_mirada: bool = True,
    ) -> list[dict]:
        import cv2

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
            yaw_mirada = yaw + signo_mirada * yaw_ojos if usar_mirada else yaw
            pitch_mirada = pitch + signo_mirada * pitch_ojos if usar_mirada else pitch

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
    """Asociación por IoU. Suficiente para caras a pocos fps; DeepSORT sería
    sobredimensionar para esto."""

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
                    "votos": [],           # similitudes de la zona indecisa (Fase 2)
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


def evaluar_cara(cara: dict, cfg: dict) -> dict:
    """Aplica los filtros baratos ANTES de gastar un embedding. Devuelve el veredicto
    con el MOTIVO del descarte — eso es lo que hace ajustables los umbrales sin adivinar,
    en vez de esconder por qué se descartó una cara."""
    if cara["altura_px"] < cfg["min_altura_cara_px"]:
        return {"pasa": False, "motivo": f"muy lejos ({cara['altura_px']}px)"}
    # Se filtra por la MIRADA (cabeza + ojos), no por la cabeza sola. Girar la cabeza y
    # mirar de reojo es justo el caso del cotilla, y con la cabeza sola se colaba entero.
    if abs(cara["yaw_mirada"]) > cfg["yaw_max_grados"]:
        return {"pasa": False, "motivo": f"no mira (mirada {cara['yaw_mirada']:.0f}°)"}
    if abs(cara["pitch_mirada"]) > cfg["pitch_max_grados"]:
        return {"pasa": False, "motivo": f"no mira (pitch {cara['pitch_mirada']:.0f}°)"}
    return {"pasa": True, "motivo": ""}
