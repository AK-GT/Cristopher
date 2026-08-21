#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Banco de pruebas PERMANENTE de la visión del Modo Seguro (no de usar y tirar).

A diferencia de `prototipo_seguro.py` (que fue el spike inicial, ya portado), este
script:
  - Usa el `Detector`/`Tracker`/`evaluar_cara` REALES de `cristopher/seguro_vision.py`
    — lo que se ve aquí es exactamente lo que hará `seguro.py` en producción.
  - Lee y escribe la config REAL (`data/seguro.json`, vía
    `cristopher.seguro._leer_config`/`_escribir_config`), así que lo que calibres
    aquí es justo lo que usará el módulo real, sin un `config_usada.json` aparte.
  - NO enrola identidad, NO dispara nada, NO bloquea el PC: es solo el banco de
    pruebas de visión (distancia + mirada), para fijar los umbrales y, sobre todo,
    `signo_mirada` — el único valor que el diseño marca como "no se puede deducir
    sin cámara, se verifica con el usuario delante".

Uso:
    python -m scripts.probar_seguro_vision

Teclas (con la ventana de vídeo enfocada):
    q / ESC   salir sin guardar
    g         guardar la config actual en data/seguro.json (preserva la contraseña
              si ya había una) y salir
    m         mostrar/ocultar la malla de landmarks
    v         alternar corrección por mirada (ojos) ON/OFF
    b         invertir signo_mirada (+1 / -1) — pruébalo mirando a la pantalla y
              girando la cabeza: MIRADA debe acercarse a 0, no alejarse
    1 / 2     min_altura_cara_px  -5 / +5
    3 / 4     yaw_max_grados      -5 / +5
"""

from __future__ import annotations

import sys
import time
from collections import deque

import cv2

from cristopher.seguro import _TASK_PATH, _TASK_URL, _leer_config, _escribir_config
from cristopher.seguro_vision import Detector, Tracker, cinco_puntos, evaluar_cara

VERDE = (80, 220, 100)
ROJO = (60, 60, 240)
GRIS = (150, 150, 150)
AMARILLO = (60, 210, 240)
BLANCO = (245, 245, 245)


def _asegurar_modelo() -> bool:
    if _TASK_PATH.exists():
        return True
    print(f"[modelos] Descargando face_landmarker.task desde {_TASK_URL} ...")
    import shutil
    import urllib.request

    _TASK_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = _TASK_PATH.with_suffix(_TASK_PATH.suffix + ".parcial")
    try:
        urllib.request.urlretrieve(_TASK_URL, tmp)
        shutil.move(str(tmp), str(_TASK_PATH))
        return True
    except Exception as exc:
        print(f"[modelos] FALLÓ la descarga: {exc}")
        print(f"[modelos] Descárgalo a mano y guárdalo en: {_TASK_PATH}")
        tmp.unlink(missing_ok=True)
        return False


def _texto(img, s, org, color=BLANCO, escala=0.45, grosor=1):
    cv2.putText(img, s, org, cv2.FONT_HERSHEY_SIMPLEX, escala, (0, 0, 0), grosor + 2, cv2.LINE_AA)
    cv2.putText(img, s, org, cv2.FONT_HERSHEY_SIMPLEX, escala, color, grosor, cv2.LINE_AA)


def _dibujar_cara(frame, tid, cara, veredicto, mostrar_malla):
    x1, y1, x2, y2 = cara["bbox"]
    color, etiqueta = (VERDE, f"#{tid} pasa") if veredicto["pasa"] else (GRIS, f"#{tid} descartado: {veredicto['motivo']}")
    cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
    _texto(frame, etiqueta, (x1, max(14, y1 - 18)), color)
    _texto(
        frame,
        f"h={cara['altura_px']}px  cabeza={cara['yaw']:+.0f}  ojos={cara['yaw_ojos']:+.0f}"
        f"  MIRADA={cara['yaw_mirada']:+.0f}",
        (x1, min(frame.shape[0] - 6, y2 + 16)), color, 0.4,
    )
    if mostrar_malla:
        for p in cara["puntos"][::4]:
            cv2.circle(frame, (int(p[0]), int(p[1])), 1, (200, 200, 60), -1)
        cinco = cinco_puntos(cara["puntos"])
        if cinco is not None:
            for p in cinco:
                cv2.circle(frame, (int(p[0]), int(p[1])), 4, (255, 0, 255), -1)


def _dibujar_panel(frame, fps, cfg):
    y = 20
    lineas = [
        (f"FPS {fps:4.1f}  (banco de pruebas de scripts/probar_seguro_vision.py)", BLANCO),
        (f"min_altura={cfg['min_altura_cara_px']}px (1/2)  yaw_max={cfg['yaw_max_grados']:.0f} (3/4)"
         + (f"  mirada=ON signo={cfg['signo_mirada']:+d} (v/b)" if cfg["usar_mirada"]
            else "  mirada=OFF (v)"),
         BLANCO if cfg["usar_mirada"] else AMARILLO),
        ("m=malla  g=guardar en data/seguro.json  q=salir sin guardar", GRIS),
    ]
    for linea, color in lineas:
        _texto(frame, linea, (10, y), color)
        y += 18


def main() -> int:
    if not _asegurar_modelo():
        print("[fatal] Sin el modelo de detección no hay nada que probar.")
        return 1

    cfg = _leer_config()
    detector = Detector(_TASK_PATH, num_caras=4)
    tracker = Tracker()

    cap = cv2.VideoCapture(cfg["camara_indice"], cv2.CAP_DSHOW if sys.platform == "win32" else 0)
    if not cap.isOpened():
        print("\n[fatal] No se pudo abrir la webcam.")
        print("[fatal] En Windows la cámara es de uso EXCLUSIVO: cierra Zoom/Teams/Meet")
        print("[fatal] o cualquier pestaña que la tenga cogida, y vuelve a intentarlo.")
        return 1
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
    # Buffer de 1: la camara entrega a ~30fps y aqui se lee mucho mas lento, asi que
    # sin esto cap.read() devuelve el frame MAS VIEJO de la cola y se decide (y se
    # guarda la foto del intruso) sobre imagen retrasada. DSHOW puede ignorarlo, pero
    # pedirlo no cuesta nada y donde se respeta lo arregla del todo.
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

    mostrar_malla = False
    tiempos = deque(maxlen=30)
    t0 = time.time()

    print("\n=== Banco de pruebas de visión del Modo Seguro. Ventana enfocada para las teclas. ===")
    print("=== Gira la cabeza mirando a la pantalla: MIRADA debe acercarse a 0. Si se ===")
    print("=== aleja, pulsa 'b' para invertir signo_mirada. ===\n")

    while True:
        ok, frame = cap.read()
        if not ok:
            print("[error] Se perdió el frame (¿otra app te quitó la cámara?). Reintento...")
            time.sleep(0.5)
            continue

        ts_ms = int((time.time() - t0) * 1000)
        caras = detector.detectar(
            frame, ts_ms, signo_mirada=cfg["signo_mirada"], usar_mirada=cfg["usar_mirada"],
        )
        pares = tracker.actualizar(caras)
        for (tid, _track), cara in zip(pares, caras):
            veredicto = evaluar_cara(cara, cfg)
            _dibujar_cara(frame, tid, cara, veredicto, mostrar_malla)

        tiempos.append(time.time())
        fps = (len(tiempos) - 1) / (tiempos[-1] - tiempos[0]) if len(tiempos) > 1 else 0.0
        _dibujar_panel(frame, fps, cfg)

        cv2.imshow("Modo Seguro - banco de pruebas de vision", frame)
        k = cv2.waitKey(1) & 0xFF

        if k in (ord("q"), 27):
            break
        elif k == ord("g"):
            actual = _leer_config()
            for campo in (
                "min_altura_cara_px", "yaw_max_grados", "pitch_max_grados",
                "usar_mirada", "signo_mirada", "camara_indice",
            ):
                actual[campo] = cfg[campo]
            _escribir_config(actual)
            print(f"\n[guardado] Config escrita en data/seguro.json.")
            break
        elif k == ord("m"):
            mostrar_malla = not mostrar_malla
        elif k == ord("v"):
            cfg["usar_mirada"] = not cfg["usar_mirada"]
            print(f"[mirada] corrección por ojos: {'ON' if cfg['usar_mirada'] else 'OFF'}")
        elif k == ord("b"):
            cfg["signo_mirada"] *= -1
            print(f"[mirada] signo invertido: {cfg['signo_mirada']:+d}")
        elif k == ord("1"):
            cfg["min_altura_cara_px"] = max(20, cfg["min_altura_cara_px"] - 5)
        elif k == ord("2"):
            cfg["min_altura_cara_px"] += 5
        elif k == ord("3"):
            cfg["yaw_max_grados"] = max(5, cfg["yaw_max_grados"] - 5)
        elif k == ord("4"):
            cfg["yaw_max_grados"] += 5

    cap.release()
    detector.cerrar()
    cv2.destroyAllWindows()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
