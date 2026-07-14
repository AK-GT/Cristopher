"""Persistencia del módulo de música (Tanda B).

Favoritos, listas de reproducción e historial en el SQLite EXISTENTE (data/memory.db,
el de la memoria — §3 del spec: "reutilizar el SQLite existente, NO crear base nueva").
Las tablas van prefijadas `musica_` para no colisionar con `facts` (que posee memory.py).
Conexión propia con su lock, patrón de `recordatorios.py`.

Se guarda la CONSULTA, no la URL de stream (las de yt-dlp caducan): al reproducir un
favorito o una lista, la pista se RE-RESUELVE (reproductor._asegurar_resuelto). Por eso
los Tracks que devuelve esta capa llevan `ubicacion=None`.
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime
from typing import Optional

from cristopher.config import DATA
from cristopher.musica.resolver import Track


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _track(titulo: str, artista: str, fuente: str, consulta: str) -> Track:
    """Construye un Track sin resolver (ubicacion=None) desde campos guardados."""
    return {
        "titulo": titulo or consulta,
        "artista": artista or "",
        "fuente": fuente or "",
        "ubicacion": None,       # se resuelve al sonar
        "consulta": consulta,
    }


class Biblioteca:
    """Almacén de favoritos, listas e historial en data/memory.db."""

    def __init__(self, db_path: Optional[str] = None) -> None:
        DATA.mkdir(parents=True, exist_ok=True)
        # timeout: espera si otra conexión (memory.py) tiene un lock de escritura breve.
        self._conn = sqlite3.connect(
            db_path or str(DATA / "memory.db"), check_same_thread=False, timeout=5
        )
        self._lock = threading.Lock()
        self._init_schema()

    def _init_schema(self) -> None:
        with self._conn:
            self._conn.execute(
                """CREATE TABLE IF NOT EXISTS musica_favoritos (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    titulo TEXT NOT NULL,
                    artista TEXT,
                    fuente TEXT,
                    consulta TEXT NOT NULL,
                    creado TEXT NOT NULL
                )"""
            )
            self._conn.execute(
                """CREATE TABLE IF NOT EXISTS musica_listas (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    nombre TEXT NOT NULL UNIQUE,
                    creada TEXT NOT NULL
                )"""
            )
            self._conn.execute(
                """CREATE TABLE IF NOT EXISTS musica_lista_canciones (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    lista_id INTEGER NOT NULL,
                    titulo TEXT NOT NULL,
                    artista TEXT,
                    fuente TEXT,
                    consulta TEXT NOT NULL,
                    orden INTEGER NOT NULL
                )"""
            )
            self._conn.execute(
                """CREATE TABLE IF NOT EXISTS musica_historial (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    titulo TEXT NOT NULL,
                    artista TEXT,
                    fuente TEXT,
                    reproducido TEXT NOT NULL
                )"""
            )

    # --- Favoritos -------------------------------------------------------------
    def add_favorito(self, track: Track) -> tuple[int, bool]:
        """Guarda un favorito. Devuelve (id, ya_existia). Deduplica por consulta."""
        consulta = (track.get("consulta") or track.get("titulo") or "").strip()
        with self._lock, self._conn:
            row = self._conn.execute(
                "SELECT id FROM musica_favoritos WHERE consulta=?", (consulta,)
            ).fetchone()
            if row:
                return row[0], True
            cur = self._conn.execute(
                "INSERT INTO musica_favoritos (titulo, artista, fuente, consulta, creado) "
                "VALUES (?, ?, ?, ?, ?)",
                (track.get("titulo") or consulta, track.get("artista") or "",
                 track.get("fuente") or "", consulta, _now()),
            )
            return cur.lastrowid, False

    def quitar_favorito(self, fav_id: int) -> Optional[str]:
        """Borra un favorito por id. Devuelve su título si existía, o None."""
        with self._lock, self._conn:
            row = self._conn.execute(
                "SELECT titulo FROM musica_favoritos WHERE id=?", (fav_id,)
            ).fetchone()
            if not row:
                return None
            self._conn.execute("DELETE FROM musica_favoritos WHERE id=?", (fav_id,))
            return row[0]

    def listar_favoritos(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, titulo, artista, fuente, consulta FROM musica_favoritos "
                "ORDER BY id"
            ).fetchall()
        return [
            {"id": r[0], "titulo": r[1], "artista": r[2], "fuente": r[3], "consulta": r[4]}
            for r in rows
        ]

    def favoritos_tracks(self) -> list[Track]:
        return [_track(f["titulo"], f["artista"], f["fuente"], f["consulta"])
                for f in self.listar_favoritos()]

    # --- Listas ----------------------------------------------------------------
    def _lista_id(self, nombre: str) -> Optional[int]:
        row = self._conn.execute(
            "SELECT id FROM musica_listas WHERE nombre=? COLLATE NOCASE", (nombre,)
        ).fetchone()
        return row[0] if row else None

    def crear_lista(self, nombre: str) -> tuple[int, bool]:
        """Crea una lista. Devuelve (id, ya_existia)."""
        nombre = (nombre or "").strip()
        with self._lock, self._conn:
            lid = self._lista_id(nombre)
            if lid is not None:
                return lid, True
            cur = self._conn.execute(
                "INSERT INTO musica_listas (nombre, creada) VALUES (?, ?)", (nombre, _now())
            )
            return cur.lastrowid, False

    def anadir_a_lista(self, nombre: str, track: Track) -> bool:
        """Añade una pista a una lista (la crea si no existe). Devuelve si creó la lista."""
        nombre = (nombre or "").strip()
        consulta = (track.get("consulta") or track.get("titulo") or "").strip()
        with self._lock, self._conn:
            lid = self._lista_id(nombre)
            creada = False
            if lid is None:
                cur = self._conn.execute(
                    "INSERT INTO musica_listas (nombre, creada) VALUES (?, ?)",
                    (nombre, _now()),
                )
                lid = cur.lastrowid
                creada = True
            orden = self._conn.execute(
                "SELECT COALESCE(MAX(orden), 0) + 1 FROM musica_lista_canciones WHERE lista_id=?",
                (lid,),
            ).fetchone()[0]
            self._conn.execute(
                "INSERT INTO musica_lista_canciones (lista_id, titulo, artista, fuente, "
                "consulta, orden) VALUES (?, ?, ?, ?, ?, ?)",
                (lid, track.get("titulo") or consulta, track.get("artista") or "",
                 track.get("fuente") or "", consulta, orden),
            )
            return creada

    def quitar_de_lista(self, nombre: str, pos: int) -> Optional[str]:
        """Quita la pista en la posición `pos` (1-based) de una lista. Devuelve su título,
        None si la lista no existe o la posición es inválida."""
        with self._lock, self._conn:
            lid = self._lista_id(nombre)
            if lid is None:
                return None
            rows = self._conn.execute(
                "SELECT id, titulo FROM musica_lista_canciones WHERE lista_id=? ORDER BY orden",
                (lid,),
            ).fetchall()
            if not (1 <= pos <= len(rows)):
                return None
            cid, titulo = rows[pos - 1]
            self._conn.execute("DELETE FROM musica_lista_canciones WHERE id=?", (cid,))
            return titulo

    def canciones_de(self, nombre: str) -> Optional[list[Track]]:
        """Tracks de una lista (ordenados). None si la lista no existe; [] si está vacía."""
        with self._lock:
            lid = self._lista_id(nombre)
            if lid is None:
                return None
            rows = self._conn.execute(
                "SELECT titulo, artista, fuente, consulta FROM musica_lista_canciones "
                "WHERE lista_id=? ORDER BY orden",
                (lid,),
            ).fetchall()
        return [_track(r[0], r[1], r[2], r[3]) for r in rows]

    def listar_listas(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT l.nombre, COUNT(c.id) FROM musica_listas l "
                "LEFT JOIN musica_lista_canciones c ON c.lista_id = l.id "
                "GROUP BY l.id ORDER BY l.nombre"
            ).fetchall()
        return [{"nombre": r[0], "n": r[1]} for r in rows]

    # --- Historial -------------------------------------------------------------
    def registrar_historial(self, track: Track) -> None:
        """Anota una reproducción. Best-effort: nunca lanza (no debe romper el audio)."""
        try:
            with self._lock, self._conn:
                self._conn.execute(
                    "INSERT INTO musica_historial (titulo, artista, fuente, reproducido) "
                    "VALUES (?, ?, ?, ?)",
                    (track.get("titulo") or track.get("consulta") or "",
                     track.get("artista") or "", track.get("fuente") or "", _now()),
                )
        except Exception:
            pass


# --- Singleton perezoso -------------------------------------------------------
_BIBLIOTECA: Optional[Biblioteca] = None
_BIBLIOTECA_LOCK = threading.Lock()


def get_biblioteca() -> Biblioteca:
    global _BIBLIOTECA
    if _BIBLIOTECA is None:
        with _BIBLIOTECA_LOCK:
            if _BIBLIOTECA is None:
                _BIBLIOTECA = Biblioteca()
    return _BIBLIOTECA
