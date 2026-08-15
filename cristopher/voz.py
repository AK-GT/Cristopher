"""Voz de CRISTOPHER (Fase 6): STT (faster-whisper) + TTS (Piper, 100% local/offline)
+ audio I/O.

- `transcribir(audio, sr)`: audio → texto (faster-whisper `small`, español, CPU).
- `hablar(texto)`: texto → voz por el altavoz con la voz Piper activa (ver
  `voz_actual_nombre`/`voz_elegir` en `tools/voz_tools.py`).
- `grabar_push_to_talk()`: graba del micrófono mientras se mantiene ESPACIO.

Catálogo de voces (`VOCES_CATALOGO`): cada entrada tiene un campo `fuente`. Las
oficiales se descargan del índice de rhasspy/piper-voices vía `piper.download_voices`;
las de comunidad llevan `url_onnx`/`url_config` propios y se descargan por HTTP directo
— ver `descargar_voz`.

Carga perezosa de modelos. Errores explícitos si falta micro/altavoz o un modelo (§1).
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import subprocess
import sys
import threading
import wave
from pathlib import Path
from typing import Any, Optional

import numpy as np

from cristopher.config import (
    DATA,
    ELEVENLABS_API_KEY,
    ELEVENLABS_CACHE_DIR,
    ELEVENLABS_MODEL,
    STT_LANG,
    STT_MODEL,
    VOICE_DIR,
    VOZ_DEFECTO,
    WHISPER_DIR,
)

SR = 16000  # tasa de muestreo para el micrófono / STT

_whisper = None
_piper_cache: dict[str, Any] = {}  # nombre de voz -> PiperVoice cargada

# Catálogo de voces Piper en español, de un único hablante. Las "oficial" están
# verificadas contra el índice de rhasspy/piper-voices (se excluye es_ES-sharvard por
# ser multi-hablante) y se descargan con `piper.download_voices`. Las "comunidad" son
# reentrenos/voces nuevas de autores independientes que NO están en ese índice — llevan
# `url_onnx`/`url_config` propios (descarga por HTTP directo, ver `descargar_voz`) y se
# verificaron una a una (num_speakers==1, formato de config compatible con PiperVoice)
# antes de añadirlas. Descartadas explícitamente tras esa verificación: voces con
# licencia ambigua, origen sintético/cruzado entre idiomas sin poder auditar la calidad
# acústica real, o que sugieren un clon de voz de un personaje con derechos de por medio.
VOCES_CATALOGO: list[dict[str, str]] = [
    {"nombre": "es_ES-davefx-medium", "region": "España", "calidad": "medium", "fuente": "oficial (rhasspy/piper-voices)"},
    {"nombre": "es_ES-carlfm-x_low", "region": "España", "calidad": "x_low", "fuente": "oficial (rhasspy/piper-voices)"},
    {"nombre": "es_ES-mls_9972-low", "region": "España", "calidad": "low", "fuente": "oficial (rhasspy/piper-voices)"},
    {"nombre": "es_ES-mls_10246-low", "region": "España", "calidad": "low", "fuente": "oficial (rhasspy/piper-voices)"},
    {"nombre": "es_MX-claude-high", "region": "México", "calidad": "high", "fuente": "oficial (rhasspy/piper-voices)"},
    {"nombre": "es_MX-ald-medium", "region": "México", "calidad": "medium", "fuente": "oficial (rhasspy/piper-voices)"},
    {"nombre": "es_AR-daniela-high", "region": "Argentina", "calidad": "high", "fuente": "oficial (rhasspy/piper-voices)"},
    {
        "nombre": "es_ES-carlfm-high",
        "region": "España",
        "calidad": "high",
        "fuente": "comunidad (friyin, HuggingFace) — reentreno desde cero del dataset público de carlfm01",
        "url_onnx": "https://huggingface.co/friyin/vits-piper-es_ES-carlfm-high/resolve/main/es_ES-carlfm-high.onnx",
        "url_config": "https://huggingface.co/friyin/vits-piper-es_ES-carlfm-high/resolve/main/es_ES-carlfm-high.onnx.json",
    },
    {
        "nombre": "es_MX-laura-high",
        "region": "México",
        "calidad": "high",
        "fuente": "comunidad (HirCoir, HuggingFace, CC-BY-NC-4.0)",
        "url_onnx": "https://huggingface.co/HirCoir/Piper-TTS-Laura/resolve/main/es_MX-laura-high.onnx",
        "url_config": "https://huggingface.co/HirCoir/Piper-TTS-Laura/resolve/main/es_MX-laura-high.onnx.json",
    },
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


def transcribir_archivo(ruta: str) -> str:
    """Transcribe un archivo de audio (wav/ogg/webm/mp3…) a texto en español.

    A diferencia de `transcribir()` (pensada para el ndarray ya decodificado de
    `grabar_push_to_talk`), aquí faster-whisper decodifica el archivo internamente
    (vía su dependencia `av`/ffmpeg) — útil para un adjunto subido en el HUD o una
    nota de voz de WhatsApp, sin depender de cómo se capturó."""
    model = _get_whisper()
    segs, _ = model.transcribe(ruta, language=STT_LANG, vad_filter=True)
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


def _leer_voz_json() -> dict:
    """Lee data/voz.json entero. Dict vacío si no existe o está corrupto."""
    if not _VOZ_PATH.exists():
        return {}
    try:
        data = json.loads(_VOZ_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def _escribir_voz_json(cambios: dict) -> None:
    """Actualiza solo las claves de `cambios`, preservando el resto."""
    with _VOZ_LOCK:
        DATA.mkdir(parents=True, exist_ok=True)
        data = _leer_voz_json()
        data.update(cambios)
        _VOZ_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _leer_voz_actual() -> str:
    nombre = _leer_voz_json().get("voz")
    return nombre if isinstance(nombre, str) and nombre in _nombres_catalogo() else VOZ_DEFECTO


def voz_actual_nombre() -> str:
    """Nombre de la voz Piper activa ahora mismo (persistida en data/voz.json)."""
    with _VOZ_LOCK:
        return _leer_voz_actual()


def _guardar_voz_actual(nombre: str) -> None:
    # También fija proveedor="piper": si estaba activo ElevenLabs, elegir una voz
    # Piper explícitamente debe volver a hablarse con Piper, no dejar la elección
    # sin efecto audible.
    _escribir_voz_json({"voz": nombre, "proveedor": "piper"})


def proveedor_actual() -> str:
    """'piper' o 'elevenlabs', según lo persistido en data/voz.json (por defecto
    'piper' si la clave no existe todavía, para compatibilidad con un voz.json viejo)."""
    with _VOZ_LOCK:
        p = _leer_voz_json().get("proveedor")
        return p if p in ("piper", "elevenlabs") else "piper"


def _leer_voz_elevenlabs_activa() -> tuple[Optional[str], Optional[str]]:
    """(voice_id, nombre) de la voz ElevenLabs activa, o (None, None) si no hay ninguna
    elegida todavía."""
    with _VOZ_LOCK:
        data = _leer_voz_json()
        return data.get("voz_elevenlabs_id"), data.get("voz_elevenlabs_nombre")


def _entrada_catalogo(nombre: str) -> Optional[dict[str, str]]:
    return next((v for v in VOCES_CATALOGO if v["nombre"] == nombre), None)


def _descargar_voz_oficial(nombre: str, destino: Path) -> None:
    """Descarga una voz del índice oficial de rhasspy/piper-voices."""
    try:
        subprocess.run(
            [sys.executable, "-m", "piper.download_voices", nombre, "--download-dir", str(destino)],
            check=True, capture_output=True, text=True, timeout=300,
        )
    except subprocess.CalledProcessError as exc:
        raise VozError(f"No pude descargar la voz '{nombre}': {exc.stderr.strip()}") from exc
    except subprocess.TimeoutExpired as exc:
        raise VozError(f"La descarga de la voz '{nombre}' tardó demasiado y se canceló.") from exc


def _descargar_archivo(url: str, destino: Path) -> None:
    try:
        import requests
        resp = requests.get(url, stream=True, timeout=(10, 300))
        resp.raise_for_status()
        with open(destino, "wb") as f:
            for chunk in resp.iter_content(chunk_size=1 << 16):
                f.write(chunk)
    except Exception as exc:
        destino.unlink(missing_ok=True)  # no dejar un archivo a medias
        raise VozError(f"No pude descargar {url}: {exc}") from exc


def _descargar_voz_url(nombre: str, entrada: dict[str, str], ruta: Path) -> None:
    """Descarga una voz de comunidad por HTTP directo (.onnx + su config, cualquiera
    que sea el nombre remoto del config — siempre se guarda como `{nombre}.onnx.json`,
    que es lo que espera Piper)."""
    _descargar_archivo(entrada["url_onnx"], ruta)
    try:
        _descargar_archivo(entrada["url_config"], ruta.with_suffix(".onnx.json"))
    except VozError:
        ruta.unlink(missing_ok=True)  # no dejar un .onnx huérfano sin su config
        raise


def descargar_voz(nombre: str) -> None:
    """Descarga una voz del catálogo si aún no está instalada. VozError si falla.
    Dos vías según `fuente`/`url_onnx` en VOCES_CATALOGO: el índice oficial de
    rhasspy/piper-voices, o una URL directa para voces de comunidad."""
    entrada = _entrada_catalogo(nombre)
    if entrada is None:
        raise VozError(f"'{nombre}' no está en el catálogo de voces conocidas.")
    if _instalada(nombre):
        return
    destino = VOICE_DIR / "piper"
    destino.mkdir(parents=True, exist_ok=True)
    if entrada.get("url_onnx") and entrada.get("url_config"):
        _descargar_voz_url(nombre, entrada, _ruta_voz(nombre))
    else:
        _descargar_voz_oficial(nombre, destino)


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


# --- TTS ElevenLabs (nube, opcional) -------------------------------------------
# Segundo backend de voz, en paralelo a Piper. Se activa solo si el usuario/agente
# elige una voz de ElevenLabs (`voz_elegir_elevenlabs` en tools/voz_tools.py), que
# persiste `proveedor: "elevenlabs"` en data/voz.json — `hablar()` es el único punto
# que decide con qué backend sintetizar (ver más abajo), así voz_repl.py y el HUD no
# necesitan saber que este backend existe.
ELEVEN_API_BASE = "https://api.elevenlabs.io/v1"
ELEVEN_SR = 24000  # pcm_24000: PCM crudo sin cabecera, 16 bits, mono

# Última búsqueda en la Voice Library pública (voz.py::elevenlabs_buscar_voces), clave
# nombre-en-minúsculas y voice_id -> {"voice_id", "owner_id", "nombre", ...}. Permite
# que voz_elegir_elevenlabs añada a la cuenta una voz recién encontrada, sin tenerla
# ya instalada — equivalente en espíritu a `_piper_cache`, pero de metadatos, no de
# modelo cargado.
_eleven_busqueda_cache: dict[str, dict] = {}


def _eleven_headers() -> dict[str, str]:
    if not ELEVENLABS_API_KEY:
        raise VozError(
            "No tengo ElevenLabs configurado: falta ELEVENLABS_API_KEY en .env "
            "(ver .env.example). Mientras tanto puedo seguir hablando con Piper."
        )
    return {"xi-api-key": ELEVENLABS_API_KEY}


def _eleven_mensaje_error(resp) -> str:
    """Extrae el mensaje de error del cuerpo JSON de ElevenLabs si lo hay (créditos
    agotados, rate limit, key inválida...); si no, recorta el texto crudo."""
    try:
        cuerpo = resp.json()
        detalle = cuerpo.get("detail") if isinstance(cuerpo, dict) else None
        if isinstance(detalle, dict):
            return str(detalle.get("message") or detalle.get("status") or detalle)
        if detalle:
            return str(detalle)
    except Exception:
        pass
    return (resp.text or "").strip()[:200] or f"HTTP {resp.status_code}"


def elevenlabs_listar_voces() -> list[dict[str, str]]:
    """Voces YA disponibles en la cuenta ElevenLabs del usuario (GET /v1/voices)."""
    import requests

    headers = _eleven_headers()
    try:
        resp = requests.get(f"{ELEVEN_API_BASE}/voices", headers=headers, timeout=15)
        if not resp.ok:
            raise VozError(f"ElevenLabs no listó las voces de tu cuenta: {_eleven_mensaje_error(resp)}")
        data = resp.json()
    except VozError:
        raise
    except Exception as exc:
        raise VozError(f"No pude contactar con ElevenLabs: {exc}") from exc
    return [
        {"voice_id": v.get("voice_id", ""), "nombre": v.get("name", ""), "categoria": v.get("category", "")}
        for v in data.get("voices", [])
    ]


def _eleven_shared_voices_request(params: dict) -> list[dict]:
    import requests

    headers = _eleven_headers()
    try:
        resp = requests.get(f"{ELEVEN_API_BASE}/shared-voices", headers=headers, params=params, timeout=15)
        if not resp.ok:
            raise VozError(f"ElevenLabs no pudo buscar voces: {_eleven_mensaje_error(resp)}")
        return resp.json().get("voices", [])
    except VozError:
        raise
    except Exception as exc:
        raise VozError(f"No pude contactar con ElevenLabs: {exc}") from exc


def elevenlabs_buscar_voces(query: str) -> list[dict[str, str]]:
    """Busca voces en la Voice Library PÚBLICA de ElevenLabs (no solo las de la
    cuenta). `query` debe ser un término corto en INGLÉS (acento, género, tono, uso —
    p. ej. "male mexican narrator") o un nombre: el índice de ElevenLabs solo etiqueta
    sus voces en inglés, una frase libre en español no encuentra nada (verificado).
    Sesga a español por defecto (`language=es`), ya que CRISTOPHER solo habla español.
    Si la búsqueda con `query` no da resultados, reintenta solo con el filtro de
    idioma para no devolver la lista vacía por un término que no matchea ningún
    campo. Cachea los resultados en memoria para que voz_elegir_elevenlabs pueda
    añadir a la cuenta una voz recién encontrada."""
    voces = _eleven_shared_voices_request({"search": query, "language": "es", "page_size": 10})
    if not voces:
        voces = _eleven_shared_voices_request({"language": "es", "page_size": 10})

    resultados = []
    for v in voces:
        entrada = {
            "voice_id": v.get("voice_id", ""),
            "owner_id": v.get("public_owner_id") or v.get("owner_id", ""),
            "nombre": v.get("name", ""),
            "idioma": v.get("language", ""),
            "descripcion": v.get("description", ""),
        }
        resultados.append(entrada)
        _eleven_busqueda_cache[entrada["nombre"].lower()] = entrada
        _eleven_busqueda_cache[entrada["voice_id"]] = entrada
    return resultados


def _eleven_anadir_voz(owner_id: str, voice_id: str, nombre: str) -> str:
    """Añade a la cuenta una voz de la Voice Library pública (POST
    /v1/voices/add/{owner_id}/{voice_id}). Devuelve el voice_id ya usable para
    síntesis (ElevenLabs conserva el mismo voice_id al añadirla)."""
    import requests

    headers = _eleven_headers()
    try:
        resp = requests.post(
            f"{ELEVEN_API_BASE}/voices/add/{owner_id}/{voice_id}",
            headers=headers,
            json={"new_name": nombre},
            timeout=30,
        )
        if not resp.ok:
            raise VozError(f"No pude añadir la voz '{nombre}' a tu cuenta: {_eleven_mensaje_error(resp)}")
        data = resp.json()
    except VozError:
        raise
    except Exception as exc:
        raise VozError(f"No pude contactar con ElevenLabs: {exc}") from exc
    return data.get("voice_id") or voice_id


def _eleven_resolver(nombre_o_id: str) -> tuple[str, str]:
    """Resuelve un nombre o voice_id a (voice_id, nombre) usable para síntesis.
    Orden: (1) ya está en la cuenta -> úsala directo; (2) apareció en la última
    búsqueda de la Voice Library -> añádela a la cuenta primero; (3) nada encontrado
    -> error explícito pidiendo buscar antes."""
    nombre_o_id = (nombre_o_id or "").strip()
    clave = nombre_o_id.lower()

    for v in elevenlabs_listar_voces():
        if v["voice_id"] == nombre_o_id or v["nombre"].lower() == clave:
            return v["voice_id"], v["nombre"]

    encontrada = _eleven_busqueda_cache.get(clave) or _eleven_busqueda_cache.get(nombre_o_id)
    if encontrada:
        voice_id = _eleven_anadir_voz(encontrada["owner_id"], encontrada["voice_id"], encontrada["nombre"])
        return voice_id, encontrada["nombre"]

    raise VozError(
        f"'{nombre_o_id}' no es una voz que conozca de ElevenLabs. Prueba primero "
        "voz_buscar_elevenlabs o voz_listar_elevenlabs."
    )


def _eleven_cache_ruta(texto: str, voice_id: str) -> Path:
    clave = f"{texto}|{voice_id}|{ELEVENLABS_MODEL}".encode("utf-8")
    return ELEVENLABS_CACHE_DIR / f"{hashlib.sha256(clave).hexdigest()}.pcm"


def _sintetizar_elevenlabs(texto: str, voice_id: str) -> tuple[np.ndarray, int]:
    """Devuelve (audio int16 mono, sample_rate) de `texto` con la voz ElevenLabs
    `voice_id`. Cachea en disco por hash de (texto, voz, modelo) — ElevenLabs cobra
    por carácter, a diferencia de Piper; sin invalidación (MVP, mismo criterio ya
    aceptado en el repo para data/whatsapp/media/)."""
    ruta = _eleven_cache_ruta(texto, voice_id)
    if ruta.exists():
        return np.frombuffer(ruta.read_bytes(), dtype=np.int16), ELEVEN_SR

    import requests

    headers = {**_eleven_headers(), "Accept": "audio/pcm", "Content-Type": "application/json"}
    try:
        resp = requests.post(
            f"{ELEVEN_API_BASE}/text-to-speech/{voice_id}",
            headers=headers,
            params={"output_format": f"pcm_{ELEVEN_SR}"},
            json={"text": texto, "model_id": ELEVENLABS_MODEL},
            timeout=60,
        )
        if not resp.ok:
            raise VozError(f"ElevenLabs no pudo sintetizar el audio: {_eleven_mensaje_error(resp)}")
        audio_bytes = resp.content
    except VozError:
        raise
    except Exception as exc:
        raise VozError(f"No pude contactar con ElevenLabs: {exc}") from exc

    ELEVENLABS_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    ruta.write_bytes(audio_bytes)
    return np.frombuffer(audio_bytes, dtype=np.int16), ELEVEN_SR


# --- Limpieza de Markdown antes de hablar --------------------------------------
# Las respuestas del agente vienen formateadas en Markdown (el HUD las renderiza así),
# pero un TTS no debe LEER los símbolos de marcado en voz alta — solo el texto que
# envuelven. Regex simple, no un parser de Markdown completo (no hace falta: MVP,
# la salida es siempre texto de un LLM, nunca Markdown adversarial).
_MD_FENCE = re.compile(r"```.*?```", re.DOTALL)
_MD_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_MD_INLINE_CODE = re.compile(r"`([^`]*)`")
_MD_STRIKE = re.compile(r"~~(.*?)~~")
_MD_HEADER = re.compile(r"^\s{0,3}#{1,6}\s*", re.MULTILINE)
_MD_BULLET = re.compile(r"^\s*[-*+]\s+", re.MULTILINE)
_MD_BLOCKQUOTE = re.compile(r"^\s*>\s?", re.MULTILINE)
_MD_EMPASIS = re.compile(r"(\*\*\*|\*\*|\*|___|__|_)")
_MD_ESPACIOS = re.compile(r"[ \t]+")


def _limpiar_markdown_para_voz(texto: str) -> str:
    """Quita marcado Markdown (negrita/cursiva, listas, encabezados, enlaces, código,
    citas) para que Piper no lea asteriscos, guiones bajos ni símbolos similares en
    voz alta — se lee solo el texto que envuelven."""
    texto = _MD_FENCE.sub(" ", texto)
    texto = _MD_LINK.sub(r"\1", texto)
    texto = _MD_INLINE_CODE.sub(r"\1", texto)
    texto = _MD_STRIKE.sub(r"\1", texto)
    texto = _MD_HEADER.sub("", texto)
    texto = _MD_BULLET.sub("", texto)
    texto = _MD_BLOCKQUOTE.sub("", texto)
    texto = _MD_EMPASIS.sub("", texto)
    texto = _MD_ESPACIOS.sub(" ", texto)
    return texto.strip()


# --- Interrupción de la reproducción (barge-in) --------------------------------
# La síntesis de Piper (CPU) puede tardar más que tiempo real (verificado: ~3s para
# sintetizar una frase de ~4s de audio) — `sd.stop()` solo puede cortar la
# REPRODUCCIÓN, no acelerar una síntesis ya en curso. `_detener_evento` es la
# bandera cooperativa que cubre ese hueco: si la interrupción llegó mientras se
# sintetizaba, `hablar()` se entera al terminar y se salta la reproducción entera
# en vez de reproducir igualmente un audio ya obsoleto.
_detener_evento = threading.Event()


def detener_habla() -> None:
    """Corta de inmediato la reproducción de voz en curso, si la hay (barge-in):
    un mensaje/instrucción nueva puede interrumpir lo que CRISTOPHER está diciendo
    en vez de esperar a que termine. `sd.stop()` desbloquea cualquier `sd.wait()`
    en curso en OTRO hilo (así lo llama el hilo HTTP del HUD mientras el worker
    está bloqueado reproduciendo) y es un no-op seguro si no había nada sonando."""
    _detener_evento.set()
    try:
        import sounddevice as sd
        sd.stop()
    except Exception:
        pass


def hablar(texto: str) -> None:
    """Sintetiza y reproduce `texto` por el altavoz con la voz activa (Piper local o
    ElevenLabs en la nube, según `proveedor_actual()`). Limpia el Markdown antes de
    sintetizar (§ TTS) y puede interrumpirse desde otro hilo con `detener_habla()` —
    tanto durante la síntesis (se salta la reproducción entera) como durante la
    reproducción misma. Único punto de integración de ambos backends: voz_repl.py y
    el HUD llaman esto sin saber cuál está activo."""
    texto = _limpiar_markdown_para_voz(texto or "")
    if not texto:
        return
    _detener_evento.clear()
    if proveedor_actual() == "elevenlabs":
        voice_id, _ = _leer_voz_elevenlabs_activa()
        if not voice_id:
            raise VozError(
                "Modo ElevenLabs activo pero sin voz elegida todavía. Usa "
                "voz_elegir_elevenlabs para elegir una."
            )
        try:
            data, sr = _sintetizar_elevenlabs(texto, voice_id)
        except VozError as exc:
            raise VozError(
                f"No pude hablar con ElevenLabs ({exc}). Dime si quieres que use "
                "Piper como respaldo (lo hago con voz_usar_piper) o prefieres que "
                "me quede en silencio hasta que se solucione (desactivar_modo_voz)."
            ) from exc
    else:
        data, sr = sintetizar(texto)
    if _detener_evento.is_set():
        return  # se pidió cortar mientras se sintetizaba: no reproducir ya un audio obsoleto
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
