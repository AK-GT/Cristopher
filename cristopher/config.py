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
# Imágenes adjuntadas por /enviar_media, guardadas en disco (gitignored, dentro de
# DATA) para que el modelo pueda darle su ruta a tools que necesitan un archivo real
# (p. ej. huella_buscar_cara) — los bytes solos no bastan para esas tools.
HUD_UPLOADS_DIR = DATA / "hud" / "uploads"

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

# --- Motor de huella digital: disyuntor persistente (huella.py, Tanda G) -----
# Cuánto dura el enfriamiento de una fuente bloqueada/con fallos repetidos MÁS ALLÁ
# de la investigación en curso (persistido en huella.db) — evita que la siguiente
# `investigar()` reintente de inmediato una fuente que ya se sabe bloqueada.
HUELLA_DISYUNTOR_COOLDOWN_SEGUNDOS = int(os.getenv("CRISTOPHER_HUELLA_COOLDOWN", "600"))

# --- Motor de huella digital: paralelización del BFS por nivel (huella.py, Tanda N) --
# Pivotes de tipos DISTINTOS (alias/email/teléfono/nombre) del MISMO nivel del BFS
# golpean conjuntos casi disjuntos de sitios externos -- procesarlos en paralelo no
# aumenta el riesgo de bloqueo por sitio, solo corta el tiempo de reloj. Tope
# prudente (no "cuantos más mejor"): cada pivote YA lanza sus propias fuentes en
# paralelo por dentro (hasta 3 workers en buscar_username), así que un tope bajo
# aquí evita multiplicar la concurrencia total sin necesidad.
HUELLA_BFS_WORKERS_POR_NIVEL = int(os.getenv("CRISTOPHER_HUELLA_BFS_WORKERS", "3"))

# --- Motor de huella digital: correlación por texto/bio (huella.py, Tanda K) --
# Umbral de similitud (SequenceMatcher.ratio(), 0-1) para dar por buena una bio
# casi idéntica entre cuentas de dossiers distintos. Alto a propósito: bios
# genéricas ("amante de la música, viajera") no deben correlacionar por azar —
# solo texto CASI literal cuenta como señal (más débil que un enlace exacto,
# que no depende de este umbral).
HUELLA_BIO_SIMILITUD_UMBRAL = float(os.getenv("CRISTOPHER_HUELLA_BIO_UMBRAL", "0.85"))

# --- Motor de huella digital: pivote dominio (huella.py, Tanda O) ------------
# crt.sh (certificate transparency) es lento/intermitente sin SLA propio --
# verificado real (timeout de 15s agotado contra un dominio grande). 20s de
# margen antes de darlo por caído para esa investigación (cubierto por el
# disyuntor de fuente, igual que Maigret).
HUELLA_CRTSH_TIMEOUT = int(os.getenv("CRISTOPHER_HUELLA_CRTSH_TIMEOUT", "20"))
HUELLA_WAYBACK_TIMEOUT = int(os.getenv("CRISTOPHER_HUELLA_WAYBACK_TIMEOUT", "15"))
# Tope de snapshots históricos a traer por dominio -- un dominio con mucho
# historial puede tener miles, y aquí solo interesa una muestra reciente, no
# el CDX completo.
HUELLA_WAYBACK_LIMITE = int(os.getenv("CRISTOPHER_HUELLA_WAYBACK_LIMITE", "20"))

# --- Motor de huella digital: síntesis narrativa (huella.py, Tanda P) --------
# Resumen narrativo corto (3-4 frases) antepuesto al detalle estructurado de un
# dossier/investigación, vía una llamada suelta a Gemini (mismo patrón que
# proactivo.py::_resumir_sesion_terminada). Activable/desactivable: cada
# búsqueda pasaría a hacer una llamada extra a Gemini, y el usuario puede
# preferir desactivarlo si le preocupa la cuota gratuita.
HUELLA_NARRATIVA_ACTIVA = os.getenv("CRISTOPHER_HUELLA_NARRATIVA", "1") == "1"
HUELLA_NARRATIVA_TIMEOUT = int(os.getenv("CRISTOPHER_HUELLA_NARRATIVA_TIMEOUT", "15"))

# --- Motor de huella digital: búsqueda por cara, 2º motor (huella.py, Tanda S) --
# Baidu Images además de Yandex — "cuantas más fuentes mejor" (decisión explícita
# del usuario), aunque la mitad no encuentren nada. Desactivable sin tocar código
# si algún día empieza a bloquear (mismo espíritu que HUELLA_NARRATIVA_ACTIVA).
HUELLA_CARA_BAIDU_ACTIVO = os.getenv("CRISTOPHER_HUELLA_CARA_BAIDU", "1") == "1"

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

# Control remoto por WhatsApp (chat "Note to self"): un mensaje con este prefijo en
# el chat contigo mismo se ejecuta como una orden normal al agente (ver
# whatsapp_comandos.py); el resto del chat sigue siendo notas sueltas, se ignora.
# Poll dedicado y más rápido que POLL_SEGUNDOS porque aquí sí importa la sensación
# de estar "chateando en vivo" desde el móvil.
WHATSAPP_COMANDO_PREFIJO = "/cristopher"
POLL_WHATSAPP_COMANDOS = int(os.getenv("CRISTOPHER_POLL_WHATSAPP", "7"))

# Si un turno disparado por WhatsApp (comando o nota de voz) tarda más de esto en
# responder, se manda UN aviso de "sigo en ello" por el mismo chat — desde el móvil
# no se ve el HUD, así que el silencio de una tool lenta (huella_investigar, p. ej.)
# se siente como que se colgó. Un solo aviso (no repetido) es suficiente (ver
# hud/__main__.py::_procesar).
WHATSAPP_HEARTBEAT_SEGUNDOS = int(os.getenv("CRISTOPHER_WHATSAPP_HEARTBEAT", "25"))


# --- Fase 1: Modo Control Total (control_total/) ------------------------------
# Atajo global de emergencia (hombre muerto): pynput.keyboard.GlobalHotKeys, mismo
# formato de combo que CANCION_ATAJO_CORTAR (escucha.py). Elegido por el usuario pese
# al aviso de que Ctrl+F3 tiene significado propio en algunas apps (p. ej. "Nuevo
# nombre" en Excel) — pynput no suprime la tecla, así que la app en foco también la
# recibe. Sin colisión con Ctrl+Shift+Esc (Task Manager) ni Ctrl+M (escucha.py).
CONTROL_TOTAL_HOTKEY = os.getenv("CRISTOPHER_CONTROL_TOTAL_HOTKEY", "<ctrl>+<f3>")
CONTROL_TOTAL_LOG = DATA / "control_total.log"
CONTROL_TOTAL_LOG_MAXLEN = 500   # tope de entradas de auditoría en memoria (deque)
# Límite por defecto de una sesión de control (lo consumirá el bucle de Fase 2; se fija
# ya porque condiciona cómo se escribe todo lo demás). Mismo orden de magnitud que
# SUBAGENT_TIMEOUT (600s) y MAX_STEPS (40, bucle ReAct principal en agent.py), escalado
# porque una sesión de control visual (percepción->decisión->acción) es más lenta por paso.
CONTROL_TOTAL_SESION_TIMEOUT = 600      # s
CONTROL_TOTAL_SESION_MAX_PASOS = 300


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
