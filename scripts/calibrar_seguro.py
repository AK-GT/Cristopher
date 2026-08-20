#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Calibra los umbrales de identidad del Modo Seguro con FAR MEDIDA, no supuesta.

- Genuinos: hold-one-out sobre las fotos de enrolamiento (cada foto contra el máximo
  de las DEMÁS fotos de esa misma persona).
- Impostores: una muestra de identidades de LFW (descargado una sola vez a
  data/seguro/lfw/) contra la galería completa, quedándose con el máximo por imagen.
- Umbrales: `umbral_alto` (a partir de aquí eres tú) sale del percentil 1 de los
  GENUINOS — tiene que reconocerte en tu peor foto; `umbral_bajo` (por debajo, nadie)
  sale del percentil 99.9 de los IMPOSTORES. Entre ambos, zona de votación.
- FAR y FRR se MIDEN con el umbral que se va a escribir, no se dan por supuestos por
  venir de un percentil: si la falsa aceptación pasa del 0,1%, no se escribe nada.
- Cordura: si `umbral_bajo > umbral_alto` hay impostores que puntúan más que tus peores
  fotos -> falla ruidosamente. Nunca deja umbrales que no separan.
- Si pasa: escribe data/seguro/calibracion.json (diagnóstico/registro) Y actualiza
  umbral_alto/umbral_bajo en data/seguro.json (los valores que seguro.py usa de
  verdad en producción).
- Imprime la matriz de parecidos entre personas de la galería.

Uso:
    python -m scripts.calibrar_seguro
"""

from __future__ import annotations

import json
import random
import sys
import tarfile
import time
from pathlib import Path

import numpy as np

from cristopher.seguro import _CALIBRACION_JSON, _GALERIA_NPZ, _leer_config, _escribir_config
from cristopher.seguro_galeria import AURAFACE_PATH, Embedder, Galeria, descargar_modelo_embedding
from cristopher.seguro_vision import Detector

_LFW_DIR = _GALERIA_NPZ.parent / "lfw"
_LFW_TGZ = _LFW_DIR / "lfw.tgz"
# El original (http://vis-www.cs.umass.edu/lfw/lfw.tgz) MURIÓ: UMass retiró el subdominio
# entero y hoy ni siquiera resuelve por DNS (www.cs.umass.edu sí). Se usa el espejo de
# figshare que mantiene scikit-learn para su fetch_lfw_people — mismo lfw.tgz de siempre
# (180.566.744 bytes, misma estructura lfw/<Persona>/*.jpg) y además por HTTPS, que aquí
# importa más de lo que parece: de este archivo salen los umbrales de seguridad, así que
# bajarlo en claro era dejar la puerta a que alguien los envenenara por el camino.
_LFW_URL = "https://ndownloader.figshare.com/files/5976018"
_LFW_IMPOSTORES_MUESTRA = 2000

_TASK_PATH = _GALERIA_NPZ.parent / "modelos" / "face_landmarker.task"
_TASK_URL = (
    "https://storage.googleapis.com/mediapipe-models/face_landmarker/"
    "face_landmarker/float16/1/face_landmarker.task"
)


def _fatal(msg: str) -> "None":
    print(f"\n[fatal] {msg}")
    sys.exit(1)


def _asegurar_modelo_deteccion() -> None:
    if _TASK_PATH.exists():
        return
    print("[modelos] Descargando face_landmarker.task ...")
    import shutil
    import urllib.request

    _TASK_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = _TASK_PATH.with_suffix(_TASK_PATH.suffix + ".parcial")
    try:
        urllib.request.urlretrieve(_TASK_URL, tmp)
        shutil.move(str(tmp), str(_TASK_PATH))
    except Exception as exc:
        tmp.unlink(missing_ok=True)
        _fatal(f"no pude descargar el modelo de detección: {exc}")


def _asegurar_lfw() -> Path:
    """Descarga y extrae LFW una sola vez. Devuelve la carpeta con las identidades
    (una subcarpeta por persona, con sus fotos dentro)."""
    extraido = _LFW_DIR / "lfw"
    if extraido.is_dir() and any(extraido.iterdir()):
        return extraido

    print(f"[lfw] Descargando {_LFW_URL} (dataset público, ~170MB, una sola vez) ...")
    _LFW_DIR.mkdir(parents=True, exist_ok=True)
    try:
        import requests

        with requests.get(_LFW_URL, stream=True, timeout=60) as r:
            r.raise_for_status()
            tmp = _LFW_TGZ.with_suffix(".parcial")
            with open(tmp, "wb") as f:
                descargado = 0
                for chunk in r.iter_content(chunk_size=1024 * 1024):
                    f.write(chunk)
                    descargado += len(chunk)
                    print(f"\r[lfw] {descargado / 1_000_000:.0f} MB", end="", flush=True)
            print()
            tmp.rename(_LFW_TGZ)
    except Exception as exc:
        _fatal(f"no pude descargar LFW: {exc}")

    print("[lfw] Extrayendo ...")
    try:
        with tarfile.open(_LFW_TGZ) as tar:
            # filter="data" (Python 3.12; backport en 3.11.4) impide que una entrada del
            # tar escriba fuera del destino con rutas tipo ../../. El dataset es de fiar,
            # pero extraer 13.000 archivos de un .tgz bajado de internet sin sanear rutas
            # es justo el patrón que no debe quedar escrito en el repo.
            try:
                tar.extractall(_LFW_DIR, filter="data")
            except TypeError:
                tar.extractall(_LFW_DIR)   # Python viejo: sin filtro disponible
    except Exception as exc:
        _fatal(f"no pude extraer LFW: {exc}")

    if not extraido.is_dir():
        _fatal(f"LFW se extrajo pero no encuentro la carpeta esperada: {extraido}")
    return extraido


def _distribucion_genuinos(galeria: Galeria) -> np.ndarray:
    """Hold-one-out: cada foto de cada persona contra el MÁXIMO de las demás fotos de
    esa misma persona."""
    sims: list[float] = []
    for nombre, n in galeria.resumen():
        if n < 2:
            _fatal(
                f"«{nombre}» solo tiene {n} foto(s): hacen falta al menos 2 para "
                "calibrar (hold-one-out deja una fuera y compara contra el resto). "
                "Enrola más fotos de esta persona con seguro_enrolar."
            )
        muestras = galeria.muestras(nombre)  # ya cargadas en memoria, sin releer el .npz
        for i in range(n):
            resto = np.delete(muestras, i, axis=0)
            sim = float(np.max(resto @ muestras[i]))
            sims.append(sim)
    return np.array(sims, dtype=np.float64)


def _distribucion_impostores(galeria: Galeria, detector: Detector, embedder: Embedder) -> "tuple[np.ndarray, int]":
    """Máximo de similitud contra TODA la galería, por cada identidad de LFW
    muestreada. Devuelve (similitudes, nº de imágenes descartadas por no dar una
    cara alineable)."""
    carpeta = _asegurar_lfw()
    identidades = sorted(p for p in carpeta.iterdir() if p.is_dir())
    if not identidades:
        _fatal(f"No encontré ninguna identidad dentro de {carpeta}")

    random.seed(0)  # muestra reproducible entre corridas
    muestra = random.sample(identidades, k=min(_LFW_IMPOSTORES_MUESTRA, len(identidades)))

    sims: list[float] = []
    descartadas = 0
    ts_ms = 0
    for i, persona_dir in enumerate(muestra):
        fotos = sorted(persona_dir.glob("*.jpg"))
        if not fotos:
            descartadas += 1
            continue
        import cv2
        frame = cv2.imread(str(fotos[0]))
        if frame is None:
            descartadas += 1
            continue

        ts_ms += 1  # RunningMode.VIDEO exige timestamps crecientes; no hace falta que
                    # sean reales para procesar imágenes sueltas en lote.
        caras = detector.detectar(frame, ts_ms)
        if not caras:
            descartadas += 1
            continue
        cara = max(caras, key=lambda c: c["altura_px"])
        crop = embedder.alinear(frame, cara["puntos"])
        if crop is None:
            descartadas += 1
            continue

        emb = embedder.embedding(crop)
        _, sim = galeria.buscar(emb)
        sims.append(sim)

        if (i + 1) % 200 == 0:
            print(f"\r[lfw] procesadas {i + 1}/{len(muestra)}", end="", flush=True)
    print()
    return np.array(sims, dtype=np.float64), descartadas


def main() -> int:
    galeria = Galeria(_GALERIA_NPZ)
    if galeria.vacia():
        _fatal("La galería está vacía: enrola al menos a una persona con seguro_enrolar antes de calibrar.")

    _asegurar_modelo_deteccion()
    if not AURAFACE_PATH.exists():
        print("[modelos] Descargando modelo de identidad (AuraFace, ~250MB) ...")
        if not descargar_modelo_embedding():
            _fatal("no pude descargar el modelo de identidad (AuraFace).")

    detector = Detector(_TASK_PATH, num_caras=1)
    embedder = Embedder(AURAFACE_PATH)

    print("[calibrar] Calculando distribución de similitudes GENUINAS (hold-one-out) ...")
    genuinos = _distribucion_genuinos(galeria)
    print(f"[calibrar] {len(genuinos)} comparaciones genuinas — "
          f"media {genuinos.mean():.3f}, min {genuinos.min():.3f}, max {genuinos.max():.3f}")

    print("[calibrar] Calculando distribución de similitudes de IMPOSTORES (LFW) ...")
    impostores, descartadas = _distribucion_impostores(galeria, detector, embedder)
    if len(impostores) == 0:
        _fatal("Ninguna imagen de LFW dio una cara alineable — no puedo medir el FAR.")
    print(f"[calibrar] {len(impostores)} impostores válidos ({descartadas} descartados "
          f"por no dar una cara alineable) — media {impostores.mean():.3f}, "
          f"max {impostores.max():.3f}")

    # Cada umbral sale de la distribución que lo define, y no al revés (estaban
    # cruzados: T_alto salía de los impostores y T_bajo de los genuinos, lo que hacía
    # que t_bajo > t_alto EXACTAMENTE cuando la separación era perfecta — el script
    # abortaba diciendo "se solapan" justo en el caso bueno).
    #
    # Semántica que espera seguro_galeria.decidir_identidad:
    #   sim >= umbral_alto  -> eres tú          (lo fija el peor de TUS genuinos)
    #   sim <= umbral_bajo  -> desconocido      (lo fija el mejor de los IMPOSTORES)
    #   en medio            -> zona de votación
    t_alto = float(np.percentile(genuinos, 1))         # reconocerte en tu peor foto
    t_bajo = float(np.percentile(impostores, 99.9))    # por encima ya no se puede negar

    # FAR MEDIDA sobre los impostores reales con el umbral que se va a escribir — es el
    # dato que da sentido a todo esto, y hasta ahora se daba por supuesto por venir de
    # un percentil. Si el umbral deja pasar a más de 1 de cada 1000, no vale.
    far = float((impostores >= t_alto).mean())
    frr = float((genuinos < t_alto).mean())

    print(f"\n[calibrar] umbral_alto (percentil 1 de genuinos)     = {t_alto:.4f}")
    print(f"[calibrar] umbral_bajo (percentil 99.9 de impostores) = {t_bajo:.4f}")
    print(f"[calibrar] FAR medida con ese umbral_alto: {far * 100:.3f}% "
          f"({int(far * len(impostores))}/{len(impostores)} impostores colados)")
    print(f"[calibrar] FRR sobre tus propias fotos:    {frr * 100:.3f}%")

    if t_bajo > t_alto:
        _fatal(
            "umbral_bajo > umbral_alto: las distribuciones de genuinos e impostores se "
            "solapan (hay impostores que puntúan más que tus peores fotos). No escribo "
            "ningún umbral. Reenrola con más fotos variadas (distinta luz, pose, gafas) "
            "o revisa si hay personas muy parecidas entre sí en la galería."
        )
    if far > 0.001:
        _fatal(
            f"la falsa aceptación medida sería del {far * 100:.2f}% (máximo admitido "
            "0,1%): con este enrolamiento, gente que no eres tú entraría como "
            "autorizada. No escribo ningún umbral. Enrola más fotos y vuelve a calibrar."
        )

    calib = {
        "fecha": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "modelo": "auraface-v1",
        "tam_galeria": len(galeria.resumen()),
        "t_alto": t_alto,
        "t_bajo": t_bajo,
        "far_medida": far,
        "frr_medida": frr,
        "genuinos": {"n": len(genuinos), "sims": genuinos.tolist()},
        "impostores": {"n": len(impostores), "sims": impostores.tolist()},
    }
    _CALIBRACION_JSON.parent.mkdir(parents=True, exist_ok=True)
    _CALIBRACION_JSON.write_text(json.dumps(calib, ensure_ascii=False, indent=2), encoding="utf-8")

    cfg = _leer_config()
    cfg["umbral_alto"] = t_alto
    cfg["umbral_bajo"] = t_bajo
    _escribir_config(cfg)

    print(f"\n[calibrar] Escrito {_CALIBRACION_JSON}")
    print("[calibrar] umbral_alto/umbral_bajo actualizados en data/seguro.json")

    lineas = galeria.matriz_parecidos(t_alto)
    if lineas:
        print("\n[calibrar] Parecidos entre personas de la galería:")
        print("\n".join(lineas))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
