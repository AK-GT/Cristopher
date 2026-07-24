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

import hashlib
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
    HUELLA_DIR,
    HUELLA_DORK_RESULTADOS_MAX,
    HUELLA_EXTRACCION_TIMEOUT,
    HUELLA_MAIGRET_TOP_SITES,
    HUELLA_MAX_NODOS_TOTAL,
    HUELLA_MAX_PIVOTES_POR_NIVEL,
    HUELLA_PHASH_UMBRAL,
    HUELLA_PROFUNDIDAD_MAX,
    HUELLA_TIMEOUT,
)
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
    tipo: str  # alias|email|telefono|nombre|url_perfil|foto|brecha
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


def _clave_sitio(sitio: str) -> str:
    """Normaliza el nombre de sitio para cruzar resultados entre herramientas
    (p. ej. 'GitHub' de Socialscan vs 'GitHub' de Maigret; distinta capitalización
    o sufijos rompería el cruce)."""
    return sitio.strip().lower()


# Peso por fuente según su fuerza real (§5: "enlaces fuertes... pesan más que débiles").
# Endpoint de registro confirmado (Socialscan/user-scanner) > dato de cuenta real (GHunt) >
# perfil público verificable (Gravatar) > check de login/registro (ignorant) > presencia
# por patrón de texto (Maigret, más falsos positivos). Fuentes futuras no listadas: 0.3.
PESO_FUENTE = {
    "socialscan": 0.6,
    "user-scanner": 0.6,
    "ghunt": 0.6,
    "gravatar": 0.5,
    "ignorant": 0.45,
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


def buscar_username(alias: str) -> Dossier:
    """Pivote USERNAME: lanza Maigret + Socialscan y cruza resultados (puntuados vía
    `_cruzar_hallazgos`/`_puntuar`). Cada fallo de herramienta se guarda en
    `dossier.errores` (nunca se oculta), y el dossier se persiste siempre, aunque una
    de las dos falle.
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

    nodo_pivote = Nodo(id=f"alias:{alias}", tipo="alias", valor=alias, confianza="confirmado")
    dossier.nodos.append(nodo_pivote)
    _cruzar_hallazgos(dossier, nodo_pivote, {"maigret": hallazgos_m, "socialscan": hallazgos_s})

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


def _correr_ghunt(email: str) -> tuple[bool, str, str]:
    """Devuelve (ok, mensaje_error_o_vacio, texto_de_salida).

    Usa `--json` (confirmado real: `ghunt email --help` lo expone) en vez de
    parsear el stdout con colores/ASCII-art de la CLI.
    """
    if not _ghunt_configurado():
        return (
            False,
            "GHunt no está instalado/configurado (opcional: instálalo aislado con "
            "'pipx install ghunt' y ejecuta 'ghunt login' una vez; ver requirements.txt).",
            "",
        )
    with tempfile.TemporaryDirectory(prefix="huella_ghunt_") as tmp:
        out_file = Path(tmp) / "ghunt.json"
        cmd = ["ghunt", "email", email, "--json", str(out_file)]
        ok, salida = _ejecutar_herramienta("ghunt", cmd, HUELLA_TIMEOUT)
        if not ok:
            return False, salida, ""
        if not out_file.exists():
            return False, "ERROR: GHunt no generó el reporte esperado.", ""
        try:
            datos = json.loads(out_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            return False, f"ERROR: no pude parsear la salida de GHunt: {exc}", ""

    return True, "", json.dumps(datos, ensure_ascii=False, indent=2)[:2000]


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


def buscar_email(email: str) -> Dossier:
    """Pivote EMAIL: user-scanner (servicios registrados) + GHunt (si Gmail y está
    configurado) + Gravatar (perfil público) + XposedOrNot (brechas).

    Los nodos `url_perfil` pasan por la misma puntuación fina que `buscar_username`
    (`_cruzar_hallazgos`). Los nodos `brecha` se marcan `confianza="expuesto"` (hecho
    directo de base de datos, no corroboración de identidad — no pasa por `_puntuar`).
    """
    dossier = Dossier(pivote=email, tipo="email")
    nodo_pivote = Nodo(id=f"email:{email}", tipo="email", valor=email, confianza="confirmado")
    dossier.nodos.append(nodo_pivote)

    with tempfile.TemporaryDirectory(prefix="huella_") as tmp:
        ok_us, err_us, hallazgos_us = _correr_user_scanner(email, Path(tmp) / "user_scanner.json")
    if not ok_us:
        dossier.errores.append(f"user-scanner: {err_us}")

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

    _cruzar_hallazgos(dossier, nodo_pivote, {"user-scanner": hallazgos_us, "gravatar": hallazgos_gravatar})

    if email.strip().lower().endswith(("@gmail.com", "@googlemail.com")):
        ok_gh, err_gh, salida_gh = _correr_ghunt(email)
        if not ok_gh:
            dossier.errores.append(f"GHunt: {err_gh}")
        elif salida_gh.strip():
            dossier.nodos.append(
                Nodo(
                    id=f"ghunt:{email}",
                    tipo="nombre",
                    valor="(ver detalle)",
                    confianza="tentativo",
                    detalle=salida_gh.strip()[:2000],
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


def buscar_telefono(numero: str) -> Dossier:
    """Pivote TELÉFONO: `phonenumbers` (país/operadora/tipo, offline) + `ignorant`
    (apps donde el número está registrado)."""
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

    ok_ig, err_ig, hallazgos_ig = _correr_ignorant(meta["e164"])
    if not ok_ig:
        dossier.errores.append(f"ignorant: {err_ig}")
    _cruzar_hallazgos(dossier, nodo_pivote, {"ignorant": hallazgos_ig})

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


def _dorking_nombre(nombre_completo: str, ciudad: str) -> list[dict]:
    """Consultas dirigidas ('dorks') vía DuckDuckGo (`ddgs`) — sin navegador: Google
    bloquea el Chromium headless sin perfil persistente con un muro anti-bot
    ("unusual traffic"), verificado real al intentarlo por Playwright. `ddgs` ya es
    dependencia del proyecto (`web_search.py`, fallback de la búsqueda de élite).
    Nunca lanza: fallo de red/rate-limit = lista vacía."""
    from ddgs import DDGS

    consultas = [f'"{nombre_completo}" {ciudad}'.strip(), f'site:linkedin.com "{nombre_completo}"']
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


def buscar_nombre(nombre_completo: str, ciudad: str = "") -> Dossier:
    """Pivote NOMBRE (el más débil en solitario — §1): genera permutaciones de alias
    y solo promueve una a pivote real si `buscar_username` la corrobora con algún
    sitio "confirmado" (≥2 fuentes independientes, la misma regla dura de §5 — no se
    relaja para este pivote). Cribado barato con Socialscan antes de pagar el coste
    de Maigret en cada permutación. Además, dorking (`_dorking_nombre`) por si hay
    perfiles públicos indexados (LinkedIn, prensa...) — resultados de búsqueda, no
    cuentas verificadas, siempre `tentativo`.
    """
    dossier = Dossier(pivote=nombre_completo, tipo="nombre")
    nodo_pivote = Nodo(id=f"nombre:{nombre_completo}", tipo="nombre", valor=nombre_completo, confianza="confirmado")
    dossier.nodos.append(nodo_pivote)

    permutaciones = _permutar_nombre(nombre_completo)
    if not permutaciones:
        dossier.errores.append("Nombre incompleto: hace falta al menos nombre y apellido para permutar.")

    for candidato in permutaciones:
        with tempfile.TemporaryDirectory(prefix="huella_nombre_") as tmp:
            ok_s, _, hallazgos_s = _correr_socialscan(candidato, Path(tmp) / "ss.json")
        if not ok_s or not hallazgos_s:
            continue  # cribado: nada en Socialscan, no merece el coste de Maigret

        sub_dossier = buscar_username(candidato)  # reutiliza la Tanda A tal cual
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


# --- Bucle de pivote (BFS) + motor de extracción (socid-extractor) -----------

# Campos que socid_extractor puede devolver y que son pivotes nuevos, no metadata
# descriptiva (bio, followers, avatar...). El resto de los 300+ campos posibles se
# ignora a propósito.
_CAMPOS_EMAIL = {"email", "emails", "your_emails", "business_email"}
_CAMPOS_TELEFONO = {"phone", "your_phone"}


def _extraer_pivotes(url: str) -> list[tuple[str, str]]:
    """Extrae candidatos a pivote nuevo (tipo, valor) de una página de perfil vía
    `socid_extractor`. Muchos "schemes" apuntan a un endpoint de API, no a la URL
    canónica del perfil (p. ej. github.com/<user> -> api.github.com/users/<user>) —
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
    for campo, valor in datos.items():
        if not valor or not isinstance(valor, str):
            continue
        campo_l = campo.lower()
        if campo_l in _CAMPOS_EMAIL and "@" in valor:
            candidatos.append(("email", valor.split(",")[0].strip()))
        elif campo_l in _CAMPOS_TELEFONO:
            candidatos.append(("telefono", valor.strip()))
        elif campo_l.endswith("_username") or campo_l in ("username", "nickname"):
            candidatos.append(("alias", valor.strip()))
    return candidatos


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

    def to_dict(self) -> dict:
        return {
            "pivote_semilla": self.pivote_semilla,
            "tipo_semilla": self.tipo_semilla,
            "dossiers": [d.to_dict() for d in self.dossiers],
            "pivotes_nuevos": [list(p) for p in self.pivotes_nuevos],
            "profundidad_alcanzada": self.profundidad_alcanzada,
            "aristas_correlacion": [a.to_dict() for a in self.aristas_correlacion],
            "creado": self.creado,
        }


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


_BUSCADORES = {
    "alias": buscar_username,
    "email": buscar_email,
    "telefono": buscar_telefono,
    "nombre": buscar_nombre,
}


def investigar(pivote: str, tipo: str, profundidad: int = HUELLA_PROFUNDIDAD_MAX) -> Investigacion:
    """Bucle de pivote (BFS): resuelve `pivote` con el colector de su tipo (Tanda
    A/B, sin tocar su firma), extrae nuevos identificadores (`_extraer_pivotes`) de
    las cuentas que encuentra y los encola para el siguiente nivel — hasta
    `profundidad` niveles, deduplicados, con topes duros de nodos por nivel y en
    total para que el bucle nunca explote ni entre en ciclo.

    Explora primero las cuentas de mayor puntuación (confirmadas/probables), pero
    NO se limita a ellas en solitario: verificado con datos reales que muchas cuentas
    solo las encuentra Maigret (tentativo, presencia por patrón) y aun así revelan
    identificadores nuevos genuinos (p. ej. redes sociales enlazadas en GitHub) — la
    disciplina de "no afirmar sin corroborar" vive en la PUNTUACIÓN de cada hallazgo,
    no en qué cuentas se exploran para seguir tirando del hilo.
    """
    inv = Investigacion(pivote_semilla=pivote, tipo_semilla=tipo)
    visitados: set[tuple[str, str]] = set()
    cola: list[tuple[str, str, int]] = [(pivote, tipo, 0)]

    while cola:
        if len(visitados) >= HUELLA_MAX_NODOS_TOTAL:
            break
        pivote_actual, tipo_actual, nivel = cola.pop(0)
        clave = (tipo_actual, pivote_actual.strip().lower())
        if clave in visitados or tipo_actual not in _BUSCADORES:
            continue
        visitados.add(clave)

        dossier = _BUSCADORES[tipo_actual](pivote_actual)
        inv.dossiers.append(dossier)
        inv.profundidad_alcanzada = max(inv.profundidad_alcanzada, nivel)

        if nivel >= profundidad:
            continue

        # Prioriza primero los sitios que `socid_extractor` sabe extraer de verdad
        # (tiene una mutación de URL registrada): con decenas de perfiles "tentativo"
        # empatados a la misma puntuación (un solo Maigret), ordenar solo por
        # puntuación gastaba el tope de exploración en sitios sin scheme conocido
        # (bit.ly, blogspot...) en vez de en los que sí revelan datos (GitHub,
        # GitLab...) — verificado con datos reales (alias 'gaearon').
        candidatos_nodo = sorted(
            (n for n in dossier.nodos if n.tipo == "url_perfil" and n.valor.startswith("http")),
            key=lambda n: (bool(mutate_url(n.valor)), n.puntuacion),
            reverse=True,
        )
        for nodo in candidatos_nodo[:HUELLA_MAX_PIVOTES_POR_NIVEL]:
            for tipo_nuevo, valor_nuevo in _extraer_pivotes(nodo.valor):
                clave_nuevo = (tipo_nuevo, valor_nuevo.strip().lower())
                if clave_nuevo in visitados or clave_nuevo == clave:
                    continue
                inv.pivotes_nuevos.append((tipo_nuevo, valor_nuevo))
                cola.append((valor_nuevo, tipo_nuevo, nivel + 1))

    inv.aristas_correlacion = _correlacionar_avatares(inv)
    get_huella().guardar_investigacion(inv)
    return inv
