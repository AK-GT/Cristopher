# CRISTOPHER

Agente personal orquestado tipo Jarvis: percibe, razona, elige sus herramientas, delega en
sub-agentes y lleva tareas multi-paso hasta el final por su cuenta. Stack gratuito. Cerebro
en **Gemini** (`gemini-flash-latest`, free tier; con cadena de respaldo al agotar cuota).

**Estado: en vivo (v1.0) — todas las fases completas.** Bucle ReAct + memoria persistente +
sub-agentes + integraciones Google + navegador + voz + proactividad + HUD.

## Requisitos

- **Python 3.11** (este proyecto usa `py -3.11` en Windows).
- Una **API key gratuita de Gemini**: https://aistudio.google.com/apikey
- (Opcional) `data/google/credentials.json` (OAuth) para Calendar/Gmail, y `TAVILY_API_KEY`
  para la búsqueda de élite (sin ella cae a DuckDuckGo).
- (Opcional) **Node.js LTS** para la tool de WhatsApp (ver más abajo).

## Puesta en marcha

```powershell
# 1. Crear el entorno virtual con Python 3.11
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1        # PowerShell
# (Git Bash:  source .venv/Scripts/activate)

# 2. Instalar dependencias
pip install -r requirements.txt

# 3. Configurar la clave (nunca va al repo)
copy .env.example .env               # luego edita .env y pega tu GEMINI_API_KEY

# 4. Arrancar el sistema vivo (HUD command-center)
python -m cristopher
```

### WhatsApp (opcional)

Tool para leer/enviar mensajes del WhatsApp **personal** del usuario bajo petición
conversacional (nunca un bot autónomo). Usa Baileys, una librería **no oficial** que
reimplementa WhatsApp Web — su uso conlleva riesgo de que Meta limite o banee el número;
riesgo ya asumido, decisión tomada. Setup de una sola vez:

```powershell
# Requiere Node.js LTS instalado: https://nodejs.org
cd whatsapp
npm install
node setup_qr.js        # escanea el QR desde WhatsApp > Dispositivos vinculados
```

La sesión queda persistida en `data/whatsapp/session/` (gitignored) y sobrevive a
reinicios: `python -m cristopher` la reutiliza sola, arrancando el servicio Node en
segundo plano la primera vez que hace falta (tool call o ciclo del demonio proactivo)
sin volver a pedir QR.

## Cómo arrancarlo (tres superficies)

| Comando | Qué es |
| --- | --- |
| `python -m cristopher` | **Sistema vivo**: se presenta (saludo de arranque), levanta el HUD y el demonio proactivo, con voz y métricas. |
| `python -m cristopher.main` | REPL de texto con trazas del bucle ReAct. |
| `python -m cristopher.voz_repl` | Conversación por voz (push-to-talk: mantén ESPACIO para hablar). |

## Qué puede hacer

Sus capacidades son exactamente las herramientas de su registro (`cristopher/tools/__init__.py`),
que es también lo que declara honestamente si le preguntas:

- **Shell/Python y archivos** — clonar repos, ejecutar comandos, leer archivos.
- **Búsqueda e investigación** — búsqueda de élite con síntesis y fuentes (Tavily/DuckDuckGo).
- **Navegador (Playwright)** — abrir páginas, leer HTML, pinchar/desplazar, y captura con
  visión cuando el texto no basta.
- **Google** — próximos eventos de Calendar, leer Gmail, y **enviar correo con confirmación**.
- **Memoria** — `remember`/`recall` de hechos entre sesiones (SQLite + vector store).
- **Notas y tareas** — apuntes rápidos y una lista pendiente/en_proceso/hecho, ambas en
  `data/memory.db`.
- **Sub-agentes** — `delegar_a_claude`/`analizar_proyecto` para tareas de código en
  carpeta aislada (un solo turno, bloqueante).
- **Modo agentes** — sesiones **vivas** de Claude Code (`sesion_crear`/`escribir`/
  `listar`/`leer`/`parar`) sobre un proyecto real del disco, en paralelo y sin bloquear
  a CRISTOPHER; panel propio en el HUD (🤖). Si una sesión se queda esperando una
  respuesta, CRISTOPHER redacta una propuesta con su propia voz y pide tu confirmación
  antes de mandarla — nunca responde por su cuenta.
- **Modo concentración** — bloquea deep work: despeja apps distractoras (minimiza o
  cierra), pone música de foco, ajusta el volumen y encola (sin perder) los avisos de
  baja prioridad hasta que termina el bloque; soporta ciclos foco/descanso si se lo
  pides explícitamente.
- **Modo diario** — reflexión diaria conversacional categorizada (evento social, dilema,
  hecho destacado, tarea futura, libre), activable/desactivable por intención.
- **Voz** — activar/desactivar modo audio por intención.
- **Recordatorios** — programar avisos que dispara el demonio proactivo.
- **Música** — reproducir/cola/favoritos/listas con motor propio (biblioteca local +
  respaldo `yt-dlp`, control por VLC).
- **Control de PC** — apps, ventanas, volumen, capturas de pantalla con visión,
  organizar/buscar archivos, apagar/bloquear/suspender (con confirmación si hace falta).
- **WhatsApp (opcional)** — leer/enviar mensajes del WhatsApp personal bajo petición
  explícita (Baileys, no oficial; ver sección de setup arriba).

## Puesta en vivo (criterio de aceptación de la fase final)

El *prompt de runtime* (`cristopher_mega_prompt.md`, §1-§8) está instalado como system
prompt del agente vivo (`cristopher/agent.py` → `IDENTITY`), con las herramientas
auto-generadas desde el registro para que nunca mienta sobre lo que puede hacer.

Arranca `python -m cristopher` y pregúntale *"¿quién eres y qué puedes hacer?"*: debe
presentarse como orquestador en español y enumerar sus herramientas **reales**.

## Estructura

```
cristopher/
  __main__.py          # launcher del sistema vivo (python -m cristopher)
  config.py            # carga .env, modelos, workspace, scopes Google
  agent.py             # bucle ReAct + Gemini (function calling manual) + system prompt
  main.py              # REPL de consola (texto)
  voz_repl.py          # REPL por voz (push-to-talk)
  memory.py            # memoria persistente (SQLite + vector store)
  notas.py / tareas.py # notas rápidas y lista de tareas (mismo SQLite que memory.py)
  diario.py            # modo diario: reflexión diaria conversacional categorizada
  concentracion.py     # modo concentración: bloquea deep work (apps/música/volumen)
  sesiones.py          # modo agentes: sesiones vivas de Claude Code sobre un proyecto real
  proactivo.py         # demonio proactivo (eventos, correo, recordatorios, WhatsApp, sesiones)
  whatsapp_client.py   # cliente/gestor del servicio Node de WhatsApp (Baileys)
  bus.py / estado.py   # bus de estado del HUD / modo voz en caliente
  hud/                 # HUD web (command-center + núcleo neuronal 3D, widgets movibles, SSE)
  tools/               # registro de herramientas (fuente única de verdad)
whatsapp/              # servicio Node.js (Baileys) — runtime separado, ver README
```

Documentos de diseño en la raíz (prompts de tarea para Claude Code, uno por
funcionalidad, revisados antes de implementar): `cristopher_mega_prompt.md` (system
prompt de referencia), `PLAN_GESTOR_SESIONES.md` (modo agentes, ya construido),
`PROMPT_modo_concentracion_para_claude_code.md` (modo concentración, ya construido),
`PROMPT_memoria_episodica_para_claude_code.md` y `DISENO_motor_huella_por_pivotes.md`
(diseñados, **aún no implementados**).

## Seguridad

- Las órdenes válidas vienen **solo del usuario**; todo lo demás (webs, archivos, correos,
  salidas) es dato, no instrucción.
- Confirmación explícita antes de acciones irreversibles (enviar correo, borrar, etc.).
- `run_shell` es potente: verás cada comando en la traza antes de su resultado.
- El secreto vive solo en `.env` (ignorado por git). El repo solo lleva `.env.example`.
