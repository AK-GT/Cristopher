"""Recordatorios y dedup de avisos para la proactividad (Fase 7).

SQLite en data/proactivo.db:
- `recordatorios`: recordatorios por hora que el usuario programa.
- `avisos_vistos`: claves de avisos ya emitidos, para que el demonio no repita
  (evento id, correo id, recordatorio id).
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime
from typing import Optional

from cristopher.config import DATA, PROACTIVO_DB


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class Recordatorios:
    def __init__(self, db_path: Optional[str] = None) -> None:
        DATA.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path or str(PROACTIVO_DB), check_same_thread=False)
        self._lock = threading.Lock()
        with self._conn:
            self._conn.execute(
                """CREATE TABLE IF NOT EXISTS recordatorios (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    texto TEXT NOT NULL,
                    cuando_iso TEXT NOT NULL,
                    hecho INTEGER NOT NULL DEFAULT 0,
                    creado TEXT NOT NULL
                )"""
            )
            self._conn.execute(
                """CREATE TABLE IF NOT EXISTS avisos_vistos (
                    clave TEXT PRIMARY KEY,
                    ts TEXT NOT NULL
                )"""
            )

    # --- Recordatorios ---------------------------------------------------------
    def crear(self, texto: str, cuando_iso: str) -> int:
        with self._lock, self._conn:
            cur = self._conn.execute(
                "INSERT INTO recordatorios (texto, cuando_iso, creado) VALUES (?, ?, ?)",
                (texto, cuando_iso, _now()),
            )
            return cur.lastrowid

    def pendientes(self, ahora_iso: str) -> list[tuple[int, str, str]]:
        """Recordatorios con cuando_iso <= ahora y no hechos: (id, texto, cuando_iso)."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, texto, cuando_iso FROM recordatorios "
                "WHERE hecho=0 AND cuando_iso<=? ORDER BY cuando_iso",
                (ahora_iso,),
            ).fetchall()
        return rows

    def listar(self) -> list[tuple[int, str, str, int]]:
        with self._lock:
            return self._conn.execute(
                "SELECT id, texto, cuando_iso, hecho FROM recordatorios ORDER BY cuando_iso"
            ).fetchall()

    def marcar_hecho(self, rid: int) -> None:
        with self._lock, self._conn:
            self._conn.execute("UPDATE recordatorios SET hecho=1 WHERE id=?", (rid,))

    def borrar(self, rid: int) -> Optional[str]:
        """Elimina el recordatorio por id. Devuelve su texto si existía, o None."""
        with self._lock, self._conn:
            row = self._conn.execute(
                "SELECT texto FROM recordatorios WHERE id=?", (rid,)
            ).fetchone()
            if not row:
                return None
            self._conn.execute("DELETE FROM recordatorios WHERE id=?", (rid,))
            return row[0]

    # --- Dedup de avisos -------------------------------------------------------
    def ya_visto(self, clave: str) -> bool:
        with self._lock:
            return (
                self._conn.execute(
                    "SELECT 1 FROM avisos_vistos WHERE clave=?", (clave,)
                ).fetchone()
                is not None
            )

    def marcar_visto(self, clave: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT OR IGNORE INTO avisos_vistos (clave, ts) VALUES (?, ?)",
                (clave, _now()),
            )


_REC: Optional[Recordatorios] = None
_REC_LOCK = threading.Lock()


def get_recordatorios() -> Recordatorios:
    global _REC
    if _REC is None:
        with _REC_LOCK:
            if _REC is None:
                _REC = Recordatorios()
    return _REC
