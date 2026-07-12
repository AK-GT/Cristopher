"""Voz de CRISTOPHER (Fase 6): STT (faster-whisper) + TTS (Piper) + audio I/O.

- `transcribir(audio, sr)`: audio → texto (faster-whisper `small`, español, CPU).
- `hablar(texto)`: texto → voz por el altavoz (Piper).
- `grabar_push_to_talk()`: graba del micrófono mientras se mantiene ESPACIO.

Carga perezosa de modelos. Errores explícitos si falta micro/altavoz o un modelo (§1).
"""

from __future__ import annotations

import io
import wave
from typing import Optional

import numpy as np

from cristopher.config import PIPER_VOICE, STT_LANG, STT_MODEL, WHISPER_DIR

SR = 16000  # tasa de muestreo para el micrófono / STT

_whisper = None
_piper = None


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
def _get_piper():
    global _piper
    if _piper is None:
        if not PIPER_VOICE.exists():
            raise VozError(
                f"Falta la voz Piper en {PIPER_VOICE}. Descárgala con: python -m "
                "piper.download_voices es_ES-davefx-medium --download-dir data/voice/piper"
            )
        try:
            from piper import PiperVoice
        except ImportError as exc:
            raise VozError("Falta piper-tts: pip install piper-tts") from exc
        _piper = PiperVoice.load(str(PIPER_VOICE))
    return _piper


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
