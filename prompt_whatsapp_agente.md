# Tarea: Añadir WhatsApp como tool del agente personal

## Contexto
Tengo un agente personal ya en funcionamiento (revisa el código del proyecto antes de
empezar para entender en qué lenguaje/runtime está y cómo registra sus tools hoy —
sigue ese mismo patrón, no inventes uno nuevo).

Quiero añadirle la capacidad de leer y enviar mensajes de mi WhatsApp **personal**
(mi número real, mis chats actuales — no un número de negocio nuevo).

## Idea central (importante, no lo cambies)
Esto NO es un bot que responde solo. WhatsApp es una **herramienta más que el agente
puede usar cuando yo se lo pido**, igual que cualquier otra tool suya.

Flujo real de uso:
1. El agente detecta mensajes nuevos y me avisa por voz/texto (ej: "te han llegado
   2 mensajes de X, ¿quieres oírlos?").
2. Yo decido si quiero que me los lea.
3. Si quiero responder, se lo digo explícitamente ("respóndele que sí puedo el jueves")
   y solo ahí el agente envía el mensaje.

No hay aprobación por botones, no hay panel externo, no hay lógica de auto-respuesta.
El control ya lo da el propio uso conversacional del agente.

## Qué construir

Usa **Baileys** (librería Node.js no oficial para WhatsApp Web) para la conexión.
Es la opción más simple porque no depende de Puppeteer/Chrome, solo de una conexión
WebSocket, y hoy es la mejor mantenida de las no oficiales.

Encapsula toda la integración en un módulo dedicado (ej. `whatsapp/client.js` o
equivalente según la estructura del proyecto) que exponga estas tres funciones
como tools del agente:

- `whatsapp_check_new()` — devuelve mensajes nuevos desde la última revisión
  (remitente, preview corto, timestamp, chat_id). Esto es lo que dispara el aviso
  proactivo del agente.
- `whatsapp_read(chat_id, n=10)` — devuelve el texto completo de los últimos N
  mensajes de un chat, para que el agente me los pueda leer/resumir.
- `whatsapp_send(chat_id, texto)` — envía un mensaje. Se llama SOLO cuando yo lo pido
  explícitamente en la conversación con el agente. No debe existir ningún camino de
  código que llame a esta función sin una instrucción directa mía.

## Requisitos técnicos
- Sesión persistente: escaneo el QR una sola vez, la sesión se guarda en disco
  y sobrevive reinicios del proceso.
- Manejo explícito de errores (nada de fallos silenciosos):
  - Sesión desconectada o QR expirado → avisar claramente, no reintentar en bucle infinito.
  - Fallo al enviar/leer → devolver error legible al agente, no crashear el proceso.
- Sin dashboard web, sin bot de Telegram, sin ninguna capa de aprobación externa —
  eso ya se descartó, mantén la solución con el mínimo de piezas móviles.
- Documenta en el propio código, en una línea, qué hace el módulo y su riesgo conocido:
  Baileys es una librería no oficial; usarla implica riesgo de que Meta limite o
  banee el número. Esto es una decisión ya tomada por mí, no hace falta que lo
  cuestiones, solo que quede anotado.

## Antes de programar
Sigue el protocolo habitual: revisa cómo está armado el proyecto hoy, y si hay más
de una forma razonable de engancharlo al sistema de tools existente, propón
brevemente 2-3 enfoques (con tu recomendación) antes de escribir código.
