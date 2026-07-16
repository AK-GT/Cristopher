"""Almacén del diario de reflexión diaria (modo diario).

Persistido en el SQLite EXISTENTE (data/memory.db, el mismo que memory.py, notas.py
y tareas.py — se reutiliza la base existente, no se crea una nueva). La tabla
`diario` no colisiona con `facts`/`notas`/`tareas`. Cada fila es una entrada
categorizada de un día concreto; no hay edición ni borrado de entradas pasadas.
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import date, datetime
from typing import Optional

from cristopher.config import DATA

CATEGORIAS = ("evento_social", "dilema", "hecho_destacado", "tarea_futura", "libre")


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class Diario:
    """Entradas del diario del usuario en data/memory.db (tabla `diario`)."""

    def __init__(self, db_path: Optional[str] = None) -> None:
        DATA.mkdir(parents=True, exist_ok=True)
        # timeout: espera si otra conexión (memory.py / notas.py / tareas.py) tiene un
        # lock breve.
        self._conn = sqlite3.connect(
            db_path or str(DATA / "memory.db"), check_same_thread=False, timeout=5
        )
        self._lock = threading.Lock()
        with self._conn:
            self._conn.execute(
                """CREATE TABLE IF NOT EXISTS diario (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    fecha TEXT NOT NULL,
                    categoria TEXT NOT NULL,
                    texto TEXT NOT NULL,
                    creado TEXT NOT NULL
                )"""
            )

    def agregar(self, categoria: str, texto: str, fecha: Optional[str] = None) -> int:
        fecha = fecha or date.today().isoformat()
        with self._lock, self._conn:
            cur = self._conn.execute(
                "INSERT INTO diario (fecha, categoria, texto, creado) VALUES (?, ?, ?, ?)",
                (fecha, categoria, texto, _now()),
            )
            return cur.lastrowid

    def entradas_de_fecha(self, fecha: str) -> list[tuple[int, str, str, str]]:
        """Entradas de esa fecha: (id, categoria, texto, creado), ordenadas por id."""
        with self._lock:
            return self._conn.execute(
                "SELECT id, categoria, texto, creado FROM diario WHERE fecha=? ORDER BY id",
                (fecha,),
            ).fetchall()

    def hay_entradas_hoy(self, fecha: str) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM diario WHERE fecha=? LIMIT 1", (fecha,)
            ).fetchone()
            return row is not None


# --- Singleton perezoso -------------------------------------------------------
_DIARIO: Optional[Diario] = None
_DIARIO_LOCK = threading.Lock()


def get_diario() -> Diario:
    global _DIARIO
    if _DIARIO is None:
        with _DIARIO_LOCK:
            if _DIARIO is None:
                _DIARIO = Diario()
    return _DIARIO
