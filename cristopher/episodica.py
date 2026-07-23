"""Memoria episódica: línea de tiempo de eventos con fecha (Backlog — implementado).

Distinta de la memoria semántica de `memory.py` (hechos atemporales por similitud):
aquí se registra CUÁNDO pasó algo ("hablaste con Juan por WhatsApp", "pediste el
briefing"), para responder preguntas temporales/de patrón. Persistida en el SQLite
EXISTENTE (data/memory.db, el mismo que memory.py/notas.py/tareas.py — se reutiliza
la base existente, no se crea una nueva). La tabla `episodios` no colisiona con
`facts` (memory.py), `notas` ni `tareas`. Conexión propia con su lock, patrón calcado
de `cristopher/notas.py` / `cristopher/tareas.py`.
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime
from typing import Optional

from cristopher.config import DATA


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class Episodica:
    """Episodios (línea de tiempo) del usuario en data/memory.db (tabla `episodios`)."""

    def __init__(self, db_path: Optional[str] = None) -> None:
        DATA.mkdir(parents=True, exist_ok=True)
        # timeout: espera si otra conexión (memory.py / notas.py / tareas.py) tiene
        # un lock breve.
        self._conn = sqlite3.connect(
            db_path or str(DATA / "memory.db"), check_same_thread=False, timeout=5
        )
        self._lock = threading.Lock()
        with self._conn:
            self._conn.execute(
                """CREATE TABLE IF NOT EXISTS episodios (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts TEXT NOT NULL,
                    tipo TEXT NOT NULL,
                    entidad TEXT,
                    resumen TEXT NOT NULL,
                    meta TEXT
                )"""
            )

    def registrar(
        self,
        resumen: str,
        tipo: str = "evento",
        entidad: Optional[str] = None,
        meta: Optional[str] = None,
    ) -> int:
        with self._lock, self._conn:
            cur = self._conn.execute(
                "INSERT INTO episodios (ts, tipo, entidad, resumen, meta) "
                "VALUES (?, ?, ?, ?, ?)",
                (_now(), tipo, entidad or None, resumen, meta),
            )
            return cur.lastrowid

    def periodo(
        self,
        desde: str,
        hasta: str,
        tipo: Optional[str] = None,
        entidad: Optional[str] = None,
        limit: int = 200,
    ) -> list[tuple[int, str, str, Optional[str], str]]:
        """Episodios con ts entre desde y hasta (ambos ISO), más recientes primero.

        Devuelve (id, ts, tipo, entidad, resumen). `desde`/`hasta` ya deben venir
        resueltos (el parseo de lenguaje natural vive en tools/episodica_tools.py).
        """
        sql = "SELECT id, ts, tipo, entidad, resumen FROM episodios WHERE ts BETWEEN ? AND ?"
        params: list = [desde, hasta]
        if tipo:
            sql += " AND tipo = ?"
            params.append(tipo)
        if entidad:
            sql += " AND entidad = ?"
            params.append(entidad)
        sql += " ORDER BY ts DESC LIMIT ?"
        params.append(limit)
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    def borrar(self, episodio_id: int) -> Optional[str]:
        """Borra un episodio por id. Devuelve su resumen si existía, o None."""
        with self._lock, self._conn:
            row = self._conn.execute(
                "SELECT resumen FROM episodios WHERE id=?", (episodio_id,)
            ).fetchone()
            if not row:
                return None
            self._conn.execute("DELETE FROM episodios WHERE id=?", (episodio_id,))
            return row[0]


# --- Singleton perezoso -------------------------------------------------------
_EPISODICA: Optional[Episodica] = None
_EPISODICA_LOCK = threading.Lock()


def get_episodica() -> Episodica:
    global _EPISODICA
    if _EPISODICA is None:
        with _EPISODICA_LOCK:
            if _EPISODICA is None:
                _EPISODICA = Episodica()
    return _EPISODICA
