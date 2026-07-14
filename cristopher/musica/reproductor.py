"""Servicio reproductor persistente del módulo de música (Tanda A).

El "cerebro musical": un singleton que vive en segundo plano y mantiene el estado de la
reproducción (qué suena, pausa, volumen) y una COLA de pistas con índice actual. Al
terminar una pista, AVANZA SOLA a la siguiente. La reproducción es NO BLOQUEANTE: VLC
suena en sus propios hilos nativos, así CRISTOPHER sigue atendiendo otras tareas.

Diseño de hilos:
- `python-vlc`/libvlc es thread-safe para las llamadas de control, así que pausar,
  volumen, siguiente, etc. se llaman directamente bajo un `Lock` desde el hilo que sea.
- REGLA DURA: no se puede llamar a libvlc DENTRO de un callback de evento de VLC (puede
  bloquear). Por eso el evento `MediaPlayerEndReached` solo SEÑALIZA un `threading.Event`;
  un hilo daemon aparte (`_bucle_avance`) hace el auto-avance fuera del callback.
- La resolución lenta (yt-dlp, red) se hace FUERA del lock; solo `set_media`+`play` van
  dentro, para no bloquear el control mientras se resuelve.

Degrada con elegancia (§2/§8): si falta VLC (libvlc) o una pista no se puede resolver, se
lanza `MusicaError` con mensaje claro; el agente nunca crashea.
"""

from __future__ import annotations

import os
import threading
from typing import Optional

from cristopher.config import MUSICA_VOLUMEN, VLC_DIR
from cristopher.musica.resolver import MusicaError, Track, resolver


def _preparar_libvlc() -> None:
    """Si VLC no está instalado en el sistema, apunta python-vlc a un VLC PORTABLE en
    VLC_DIR (data/vlc). Debe correr ANTES de `import vlc`. Si no hay portable, no hace
    nada: se confía en la instalación del sistema (y si tampoco está, el import falla con
    un mensaje claro)."""
    if os.environ.get("PYTHON_VLC_LIB_PATH"):
        return  # ya configurado (o el usuario lo fijó a mano)
    try:
        if not VLC_DIR.exists():
            return
        matches = list(VLC_DIR.rglob("libvlc.dll"))
    except Exception:
        return
    if not matches:
        return
    lib = matches[0]
    libdir = lib.parent
    os.environ["PYTHON_VLC_LIB_PATH"] = str(lib)
    plugins = libdir / "plugins"
    if plugins.is_dir():
        os.environ["PYTHON_VLC_MODULE_PATH"] = str(plugins)
    try:
        # Para que libvlc.dll resuelva su dependencia libvlccore.dll (misma carpeta).
        os.add_dll_directory(str(libdir))
    except Exception:
        pass


def _descr(track: Track) -> str:
    """Descripción corta de una pista para mensajes al usuario."""
    titulo = track.get("titulo") or track.get("consulta") or "(desconocido)"
    artista = track.get("artista") or ""
    fuente = track.get("fuente") or ""
    base = f"{titulo} — {artista}" if artista else titulo
    return f"{base} ({fuente})" if fuente else base


class Reproductor:
    """Reproductor VLC persistente con cola y auto-avance."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._instance = None          # vlc.Instance (perezoso)
        self._player = None            # vlc.MediaPlayer
        self._cola: list[Track] = []
        self._indice: int = -1         # -1 = nada cargado
        self._pausado: bool = False
        self._parado: bool = True      # True = no hay reproducción activa
        self._volumen: int = max(0, min(100, MUSICA_VOLUMEN))
        # Hook opcional que se invoca al arrancar una pista (lo cablea get_reproductor a
        # la biblioteca para el historial). Guardado: nunca debe romper la reproducción.
        self._on_track_start = None
        # Auto-avance: el callback de fin de pista señaliza; el hilo daemon reacciona.
        self._fin = threading.Event()
        self._avance = threading.Thread(target=self._bucle_avance, daemon=True)
        self._avance.start()

    # --- VLC perezoso ----------------------------------------------------------
    def _ensure_vlc(self) -> None:
        """Crea la instancia y el player la primera vez. Error claro si falta libvlc."""
        if self._player is not None:
            return
        _preparar_libvlc()  # apunta a VLC portable si no hay instalación de sistema
        try:
            import vlc  # noqa: F401
        except Exception as exc:  # ImportError o fallo al cargar libvlc
            raise MusicaError(
                "No encuentro VLC. Instala VLC de 64 bits (videolan.org) y "
                "`pip install python-vlc`. VLC debe coincidir en bits con Python."
            ) from exc
        try:
            # --no-video: solo audio, sin abrir ventana de vídeo para streams web.
            self._instance = vlc.Instance("--no-video", "--quiet")
            self._player = self._instance.media_player_new()
            self._player.event_manager().event_attach(
                vlc.EventType.MediaPlayerEndReached, self._on_fin
            )
        except Exception as exc:
            raise MusicaError(f"No pude inicializar VLC: {exc}") from exc

    def _on_fin(self, _event) -> None:
        """Callback de VLC: SOLO señaliza (prohibido llamar a libvlc aquí)."""
        self._fin.set()

    def _bucle_avance(self) -> None:
        """Hilo daemon: al terminar una pista, avanza sola a la siguiente de la cola."""
        while True:
            self._fin.wait()
            self._fin.clear()
            try:
                self._auto_siguiente()
            except Exception:
                pass  # el hilo de avance nunca debe morir por un fallo puntual

    # --- Helpers bajo lock -----------------------------------------------------
    def _sonar_locked(self, track: Track) -> None:
        """Carga la pista en VLC y la reproduce. Asume lock adquirido y track resuelto."""
        media = self._instance.media_new(track["ubicacion"])
        self._player.set_media(media)
        self._player.play()
        # El volumen a veces no "pega" hasta que hay media cargada: lo fijamos aquí.
        self._player.audio_set_volume(self._volumen)
        self._pausado = False
        self._parado = False
        # Historial (best-effort): sqlite local rápido; nunca rompe la reproducción.
        if self._on_track_start is not None:
            try:
                self._on_track_start(track)
            except Exception:
                pass

    def _detener_locked(self) -> None:
        if self._player is not None:
            self._player.stop()
        self._parado = True
        self._pausado = False

    @staticmethod
    def _asegurar_resuelto(track: Track) -> Track:
        """Si la pista aún no tiene ubicación reproducible, la resuelve (fuera del lock).
        Puede lanzar MusicaError."""
        if track.get("ubicacion"):
            return track
        resuelto = resolver(track.get("consulta", ""))
        # Conserva metadatos que ya tuviéramos (p. ej. título de una lista guardada).
        resuelto.setdefault("titulo", track.get("titulo", ""))
        return resuelto

    # --- API pública (control directo) -----------------------------------------
    def reproducir(self, consulta: str) -> str:
        """Resuelve y suena AHORA, reemplazando la cola por esta pista. Feedback o error
        claro. Es la vía primaria de 'pon [algo]'."""
        track = resolver(consulta)  # fuera del lock (red); MusicaError si falla
        self._ensure_vlc()
        with self._lock:
            self._cola = [track]
            self._indice = 0
            self._sonar_locked(track)
        return f"Sonando: {_descr(track)}"

    def encolar(self, consulta: str) -> str:
        """Añade una pista al final de la cola. Si no había nada sonando, arranca."""
        track = resolver(consulta)  # resolución (y error) inmediata al añadir
        self._ensure_vlc()
        with self._lock:
            self._cola.append(track)
            pos = len(self._cola)
            # Si no hay reproducción activa, empieza a sonar esta.
            if self._parado or self._indice < 0:
                self._indice = len(self._cola) - 1
                self._sonar_locked(track)
                return f"Sonando: {_descr(track)}"
        return f"Añadida a la cola (posición {pos}): {_descr(track)}"

    def pausar(self) -> str:
        with self._lock:
            if self._player is None or self._parado:
                return "No hay nada sonando."
            if self._pausado:
                return "Ya estaba en pausa."
            self._player.set_pause(1)
            self._pausado = True
        return "Pausado."

    def reanudar(self) -> str:
        with self._lock:
            if self._player is None or self._parado:
                return "No hay nada que reanudar."
            if not self._pausado:
                return "Ya estaba sonando."
            self._player.set_pause(0)
            self._pausado = False
        return "Reanudado."

    def siguiente(self) -> str:
        """Salta a la siguiente pista de la cola (orden explícita del usuario)."""
        with self._lock:
            if not self._cola:
                return "La cola está vacía."
            if self._indice + 1 >= len(self._cola):
                return "Ya estás en la última pista de la cola."
            objetivo = self._indice + 1
            track = self._cola[objetivo]
        try:
            track = self._asegurar_resuelto(track)
        except MusicaError as exc:
            return f"No pude cargar la siguiente pista: {exc}"
        with self._lock:
            self._cola[objetivo] = track
            self._indice = objetivo
            self._sonar_locked(track)
        return f"Siguiente: {_descr(track)}"

    def anterior(self) -> str:
        """Vuelve a la pista anterior de la cola."""
        with self._lock:
            if not self._cola:
                return "La cola está vacía."
            if self._indice <= 0:
                return "Ya estás en la primera pista de la cola."
            objetivo = self._indice - 1
            track = self._cola[objetivo]
        try:
            track = self._asegurar_resuelto(track)
        except MusicaError as exc:
            return f"No pude cargar la pista anterior: {exc}"
        with self._lock:
            self._cola[objetivo] = track
            self._indice = objetivo
            self._sonar_locked(track)
        return f"Anterior: {_descr(track)}"

    def _auto_siguiente(self) -> None:
        """Auto-avance al terminar una pista: salta a la siguiente RESUELTA, saltando las
        que no se puedan cargar. Si no queda nada, se detiene. Corre en el hilo de avance."""
        while True:
            with self._lock:
                if self._indice + 1 >= len(self._cola):
                    self._detener_locked()
                    return
                objetivo = self._indice + 1
                self._indice = objetivo  # avanza el índice aunque falle (para no repetir)
                track = self._cola[objetivo]
            try:
                track = self._asegurar_resuelto(track)
            except MusicaError:
                continue  # pista rota: prueba la siguiente
            with self._lock:
                self._cola[objetivo] = track
                self._sonar_locked(track)
            return

    def set_volumen(self, nivel: int) -> str:
        try:
            nivel = int(nivel)
        except (TypeError, ValueError):
            return "El volumen debe ser un número de 0 a 100."
        nivel = max(0, min(100, nivel))
        with self._lock:
            self._volumen = nivel
            if self._player is not None:
                self._player.audio_set_volume(nivel)
        return f"Volumen al {nivel}%."

    def quitar(self, pos: int) -> str:
        """Quita la pista en la posición `pos` (1-based, como la ve el usuario)."""
        try:
            pos = int(pos)
        except (TypeError, ValueError):
            return "Indica la posición como un número."
        with self._lock:
            if not (1 <= pos <= len(self._cola)):
                return f"No hay pista en la posición {pos} (la cola tiene {len(self._cola)})."
            idx = pos - 1
            track = self._cola.pop(idx)
            # Ajusta el índice actual para que siga apuntando a la pista correcta.
            if idx < self._indice:
                self._indice -= 1
            elif idx == self._indice:
                # Quitamos la que sonaba: paramos (no forzamos salto brusco).
                self._detener_locked()
                self._indice = min(self._indice, len(self._cola) - 1)
        return f"Quitada de la cola: {_descr(track)}"

    def vaciar(self) -> str:
        with self._lock:
            n = len(self._cola)
            self._detener_locked()
            self._cola = []
            self._indice = -1
        return f"Cola vaciada ({n} pista{'s' if n != 1 else ''} eliminada{'s' if n != 1 else ''})."

    # --- Snapshot de estado (para que_suena y, en Tanda B, el HUD) -------------
    def estado(self) -> dict:
        with self._lock:
            actual = self._cola[self._indice] if 0 <= self._indice < len(self._cola) else None
            pos_ms = dur_ms = 0
            if self._player is not None and not self._parado:
                try:
                    pos_ms = max(0, self._player.get_time())
                    dur_ms = max(0, self._player.get_length())
                except Exception:
                    pass
            return {
                "sonando": (not self._parado) and actual is not None,
                "pausado": self._pausado,
                "titulo": (actual or {}).get("titulo", ""),
                "artista": (actual or {}).get("artista", ""),
                "fuente": (actual or {}).get("fuente", ""),
                "pos_seg": pos_ms // 1000,
                "dur_seg": dur_ms // 1000,
                "volumen": self._volumen,
                "indice": self._indice,
                "cola": [_descr(t) for t in self._cola],
            }

    # --- API adicional para Tanda B (favoritos/listas/HUD) ---------------------
    def pista_actual(self) -> Optional[Track]:
        """Devuelve el Track que suena ahora (para guardarlo en favoritos), o None."""
        with self._lock:
            if 0 <= self._indice < len(self._cola):
                return dict(self._cola[self._indice])
        return None

    def cargar_cola(self, tracks: list[Track]) -> str:
        """Reemplaza la cola por una lista de pistas y arranca. Resuelve la 1ª en el acto
        (feedback/error inmediato); el resto perezosamente al sonar. Para reproducir una
        lista o los favoritos."""
        if not tracks:
            return "No hay nada que reproducir."
        primero = self._asegurar_resuelto(tracks[0])  # MusicaError si falla
        self._ensure_vlc()
        with self._lock:
            self._cola = [primero] + [dict(t) for t in tracks[1:]]
            self._indice = 0
            self._sonar_locked(primero)
        n = len(tracks)
        return f"Reproduciendo {n} pista{'s' if n != 1 else ''}. Sonando: {_descr(primero)}"

    def buscar(self, fraccion: float) -> str:
        """Salta a una posición de la pista actual (0.0–1.0). Para el clic en la barra."""
        try:
            f = max(0.0, min(1.0, float(fraccion)))
        except (TypeError, ValueError):
            return "Posición inválida."
        with self._lock:
            if self._player is None or self._parado:
                return "No hay nada sonando."
            self._player.set_position(f)
        return f"Saltado al {int(f * 100)}%."


# --- Singleton perezoso -------------------------------------------------------
_REPRODUCTOR: Optional[Reproductor] = None
_LOCK = threading.Lock()


def get_reproductor() -> Reproductor:
    """Devuelve la instancia compartida del reproductor (creándola la primera vez).
    Cablea el hook de historial a la biblioteca (import perezoso para no acoplar el motor
    a la persistencia hasta que hace falta)."""
    global _REPRODUCTOR
    if _REPRODUCTOR is None:
        with _LOCK:
            if _REPRODUCTOR is None:
                r = Reproductor()
                try:
                    from cristopher.musica.biblioteca import get_biblioteca
                    r._on_track_start = lambda track: get_biblioteca().registrar_historial(track)
                except Exception:
                    pass  # sin historial si la biblioteca no está disponible
                _REPRODUCTOR = r
    return _REPRODUCTOR
