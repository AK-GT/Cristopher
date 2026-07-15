# Prompts para Claude Code — huecos funcionales de CRISTOPHER

Cada prompt es autocontenido: pégalo directo en Claude Code dentro del repo (`C:\Users\Usuario\Desktop\Cristopher V1`). Uno por sesión — el propio CLAUDE.md del proyecto pide "una fase/tarea por sesión, no avances sin prueba de aceptación verde".

---

## 1. Olvidar / corregir hechos de memoria

```
Lee CLAUDE.md antes de nada y respeta su modo de trabajo (MVP, cambios mínimos,
enséñame el plan antes de tocar código, fallos explícitos).

Contexto: cristopher/memory.py define la clase Memory (SQLite en data/memory.db,
tabla `facts` con id/text/embedding/created_at) con remember() y recall(). Se expone
al modelo vía cristopher/tools/memory_tools.py (funciones remember/recall) y se
registra en cristopher/tools/__init__.py (lista TOOLS: cada entrada tiene name,
description, parameters JSON-Schema y fn).

Problema: no existe forma de borrar o corregir un hecho guardado. Si el usuario dice
algo que luego cambia, el hecho viejo se queda para siempre y puede salir en el
recall junto (o en vez de) el correcto.

Tarea: añade la capacidad de olvidar un hecho.
1. En memory.py, un método Memory.olvidar(fragmento: str) -> list[str] que borre de
   `facts` las filas cuyo texto contenga `fragmento` (case-insensitive, como ya hace
   quitar_tema en briefing.py — sigue ese mismo patrón de "borra por coincidencia de
   subcadena y te digo qué borré"). Debe devolver los textos borrados para poder
   confirmarlo al usuario.
2. Una tool nueva olvidar_hecho(fragmento: str) en memory_tools.py que la envuelva,
   con manejo de "no encontré nada que borrar" explícito (nunca fingir éxito).
3. Regístrala en TOOLS (tools/__init__.py) siguiendo el mismo formato que remember/
   recall — mira cómo están definidas esas dos como referencia de estilo y de
   parameters.
4. No toques memory.py más allá de lo necesario: no añadas versionado de hechos ni
   historial de ediciones, eso no lo ha pedido nadie (evita arquitectura por si acaso).

Antes de escribir código, enséñame en 3-4 líneas el plan (qué archivos tocas y por
qué) y espera mi OK.

Prueba de aceptación: desde el REPL (python -m cristopher.main), guardo un hecho
("recuerda que odio el cilantro"), le pido olvidarlo ("olvida lo del cilantro"),
compruebo que recall() ya no lo devuelve y que la tabla facts en data/memory.db
efectivamente perdió esa fila.
```

---

## 2. Crear eventos en el calendario (Google Calendar)

```
Lee CLAUDE.md antes de nada y respeta su modo de trabajo (MVP, cambios mínimos,
enséñame el plan antes de tocar código). Especial atención a la sección de Seguridad:
las acciones irreversibles o con efectos necesitan confirmación explícita del
usuario, igual que ya hace enviar_correo.

Contexto: cristopher/config.py define GOOGLE_SCOPES con calendar.readonly,
gmail.readonly y gmail.send — el calendario hoy es SOLO lectura. cristopher/
google_auth.py expone get_calendar()/get_gmail() (clientes autenticados vía OAuth,
token en data/google/token.json). cristopher/tools/google_tools.py tiene
proximo_evento() (lectura) y enviar_correo() (escritura GATEADA: usa un _confirm
callable inyectable vía set_confirmer(), con _default_confirm() pidiendo [s/N] por
consola — ese es el patrón de confirmación a seguir).

Problema: no hay forma de que CRISTOPHER cree un evento de calendario. Solo puede
leer y contarte lo que hay.

Tarea:
1. Añade el scope de escritura de calendario (calendar.events o calendar completo,
   el mínimo necesario) a GOOGLE_SCOPES en config.py. Esto invalida el token
   guardado — dime en tu plan que habrá que borrar data/google/token.json y volver
   a autenticar (login_google.py) para que el usuario lo sepa, no lo hagas tú solo.
2. En google_tools.py, una función crear_evento(titulo, inicio, fin, descripcion="",
   ubicacion="") -> str que:
   - Pida confirmación con el mismo mecanismo _confirm que enviar_correo (mostrando
     el borrador del evento antes de crear nada).
   - Si se confirma, llame a svc.events().insert(...) sobre get_calendar().
   - Maneje GoogleAuthError y excepciones generales igual que el resto del archivo
     (mensajes de error explícitos, nunca silencio).
   - Acepta inicio/fin en ISO 8601 (igual de simple que crear_recordatorio en
     tools/recordatorio_tools.py: no reinventes parsing de fechas natural, delega esa
     interpretación al propio modelo que llama a la tool con la hora ya resuelta).
3. Regístrala en TOOLS (tools/__init__.py), descripción clara de qué formato de
   fecha espera.

Antes de escribir código, enséñame el plan y espera mi OK — en particular confírmame
que estás de acuerdo en tocar el scope OAuth antes de hacerlo, porque obliga a
reautenticar.

Prueba de aceptación: desde el REPL, pido crear un evento de prueba mañana a las
10:00, confirmo cuando me lo pregunta, y lo veo aparecer en mi Google Calendar real.
Pruebo también que si respondo "no" a la confirmación, NO se crea nada.
```

---

## 3. Responder correos y marcarlos como leídos

```
Lee CLAUDE.md antes de nada y respeta su modo de trabajo (MVP, cambios mínimos,
enséñame el plan antes de tocar código, confirmación antes de acciones irreversibles).

Contexto: cristopher/tools/google_tools.py tiene buscar_correos() (lectura) y
enviar_correo() (escritura gateada por confirmación, patrón _confirm/set_confirmer
ya explicado arriba). buscar_correos() devuelve texto formateado con Asunto/De/
Fecha/snippet pero NO devuelve el id ni el threadId del mensaje — eso hay que
añadirlo para poder responder al mensaje correcto.

Problema: solo se puede enviar correo nuevo, no responder dentro de un hilo
existente (hay que reescribir asunto y destinatario a mano), ni marcar un correo
como leído/archivarlo.

Tarea, en dos partes:
1. Modifica buscar_correos() para que cada resultado incluya el id del mensaje
   (formato "[id:XXXXX]" al principio de cada bloque, o similar — decide el formato
   más simple que el modelo pueda parsear y volver a pasarte). No cambies la firma
   de la función ni rompas cómo se usa desde briefing.py (_seccion_correos la llama).
2. Añade responder_correo(message_id: str, body: str) -> str que:
   - Lea el mensaje original (svc.users().messages().get) para sacar threadId,
     Subject y From, y arme el "Re: " + reply-to automáticamente (no le pidas al
     modelo que adivine el asunto).
   - Pida confirmación con el mismo mecanismo que enviar_correo, mostrando el
     borrador completo.
   - Envíe con threadId para que quede en el mismo hilo (svc.users().messages().send
     con "threadId" en el body, igual que enviar_correo pero añadiendo ese campo).
3. Añade marcar_leido(message_id: str) -> str, una llamada directa a
   svc.users().messages().modify(removeLabelIds=["UNREAD"]) — esta SÍ es reversible
   y de bajo riesgo, no necesita confirmación (sé explícito en el docstring de la
   tool sobre por qué no la pide, para que quede documentado el criterio).
4. Registra ambas en TOOLS (tools/__init__.py).

No añadas archivar/borrar en esta tarea — eso es otro hueco, no lo mezcles aquí
(una tarea por sesión).

Antes de escribir código, enséñame el plan y espera mi OK.

Prueba de aceptación: busco un correo real, le pido responder con un texto de
prueba, confirmo, y compruebo en Gmail que la respuesta quedó en el mismo hilo con
el asunto correcto. Pruebo también marcar_leido sobre un correo no leído y confirmo
que desaparece de is:unread.
```

---

## 4. Borrar / editar recordatorios

```
Lee CLAUDE.md antes de nada y respeta su modo de trabajo (MVP, cambios mínimos,
enséñame el plan antes de tocar código).

Contexto: cristopher/recordatorios.py es el almacén SQLite (data/proactivo.db) que
usa get_recordatorios(); ya tiene crear(), listar(), pendientes(), ya_visto(),
marcar_visto(), marcar_hecho() (revísalo para ver el patrón de acceso a la DB:
conexión + lock, igual que memory.py). cristopher/tools/recordatorio_tools.py
expone crear_recordatorio() y listar_recordatorios() al modelo, con el parser
_parse_cuando() que interpreta 'HH:MM', 'en N minutos/horas' o ISO.

Problema: un recordatorio creado con la hora o el texto equivocado no se puede
corregir ni borrar — solo queda ahí hasta que se dispara solo.

Tarea:
1. En recordatorios.py, añade un método borrar(rid: int) -> bool que elimine la fila
   por id (DELETE FROM ... WHERE id = ?) y devuelva si borró algo o no (para poder
   decir "no encontré ese recordatorio" con honestidad).
2. Decide si editar es necesario o si "borrar + crear de nuevo" ya cubre el caso de
   uso real (mantén el criterio MVP: no construyas un update() si borrar+crear
   resuelve lo mismo con menos piezas) — pero dime tu razonamiento en el plan antes
   de decidir, no lo des por hecho tú solo.
3. Añade borrar_recordatorio(rid: int) -> str en recordatorio_tools.py que llame a
   ese método y devuelva confirmación clara (qué recordatorio se borró, con su
   texto, no solo "OK").
4. Regístrala en TOOLS (tools/__init__.py).

Esta es de bajo riesgo (no toca APIs externas ni acciones irreversibles fuera del
propio sistema) — no hace falta gate de confirmación tipo enviar_correo, basta con
que la tool sea explícita sobre qué borró.

Antes de escribir código, enséñame el plan (en particular tu decisión sobre editar
vs. borrar+crear) y espera mi OK.

Prueba de aceptación: creo un recordatorio de prueba, lo listo, pido borrarlo por su
número o texto, lo vuelvo a listar y confirmo que ya no aparece (ni siquiera como
pendiente ni como hecho).
```

---

## 5. Canal remoto para avisos urgentes

```
Lee CLAUDE.md antes de nada y respeta su modo de trabajo (MVP, cambios mínimos,
enséñame el plan antes de tocar código — y en este caso especialmente: propón
alternativas y espera mi decisión antes de elegir proveedor/dependencia, porque esto
sí añade una integración nueva).

Contexto: cristopher/proactivo.py tiene la clase Demonio, que clasifica cada aviso
en nivel 1/2/3 y lo entrega en _entregar(): nivel 3 imprime en terminal Y habla por
TTS (voz.hablar, llamado dos veces). Si el usuario no está delante del PC ni
escuchando, ese aviso "urgente" se pierde sin más recurso.

Problema: no hay ningún canal de aviso que llegue al usuario si está fuera de la
máquina donde corre CRISTOPHER.

Antes de tocar nada: PROPÓN 2-3 opciones concretas (no las seleccione yo, dame tu
recomendación con el porqué, siguiendo el protocolo "propón antes de ejecutar"):
- Opción simple: un bot de Telegram (API gratuita, sin servidor propio, solo un
  token + chat_id) — probablemente la más barata en piezas nuevas.
- Opción con lo que ya hay: correo (ya existe enviar_correo() y las credenciales
  Google) — cero dependencias nuevas, pero un correo es peor canal para "urgente
  ya" que una notificación push.
- Cualquier otra que se te ocurra que respete "gratis siempre que se pueda" (regla
  de CLAUDE.md) y no añada un servidor/infraestructura que haya que mantener.

Con mi OK sobre la opción elegida:
1. Añade la config necesaria en config.py (siguiendo el patrón de TAVILY_API_KEY:
   variable de entorno opcional, degrada con elegancia si no está puesta — con
   comentario explicando qué pasa sin ella).
2. Encapsula el envío en una función dedicada y ENCAPSULADA (p. ej.
   notificar_remoto.py, nuevo módulo, no lo metas dentro de proactivo.py) que reciba
   un texto y lo mande por el canal elegido, con manejo de error explícito si falla
   (nunca debe tumbar el demonio — mismo principio que _entregar() ya sigue con
   try/except alrededor de hablar()).
3. En Demonio._entregar(), para nivel 3 únicamente, llama a esa función además de lo
   que ya hace (terminal + voz) — no toques el comportamiento de nivel 1 y 2.
4. NO lo conviertas en tool del registro TOOLS — esto es una acción del demonio en
   background, no algo que el modelo decida invocar a mitad de conversación (no
   mezcles los dos sistemas).

Prueba de aceptación: fuerzo un aviso de nivel 3 (con el clasificar_fn inyectable
del Demonio, como ya se hace en tests/verificación manual de la Fase 7) y confirmo
que me llega la notificación en el canal elegido, con el demonio siguiendo vivo si
el envío falla (por ejemplo, con la key/token puestos mal a propósito).
```