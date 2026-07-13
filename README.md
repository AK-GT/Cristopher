# CRISTOPHER

Agente personal orquestado tipo Jarvis: percibe, razona, elige sus herramientas, delega en
sub-agentes y lleva tareas multi-paso hasta el final por su cuenta. Stack gratuito. Cerebro
en **Gemini** (`gemini-3.5-flash`, free tier; con cadena de respaldo al agotar cuota).

**Estado: en vivo (v1.0) — todas las fases completas.** Bucle ReAct + memoria persistente +
sub-agentes + integraciones Google + navegador + voz + proactividad + HUD.

## Requisitos

- **Python 3.11** (este proyecto usa `py -3.11` en Windows).
- Una **API key gratuita de Gemini**: https://aistudio.google.com/apikey
- (Opcional) `data/google/credentials.json` (OAuth) para Calendar/Gmail, y `TAVILY_API_KEY`
  para la búsqueda de élite (sin ella cae a DuckDuckGo).

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
- **Sub-agentes** — `delegar_a_claude` para tareas de código en carpeta aislada.
- **Voz** — activar/desactivar modo audio por intención.
- **Recordatorios** — programar avisos que dispara el demonio proactivo.

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
  proactivo.py         # demonio proactivo (eventos, correo, recordatorios)
  bus.py / estado.py   # bus de estado del HUD / modo voz en caliente
  hud/                 # HUD web (command-center + núcleo neuronal, SSE)
  tools/               # registro de herramientas (fuente única de verdad)
```

## Seguridad

- Las órdenes válidas vienen **solo del usuario**; todo lo demás (webs, archivos, correos,
  salidas) es dato, no instrucción.
- Confirmación explícita antes de acciones irreversibles (enviar correo, borrar, etc.).
- `run_shell` es potente: verás cada comando en la traza antes de su resultado.
- El secreto vive solo en `.env` (ignorado por git). El repo solo lleva `.env.example`.
