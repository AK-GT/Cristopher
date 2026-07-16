"""Almacén de tareas pendientes (pendiente / en_proceso / hecho).

Persistidas en el SQLite EXISTENTE (data/memory.db, el mismo que memory.py y
notas.py — se reutiliza la base existente, no se crea una nueva). La tabla
`tareas` no colisiona con `facts` (memory.py) ni con `notas` (notas.py).
Marcar una tarea como "hecho" la elimina de la lista (no queda histórico).
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime
from typing import Optional

from cristopher.config import DATA


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class Tareas:
    """Tareas pendientes del usuario en data/memory.db (tabla `tareas`)."""

    def __init__(self, db_path: Optional[str] = None) -> None:
        DATA.mkdir(parents=True, exist_ok=True)
        # timeout: espera si otra conexión (memory.py / notas.py) tiene un lock breve.
        self._conn = sqlite3.connect(
            db_path or str(DATA / "memory.db"), check_same_thread=False, timeout=5
        )
        self._lock = threading.Lock()
        with self._conn:
            self._conn.execute(
                """CREATE TABLE IF NOT EXISTS tareas (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    texto TEXT NOT NULL,
                    estado TEXT NOT NULL DEFAULT 'pendiente',
                    creado TEXT NOT NULL,
                    actualizado TEXT NOT NULL
                )"""
            )

    def crear(self, texto: str) -> int:
        with self._lock, self._conn:
            ahora = _now()
            cur = self._conn.execute(
                "INSERT INTO tareas (texto, estado, creado, actualizado) "
                "VALUES (?, 'pendiente', ?, ?)",
                (texto, ahora, ahora),
            )
            return cur.lastrowid

    def listar(self) -> list[tuple[int, str, str, str]]:
        """Todas las tareas activas: (id, texto, estado, creado)."""
        with self._lock:
            return self._conn.execute(
                "SELECT id, texto, estado, creado FROM tareas ORDER BY id"
            ).fetchall()

    def actualizar_estado(self, tarea_id: int, estado: str) -> Optional[str]:
        """Cambia el estado de una tarea. Si estado es 'hecho', la borra.

        Devuelve el texto de la tarea si existía, o None si no.
        """
        with self._lock, self._conn:
            row = self._conn.execute(
                "SELECT texto FROM tareas WHERE id=?", (tarea_id,)
            ).fetchone()
            if not row:
                return None
            if estado == "hecho":
                self._conn.execute("DELETE FROM tareas WHERE id=?", (tarea_id,))
            else:
                self._conn.execute(
                    "UPDATE tareas SET estado=?, actualizado=? WHERE id=?",
                    (estado, _now(), tarea_id),
                )
            return row[0]

    def borrar(self, tarea_id: int) -> Optional[str]:
        """Borra una tarea por id sin pasar por 'hecho'. Devuelve su texto, o None."""
        with self._lock, self._conn:
            row = self._conn.execute(
                "SELECT texto FROM tareas WHERE id=?", (tarea_id,)
            ).fetchone()
            if not row:
                return None
            self._conn.execute("DELETE FROM tareas WHERE id=?", (tarea_id,))
            return row[0]


# --- Singleton perezoso -------------------------------------------------------
_TAREAS: Optional[Tareas] = None
_TAREAS_LOCK = threading.Lock()


def get_tareas() -> Tareas:
    global _TAREAS
    if _TAREAS is None:
        with _TAREAS_LOCK:
            if _TAREAS is None:
                _TAREAS = Tareas()
    return _TAREAS
