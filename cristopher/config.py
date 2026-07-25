"""Configuración central de CRISTOPHER.

Carga el entorno desde .env y expone la clave y el modelo. Falla de forma
explícita si falta la clave (esencia §1: "fallos explícitos", nunca fingir éxito).
El secreto vive SOLO en el entorno, jamás en el código (build prompt §0/§9).
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# Raíz del proyecto = carpeta que contiene el paquete `cristopher`.
ROOT = Path(__file__).resolve().parent.parent

# Carga .env de la raíz si existe (no pisa variables ya definidas en el sistema).
load_dotenv(ROOT / ".env")

# Modelo del cerebro. Usamos el alias `-latest` para no quedar clavados a una versión
# concreta que Google pueda retirar (404). Soporta function calling + multimodal.
# Sobrescribible con la variable de entorno CRISTOPHER_MODEL.
MODEL = os.getenv("CRISTOPHER_MODEL", "gemini-flash-latest")

# Cerebro de respaldo: si el principal agota su cuota (429), CRISTOPHER cae a este y
# sigue funcionando (§8 "degrada con elegancia"). Debe ser OTRO modelo Gemini que
# soporte function calling + system_instruction + visión (la misma config del bucle se
# reutiliza tal cual): un modelo sin FC — p. ej. Gemma — la rechazaría con 400. Poner
# "" para desactivarlo.
FALLBACK_MODEL = os.getenv("CRISTOPHER_FALLBACK_MODEL", "gemini-flash-lite-latest")

# Modo de salida: "texto" (REPL) o "voz" (Fase 6). En voz, el pensamiento intermedio
# NO se verbaliza: solo se habla la respuesta final. El flag deja esto preparado.
MODO_SALIDA = os.getenv("CRISTOPHER_MODO_SALIDA", "texto").strip().lower()
MOSTRAR_PENSAMIENTO = MODO_SALIDA != "voz"

# Directorio de trabajo donde CRISTOPHER clona repos / crea archivos temporales.
WORKSPACE = ROOT / "workspace"

# Almacén local de memoria persistente (SQLite). No se versiona (.gitignore).
DATA = ROOT / "data"

# Carpeta donde cada sub-agente delegado trabaja aislado (bajo workspace/).
SUBAGENTS = WORKSPACE / "subagents"
# Timeout amplio: las tareas de código delegadas pueden tardar.
SUBAGENT_TIMEOUT = 600

# Timeout del modo "análisis de proyecto" (delegate.py::analizar_proyecto): explorar
# un proyecto real de punta a punta tarda más que una tarea acotada.
PROJECT_ANALYSIS_TIMEOUT = 900

# --- Gestor de sesiones vivas de Claude Code (sesiones.py, Fase 1) -----------
SESION_TRANSCRIPT_MAXLEN = 300  # tope de entradas de transcript por sesión
SESION_STOP_TIMEOUT = 5         # s de espera tras terminate() antes de kill()
# Cuánto tiempo sin actividad en estado 'trabajando' se considera "colgada" (modo
# code, Fase 3 de Cristopher Code) — mismo orden de magnitud que SUBAGENT_TIMEOUT.
SESION_COLGADA_SEGUNDOS = 600

# --- Fase 4a: tipo de sesión, modelo y vigilancia de contexto ----------------
# permission-mode de las sesiones tipo "chat" (idear/conversar, sin proyecto real).
# Verificado real en Paso 0: "plan" dispara el flujo completo de Plan Mode (escribe
# un .md en ~/.claude/plans y llama a ExitPlanMode, que falla en headless) — NO
# sirve. "dontAsk" deniega cada tool call arriesgada con un mensaje claro, sin
# colgarse (no hay TTY) y sin tocar disco: es el que de verdad cumple "sin tocar
# disco" del diseño. Las sesiones tipo "code" siguen en bypassPermissions (Fase 1-3).
SESION_CHAT_PERMISSION_MODE = "dontAsk"
# Umbral de aviso proactivo de contexto alto (0-1). Regla del propio diseño: parar
# sobre el ~75% de contexto da mejor calidad que exprimir al 90%. El tamaño real de
# la ventana de contexto NO se hardcodea aquí: se lee del propio evento `result`
# (modelUsage.<modelo>.contextWindow), que la API ya reporta con exactitud.
SESION_CONTEXTO_AVISO_PCT = 0.75

# --- Fase 4b: modo discusión entre sesiones ----------------------------------
# Tope de espera de GestorSesiones.discutir() por cada turno bloqueante (sesión
# crítica respondiendo, o sesión A revisando la crítica). Mismo orden de magnitud
# que SUBAGENT_TIMEOUT: si se agota, es un fallo explícito (§1), no un cuelgue.
SESION_DISCUSION_TIMEOUT = 300

# --- Fase 4: integraciones Google + búsqueda de élite ------------------------
# Credenciales OAuth y token persistente (bajo data/, gitignored; §9).
GOOGLE_DIR = DATA / "google"
GOOGLE_CREDENTIALS = GOOGLE_DIR / "credentials.json"  # lo descarga el usuario
GOOGLE_TOKEN = GOOGLE_DIR / "token.json"              # se genera en el consentimiento

# Scopes: Calendar en lectura/escritura de eventos (gateado por confirmación) +
# Gmail en lectura + envío de correo (gateado por confirmación) + gmail.modify
# (necesario para marcar_leido: cambiar etiquetas no lo cubre readonly ni send).
GOOGLE_SCOPES = [
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.send",
]

# Búsqueda de élite (opcional). Sin ella, la búsqueda cae a DuckDuckGo.
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "").strip()

# --- Fase 8: HUD -------------------------------------------------------------
HUD_PORT = int(os.getenv("CRISTOPHER_HUD_PORT", "8765"))

# --- Fase 7: proactividad ----------------------------------------------------
PROACTIVO_DB = DATA / "proactivo.db"          # recordatorios + dedup de avisos
POLL_SEGUNDOS = int(os.getenv("CRISTOPHER_POLL", "60"))   # cada cuánto revisa el demonio
LEAD_MINUTOS = int(os.getenv("CRISTOPHER_LEAD", "15"))    # antelación para avisar de eventos
# Hora local (0-23) a partir de la que avisa si el usuario no ha hecho su diario de hoy.
DIARIO_HORA_AVISO = int(os.getenv("CRISTOPHER_DIARIO_HORA", "20"))
# Mini-modelo que clasifica la prioridad (1-3) de cada aviso. Fallback: FALLBACK_MODEL.
CLASSIFIER_MODEL = os.getenv("CRISTOPHER_CLASSIFIER_MODEL", "gemini-flash-lite-latest")
GMAIL_QUERY_PROACTIVO = os.getenv("CRISTOPHER_GMAIL_QUERY", "is:unread")

# Correo de destino para avisos urgentes (nivel 3) que deben llegar aunque no estés
# delante del PC ni escuchando la voz. Reutiliza las credenciales Gmail ya autorizadas
# (Fase 4). Sin esta variable, el demonio sigue avisando solo por terminal+voz como
# hasta ahora (degrada con elegancia, igual que TAVILY_API_KEY).
NOTIFICAR_EMAIL_TO = os.getenv("CRISTOPHER_NOTIFICAR_EMAIL", "").strip()

# --- Fase 6: voz -------------------------------------------------------------
VOICE_DIR = DATA / "voice"
WHISPER_DIR = VOICE_DIR / "whisper"          # caché del modelo faster-whisper
STT_MODEL = os.getenv("CRISTOPHER_STT_MODEL", "small")  # tamaño de faster-whisper
STT_LANG = "es"
VOZ_DEFECTO = "es_ES-davefx-medium"  # nombre de la voz Piper por defecto

# --- Arranque por 2 palmadas + música ----------------------------------------
# Escucha ambiental ligera (cristopher/escucha.py): 2 palmadas despiertan el HUD y
# suena la canción. Umbrales ajustables por entorno para calibrar según el micro.
PALMADA_FACTOR   = float(os.getenv("CRISTOPHER_PALMADA_FACTOR", "40"))    # pico / suelo de ruido (subido para evitar falsos positivos)
PALMADA_UMBRAL   = float(os.getenv("CRISTOPHER_PALMADA_UMBRAL", "1"))     # pico mínimo absoluto (subido para evitar falsos positivos)
PALMADA_GAP_MIN  = float(os.getenv("CRISTOPHER_PALMADA_GAP_MIN", "0.12")) # s mínimos entre palmadas
PALMADA_GAP_MAX  = float(os.getenv("CRISTOPHER_PALMADA_GAP_MAX", "0.6"))  # s máximos entre palmadas
PALMADA_COOLDOWN = float(os.getenv("CRISTOPHER_PALMADA_COOLDOWN", "5"))   # s de guarda tras disparar
# Modo normal corre sin consola (pythonw vía autostart): nada de print(), todo a este log.
ESCUCHA_LOG = DATA / "escucha.log"
# Si este archivo existe, escucha.py no abre el micro (o corta si ya estaba escuchando).
# Interruptor manual: crear/borrar el archivo activa/desactiva sin tocar el autostart.
ESCUCHA_FLAG_DESACTIVAR = DATA / "escucha.desactivada"
# Canción de arranque: se abre en una pestaña de YouTube (se descartó VLC en local, no
# sonaba en esta máquina). URL exacta si la fijas; si no, se busca por la query.
CANCION_YOUTUBE_URL   = os.getenv("CRISTOPHER_CANCION_YOUTUBE_URL", "").strip()
CANCION_YOUTUBE_QUERY = os.getenv("CRISTOPHER_CANCION_YOUTUBE_QUERY", "AC/DC Back In Black")

# Modelo de embeddings para el recuerdo semántico (free tier, dim 3072).
EMBED_MODEL = os.getenv("CRISTOPHER_EMBED_MODEL", "gemini-embedding-001")

# --- Módulo de música (Tanda A) ----------------------------------------------
# Motor: VLC (python-vlc) + yt-dlp para "cualquier canción" de la web. Requiere VLC
# 64-bit instalado en el sistema (para libvlc). La biblioteca local vive en data/musica
# (gitignored como el resto de data/); el resolver la crea al arrancar.
MUSICA_DIR    = DATA / "musica"                                   # biblioteca local
MUSICA_VOLUMEN = int(os.getenv("CRISTOPHER_MUSICA_VOLUMEN", "70"))  # volumen inicial 0-100
# Formato de audio que yt-dlp entrega a VLC (stream directo, sin descargar).
YTDLP_FORMAT  = os.getenv("CRISTOPHER_YTDLP_FORMAT", "bestaudio/best")
# Carpeta de VLC PORTABLE (fallback si VLC no está instalado en el sistema). El
# reproductor busca libvlc.dll aquí y apunta python-vlc a ella; si el sistema ya tiene
# VLC, esto se ignora. Sobrescribible con CRISTOPHER_VLC_DIR. Por defecto data/vlc.
VLC_DIR       = Path(os.getenv("CRISTOPHER_VLC_DIR", str(DATA / "vlc")))

# --- Motor de huella digital por pivotes (huella.py, Tanda A) ----------------
# Dossiers de OSINT sobre terceros: base propia y aislada (no data/memory.db, que
# guarda hechos/notas/tareas del propio usuario), ciclo de vida y borrado distintos.
HUELLA_DIR = DATA / "huella"
# Maigret puede tardar varios minutos si escanea sus 3000+ sitios; se acota con
# --top-sites y se da un timeout generoso de subproceso por herramienta.
HUELLA_TIMEOUT = 180
HUELLA_MAIGRET_TOP_SITES = int(os.getenv("CRISTOPHER_HUELLA_TOP_SITES", "500"))

# --- Motor de huella digital: bucle de pivote (huella.py, Tanda C) -----------
# Profundidad y topes del BFS que encadena buscar_username/buscar_email/buscar_telefono
# automáticamente — cinturón y tirantes contra bucles infinitos/explosión de nodos.
HUELLA_PROFUNDIDAD_MAX = int(os.getenv("CRISTOPHER_HUELLA_PROFUNDIDAD", "2"))
# Cuántos url_perfil se exploran por cada pivote resuelto. Verificado con datos reales
# (alias con 30+ perfiles hallados): un tope de 5 dejaba fuera sitios con extracción
# real conocida (GitHub) por un solo puesto de diferencia frente a sitios sin scheme.
HUELLA_MAX_PIVOTES_POR_NIVEL = 10
HUELLA_MAX_NODOS_TOTAL = 40        # tope duro de pivotes visitados en toda la investigación
HUELLA_EXTRACCION_TIMEOUT = 8      # s por fetch de socid_extractor.parse (una URL de perfil)

# --- Motor de huella digital: disyuntor + parada temprana (huella.py, Tanda F) --
# Presupuesto de tiempo TOTAL de una `investigar()` — cinturón adicional al tope de
# nodos: con Maigret pudiendo tardar hasta HUELLA_TIMEOUT por pivote, 40 nodos
# secuenciales podrían tardar horas en el peor caso. 20 min por defecto.
HUELLA_TIEMPO_MAX_SEGUNDOS = int(os.getenv("CRISTOPHER_HUELLA_TIEMPO_MAX", "1200"))
# Pivotes seguidos sin aportar NINGÚN identificador nuevo antes de cortar el BFS
# por rendimientos decrecientes (aunque queden nodos en cola y tope sin agotar).
HUELLA_VENTANA_SIN_NOVEDAD = 5

# --- Motor de huella digital: correlación avanzada (huella.py, Tanda D) ------
# Distancia de Hamming (hash de 64 bits) para enlazar dos avatares como "el mismo".
# Conservador a propósito: el propio documento avisa de falsos positivos con avatares
# genéricos/por defecto — verificado con avatares reales distintos (distancia 24).
HUELLA_PHASH_UMBRAL = 6
HUELLA_AVATAR_TIMEOUT = 8          # s por descarga de avatar para pHash
# Dorking del pivote nombre: vía `ddgs` (DuckDuckGo), no navegador — Google bloquea el
# Chromium headless sin perfil persistente con un muro anti-bot ("unusual traffic"),
# verificado real (no hipotético) al probar el dorking por Playwright headless.
HUELLA_DORK_RESULTADOS_MAX = 5

# --- Integración de WhatsApp (Baileys, Node.js) -------------------------------
# Baileys es una librería NO OFICIAL que reimplementa el protocolo de WhatsApp Web;
# usarla conlleva riesgo de que Meta limite o banee el número. Decisión ya tomada por
# el usuario, solo se anota. Requiere Node.js instalado (segundo runtime del proyecto,
# fuera del venv de Python) y `npm install` dentro de whatsapp/.
WHATSAPP_DIR = ROOT / "whatsapp"                  # servicio Node, hermano de cristopher/
WHATSAPP_DATA = DATA / "whatsapp"                 # sesión + histórico local (gitignored)
WHATSAPP_SESSION_DIR = WHATSAPP_DATA / "session"  # credenciales Baileys (useMultiFileAuthState)
WHATSAPP_STORE_DIR = WHATSAPP_DATA / "store"      # histórico de mensajes en JSON por chat
WHATSAPP_LOG = WHATSAPP_DATA / "server.log"       # stdout/stderr del proceso Node
WHATSAPP_PORT = int(os.getenv("CRISTOPHER_WHATSAPP_PORT", "8766"))
WHATSAPP_START_TIMEOUT = 20   # s: esperando a que /health responda tras lanzar el proceso Node
WHATSAPP_HTTP_TIMEOUT = 15    # s: timeout por request a la API del servicio Node


class ConfigError(RuntimeError):
    """Fallo de configuración que impide arrancar (p. ej. falta la API key)."""


def get_api_key() -> str:
    """Devuelve la GEMINI_API_KEY o lanza ConfigError con instrucciones claras."""
    key = os.getenv("GEMINI_API_KEY", "").strip()
    if not key:
        raise ConfigError(
            "Falta GEMINI_API_KEY.\n"
            "  1) Consíguela gratis en https://aistudio.google.com/apikey\n"
            "  2) Copia .env.example a .env y pega la clave ahí, o exporta\n"
            "     la variable de entorno GEMINI_API_KEY antes de arrancar."
        )
    return key
