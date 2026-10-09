# PLAN — Cristopher en la nube (acceso desde cualquier sitio)

Documento de diseño. Según CLAUDE.md: se revisa y aprueba antes de tocar código,
una fase por sesión, y se BORRA cuando la funcionalidad esté construida (lo que valga
la pena conservar pasa a CLAUDE.md).

## Objetivo
Hablar con Cristopher desde cualquier sitio, sin depender del PC de casa:
1. Primero por WhatsApp (texto y notas de voz), con el PC apagado.
2. Después, con una pulsera con pulsador + micro y un auricular: pulsas, hablas, te responde al oído.

## Punto de partida (ya existe)
- Control remoto por WhatsApp: `/Cristopher` en "Note to self", notas de voz transcritas,
  adjuntos y confirmaciones (§9) por el mismo chat (`whatsapp_comandos.py`).
- Voz: faster-whisper (STT) y Piper / ElevenLabs (TTS), ya portables a Linux.
- El único problema: todo corre en un PC con Windows. Hay que mudarlo, no rehacerlo.

## Decisiones tomadas
- NO se reescribe nada. Se añade un **modo servidor** (`CRISTOPHER_MODO=servidor`).
- En modo servidor, el registro `TOOLS` omite las herramientas de escritorio. El prompt
  enumera `TOOLS`, así que el modelo deja de verlas solas (fuente única de verdad).
- Servidor: VPS pequeño siempre encendido (Hetzner ~4 EUR/mes, 4 GB RAM por Whisper `small`),
  systemd (sin Docker: menos piezas). Nada de esto se instala en el PC del usuario: Docker/WSL
  NO hacen falta. Sin puertos abiertos al exterior; el HTTP interno solo en 127.0.0.1.
- Claves por variables de entorno del servidor, nunca en el repo. `data/` se copia una vez
  y se respalda a diario.
- WhatsApp: se RE-VINCULA en el servidor con el código de emparejamiento de 8 dígitos
  (ya soportado por `wa.js`). No se copia `data/whatsapp/session/` (frágil, y una sesión
  de Baileys no puede vivir en dos sitios a la vez). Se apaga el servicio en el PC.
  Recomendado: número secundario (Baileys es no oficial, riesgo de baneo ya asumido).

## Qué se queda fuera del servidor
Música (VLC), control del PC (apagar/bloquear/volumen/ventanas/apps/portapapeles/capturas),
Control Total, Modo Seguro (cámara), modos de pantalla (code/redes/calendario), canción de
arranque y escucha por palmadas, concentración, navegador visible (`buscar_en_google`,
`navegador_*`). Se quedan `navegar_leer` y Tavily para buscar.

## Hallazgos del diagnóstico (fase A)
1. `tools/__init__.py` importa TODOS los módulos en cabecera (VLC, pycaw/ctypes, pynput,
   cv2/mediapipe...). En Linux sin pantalla alguno puede fallar al importar aunque la tool
   no se use. Hay que MEDIR cuáles fallan y proteger solo esos.
2. Todo el arranque vive en `hud/__main__.py::main()`: worker, demonio, vigilante de
   WhatsApp, métricas, HTTP, y además Chrome (`chrome.exe`), kill-switch (pynput) y cámara.
3. `whatsapp_client.py` ya es portable (`os.name == "nt"`). No se toca.
4. `requirements.txt` mezcla lo del servidor con lo solo-Windows (pycaw, comtypes, pywebview,
   python-vlc, pynput, opencv, mediapipe, onnxruntime).
5. CLAUDE.md está desactualizado: dice que Modo Seguro no existe, pero ya está implementado.

## Fases (una por sesión; no avanzar sin prueba verde)

### Fase A — Modo servidor (en local) — HECHA (2026-10-08)
Resultado (simulado en Windows: venv limpio con solo `requirements-servidor.txt` + finder
que bloquea pynput/vlc/pycaw/comtypes/cv2/mediapipe/onnxruntime/webview/mss/pyperclip/
sounddevice; no había WSL ni Docker):
- Import completo sin ningún import de escritorio intentado → el punto 3 quedó en CERO
  cambios (todo lo de escritorio ya era perezoso).
- `TOOLS` 161 → 94 en modo servidor; siguen memoria, calendario, WhatsApp, Tavily.
  `_SOLO_ESCRITORIO` revienta al importar si nombra una tool que ya no existe.
- `/enviar` respondió ("…operando en modo servidor, señor"): el bloque `# MODO SERVIDOR`
  del prompt evita que intente tools de escritorio.
- Confirmaciones §9 en modo servidor → WhatsApp "Note to self" (decidido en esta sesión).
  Probado el camino negativo: sin sesión vinculada, cancela y lo dice en el log. **Falta
  el positivo** (llega la pregunta, contestas sí/no): WhatsApp estaba en `qr_required`.
- Modo normal en Windows: arranca igual (HUD, métricas, 161 tools).

Pendiente para la B, visto aquí: zona horaria del VPS (`TZ=Europe/Madrid`, o el bloque
`# AHORA` y los eventos salen en UTC) y probar el gate positivo por WhatsApp.

Cambios mínimos:
1. Interruptor `CRISTOPHER_MODO=servidor` leído en `config.py`.
2. Filtro de `TOOLS` justo antes de crear `_BY_NAME`.
3. Imports de escritorio protegidos: solo los que fallen de verdad en Linux.
4. `main()` en modo servidor omite Chrome, kill-switch, hilo de cámara y métricas; mantiene
   worker, demonio proactivo, vigilante de WhatsApp, precalentado de voz y HTTP en 127.0.0.1.
5. `requirements-servidor.txt` con solo lo necesario.

Prueba de aceptación:
- En Linux (lo pruebo yo en la máquina Linux de la sesión; el usuario no instala nada) con `CRISTOPHER_MODO=servidor`,
  `python -m cristopher` arranca sin `ImportError`.
- `TOOLS` no contiene ninguna tool de escritorio y sí memoria, calendario, WhatsApp y búsqueda.
- Un mensaje por `/enviar` (curl) recibe respuesta del agente.
- El modo normal en Windows sigue arrancando igual.

### Fase B — VPS + WhatsApp
Contratar VPS, desplegar con systemd (el asistente guía paso a paso, sin Docker ni WSL en el PC), copiar `data/` y variables de entorno, re-vincular
WhatsApp con código de 8 dígitos, apagar el servicio en el PC, respaldo diario de `data/`.
Prueba: con el PC apagado, `/Cristopher ¿qué tengo mañana?` desde el móvil contesta.

### Fase C — Voz de ida y vuelta por WhatsApp
Hoy la nota de voz se entiende pero la respuesta sale en texto. Añadir envío de nota de voz:
TTS (Piper o ElevenLabs) -> ffmpeg a ogg/opus -> `wa.js` lo manda como audio.
Si el mensaje entró por voz, la respuesta sale por voz.
Prueba: mandas un audio y recibes un audio.

### Fase D — Pulsera (solo diseño por ahora)
- Endpoint autenticado `POST /voz` en el servidor: recibe audio, devuelve audio.
- App web mínima en el móvil como puente: recibe el audio de la pulsera por Bluetooth,
  lo manda al servidor y reproduce la respuesta en los auriculares.
- Hardware: ESP32 + micro I2S (INMP441) + botón. Auriculares TWS o de conducción ósea.
- Antes de comprar nada, probar el pulsador con un mando Bluetooth barato (tipo selfie).
Prueba: pulsas, hablas y oyes la respuesta.

## Decisiones
1. **Rama git:** DECIDIDO — directo en `main`.
2. **Confirmaciones sin HUD (§9):** DECIDIDO — en modo servidor van al WhatsApp "Note to
   self" (`hud/__main__.py::_confirmadores_por_defecto`); sin WhatsApp, silencio = no.
3. **(Pendiente) ¿Seguirá encendido el PC alguna vez?** Si sí, se puede dejar un puente opcional para
   tareas de escritorio. Si no, se pierden música, Control Total y control de apps.

## Riesgos asumidos
- Baileys no oficial: posible baneo/limitación de Meta (más expuesto en un servidor).
- Cuota del free tier de Gemini: un agente 24/7 la gasta; hay que vigilarla.
- Latencia de voz (STT + modelo + TTS): 2-4 s; mejorar con respuestas cortas y streaming.
- Batería y tamaño de la pulsera: lo más difícil del hardware.
