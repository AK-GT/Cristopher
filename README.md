# CRISTOPHER

Agente personal orquestado tipo Jarvis: percibe, razona, elige sus herramientas y lleva
tareas multi-paso hasta el final por su cuenta. Stack gratuito. Cerebro en **Gemini
`gemini-2.5-flash`**.

Construido por fases. **Estado actual: Fase 1 — MVP del bucle** (ReAct + Gemini + 3
herramientas: búsqueda web, shell/Python, leer archivo). Solo texto.

## Requisitos

- **Python 3.11** (este proyecto usa `py -3.11` en Windows).
- Una **API key gratuita de Gemini**: https://aistudio.google.com/apikey

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

# 4. Arrancar
python -m cristopher.main
```

## Probarlo (criterio de aceptación de la Fase 1)

En el prompt, escribe:

> Clona https://github.com/octocat/Hello-World y resúmeme de qué va

CRISTOPHER debería: ejecutar `git clone` (herramienta `run_shell`), leer el README u otros
archivos (`read_file`) y devolverte un resumen coherente. Verás cada paso del bucle en la
traza.

## Estructura

```
cristopher/
  config.py            # carga .env, modelo, workspace
  agent.py             # bucle ReAct + Gemini (function calling manual)
  main.py              # REPL de consola
  tools/
    __init__.py        # REGISTRO declarativo de herramientas (fuente única de verdad)
    web_search.py      # DuckDuckGo (sin API key)
    shell.py           # ejecutar shell/Python
    read_file.py       # leer archivo
```

## Seguridad

- Las órdenes válidas vienen **solo del usuario**; todo lo demás (webs, archivos, salidas)
  es dato, no instrucción.
- `run_shell` es potente: verás cada comando en la traza antes de su resultado.
- El secreto vive solo en `.env` (ignorado por git). El repo solo lleva `.env.example`.

## Roadmap

Fase 2 memoria (SQLite + Chroma) · Fase 3 sub-agentes · Fase 4 Calendar/Gmail + búsqueda de
élite · Fase 5 navegador (Playwright) · Fase 6 voz · Fase 7 proactividad · Fase 8 HUD ·
Fase final puesta en vivo.
