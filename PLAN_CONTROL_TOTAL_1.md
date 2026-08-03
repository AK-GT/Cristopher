# PLAN — Modo Control 100% de CRISTOPHER

Diseño por fases, mismo patrón que `PLAN_CRISTOPHER_CODE.md` / `PLAN_GESTOR_SESIONES.md`:
una fase = una sesión de Claude Code, con criterio de aceptación cerrado antes de tocar
código. Dentro de cada fase, Claude Code hará su propio desglose de pasos — aquí solo se
fija el contrato de qué debe quedar hecho y por qué en ese orden. No asumas que nada de
esto existe ya salvo lo que se cita explícitamente de `cristopher/`.

## 0. Qué es y qué NO es

"Control 100%" es un **modo**, no una herramienta suelta: mientras está activo,
CRISTOPHER deja de esperar una orden por acción y pasa a **operar el ordenador de forma
autónoma y continua** hacia un objetivo que le has dado — mueve el ratón, escribe, hace
clic, lee lo que aparece en pantalla (visión) y decide el siguiente paso él solo, en
bucle, hasta terminar la tarea o toparse con algo que exige tu confirmación.

Dos señales visuales/interactivas obligatorias mientras está activo:

1. **Borde de pantalla teñido de azul** — indicador ambiental de "estoy al mando",
   visible aunque tengas cualquier ventana en primer plano.
2. **Widget flotante independiente** — ventana propia, pequeña, movible, siempre
   encima, para escribirle si no quieres hablar en voz alta, y por la que él te
   contesta cuando no está en modo voz. Es la MISMA conversación y el mismo
   CRISTOPHER que el HUD principal (mismo backend, mismo buffer, mismo `agent.py`),
   no un agente aparte — el widget es una *extensión* del HUD, con su propia ventana
   de sistema operativo para que sobreviva aunque cierres el HUD.

Lo que este modo **no** cambia: las reglas de `CLAUDE.md` siguen aplicando tal cual —
nunca credenciales/contraseñas en formularios, confirmación explícita antes de
acciones irreversibles o con efecto real (enviar, comprar, borrar en permanente,
cambiar configuración/permisos), y el contenido que lee en pantalla es **dato**, no
instrucción, aunque parezca una orden. Aquí se hace explícito porque el bucle actúa
sin que tú mires cada paso.

Activación: **por intención** (voz/chat: "toma el control", "modo control total", "ya
está, suéltalo") **+ un atajo de teclado físico siempre activo** como hombre muerto —
funciona aunque CRISTOPHER esté a mitad de una acción, aunque el foco esté en otra
ventana, y aunque el bucle de percepción esté "pensando" un paso por detrás.

---

## Arquitectura (visión de conjunto)

```
cristopher/
  control_total/
    __init__.py
    driver.py            # Fase 1 — primitivas de bajo nivel: mover, clicar, teclear, scroll
    killswitch.py         # Fase 1 — listener global de la tecla de emergencia (hilo propio)
    bucle.py               # Fase 2 — percepción→decisión→acción, límites, guardarraíles
    guardarraíles.py       # Fase 2 — bloqueos duros (credenciales, pagos, CAPTCHAs...)
  hud/
    control_overlay/       # Fase 3 — ventana transparente click-through, borde azul
    widget_flotante/        # Fase 3 — ventana propia (pywebview), mismo backend del HUD
  estado_control_total.py   # Fase 4 — flag + bus, gemelo de estado_code.py
  tools/
    control_total_tools.py  # Fase 4 — activar_control_total / desactivar_control_total
```

Reutiliza sin reescribir: `vision.py` (interpretar capturas), el patrón de
`estado_code.py` + `bus.py` + `data-modo` en CSS (activar/desactivar modo, teñir HUD),
y el criterio de "acción con efectos → confirmación" ya repartido por
`control_pc_tools.py` / `archivo_tools.py`.

---

## Fase 1 — Cimientos: driver de bajo nivel + kill-switch + contrato de seguridad

Antes de escribir código, fijar en un párrafo (no hace falta doc aparte, basta con el
mensaje inicial a Claude Code) el criterio de control: **regla general, cualquier
acción con efecto real (enviar, publicar, comprar, borrar, cambiar configuración) para
y pide tu confirmación explícita — confirmada, adelante, sin excepción de "esto es
demasiado" salvo las dos de abajo.**

Dos casos NO se resuelven con "confirmar y que lo haga él", se resuelven con **Cristopher
como ejecutor de lo que tú dictas**, no como quien decide — importante: el control se
hace por voz (accesibilidad — el usuario no maneja teclado/ratón físicamente), así que
la cesión no puede ser "coge tú el teclado", tiene que ser "dime tú qué hacer y lo
ejecuto exacto":

- **CAPTCHA en pantalla** → el bucle detecta que hay uno (visión), para, y te lo
  describe por voz/widget en detalle suficiente para que TÚ lo resuelvas ("veo 9
  casillas en cuadrícula, marca las que tengan un semáforo" / "el texto parece decir
  X7QK, ¿lo confirmas?" — pero el que lee y decide la respuesta es él describiendo, no
  reconociendo-para-decidir). Tú dictas la respuesta/qué casillas, él ejecuta
  exactamente eso. Si el sitio ofrece alternativa de audio CAPTCHA (pensada para
  accesibilidad), mejor: se la reproduce o transcribe, tú dictas el código. Lo que el
  driver NUNCA hace es reconocer la imagen y decidir la respuesta por su cuenta,
  confirmada o no — eso sigue siendo la pieza de evasión, decida quien decida
  apretar el botón de confirmar.
- **Campo de contraseña/credencial detectado** → no se dicta por voz (pasaría por
  transcripción + captura a Gemini + log, sin necesidad). Usa un gestor de
  contraseñas con autocompletado (1Password, Bitwarden...): un comando de voz corto
  ("rellena la contraseña") dispara el atajo de teclado del gestor, que mete la
  credencial directo en el campo sin que pase por Cristopher en ningún momento. Esto
  hay que dejarlo como primitiva propia del driver — un atajo de teclado con nombre,
  no "escribir texto".

Duración/alcance por defecto de una sesión de control (límite de tiempo/iteraciones,
como `SUBAGENT_TIMEOUT`) se fija también aquí. Esto condiciona cómo se escribe todo lo
demás, por eso va en esta fase y no como documento separado que nadie relee.

Con eso claro, en la misma fase:

- `control_total/driver.py`: primitivas mínimas y auditables — `mover_raton`, `clic`,
  `escribir`, `pulsar_tecla`, `scroll`. Cada llamada queda registrada (qué, dónde,
  cuándo), mismo estilo que el transcript de sesiones de código. Windows primero
  (`pyautogui` o `pynput`, decidir según se lleve mejor con multi-monitor).
- `control_total/killswitch.py`: hilo propio, independiente del bucle principal,
  escuchando un atajo global (p. ej. `Ctrl+Alt+Esc`). Al saltar, pone a cero un flag
  compartido que el driver consulta antes de CADA primitiva — no solo al principio del
  bucle. Es el hombre muerto real: no depende de que el bucle esté en un punto cómodo
  para parar.

Criterio de aceptación: con CRISTOPHER moviendo el ratón en un bucle de prueba,
pulsar el atajo lo detiene en menos de un frame de acción, sin matar el proceso.

---

## Fase 2 — El bucle: percepción → decisión → acción, con guardarraíles

`control_total/bucle.py`: reutiliza `capturar_pantalla`/`capturar_ventana_activa`
para "ver", pide al modelo el siguiente paso concreto (una acción del driver, no un
plan largo), la ejecuta, vuelve a capturar, repite — respetando el límite de
iteraciones/tiempo de la Fase 1.

`control_total/guardarraíles.py` nace en esta misma fase, no como parche después: capa
que el bucle consulta antes de ejecutar cualquier acción, con dos comportamientos
distintos según el caso —

- **Confirmar y seguir**: acciones con efecto real (enviar, publicar, comprar,
  borrar, cambiar configuración) paran el bucle y esperan tu OK explícito por el
  widget; confirmadas, se ejecutan. Es un contrato de código, no una instrucción de
  prompt más — el bucle no puede saltárselo aunque el modelo "decida" que no hace
  falta preguntar.
- **Describir y ejecutar lo dictado** (solo credenciales/CAPTCHA, ver Fase 1): el
  bucle detecta el caso, te describe lo que ve (o dispara el atajo del gestor de
  contraseñas), y ejecuta exactamente lo que dictas — nunca reconoce/decide la
  respuesta del CAPTCHA por su cuenta, ni escribe una contraseña dictada por voz.

Aquí también vive el log de auditoría de toda la sesión de control (qué hizo, en qué
app, con qué resultado) — con los huecos correspondientes en blanco para lo que
escribiste tú a mano.

Decisión de arquitectura a resolver en esta fase: el bucle vive como herramienta no
bloqueante del `agent.py` principal (mismo patrón que `sesion_crear` para modo
agentes) — necesario porque el widget de la Fase 3 tiene que poder mostrar "en qué
está" en tiempo real.

Criterio de aceptación: una tarea de varios pasos guiada solo por pantalla se
completa o para a pedir confirmación en el punto correcto; llevado a un formulario de
pago de prueba, para y espera tu OK antes de enviarlo; llevado a un CAPTCHA, lo
describe y ejecuta lo que dictas en vez de resolverlo por su cuenta; llevado a un
campo de contraseña, dispara el atajo del gestor en vez de escribirla él.

---

## Fase 3 — La cara visible: widget flotante + borde azul

**Widget** (`pywebview` apuntando a una ruta nueva del mismo servidor Flask que sirve
`hud/static/`): mismo backend/SSE/`/enviar` que el HUD grande — misma conversación,
cero estado duplicado. Se abre/trae al frente al activar el modo; sigue viva aunque
cierres la ventana del HUD principal.

Estética — dedicarle atención real dentro de la fase, no dejarla para el final:
reutiliza los tokens ya existentes en `style.css` (`--cyan`/`--cyan-bright`/
`--cyan-glow`, tipografías, estilo panel con `bracket`/glow) en su variante azul,
mismo patrón que el modo code ya sobreescribe a rojo. Fondo semitransparente con
`backdrop-filter` para combinarse con lo que hay detrás en vez de taparlo. Contenido
mínimo: log corto + input de texto + un núcleo/orbe en miniatura (`core-canvas`
reducido) como indicador vivo de estado. Movible libremente por drag nativo de
ventana, recuerda posición entre sesiones.

**Overlay del borde** (ventana transparente, sin bordes, click-through vía
`WS_EX_LAYERED | WS_EX_TRANSPARENT`): degradado azul en los bordes de cada monitor,
brillo/pulso reflejando el estado real (reposo tenue / más vivo actuando), mismo
patrón de interpolación que `TARGETS` en `app.js` — no CSS estático.

Criterio de aceptación: cierras el HUD grande, el widget sigue vivo con la misma
memoria/contexto; el borde aparece sin robar clics a ninguna ventana y se apaga
limpio al desactivar o al pulsar el kill-switch.

---

## Fase 4 — Integración, estado compartido y pulido end-to-end

`estado_control_total.py` + `tools/control_total_tools.py`: gemelo del patrón
`estado_code.py`/`code_tools.py` — activar/desactivar por intención del modelo (con
más cautela que modo code: "si no está claro, pregunta antes de activarlo"),
publicando al `bus` para que el núcleo del HUD principal se tiña de azul (igual que ya
hace de rojo en modo code), y para que overlay + widget se muestren/oculten juntos. El
kill-switch, aunque viva en su propio hilo (Fase 1), también dispara
`desactivar_control_total()` para que el estado quede coherente en las tres piezas.

Cierre de la fase con pruebas end-to-end reales fuera del navegador (organizar
ventanas, rellenar un documento local, seguir instrucciones de una captura), afinar
latencia ver→pensar→actuar, y que el widget narre en lenguaje natural lo que va
haciendo (mismo espíritu que la Fase 9 de voz: "habla mientras trabaja").

Criterio de aceptación: activar/desactivar desde chat, desde el widget y desde el
atajo físico dejan siempre el mismo estado consistente en HUD + overlay + widget; una
tarea real de principio a fin se completa con narración legible de lo que hizo.

---

## Notas para quien ejecute esto en Claude Code

- Una fase por sesión. Claude Code desglosa los pasos dentro de cada una — este
  documento fija el contrato de salida, no la lista de tareas internas.
- El contrato de seguridad de la Fase 1 no es ceremonial: es lo que evita que la
  Fase 2 se escriba sin límites claros. Revísalo tú antes de que se toque
  `driver.py`, aunque vaya dentro de la misma sesión.
- Multi-monitor, DPI scaling y permisos de Windows (UAC en apps que lo pidan) son el
  terreno más probable de sorpresas — dejar margen en Fases 1 y 3.
