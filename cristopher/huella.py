"""Motor de huella digital por pivotes (OSINT). Tanda A: cimientos + pivote username.
Tanda B: pivotes email, brechas y teléfono.

Modelo de datos común (Nodo/Arista/Dossier) que usarán las tandas C-E, y tres pivotes
reales:
  - username, vía Maigret (amplitud, 3000+ sitios) + Socialscan (alta confianza,
    endpoints de registro).
  - email, vía user-scanner (servicios registrados) + GHunt (Gmail, opcional) +
    Gravatar (perfil público) + XposedOrNot (brechas).
  - teléfono, vía `phonenumbers` (país/operadora/tipo, offline) + ignorant (apps
    donde está registrado).
Un sitio confirmado por ≥2 fuentes independientes se marca "confirmado"; por una
sola, "tentativo" (§5/regla de ≥2 fuentes de HERRAMIENTA_BUSQUEDA.md — la
puntuación numérica fina llega en la Tanda C). Los nodos "brecha" son la excepción
explícita: un hecho directo de base de datos, no una corroboración de identidad, se
marcan `confianza="expuesto"` y no consumen esa regla (decisión de la Tanda B).

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
from pathlib import Path
from typing import Optional

import httpx
import phonenumbers
import requests
import trio
from phonenumbers import PhoneNumberType, carrier, geocoder
from xposedornot import XposedOrNot

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
    # "tentativo" | "confirmado" (corroboración de identidad, puntuación fina: Tanda C) |
    # "expuesto" (solo tipo="brecha": hecho directo de base de datos, no corroboración de
    # identidad — no consume la regla de ≥2 fuentes, ver Tanda B).
    confianza: str = "tentativo"
    # Texto descriptivo determinista calculado sobre el propio nodo (p. ej. país/operadora/
    # tipo de línea de un teléfono, vía `phonenumbers`) — no implica una fuente externa
    # corroborando una identidad, así que no genera Arista ni consume la escala de confianza.
    detalle: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "tipo": self.tipo,
            "valor": self.valor,
            "confianza": self.confianza,
            "detalle": self.detalle,
        }


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


def _cruzar_hallazgos(
    dossier: Dossier, nodo_origen: Nodo, hallazgos_por_fuente: dict[str, list[dict]]
) -> None:
    """Cruza hallazgos {sitio, url} de varias fuentes y añade nodos `url_perfil` +
    aristas al dossier. Un sitio hallado por ≥2 fuentes se marca "confirmado"; por
    una sola, "tentativo" (regla dura de §5/HERRAMIENTA_BUSQUEDA.md). Reutilizado
    por todos los pivotes que producen listas de cuentas (username, email)."""
    por_sitio: dict[str, dict] = {}
    for fuente, hallazgos in hallazgos_por_fuente.items():
        for h in hallazgos:
            clave = _clave_sitio(h["sitio"])
            entrada = por_sitio.setdefault(clave, {"sitio": h["sitio"], "url": h.get("url", ""), "fuentes": set()})
            if not entrada["url"] and h.get("url"):
                entrada["url"] = h["url"]
            entrada["fuentes"].add(fuente)

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
                Arista(origen=nodo_origen.id, destino=nodo.id, tipo_enlace="cuenta_en", fuente=fuente)
            )


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
    """Devuelve (ok, mensaje_error_o_vacio, texto_de_salida)."""
    if not _ghunt_configurado():
        return (
            False,
            "GHunt no está instalado/configurado (opcional: instálalo aislado con "
            "'pipx install ghunt' y ejecuta 'ghunt login' una vez; ver requirements.txt).",
            "",
        )
    cmd = ["ghunt", "email", email]
    ok, salida = _ejecutar_herramienta("ghunt", cmd, HUELLA_TIMEOUT)
    return ok, ("" if ok else salida), (salida if ok else "")


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

    Los nodos `url_perfil` siguen la misma regla de ≥2 fuentes que `buscar_username`.
    Los nodos `brecha` se marcan `confianza="expuesto"` (hecho directo de base de
    datos, no corroboración de identidad — no consume la regla de ≥2 fuentes).
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
        hallazgos_gravatar = [{"sitio": "Gravatar", "url": perfil_gravatar.get("profile_url", "")}]

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
