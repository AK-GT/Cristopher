# MODO SEGURO — plan de construcción para Claude Code

> Prompt de tarea sobre este repo, misma convención que
> `PROMPT_modo_concentracion_para_claude_code.md` (ya borrado al implementarse).
> **Tres fases. Se construye una, se para, y el usuario la verifica delante de la webcam
> antes de seguir.** No encadenes fases.
>
> Escrito **después** de tener un prototipo funcionando contra una webcam real
> (`prototipo_seguro.py`, en la raíz de este repo). Lo que aquí se da por decidido está
> validado, no supuesto.

---

## 1. Qué se construye

Un modo que el usuario activa bajo petición: CRISTOPHER vigila la webcam y, si detecta a
alguien **no autorizado mirando la pantalla**, bloquea el portátil con un aviso propio
(cara enfadada) y pide una contraseña.

**Qué NO es:** no es videovigilancia, no graba vídeo, no se activa solo, y la cara **nunca
desbloquea** — solo silencia la alarma.

> **Regla de oro.** La galería es una **lista blanca**: quien está dentro puede mirar; todo
> lo demás dispara. Ante la duda persistente: desconocido.

---

## 2. Punto de partida: hay un prototipo que ya funciona

`prototipo_seguro.py` (raíz del repo) resuelve la parte difícil: detección, pose, mirada,
tracking, embeddings y galería, todo verificado con una webcam de verdad.

**No lo reescribas.** Es la implementación de referencia del núcleo de visión: la Fase 1
consiste en gran parte en portarlo con la estructura buena. Cuando el módulo real esté
construido, el prototipo se borra (misma norma que los `.md` de diseño de este repo).

### Lo que el prototipo demostró (no lo re-litigues)

| Hallazgo | Consecuencia |
|---|---|
| Pose de cabeza **no** basta: girando la cabeza y mirando de reojo, el filtro se lo comía entero | El filtro es **mirada = cabeza + ojos** (§3.2) |
| Los blendshapes `eyeLookIn/Out/Up/Down` de MediaPipe ya dan la desviación ocular | Cero coste extra; no hace falta L2CS-Net |
| Los landmarks de iris (468/473) **no siempre existen**: salen 468 o 478 según versión | Degradar promediando esquinas de ojo, nunca indexar a ciegas |
| Recortar la cara del frame ya dibujado mete los recuadros dentro del embedding | Los recortes salen SIEMPRE de una copia limpia del frame |
| Preguntar el nombre en cada foto congelaba el vídeo justo al posar | El nombre se pide **una vez por persona** |
| Guardar una captura por frame mientras dispara generó **143 imágenes en una sesión** | Una captura **por evento** de disparo, no por frame (§4.7) |

### Datos reales que ya existen en `Prototipo/proto_seguro_data/`

- `galeria.npz` → 1 persona (`fran`) con **4 embeddings** de 512 float32.
- `enrolamiento/fran/` → 4 recortes 112×112.
- `config_usada.json` **no existe todavía**: solo se escribe al salir con `q`, y las
  sesiones se cerraron con la X.

**Qué hacer con esto en la Fase 1:** si `config_usada.json` aparece, **úsalo como valores
por defecto** de `data/seguro.json` en vez de los de arranque de §5. Si no, usa los de §5 y
**pídele al usuario que corra el prototipo, ajuste con las teclas y salga con `q`** — en
particular `signo_mirada`, que es el único valor que no se puede deducir sin cámara.

La galería del prototipo **no se migra**: 4 fotos de una sola persona está por debajo del
mínimo útil (5-10 variadas). Se reenrola en la Fase 2.

---

## 3. El pipeline y los números

```
frame (640×480, cv2, CAP_DSHOW)
  └─> MediaPipe FaceLandmarker (VIDEO, num_faces=4)
        └─> por cara: bbox, landmarks, matriz 4×4, blendshapes
              └─> tracker IoU -> track_id estable
                    └─> FILTROS BARATOS (§3.1, §3.2) ──> descartado + MOTIVO
                          └─> ¿track sin identidad? ──> EMBEDDING (§3.3)
                                └─> autorizado | desconocido | indeciso
                                      └─> persistencia (§3.4) ──> DISPARO
```

**Presupuesto:** ~4 fps (es presencia, no videovigilancia). MediaPipe ~5-10 ms/frame; el
embedding (~10-30 ms) **solo en tracks nuevos**. Tiene que notarse como proceso de fondo o
el usuario lo desactivará.

### 3.1 Filtro de distancia — el que más falsos positivos mata

`altura_bbox_px >= min_altura_cara_px`. Traduce a "está lo bastante cerca para **leer** la
pantalla": alguien a 4 metros por detrás no puede. Cuesta cero y va **antes** que cualquier
modelo.

### 3.2 Filtro de mirada — cabeza + ojos, no solo cabeza

```python
mirada_yaw = yaw_cabeza + signo_mirada * yaw_ojos

h_izq = bs["eyeLookOutLeft"] - bs["eyeLookInLeft"]     # "out" = hacia la sien
h_der = bs["eyeLookInRight"] - bs["eyeLookOutRight"]
yaw_ojos = ((h_izq + h_der) / 2) * grados_mirada_max   # ~35°
```

Condición: `|mirada_yaw| <= yaw_max_grados` y lo análogo en pitch.

> **Los signos se verifican con el usuario delante de la cámara, no se deducen.** Prueba:
> girar la cabeza de lado mirando a la pantalla → `MIRADA` debe acercarse a 0. Equivocarse
> produce un modo que dispara justo cuando la persona *deja* de mirar.

### 3.3 Identidad: máximo, no media, y dos umbrales

Galería: 5-10 embeddings por persona, L2-normalizados.

```python
sim = max(cos(emb, e) for persona in galeria for e in persona.embeddings)
```

**Máximo sobre las muestras, nunca media de los embeddings**: el promedio de "con gafas" y
"sin gafas" es un vector que no se parece a ninguno de los dos.

| Condición | Resultado |
|---|---|
| `sim >= T_alto` | **Autorizado.** Se cachea en el track |
| `sim <= T_bajo` | **Desconocido** |
| en medio | **No decidir.** Vota sobre `max_votos` frames del track |

Sin resolver tras `max_votos` → **desconocido** (fallo hacia la seguridad). Por eso `T_bajo`
se calibra con holgura: un `T_bajo` alto convierte cada indecisión en un bloqueo, y un modo
insufrible se desactiva al día siguiente.

### 3.4 Persistencia

`frames_para_disparar` frames **consecutivos** del mismo track. Quien cruza el fondo no
dispara nada.

### 3.5 Máquina de estados

```
INACTIVO ──activar──> VIGILANDO ──candidato──> SOSPECHA ──confirmado──> BLOQUEADO
                          ^                        │                        │
                          │                        └──se va──┐              │ contraseña OK
                          └──────── GRACIA <───────────────────────────────┘
```

**GRACIA es obligatorio.** Sin él, al desbloquear con el intruso todavía delante el modo
vuelve a disparar: bucle de bloqueo infinito.

### 3.6 Dónde vive y cuándo actúa — requisito central

**El modo vigila con CRISTOPHER de fondo. No hace falta verlo, ni que esté en primer plano,
ni tener la pestaña del HUD abierta.** Es el caso de uso normal: estás trabajando en otra
cosa, con CRISTOPHER arrancado y olvidado, y alguien se asoma.

Consecuencias concretas de diseño:

- El hilo vigilante vive en el **proceso del HUD** (`hud/__main__.py`), como hilo daemon,
  igual que el demonio proactivo y el vigilante de comandos de WhatsApp. El navegador es
  solo una **vista**: cerrar la pestaña no para nada. Si el usuario cierra el navegador
  entero, el modo sigue vigilando.
- El modo **no depende del foco de ninguna ventana**. Nada de leer la ventana activa, nada
  de pausarse al minimizar.
- El overlay tiene que salir **encima de lo que sea que esté haciendo el usuario**: otra
  app maximizada, un vídeo, un juego, un segundo monitor. Ventana `pywebview` con
  `on_top=True` + `fullscreen`, y **robar el foco** para que se pueda teclear la contraseña
  sin pinchar antes.
- **Multi-monitor:** un overlay en una sola pantalla deja la otra a la vista, que es
  justamente lo que se quería tapar. Cubre **todas** las pantallas (una ventana por
  monitor, o una que abarque el escritorio virtual completo).

> **Límite honesto, y por qué `LockWorkStation` no es opcional.** Una app en *fullscreen
> exclusivo* (muchos juegos, algún reproductor) puede tapar incluso a una ventana
> "siempre encima": Windows no garantiza que nada se ponga por delante. Por eso el bloqueo
> del sistema operativo es la garantía de verdad — la pantalla de bloqueo de Windows sí
> cubre absolutamente todo, siempre. El overlay es la personalidad del aviso; si alguna vez
> no llega a verse, el portátil **ya está bloqueado igual**. No inviertas tiempo en pelearte
> con el fullscreen exclusivo: es una batalla perdida y ya está cubierta.

---

## 4. Decisiones cerradas

| Capa | Elegido | Por qué |
|---|---|---|
| Detección + pose + mirada | **MediaPipe Face Landmarker** (Apache 2.0) | Una pasada da bbox, landmarks, matriz de pose y 52 blendshapes |
| Embedding | **AuraFace-v1** ONNX vía `onnxruntime` | ArcFace 512-d con pesos de licencia permisiva |
| Galería | **NumPy + coseno + dos umbrales** | 200 vectores: el producto escalar es instantáneo |
| Tracking | Tracker IoU propio | El embedding solo se calcula en tracks nuevos |
| Bloqueo real | `LockWorkStation` (ctypes) | El overlay es la personalidad; la seguridad la da Windows |
| Contraseña | `hashlib.scrypt` (stdlib) | Cero dependencias nuevas |

**Descartados:** InsightFace `buffalo_l`/`antelopev2` (código MIT pero **pesos
solo-investigación**, y este repo es público) · `dlib`/`face_recognition` (modelo de 2017,
falla con poca luz y con hermanos, compila en Windows) · L2CS-Net/OpenFace (los blendshapes
ya cubren el caso; OpenFace además no es comercial) · FAISS/Qdrant/Milvus (para 100k
identidades) · un softmax sobre las N personas (siempre devuelve una clase: metería a un
desconocido en la cara más parecida con 94% de confianza — esto es **open-set**) ·
anti-spoofing (innecesario mientras la cara no autentique; si algún día hay desbloqueo
facial, entra Silent-Face-Anti-Spoofing y **no antes**) · cifrar la galería (ofuscación con
la clave en la misma máquina, y `data/memory.db` ya guarda todo en claro: se documenta el
modelo de amenaza en vez de montar teatro).

---

## 5. Contrato entre módulos y config

```
cristopher/
  seguro_vision.py    # VISIÓN PURA: sin cámara, sin hilos, sin estado global
                      #   class Detector:  detectar(frame_bgr, ts_ms) -> list[Cara]
                      #   cinco_puntos(puntos) -> np.ndarray | None
                      #   euler_desde_matriz(m) -> (yaw, pitch, roll)
                      #   mirada_desde_blendshapes(bs) -> (yaw_ojos, pitch_ojos)
                      #   class Tracker:   actualizar(caras) -> list[(id, track)]
                      #   evaluar_cara(cara, cfg) -> {"pasa": bool, "motivo": str}
  seguro_galeria.py   # IDENTIDAD: sin cámara, sin hilos
                      #   class Embedder:  alinear(frame, puntos), embedding(crop)
                      #   class Galeria:   anadir/borrar/buscar/resumen/matriz_parecidos
                      #   decidir_identidad(track, galeria, emb, cfg) -> None
  seguro.py           # EL ÚNICO con estado global: config, cámara, hilo, estados, disparo
  seguro_overlay.py   # ventana pywebview de bloqueo (proceso aparte)
  seguro_hud.py       # rutas del panel + stream MJPEG
  hud/static/seguro.js
  tools/seguro_tools.py
scripts/
  probar_seguro_vision.py    # banco de pruebas permanente
  calibrar_seguro.py
data/                        # (gitignored)
  seguro.json
  seguro/  galeria.npz  personas.json  calibracion.json
           enrolamiento/<persona>/*.jpg   capturas/*.jpg   modelos/*.onnx
```

Estado runtime **en memoria** (dict + `RLock`), igual que `concentracion.py`: lo tocan el
hilo vigilante y el worker del agente. **No se persiste** — no tiene sentido que sobreviva
a un reinicio.

```jsonc
// data/seguro.json  (valores de arranque; los pisa config_usada.json del prototipo si existe)
{
  "fps_vigilancia": 4,
  "camara_indice": 0,
  "min_altura_cara_px": 90,
  "yaw_max_grados": 35,
  "pitch_max_grados": 25,
  "usar_mirada": true,
  "signo_mirada": 1,            // LO FIJA EL USUARIO con el prototipo
  "grados_mirada_max": 35,
  "frames_para_disparar": 6,
  "max_votos": 5,
  "segundos_gracia_tras_desbloqueo": 60,
  "segundos_entre_capturas": 30,
  "bloquear_windows": true,
  "guardar_captura_intruso": true,
  "modelo_embedding": "auraface-v1",
  "umbral_alto": null,          // los escribe calibrar_seguro.py
  "umbral_bajo": null,
  "password_hash": "",
  "password_salt": ""
}
```

---

# FASE 1 — El modo seguro, funcionando

**Objetivo:** al terminar, el modo **ya sirve**: cualquier cara ajena mirando la pantalla
bloquea el portátil y hace falta la contraseña para volver. Sin identidad todavía.

1.1. **Portar la visión.** `seguro_vision.py` y `seguro_galeria.py` según §5, desde el
prototipo. Limpia el andamiaje (dibujo, teclas, `main`) pero **conserva intactos los
docstrings de los gotchas** (iris, signos, frame limpio): son conocimiento pagado con
fallos reales. La config deja de ser un dict global: `evaluar_cara` recibe `cfg`.

1.2. **`scripts/probar_seguro_vision.py`**: pinta lo mismo que el prototipo (`h=`,
`cabeza=`, `ojos=`, `MIRADA=`, motivo del descarte). Banco de pruebas permanente, no código
de usar y tirar.

1.3. **Dependencias:** `opencv-python`, `mediapipe`, `onnxruntime`. **Antes de tocar
`requirements.txt`**, instálalas en un venv limpio y comprueba con `pip list` que no mueven
`httpx`, `aiohttp` ni `aiodns` — lee los comentarios de ese archivo sobre el conflicto real
de GHunt y el bug de pycares de `huella.py`. Documenta el resultado con ese mismo estilo.

1.4. **`seguro.py`**: config autoeditable en `data/seguro.json` (patrón exacto de
`concentracion.py`: `_leer_config`/`_escribir_config`/`_CFG_LOCK`), estado runtime con
`RLock`, y hilo vigilante **dueño único** de `VideoCapture` (se abre y cierra dentro del
hilo, nunca se comparte). Máquina de estados completa de §3.5.

El hilo se lanza desde `hud/__main__.py::main()` junto a los otros daemon (métricas,
demonio proactivo, comandos de WhatsApp, sondeo de redes) — ver §3.6: vigila de fondo, sin
depender de que el HUD esté visible ni en primer plano.

1.5. **Cámara ocupada.** En Windows es de uso exclusivo: si Zoom/Teams la tiene,
`VideoCapture` falla o devuelve negro. Detectarlo, avisar por `bus.log`, pausar y
reintentar cada N segundos. **Nunca quedarse en silencio dando por hecho que vigila** — en
un módulo de seguridad ese es el peor fallo posible.

1.6. **Overlay de bloqueo** (`seguro_overlay.py`): ventana `pywebview` fullscreen, sin
marco, `on_top=True`, **con el foco robado** para poder teclear la contraseña sin pinchar
antes, y **cubriendo todas las pantallas** si hay más de un monitor (§3.6). Cara enfadada +
campo de contraseña. Proceso aparte con PID-guard, mismo patrón que `control_total`
(`CONTROL_TOTAL_WIDGET_PID`).

1.7. **Contraseña** con `hashlib.scrypt` + salt. **Sin contraseña configurada el modo se
niega a activarse**, con mensaje claro: activar un bloqueo que no puedes desbloquear es una
trampa.

1.8. **`LockWorkStation`** vía ctypes en paralelo al overlay, tras `bloquear_windows`. En
README y en el mensaje al usuario, decir claro que **la seguridad real la da Windows**: el
overlay se salta con Alt+F4 y no hay que fingir lo contrario.

1.9. **Al disparar:** `bus.add_alerta(3, ...)` (nivel 3 → si `CRISTOPHER_NOTIFICAR_EMAIL`
está puesto, llega también por correo), guardar **una** captura por evento respetando
`segundos_entre_capturas` (el prototipo generó 143 imágenes en una sesión guardando una por
frame), y respetar la cola de avisos si hay `concentracion` activo. Estado GRACIA tras el
desbloqueo.

1.10. **Tools:** `seguro_activar`, `seguro_desactivar`, `seguro_estado`,
`seguro_configurar`, `seguro_configurar_contrasena`. Registrar en `tools/__init__.py`
(recuerda: `TOOLS` es la fuente única de verdad, el system prompt se genera de ahí).
`bus.set_seguro(...)` siguiendo el patrón de `set_concentracion`.

**Criterio de aceptación — lo verifica el usuario, tú no tienes webcam:**

1. "activa el modo seguro" por chat lo enciende.
2. La cámara solo se abre entonces y se libera al desactivar (compruébalo con el LED).
3. Con alguien asomándose, bloquea. La contraseña correcta desbloquea, la incorrecta no.
4. **No entra en bucle de bloqueo** al desbloquear con el intruso todavía delante.
5. **Vigila de fondo** (§3.6), verificado en las cuatro situaciones:
   - HUD minimizado y trabajando en otra app → bloquea igual.
   - **Pestaña del navegador cerrada** → sigue vigilando; el overlay sale igual.
   - Vídeo a pantalla completa → el overlay sale por encima (y si no, Windows ha bloqueado
     igualmente: eso también es un pase).
   - Con dos monitores → **se tapan los dos**, no solo uno.

> **Parada obligatoria:** pídele al usuario que lo use **un día real** antes de la Fase 2.
> Los falsos positivos que salgan ahí son los que dicen si los umbrales de §3.1/§3.2 están
> bien, y aparecen usándolo, no probándolo diez minutos.

---

# FASE 2 — Lista blanca: reconocimiento facial + calibración

**Objetivo:** solo dispara con caras **no autorizadas**, y con umbrales medidos.

2.1. **`Embedder`** con AuraFace en `onnxruntime`. Alineación de 5 puntos desde los
landmarks que MediaPipe ya calculó — **no añadas SCRFD**. Plantilla ArcFace 112×112:

```python
REF_5PTS = np.array([[38.2946, 51.6963], [73.5318, 51.5014], [56.0252, 71.7366],
                     [41.5493, 92.3655], [70.7299, 92.2041]], dtype=np.float32)
```

2.2. **`Galeria`** sobre `.npz` + `personas.json`, comparación por máximo (§3.3). Embedding
**solo en tracks sin identidad resuelta**, y siempre desde una **copia limpia del frame**.

2.3. **Enrolamiento con puertas de calidad:** rechaza cara < 100 px, `|yaw| > 30°` o borrosa
(varianza del Laplaciano bajo umbral), **diciendo cuál de las tres**. El nombre se pide una
vez y luego cada foto es instantánea. Guarda el recorte en `data/seguro/enrolamiento/<persona>/`:
hace falta para recalcular la galería el día que se cambie de modelo. Diez fotos variadas
con un modelo mediocre baten a una foto perfecta con el mejor modelo del mundo.

2.4. **Tools:** `seguro_enrolar`, `seguro_listar_personas`, `seguro_olvidar_persona` (borra
embeddings **y** fotos).

2.5. **`scripts/calibrar_seguro.py`:**
- **Genuinos:** hold-one-out sobre las fotos de enrolamiento.
- **Impostores:** subconjunto de **LFW** (~2000 identidades) contra la galería, quedándose
  con el **máximo** por imagen. Descarga con `requests` a `data/seguro/lfw/`; **no entra en
  `requirements.txt`**, es solo para este script.
- **Barrido:** `T_alto` donde la falsa aceptación sobre impostores ≤ **0,1%**; `T_bajo` en
  el percentil 1 de la distribución genuina.
- **Cordura:** si `T_bajo > T_alto`, las distribuciones se solapan → **fallar ruidosamente**
  pidiendo reenrolar. Nunca escribir umbrales que no separan.
- Escribir `calibracion.json` con distribuciones, umbrales, fecha, **modelo** y **tamaño de
  galería**. Avisar de recalibrar si la galería crece >50%: cuanta más gente, más probable
  es que un desconocido se parezca a alguien, y un umbral fijo se degrada solo.
- Imprimir la **matriz de similitud entre personas**, marcando pares por encima de `T_alto`.
  Si hay hermanos o parecidos, el usuario tiene que verlo.

**Criterio de aceptación:** el usuario se enrola (5-10 fotos variadas) y deja de dispararle.
Enrola a una segunda persona: tampoco dispara con ella. Con un tercero no enrolado, dispara.
`calibracion.json` con FAR **medida**, no supuesta.

---

# FASE 3 — Zona de caras en el HUD + cierre

**Objetivo:** gestionar las personas autorizadas sin tocar consola ni chat, y cerrar la
documentación.

3.1. **Un solo dueño de la cámara.** Nada de `getUserMedia` en el navegador: en Windows la
webcam es exclusiva y se pelearía con el hilo vigilante. El panel consume un stream
**MJPEG** (`multipart/x-mixed-replace`) de los frames que el hilo ya captura, y "hacer una
foto" es pedirle el **frame actual**. Genera el stream solo si hay suscriptores (contador
con lock): panel cerrado, cero coste de JPEG.

3.2. **Rutas directas en el hilo HTTP** (`seguro.py` es thread-safe), igual que `/musica` y
`/sesiones`. **Nunca por `_JOBS`**: si pasaran por la cola del worker, abrir el panel
esperaría detrás del turno que CRISTOPHER tenga en marcha.

```
GET  /seguro                    → estado + personas + calidad + aviso de recalibración
GET  /seguro/preview            → MJPEG anotado
GET  /seguro/capturas           → últimas capturas de intruso
POST /seguro/activar            → {activo}
POST /seguro/enrolar            → {persona, desde:"camara"|"captura", archivo?}
                                  → {ok} | {ok:false, motivo:"borrosa"|"lejos"|"girada"}
POST /seguro/persona/borrar     → {persona}
POST /seguro/persona/renombrar  → {persona, nuevo}
POST /seguro/calibrar           → lanza el script en un hilo; progreso por bus
```

3.3. **El panel:**
- **Vista en vivo anotada.** 🟩 verde = autorizado (nombre + similitud) · 🟥 rojo =
  dispararía · ⬜ gris = descartado **con el motivo escrito** ("muy lejos (62px)", "no mira
  (mirada 58°)"). El gris con motivo no es decoración: es cómo el usuario ajusta los
  umbrales viendo lo que ve el modo, en vez de tocar números a ciegas.
- **Rejilla de personas:** miniatura, nombre, nº de fotos, indicador de calidad del
  enrolamiento, botones *Añadir foto* / *Borrar*.
- **"+ Añadir persona" → captura guiada:** nombre una sola vez, luego pide poses (de frente
  · girado a cada lado · con gafas · sin · luz tenue) con feedback en vivo de las puertas de
  calidad.
- **Últimos intrusos** con botón *"Autorizar a esta persona"*, que precarga esa foto en el
  enrolamiento. Es el caso real: salta con tu compañero de piso y lo añades en dos clics.

3.4. `/seguro` **nunca devuelve embeddings** al navegador: miniaturas y metadatos. El panel
y las tools llaman a **las mismas funciones** de `seguro_galeria.py` — el panel es una
superficie, no una segunda implementación.

3.5. **Privacidad en el README, en claro:** la cámara solo se abre con el modo activo (nunca
al arrancar `python -m cristopher`), el LED físico avisa siempre, no se graba vídeo,
`data/seguro/` contiene **datos biométricos sin cifrar**, y se guarda biometría de terceros
— el panel debe recordarlo al añadir a alguien.

3.6. **Documentar** el módulo en `CLAUDE.md` (sección de decisiones de arquitectura y tabla
de extensiones) y en la tabla de capacidades del README, con el mismo tono honesto del
resto: qué protege de verdad y qué no. **Borrar `prototipo_seguro.py` y este `.md`**, según
la norma del repo: el código pasa a ser la verdad.

**Criterio de aceptación:** añadir una persona de principio a fin sin tocar consola ni chat,
y que el modo deje de dispararse con ella al instante.

---

## 6. Reglas de trabajo

1. **Una fase, parar, reportar:** qué se hizo, qué falta, y **qué tiene que probar el
   usuario**. No empieces la siguiente.
2. **No puedes verificar esto tú.** No tienes webcam. Tu trabajo es dejarlo *probable*:
   números en pantalla, motivos escritos, fallos explícitos.
3. **Fallos explícitos, nunca silencio.** Es un módulo de seguridad.
4. **Nada de abstracciones prematuras.** Si una función de 20 líneas resuelve, esa es la
   solución.
5. **Secretos y biometría** bajo `data/` (ya gitignored). Contraseña hasheada, nunca en
   claro, nunca en logs ni commits.
6. **Estilo del repo:** español en docstrings y mensajes, nombres que explican intención,
   comentarios que expliquen *por qué*.

## 7. Gotchas anotados de antemano

- **Cámara de uso exclusivo (Windows):** Zoom/Teams/Meet la bloquean. Detectar y degradar
  con aviso.
- **`cv2.CAP_DSHOW`** en Windows: MSMF añade ~2 s al abrir.
- **Hilos:** todo el ciclo de vida de `VideoCapture` dentro del hilo vigilante.
- **MJPEG + `ThreadingHTTPServer`:** cada stream ocupa un hilo. Cerrar el generador cuando
  el navegador corta (`BrokenPipeError`/`ConnectionAbortedError`, ya contemplado en
  `Handler.handle_error`) o se acumulan hilos zombis con cada recarga.
- **Landmarks 468 vs 478:** no indexes el iris a ciegas.
- **Signos de yaw/pitch/mirada:** se verifican con el usuario delante de la cámara.
- **`mediapipe` con Python 3.11 en Windows:** hay wheel. Fija versión, no la dejes flotando.
- **Bajo `pythonw` `sys.stdout` es `None`** y `print()` revienta (ya documentado en
  `CLAUDE.md`): si añades trazas de arranque, guárdalas tras `if sys.stdout is not None`.
