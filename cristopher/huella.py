"""Motor de huella digital por pivotes (OSINT). Tanda A: cimientos + pivote username.
Tanda B: pivotes email, brechas y teléfono. Tanda C: bucle de pivote (BFS) + puntuación
de confianza fina.

Modelo de datos común (Nodo/Arista/Dossier/Investigacion) que usarán las tandas D-E, y
tres pivotes reales:
  - username, vía Maigret (amplitud, 3000+ sitios) + Socialscan (alta confianza,
    endpoints de registro).
  - email, vía user-scanner (servicios registrados) + GHunt (Gmail, opcional) +
    Gravatar (perfil público) + XposedOrNot (brechas).
  - teléfono, vía `phonenumbers` (país/operadora/tipo, offline) + ignorant (apps
    donde está registrado).
Cada cuenta hallada se puntúa 0.0-1.0 (`_puntuar()`, suma de pesos por FUENTE
INDEPENDIENTE — dos módulos que preguntan a la misma API cuentan como una sola,
`_grupo_independencia()`) y se etiqueta por umbral: `<0.4` tentativo · `0.4-0.7`
probable · `>0.7` confirmado (§5/HERRAMIENTA_BUSQUEDA.md). Los nodos "brecha" son la
excepción explícita: un hecho directo de base de datos, no una corroboración de
identidad, se marcan `confianza="expuesto"` y no pasan por `_puntuar()` (Tanda B).

`investigar()` (Tanda C) encadena BFS estos tres pivotes: cada cuenta confirmada o
probable se pasa por `socid_extractor` para extraer nuevos identificadores (email en
bio, alias enlazado...) que se encolan como pivotes nuevos, deduplicados, hasta
`HUELLA_PROFUNDIDAD_MAX` niveles — con topes duros de nodos para que no explote.

GHunt es OPCIONAL y no vive en el venv del proyecto: `pip install ghunt` baja
`httpx` por debajo de 0.28 y rompe `google-genai`/`ddgs` (verificado, no hipotético
— ver `requirements.txt`). Se instala aislado (pipx u otro venv) y solo se usa si
`ghunt` está en el PATH Y hay sesión (`ghunt login`) guardada; si no, `buscar_email`
lo reporta en `dossier.errores` sin romper el resto.

Gotcha de esta máquina (documentado para no repetir la investigación): `aiodns`
(dependencia de Maigret) usa `pycares`, que en este Windows no detecta los
servidores DNS reales del adaptador de red y cae a 127.0.0.1, rompiendo TODAS las
resoluciones con "Could not contact DNS servers" — para Maigret y también para
Socialscan (aiohttp activa el resolver async en cuanto detecta `aiodns` instalado
en el entorno, aunque la tool en sí no lo use). Mitigación:
  - Maigret: expone `--dns-resolver threaded` (issue conocida en su propio código,
    `maigret/checking.py`), que usa el resolver por hilos de aiohttp en vez de
    aiodns/c-ares.
  - Socialscan: no tiene ese flag. Se lanza con `python -c "..."` bloqueando el
    import de aiodns (`sys.modules['aiodns'] = None`) ANTES de importar su CLI, lo
    que fuerza a aiohttp a caer también al resolver por hilos.
Sin este bloqueo, ambas tools fallan el 100% de las búsquedas con un error de DNS
que no tiene nada que ver con la conectividad real de la máquina (verificado: la
resolución DNS normal por socket funciona bien).
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from difflib import SequenceMatcher
from io import BytesIO
from pathlib import Path
from typing import Optional

import httpx
import imagehash
import phonenumbers
import requests
import trio
from PIL import Image
from phonenumbers import PhoneNumberType, carrier, geocoder
from xposedornot import XposedOrNot

from socid_extractor import extract, mutate_url, parse as socid_parse

from cristopher.browser import get_browser
from cristopher.config import (
    HUELLA_AVATAR_TIMEOUT,
    HUELLA_BIO_SIMILITUD_UMBRAL,
    HUELLA_DIR,
    HUELLA_DISYUNTOR_COOLDOWN_SEGUNDOS,
    HUELLA_DORK_RESULTADOS_MAX,
    HUELLA_EXTRACCION_TIMEOUT,
    HUELLA_MAIGRET_TOP_SITES,
    HUELLA_MAX_NODOS_TOTAL,
    HUELLA_MAX_PIVOTES_POR_NIVEL,
    HUELLA_PHASH_UMBRAL,
    HUELLA_PROFUNDIDAD_MAX,
    HUELLA_TIEMPO_MAX_SEGUNDOS,
    HUELLA_TIMEOUT,
    HUELLA_VENTANA_SIN_NOVEDAD,
)
from cristopher.episodica import get_episodica
from cristopher.vision import preguntar_sobre_imagen


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _entorno_saneado() -> dict[str, str]:
    """Entorno de los subprocesos OSINT, sin variables CLAUDE_* de sesión.

    Mismo patrón que `delegate._entorno_sub_agente()` (no se importa de ahí para
    no acoplar `huella.py` al módulo de delegación de código, que es un dominio
    distinto — pero es idéntico a propósito).
    """
    return {k: v for k, v in os.environ.items() if not k.startswith("CLAUDE")}


# --- Modelo de datos común (lo reutilizan las Tandas B-E) --------------------


@dataclass
class Nodo:
    id: str
    tipo: str  # alias|email|telefono|nombre|url_perfil|foto|brecha|termino
    valor: str
    # Etiqueta DERIVADA de `puntuacion` vía `_puntuar()` (Tanda C): "tentativo" (<0.4) |
    # "probable" (0.4-0.7) | "confirmado" (>0.7) — corroboración de identidad. Excepción:
    # "expuesto" (solo tipo="brecha": hecho directo de base de datos, no corroboración de
    # identidad — no pasa por `_puntuar()`, ver Tanda B).
    confianza: str = "tentativo"
    # Puntuación 0.0-1.0 que produce `confianza` (suma de pesos por fuente independiente,
    # capada a 1.0; ver PESO_FUENTE/_grupo_independencia/_puntuar). 0.0 en nodos que no
    # pasan por el cruce de fuentes (el propio pivote, nodos "expuesto"/"dato").
    puntuacion: float = 0.0
    # Texto descriptivo determinista calculado sobre el propio nodo (p. ej. país/operadora/
    # tipo de línea de un teléfono, vía `phonenumbers`) — no implica una fuente externa
    # corroborando una identidad, así que no genera Arista ni consume la escala de confianza.
    detalle: str = ""
    # URL del avatar de esta cuenta, si la fuente lo dio (Maigret/Gravatar). Alimenta la
    # correlación por pHash (Tanda D) — vacío si no se conoce.
    avatar_url: str = ""
    # Pivotes nuevos (tipo, valor) que el propio colector ya extrajo estructuralmente
    # al construir este nodo (p. ej. GHunt: email/teléfono de su JSON) — atajo del
    # bucle de pivote (Tanda F) que evita depender solo de re-fetchear una URL cuando
    # el dato ya vino estructurado. NO se persiste (fuera de `to_dict`): es una pista
    # efímera para la pasada de `investigar()`, no un dato del dossier en sí.
    pivotes: list = field(default_factory=list)
    # Campos de texto (bio/enlace externo) que `_extraer_pivotes` ya sacó de esta
    # cuenta al minar pivotes nuevos (Tanda K) — reutilizados por
    # `_correlacionar_texto()` para enlazar cuentas de avatares DISTINTOS que
    # comparten el mismo enlace o una bio casi idéntica. NO se persiste (mismo
    # criterio que `pivotes`): efímero para la pasada de `investigar()`.
    campos_correlacion: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "tipo": self.tipo,
            "valor": self.valor,
            "confianza": self.confianza,
            "puntuacion": self.puntuacion,
            "detalle": self.detalle,
            "avatar_url": self.avatar_url,
        }

    @classmethod
    def from_dict(cls, datos: dict) -> "Nodo":
        """Reconstruye desde `to_dict()` (Tanda L, expediente reabrible). Los
        campos efímeros (`pivotes`, `campos_correlacion`) quedan vacíos a
        propósito: ya hicieron su trabajo en la pasada de `investigar()` que los
        generó — nunca se persistieron (fuera de `to_dict`) y no hace falta
        recuperarlos para reabrir el expediente, solo para seguir explorando la
        rama NUEVA que añada `ampliar_investigacion`."""
        return cls(
            id=datos["id"],
            tipo=datos["tipo"],
            valor=datos["valor"],
            confianza=datos.get("confianza", "tentativo"),
            puntuacion=datos.get("puntuacion", 0.0),
            detalle=datos.get("detalle", ""),
            avatar_url=datos.get("avatar_url", ""),
        )


@dataclass
class Arista:
    origen: str
    destino: str
    tipo_enlace: str
    fuente: str
    peso: float = 1.0  # peso de ESTA fuente en la puntuación del nodo destino (Tanda C, PESO_FUENTE)

    def to_dict(self) -> dict:
        return {
            "origen": self.origen,
            "destino": self.destino,
            "tipo_enlace": self.tipo_enlace,
            "fuente": self.fuente,
            "peso": self.peso,
        }

    @classmethod
    def from_dict(cls, datos: dict) -> "Arista":
        return cls(
            origen=datos["origen"],
            destino=datos["destino"],
            tipo_enlace=datos["tipo_enlace"],
            fuente=datos["fuente"],
            peso=datos.get("peso", 1.0),
        )


@dataclass
class Dossier:
    pivote: str
    tipo: str
    nodos: list[Nodo] = field(default_factory=list)
    aristas: list[Arista] = field(default_factory=list)
    errores: list[str] = field(default_factory=list)  # fallos de herramientas, nunca ocultos
    creado: str = field(default_factory=_now)

    def to_dict(self) -> dict:
        return {
            "pivote": self.pivote,
            "tipo": self.tipo,
            "nodos": [n.to_dict() for n in self.nodos],
            "aristas": [a.to_dict() for a in self.aristas],
            "errores": self.errores,
            "creado": self.creado,
        }

    @classmethod
    def from_dict(cls, datos: dict) -> "Dossier":
        return cls(
            pivote=datos["pivote"],
            tipo=datos["tipo"],
            nodos=[Nodo.from_dict(n) for n in datos.get("nodos", [])],
            aristas=[Arista.from_dict(a) for a in datos.get("aristas", [])],
            errores=list(datos.get("errores", [])),
            creado=datos.get("creado", _now()),
        )


# --- Persistencia (patrón de tareas.py/notas.py, base propia y aislada) ------


class Huella:
    """Dossiers de huella digital en `data/huella/huella.db` (tabla `huella_dossiers`).

    Base de datos propia y aislada (no `data/memory.db`): son datos de terceros,
    con propósito y ciclo de vida distintos a hechos/notas/tareas del usuario.
    """

    def __init__(self, db_path: Optional[str] = None) -> None:
        HUELLA_DIR.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(
            db_path or str(HUELLA_DIR / "huella.db"), check_same_thread=False, timeout=5
        )
        self._lock = threading.Lock()
        with self._conn:
            self._conn.execute(
                """CREATE TABLE IF NOT EXISTS huella_dossiers (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    pivote TEXT NOT NULL,
                    tipo TEXT NOT NULL,
                    datos TEXT NOT NULL,
                    creado TEXT NOT NULL
                )"""
            )
            # Tanda C: resultado del bucle de pivote (BFS), varios dossiers anidados
            # bajo una sola semilla — tabla propia, no se mezcla con huella_dossiers
            # (un dossier = una búsqueda; una investigación = varias encadenadas).
            self._conn.execute(
                """CREATE TABLE IF NOT EXISTS huella_investigaciones (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    pivote_semilla TEXT NOT NULL,
                    tipo_semilla TEXT NOT NULL,
                    datos TEXT NOT NULL,
                    creado TEXT NOT NULL
                )"""
            )
            # Tanda E: pivotes/términos que el usuario pide vigilar — el demonio
            # proactivo los re-audita periódicamente (ver proactivo.py).
            self._conn.execute(
                """CREATE TABLE IF NOT EXISTS huella_vigilancia (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tipo TEXT NOT NULL,
                    valor TEXT NOT NULL,
                    creado TEXT NOT NULL
                )"""
            )
            # Tanda G: cooldown de fuente que sobrevive entre `DisyuntorFuente`
            # distintos (una por cada `investigar()`) — sin esto, una fuente ya
            # bloqueada en una investigación se reintentaba de inmediato en la
            # siguiente, en vez de respetar el enfriamiento.
            self._conn.execute(
                """CREATE TABLE IF NOT EXISTS huella_disyuntor (
                    fuente TEXT PRIMARY KEY,
                    hasta TEXT NOT NULL
                )"""
            )

    def agregar_vigilancia(self, tipo: str, valor: str) -> int:
        with self._lock, self._conn:
            cur = self._conn.execute(
                "INSERT INTO huella_vigilancia (tipo, valor, creado) VALUES (?, ?, ?)",
                (tipo, valor, _now()),
            )
            return cur.lastrowid

    def listar_vigilancia(self) -> list[tuple[int, str, str, str]]:
        """(id, tipo, valor, creado) de todo lo vigilado."""
        with self._lock:
            return self._conn.execute(
                "SELECT id, tipo, valor, creado FROM huella_vigilancia ORDER BY id"
            ).fetchall()

    def quitar_vigilancia(self, vigilancia_id: int) -> Optional[str]:
        """Borra por id. Devuelve el `valor` borrado, o None si no existía."""
        with self._lock, self._conn:
            row = self._conn.execute(
                "SELECT valor FROM huella_vigilancia WHERE id=?", (vigilancia_id,)
            ).fetchone()
            if not row:
                return None
            self._conn.execute("DELETE FROM huella_vigilancia WHERE id=?", (vigilancia_id,))
            return row[0]

    def guardar_dossier(self, dossier: Dossier) -> int:
        with self._lock, self._conn:
            cur = self._conn.execute(
                "INSERT INTO huella_dossiers (pivote, tipo, datos, creado) VALUES (?, ?, ?, ?)",
                (dossier.pivote, dossier.tipo, json.dumps(dossier.to_dict(), ensure_ascii=False), dossier.creado),
            )
            return cur.lastrowid

    def guardar_investigacion(self, inv: "Investigacion") -> int:
        with self._lock, self._conn:
            cur = self._conn.execute(
                "INSERT INTO huella_investigaciones (pivote_semilla, tipo_semilla, datos, creado) "
                "VALUES (?, ?, ?, ?)",
                (inv.pivote_semilla, inv.tipo_semilla, json.dumps(inv.to_dict(), ensure_ascii=False), inv.creado),
            )
            return cur.lastrowid

    def ultimo_dossier(self, pivote: str) -> Optional[dict]:
        with self._lock:
            row = self._conn.execute(
                "SELECT datos FROM huella_dossiers WHERE pivote=? ORDER BY id DESC LIMIT 1",
                (pivote,),
            ).fetchone()
            return json.loads(row[0]) if row else None

    def marcar_cooldown(self, fuente: str, hasta: str) -> None:
        """Tanda G: registra/actualiza hasta cuándo debe evitarse `fuente` (fecha ISO,
        comparable como string por ser el mismo formato que `_now()`)."""
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO huella_disyuntor (fuente, hasta) VALUES (?, ?) "
                "ON CONFLICT(fuente) DO UPDATE SET hasta=excluded.hasta",
                (fuente, hasta),
            )

    def fuentes_en_cooldown(self) -> list[str]:
        """Tanda G: fuentes con un cooldown TODAVÍA vigente (hasta > ahora). No borra
        las filas vencidas (barato conservarlas, sirven de historial si hace falta
        depurar bloqueos futuros)."""
        with self._lock:
            filas = self._conn.execute("SELECT fuente, hasta FROM huella_disyuntor").fetchall()
        ahora = _now()
        return [fuente for fuente, hasta in filas if hasta > ahora]

    def obtener_investigacion(self, investigacion_id: int) -> Optional[dict]:
        """Tanda L: lee una investigación guardada por su id. `huella_investigaciones`
        era hasta ahora WRITE-ONLY (solo `guardar_investigacion` insertaba, nada
        volvía a leerla) — hueco que impedía reabrir un expediente para ampliarlo."""
        with self._lock:
            row = self._conn.execute(
                "SELECT datos FROM huella_investigaciones WHERE id=?", (investigacion_id,)
            ).fetchone()
            return json.loads(row[0]) if row else None

    def listar_investigaciones(self, limit: int = 20) -> list[tuple[int, str, str, str]]:
        """(id, pivote_semilla, tipo_semilla, creado) de las investigaciones
        guardadas, más recientes primero — para saber qué id pasarle a
        `ampliar_investigacion` (Tanda L)."""
        with self._lock:
            return self._conn.execute(
                "SELECT id, pivote_semilla, tipo_semilla, creado FROM huella_investigaciones "
                "ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()


_HUELLA: Optional[Huella] = None
_HUELLA_LOCK = threading.Lock()


def get_huella() -> Huella:
    global _HUELLA
    if _HUELLA is None:
        with _HUELLA_LOCK:
            if _HUELLA is None:
                _HUELLA = Huella()
    return _HUELLA


# --- Wrapper de subproceso aislado (patrón de delegate.py) -------------------


def _ejecutar_herramienta(nombre_binario: str, cmd: list[str], timeout: int) -> tuple[bool, str]:
    """Lanza `cmd` como subproceso aislado. Nunca lanza excepción: devuelve
    (ok, salida_o_error) para que el fallo de UNA herramienta externa no tumbe el
    resto del dossier (§1 "fallos explícitos", nunca fingir éxito)."""
    exe = shutil.which(cmd[0]) if cmd[0] != sys.executable else sys.executable
    if not exe:
        return False, f"ERROR: no encuentro el ejecutable '{cmd[0]}' en el PATH."
    try:
        proc = subprocess.run(
            [exe, *cmd[1:]],
            capture_output=True,
            text=True,
            timeout=timeout,
            env=_entorno_saneado(),
        )
    except subprocess.TimeoutExpired:
        return False, f"ERROR: {nombre_binario} superó el timeout de {timeout}s y fue abortado."
    except Exception as exc:
        return False, f"ERROR al lanzar {nombre_binario}: {exc}"
    if proc.returncode != 0 and not (proc.stdout or "").strip():
        err = (proc.stderr or "").strip()[:2000]
        return False, f"ERROR: {nombre_binario} terminó con código {proc.returncode}. {err}"
    return True, (proc.stdout or "")


# --- Disyuntor (circuit breaker) por fuente — Tanda F ------------------------
#
# "Gratis" no es "impune": Maigret/Socialscan/user-scanner/GHunt/ignorant corren
# contra servicios reales y un uso intensivo dentro de UNA MISMA investigación
# (hasta HUELLA_MAX_NODOS_TOTAL pivotes, cada uno relanzando estas mismas tools)
# puede acabar en bloqueo de IP o, en el caso de GHunt, en marcar como sospechosa
# la cuenta de Google real tras la que corre — verificado como riesgo real, no
# hipotético (crítica externa revisada y confirmada por el usuario). El disyuntor
# desactiva una fuente para el RESTO de esa investigación (nunca de forma global/
# persistente entre llamadas: una `huella_buscar` suelta siempre prueba todas las
# fuentes) tras fallos repetidos, o de inmediato si el error tiene pinta de
# bloqueo/rate-limit.

_PATRON_BLOQUEO = re.compile(
    r"429|rate.?limit|too many requests|blocked|banned|forbidden|\b403\b", re.IGNORECASE
)


class DisyuntorFuente:
    """Tolerancia a fallos por fuente. El desactivado en memoria (`_desactivadas`)
    nace fresco en cada instancia (una por `investigar()`), pero se precarga desde
    `huella.db` (Tanda G): si una fuente quedó en cooldown en una investigación
    anterior, esta instancia arranca ya sabiéndolo, en vez de tener que volver a
    fallar 2 veces para descubrirlo de nuevo.

    `huella`: inyectable para tests (evita que pruebas unitarias lean/escriban la
    `huella.db` real de producción); por defecto usa el singleton (`get_huella()`),
    igual que el resto del módulo."""

    UMBRAL_FALLOS_SEGUIDOS = 2

    def __init__(self, huella: Optional["Huella"] = None) -> None:
        self._huella = huella if huella is not None else get_huella()
        self._fallos_seguidos: dict[str, int] = {}
        self._desactivadas: set[str] = set(self._huella.fuentes_en_cooldown())

    def activa(self, fuente: str) -> bool:
        return fuente not in self._desactivadas

    def _desactivar(self, fuente: str) -> None:
        self._desactivadas.add(fuente)
        hasta = (datetime.now() + timedelta(seconds=HUELLA_DISYUNTOR_COOLDOWN_SEGUNDOS)).isoformat(
            timespec="seconds"
        )
        self._huella.marcar_cooldown(fuente, hasta)

    def registrar(self, fuente: str, ok: bool, detalle_error: str = "") -> None:
        if ok:
            self._fallos_seguidos[fuente] = 0
            return
        if _PATRON_BLOQUEO.search(detalle_error or ""):
            self._desactivar(fuente)  # señal fuerte: no esperamos a un segundo fallo
            return
        self._fallos_seguidos[fuente] = self._fallos_seguidos.get(fuente, 0) + 1
        if self._fallos_seguidos[fuente] >= self.UMBRAL_FALLOS_SEGUIDOS:
            self._desactivar(fuente)

    def degradadas(self) -> list[str]:
        return sorted(self._desactivadas)


# --- Pivote USERNAME: Maigret + Socialscan ------------------------------------


def _correr_maigret(alias: str, out_dir: Path) -> tuple[bool, str, list[dict]]:
    """Devuelve (ok, mensaje_error_o_vacio, hallazgos). hallazgos: [{sitio, url}]."""
    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable,
        "-m",
        "maigret",
        alias,
        "--top-sites",
        str(HUELLA_MAIGRET_TOP_SITES),
        "--dns-resolver",
        "threaded",  # ver docstring del módulo: workaround del bug de aiodns/pycares en Windows
        "-J",
        "ndjson",
        "--folderoutput",
        str(out_dir),
        "--no-recursion",  # Tanda A: solo el pivote directo, sin BFS (eso es la Tanda C)
        "--no-progressbar",
        "--timeout",
        "15",
        "-n",
        "30",
    ]
    ok, salida = _ejecutar_herramienta("maigret", cmd, HUELLA_TIMEOUT)
    if not ok:
        return False, salida, []

    reporte = out_dir / f"report_{alias}_ndjson.json"
    if not reporte.exists():
        return False, "ERROR: Maigret no generó el reporte esperado.", []

    hallazgos = []
    for linea in reporte.read_text(encoding="utf-8").splitlines():
        linea = linea.strip()
        if not linea:
            continue
        try:
            registro = json.loads(linea)
        except json.JSONDecodeError:
            continue
        status = registro.get("status", {})
        sitio = status.get("site_name") or registro.get("sitename")
        url = status.get("url") or registro.get("url_user")
        avatar = status.get("ids", {}).get("image", "")
        if sitio and url:
            hallazgos.append({"sitio": sitio, "url": url, "avatar": avatar})
    return True, "", hallazgos


def _correr_socialscan(alias: str, out_file: Path) -> tuple[bool, str, list[dict]]:
    """Devuelve (ok, mensaje_error_o_vacio, hallazgos). hallazgos: [{sitio, url}]."""
    # Bloquea el import de aiodns ANTES de importar socialscan.cli: ver docstring
    # del módulo (mismo bug de DNS que Maigret, distinta mitigación porque
    # socialscan no expone un flag de resolver propio).
    codigo = (
        "import sys; sys.modules['aiodns'] = None; "
        "import asyncio; from socialscan.cli import main; asyncio.run(main())"
    )
    cmd = [sys.executable, "-c", codigo, alias, "--show-urls", "--json", str(out_file)]
    ok, salida = _ejecutar_herramienta("socialscan", cmd, HUELLA_TIMEOUT)
    if not ok:
        return False, salida, []

    if not out_file.exists():
        return False, "ERROR: Socialscan no generó el reporte esperado.", []

    try:
        datos = json.loads(out_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return False, f"ERROR: no pude parsear la salida de Socialscan: {exc}", []

    hallazgos = []
    for filas in datos.values():
        for fila in filas:
            # socialscan serializa los booleanos como texto ("True"/"False").
            if fila.get("success") == "True" and fila.get("valid") == "True" and fila.get("available") == "False":
                hallazgos.append({"sitio": fila.get("platform", "?"), "url": fila.get("link", "")})
    return True, "", hallazgos


def _parsear_csv_sherlock(ruta: Path) -> list[dict]:
    """Extrae {sitio, url} de las filas 'Claimed' del CSV de Sherlock. Aislado de
    `_correr_sherlock` para poder testear el parseo sin lanzar el subproceso real."""
    hallazgos = []
    with ruta.open(encoding="utf-8", newline="") as f:
        for fila in csv.DictReader(f):
            if fila.get("exists") == "Claimed":
                hallazgos.append({"sitio": fila.get("name", "?"), "url": fila.get("url_user", "")})
    return hallazgos


def _correr_sherlock(alias: str, out_dir: Path) -> tuple[bool, str, list[dict]]:
    """Devuelve (ok, mensaje_error_o_vacio, hallazgos). hallazgos: [{sitio, url}].

    Tanda J: tercera fuente independiente para el pivote username. Verificado a
    mano antes de integrarlo ("verifica, no asumas"): `sherlock-project` en PyPI
    instala limpio y NO depende de httpx/aiohttp/aiodns (usa `requests`), así que
    no puede repetir el conflicto de GHunt con `google-genai`/`ddgs`. Se descartó
    el paquete "blackbird" de PyPI (namespace package roto, no es la herramienta
    OSINT real) y WhatsMyName (sin paquete instalable, solo clonable de GitHub).

    `--json` de Sherlock es para CARGAR un fichero de sitios alternativo, NO para
    exportar resultados (gotcha verificado a mano) — el formato exportable es CSV
    (`--csv`), con columna `exists` en "Claimed"/"Available"/etc."""
    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable,
        "-m",
        "sherlock_project",
        alias,
        "--csv",
        "--no-txt",
        "--no-color",
        "--timeout",
        "15",
        "--folderoutput",
        str(out_dir),
    ]
    ok, salida = _ejecutar_herramienta("sherlock", cmd, HUELLA_TIMEOUT)
    if not ok:
        return False, salida, []

    reporte = out_dir / f"{alias}.csv"
    if not reporte.exists():
        return False, "ERROR: Sherlock no generó el reporte esperado.", []

    try:
        hallazgos = _parsear_csv_sherlock(reporte)
    except Exception as exc:
        return False, f"ERROR al parsear la salida de Sherlock: {exc}", []
    return True, "", hallazgos


def _clave_sitio(sitio: str) -> str:
    """Normaliza el nombre de sitio para cruzar resultados entre herramientas
    (p. ej. 'GitHub' de Socialscan vs 'GitHub' de Maigret; distinta capitalización
    o sufijos rompería el cruce)."""
    return sitio.strip().lower()


# Peso por fuente según su fuerza real (§5: "enlaces fuertes... pesan más que débiles").
# Endpoint de registro confirmado (Socialscan/user-scanner) > dato de cuenta real (GHunt) >
# perfil público verificable (Gravatar) > check de login/registro (ignorant) > verificación
# de URL de perfil real con lista curada (Sherlock, Tanda J: mismo método que Maigret pero
# ~400 sitios activamente mantenidos en vez de 3000+ con más ruido, de ahí el peso
# intermedio) > presencia por patrón de texto (Maigret, más falsos positivos). Fuentes
# futuras no listadas: 0.3.
PESO_FUENTE = {
    "socialscan": 0.6,
    "user-scanner": 0.6,
    "ghunt": 0.6,
    "gravatar": 0.5,
    "ignorant": 0.45,
    "sherlock": 0.4,
    "maigret": 0.35,
}
_PESO_FUENTE_DEFECTO = 0.3

# Umbrales exactos de HERRAMIENTA_BUSQUEDA.md §5.
_UMBRAL_CONFIRMADO = 0.7
_UMBRAL_PROBABLE = 0.4


def _grupo_independencia(fuente: str, clave_sitio: str) -> str:
    """Dos fuentes que preguntan a la MISMA API no cuentan como 2 confirmaciones
    independientes (§5, "dos módulos que leen la MISMA API cuentan como UNA
    fuente") — regla dura, no una excepción puntual. Caso real encontrado en la
    Tanda B: el módulo Gravatar de `user-scanner` y nuestra consulta directa a
    Gravatar (`_consultar_gravatar`) preguntan lo mismo."""
    if clave_sitio == "gravatar" and fuente in ("gravatar", "user-scanner"):
        return "gravatar_api"
    return fuente


def _puntuar(fuentes: set[str], clave_sitio: str) -> tuple[float, str]:
    """Suma el peso MÁXIMO por grupo de independencia (no la suma de repeticiones
    dentro del mismo grupo), capada a 1.0, y la traduce a etiqueta por umbral."""
    por_grupo: dict[str, float] = {}
    for fuente in fuentes:
        grupo = _grupo_independencia(fuente, clave_sitio)
        peso = PESO_FUENTE.get(fuente, _PESO_FUENTE_DEFECTO)
        por_grupo[grupo] = max(por_grupo.get(grupo, 0.0), peso)
    puntuacion = min(sum(por_grupo.values()), 1.0)
    if puntuacion > _UMBRAL_CONFIRMADO:
        etiqueta = "confirmado"
    elif puntuacion >= _UMBRAL_PROBABLE:
        etiqueta = "probable"
    else:
        etiqueta = "tentativo"
    return puntuacion, etiqueta


def _cruzar_hallazgos(
    dossier: Dossier, nodo_origen: Nodo, hallazgos_por_fuente: dict[str, list[dict]]
) -> None:
    """Cruza hallazgos {sitio, url} de varias fuentes y añade nodos `url_perfil` +
    aristas al dossier, puntuados por `_puntuar()` (§5/HERRAMIENTA_BUSQUEDA.md,
    puntuación fina de la Tanda C). Reutilizado por todos los pivotes que producen
    listas de cuentas (username, email)."""
    por_sitio: dict[str, dict] = {}
    for fuente, hallazgos in hallazgos_por_fuente.items():
        for h in hallazgos:
            clave = _clave_sitio(h["sitio"])
            entrada = por_sitio.setdefault(
                clave, {"sitio": h["sitio"], "url": h.get("url", ""), "avatar": "", "fuentes": set()}
            )
            if not entrada["url"] and h.get("url"):
                entrada["url"] = h["url"]
            if not entrada["avatar"] and h.get("avatar"):
                entrada["avatar"] = h["avatar"]
            entrada["fuentes"].add(fuente)

    for clave, entrada in sorted(por_sitio.items()):
        puntuacion, etiqueta = _puntuar(entrada["fuentes"], clave)
        nodo = Nodo(
            id=f"url_perfil:{entrada['url'] or clave}",
            tipo="url_perfil",
            valor=entrada["url"] or entrada["sitio"],
            confianza=etiqueta,
            puntuacion=puntuacion,
            avatar_url=entrada["avatar"],
        )
        dossier.nodos.append(nodo)
        for fuente in sorted(entrada["fuentes"]):
            dossier.aristas.append(
                Arista(
                    origen=nodo_origen.id,
                    destino=nodo.id,
                    tipo_enlace="cuenta_en",
                    fuente=fuente,
                    peso=PESO_FUENTE.get(fuente, _PESO_FUENTE_DEFECTO),
                )
            )


def buscar_username(alias: str, disyuntor: Optional["DisyuntorFuente"] = None) -> Dossier:
    """Pivote USERNAME: lanza Maigret + Socialscan + Sherlock (Tanda J) EN PARALELO
    (3 workers como mucho — "gratis" no es "impune": más concurrencia de la
    necesaria hacia los mismos sitios de destino aumenta el riesgo de bloqueo, no
    solo la velocidad) y cruza resultados (puntuados vía `_cruzar_hallazgos`/
    `_puntuar`). Tres fuentes en vez de dos: sube la frecuencia con la que un
    hallazgo llega a "confirmado" (antes tope en 2 grupos de independencia
    posibles). Cada fallo de herramienta se guarda en `dossier.errores` (nunca se
    oculta), y el dossier se persiste siempre, aunque alguna falle. Si `disyuntor`
    viene informado (siempre lo pasa `investigar()`, nunca `huella_buscar` suelto)
    y una fuente ya está degradada por fallos repetidos en ESTA investigación, se
    omite sin intentarla — Tanda F.
    """
    dossier = Dossier(pivote=alias, tipo="alias")
    hallazgos_m: list[dict] = []
    hallazgos_s: list[dict] = []
    hallazgos_sh: list[dict] = []

    with tempfile.TemporaryDirectory(prefix="huella_") as tmp:
        tmp_path = Path(tmp)
        tareas = {}
        with ThreadPoolExecutor(max_workers=3) as pool:
            if disyuntor is None or disyuntor.activa("maigret"):
                tareas["maigret"] = pool.submit(_correr_maigret, alias, tmp_path / "maigret")
            elif disyuntor is not None:
                dossier.errores.append("Maigret: fuente degradada en esta investigación (fallos repetidos), omitida.")
            if disyuntor is None or disyuntor.activa("socialscan"):
                tareas["socialscan"] = pool.submit(_correr_socialscan, alias, tmp_path / "socialscan.json")
            elif disyuntor is not None:
                dossier.errores.append("Socialscan: fuente degradada en esta investigación (fallos repetidos), omitida.")
            if disyuntor is None or disyuntor.activa("sherlock"):
                tareas["sherlock"] = pool.submit(_correr_sherlock, alias, tmp_path / "sherlock")
            elif disyuntor is not None:
                dossier.errores.append("Sherlock: fuente degradada en esta investigación (fallos repetidos), omitida.")

            if "maigret" in tareas:
                ok_m, err_m, hallazgos_m = tareas["maigret"].result()
                if disyuntor is not None:
                    disyuntor.registrar("maigret", ok_m, err_m)
                if not ok_m:
                    dossier.errores.append(f"Maigret: {err_m}")
            if "socialscan" in tareas:
                ok_s, err_s, hallazgos_s = tareas["socialscan"].result()
                if disyuntor is not None:
                    disyuntor.registrar("socialscan", ok_s, err_s)
                if not ok_s:
                    dossier.errores.append(f"Socialscan: {err_s}")
            if "sherlock" in tareas:
                ok_sh, err_sh, hallazgos_sh = tareas["sherlock"].result()
                if disyuntor is not None:
                    disyuntor.registrar("sherlock", ok_sh, err_sh)
                if not ok_sh:
                    dossier.errores.append(f"Sherlock: {err_sh}")

    nodo_pivote = Nodo(id=f"alias:{alias}", tipo="alias", valor=alias, confianza="confirmado")
    dossier.nodos.append(nodo_pivote)
    _cruzar_hallazgos(
        dossier, nodo_pivote, {"maigret": hallazgos_m, "socialscan": hallazgos_s, "sherlock": hallazgos_sh}
    )

    get_huella().guardar_dossier(dossier)
    return dossier


# --- Pivote EMAIL: user-scanner + GHunt (Gmail) + Gravatar + XposedOrNot -------


def _correr_user_scanner(email: str, out_file: Path) -> tuple[bool, str, list[dict]]:
    """Devuelve (ok, mensaje_error_o_vacio, hallazgos). hallazgos: [{sitio, url}]."""
    cmd = [
        sys.executable,
        "-m",
        "user_scanner",
        "-e",
        email,
        "-f",
        "json",
        "-o",
        str(out_file),
        "--no-nsfw",
    ]
    ok, salida = _ejecutar_herramienta("user-scanner", cmd, HUELLA_TIMEOUT)
    if not ok:
        return False, salida, []

    if not out_file.exists():
        return False, "ERROR: user-scanner no generó el reporte esperado.", []

    try:
        datos = json.loads(out_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return False, f"ERROR: no pude parsear la salida de user-scanner: {exc}", []

    hallazgos = [
        {"sitio": fila.get("site_name", "?"), "url": fila.get("url", "")}
        for fila in datos
        if fila.get("status") == "Registered"
    ]
    return True, "", hallazgos


def _ghunt_configurado() -> bool:
    """GHunt no vive en el venv del proyecto (rompe httpx de google-genai/ddgs, ver
    requirements.txt) — se instala aislado (pipx) y solo funciona si además se hizo
    `ghunt login` una vez. Comprobamos AMBAS condiciones antes de intentar nada, para
    no perder tiempo en un fallo garantizado."""
    if not shutil.which("ghunt"):
        return False
    creds = Path.home() / ".malfrats" / "ghunt" / "creds.m"
    return creds.exists()


def _correr_ghunt(email: str) -> tuple[bool, str, dict]:
    """Devuelve (ok, mensaje_error_o_vacio, datos_json).

    Usa `--json` (confirmado real: `ghunt email --help` lo expone) en vez de
    parsear el stdout con colores/ASCII-art de la CLI. Devuelve el dict YA
    parseado (antes se devolvía como texto truncado, perdiendo la estructura que
    necesita `_minar_pivotes_ghunt` para sacar pivotes nuevos del bucle — Tanda F).
    """
    if not _ghunt_configurado():
        return (
            False,
            "GHunt no está instalado/configurado (opcional: instálalo aislado con "
            "'pipx install ghunt' y ejecuta 'ghunt login' una vez; ver requirements.txt).",
            {},
        )
    with tempfile.TemporaryDirectory(prefix="huella_ghunt_") as tmp:
        out_file = Path(tmp) / "ghunt.json"
        cmd = ["ghunt", "email", email, "--json", str(out_file)]
        ok, salida = _ejecutar_herramienta("ghunt", cmd, HUELLA_TIMEOUT)
        if not ok:
            return False, salida, {}
        if not out_file.exists():
            return False, "ERROR: GHunt no generó el reporte esperado.", {}
        try:
            datos = json.loads(out_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            return False, f"ERROR: no pude parsear la salida de GHunt: {exc}", {}

    return True, "", datos


def _minar_pivotes_ghunt(datos: dict, email_semilla: str) -> list[tuple[str, str]]:
    """Recorre recursivamente el JSON de GHunt buscando campos de email/teléfono
    para alimentarlos de vuelta al bucle de pivote (Tanda F). Antes ese dato se
    volcaba entero como texto en `Nodo.detalle` y nunca se reutilizaba (verificado:
    `investigar()` solo minaba nodos `url_perfil` con URL http, así que el email/
    teléfono asociado que da GHunt moría ahí sin generar ningún pivote nuevo).

    Escanea por NOMBRE DE CLAVE (no por un schema fijo): la estructura del JSON de
    GHunt no es un contrato estable entre versiones, y un cambio de forma no debe
    romper el minado, solo dejar de encontrar campos con esos nombres.
    """
    candidatos: list[tuple[str, str]] = []

    def recorrer(obj):
        if isinstance(obj, dict):
            for clave, valor in obj.items():
                clave_l = clave.lower()
                if isinstance(valor, str) and valor.strip():
                    if "email" in clave_l and "@" in valor:
                        # Igual que en `_extraer_pivotes`: un campo con varios
                        # emails separados por coma no debe perder todos menos
                        # el primero.
                        for correo in valor.split(","):
                            correo = correo.strip()
                            if "@" in correo:
                                candidatos.append(("email", correo))
                    elif "phone" in clave_l or "telefono" in clave_l:
                        candidatos.append(("telefono", valor.strip()))
                else:
                    recorrer(valor)
        elif isinstance(obj, list):
            for item in obj:
                recorrer(item)

    recorrer(datos)
    semilla_l = email_semilla.strip().lower()
    # Dedup preservando orden, y nunca "descubrir" el propio email semilla como si
    # fuera un pivote nuevo (GHunt lo repite en varios campos de su JSON).
    vistos: set[tuple[str, str]] = set()
    resultado: list[tuple[str, str]] = []
    for tipo, valor in candidatos:
        clave = (tipo, valor.strip().lower())
        if clave in vistos or clave == ("email", semilla_l):
            continue
        vistos.add(clave)
        resultado.append((tipo, valor))
    return resultado


def _consultar_gravatar(email: str) -> Optional[dict]:
    """Perfil público de Gravatar (API v3, sin key). None si no existe (404) o falla
    la petición — nunca lanza, es una fuente opcional y de bajo coste."""
    hash_ = hashlib.sha256(email.strip().lower().encode()).hexdigest()
    try:
        r = requests.get(f"https://api.gravatar.com/v3/profiles/{hash_}", timeout=10)
    except requests.RequestException:
        return None
    if r.status_code != 200:
        return None
    try:
        return r.json()
    except ValueError:
        return None


def _consultar_xposedornot(email: str) -> tuple[bool, str, list[str]]:
    """Devuelve (ok, mensaje_error_o_vacio, nombres_de_brechas)."""
    try:
        resultado = XposedOrNot().check_email(email)
    except Exception as exc:
        return False, f"ERROR al consultar XposedOrNot: {exc}", []
    return True, "", list(resultado.breaches or [])


_TIPO_LINEA = {
    PhoneNumberType.MOBILE: "móvil",
    PhoneNumberType.FIXED_LINE: "fijo",
    PhoneNumberType.FIXED_LINE_OR_MOBILE: "fijo o móvil",
    PhoneNumberType.VOIP: "VoIP",
    PhoneNumberType.TOLL_FREE: "gratuito",
    PhoneNumberType.PREMIUM_RATE: "tarificación especial",
    PhoneNumberType.PAGER: "buscapersonas",
    PhoneNumberType.UAN: "UAN",
    PhoneNumberType.VOICEMAIL: "buzón de voz",
    PhoneNumberType.PERSONAL_NUMBER: "número personal",
    PhoneNumberType.SHARED_COST: "coste compartido",
}


def _analizar_telefono(numero: str) -> Optional[dict]:
    """Metadata determinista y 100% offline (sin red, sin key) vía `phonenumbers`.
    Región por defecto "ES" si el número no viene con prefijo "+". None si no
    parsea como número de teléfono."""
    try:
        p = phonenumbers.parse(numero, None if numero.strip().startswith("+") else "ES")
    except phonenumbers.NumberParseException:
        return None
    return {
        "valido": phonenumbers.is_valid_number(p),
        "pais": geocoder.description_for_number(p, "es") or "desconocido",
        "operadora": carrier.name_for_number(p, "es") or "sin dato",
        "tipo": _TIPO_LINEA.get(phonenumbers.number_type(p), "desconocido"),
        "e164": phonenumbers.format_number(p, phonenumbers.PhoneNumberFormat.E164),
    }


def _normalizar_telefono(valor: str) -> str:
    """Normaliza un teléfono a E.164 para que el dedup del bucle de pivote (Tanda F)
    no trate '+34 600 123 456', '34600123456' y '0034600123456' como pivotes
    DISTINTOS — verificado real: sin esto, el mismo teléfono descubierto en
    formatos distintos por GHunt/socid_extractor generaba dos nodos y relanzaba
    `ignorant` dos veces sobre el mismo número. Si no parsea como número válido,
    devuelve el valor tal cual (nunca lanza, mismo criterio que el resto del
    módulo) — el dedup cae de vuelta a comparar el string crudo."""
    try:
        p = phonenumbers.parse(valor, None if valor.strip().startswith("+") else "ES")
        return phonenumbers.format_number(p, phonenumbers.PhoneNumberFormat.E164)
    except phonenumbers.NumberParseException:
        return valor


async def _lanzar_ignorant(numero_nacional: str, codigo_pais: str) -> list[dict]:
    from ignorant.core import get_functions, import_submodules, launch_module

    modulos = import_submodules("ignorant.modules")
    sitios = get_functions(modulos)
    cliente = httpx.AsyncClient(timeout=10)
    salida: list[dict] = []
    async with trio.open_nursery() as nursery:
        for sitio in sitios:
            nursery.start_soon(launch_module, sitio, numero_nacional, codigo_pais, cliente, salida)
    await cliente.aclose()
    return salida


def _correr_ignorant(numero_e164: str) -> tuple[bool, str, list[dict]]:
    """Devuelve (ok, mensaje_error_o_vacio, hallazgos). hallazgos: [{sitio, url}].

    Llama a las funciones internas de `ignorant` EN PROCESO (trio), no a su CLI: su
    `main()` intenta auto-actualizarse por pip en cada arranque
    (`ignorant/core.py::check_update`), efecto secundario que no queremos disparar
    desde aquí. No usa aiohttp/aiodns (es httpx/trio-based) — no sufre el bug de
    DNS de la Tanda A.
    """
    p = phonenumbers.parse(numero_e164, None)
    codigo_pais = str(p.country_code)
    numero_nacional = str(p.national_number)
    try:
        salida = trio.run(_lanzar_ignorant, numero_nacional, codigo_pais)
    except Exception as exc:
        return False, f"ERROR al lanzar ignorant: {exc}", []

    hallazgos = [
        {"sitio": r["name"], "url": ""}
        for r in salida
        if r.get("exists") and not r.get("rateLimit")
    ]
    return True, "", hallazgos


def buscar_email(email: str, disyuntor: Optional["DisyuntorFuente"] = None) -> Dossier:
    """Pivote EMAIL: user-scanner (servicios registrados) + GHunt (si Gmail y está
    configurado) — EN PARALELO, máx. 2 workers (Tanda F) — + Gravatar (perfil
    público) + XposedOrNot (brechas).

    Los nodos `url_perfil` pasan por la misma puntuación fina que `buscar_username`
    (`_cruzar_hallazgos`). Los nodos `brecha` se marcan `confianza="expuesto"` (hecho
    directo de base de datos, no corroboración de identidad — no pasa por `_puntuar`).
    `disyuntor`: igual que en `buscar_username`, solo lo pasa `investigar()`.
    """
    dossier = Dossier(pivote=email, tipo="email")
    nodo_pivote = Nodo(id=f"email:{email}", tipo="email", valor=email, confianza="confirmado")
    dossier.nodos.append(nodo_pivote)

    es_gmail = email.strip().lower().endswith(("@gmail.com", "@googlemail.com"))
    hallazgos_us: list[dict] = []
    datos_gh: dict = {}

    with tempfile.TemporaryDirectory(prefix="huella_") as tmp:
        tareas = {}
        with ThreadPoolExecutor(max_workers=2) as pool:
            if disyuntor is None or disyuntor.activa("user-scanner"):
                tareas["user-scanner"] = pool.submit(_correr_user_scanner, email, Path(tmp) / "user_scanner.json")
            elif disyuntor is not None:
                dossier.errores.append("user-scanner: fuente degradada en esta investigación (fallos repetidos), omitida.")
            if es_gmail and (disyuntor is None or disyuntor.activa("ghunt")):
                tareas["ghunt"] = pool.submit(_correr_ghunt, email)
            elif es_gmail and disyuntor is not None:
                dossier.errores.append("GHunt: fuente degradada en esta investigación (fallos repetidos), omitida.")

            if "user-scanner" in tareas:
                ok_us, err_us, hallazgos_us = tareas["user-scanner"].result()
                if disyuntor is not None:
                    disyuntor.registrar("user-scanner", ok_us, err_us)
                if not ok_us:
                    dossier.errores.append(f"user-scanner: {err_us}")
            if "ghunt" in tareas:
                ok_gh, err_gh, datos_gh = tareas["ghunt"].result()
                if disyuntor is not None:
                    disyuntor.registrar("ghunt", ok_gh, err_gh)
                if not ok_gh:
                    dossier.errores.append(f"GHunt: {err_gh}")

    hallazgos_gravatar: list[dict] = []
    perfil_gravatar = _consultar_gravatar(email)
    if perfil_gravatar:
        hallazgos_gravatar = [
            {
                "sitio": "Gravatar",
                "url": perfil_gravatar.get("profile_url", ""),
                "avatar": perfil_gravatar.get("avatar_url", ""),
            }
        ]
        # Cuentas verificadas que el propio perfil de Gravatar enlaza (otras redes
        # sociales) — antes se ignoraban por completo; son url_perfil normales, así
        # que entran por el mismo `_cruzar_hallazgos` y de ahí al bucle de pivote
        # (Tanda F) igual que cualquier otro hallazgo con URL.
        for cuenta in perfil_gravatar.get("verified_accounts", []) or []:
            url_cuenta = (cuenta or {}).get("url", "")
            if url_cuenta:
                servicio = cuenta.get("service_label") or cuenta.get("service_type") or "?"
                hallazgos_gravatar.append({"sitio": servicio, "url": url_cuenta})

    _cruzar_hallazgos(dossier, nodo_pivote, {"user-scanner": hallazgos_us, "gravatar": hallazgos_gravatar})

    if datos_gh:
        dossier.nodos.append(
            Nodo(
                id=f"ghunt:{email}",
                tipo="nombre",
                valor="(ver detalle)",
                confianza="tentativo",
                detalle=json.dumps(datos_gh, ensure_ascii=False, indent=2)[:2000],
                pivotes=_minar_pivotes_ghunt(datos_gh, email),
            )
        )
        dossier.aristas.append(
            Arista(origen=nodo_pivote.id, destino=f"ghunt:{email}", tipo_enlace="perfil_google", fuente="ghunt")
        )

    ok_xon, err_xon, brechas = _consultar_xposedornot(email)
    if not ok_xon:
        dossier.errores.append(f"XposedOrNot: {err_xon}")
    for nombre_brecha in brechas:
        nodo_brecha = Nodo(
            id=f"brecha:{email}:{nombre_brecha}", tipo="brecha", valor=nombre_brecha, confianza="expuesto"
        )
        dossier.nodos.append(nodo_brecha)
        dossier.aristas.append(
            Arista(origen=nodo_pivote.id, destino=nodo_brecha.id, tipo_enlace="expuesto_en", fuente="xposedornot")
        )

    get_huella().guardar_dossier(dossier)
    return dossier


# --- Pivote TELÉFONO: phonenumbers + ignorant ---------------------------------


def buscar_telefono(numero: str, disyuntor: Optional["DisyuntorFuente"] = None) -> Dossier:
    """Pivote TELÉFONO: `phonenumbers` (país/operadora/tipo, offline) + `ignorant`
    (apps donde el número está registrado) + dorking del número exacto (Tanda H,
    mismo patrón que `buscar_nombre`/`buscar_termino` — resultados siempre
    tentativos, no cuentas verificadas). `disyuntor`: igual que en
    `buscar_username` (Tanda F), solo lo pasa `investigar()`."""
    dossier = Dossier(pivote=numero, tipo="telefono")

    meta = _analizar_telefono(numero)
    if meta is None:
        dossier.errores.append(f"'{numero}' no parece un número de teléfono válido.")
        nodo_pivote = Nodo(id=f"telefono:{numero}", tipo="telefono", valor=numero, confianza="confirmado")
        dossier.nodos.append(nodo_pivote)
        get_huella().guardar_dossier(dossier)
        return dossier

    detalle = f"País: {meta['pais']} · Operadora: {meta['operadora']} · Tipo: {meta['tipo']} · Válido: {'sí' if meta['valido'] else 'no'}"
    nodo_pivote = Nodo(
        id=f"telefono:{meta['e164']}", tipo="telefono", valor=meta["e164"], confianza="confirmado", detalle=detalle
    )
    dossier.nodos.append(nodo_pivote)

    hallazgos_ig: list[dict] = []
    if disyuntor is None or disyuntor.activa("ignorant"):
        ok_ig, err_ig, hallazgos_ig = _correr_ignorant(meta["e164"])
        if disyuntor is not None:
            disyuntor.registrar("ignorant", ok_ig, err_ig)
        if not ok_ig:
            dossier.errores.append(f"ignorant: {err_ig}")
    elif disyuntor is not None:
        dossier.errores.append("ignorant: fuente degradada en esta investigación (fallos repetidos), omitida.")
    _cruzar_hallazgos(dossier, nodo_pivote, {"ignorant": hallazgos_ig})

    for hallazgo in _dorking_telefono(meta["e164"])[:HUELLA_DORK_RESULTADOS_MAX]:
        nodo_url = Nodo(id=f"url_perfil:{hallazgo['url']}", tipo="url_perfil", valor=hallazgo["url"], confianza="tentativo")
        dossier.nodos.append(nodo_url)
        dossier.aristas.append(
            Arista(origen=nodo_pivote.id, destino=nodo_url.id, tipo_enlace="dorking", fuente="google_dork", peso=0.3)
        )

    get_huella().guardar_dossier(dossier)
    return dossier


# --- Pivote NOMBRE: permutación validada + dorking (Tanda D) -----------------


def _permutar_nombre(nombre_completo: str) -> list[str]:
    """Genera candidatos a alias a partir de un nombre real: 'nombre.apellido',
    'nombreapellido', inicial+apellido, 'nombre_apellido', 'apellidonombre'. Sin
    variante "+año": no hay fecha de nacimiento como entrada, inventar un año sería
    puro ruido. El nombre real es el pivote MÁS DÉBIL (§1/HERRAMIENTA_BUSQUEDA.md) —
    ninguno de estos candidatos se afirma como pivote real sin validarlo (ver
    `buscar_nombre`)."""
    partes = [p for p in nombre_completo.strip().split() if p]
    if len(partes) < 2:
        return []
    nombre, apellido = partes[0], partes[-1]
    candidatos = {
        f"{nombre}.{apellido}",
        f"{nombre}{apellido}",
        f"{nombre[0]}{apellido}",
        f"{nombre}_{apellido}",
        f"{apellido}{nombre}",
    }
    return sorted(c.lower() for c in candidatos)


def _dorking(consultas: list[str]) -> list[dict]:
    """Motor de dorking compartido (Tanda D: nombre/término; Tanda H: teléfono) vía
    DuckDuckGo (`ddgs`) — sin navegador. Se probó (Tanda H) usar el Chromium headless
    como segundo motor de respaldo (Bing, Yandex texto, Mojeek): los tres bloquean el
    headless con CAPTCHA/muro anti-bot, igual que ya le pasaba a Google (motivo
    original de este diseño). La sesión de Chrome visible SÍ los pasa, pero abrirla
    para un dorking de fondo sorprendería al usuario con una ventana inesperada en
    medio de una búsqueda que se supone silenciosa — descartado a propósito, no un
    descuido. `ddgs` ya es dependencia del proyecto (`web_search.py`, fallback de la
    búsqueda de élite). Nunca lanza: fallo de red/rate-limit en una consulta = esa
    consulta no aporta nada, se sigue con las demás."""
    from ddgs import DDGS

    resultados: list[dict] = []
    for consulta in consultas:
        try:
            with DDGS() as ddgs:
                for r in ddgs.text(consulta, max_results=HUELLA_DORK_RESULTADOS_MAX):
                    url = r.get("href") or r.get("url", "")
                    if url:
                        resultados.append({"titulo": r.get("title", url), "url": url})
        except Exception:
            continue
    return resultados


def _dorking_nombre(nombre_completo: str, ciudad: str) -> list[dict]:
    """Consultas dirigidas ('dorks') para el pivote nombre/término: nombre+ciudad y
    perfil de LinkedIn. Ver `_dorking()` para el motor compartido."""
    consultas = [f'"{nombre_completo}" {ciudad}'.strip(), f'site:linkedin.com "{nombre_completo}"']
    return _dorking(consultas)


def _dorking_telefono(numero_e164: str) -> list[dict]:
    """Consulta dirigida para el pivote teléfono (Tanda H): el número exacto entre
    comillas — clasificados, foros, listados de empresa que lo mencionen tal cual.
    Hueco que tenía el motor desde su diseño original (`HERRAMIENTA_BUSQUEDA.md`
    marcaba PhoneInfoga como pieza clave para esto, nunca implementada): a
    diferencia de nombre/término, teléfono no tenía NINGÚN paso de huella-web."""
    return _dorking([f'"{numero_e164}"'])


def buscar_nombre(nombre_completo: str, ciudad: str = "", disyuntor: Optional["DisyuntorFuente"] = None) -> Dossier:
    """Pivote NOMBRE (el más débil en solitario — §1): genera permutaciones de alias
    y solo promueve una a pivote real si `buscar_username` la corrobora con algún
    sitio "confirmado" (≥2 fuentes independientes, la misma regla dura de §5 — no se
    relaja para este pivote). Cribado barato con Socialscan antes de pagar el coste
    de Maigret en cada permutación. Además, dorking (`_dorking_nombre`) por si hay
    perfiles públicos indexados (LinkedIn, prensa...) — resultados de búsqueda, no
    cuentas verificadas, siempre `tentativo`. `disyuntor`: igual que en
    `buscar_username` (Tanda F), solo lo pasa `investigar()`.
    """
    dossier = Dossier(pivote=nombre_completo, tipo="nombre")
    nodo_pivote = Nodo(id=f"nombre:{nombre_completo}", tipo="nombre", valor=nombre_completo, confianza="confirmado")
    dossier.nodos.append(nodo_pivote)

    permutaciones = _permutar_nombre(nombre_completo)
    if not permutaciones:
        dossier.errores.append("Nombre incompleto: hace falta al menos nombre y apellido para permutar.")

    for candidato in permutaciones:
        if disyuntor is not None and not disyuntor.activa("socialscan"):
            break  # sin cribado posible (fuente degradada), seguir generaría solo ruido de Maigret sin filtrar
        with tempfile.TemporaryDirectory(prefix="huella_nombre_") as tmp:
            ok_s, err_s, hallazgos_s = _correr_socialscan(candidato, Path(tmp) / "ss.json")
        if disyuntor is not None:
            disyuntor.registrar("socialscan", ok_s, err_s)
        if not ok_s or not hallazgos_s:
            continue  # cribado: nada en Socialscan, no merece el coste de Maigret

        sub_dossier = buscar_username(candidato, disyuntor=disyuntor)  # reutiliza la Tanda A tal cual
        confirmados = [n for n in sub_dossier.nodos if n.tipo == "url_perfil" and n.confianza == "confirmado"]
        if not confirmados:
            continue

        nodo_candidato = Nodo(id=f"alias:{candidato}", tipo="alias", valor=candidato, confianza="confirmado")
        dossier.nodos.append(nodo_candidato)
        dossier.aristas.append(
            Arista(
                origen=nodo_pivote.id,
                destino=nodo_candidato.id,
                tipo_enlace="permutacion_validada",
                fuente="permutador",
                peso=1.0,
            )
        )
        dossier.errores.extend(f"(vía permutación '{candidato}') {e}" for e in sub_dossier.errores)

    for hallazgo in _dorking_nombre(nombre_completo, ciudad)[: HUELLA_DORK_RESULTADOS_MAX]:
        nodo_url = Nodo(id=f"url_perfil:{hallazgo['url']}", tipo="url_perfil", valor=hallazgo["url"], confianza="tentativo")
        dossier.nodos.append(nodo_url)
        dossier.aristas.append(
            Arista(origen=nodo_pivote.id, destino=nodo_url.id, tipo_enlace="dorking", fuente="google_dork", peso=0.3)
        )

    get_huella().guardar_dossier(dossier)
    return dossier


# --- Pivote CARA: búsqueda inversa por imagen (Yandex) — Tanda D -------------
# Metadatos EXIF de la foto — Tanda I -----------------------------------------

_EXIF_CAMPOS_UTILES = {"Make", "Model", "DateTime", "DateTimeOriginal", "Software"}
_EXIF_GPS_IFD = 0x8825  # tag "GPSInfo" del IFD principal — Pillow lo expone vía get_ifd()


def _gps_a_decimal(valores, ref) -> Optional[float]:
    """(grados, minutos, segundos) + referencia N/S/E/W -> grados decimales.
    `None` si `valores`/`ref` no tienen la forma esperada — nunca lanza."""
    try:
        grados, minutos, segundos = valores
        decimal = float(grados) + float(minutos) / 60 + float(segundos) / 3600
        if ref in ("S", "W"):
            decimal = -decimal
        return round(decimal, 6)
    except Exception:
        return None


def _extraer_exif(ruta_imagen: str) -> Optional[dict]:
    """Metadatos EXIF de una foto LOCAL (cámara, fecha, y GPS si no se limpió al
    subirla) — offline, sin red, gratis (Pillow ya lo cubre, no hace falta
    `exifread`). Solo aplica a rutas locales: una URL es una imagen ajena servida
    por un tercero (casi siempre limpiada de EXIF por la propia plataforma), y
    descargarla solo para leer metadatos sería un fetch de red que este dato no
    justifica. Nunca lanza: sin EXIF, corrupta, o formato no soportado -> `None`,
    en silencio (mismo contrato que `_consultar_gravatar` y similares)."""
    if ruta_imagen.strip().lower().startswith(("http://", "https://")):
        return None
    try:
        from PIL import ExifTags, Image

        with Image.open(ruta_imagen) as img:
            exif = img.getexif()
            if not exif:
                return None
            datos: dict = {}
            for tag_id, valor in exif.items():
                nombre = ExifTags.TAGS.get(tag_id, tag_id)
                if nombre in _EXIF_CAMPOS_UTILES and str(valor).strip():
                    datos[nombre] = str(valor).strip()

            gps_ifd = exif.get_ifd(_EXIF_GPS_IFD)
            if gps_ifd:
                gps = {ExifTags.GPSTAGS.get(k, k): v for k, v in gps_ifd.items()}
                lat = _gps_a_decimal(gps.get("GPSLatitude"), gps.get("GPSLatitudeRef"))
                lon = _gps_a_decimal(gps.get("GPSLongitude"), gps.get("GPSLongitudeRef"))
                if lat is not None and lon is not None:
                    datos["GPS"] = f"{lat}, {lon} (https://www.google.com/maps?q={lat},{lon})"

            return datos or None
    except Exception:
        return None


def buscar_cara(imagen: str) -> Dossier:
    """Pivote CARA: dónde más aparece una foto, vía búsqueda inversa en Yandex
    Images (`browser.buscar_por_imagen`). El pivote más potente y más sensible del
    sistema (reconocimiento facial sobre datos públicos) — SIN gate aquí dentro: el
    gate vive en la tool (`huella_tools.huella_buscar_cara`), `huella.py` no importa
    `confirmar_accion` (mismo criterio de capas que el resto del módulo: el motor
    ejecuta, la capa de tool decide permisos).

    `imagen`: URL (http/https) o ruta local a la foto.
    """
    dossier = Dossier(pivote=imagen, tipo="foto")
    nodo_pivote = Nodo(id=f"foto:{imagen}", tipo="foto", valor=imagen, confianza="confirmado")
    dossier.nodos.append(nodo_pivote)

    # Tanda I: offline, antes de tocar red — corre siempre, aunque Yandex falle abajo.
    exif = _extraer_exif(imagen)
    if exif:
        detalle_exif = " · ".join(f"{clave}: {valor}" for clave, valor in exif.items())
        nodo_exif = Nodo(
            id=f"exif:{imagen}",
            tipo="metadato_exif",
            valor="(ver detalle)",
            confianza="confirmado",  # hecho leído del propio archivo, no una corroboración externa
            detalle=detalle_exif,
        )
        dossier.nodos.append(nodo_exif)
        dossier.aristas.append(
            Arista(origen=nodo_pivote.id, destino=nodo_exif.id, tipo_enlace="metadato", fuente="exif")
        )

    try:
        resultados, captura = get_browser().buscar_por_imagen(imagen)
    except Exception as exc:
        dossier.errores.append(f"Yandex: {exc}")
        get_huella().guardar_dossier(dossier)
        return dossier

    for r in resultados:
        nodo_url = Nodo(id=f"url_perfil:{r['url']}", tipo="url_perfil", valor=r["url"], confianza="tentativo")
        dossier.nodos.append(nodo_url)
        dossier.aristas.append(
            Arista(origen=nodo_pivote.id, destino=nodo_url.id, tipo_enlace="aparece_en", fuente="yandex_cara", peso=0.4)
        )

    # Red de seguridad: el DOM de Yandex cambia con frecuencia (gotcha conocido). Si
    # no dio resultados estructurados pero sí una captura, la visión lee la pantalla
    # (mismo patrón que `navegador_captura`).
    if not resultados and captura:
        try:
            resumen = preguntar_sobre_imagen(
                captura,
                "Esta es una captura de resultados de búsqueda inversa por imagen en "
                "Yandex Images. Lista los sitios o páginas donde aparece la foto "
                "buscada, con su URL si es legible en la captura.",
            )
            dossier.nodos.append(
                Nodo(
                    id=f"vision:{imagen}",
                    tipo="url_perfil",
                    valor="(ver detalle — leído por visión, no del DOM)",
                    confianza="tentativo",
                    detalle=resumen,
                )
            )
            dossier.aristas.append(
                Arista(origen=nodo_pivote.id, destino=f"vision:{imagen}", tipo_enlace="aparece_en", fuente="yandex_cara_vision")
            )
        except Exception as exc:
            dossier.errores.append(f"Visión sobre captura de Yandex: {exc}")

    get_huella().guardar_dossier(dossier)
    return dossier


# --- Pivote TÉRMINO: término libre para la lista de vigilancia (Tanda E) -----


def buscar_termino(texto: str) -> Dossier:
    """Pivote TÉRMINO: para la lista de vigilancia (Tanda E) cuando lo que se
    vigila no es un alias/email/teléfono/nombre-de-persona, sino una frase u otro
    identificador libre. SOLO dorking (`_dorking_nombre` reutilizado tal cual, sin
    permutación ni `buscar_username` — un término libre no es necesariamente el
    nombre de una persona real, permutarlo no tendría sentido). Todos los hallazgos
    son `tentativo` (un resultado de búsqueda no es una cuenta verificada).
    """
    dossier = Dossier(pivote=texto, tipo="termino")
    nodo_pivote = Nodo(id=f"termino:{texto}", tipo="termino", valor=texto, confianza="confirmado")
    dossier.nodos.append(nodo_pivote)

    for hallazgo in _dorking_nombre(texto, "")[:HUELLA_DORK_RESULTADOS_MAX]:
        nodo_url = Nodo(id=f"url_perfil:{hallazgo['url']}", tipo="url_perfil", valor=hallazgo["url"], confianza="tentativo")
        dossier.nodos.append(nodo_url)
        dossier.aristas.append(
            Arista(origen=nodo_pivote.id, destino=nodo_url.id, tipo_enlace="dorking", fuente="google_dork", peso=0.3)
        )

    get_huella().guardar_dossier(dossier)
    return dossier


# --- Bucle de pivote (BFS) + motor de extracción (socid-extractor) -----------

# Campos que socid_extractor puede devolver y que son pivotes nuevos, no metadata
# descriptiva (bio, followers, avatar...). El resto de los 300+ campos posibles se
# ignora a propósito.
_CAMPOS_EMAIL = {"email", "emails", "your_emails", "business_email"}
_CAMPOS_TELEFONO = {"phone", "your_phone"}
# Campos de texto que sirven para CORRELACIONAR cuentas dentro de una misma
# investigación (Tanda K: enlace externo idéntico o bio casi igual), no como
# pivotes nuevos que perseguir — antes `_extraer_pivotes` los descartaba tras
# minar solo email/teléfono/username. Best-effort: `socid_extractor` no garantiza
# estos nombres de campo en todos los sitios (300+ posibles, sin schema fijo) —
# aditivo, no rompe nada si un sitio no trae ninguno.
_CAMPOS_BIO = {"bio", "about", "description"}
_CAMPOS_ENLACE = {"blog", "website", "homepage", "external_url"}


def _extraer_pivotes(url: str) -> tuple[list[tuple[str, str]], dict]:
    """Extrae de una página de perfil vía `socid_extractor`: (a) candidatos a
    pivote nuevo (tipo, valor), igual que siempre (Tanda F); y (b) campos de
    texto para correlación (Tanda K: `bio`/`enlace_externo`) — UN SOLO fetch de
    red sirve para ambos usos, no se repite la petición solo por el segundo.

    Muchos "schemes" apuntan a un endpoint de API, no a la URL canónica del
    perfil (p. ej. github.com/<user> -> api.github.com/users/<user>) —
    `mutate_url()` da esas variantes. Un mismo sitio puede tener VARIAS mutaciones
    útiles a la vez (p. ej. GitHub: perfil + social_accounts, cada uno con campos
    distintos) — se prueban TODAS (más la URL original) y se fusionan los campos,
    no se para en la primera que responda. Nunca lanza: red/parseo fallido en una
    URL candidata = se ignora esa URL (igual que `_consultar_gravatar`), es una
    fuente opcional y de bajo coste por nodo.
    """
    candidatas = [u for u, _ in mutate_url(url)] + [url]
    datos: dict = {}
    for candidata in dict.fromkeys(candidatas):  # dedup preservando orden
        try:
            html, status = socid_parse(candidata, timeout=HUELLA_EXTRACCION_TIMEOUT)
        except Exception:
            continue
        if status != 200:
            continue
        try:
            datos.update(extract(html))
        except Exception:
            continue

    candidatos: list[tuple[str, str]] = []
    texto_correlacion: dict = {}
    for campo, valor in datos.items():
        if not valor or not isinstance(valor, str):
            continue
        campo_l = campo.lower()
        if campo_l in _CAMPOS_EMAIL and "@" in valor:
            # Antes solo se tomaba `valor.split(",")[0]` — un campo con VARIOS
            # emails separados por coma perdía en silencio todos menos el primero.
            # Verificado real: algunos sitios devuelven "email_secundario, ..." en
            # un solo campo de texto.
            for correo in valor.split(","):
                correo = correo.strip()
                if "@" in correo:
                    candidatos.append(("email", correo))
        elif campo_l in _CAMPOS_TELEFONO:
            candidatos.append(("telefono", valor.strip()))
        elif campo_l.endswith("_username") or campo_l in ("username", "nickname"):
            candidatos.append(("alias", valor.strip()))
        elif campo_l in _CAMPOS_BIO and "bio" not in texto_correlacion:
            texto_correlacion["bio"] = valor.strip()
        elif campo_l in _CAMPOS_ENLACE and "enlace_externo" not in texto_correlacion:
            texto_correlacion["enlace_externo"] = valor.strip()
    return candidatos, texto_correlacion


def _clasificar_pivote(valor: str) -> str:
    """Red de seguridad para candidatos que no vienen ya tipados (en la práctica,
    `_extraer_pivotes` los tipa todos): email si tiene "@", teléfono si es mayoría
    dígitos con longitud plausible, si no alias."""
    if "@" in valor:
        return "email"
    digitos = sum(c.isdigit() for c in valor)
    if digitos >= 7 and digitos / max(len(valor), 1) > 0.6:
        return "telefono"
    return "alias"


@dataclass
class Investigacion:
    pivote_semilla: str
    tipo_semilla: str
    dossiers: list[Dossier] = field(default_factory=list)
    # Todo lo descubierto durante el BFS, aunque no llegara a investigarse por el
    # tope de nodos — trazabilidad honesta de lo que el motor vio pero no siguió.
    pivotes_nuevos: list[tuple[str, str]] = field(default_factory=list)
    profundidad_alcanzada: int = 0
    # Aristas CRUZADAS entre dossiers distintos (Tanda D: mismo avatar aunque cambie
    # el alias) — fuera del modelo "una arista vive dentro de un dossier" porque por
    # definición conectan cuentas de búsquedas/pivotes diferentes.
    aristas_correlacion: list[Arista] = field(default_factory=list)
    creado: str = field(default_factory=_now)
    # Tanda F: fuentes que el disyuntor desactivó por fallos repetidos/bloqueo
    # DURANTE esta investigación (no persistente entre investigaciones distintas).
    fuentes_degradadas: list[str] = field(default_factory=list)
    # Motivo de corte si el BFS terminó antes de vaciar la cola: "tope_nodos" |
    # "rendimientos_decrecientes" | "presupuesto_tiempo" | "" (cola vacía, corte natural).
    motivo_corte: str = ""

    def to_dict(self) -> dict:
        return {
            "pivote_semilla": self.pivote_semilla,
            "tipo_semilla": self.tipo_semilla,
            "dossiers": [d.to_dict() for d in self.dossiers],
            "pivotes_nuevos": [list(p) for p in self.pivotes_nuevos],
            "profundidad_alcanzada": self.profundidad_alcanzada,
            "aristas_correlacion": [a.to_dict() for a in self.aristas_correlacion],
            "creado": self.creado,
            "fuentes_degradadas": self.fuentes_degradadas,
            "motivo_corte": self.motivo_corte,
        }

    @classmethod
    def from_dict(cls, datos: dict) -> "Investigacion":
        """Reconstruye una `Investigacion` guardada (Tanda L) para reabrirla vía
        `ampliar_investigacion` — simétrico con `to_dict()`."""
        return cls(
            pivote_semilla=datos["pivote_semilla"],
            tipo_semilla=datos["tipo_semilla"],
            dossiers=[Dossier.from_dict(d) for d in datos.get("dossiers", [])],
            pivotes_nuevos=[tuple(p) for p in datos.get("pivotes_nuevos", [])],
            profundidad_alcanzada=datos.get("profundidad_alcanzada", 0),
            aristas_correlacion=[Arista.from_dict(a) for a in datos.get("aristas_correlacion", [])],
            creado=datos.get("creado", _now()),
            fuentes_degradadas=list(datos.get("fuentes_degradadas", [])),
            motivo_corte=datos.get("motivo_corte", ""),
        )


# --- Correlación por avatar (pHash, Tanda D) ----------------------------------


def _hash_avatar(url: str) -> Optional["imagehash.ImageHash"]:
    """Descarga un avatar y calcula su hash perceptual (pHash). `None` ante
    cualquier fallo (red, imagen corrupta/formato no soportado) — ese avatar
    simplemente no participa en la correlación, nunca rompe el resto."""
    try:
        r = requests.get(url, timeout=HUELLA_AVATAR_TIMEOUT)
        r.raise_for_status()
        return imagehash.phash(Image.open(BytesIO(r.content)))
    except Exception:
        return None


def _correlacionar_avatares(inv: Investigacion) -> list[Arista]:
    """Enlaza cuentas de DISTINTOS pivotes/dossiers que comparten avatar (distancia
    de Hamming ≤ HUELLA_PHASH_UMBRAL) — "aunque cambie el alias" (§ Tanda D). Solo
    tiene sentido comparando cuentas de búsquedas distintas: dentro de UN mismo
    alias, casi todas las cuentas ya comparten avatar por ser la misma persona
    registrándose igual en todas partes, eso no es una correlación nueva.
    """
    candidatos: list[Nodo] = [
        n for d in inv.dossiers for n in d.nodos if n.tipo == "url_perfil" and n.avatar_url
    ]
    hashes: list[tuple[Nodo, "imagehash.ImageHash"]] = []
    for n in candidatos:
        h = _hash_avatar(n.avatar_url)
        if h is not None:
            hashes.append((n, h))

    aristas: list[Arista] = []
    for i in range(len(hashes)):
        n1, h1 = hashes[i]
        for j in range(i + 1, len(hashes)):
            n2, h2 = hashes[j]
            if n1.valor == n2.valor:
                continue
            distancia = h1 - h2
            if distancia <= HUELLA_PHASH_UMBRAL:
                peso = round(1.0 - distancia / max(HUELLA_PHASH_UMBRAL, 1), 2)
                aristas.append(
                    Arista(origen=n1.id, destino=n2.id, tipo_enlace="mismo_avatar", fuente="phash", peso=peso)
                )
    return aristas


def _correlacionar_texto(inv: Investigacion) -> list[Arista]:
    """Enlaza cuentas de DISTINTOS pivotes/dossiers que comparten el mismo enlace
    externo exacto (señal FUERTE, peso 1.0) o una bio casi idéntica (señal más
    débil, ponderada por similitud — Tanda K). Complementa `_correlacionar_avatares`:
    dos cuentas con avatares DISTINTOS pueden seguir siendo la misma persona si
    enlazan la misma web personal o repiten la misma bio.

    Compara por `d.pivote` (identidad de la búsqueda), no por URL como
    `_correlacionar_avatares`: a diferencia del avatar (raro que coincida por
    azar), una bio suele repetirse IDÉNTICA en todas las cuentas de una misma
    búsqueda de alias — comparar ahí generaría solo ruido esperado ("sus propios
    perfiles tienen la misma bio"), no una correlación nueva entre búsquedas.
    """
    candidatos = [
        (n, d.pivote)
        for d in inv.dossiers
        for n in d.nodos
        if n.tipo == "url_perfil" and n.campos_correlacion
    ]

    aristas: list[Arista] = []
    for i in range(len(candidatos)):
        n1, pivote1 = candidatos[i]
        for j in range(i + 1, len(candidatos)):
            n2, pivote2 = candidatos[j]
            if pivote1 == pivote2:
                continue
            enlace1 = n1.campos_correlacion.get("enlace_externo")
            enlace2 = n2.campos_correlacion.get("enlace_externo")
            if enlace1 and enlace2 and enlace1 == enlace2:
                aristas.append(
                    Arista(origen=n1.id, destino=n2.id, tipo_enlace="correlacion_enlace", fuente="socid_extractor", peso=1.0)
                )
                continue
            bio1 = n1.campos_correlacion.get("bio")
            bio2 = n2.campos_correlacion.get("bio")
            if bio1 and bio2:
                ratio = SequenceMatcher(None, bio1, bio2).ratio()
                if ratio >= HUELLA_BIO_SIMILITUD_UMBRAL:
                    aristas.append(
                        Arista(
                            origen=n1.id, destino=n2.id, tipo_enlace="correlacion_bio",
                            fuente="socid_extractor", peso=round(ratio, 2),
                        )
                    )
    return aristas


_BUSCADORES = {
    "alias": buscar_username,
    "email": buscar_email,
    "telefono": buscar_telefono,
    "nombre": buscar_nombre,
}


def _clave_pivote(tipo: str, valor: str) -> tuple[str, str]:
    """Clave de dedup normalizada para el BFS: strip+lower siempre, y además E.164
    para teléfonos (Tanda F) — sin esto, '+34 600 123 456' y '0034600123456'
    contaban como dos pivotes DISTINTOS y el mismo número se investigaba dos veces."""
    valor = valor.strip()
    if tipo == "telefono":
        valor = _normalizar_telefono(valor)
    return (tipo, valor.lower())


def _procesar_bfs(
    inv: Investigacion,
    cola: list[tuple[str, str, int]],
    visitados: set[tuple[str, str]],
    en_cola: set[tuple[str, str]],
    disyuntor: "DisyuntorFuente",
    profundidad: int,
) -> Investigacion:
    """Cuerpo del bucle de pivote (BFS) — extraído de `investigar()` en la Tanda L
    para que `ampliar_investigacion()` lo reutilice EXACTO sobre una `Investigacion`
    YA existente, en vez de duplicar esta lógica. Recibe el estado inicial ya
    armado (semilla nueva desde `investigar()`, o un pivote añadido a un
    expediente viejo desde `ampliar_investigacion()`) y hace todo lo demás:
    explorar, extraer, correlacionar y persistir.

    Deduplicados (en cola Y en visitados, Tanda F — antes solo se deduplicaba al
    desencolar, dejando encolar el mismo pivote varias veces si dos rutas
    distintas lo descubrían en el mismo nivel), con topes duros de nodos y de
    TIEMPO totales para que el bucle nunca explote, entre en ciclo, ni se quede
    corriendo horas.

    Explora primero las cuentas de mayor puntuación (confirmadas/probables), pero
    NO se limita a ellas en solitario: verificado con datos reales que muchas cuentas
    solo las encuentra Maigret (tentativo, presencia por patrón) y aun así revelan
    identificadores nuevos genuinos (p. ej. redes sociales enlazadas en GitHub) — la
    disciplina de "no afirmar sin corroborar" vive en la PUNTUACIÓN de cada hallazgo,
    no en qué cuentas se exploran para seguir tirando del hilo.

    Tanda F — sostenibilidad frente a bloqueos: `disyuntor` desactiva una fuente
    (Maigret, Socialscan, Sherlock, user-scanner, GHunt, ignorant) tras fallos
    repetidos o un error con pinta de rate-limit/bloqueo, para no seguir
    insistiendo con el resto de pivotes. Y una parada por rendimientos
    decrecientes corta el BFS si varios pivotes seguidos no aportan NADA nuevo,
    aunque queden nodos en cola y presupuesto de nodos/tiempo sin agotar.
    """
    inv.motivo_corte = ""  # de esta pasada, no de una anterior (relevante al reabrir un expediente, Tanda L)
    sin_novedad_seguidos = 0
    inicio = time.monotonic()

    def _encolar(tipo_nuevo: str, valor_nuevo: str, nivel_nuevo: int, clave_actual: tuple[str, str]) -> bool:
        clave_nuevo = _clave_pivote(tipo_nuevo, valor_nuevo)
        if clave_nuevo in visitados or clave_nuevo in en_cola or clave_nuevo == clave_actual:
            return False
        en_cola.add(clave_nuevo)
        inv.pivotes_nuevos.append((tipo_nuevo, valor_nuevo))
        cola.append((valor_nuevo, tipo_nuevo, nivel_nuevo))
        return True

    while cola:
        if len(visitados) >= HUELLA_MAX_NODOS_TOTAL:
            inv.motivo_corte = "tope_nodos"
            break
        if time.monotonic() - inicio >= HUELLA_TIEMPO_MAX_SEGUNDOS:
            inv.motivo_corte = "presupuesto_tiempo"
            break

        pivote_actual, tipo_actual, nivel = cola.pop(0)
        clave = _clave_pivote(tipo_actual, pivote_actual)
        en_cola.discard(clave)
        if clave in visitados or tipo_actual not in _BUSCADORES:
            continue
        visitados.add(clave)

        dossier = _BUSCADORES[tipo_actual](pivote_actual, disyuntor=disyuntor)
        inv.dossiers.append(dossier)
        inv.profundidad_alcanzada = max(inv.profundidad_alcanzada, nivel)

        nuevos_esta_iteracion = 0

        if nivel < profundidad:
            # Pivotes YA estructurados por el propio colector (p. ej. GHunt: email/
            # teléfono de su JSON, Tanda F) — coste cero, sin fetch de red de por
            # medio, así que no cuentan contra HUELLA_MAX_PIVOTES_POR_NIVEL (ese tope
            # existe para acotar fetches http, no datos que ya se tenían en mano).
            for nodo in dossier.nodos:
                for tipo_nuevo, valor_nuevo in nodo.pivotes:
                    if _encolar(tipo_nuevo, valor_nuevo, nivel + 1, clave):
                        nuevos_esta_iteracion += 1

            # Prioriza primero los sitios que `socid_extractor` sabe extraer de
            # verdad (tiene una mutación de URL registrada): con decenas de
            # perfiles "tentativo" empatados a la misma puntuación (un solo
            # Maigret), ordenar solo por puntuación gastaba el tope de
            # exploración en sitios sin scheme conocido (bit.ly, blogspot...) en
            # vez de en los que sí revelan datos (GitHub, GitLab...) — verificado
            # con datos reales (alias 'gaearon').
            candidatos_nodo = sorted(
                (n for n in dossier.nodos if n.tipo == "url_perfil" and n.valor.startswith("http")),
                key=lambda n: (bool(mutate_url(n.valor)), n.puntuacion),
                reverse=True,
            )
            for nodo in candidatos_nodo[:HUELLA_MAX_PIVOTES_POR_NIVEL]:
                pivotes_nodo, texto_correlacion = _extraer_pivotes(nodo.valor)
                nodo.campos_correlacion = texto_correlacion  # Tanda K: para _correlacionar_texto()
                for tipo_nuevo, valor_nuevo in pivotes_nodo:
                    if _encolar(tipo_nuevo, valor_nuevo, nivel + 1, clave):
                        nuevos_esta_iteracion += 1

        sin_novedad_seguidos = 0 if nuevos_esta_iteracion else sin_novedad_seguidos + 1
        if sin_novedad_seguidos >= HUELLA_VENTANA_SIN_NOVEDAD and cola:
            inv.motivo_corte = "rendimientos_decrecientes"
            break

    inv.fuentes_degradadas = disyuntor.degradadas()
    inv.aristas_correlacion = _correlacionar_avatares(inv) + _correlacionar_texto(inv)
    get_huella().guardar_investigacion(inv)
    return inv


def investigar(pivote: str, tipo: str, profundidad: int = HUELLA_PROFUNDIDAD_MAX) -> Investigacion:
    """Bucle de pivote (BFS) desde una semilla nueva: resuelve `pivote` con el
    colector de su tipo (Tanda A/B, sin tocar su firma), extrae nuevos
    identificadores (`_extraer_pivotes`) de las cuentas que encuentra y los
    encola para el siguiente nivel — hasta `profundidad` niveles. El bucle en sí
    vive en `_procesar_bfs()` (Tanda L); esta función solo arma el estado inicial
    de una investigación DESDE CERO. Para añadir un pivote a una investigación YA
    guardada, ver `ampliar_investigacion()`.
    """
    inv = Investigacion(pivote_semilla=pivote, tipo_semilla=tipo)
    disyuntor = DisyuntorFuente()
    clave_semilla = _clave_pivote(tipo, pivote)
    visitados: set[tuple[str, str]] = set()
    en_cola: set[tuple[str, str]] = {clave_semilla}
    cola: list[tuple[str, str, int]] = [(pivote, tipo, 0)]
    return _procesar_bfs(inv, cola, visitados, en_cola, disyuntor, profundidad)


def ampliar_investigacion(
    investigacion_id: int, nuevo_pivote: str, nuevo_tipo: str, profundidad: int = HUELLA_PROFUNDIDAD_MAX
) -> Investigacion:
    """Tanda L — "expediente reabrible": añade UN pivote nuevo a una investigación
    YA guardada (por su id, ver `Huella.listar_investigaciones`) en vez de
    empezar de cero. Reconstruye `visitados` a partir de los dossiers ya
    presentes (no se re-busca nada ya resuelto) y arranca el BFS solo con el
    pivote nuevo, sobre el MISMO objeto `Investigacion` — así lo viejo y lo nuevo
    quedan en la misma lista de dossiers, y `_procesar_bfs` vuelve a correlacionar
    (avatar + texto/bio, Tanda K) sobre el conjunto COMBINADO: una cuenta que
    antes no encajaba con nada puede de repente enlazar con algo de la rama
    recién añadida.

    Se guarda como una fila NUEVA en `huella_investigaciones` (nunca un UPDATE
    sobre la vieja) — mismo criterio que `huella_dossiers`, que tampoco
    actualiza: se conserva el histórico completo de cómo creció el expediente.

    Lanza `ValueError` (no "nunca lanza": esto es validación de entrada del
    usuario, no una fuente externa que pueda fallar) si el id no existe o si el
    pivote nuevo ya estaba investigado en ese expediente — la capa de tool
    (`huella_tools.huella_ampliar`) lo convierte en un mensaje de error legible.
    """
    datos = get_huella().obtener_investigacion(investigacion_id)
    if datos is None:
        raise ValueError(f"no existe ninguna investigación guardada con id {investigacion_id}.")
    inv = Investigacion.from_dict(datos)

    visitados: set[tuple[str, str]] = {_clave_pivote(d.tipo, d.pivote) for d in inv.dossiers}
    clave_nueva = _clave_pivote(nuevo_tipo, nuevo_pivote)
    if clave_nueva in visitados:
        raise ValueError(f"'{nuevo_pivote}' ({nuevo_tipo}) ya está investigado en este expediente.")

    disyuntor = DisyuntorFuente()
    en_cola: set[tuple[str, str]] = {clave_nueva}
    cola: list[tuple[str, str, int]] = [(nuevo_pivote, nuevo_tipo, 0)]
    return _procesar_bfs(inv, cola, visitados, en_cola, disyuntor, profundidad)


# --- Protección continua: diff + auditoría periódica (Tanda E) ---------------


def _diferenciar(anterior: Optional[dict], nuevo: Dossier) -> tuple[list[Nodo], list[Nodo]]:
    """Compara el dossier ANTERIOR (dict, de `Huella.ultimo_dossier`) contra el
    `nuevo` recién buscado, por `(tipo, valor normalizado)`. `anterior=None`
    (primera vez que se audita este pivote) devuelve listas vacías A PROPÓSITO: no
    tiene sentido avisar de "todo es nuevo" en la línea base — sería puro ruido.

    Devuelve `(nodos_nuevos, brechas_nuevas)` — `brechas_nuevas` es el subconjunto
    de `nodos_nuevos` con `tipo == "brecha"`, la señal que dispara nivel 3.
    """
    if anterior is None:
        return [], []
    conocidos = {(n["tipo"], n["valor"].strip().lower()) for n in anterior.get("nodos", [])}
    nuevos = [n for n in nuevo.nodos if (n.tipo, n.valor.strip().lower()) not in conocidos]
    brechas_nuevas = [n for n in nuevos if n.tipo == "brecha"]
    return nuevos, brechas_nuevas


@dataclass
class ResultadoAuditoria:
    pivote: str
    tipo: str
    hay_novedades: bool
    nodos_nuevos: list[Nodo]
    brechas_nuevas: list[Nodo]
    mensaje: str


def auditar_pivote(tipo: str, valor: str) -> ResultadoAuditoria:
    """Re-audita un pivote/término vigilado y compara contra su última auditoría
    (`Huella.ultimo_dossier`, pedido ANTES de buscar — si no, compararíamos el
    dossier nuevo contra sí mismo). Registra un episodio (`tipo="huella_auditoria"`)
    con el resumen, para que quede en la línea de tiempo del usuario. Reutilizable
    tanto por el demonio proactivo (mensual) como por una tool bajo demanda.
    """
    anterior = get_huella().ultimo_dossier(valor)

    if tipo == "termino":
        nuevo = buscar_termino(valor)
    elif tipo in _BUSCADORES:
        nuevo = _BUSCADORES[tipo](valor)
    else:
        raise ValueError(f"tipo de vigilancia no soportado: {tipo!r}")

    nodos_nuevos, brechas_nuevas = _diferenciar(anterior, nuevo)
    hay_novedades = bool(nodos_nuevos)

    if anterior is None:
        mensaje = f"Primera auditoría de '{valor}' ({tipo}): línea base guardada, {len(nuevo.nodos)} elemento(s)."
    elif hay_novedades:
        partes = [f"{len(nodos_nuevos)} novedad(es) en '{valor}' ({tipo})"]
        if brechas_nuevas:
            partes.append("⚠ brecha(s) NUEVA(s): " + ", ".join(n.valor for n in brechas_nuevas))
        otras = [n for n in nodos_nuevos if n.tipo != "brecha"]
        if otras:
            partes.append(", ".join(f"{n.tipo}:{n.valor}" for n in otras[:5]))
        mensaje = " — ".join(partes)
    else:
        mensaje = f"Auditoría de '{valor}' ({tipo}): sin novedades."

    get_episodica().registrar(mensaje, tipo="huella_auditoria", entidad=valor)

    return ResultadoAuditoria(
        pivote=valor,
        tipo=tipo,
        hay_novedades=hay_novedades,
        nodos_nuevos=nodos_nuevos,
        brechas_nuevas=brechas_nuevas,
        mensaje=mensaje,
    )
