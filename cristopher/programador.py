"""Programador de tareas (sistema de programación): a diferencia de un recordatorio
(`recordatorios.py`, que solo genera un aviso de texto), una fila de `programadas` es
una instrucción REAL que se ejecuta con el turno completo del agente (todas sus
tools) cuando toca.

SQLite en data/proactivo.db (mismo archivo que recordatorios.py, tabla propia):
- `programadas`: instrucciones sueltas (por hora), en cadena (mismo `grupo`, orden
  1..N) o que esperan a que una sesión de Claude Code termine (`espera_sesion`).

Una sola regla de "lista para disparar" (`pendientes_listas`) cubre los 3 casos sin
ramas especiales — ver PLAN de diseño para el porqué.
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime
from typing import Optional

from cristopher.config import DATA, PROACTIVO_DB


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class Programador:
    def __init__(self, db_path: Optional[str] = None) -> None:
        DATA.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path or str(PROACTIVO_DB), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._conn:
            self._conn.execute(
                """CREATE TABLE IF NOT EXISTS programadas (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    texto TEXT NOT NULL,
                    grupo TEXT,
                    orden INTEGER,
                    dispara_en_iso TEXT,
                    espera_sesion TEXT,
                    estado TEXT NOT NULL DEFAULT 'pendiente',
                    creado TEXT NOT NULL,
                    disparado TEXT
                )"""
            )

    def crear(
        self,
        texto: str,
        cuando_iso: Optional[str] = None,
        grupo: Optional[str] = None,
        espera_sesion: Optional[str] = None,
    ) -> int:
        orden = None
        with self._lock, self._conn:
            if grupo:
                row = self._conn.execute(
                    "SELECT MAX(orden) FROM programadas WHERE grupo=?", (grupo,)
                ).fetchone()
                orden = (row[0] or 0) + 1
            cur = self._conn.execute(
                "INSERT INTO programadas "
                "(texto, grupo, orden, dispara_en_iso, espera_sesion, creado) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (texto, grupo, orden, cuando_iso, espera_sesion, _now()),
            )
            return cur.lastrowid

    def listar(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM programadas ORDER BY "
                "COALESCE(grupo, ''), COALESCE(orden, 0), id"
            ).fetchall()
        return [dict(r) for r in rows]

    def pendientes_listas(self, sesiones: dict[str, str]) -> list[dict]:
        """Filas en 'pendiente' cuya condición de disparo ya se cumple:
        - si tiene grupo y orden>1, el paso anterior del mismo grupo debe estar 'hecha'.
        - si tiene dispara_en_iso, ya debe haber pasado.
        - si tiene espera_sesion, esa sesión debe estar 'terminado' o 'error'.
        """
        ahora_iso = _now()
        listas = []
        with self._lock:
            filas = self._conn.execute(
                "SELECT * FROM programadas WHERE estado='pendiente'"
            ).fetchall()
            for f in filas:
                fila = dict(f)
                if fila["grupo"] and fila["orden"] and fila["orden"] > 1:
                    anterior = self._conn.execute(
                        "SELECT estado FROM programadas WHERE grupo=? AND orden=?",
                        (fila["grupo"], fila["orden"] - 1),
                    ).fetchone()
                    if anterior is None or anterior["estado"] != "hecha":
                        continue
                if fila["dispara_en_iso"] and fila["dispara_en_iso"] > ahora_iso:
                    continue
                if fila["espera_sesion"] and sesiones.get(fila["espera_sesion"]) not in (
                    "terminado", "error",
                ):
                    continue
                listas.append(fila)
        return listas

    def marcar_lanzada(self, pid: int) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE programadas SET estado='lanzada', disparado=? WHERE id=?",
                (_now(), pid),
            )

    def marcar_hecha(self, pid: int) -> None:
        with self._lock, self._conn:
            self._conn.execute("UPDATE programadas SET estado='hecha' WHERE id=?", (pid,))

    def marcar_error(self, pid: int, mensaje: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE programadas SET estado='error', texto=texto || ' [ERROR: ' || ? || ']' "
                "WHERE id=?",
                (mensaje, pid),
            )

    def cancelar(self, pid: int) -> Optional[str]:
        with self._lock, self._conn:
            row = self._conn.execute(
                "SELECT texto FROM programadas WHERE id=?", (pid,)
            ).fetchone()
            if not row:
                return None
            self._conn.execute(
                "UPDATE programadas SET estado='cancelada' WHERE id=?", (pid,)
            )
            return row["texto"]

    def recuperar_huerfanas(self) -> None:
        """Al arrancar: todo lo que quedó en 'lanzada' (el proceso murió con la fila
        ya sacada de la cola en memoria, sin marcar hecha/error) vuelve a 'pendiente'
        para que el demonio la relance en el próximo tick."""
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE programadas SET estado='pendiente' WHERE estado='lanzada'"
            )


_PROG: Optional[Programador] = None
_PROG_LOCK = threading.Lock()


def get_programador() -> Programador:
    global _PROG
    if _PROG is None:
        with _PROG_LOCK:
            if _PROG is None:
                _PROG = Programador()
    return _PROG
