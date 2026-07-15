"""Memoria persistente de CRISTOPHER (Fase 2).

Hechos durables en SQLite + recuerdo semántico por similitud coseno sobre embeddings
de Gemini. Sin dependencias nuevas: coseno en Python puro. Degrada con elegancia
(§8): si el embedding falla (red/cuota), el hecho se guarda igual y el recuerdo cae a
búsqueda por palabra clave — nunca se pierde información ni se finge éxito (§1).

La memoria vive en data/memory.db (gitignored) y persiste entre sesiones.
"""

from __future__ import annotations

import json
import math
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Optional

from google import genai

from cristopher.config import DATA, EMBED_MODEL, get_api_key

# Umbral mínimo de similitud coseno para considerar un hecho "relevante".
# gemini-embedding-001 tiene una línea base alta: en la verificación de la Fase 2 los
# aciertos caían en 0.69–0.79 y el ruido en 0.53–0.62, así que 0.65 los separa limpio.
SIMILARITY_THRESHOLD = 0.65


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _cosine(a: list[float], b: list[float]) -> float:
    """Similitud coseno entre dos vectores (Python puro)."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


class Memory:
    """Almacén de hechos en SQLite con recuerdo semántico."""

    def __init__(self, db_path: Optional[str] = None) -> None:
        DATA.mkdir(parents=True, exist_ok=True)
        self._path = db_path or str(DATA / "memory.db")
        # check_same_thread=False: el REPL y el demonio (Fase 7) pueden compartirla.
        self._conn = sqlite3.connect(self._path, check_same_thread=False)
        self._lock = threading.Lock()
        self._client: Optional[genai.Client] = None
        self._init_schema()

    def _init_schema(self) -> None:
        with self._conn:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS facts (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    text       TEXT NOT NULL,
                    embedding  TEXT,           -- JSON de floats; NULL si falló el embed
                    created_at TEXT NOT NULL
                )
                """
            )

    # --- Embeddings (perezoso, degrada con elegancia) --------------------------

    def _embed(self, text: str) -> Optional[list[float]]:
        """Devuelve el embedding del texto, o None si no se pudo (sin lanzar)."""
        try:
            if self._client is None:
                self._client = genai.Client(api_key=get_api_key())
            resp = self._client.models.embed_content(model=EMBED_MODEL, contents=text)
            return list(resp.embeddings[0].values)
        except Exception:
            return None

    # --- API pública -----------------------------------------------------------

    def remember(self, text: str) -> str:
        """Guarda un hecho durable. Devuelve un mensaje de confirmación."""
        text = (text or "").strip()
        if not text:
            return "No había nada que recordar (texto vacío)."
        embedding = self._embed(text)
        emb_json = json.dumps(embedding) if embedding is not None else None
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO facts (text, embedding, created_at) VALUES (?, ?, ?)",
                (text, emb_json, _now()),
            )
        note = "" if embedding is not None else " (sin índice semántico: embedding no disponible)"
        return f"Recordado: {text}{note}"

    def recall(self, query: str, k: int = 3) -> list[str]:
        """Devuelve hasta k hechos relevantes para la consulta, por similitud coseno.
        Si el embedding no está disponible, cae a búsqueda por palabra clave."""
        query = (query or "").strip()
        if not query:
            return []
        with self._lock:
            rows = self._conn.execute("SELECT text, embedding FROM facts").fetchall()
        if not rows:
            return []

        q_emb = self._embed(query)
        if q_emb is None:
            return self._keyword_recall(query, k)

        scored: list[tuple[float, str]] = []
        for text, emb_json in rows:
            if not emb_json:
                continue
            score = _cosine(q_emb, json.loads(emb_json))
            if score >= SIMILARITY_THRESHOLD:
                scored.append((score, text))
        scored.sort(key=lambda t: t[0], reverse=True)
        results = [text for _, text in scored[:k]]
        # Si el índice semántico no dio nada útil, prueba palabra clave como respaldo.
        return results or self._keyword_recall(query, k)

    def _keyword_recall(self, query: str, k: int) -> list[str]:
        """Respaldo: busca por coincidencia de subcadena en el texto del hecho."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT text FROM facts WHERE text LIKE ? ORDER BY id DESC LIMIT ?",
                (f"%{query}%", k),
            ).fetchall()
        return [r[0] for r in rows]

    def olvidar(self, fragmento: str) -> list[str]:
        """Borra los hechos cuyo texto contenga `fragmento` (sin distinguir mayúsculas,
        incluyendo tildes/ñ: LIKE de SQLite solo pliega mayúsculas ASCII, así que el
        filtro se hace en Python con casefold()).
        Devuelve los textos borrados, para poder confirmarlos al usuario."""
        fragmento = (fragmento or "").strip()
        if not fragmento:
            return []
        frag_cf = fragmento.casefold()
        with self._lock, self._conn:
            rows = self._conn.execute("SELECT id, text FROM facts").fetchall()
            match = [(rid, text) for rid, text in rows if frag_cf in text.casefold()]
            if not match:
                return []
            self._conn.executemany(
                "DELETE FROM facts WHERE id = ?", [(rid,) for rid, _ in match]
            )
        return [text for _, text in match]

    def count(self) -> int:
        with self._lock:
            return self._conn.execute("SELECT COUNT(*) FROM facts").fetchone()[0]


# --- Singleton perezoso a nivel de módulo -------------------------------------
_MEMORY: Optional[Memory] = None
_MEMORY_LOCK = threading.Lock()


def get_memory() -> Memory:
    """Devuelve la instancia compartida de Memory (creándola la primera vez)."""
    global _MEMORY
    if _MEMORY is None:
        with _MEMORY_LOCK:
            if _MEMORY is None:
                _MEMORY = Memory()
    return _MEMORY
