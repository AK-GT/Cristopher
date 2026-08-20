#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Captura GUIADA de enrolamiento del Modo Seguro: te pide una pose cada vez y tú
pulsas ENTER cuando estés colocado.

Por qué existe, y por qué no basta con `seguro_enrolar` (la tool de chat): aquella
dispara N fotos seguidas con una pausa de 1s, así que salen N variantes de LA MISMA
pose, la misma luz y la misma distancia. La galería compara por MÁXIMO sobre las
muestras, o sea que cada foto solo aporta si cubre un "modo" tuyo distinto: cinco
frontales seguidas valen casi lo mismo que una. Diez fotos variadas con un modelo
mediocre baten a una foto perfecta con el mejor modelo del mundo.

El guion de poses no es arbitrario, sale de qué mueve el embedding:
  - yaw: mucho, pero ACOTADO — `calidad_enrolamiento` rechaza por encima de 30°, así
    que se piden giros moderados (12-28°), nunca perfiles.
  - pitch: crítico en este equipo, con la webcam por encima de la línea de ojos.
  - gafas: la mayor variación intra-persona que existe si se usan a ratos.
  - luz: día vs lámpara de noche son dos caras distintas para el modelo.
  - distancia: cambia la resolución efectiva del recorte 112x112.
  - expresión: aporta poco; no se gasta una foto en ella.

Los signos de yaw/pitch NO se asumen (el repo ya documenta que las convenciones de eje
se documentan mal): la pose frontal fija la referencia y las poses de giro solo exigen
que la SEGUNDA vaya hacia el lado contrario de la primera, sea cual sea el signo.

Uso:
    python -m scripts.enrolar_guiado            (pregunta el nombre)
    python -m scripts.enrolar_guiado dueño

Teclas (con la ventana de vídeo enfocada):
    ENTER   capturar la pose actual (solo si el indicador está en verde)
    S       saltar esta pose (p. ej. las gafas, si no usas)
    R       repetir la última capturada (la borra y vuelve a pedirla)
    Q/ESC   salir; lo capturado hasta ahí SE QUEDA guardado
"""

from __future__ import annotations

import json
import sys
import unicodedata
import time
import urllib.request

import cv2
import numpy as np

from cristopher import seguro
from cristopher.config import HUD_PORT
from cristopher.seguro_galeria import (
    AURAFACE_PATH, POSES_ENROLAMIENTO, Embedder, Galeria, calidad_enrolamiento,
    comprobar_pose, descargar_modelo_embedding, tras_capturar_pose,
)
from cristopher.seguro_vision import Detector

VERDE = (80, 220, 100)
ROJO = (60, 60, 240)
GRIS = (150, 150, 150)
AMBAR = (60, 210, 240)
BLANCO = (245, 245, 245)

def _ascii(t: str) -> str:
    """cv2.putText usa fuentes Hershey, que no tienen tildes ni ñ: se pintan como
    interrogantes. El guion compartido está en español de verdad (lo habla la tool), así
    que aquí se transcribe justo en el borde del dibujo, no en la fuente."""
    return unicodedata.normalize("NFKD", t).encode("ascii", "ignore").decode("ascii")


# --- Dibujo ---------------------------------------------------------------------
def _texto(img, s, org, color=BLANCO, escala=0.5, grosor=1):
    cv2.putText(img, s, org, cv2.FONT_HERSHEY_SIMPLEX, escala, (0, 0, 0), grosor + 2, cv2.LINE_AA)
    cv2.putText(img, s, org, cv2.FONT_HERSHEY_SIMPLEX, escala, color, grosor, cv2.LINE_AA)


def _pintar(frame, pose, idx, total, guardadas, cara, ok, motivo):
    h, w = frame.shape[:2]
    cv2.rectangle(frame, (0, 0), (w, 66), (12, 12, 12), -1)
    _texto(frame, _ascii(f"[{idx + 1}/{total}]  {pose['rotulo']}"), (10, 24), BLANCO, 0.6, 2)
    _texto(frame, _ascii(pose["ayuda"]), (10, 46), GRIS, 0.42)
    _texto(frame, f"guardadas: {guardadas}", (w - 130, 24), AMBAR, 0.45)

    if cara is not None:
        x1, y1, x2, y2 = cara["bbox"]
        cv2.rectangle(frame, (x1, y1), (x2, y2), VERDE if ok else ROJO, 2)
        _texto(frame, f"h={cara['altura_px']}px  yaw={cara['yaw']:+.0f}  pitch={cara['pitch']:+.0f}",
               (10, h - 32), GRIS, 0.42)

    cv2.rectangle(frame, (0, h - 26), (w, h), (12, 12, 12), -1)
    if ok:
        _texto(frame, ">>> PULSA ENTER PARA CAPTURAR <<<", (10, h - 7), VERDE, 0.55, 2)
    else:
        _texto(frame, _ascii(motivo), (10, h - 7), ROJO, 0.48)
    _texto(frame, "S=saltar  R=repetir  Q=salir", (w - 250, h - 7), GRIS, 0.38)


# --- Comprobaciones previas -----------------------------------------------------
def _modo_seguro_activo() -> bool:
    """La webcam es de uso EXCLUSIVO en Windows: si el hilo vigilante la tiene cogida,
    este script no puede abrirla. Mejor decirlo que fallar con 'no se pudo abrir'."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{HUD_PORT}/seguro/estado", timeout=2) as r:
            return bool(json.loads(r.read()).get("activo"))
    except Exception:
        return False


def main() -> int:
    nombre = " ".join(sys.argv[1:]).strip()
    if not nombre:
        nombre = input("Nombre de la persona a enrolar: ").strip()
    if not nombre:
        print("[fatal] Sin nombre no hay nada que enrolar.")
        return 1

    if _modo_seguro_activo():
        print("\n[fatal] El Modo Seguro está activo y tiene cogida la webcam.")
        print("[fatal] Ciérralo primero (el botón 📷 del HUD, o 'desactiva el modo seguro').")
        return 1

    if not seguro._TASK_PATH.exists():
        print("[modelos] Descargando el modelo de detección de rostro ...")
        if not seguro._descargar_modelo_deteccion():
            print("[fatal] No pude descargar face_landmarker.task.")
            return 1
    if not AURAFACE_PATH.exists():
        print("[modelos] Descargando el modelo de identidad (AuraFace, ~250MB, una vez) ...")
        if not descargar_modelo_embedding():
            print("[fatal] No pude descargar AuraFace.")
            return 1

    detector = Detector(seguro._TASK_PATH, num_caras=1)
    embedder = Embedder(AURAFACE_PATH)
    galeria = Galeria(seguro._GALERIA_NPZ)
    previas = dict(galeria.resumen()).get(nombre, 0)

    cfg = seguro._leer_config()
    cap = cv2.VideoCapture(cfg["camara_indice"], cv2.CAP_DSHOW)
    if not cap.isOpened():
        cap.release()
        print("\n[fatal] No se pudo abrir la webcam (¿la tiene Zoom/Teams/Meet, o el HUD?).")
        return 1
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)

    print(f"\n=== Enrolamiento guiado de «{nombre}» ({previas} foto(s) ya en la galería) ===")
    print("=== Colócate como te pida el rótulo y pulsa ENTER cuando esté en VERDE. ===\n")

    ctx: dict = {}
    idx = 0
    guardadas = 0
    ultima: "str | None" = None
    t0 = time.time()

    try:
        while idx < len(POSES_ENROLAMIENTO):
            ok_frame, frame = cap.read()
            if not ok_frame:
                print("[error] Se perdió el frame de la webcam. Reintento...")
                time.sleep(0.3)
                continue

            pose = POSES_ENROLAMIENTO[idx]
            caras = detector.detectar(frame, int((time.time() - t0) * 1000))
            cara = max(caras, key=lambda c: c["altura_px"]) if caras else None

            if cara is None:
                ok, motivo = False, "no veo ninguna cara"
                crop = None
            else:
                crop = embedder.alinear(frame, cara["puntos"])
                problema = calidad_enrolamiento(cara, crop)
                if problema:
                    ok, motivo = False, problema
                else:
                    ok, motivo = comprobar_pose(pose["id"], cara, ctx)

            vista = frame.copy()
            _pintar(vista, pose, idx, len(POSES_ENROLAMIENTO), guardadas, cara, ok, motivo)
            cv2.imshow("Modo Seguro - enrolamiento guiado", vista)
            k = cv2.waitKey(1) & 0xFF

            if k in (ord("q"), 27):
                print("\n[salir] Dejo lo capturado hasta aquí.")
                break

            if k == ord("s"):
                print(f"  · saltada: {pose['rotulo']}")
                idx += 1
                continue

            if k == ord("r") and ultima:
                # Quita SOLO la última muestra añadida, no a la persona entera.
                if galeria.quitar_ultima(nombre):
                    guardadas = max(0, guardadas - 1)
                    idx = max(0, idx - 1)
                    ultima = None
                    print(f"  · repetimos: {POSES_ENROLAMIENTO[idx]['instruccion']}")
                continue

            if k in (13, 10) and ok and cara is not None and crop is not None:
                emb = embedder.embedding(crop)
                galeria.anadir(nombre, emb)
                seguro._guardar_recorte_enrolamiento(nombre, crop, time.time())
                seguro._actualizar_personas_json(nombre)
                guardadas += 1
                ultima = pose["id"]
                tras_capturar_pose(pose["id"], cara, ctx)
                print(f"  ✓ {pose['rotulo']}  (yaw {cara['yaw']:+.0f}, pitch "
                      f"{cara['pitch']:+.0f}, {cara['altura_px']}px)")
                idx += 1
    finally:
        cap.release()
        cv2.destroyAllWindows()

    total = dict(galeria.resumen()).get(nombre, 0)
    print(f"\n=== «{nombre}»: {guardadas} foto(s) nueva(s) · {total} en total ===")

    m = galeria.muestras(nombre)
    if m is not None and m.shape[0] >= 2:
        sims = [float(np.max(np.delete(m, i, axis=0) @ m[i])) for i in range(m.shape[0])]
        print(f"Consistencia (hold-one-out): min {min(sims):.3f} · media {np.mean(sims):.3f}")
        if min(sims) < 0.45:
            print("OJO: alguna foto se parece poco al resto. Puede ser una mala captura "
                  "(borrosa, media cara) o justo la variedad que buscábamos. Si tras "
                  "calibrar no te reconoce, sospecha de esa.")

    if total >= 5:
        print("\nSiguiente paso: python -m scripts.calibrar_seguro")
    else:
        print(f"\nAún faltan fotos: hacen falta al menos 5 para calibrar (tienes {total}).")
    print("Reinicia el HUD para que la vigilancia use la galería nueva "
          "(la tiene cargada en memoria en su propio proceso).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
