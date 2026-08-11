# AJUSTES POR HACER

Pendientes reales encontrados en la auditoría del 2026-08-01: se probaron en vivo las
33 herramientas de solo-lectura/consulta del registro (`TOOLS`, 125 en total) y todas
corren sin errores de código. Estos son los huecos de configuración/instalación que
quedaron al descubierto — no son bugs, son setup pendiente.

## 1. Google Calendar/Gmail (Fase 4)

Falta `data/google/credentials.json`. Sin él, `proximo_evento`, `buscar_correos`,
`crear_evento`, `enviar_correo`, `responder_correo`, `marcar_leido` no funcionan
(fallan con "Falta el archivo de credenciales de Google").

- Descargar credenciales OAuth desde Google Cloud Console (ver `google_auth.py` /
  `login_google.py` para el flujo exacto).
- Colocar en `data/google/credentials.json`.
- Primer uso disparará el consentimiento OAuth y generará `data/google/token.json`.

## 2. Navegador (Playwright, Fase 5)

Chromium no está instalado — `playwright install chromium` nunca se corrió en este
venv (`C:\Users\Usuario\AppData\Local\ms-playwright` no existe). Rompe `navegar_leer`,
`buscar_en_google`, `navegador_ir/click/scroll/leer/captura/cerrar` enteros.

- Correr: `playwright install chromium` (dentro del venv activado).

## 3. WhatsApp (Fase 9, opcional)

Node.js no está instalado en el sistema (`node` no está en el PATH), y
`whatsapp/node_modules` tampoco existe — la integración entera sigue sin montar, tal
como ya avisaba el CLAUDE.md ("pendiente de verificación con Node.js real").

- Instalar Node.js LTS.
- `cd whatsapp && npm install`
- `node setup_qr.js` (una sola vez, para vincular la sesión).

## Otros detalles menores observados

- **faster-whisper (STT)**: no hay caché de modelo en `data/voice/whisper` — se
  descargará solo en el primer uso real (necesita red en ese momento). Piper (TTS) sí
  está listo (4 voces instaladas, confirmado con `voz_listar_voces`).
- **Tavily**: key presente y funcionando (`busqueda_elite` confirmado en vivo contra
  Tavily de verdad).
- **VLC / música / canción de arranque**: verificado funcionando de punta a punta en
  la sesión anterior (instalación de VLC, autoarranque por palmadas, tool
  `cancion_arranque_cambiar`).

## No probado en esta auditoría (a propósito)

Todo lo que altera datos, controla el PC o envía algo — se dejó sin ejecutar por
instrucción explícita de no alterar nada sin confirmación: herramientas de
crear/borrar (notas, tareas, recordatorios, personalidad, episodios, diario), control
de música real (reproducir/volumen/colas/listas), activar/desactivar modos
(voz/diario/code), control de PC (`apagar`, `reiniciar`, `suspender`, `bloquear_pc`,
`gestionar_ventana`, `abrir_app`/`cerrar_app`, `volumen_sistema`, `organizar_carpeta`),
envío de algo (`enviar_correo`, `responder_correo`, `whatsapp_send`, `marcar_leido`,
`crear_evento`), lanzamiento de procesos reales de trabajo (`sesion_crear`,
`delegar_a_claude`, `analizar_proyecto`, `auto_arreglo_lanzar_sesion`), y capturas de
pantalla (`capturar_pantalla`/`capturar_ventana_activa` — su propia descripción exige
pedirlo explícitamente, porque manda la imagen a la nube).
