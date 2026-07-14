"""Voz de CRISTOPHER (Fase 6): STT (faster-whisper) + TTS (Piper) + audio I/O.

- `transcribir(audio, sr)`: audio → texto (faster-whisper `small`, español, CPU).
- `hablar(texto)`: texto → voz por el altavoz (Piper).
- `grabar_push_to_talk()`: graba del micrófono mientras se mantiene ESPACIO.

Carga perezosa de modelos. Errores explícitos si falta micro/altavoz o un modelo (§1).
"""

from __future__ import annotations

import io
import json
import subprocess
import sys
import threading
import wave
from pathlib import Path
from typing import Any, Optional

import numpy as np

from cristopher.config import DATA, STT_LANG, STT_MODEL, VOICE_DIR, VOZ_DEFECTO, WHISPER_DIR

SR = 16000  # tasa de muestreo para el micrófono / STT

_whisper = None
_piper_cache: dict[str, Any] = {}  # nombre de voz -> PiperVoice cargada

# Catálogo de voces Piper en español, verificado contra rhasspy/piper-voices (solo
# voces de un único hablante; se excluye es_ES-sharvard por ser multi-hablante).
VOCES_CATALOGO: list[dict[str, str]] = [
    {"nombre": "es_ES-davefx-medium", "region": "España", "calidad": "medium"},
    {"nombre": "es_ES-carlfm-x_low", "region": "España", "calidad": "x_low"},
    {"nombre": "es_ES-mls_9972-low", "region": "España", "calidad": "low"},
    {"nombre": "es_ES-mls_10246-low", "region": "España", "calidad": "low"},
    {"nombre": "es_MX-claude-high", "region": "México", "calidad": "high"},
    {"nombre": "es_MX-ald-medium", "region": "México", "calidad": "medium"},
    {"nombre": "es_AR-daniela-high", "region": "Argentina", "calidad": "high"},
]

_VOZ_PATH = DATA / "voz.json"
_VOZ_LOCK = threading.Lock()


class VozError(RuntimeError):
    """Fallo de audio o de un modelo de voz."""


# --- STT ----------------------------------------------------------------------
def _get_whisper():
    global _whisper
    if _whisper is None:
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise VozError("Falta faster-whisper: pip install faster-whisper") from exc
        WHISPER_DIR.mkdir(parents=True, exist_ok=True)
        _whisper = WhisperModel(
            STT_MODEL, device="cpu", compute_type="int8",
            download_root=str(WHISPER_DIR),
        )
    return _whisper


def transcribir(audio: np.ndarray, sr: int = SR) -> str:
    """Transcribe audio (float32 mono) a texto en español."""
    model = _get_whisper()
    if audio.dtype != np.float32:
        audio = audio.astype(np.float32)
    segs, _ = model.transcribe(audio, language=STT_LANG, vad_filter=True)
    return " ".join(s.text for s in segs).strip()


# --- TTS ----------------------------------------------------------------------
def _nombres_catalogo() -> set[str]:
    return {v["nombre"] for v in VOCES_CATALOGO}


def _ruta_voz(nombre: str) -> Path:
    return VOICE_DIR / "piper" / f"{nombre}.onnx"


def _instalada(nombre: str) -> bool:
    """Una voz cuenta como instalada solo si están el modelo .onnx Y su config
    .onnx.json (Piper necesita ambos; una descarga interrumpida a medias puede dejar
    solo el .onnx)."""
    ruta = _ruta_voz(nombre)
    return ruta.exists() and ruta.with_suffix(".onnx.json").exists()


def voces_instaladas() -> list[dict[str, str]]:
    """Voces del catálogo completamente instaladas (modelo + config), en orden de
    catálogo."""
    return [v for v in VOCES_CATALOGO if _instalada(v["nombre"])]


def _leer_voz_actual() -> str:
    if not _VOZ_PATH.exists():
        return VOZ_DEFECTO
    try:
        data = json.loads(_VOZ_PATH.read_text(encoding="utf-8"))
        nombre = data.get("voz") if isinstance(data, dict) else None
        return nombre if isinstance(nombre, str) and nombre in _nombres_catalogo() else VOZ_DEFECTO
    except (json.JSONDecodeError, OSError):
        return VOZ_DEFECTO


def voz_actual_nombre() -> str:
    """Nombre de la voz Piper activa ahora mismo (persistida en data/voz.json)."""
    with _VOZ_LOCK:
        return _leer_voz_actual()


def _guardar_voz_actual(nombre: str) -> None:
    with _VOZ_LOCK:
        DATA.mkdir(parents=True, exist_ok=True)
        _VOZ_PATH.write_text(json.dumps({"voz": nombre}, ensure_ascii=False, indent=2), encoding="utf-8")


def descargar_voz(nombre: str) -> None:
    """Descarga una voz del catálogo si aún no está instalada. VozError si falla."""
    if nombre not in _nombres_catalogo():
        raise VozError(f"'{nombre}' no está en el catálogo de voces conocidas.")
    if _instalada(nombre):
        return
    destino = VOICE_DIR / "piper"
    destino.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(
            [sys.executable, "-m", "piper.download_voices", nombre, "--download-dir", str(destino)],
            check=True, capture_output=True, text=True, timeout=300,
        )
    except subprocess.CalledProcessError as exc:
        raise VozError(f"No pude descargar la voz '{nombre}': {exc.stderr.strip()}") from exc
    except subprocess.TimeoutExpired as exc:
        raise VozError(f"La descarga de la voz '{nombre}' tardó demasiado y se canceló.") from exc


def _get_piper(nombre: Optional[str] = None):
    nombre = nombre or voz_actual_nombre()
    if nombre not in _piper_cache:
        ruta = _ruta_voz(nombre)
        if not ruta.exists():
            raise VozError(
                f"Falta la voz Piper '{nombre}' en {ruta}. Descárgala con: python -m "
                f"piper.download_voices {nombre} --download-dir data/voice/piper"
            )
        try:
            from piper import PiperVoice
        except ImportError as exc:
            raise VozError("Falta piper-tts: pip install piper-tts") from exc
        _piper_cache[nombre] = PiperVoice.load(str(ruta))
    return _piper_cache[nombre]


def sintetizar(texto: str) -> tuple[np.ndarray, int]:
    """Devuelve (audio int16 mono, sample_rate) de la voz sintetizada."""
    voice = _get_piper()
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        voice.synthesize_wav(texto, wf)
    buf.seek(0)
    with wave.open(buf, "rb") as wf:
        sr = wf.getframerate()
        data = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16)
    return data, sr


def hablar(texto: str) -> None:
    """Sintetiza y reproduce el texto por el altavoz."""
    texto = (texto or "").strip()
    if not texto:
        return
    data, sr = sintetizar(texto)
    try:
        import sounddevice as sd
    except ImportError as exc:
        raise VozError("Falta sounddevice: pip install sounddevice") from exc
    try:
        sd.play(data, sr)
        sd.wait()
    except Exception as exc:
        raise VozError(f"No pude reproducir audio (¿altavoz?): {exc}") from exc


# --- Micrófono (push-to-talk) -------------------------------------------------
def grabar_push_to_talk(tecla: str = "space") -> Optional[np.ndarray]:
    """Graba del micrófono mientras se mantiene la tecla (por defecto ESPACIO).

    Espera a que se pulse la tecla, graba hasta que se suelta, y devuelve el audio
    float32 mono a 16 kHz (o None si no se grabó nada)."""
    try:
        import sounddevice as sd
        from pynput import keyboard
    except ImportError as exc:
        raise VozError("Faltan sounddevice/pynput para el micrófono.") from exc

    key = keyboard.Key.space if tecla == "space" else keyboard.KeyCode.from_char(tecla)

    # 1) Esperar a que se pulse la tecla.
    def _on_press(k):
        if k == key:
            return False  # detiene el listener

    with keyboard.Listener(on_press=_on_press) as l:
        l.join()

    # 2) Grabar mientras la tecla siga pulsada.
    soltada = {"v": False}

    def _on_release(k):
        if k == key:
            soltada["v"] = True
            return False

    listener = keyboard.Listener(on_release=_on_release)
    listener.start()

    frames = []
    try:
        with sd.InputStream(samplerate=SR, channels=1, dtype="float32") as stream:
            while not soltada["v"]:
                data, _ = stream.read(int(SR * 0.1))
                frames.append(data.copy())
    except Exception as exc:
        listener.stop()
        raise VozError(f"No pude grabar del micrófono: {exc}") from exc
    listener.stop()

    if not frames:
        return None
    return np.concatenate(frames, axis=0).flatten()
