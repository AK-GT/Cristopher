"""Motor de huella digital por pivotes (OSINT) — Tanda A: cimientos + pivote username.

Modelo de datos común (Nodo/Arista/Dossier) que usarán las tandas B-E, y el primer
pivote real: username, vía Maigret (amplitud, 3000+ sitios) + Socialscan (alta
confianza, endpoints de registro). Un sitio confirmado por AMBAS herramientas se
marca "confirmado"; por una sola, "tentativo" (§5/regla de ≥2 fuentes de
HERRAMIENTA_BUSQUEDA.md — la puntuación numérica fina llega en la Tanda C).

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

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

from cristopher.config import HUELLA_DIR, HUELLA_MAIGRET_TOP_SITES, HUELLA_TIMEOUT


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
    tipo: str  # alias|email|telefono|nombre|url_perfil|foto|brecha
    valor: str
    confianza: str = "tentativo"  # "tentativo" | "confirmado" (puntuación fina: Tanda C)

    def to_dict(self) -> dict:
        return {"id": self.id, "tipo": self.tipo, "valor": self.valor, "confianza": self.confianza}


@dataclass
class Arista:
    origen: str
    destino: str
    tipo_enlace: str
    fuente: str
    peso: float = 1.0  # sin lógica de ponderación todavía (llega en la Tanda C)

    def to_dict(self) -> dict:
        return {
            "origen": self.origen,
            "destino": self.destino,
            "tipo_enlace": self.tipo_enlace,
            "fuente": self.fuente,
            "peso": self.peso,
        }


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

    def guardar_dossier(self, dossier: Dossier) -> int:
        with self._lock, self._conn:
            cur = self._conn.execute(
                "INSERT INTO huella_dossiers (pivote, tipo, datos, creado) VALUES (?, ?, ?, ?)",
                (dossier.pivote, dossier.tipo, json.dumps(dossier.to_dict(), ensure_ascii=False), dossier.creado),
            )
            return cur.lastrowid

    def ultimo_dossier(self, pivote: str) -> Optional[dict]:
        with self._lock:
            row = self._conn.execute(
                "SELECT datos FROM huella_dossiers WHERE pivote=? ORDER BY id DESC LIMIT 1",
                (pivote,),
            ).fetchone()
            return json.loads(row[0]) if row else None


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
        if sitio and url:
            hallazgos.append({"sitio": sitio, "url": url})
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


def _clave_sitio(sitio: str) -> str:
    """Normaliza el nombre de sitio para cruzar resultados entre herramientas
    (p. ej. 'GitHub' de Socialscan vs 'GitHub' de Maigret; distinta capitalización
    o sufijos rompería el cruce)."""
    return sitio.strip().lower()


def buscar_username(alias: str) -> Dossier:
    """Pivote USERNAME: lanza Maigret + Socialscan y cruza resultados.

    Un sitio hallado por AMBAS herramientas se marca "confirmado"; por una sola,
    "tentativo". Cada fallo de herramienta se guarda en `dossier.errores` (nunca se
    oculta), y el dossier se persiste siempre, aunque una de las dos falle.
    """
    dossier = Dossier(pivote=alias, tipo="alias")

    with tempfile.TemporaryDirectory(prefix="huella_") as tmp:
        tmp_path = Path(tmp)

        ok_m, err_m, hallazgos_m = _correr_maigret(alias, tmp_path / "maigret")
        if not ok_m:
            dossier.errores.append(f"Maigret: {err_m}")

        ok_s, err_s, hallazgos_s = _correr_socialscan(alias, tmp_path / "socialscan.json")
        if not ok_s:
            dossier.errores.append(f"Socialscan: {err_s}")

    por_sitio: dict[str, dict] = {}
    for h in hallazgos_m:
        clave = _clave_sitio(h["sitio"])
        por_sitio.setdefault(clave, {"sitio": h["sitio"], "url": h["url"], "fuentes": set()})
        por_sitio[clave]["fuentes"].add("maigret")
    for h in hallazgos_s:
        clave = _clave_sitio(h["sitio"])
        entrada = por_sitio.setdefault(clave, {"sitio": h["sitio"], "url": h["url"] or "", "fuentes": set()})
        if not entrada["url"] and h["url"]:
            entrada["url"] = h["url"]
        entrada["fuentes"].add("socialscan")

    nodo_pivote = Nodo(id=f"alias:{alias}", tipo="alias", valor=alias, confianza="confirmado")
    dossier.nodos.append(nodo_pivote)

    for clave, entrada in sorted(por_sitio.items()):
        confirmado = len(entrada["fuentes"]) >= 2
        nodo = Nodo(
            id=f"url_perfil:{entrada['url'] or clave}",
            tipo="url_perfil",
            valor=entrada["url"] or entrada["sitio"],
            confianza="confirmado" if confirmado else "tentativo",
        )
        dossier.nodos.append(nodo)
        for fuente in sorted(entrada["fuentes"]):
            dossier.aristas.append(
                Arista(origen=nodo_pivote.id, destino=nodo.id, tipo_enlace="cuenta_en", fuente=fuente)
            )

    get_huella().guardar_dossier(dossier)
    return dossier
