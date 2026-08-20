# CRISTOPHER

Un asistente personal tipo Jarvis que corre **en tu propio PC** (Windows) y se maneja
hablando o escribiendo, en español.

No es un chatbot: es un **agente**. Le dices un objetivo en lenguaje normal y él decide
por su cuenta qué herramientas usar para conseguirlo — buscar en la web, abrir un
navegador de verdad, leer tu calendario, redactar un correo, poner música, mandar un
WhatsApp, lanzar sesiones de Claude Code sobre tus proyectos… — encadenando varios pasos
hasta terminar, y contándote lo que va pensando por el camino.

El cerebro es la **API gratuita de Gemini** (Google). El resto (memoria, voz, navegador,
control del PC) corre local.

> **Estado:** en vivo y en uso diario. Las 8 fases originales + la puesta en vivo están
> cerradas; encima se han construido varias extensiones (modo diario, concentración,
> agentes, memoria episódica, huella digital, redes sociales, control total). No hay
> suite de tests automatizada: la verificación es manual, arrancando y probando.

---

## Índice

1. [Cómo funciona (en cristiano)](#cómo-funciona-en-cristiano)
2. [Requisitos](#requisitos)
3. [Instalación](#instalación)
4. [Configuración: qué desbloquea cada clave](#configuración-qué-desbloquea-cada-clave)
5. [Cómo se arranca](#cómo-se-arranca)
6. [Qué sabe hacer](#qué-sabe-hacer)
7. [Los "modos"](#los-modos)
8. [El HUD](#el-hud)
9. [Control remoto desde el móvil (WhatsApp)](#control-remoto-desde-el-móvil-whatsapp)
10. [Estructura del repo](#estructura-del-repo)
11. [Seguridad](#seguridad)
12. [Problemas típicos](#problemas-típicos)
13. [Documentos de diseño](#documentos-de-diseño)

---

## Cómo funciona (en cristiano)

Cada vez que le dices algo, CRISTOPHER da vueltas a este bucle (se llama **ReAct**:
*razonar + actuar*) hasta que la tarea está hecha:

```
   tú dices algo
        │
        ▼
  ┌───────────────────────────────────────────────┐
  │ 1. Recuerda        busca en su memoria lo que  │
  │                    sepa relevante sobre ti     │
  │ 2. Piensa          Gemini decide: ¿contesto ya │
  │                    o necesito una herramienta? │
  │ 3. Actúa           ejecuta la herramienta      │
  │                    (buscar, navegar, leer…)    │
  │ 4. Observa         mete el resultado de vuelta │
  │                    y vuelve al paso 2          │
  └───────────────────────────────────────────────┘
        │  (cuando ya no necesita herramientas)
        ▼
   pensamiento visible  ===RESPUESTA===  respuesta final
```

Tres ideas que explican casi todo el proyecto:

- **Una sola fuente de verdad para sus capacidades.** Todas las herramientas viven en un
  registro único (`cristopher/tools/__init__.py`, ~147 herramientas). De esa lista salen a
  la vez lo que Gemini "ve" que puede llamar, el despachador que las ejecuta y la
  enumeración que aparece en su prompt. Por eso, si le preguntas qué sabe hacer, no puede
  inventárselo.
- **Elige por intención, nunca por palabras clave.** No hay ningún `if "música" in texto`.
  Es el modelo el que decide, a partir de lo que quieres decir.
- **El pensamiento se enseña.** Verás su razonamiento antes de la respuesta (separados por
  el marcador `===RESPUESTA===`). En modo voz solo se habla la respuesta final.

Y una decisión de diseño que conviene conocer: **el modelo se fija con versión explícita**
(`gemini-3.1-flash-lite`), nunca un alias `-latest`, porque Google los mueve sin avisar y
ya cambió el cerebro por debajo a peor. Si el modelo principal agota su cuota (error 429),
cae solo a un modelo de respaldo con su propio cubo de cuota.

---

## Requisitos

| Qué | ¿Obligatorio? | Para qué |
| --- | --- | --- |
| **Windows** | Sí | El control del PC, las capturas y los atajos globales son específicos de Windows. |
| **Python 3.11** | Sí | El proyecto se instala con `py -3.11`. |
| **API key de Gemini** | Sí | El cerebro. Gratis en https://aistudio.google.com/apikey |
| **VLC de 64 bits** | Solo para música | Motor de reproducción (o VLC portable en `data/vlc`). |
| **Node.js LTS** | Solo para WhatsApp | El servicio de WhatsApp es un proceso Node aparte. |
| **Chrome** | Recomendado | El HUD se abre en el navegador; Playwright instala su propio Chromium. |

---

## Instalación

```powershell
# 1. Entorno virtual con Python 3.11 (obligatorio, no vale otra versión)
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1          # PowerShell
# (Git Bash:  source .venv/Scripts/activate)

# 2. Dependencias
pip install -r requirements.txt
playwright install chromium           # navegador para las herramientas web

# 3. La clave (el archivo .env NUNCA se sube al repo)
copy .env.example .env                # edita .env y pega tu GEMINI_API_KEY

# 4. Arrancar
python -m cristopher
```

Eso ya te deja un CRISTOPHER funcional: conversación, memoria, búsqueda, navegador,
archivos, control del PC, notas, tareas y HUD. Todo lo demás es opcional y se activa
añadiendo credenciales.

### Extras opcionales

<details>
<summary><b>Google Calendar y Gmail</b></summary>

No es una clave en `.env`, es un archivo. En Google Cloud crea unas credenciales OAuth de
tipo **"App de escritorio"**, descarga el JSON y guárdalo como
`data/google/credentials.json`. La primera vez que uses una herramienta de Google se abre
el navegador para dar el consentimiento; el token queda en `data/google/token.json`.
</details>

<details>
<summary><b>WhatsApp</b> (requiere Node.js)</summary>

Usa **Baileys**, una librería *no oficial* que reimplementa WhatsApp Web: conlleva riesgo
de que Meta limite o banee el número. Riesgo asumido a conciencia.

```powershell
cd whatsapp
npm install
node setup_qr.js     # escanea el QR desde WhatsApp > Dispositivos vinculados
```

La sesión queda en `data/whatsapp/session/` y sobrevive a reinicios. A partir de ahí,
`python -m cristopher` arranca el servicio Node solo, en segundo plano, la primera vez que
hace falta. No vuelve a pedir QR.
</details>

<details>
<summary><b>Redes sociales</b> (YouTube / Instagram / TikTok)</summary>

Multi-cuenta: cada cuenta se conecta con un alias propio (`redes_cuenta_conectar`) y su
propio token bajo `data/redes/`.

- **YouTube:** reutiliza el mismo `data/google/credentials.json`. Solo hay que activar
  *YouTube Data API v3* y *YouTube Analytics API* en ese proyecto de Google Cloud.
- **Instagram:** app en developers.facebook.com con "Business login for Instagram" →
  `META_APP_ID` y `META_APP_SECRET` en `.env`.
- **TikTok:** app en developers.tiktok.com con "Login Kit" → `TIKTOK_CLIENT_KEY` y
  `TIKTOK_CLIENT_SECRET`. Para uso personal basta el modo Sandbox.
- **Publicar Reels en Instagram** exige además una URL pública temporal del vídeo: se
  resuelve con un bucket de Google Cloud Storage (`GCS_BUCKET_TEMPORAL` +
  `data/google/gcs-service-account.json`).
</details>

<details>
<summary><b>Voces en la nube (ElevenLabs) y búsqueda Tavily</b></summary>

- `ELEVENLABS_API_KEY`: voces mucho más naturales. Sin ella, CRISTOPHER habla igual con
  **Piper** (voz local, gratis, sin internet).
- `TAVILY_API_KEY`: búsqueda web rápida y con síntesis. Sin ella cae automáticamente a
  DuckDuckGo.
</details>

<details>
<summary><b>Huella digital (OSINT)</b></summary>

La mayoría de motores (Maigret, Socialscan, Sherlock, user-scanner, ignorant…) se instalan
con `requirements.txt`. **GHunt** (perfiles de Gmail) se deja fuera a propósito: entra en
conflicto de versiones con el SDK de Gemini. Si lo quieres, instálalo aislado
(`pipx install ghunt`) para que quede en el PATH sin tocar el entorno del proyecto.
</details>

---

## Configuración: qué desbloquea cada clave

Todo vive en `.env` (ignorado por git; el repo solo lleva `.env.example`).

| Variable | ¿Necesaria? | Qué pasa si falta |
| --- | --- | --- |
| `GEMINI_API_KEY` | **Sí** | No arranca. |
| `CRISTOPHER_MODEL` | No | Usa `gemini-3.1-flash-lite`. |
| `CRISTOPHER_FALLBACK_MODEL` | No | Usa `gemini-3.1-flash-lite-preview` al agotar cuota. |
| `TAVILY_API_KEY` | No | Busca con DuckDuckGo. |
| `ELEVENLABS_API_KEY` | No | Habla con Piper (local). |
| `META_APP_ID` / `META_APP_SECRET` | No | Sin Instagram. |
| `TIKTOK_CLIENT_KEY` / `_SECRET` | No | Sin TikTok. |
| `GCS_BUCKET_TEMPORAL` | No | Puede leer Instagram pero no publicar Reels. |
| `CRISTOPHER_HUD_PORT` | No | HUD en el puerto `8765`. |
| `CRISTOPHER_PALMADA_*` | No | Umbrales de la escucha por palmadas (ver `--calibrar`). |
| `CRISTOPHER_CONTROL_TOTAL_HOTKEY` | No | Kill-switch en `Ctrl+F3`. |

⚠️ **Aviso sobre modelos, aprendido a base de fallo:** no pongas aquí
`gemini-3.5-flash-lite` (no llama a las herramientas: contesta "Anotado, señor" sin apuntar
nada) ni `gemini-3.5-flash` / `gemini-3.6-flash` (**20 peticiones al día**, y un solo turno
gasta de 2 a 4). Está medido, no es teoría.

---

## Cómo se arranca

Hay varias "superficies" que comparten exactamente el mismo agente; solo cambia cómo hablas
con él.

| Comando | Qué es |
| --- | --- |
| `python -m cristopher` | **El sistema vivo.** Saludo de arranque + HUD en el navegador + demonio proactivo + voz. Es el modo normal. |
| `python -m cristopher.main` | REPL de texto en la terminal, con las trazas del bucle ReAct a la vista. Para depurar. |
| `python -m cristopher.voz_repl` | Conversación por voz pura (push-to-talk: mantén **ESPACIO**). |
| `python -m cristopher.escucha` | Escucha ambiental: **Ctrl+N** arma, **2 palmadas** despiertan el sistema (suena la canción de arranque y se lanza el HUD). **Ctrl+F** cancela la escucha hasta reiniciar. |
| `python -m cristopher.escucha --instalar` | Que la escucha arranque sola al iniciar sesión de Windows. (`--desinstalar` lo quita.) |
| `python -m cristopher.calibracion` | Mide el nivel de audio de la sala para ajustar el umbral de palmadas. |
| `python -m cristopher.hud.widget_flotante` | Widget pequeño y siempre visible, contra el HUD que ya esté corriendo. |
| `python -m cristopher.control_total.killswitch` | Deja el freno de emergencia (Ctrl+F3) escuchando aunque no haya nada más corriendo. |

Si lanzas `python -m cristopher` y ya hay una instancia viva, no apila otra: te reabre la
pestaña del HUD.

---

## Qué sabe hacer

Sus capacidades **son** su registro de herramientas (~147). Resumen por áreas:

**Conversación y memoria**
- Memoria de hechos entre sesiones (SQLite + embeddings, búsqueda por similitud).
- **Memoria episódica**: línea de tiempo de lo que pasó y cuándo, para preguntas del tipo
  "¿cuándo hablé de esto?".
- **Notas** rápidas y **lista de tareas** (pendiente / en proceso / hecho).
- **Personalidad autoeditable**: cuando detecta una preferencia tuya clara, se ajusta el
  tono o los gustos él solo (y puedes ver/quitar esas directivas).

**Información**
- Búsqueda web con síntesis y fuentes (Tavily, o DuckDuckGo de respaldo).
- **Navegador real** (Playwright): abrir páginas, leer el HTML, pinchar, hacer scroll y
  capturar con visión cuando el texto no basta. "Buscar en Google delante de ti" es un modo
  aparte, elegido por intención frente a la búsqueda silenciosa.
- **Súper briefing diario**: agenda, correos, recordatorios y noticias de los temas que le
  vas marcando como de interés.
- **Google**: próximos eventos, crear eventos, leer y buscar correos, responder, marcar
  como leído y **enviar (solo con tu confirmación)**.

**Tu PC**
- Abrir y cerrar apps, gestionar ventanas, volumen del sistema, bloquear / apagar /
  reiniciar / suspender, leer el portapapeles.
- Capturas de pantalla y de la ventana activa, interpretadas con visión.
- Buscar archivos, resumir documentos, organizar carpetas.
- Ejecutar comandos de shell (verás cada comando en la traza).

**Tiempo**
- **Recordatorios**: avisos de texto que dispara el demonio proactivo.
- **Programador de tareas**: distinto de un recordatorio — es una instrucción **real** que
  se ejecuta con el turno completo del agente cuando toca, sola, en cadena, o esperando a
  que termine una sesión de Claude Code.

**Voz**
- Escucha con faster-whisper y habla con Piper (local) o ElevenLabs (nube), con catálogo de
  voces buscable y cambiable en caliente.

**Música**
- Motor propio: biblioteca local primero, `yt-dlp` como respaldo web, reproducción con VLC.
  Cola, favoritos y listas.

**Programación**
- **Sub-agentes de un turno** (`delegar_a_claude`, `analizar_proyecto`): tareas de código
  aisladas en su propia carpeta.
- **Sesiones vivas de Claude Code**: procesos de larga duración sobre proyectos **reales**
  del disco, en paralelo, con diff, detección de conflictos y limpieza de worktree. Si una
  sesión se queda esperando respuesta, CRISTOPHER te redacta una propuesta con su propia voz
  y pide tu OK antes de mandarla; nunca contesta por su cuenta.
- **Auto-arreglos**: cuando choca con un límite suyo, propone abrir una sesión de Claude
  Code sobre su propio código — enseñándote antes el prompt exacto.

**Redes sociales**
- Métricas y contenido de YouTube, Instagram y TikTok (multi-cuenta), con dashboard propio
  en el HUD e historial para detectar tendencias.
- Publicar: vídeo en YouTube, Reel en Instagram, vídeo en TikTok.

**WhatsApp** (opcional)
- Leer y enviar mensajes de tu WhatsApp personal, siempre a petición tuya. Nunca es un bot
  autónomo. El demonio te avisa de mensajes nuevos.

**Huella digital (OSINT, uso ético)**
- Investiga la huella pública de una identidad partiendo de un pivote (usuario, email,
  teléfono, dominio o una cara), cruzando varias fuentes independientes y puntuando la
  confianza de cada hallazgo. Genera un expediente reabrible y ampliable.
- Vigilancia continua, auditoría periódica, remediación y redacción de solicitudes de
  borrado RGPD. Pensado para auditar **tu propia** exposición.

---

## Los "modos"

Son estados que cambian cómo se comporta o cómo se ve el HUD. Todos se activan y desactivan
**hablando** (por intención), y algunos también con un botón.

| Modo | Qué hace |
| --- | --- |
| **Voz** | Responde hablando en vez de solo escribir. El pensamiento no se verbaliza. |
| **Diario** | Reflexión diaria conversacional, categorizada (evento social, dilema, hecho destacado, tarea futura, libre). |
| **Concentración** | Defiende un bloque de deep work: minimiza o cierra apps distractoras, pone música de foco, ajusta el volumen y **encola** (sin perder) los avisos poco importantes. Admite ciclos foco/descanso si los pides. |
| **Code** | El HUD se transforma en un panel de sesiones de Claude Code y el agente razona como gestor de esas sesiones. |
| **Redes** | El HUD se transforma en el dashboard de YouTube/Instagram/TikTok. (Excluyente con el modo Code.) |
| **Control total** | CRISTOPHER maneja el ratón y el teclado por su cuenta: mira la pantalla, decide **una** acción, la pasa por unos guardarraíles en código y la ejecuta. Repite hasta terminar o agotar el límite de pasos/tiempo. |

### Sobre el Modo Control Total

Es el modo con más poder y por eso el que más frenos lleva:

- **Kill-switch real: `Ctrl+F3`.** Es un hombre muerto, no un botón de cortesía: *cada*
  primitiva (mover, clicar, teclear, scroll) lo consulta **antes** de ejecutarse, así que
  detiene también una acción ya en marcha.
- **Guardarraíles en código, no en el prompt.** El veto (CAPTCHAs, credenciales, etc.) no es
  una instrucción que el modelo pueda ignorar: es una comprobación que se ejecuta.
- **Todo auditado**: cada acción queda registrada con qué, dónde, cuándo y con qué resultado.
- **Límites duros** de pasos y de tiempo por sesión.
- Un borde de pantalla y un widget flotante te muestran en todo momento que está activo y
  qué está haciendo.

---

## El HUD

Es la interfaz principal: un servidor local que se abre en el navegador
(`http://localhost:8765`).

- **Núcleo neuronal 3D** en el centro que reacciona al **estado real** del agente (pensando,
  ejecutando, hablando) — no es decoración.
- **Widgets movibles y redimensionables**: música, notas, tareas, sesiones, concentración,
  tareas programadas, métricas del sistema.
- **Eventos en vivo** por SSE: ves el pensamiento y las llamadas a herramientas según pasan.
- **Confirmaciones por clic**: las acciones irreversibles (enviar un correo, por ejemplo)
  bloquean al agente hasta que pinchas Confirmar o Cancelar. Si no contestas, se cancela —
  el silencio nunca es un sí.
- **Entrada por voz** con tres modos, seleccionables en la barra:
  - **Texto** — escribir normal.
  - **Audio pulsando** — mantén **`H`** (o el botón 🎤) para hablar; al soltar se manda.
  - **Audio libre** — manos libres: detecta cuándo empiezas a hablar (se calibra ~1 s contra
    el ruido real de la sala) y manda el turno tras 800 ms de silencio. Mientras CRISTOPHER
    habla, el micro no cuenta — si no, se oiría a sí mismo y entraría en bucle. En este modo
    no puedes interrumpirle a media frase; en "audio pulsando" sí.

Detalle importante de arquitectura: el agente vive en **un único hilo worker** que consume
una cola. Playwright es síncrono y queda atado al hilo que lo creó, así que nunca se crea un
agente por petición HTTP.

---

## Control remoto desde el móvil (WhatsApp)

Excepción deliberada y muy acotada a la regla de "WhatsApp nunca es autónomo":

Un mensaje que **te mandas a ti mismo** en tu propio chat ("Note to self") empezando por
`/Cristopher` se ejecuta como un turno normal del agente, con todas sus herramientas, y la
respuesta te vuelve por el mismo chat. Sigue siendo una orden literal tuya; solo llega por
otro canal. El resto de mensajes de ese chat se ignoran.

- Funciona también con **imágenes y PDFs** (con el prefijo en el pie de foto) y con **notas
  de voz** (estas disparan sin prefijo: mandarte una ya es un acto deliberado; se transcriben
  automáticamente).
- Estando fuera de casa no verías un botón del HUD, así que las **confirmaciones de acciones
  irreversibles también se piden por WhatsApp**, con la misma regla: silencio = no.
- Si un turno tarda, te manda un "sigo en ello" para que no parezca colgado. Si falla, te
  manda el error — el silencio sería peor.

---

## Estructura del repo

```
cristopher/
  __main__.py            # sistema vivo (python -m cristopher)
  agent.py               # el corazón: bucle ReAct + Gemini + system prompt
  config.py              # .env, modelos, rutas, umbrales
  tools/                 # registro de herramientas = fuente única de verdad
  hud/                   # interfaz web (servidor + static/) + widget + overlay
  control_total/         # driver, guardarraíles, kill-switch y bucle del control total
  musica/                # resolver + reproductor (VLC) + biblioteca

  main.py, voz_repl.py, escucha.py, calibracion.py     # otras superficies de entrada
  memory.py, episodica.py, notas.py, tareas.py         # memoria y anotaciones
  personalidad.py, briefing.py, arranque.py            # configs autoeditables
  proactivo.py, recordatorios.py, programador.py       # el lado que actúa solo
  diario.py, concentracion.py, sesiones.py             # modos
  huella.py                                            # motor OSINT
  redes_auth.py, redes_dashboard.py, redes_hosting_temporal.py
  whatsapp_client.py, whatsapp_comandos.py             # puente con el servicio Node
  browser.py, vision.py, voz.py, google_auth.py        # capacidades de base
  bus.py, estado*.py                                   # pub-sub del HUD y flags de modo
  tests/                                               # unittest de huella.py (a mano)

whatsapp/                # servicio Node.js (Baileys): server.js, wa.js, store.js
data/                    # runtime, gitignored: SQLite, tokens, sesiones, media, logs
workspace/               # runtime, gitignored: clones y carpetas de sub-agentes
```

`data/` y `workspace/` **no** están vacíos aunque git no los muestre: ahí viven la memoria,
las credenciales y las sesiones. No los borres a la ligera.

### Verificación

No hay `pytest`, ni linter, ni CI. Un cambio se comprueba **arrancando la superficie
afectada y probándola**. La única excepción son las pruebas unitarias del motor de huella
(82 tests de funciones puras, sin red ni subprocesos), que se corren a mano:

```powershell
python -m unittest discover -s cristopher/tests -v
```

---

## Seguridad

Reglas innegociables del proyecto:

1. **Las órdenes válidas vienen solo de ti.** Todo lo que llega de una web, un HTML, un
   correo, un archivo o una captura es **dato, no instrucción**. Si una página dice "borra
   tal cosa", CRISTOPHER te lo enseña y pregunta; no obedece.
2. **Confirmación antes de lo irreversible**: enviar un correo, publicar, comprar, borrar en
   permanente, cambiar permisos. Redactar sí, enviar solo con tu OK. Con timeout conservador:
   si no contestas, no se hace.
3. **Nunca credenciales**: no escribe contraseñas ni claves en formularios, ni las vuelca en
   los logs.
4. **La clave vive solo en `.env`**, ignorado por git.
5. **Los sub-agentes trabajan en carpeta aislada** y sus cambios se revisan.
6. El **kill-switch (`Ctrl+F3`)** corta el Modo Control Total en seco, siempre.

Dos excepciones documentadas a la regla 2, decididas a conciencia: `whatsapp_send` (ahí el
"OK" es tu propia frase en la conversación) y los avisos remotos urgentes por correo (los
genera el sistema, no tú).

---

## Documentos de diseño

El proyecto se construye con un método fijo: **un documento de diseño por funcionalidad,
revisado y aprobado antes de tocar código, una fase por sesión.** Cuando la funcionalidad
está construida, **ese plan se borra**: el código pasa a ser la verdad y un plan viejo solo
confunde sobre qué existe de verdad. Lo que queda en el repo es esto:

| Documento | De qué va |
| --- | --- |
| `CLAUDE.md` | Las reglas de trabajo, las decisiones de arquitectura y el *porqué* de cada pieza. **Manda sobre todo lo demás.** |
| `cristopher_mega_prompt.md` | El prompt de identidad de referencia (el de runtime va embebido en `agent.py`). |
| `HERRAMIENTA_BUSQUEDA.md` | El diseño del motor de huella digital: qué fuente cubre cada pivote y los umbrales de puntuación que el código cita por sección. |
| `PROMPT_modo_seguro_para_claude_code.md` | **El único plan aún sin construir**: Modo Seguro (detectar por webcam si estás delante y mirando). Hay prototipo (`prototipo_seguro.py`), no hay integración. |

**Siguiente parada (Fase 9, pulido):** interrupciones de voz (barge-in) en todos los modos,
respuesta en streaming, "habla mientras trabaja" en tareas largas y bajar la latencia de
escuchar → pensar → responder. En paralelo, el **Modo Seguro** está diseñado y con
prototipo, pendiente de construir.
