"""Almacén de notas rápidas (Módulo D — utilidades, Tanda A).

Captura al vuelo de notas del usuario ("apunta que tengo que llamar al fontanero"),
persistidas en el SQLite EXISTENTE (data/memory.db, el de la memoria — el spec pide
reutilizar la base existente, no crear una nueva). La tabla `notas` no colisiona con
`facts` (memory.py) ni con las `musica_*` (biblioteca.py). Conexión propia con su lock,
patrón calcado de `cristopher/musica/biblioteca.py` / `recordatorios.py`.
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime
from typing import Optional

from cristopher.config import DATA


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class Notas:
    """Notas del usuario en data/memory.db (tabla `notas`)."""

    def __init__(self, db_path: Optional[str] = None) -> None:
        DATA.mkdir(parents=True, exist_ok=True)
        # timeout: espera si otra conexión (memory.py / biblioteca) tiene un lock breve.
        self._conn = sqlite3.connect(
            db_path or str(DATA / "memory.db"), check_same_thread=False, timeout=5
        )
        self._lock = threading.Lock()
        with self._conn:
            self._conn.execute(
                """CREATE TABLE IF NOT EXISTS notas (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    texto TEXT NOT NULL,
                    creado TEXT NOT NULL
                )"""
            )

    def apuntar(self, texto: str) -> int:
        with self._lock, self._conn:
            cur = self._conn.execute(
                "INSERT INTO notas (texto, creado) VALUES (?, ?)", (texto, _now())
            )
            return cur.lastrowid

    def listar(self) -> list[tuple[int, str, str]]:
        """Todas las notas, más recientes primero: (id, texto, creado)."""
        with self._lock:
            return self._conn.execute(
                "SELECT id, texto, creado FROM notas ORDER BY id DESC"
            ).fetchall()

    def buscar(self, consulta: str) -> list[tuple[int, str, str]]:
        """Notas cuyo texto contiene `consulta` (sin distinguir mayúsculas)."""
        patron = f"%{(consulta or '').strip()}%"
        with self._lock:
            return self._conn.execute(
                "SELECT id, texto, creado FROM notas "
                "WHERE texto LIKE ? COLLATE NOCASE ORDER BY id DESC",
                (patron,),
            ).fetchall()

    def borrar(self, nota_id: int) -> Optional[str]:
        """Borra una nota por id. Devuelve su texto si existía, o None."""
        with self._lock, self._conn:
            row = self._conn.execute(
                "SELECT texto FROM notas WHERE id=?", (nota_id,)
            ).fetchone()
            if not row:
                return None
            self._conn.execute("DELETE FROM notas WHERE id=?", (nota_id,))
            return row[0]


# --- Singleton perezoso -------------------------------------------------------
_NOTAS: Optional[Notas] = None
_NOTAS_LOCK = threading.Lock()


def get_notas() -> Notas:
    global _NOTAS
    if _NOTAS is None:
        with _NOTAS_LOCK:
            if _NOTAS is None:
                _NOTAS = Notas()
    return _NOTAS
