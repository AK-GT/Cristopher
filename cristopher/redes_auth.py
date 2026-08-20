"""Autenticación multi-cuenta para redes sociales (Fase 1: YouTube, Fase 2:
Instagram, Fase 3: TikTok; y fase de publicación: scopes de escritura sobre
esta misma infraestructura de tokens — deliberadamente NO llamada "Fase 4" para
no colisionar con el uso ya existente de ese número en el repo, ver CLAUDE.md).

Analogía deliberada con google_auth.py, pero MULTI-CUENTA: cada alias (canal/cuenta
que el usuario elige, p. ej. "principal", "humor") tiene su propio token bajo
data/redes/youtube/<alias>/token.json, generado con su propio flujo de
consentimiento — el usuario elige qué cuenta de Google autorizar en el navegador
cada vez que conecta un alias nuevo. Reutiliza el MISMO credentials.json de Google
(GOOGLE_CREDENTIALS, mismo proyecto de Cloud que Calendar/Gmail) con su propio scope
list (YOUTUBE_SCOPES), sin tocar GOOGLE_SCOPES ni GOOGLE_TOKEN (ver config.py).

A diferencia de google_auth._load_credentials(), aquí NO se lanza el navegador de
forma automática si falta el token: conectar una cuenta es un paso deliberado del
usuario (elige QUÉ cuenta de Google en el navegador), así que las tools de lectura
fallan con un mensaje explícito pidiendo `redes_cuenta_conectar` en vez de abrir un
navegador sin avisar en mitad de una conversación.

El índice de cuentas (data/redes/cuentas.json) sigue la estructura del plan:
{"youtube": [{"alias": ..., "canal_id": ..., "canal_titulo": ..., "uploads_playlist_id": ...}],
 "instagram": [{"alias": ..., "ig_user_id": ..., "username": ...}], "tiktok": []}
La clave tiktok se deja vacía por ahora (Fase 3, aún no construida).

Instagram usa "Instagram API with Instagram Login" (Business Login): login directo
con la cuenta de Instagram Creador/Empresa, SIN página de Facebook vinculada
(verificado en la documentación de Meta, ago 2026 — mejora sobre el diseño original
que asumía graph.facebook.com + página). A diferencia de Google, Meta no tiene un
paquete Python de OAuth desktop equivalente a InstalledAppFlow, así que aquí se
implementa a mano: navegador + servidor HTTP local de un solo uso para capturar el
código de autorización, más las llamadas de intercambio de token vía `requests`.

Errores explícitos y accionables (§1 CLAUDE.md: no fingir éxito).
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import secrets
import ssl
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, urlencode, urlparse

from cristopher.config import (
    GOOGLE_CREDENTIALS,
    INSTAGRAM_GRAPH_VERSION,
    INSTAGRAM_REDIRECT_PORT,
    INSTAGRAM_REDIRECT_URI,
    INSTAGRAM_REFRESH_MARGIN_SEGUNDOS,
    INSTAGRAM_SCOPES,
    INSTAGRAM_TOKENS_DIR,
    META_APP_ID,
    META_APP_SECRET,
    REDES_CUENTAS,
    REDES_DIR,
    TIKTOK_CLIENT_KEY,
    TIKTOK_CLIENT_SECRET,
    TIKTOK_REDIRECT_PORT,
    TIKTOK_REDIRECT_URI,
    TIKTOK_REFRESH_MARGIN_SEGUNDOS,
    TIKTOK_SCOPES,
    TIKTOK_TOKENS_DIR,
    YOUTUBE_SCOPES,
    YOUTUBE_TOKENS_DIR,
)

_ALIAS_RE = re.compile(r"^[a-zA-Z0-9_-]{1,40}$")
_PLATAFORMAS = ("youtube", "instagram", "tiktok")

_LOCK = threading.Lock()
# Cache de servicios autorizados (build perezoso), clave "youtube:v3:<alias>" /
# "youtubeAnalytics:v2:<alias>".
_SERVICES: dict[str, object] = {}


class RedesAuthError(RuntimeError):
    """Falta de credenciales, alias no conectado, o fallo de autenticación con la
    plataforma. Mensaje siempre accionable (§1 CLAUDE.md: fallos explícitos)."""


# --- Índice de cuentas (data/redes/cuentas.json) ------------------------------
def _leer_indice() -> dict:
    """Lee el índice completo (todas las plataformas). Si falta o está corrupto,
    devuelve el esqueleto vacío en vez de fallar (patrón defensivo de personalidad.py)."""
    vacio = {p: [] for p in _PLATAFORMAS}
    if not REDES_CUENTAS.exists():
        return vacio
    try:
        data = json.loads(REDES_CUENTAS.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return vacio
    if not isinstance(data, dict):
        return vacio
    for p in _PLATAFORMAS:
        data.setdefault(p, [])
    return data


def _escribir_indice(indice: dict) -> None:
    REDES_DIR.mkdir(parents=True, exist_ok=True)
    REDES_CUENTAS.write_text(json.dumps(indice, ensure_ascii=False, indent=2), encoding="utf-8")


def leer_cuentas(plataforma: str) -> list[dict]:
    """Lista de cuentas conectadas de una plataforma ('youtube'/'instagram'/'tiktok')."""
    with _LOCK:
        return _leer_indice().get(plataforma, [])


def indice_completo() -> dict:
    """El dict entero de cuentas.json, para redes_tools.redes_cuentas_listar."""
    with _LOCK:
        return _leer_indice()


def _upsert_cuenta_youtube(cuenta: dict) -> None:
    """Añade o reemplaza (por alias) la cuenta de YouTube en el índice."""
    with _LOCK:
        indice = _leer_indice()
        cuentas = [c for c in indice["youtube"] if c.get("alias") != cuenta["alias"]]
        cuentas.append(cuenta)
        indice["youtube"] = cuentas
        _escribir_indice(indice)


def _cuenta_youtube(alias: str) -> Optional[dict]:
    for c in leer_cuentas("youtube"):
        if c.get("alias") == alias:
            return c
    return None


def _alias_valido(alias: str) -> str:
    """Normaliza y valida el alias (solo letras/números/guion/guion_bajo, 1-40
    caracteres) — evita path traversal, ya que el alias llega vía function-calling
    del modelo y se usa para construir una ruta de archivo."""
    alias = (alias or "").strip()
    if not _ALIAS_RE.match(alias):
        raise RedesAuthError(
            f"Alias inválido: '{alias}'. Usa solo letras, números, guiones o guion "
            "bajo (1-40 caracteres), p. ej. 'principal' o 'humor'."
        )
    return alias


# --- YouTube: token por alias --------------------------------------------------
def _token_path_youtube(alias: str) -> Path:
    return YOUTUBE_TOKENS_DIR / alias / "token.json"


def conectar_cuenta_youtube(alias: str) -> str:
    """Lanza el consentimiento OAuth (navegador) para un canal de YouTube y lo
    guarda como `alias`. El usuario elige la cuenta de Google a consentir en el
    navegador — así se logra "multi-cuenta" reutilizando un único credentials.json.

    Lanza RedesAuthError si falta GOOGLE_CREDENTIALS, con instrucciones accionables.
    """
    alias = _alias_valido(alias)

    if not GOOGLE_CREDENTIALS.exists():
        raise RedesAuthError(
            "Falta el archivo de credenciales de Google.\n"
            f"  Colócalo en: {GOOGLE_CREDENTIALS}\n"
            "  Es el MISMO credentials.json que usan Calendar/Gmail (Google Cloud "
            "Console → 'App de escritorio'). Si ya lo tienes ahí para Calendar/Gmail, "
            "no hace falta descargar otro."
        )

    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    flow = InstalledAppFlow.from_client_secrets_file(str(GOOGLE_CREDENTIALS), YOUTUBE_SCOPES)
    creds = flow.run_local_server(port=0)  # abre el navegador, el usuario elige la cuenta

    token_path = _token_path_youtube(alias)
    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text(creds.to_json(), encoding="utf-8")

    # Una sola llamada para sacar canal_id/canal_titulo/uploads_playlist_id, sin
    # coste de cuota extra más allá de esta.
    youtube = build("youtube", "v3", credentials=creds, cache_discovery=False)
    resp = (
        youtube.channels()
        .list(part="snippet,contentDetails", mine=True)
        .execute()
    )
    items = resp.get("items", [])
    if not items:
        raise RedesAuthError(
            "La cuenta de Google que elegiste no tiene ningún canal de YouTube "
            "asociado. Vuelve a intentarlo con la cuenta correcta."
        )
    canal = items[0]
    canal_id = canal["id"]
    canal_titulo = canal.get("snippet", {}).get("title", "(sin nombre)")
    uploads_playlist_id = (
        canal.get("contentDetails", {}).get("relatedPlaylists", {}).get("uploads", "")
    )

    _upsert_cuenta_youtube(
        {
            "alias": alias,
            "canal_id": canal_id,
            "canal_titulo": canal_titulo,
            "uploads_playlist_id": uploads_playlist_id,
        }
    )
    return f"Canal de YouTube conectado como '{alias}': {canal_titulo} ({canal_id})."


def _cargar_credenciales_youtube(alias: str):
    """Credenciales OAuth válidas para `alias`, refrescando si hace falta (igual
    que google_auth._load_credentials, pero SIN lanzar el flujo de consentimiento
    si no hay token: eso es responsabilidad exclusiva de conectar_cuenta_youtube).
    """
    cuenta = _cuenta_youtube(alias)
    token_path = _token_path_youtube(alias)
    if cuenta is None or not token_path.exists():
        raise RedesAuthError(
            f"La cuenta '{alias}' no está conectada. Usa "
            f"redes_cuenta_conectar('youtube', '{alias}') primero."
        )

    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    creds = Credentials.from_authorized_user_file(str(token_path), YOUTUBE_SCOPES)
    if creds and creds.valid:
        return creds

    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            token_path.write_text(creds.to_json(), encoding="utf-8")
            return creds
        except Exception as exc:
            raise RedesAuthError(
                f"El token de '{alias}' caducó y no se pudo renovar ({exc}). "
                f"Reconéctala con redes_cuenta_conectar('youtube', '{alias}')."
            ) from exc

    raise RedesAuthError(
        f"El token de '{alias}' no es válido. Reconéctala con "
        f"redes_cuenta_conectar('youtube', '{alias}')."
    )


def _get_service(alias: str, name: str, version: str):
    key = f"{name}:{version}:{alias}"
    if key not in _SERVICES:
        from googleapiclient.discovery import build

        creds = _cargar_credenciales_youtube(alias)
        _SERVICES[key] = build(name, version, credentials=creds, cache_discovery=False)
    return _SERVICES[key]


def get_youtube_service(alias: str):
    """Servicio autorizado de YouTube Data API (v3) para `alias`. Cacheado."""
    return _get_service(alias, "youtube", "v3")


def get_youtube_analytics_service(alias: str):
    """Servicio autorizado de YouTube Analytics API (v2, nombre de discovery
    'youtubeAnalytics') para `alias`. Cacheado."""
    return _get_service(alias, "youtubeAnalytics", "v2")


# --- Instagram: cuenta por alias -----------------------------------------------
def _token_path_instagram(alias: str) -> Path:
    return INSTAGRAM_TOKENS_DIR / alias / "token.json"


def _upsert_cuenta_instagram(cuenta: dict) -> None:
    """Añade o reemplaza (por alias) la cuenta de Instagram en el índice."""
    with _LOCK:
        indice = _leer_indice()
        cuentas = [c for c in indice["instagram"] if c.get("alias") != cuenta["alias"]]
        cuentas.append(cuenta)
        indice["instagram"] = cuentas
        _escribir_indice(indice)


def _cuenta_instagram(alias: str) -> Optional[dict]:
    for c in leer_cuentas("instagram"):
        if c.get("alias") == alias:
            return c
    return None


class _OAuthCallbackHandler(BaseHTTPRequestHandler):
    """Captura UNA sola petición GET del redirect de Meta (con `code` o `error` en
    la query string) y la guarda en el propio servidor, para que
    conectar_cuenta_instagram la lea tras server.handle_request()."""

    def do_GET(self) -> None:  # noqa: N802 (nombre exigido por BaseHTTPRequestHandler)
        qs = parse_qs(urlparse(self.path).query)
        self.server.resultado = {k: v[0] for k, v in qs.items()}  # type: ignore[attr-defined]
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(
            "<html><body><p>Ya puedes cerrar esta ventana y volver a "
            "CRISTOPHER.</p></body></html>".encode("utf-8")
        )

    def log_message(self, format: str, *args) -> None:  # silencia el log por stderr
        pass


def _generar_cert_local() -> tuple[str, str]:
    """(cert_path, key_path) de un certificado autofirmado para localhost,
    cacheado en disco (se genera una sola vez, se reutiliza después).

    GOTCHA real (verificado con un fallo real, no hipotético): Meta rechazaba el
    redirect_uri de Business Login for Instagram con 'Error al guardar los URI de
    redireccionamiento' cuando era http://localhost:..., y lo aceptó en cuanto se
    cambió a https://localhost:... — exige HTTPS incluso para localhost. El
    servidor local de conectar_cuenta_instagram necesita por tanto terminar TLS;
    el navegador mostrará un aviso de certificado no confiable la primera vez
    (normal en autofirmados de localhost), hay que aceptar 'Avanzado > Continuar'."""
    cert_path = REDES_DIR / "instagram_local_cert.pem"
    key_path = REDES_DIR / "instagram_local_key.pem"
    if cert_path.exists() and key_path.exists():
        return str(cert_path), str(key_path)

    import datetime as _dt

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    nombre = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    ahora = _dt.datetime.now(_dt.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(nombre)
        .issuer_name(nombre)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(ahora)
        .not_valid_after(ahora + _dt.timedelta(days=3650))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost")]), critical=False)
        .sign(key, hashes.SHA256())
    )

    REDES_DIR.mkdir(parents=True, exist_ok=True)
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    return str(cert_path), str(key_path)


def conectar_cuenta_instagram(alias: str) -> str:
    """Lanza el consentimiento OAuth (navegador) para una cuenta de Instagram
    Creador/Empresa y la guarda como `alias`. Usa "Instagram API with Instagram
    Login" (Business Login) — NO requiere página de Facebook vinculada.

    Lanza RedesAuthError si faltan META_APP_ID/META_APP_SECRET, con instrucciones
    accionables (crear app en Meta for Developers, producto "Business login for
    Instagram").
    """
    alias = _alias_valido(alias)

    if not META_APP_ID or not META_APP_SECRET:
        raise RedesAuthError(
            "Faltan META_APP_ID/META_APP_SECRET.\n"
            "  1) Crea una app en https://developers.facebook.com/apps/\n"
            "  2) Añade el producto 'Instagram' → configura 'Business login for "
            "Instagram'\n"
            "  3) Copia el Instagram App ID / Instagram App Secret de ESE producto "
            "a tu .env como META_APP_ID / META_APP_SECRET\n"
            f"  4) En ese mismo producto, añade como redirect URI válido: "
            f"{INSTAGRAM_REDIRECT_URI}"
        )

    import webbrowser

    import requests

    auth_url = "https://www.instagram.com/oauth/authorize?" + urlencode(
        {
            "client_id": META_APP_ID,
            "redirect_uri": INSTAGRAM_REDIRECT_URI,
            "response_type": "code",
            "scope": INSTAGRAM_SCOPES,
        }
    )
    webbrowser.open(auth_url)

    # HTTPS, no HTTP (ver _generar_cert_local): el navegador mostrará un aviso de
    # certificado no confiable la primera vez — es esperado en un autofirmado de
    # localhost, hay que aceptar "Avanzado > Continuar" para llegar al callback.
    cert_path, key_path = _generar_cert_local()
    server = HTTPServer(("localhost", INSTAGRAM_REDIRECT_PORT), _OAuthCallbackHandler)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(certfile=cert_path, keyfile=key_path)
    server.socket = ctx.wrap_socket(server.socket, server_side=True)
    server.timeout = 120
    server.resultado = None  # type: ignore[attr-defined]
    try:
        server.handle_request()
    finally:
        server.server_close()

    resultado = server.resultado  # type: ignore[attr-defined]
    if not resultado:
        raise RedesAuthError(
            "No llegó respuesta de Instagram en 2 minutos (o el navegador se quedó "
            "en el aviso de certificado no confiable sin continuar). Vuelve a "
            f"intentarlo con redes_cuenta_conectar('instagram', '{alias}')."
        )
    if "error" in resultado:
        raise RedesAuthError(
            "Instagram rechazó la conexión: "
            + resultado.get("error_description", resultado["error"])
        )
    code = resultado.get("code")
    if not code:
        raise RedesAuthError("Instagram no devolvió un código de autorización válido.")

    resp = requests.post(
        "https://api.instagram.com/oauth/access_token",
        data={
            "client_id": META_APP_ID,
            "client_secret": META_APP_SECRET,
            "grant_type": "authorization_code",
            "redirect_uri": INSTAGRAM_REDIRECT_URI,
            "code": code,
        },
        timeout=15,
    )
    data = resp.json()
    if "access_token" not in data:
        raise RedesAuthError(f"Instagram no devolvió un token de acceso: {data}")
    short_token = data["access_token"]

    resp = requests.get(
        "https://graph.instagram.com/access_token",
        params={
            "grant_type": "ig_exchange_token",
            "client_secret": META_APP_SECRET,
            "access_token": short_token,
        },
        timeout=15,
    )
    data = resp.json()
    if "access_token" not in data:
        raise RedesAuthError(f"No se pudo obtener un token de larga duración: {data}")
    access_token = data["access_token"]
    expires_in = data.get("expires_in", 60 * 24 * 3600)

    resp = requests.get(
        f"https://graph.instagram.com/{INSTAGRAM_GRAPH_VERSION}/me",
        params={"fields": "user_id,username", "access_token": access_token},
        timeout=15,
    )
    data = resp.json()
    if "error" in data:
        raise RedesAuthError(
            f"Error leyendo el perfil de Instagram: {data['error'].get('message', data)}"
        )
    ig_user_id = str(data.get("user_id", ""))
    username = data.get("username", "(sin nombre)")

    token_path = _token_path_instagram(alias)
    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text(
        json.dumps(
            {
                "access_token": access_token,
                "obtenido_en": time.time(),
                "expires_in": expires_in,
                "ig_user_id": ig_user_id,
                "username": username,
            }
        ),
        encoding="utf-8",
    )

    _upsert_cuenta_instagram({"alias": alias, "ig_user_id": ig_user_id, "username": username})
    return f"Cuenta de Instagram conectada como '{alias}': @{username}."


def _refrescar_token_instagram(alias: str, data: dict) -> dict:
    import requests

    resp = requests.get(
        "https://graph.instagram.com/refresh_access_token",
        params={"grant_type": "ig_refresh_token", "access_token": data["access_token"]},
        timeout=15,
    )
    nuevo = resp.json()
    if "access_token" not in nuevo:
        raise RedesAuthError(f"No se pudo refrescar el token de '{alias}': {nuevo}")
    data = {
        **data,
        "access_token": nuevo["access_token"],
        "obtenido_en": time.time(),
        "expires_in": nuevo.get("expires_in", data["expires_in"]),
    }
    _token_path_instagram(alias).write_text(json.dumps(data), encoding="utf-8")
    return data


def _cargar_token_instagram(alias: str) -> tuple[str, str]:
    """(access_token, ig_user_id) válidos para `alias`. Refresca en silencio si el
    token está cerca de caducar (igual de espíritu que google_auth: silencioso si
    se puede, error explícito si no)."""
    if _cuenta_instagram(alias) is None:
        raise RedesAuthError(
            f"La cuenta '{alias}' no está conectada. Usa "
            f"redes_cuenta_conectar('instagram', '{alias}') primero."
        )
    token_path = _token_path_instagram(alias)
    if not token_path.exists():
        raise RedesAuthError(
            f"La cuenta '{alias}' no está conectada. Usa "
            f"redes_cuenta_conectar('instagram', '{alias}') primero."
        )
    try:
        data = json.loads(token_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise RedesAuthError(
            f"El token de '{alias}' está corrupto. Reconéctala con "
            f"redes_cuenta_conectar('instagram', '{alias}')."
        ) from exc

    ahora = time.time()
    expira_en = data["obtenido_en"] + data["expires_in"]
    if ahora > expira_en:
        raise RedesAuthError(
            f"El token de '{alias}' caducó. Reconéctala con "
            f"redes_cuenta_conectar('instagram', '{alias}')."
        )

    if expira_en - ahora < INSTAGRAM_REFRESH_MARGIN_SEGUNDOS:
        try:
            data = _refrescar_token_instagram(alias, data)
        except Exception:
            pass  # sigue con el token actual mientras siga vigente; se reintenta la próxima vez

    return data["access_token"], data["ig_user_id"]


def instagram_graph_get(alias: str, path: str, params: Optional[dict] = None) -> dict:
    """GET autenticado a la Graph API de Instagram (graph.instagram.com) para
    `alias`. Lanza RedesAuthError con el mensaje de error de Meta si la API falla
    (nunca deja pasar un dict de error crudo)."""
    import requests

    access_token, _ = _cargar_token_instagram(alias)
    params = dict(params or {})
    params["access_token"] = access_token
    resp = requests.get(
        f"https://graph.instagram.com/{INSTAGRAM_GRAPH_VERSION}/{path}",
        params=params,
        timeout=15,
    )
    data = resp.json()
    if isinstance(data, dict) and "error" in data:
        raise RedesAuthError(data["error"].get("message", str(data["error"])))
    return data


def instagram_graph_post(alias: str, path: str, params: Optional[dict] = None) -> dict:
    """POST autenticado a la Graph API de Instagram (graph.instagram.com) para
    `alias` — simétrica a instagram_graph_get pero para escritura (fase de
    publicación: crear contenedor de media, publicarlo). Los parámetros van como
    form data + access_token, igual que el resto de la Graph API de Meta. Lanza
    RedesAuthError con el mensaje de error de Meta si la API falla."""
    import requests

    access_token, _ = _cargar_token_instagram(alias)
    params = dict(params or {})
    params["access_token"] = access_token
    resp = requests.post(
        f"https://graph.instagram.com/{INSTAGRAM_GRAPH_VERSION}/{path}",
        data=params,
        timeout=30,
    )
    data = resp.json()
    if isinstance(data, dict) and "error" in data:
        raise RedesAuthError(data["error"].get("message", str(data["error"])))
    return data


# --- TikTok: cuenta por alias ---------------------------------------------------
def _token_path_tiktok(alias: str) -> Path:
    return TIKTOK_TOKENS_DIR / alias / "token.json"


def _upsert_cuenta_tiktok(cuenta: dict) -> None:
    """Añade o reemplaza (por alias) la cuenta de TikTok en el índice."""
    with _LOCK:
        indice = _leer_indice()
        cuentas = [c for c in indice["tiktok"] if c.get("alias") != cuenta["alias"]]
        cuentas.append(cuenta)
        indice["tiktok"] = cuentas
        _escribir_indice(indice)


def _cuenta_tiktok(alias: str) -> Optional[dict]:
    for c in leer_cuentas("tiktok"):
        if c.get("alias") == alias:
            return c
    return None


def _generar_pkce() -> tuple[str, str]:
    """(code_verifier, code_challenge) para el flujo PKCE que TikTok exige en
    apps de escritorio.

    GOTCHA real (verificado con un fallo real, no hipotético): a diferencia del
    S256 estándar de RFC 7636 (challenge = base64url(sha256(verifier))), que es lo
    que usan Google/GitHub/etc., TikTok exige el challenge en HEXADECIMAL —
    `hashlib.sha256(verifier).hexdigest()` — no en base64url. Usar base64url aquí
    da 'invalid_request: Code verifier or code challenge is invalid' en el
    intercambio de token, con un code_challenge que a simple vista parece válido.
    """
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(64)).rstrip(b"=").decode("ascii")
    challenge = hashlib.sha256(verifier.encode("ascii")).hexdigest()
    return verifier, challenge


def conectar_cuenta_tiktok(alias: str) -> str:
    """Lanza el consentimiento OAuth (navegador) para una cuenta de TikTok y la
    guarda como `alias`. Usa Login Kit con PKCE (obligatorio en apps de
    escritorio) y verificación de `state` anti-CSRF.

    Lanza RedesAuthError si faltan TIKTOK_CLIENT_KEY/TIKTOK_CLIENT_SECRET, con
    instrucciones accionables (crear app en TikTok for Developers, producto
    "Login Kit", y añadir la cuenta como Target User de Sandbox para probar sin
    esperar revisión).
    """
    alias = _alias_valido(alias)

    if not TIKTOK_CLIENT_KEY or not TIKTOK_CLIENT_SECRET:
        raise RedesAuthError(
            "Faltan TIKTOK_CLIENT_KEY/TIKTOK_CLIENT_SECRET.\n"
            "  1) Crea una app en https://developers.tiktok.com/\n"
            "  2) Activa el producto 'Login Kit' con los scopes user.info.basic, "
            "user.info.stats, video.list\n"
            "  3) Copia el Client key / Client secret a tu .env como "
            "TIKTOK_CLIENT_KEY / TIKTOK_CLIENT_SECRET\n"
            f"  4) Añade como Redirect URI: {TIKTOK_REDIRECT_URI}\n"
            "  5) En Sandbox → Target Users, añade tu propia cuenta de TikTok "
            "para probar sin esperar la revisión de 3-7 días."
        )

    import webbrowser

    import requests

    verifier, challenge = _generar_pkce()
    state = secrets.token_urlsafe(16)

    auth_url = "https://www.tiktok.com/v2/auth/authorize/?" + urlencode(
        {
            "client_key": TIKTOK_CLIENT_KEY,
            "redirect_uri": TIKTOK_REDIRECT_URI,
            "scope": TIKTOK_SCOPES,
            "response_type": "code",
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
    )
    webbrowser.open(auth_url)

    server = HTTPServer(("localhost", TIKTOK_REDIRECT_PORT), _OAuthCallbackHandler)
    server.timeout = 120
    server.resultado = None  # type: ignore[attr-defined]
    try:
        server.handle_request()
    finally:
        server.server_close()

    resultado = server.resultado  # type: ignore[attr-defined]
    if not resultado:
        raise RedesAuthError(
            "No llegó respuesta de TikTok en 2 minutos. Vuelve a intentarlo con "
            f"redes_cuenta_conectar('tiktok', '{alias}')."
        )
    if "error" in resultado:
        raise RedesAuthError(
            "TikTok rechazó la conexión: "
            + resultado.get("error_description", resultado["error"])
        )
    if resultado.get("state") != state:
        raise RedesAuthError(
            "La respuesta de TikTok no trae el 'state' esperado (posible sesión "
            "cruzada). Vuelve a intentarlo."
        )
    code = resultado.get("code")
    if not code:
        raise RedesAuthError("TikTok no devolvió un código de autorización válido.")

    resp = requests.post(
        "https://open.tiktokapis.com/v2/oauth/token/",
        data={
            "client_key": TIKTOK_CLIENT_KEY,
            "client_secret": TIKTOK_CLIENT_SECRET,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": TIKTOK_REDIRECT_URI,
            "code_verifier": verifier,
        },
        timeout=15,
    )
    data = resp.json()
    if "access_token" not in data:
        raise RedesAuthError(f"TikTok no devolvió un token de acceso: {data}")

    token_data = {
        "access_token": data["access_token"],
        "refresh_token": data.get("refresh_token", ""),
        "obtenido_en": time.time(),
        "expires_in": data.get("expires_in", 24 * 3600),
        "open_id": data.get("open_id", ""),
    }

    resp = requests.get(
        "https://open.tiktokapis.com/v2/user/info/",
        params={"fields": "display_name"},
        headers={"Authorization": f"Bearer {token_data['access_token']}"},
        timeout=15,
    )
    perfil = resp.json()
    error = (perfil.get("error") or {})
    if error.get("code") not in (None, "ok"):
        raise RedesAuthError(f"Error leyendo el perfil de TikTok: {error.get('message', error)}")
    display_name = perfil.get("data", {}).get("user", {}).get("display_name", "(sin nombre)")

    token_path = _token_path_tiktok(alias)
    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text(json.dumps(token_data), encoding="utf-8")

    _upsert_cuenta_tiktok(
        {"alias": alias, "open_id": token_data["open_id"], "display_name": display_name}
    )
    return f"Cuenta de TikTok conectada como '{alias}': {display_name}."


def _refrescar_token_tiktok(alias: str, data: dict) -> dict:
    import requests

    resp = requests.post(
        "https://open.tiktokapis.com/v2/oauth/token/",
        data={
            "client_key": TIKTOK_CLIENT_KEY,
            "client_secret": TIKTOK_CLIENT_SECRET,
            "grant_type": "refresh_token",
            "refresh_token": data["refresh_token"],
        },
        timeout=15,
    )
    nuevo = resp.json()
    if "access_token" not in nuevo:
        raise RedesAuthError(f"No se pudo refrescar el token de '{alias}': {nuevo}")
    # El refresh_token ROTA en cada refresco: hay que guardar el nuevo, no el viejo.
    data = {
        **data,
        "access_token": nuevo["access_token"],
        "refresh_token": nuevo.get("refresh_token", data["refresh_token"]),
        "obtenido_en": time.time(),
        "expires_in": nuevo.get("expires_in", data["expires_in"]),
    }
    _token_path_tiktok(alias).write_text(json.dumps(data), encoding="utf-8")
    return data


def _cargar_token_tiktok(alias: str) -> tuple[str, str]:
    """(access_token, open_id) válidos para `alias`. A diferencia de Instagram, el
    access_token de TikTok dura solo 24h: refrescar cerca de caducar es la
    operación NORMAL de cada día, no un mejor-esfuerzo silencioso — si falla, error
    explícito (el refresh_token puede haber caducado a los 365 días o estar
    revocado)."""
    if _cuenta_tiktok(alias) is None:
        raise RedesAuthError(
            f"La cuenta '{alias}' no está conectada. Usa "
            f"redes_cuenta_conectar('tiktok', '{alias}') primero."
        )
    token_path = _token_path_tiktok(alias)
    if not token_path.exists():
        raise RedesAuthError(
            f"La cuenta '{alias}' no está conectada. Usa "
            f"redes_cuenta_conectar('tiktok', '{alias}') primero."
        )
    try:
        data = json.loads(token_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise RedesAuthError(
            f"El token de '{alias}' está corrupto. Reconéctala con "
            f"redes_cuenta_conectar('tiktok', '{alias}')."
        ) from exc

    ahora = time.time()
    expira_en = data["obtenido_en"] + data["expires_in"]
    if ahora > expira_en - TIKTOK_REFRESH_MARGIN_SEGUNDOS:
        try:
            data = _refrescar_token_tiktok(alias, data)
        except RedesAuthError:
            raise
        except Exception as exc:
            raise RedesAuthError(
                f"No se pudo refrescar el token de '{alias}' ({exc}). Reconéctala "
                f"con redes_cuenta_conectar('tiktok', '{alias}')."
            ) from exc

    return data["access_token"], data["open_id"]


def tiktok_api_request(
    alias: str, method: str, path: str, params: Optional[dict] = None, json_body: Optional[dict] = None
) -> dict:
    """Petición autenticada a la API v2 de TikTok (open.tiktokapis.com) para
    `alias`. Todas las respuestas traen `{"data": ..., "error": {"code": "ok" | ...}}`
    incluso en éxito — lanza RedesAuthError si `error.code` no es "ok"."""
    import requests

    access_token, _ = _cargar_token_tiktok(alias)
    resp = requests.request(
        method,
        f"https://open.tiktokapis.com/v2/{path}",
        headers={"Authorization": f"Bearer {access_token}"},
        params=params,
        json=json_body,
        timeout=15,
    )
    data = resp.json()
    error = data.get("error") or {}
    if error.get("code") not in (None, "ok"):
        raise RedesAuthError(error.get("message", str(error)))
    return data


def tiktok_subir_chunk(upload_url: str, chunk: bytes, inicio: int, fin: int, total: int) -> None:
    """PUT de un fragmento de bytes al `upload_url` devuelto por
    post/publish/video/init/ (fase de publicación, subida FILE_UPLOAD). Sin
    `Authorization`: la URL ya viene autorizada por TikTok para esta subida en
    concreto. RedesAuthError si el status HTTP no es 2xx."""
    import requests

    resp = requests.put(
        upload_url,
        data=chunk,
        headers={
            "Content-Range": f"bytes {inicio}-{fin}/{total}",
            "Content-Type": "video/mp4",
        },
        timeout=120,
    )
    if not resp.ok:
        raise RedesAuthError(
            f"Fallo al subir un fragmento del vídeo a TikTok (HTTP {resp.status_code}): "
            f"{resp.text[:300]}"
        )
