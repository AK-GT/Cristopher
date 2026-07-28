# HERRAMIENTA DE BÚSQUEDA — Motor de Huella Digital por Pivotes (CRISTOPHER)

Sistema de búsqueda de información de una persona a partir de **cualquier pivote** (username,
email, teléfono, nombre, imagen), **gratis** y **contrastando varias fuentes**. Propósito:
auditar y **proteger mi propia huella digital**; y verificación con propósito legítimo. Este
documento es a la vez el **diseño** (qué herramientas, por qué) y el **plan de ejecución**
(5 tandas con prueba de aceptación) para construirlo en Cristopher con Claude Code.
Punto extra: Esta herramienta tiene que poder ser utilizable con cualquier user

> Documento único. Reemplaza a los antiguos `DISENO_motor_huella_por_pivotes.md` y
> `PLAN_motor_huella_5_tandas_para_claude_code.md`.

---

# PARTE I — DISEÑO

## 1. La idea que lo gobierna: pivotar + contrastar

Un **identificador** (alias, email, teléfono, dominio, imagen) se usa como **pivote**; cada
pivote produce **nuevos leads** que se convierten en el siguiente pivote. La regla de oro de
la disciplina: **ningún match aislado prueba nada.** Usernames compartidos, avatares
reciclados y cuentas paródicas generan falsos positivos constantemente. Por eso el sistema NO
es "una herramienta": es un bucle que lanza varias, **cruza sus resultados** y solo da por
bueno lo confirmado por **2+ fuentes independientes**.

Esto es exactamente la "búsqueda profunda" aplicada a personas: descomponer → lanzar
herramientas (o sub-agentes) en paralelo → sintetizar con fuentes.

**¿Basta un solo pivote?** Sí para arrancar, y normalmente para llegar lejos, **si el pivote
es distintivo**. De más a menos potente en solitario: email (sobre todo Gmail) ≈ username
distintivo > teléfono > nombre real (el más débil; genera ruido). La unicidad manda: un alias
raro cae en cascada; un nombre común se atasca. Con un pivote encuentras *leads*; la certeza
la da el cruce.

## 2. Catálogo contrastado por pivote (todo gratis, todo integrable)

Cada herramienta marcada con **cómo se integra** en los patrones existentes de Cristopher
(subproceso tipo `yt-dlp`/`delegate.py`, o `pip install` + import). ⭐ = pieza clave.

### Pivote USERNAME
| Herramienta | Cobertura | Fuerte en | Integración |
|---|---|---|---|
| **Maigret** ⭐ | 3.100+ sitios | Recursivo; extrae foto/bio/cuentas enlazadas, fechas de creación; detecta falsos positivos por análisis de contenido | Python, CLI/import |
| **Socialscan** ⭐ | 11 grandes (IG, X, GitHub, Reddit…) | Consulta endpoints de registro → **100% precisión**; async, muy rápido | Python, import |
| Sherlock | 479 | Batch (lista de alias), salida CSV/JSON | Python, CLI |
| Blackbird | 732+ | Salida rica; también acepta email | Python |
| Enola | 407 | Binario Go rápido | subproceso |

**Estrategia:** Maigret para amplitud + Socialscan para verificar con alta confianza las
plataformas grandes. Un hit confirmado por ambos = fiable.

### Pivote EMAIL
| Herramienta | Qué saca | Integración |
|---|---|---|
| **holehe** / **user-scanner** ⭐ | En qué servicios está registrado el correo (vía flujo de reset de contraseña, silencioso). `user-scanner` es el sucesor **mantenido** de holehe | Python |
| **GHunt** ⭐ | Si es Gmail: servicios Google asociados, foto de perfil, reseñas, nombre | Python |
| **Gravatar** | Hash del email → avatar y perfil público | Petición HTTP trivial, sin key |
| Mosint | Agrega brechas + social (Go) | subproceso |
| Epieos | Reverse email/teléfono, reseñas Google (web) | navegador |

**Estrategia:** user-scanner para el mapa de cuentas + GHunt si es Gmail + Gravatar como
comprobación barata.

### Pivote TELÉFONO (el más difícil: pocas bases abiertas)
| Herramienta | Qué saca | Integración |
|---|---|---|
| **PhoneInfoga** ⭐ | País, operadora, tipo de línea, VoIP + huella en buscadores | Python/Go; usa Numverify (free tier) |
| **ignorant** ⭐ | Si el número está usado en Amazon, Instagram, etc. (el "holehe de teléfonos") | Python |
| Epieos | Reverse phone | web |
| Truecaller | Nombre por número (base colaborativa) — ojo a ToS/límites | no oficial |

### Pivote BRECHAS/LEAKS (el pegamento que enlaza pivotes)
| Fuente | Gratis | Integración |
|---|---|---|
| **XposedOrNot API** ⭐ | **Sí, open-source, sin key** en casi todos los endpoints; incluye stealer logs; cliente Python oficial | API REST |
| **HIBP** | Web y Pwned Passwords sin key; búsqueda por email = API de pago (~4,39 $/mes) | API |
| Mozilla Monitor / DataBreach.com / BreachDirectory / Leak-Lookup / LeakCheck / EmailLeaked | Consulta gratis (tiers) | web/API |

**Estrategia:** **XposedOrNot** es la joya para automatizar gratis (Python, sin key). HIBP
Pwned Passwords para exposición de contraseñas.

### Pivote IMAGEN / CARA (potente y éticamente el más delicado)
| Herramienta | Uso | Integración |
|---|---|---|
| Yandex Images ⭐ | La mejor **gratis** para caras (índice amplio, algoritmo prioriza rostro) | navegador |
| Google Lens | Fuente de una imagen (no es reconocimiento facial puro) | navegador |
| TinEye | Detecta foto reutilizada en otros sitios | navegador |
| FaceCheck.ID / FaceSeek / Lenso.ai / Search4Faces | Búsqueda facial, tier gratis | navegador |

**Recomendación:** dejar la búsqueda de **cara** FUERA del MVP. Si se incluye, vía Playwright
+ el módulo de visión, y siempre con gate de confirmación reforzado.

### El motor de extracción/pivote
- **socid-extractor** ⭐ (`pip install socid-extractor`): es el motor que hay **detrás de
  Maigret**. Convierte cualquier URL de perfil en un **registro estructurado** (150+ sitios),
  y hace **búsqueda recursiva por los nuevos usernames/IDs que encuentra**, incluso pivotando
  a dumps/breaches donde el user ID es clave de unión. Este es el corazón del salto automático
  entre pivotes.

## 3. Frameworks que YA orquestan (alternativa a construirlo a mano)

| Framework | Qué es | Encaje en Cristopher |
|---|---|---|
| **SpiderFoot** ⭐ | Automatiza 200+ fuentes y **correlaciona** solo (modelo publisher/subscriber: los módulos se alimentan entre sí); open-source (MIT, Python), self-host, con API/módulos | Correrlo como **servicio** (patrón del server Node de WhatsApp) y que Cristopher lo consulte por HTTP. La pieza gratis más potente de un tiro |
| Recon-ng | Framework modular estilo Metasploit, workspaces, automatizable por CLI; módulo WhatsMyName | subproceso guionizado |
| theHarvester | Barrido rápido de emails/dominios/subdominios (54+ fuentes). **Limitación: no correlaciona individuos** | subproceso, primera pasada |
| Maltego CE | Grafo de relaciones (análisis visual, manual); límites de resultados en la free | fuera de un agente; para revisión humana |

**Patrón ganador del sector:** dos etapas — un **colector automático** (SpiderFoot/Recon-ng)
reúne datos crudos → un **grafo** (Maltego) muestra cómo se relacionan.

## 4. Arquitectura en Cristopher

**Dos caminos:**
- **A (recomendado) — Bucle propio y ligero.** Envuelves un conjunto escogido de herramientas
  como tools del registro y montas un bucle de pivote controlado por ti. Más control, más
  liviano, 100% gratis, sin depender de un servicio pesado.
- **B — Envolver SpiderFoot** como servicio. Más fuentes de golpe, pero más peso y menos
  control fino. Vale si algún día quieres amplitud máxima.

**Flujo del camino A:**
1. **Semilla:** el usuario da un pivote (nombre / alias / email / teléfono / imagen) → **gate
   de confirmación (§8)**.
2. **Despacho por tipo:** se lanzan las tools del pivote correspondiente (varias, en paralelo
   — reutiliza `delegar_a_claude`/sub-agentes o subprocesos).
3. **Normalización:** **socid-extractor** convierte los perfiles hallados en registros
   estructurados.
4. **Extracción de nuevos pivotes:** de cada resultado se sacan nuevos identificadores (email
   en una bio, alias enlazado, teléfono…) → se encolan como pivotes nuevos, **deduplicados**,
   hasta profundidad N.
5. **Capa de contraste:** cada enlace se puntúa; **≥2 confirmaciones independientes = alta
   confianza**; un solo match = tentativo y marcado como tal.
6. **Dossier:** Gemini sintetiza un informe de huella con entidades, nivel de confianza y
   **fuentes** (estilo `busqueda_elite`, pero estructurado y con grafo de relaciones en texto).

**Reutiliza de lo que ya tienes:** subproceso (`delegate.py`, `yt-dlp`), paralelización con
sub-agentes, navegador+visión para imagen y sitios manuales, memoria para guardar dossiers, y
el gate de confirmación de §8.

## 5. Modelo de datos y precisión (el corazón de la calidad)

**Grafo de identidad:**
- **Nodo** = identificador: `{id, tipo: alias|email|telefono|nombre|url_perfil|foto|brecha, valor, confianza}`.
- **Arista** = enlace entre nodos: `{origen, destino, tipo_enlace, fuente, peso}`.

**Puntuación de confianza (capa de contraste):**
- Confianza de una entidad = suma ponderada de corroboraciones **independientes**.
- Dos módulos que leen la **MISMA API cuentan como UNA** fuente (evita la falsa corroboración
  — el fallo nº1 de las herramientas malas).
- Enlaces **fuertes** (endpoint de registro confirmado tipo Socialscan/holehe, foto idéntica
  por pHash, email verificado en la bio) pesan más que **débiles** (un alias suelto que
  "existe").
- Umbrales: `<0.4` tentativo · `0.4–0.7` probable · `>0.7` confirmado. **El dossier nunca
  funde niveles.**

**Anti falsos positivos (lo que separa un juguete de una herramienta seria):**
- Regla dura: **mínimo 2 fuentes independientes** por relación antes de afirmarla.
- Corrobora con los **metadatos** que da socid-extractor/Maigret (fecha de creación, cuentas
  enlazadas) — no solo "el alias existe".
- **Puntuación de confianza** por entidad; el dossier distingue "confirmado" de "posible".
- Una suposición débil temprana contamina toda la investigación: el bucle marca la
  **procedencia** de cada dato.

## 6. Correlación avanzada (sube la precisión por encima del alias literal)

**Enlace por foto de perfil (pHash):** reutilizar el mismo avatar en varias cuentas es de los
enlaces **más fuertes y más difíciles de falsear**. **Hashing perceptual** (`imagehash`/pHash
— Python, gratis, offline): se descargan los avatares que devuelven Maigret/socid-extractor,
se hashean, y cuentas con distancia de hash pequeña se enlazan **aunque cambie el alias**.
Encaja con el módulo de visión. Es lo que separa "encontré un alias igual" de "es la misma
persona".

**Crackear el pivote NOMBRE (el más débil):**
- **Permutación:** generar candidatos de alias/email (`nombre.apellido`, `napellido`,
  iniciales, `+año`) y meterlos por las tools de username/email. Un candidato confirmado en
  2+ sitios se **promueve a pivote real**.
- **Dorking automático:** consultas dirigidas a Google/DuckDuckGo/Bing (`"Nombre Apellido"
  ciudad`, `site:linkedin.com "Nombre"`) vía el navegador Playwright que YA tienes. Los
  perfiles hallados vuelven al bucle.

## 7. Protección continua (encontrar es la mitad; reducir es la otra)

**Auto-auditoría recurrente con diff:** para protegerte no sirve un escaneo de una vez, sirve
**vigilar**. Monitor programado (planificador + demonio proactivo `proactivo.py` + memoria
episódica):
- Escaneo periódico (p. ej. mensual) de TUS pivotes; el dossier se guarda como episodio.
- **Diff** contra el anterior: alerta solo de lo NUEVO (una brecha nueva, una cuenta que te
  suplanta, un dato que reaparece tras pedir su borrado).
- Alerta **nivel 3** del demonio si aparece algo sensible (una contraseña tuya en un stealer
  log).

**Remediación:** tras el dossier, Cristopher propone y prepara acciones:
- **Opt-out** de data brokers y buscadores de personas (guías de borrado por sitio).
- **Alertas continuas** (HIBP / Mozilla Monitor) sobre tus correos.
- **Endurecer privacidad** en las cuentas halladas (checklist por plataforma).
- Plantillas de **solicitud de borrado RGPD (art. 17)** para sitios que expongan tus datos —
  redactar sí, **enviar con tu OK** (§8).

## 8. Límites legales y éticos (parte del diseño, no un añadido)

- **RGPD / España:** recopilar y perfilar datos de terceros está regulado. El caso más limpio
  y probablemente el más útil: **auditar tu propia huella**. El sistema es "self-first".
- **Gate de confirmación** obligatorio antes de lanzar una búsqueda de persona (trátalo como
  `enviar_correo` en §8).
- **Búsqueda de cara fuera del MVP:** es lo más potente y lo más peligroso.
- **No persistir** dossiers de terceros a la ligera; propósito limitado; fuentes públicas
  únicamente.

## 9. Stack MVP: 100% gratis, sin keys de pago

- **Username:** Maigret + Socialscan
- **Email:** holehe/user-scanner + GHunt (si Gmail) + Gravatar
- **Brechas:** XposedOrNot API (gratis, sin key)
- **Teléfono:** PhoneInfoga + ignorant
- **Motor de pivote:** socid-extractor
- **Correlación por foto:** `imagehash` (pHash de avatares)
- **Pivote nombre:** permutador de alias/email + dorking por el navegador
- **Auto-auditoría:** monitor programado + diff sobre memoria episódica
- **Remediación:** guías opt-out + plantillas RGPD + alertas HIBP/Monitor
- **Orquestación:** bucle propio en Cristopher (camino A)

Todo Python, todo gratis, cero keys de pago. Keys **gratis** opcionales para ampliar:
Numverify (teléfono), HIBP (si quieres su búsqueda por email).

---

# PARTE II — PLAN DE EJECUCIÓN (5 TANDAS)

> **Una tanda por sesión.** Al empezar cada sesión de Claude Code, pega las **REGLAS
> GLOBALES** + la tanda que toque. **No se avanza de tanda sin la prueba de aceptación en
> verde.** Commit por hito. El detalle de cada herramienta y decisión está en la Parte I.

## REGLAS GLOBALES (pégalas al inicio de cada sesión)

Vas a trabajar en CRISTOPHER (este repo). **Lee `CLAUDE.md` y la Parte I de este documento
enteros y respétalos.** En especial: MVP primero, cambios mínimos y localizados, NO reescribas
el proyecto, comunicación en formato medio, **fallos explícitos**. Regla de oro: **diagnostica
y enséñame el plan ANTES de tocar código**; no implementes hasta que apruebe el plan.

1. **Propósito:** auditar y proteger MI propia huella. Es el encuadre legal más limpio
   (RGPD/España). Diséñalo "self-first".
2. **Gate §8 obligatorio:** ninguna búsqueda de persona se lanza sin confirmación explícita,
   igual que `enviar_correo`. Reutiliza el confirmador existente (`google_tools.set_confirmer`
   / `confirmar_accion`).
3. **Regla dura de precisión — SAGRADA:** ningún enlace se afirma con una sola fuente. **≥2
   fuentes independientes** para "confirmado". Dos módulos que leen la MISMA API cuentan como
   UNA. Todo dato lleva su procedencia.
4. **Todo gratis, sin keys de pago.** Solo herramientas Python/CLI libres. Keys gratis
   (Numverify, HIBP) van detrás de config, nunca obligatorias.
5. **Herramientas nuevas = entradas en el registro `TOOLS`** (fuente única de verdad; el
   system prompt se autogenera de ahí). No hay lista de capacidades aparte.
6. **Aislamiento de herramientas externas:** cada tool externa se lanza como subproceso con
   timeout y entorno saneado — reutiliza `delegate._entorno_sub_agente()` y el patrón de
   `delegate.py`/`yt-dlp`. Nunca hereda permisos amplios.
7. **Persistencia local:** dossiers en `data/huella/` (gitignored). Datos de terceros:
   propósito limitado, no persistir a la ligera.
8. **Verifica, no asumas:** que cada herramienta externa instala y ARRANCA de verdad antes de
   darla por buena (varias están medio abandonadas). Falla explícito si no; no finjas
   resultados.

**Modelo de datos común** (se define en la Tanda A, lo usan todas): grafo de identidad
(nodos/aristas con `{tipo, fuente, peso}`) + puntuación de confianza con umbrales
tentativo/probable/confirmado (ver Parte I §5). Módulo raíz `cristopher/huella.py`; wrappers
en `cristopher/tools/huella_tools.py`.

---

## TANDA A — Cimientos + primer pivote usable (username)

**Objetivo:** dejar el andamiaje probado POR un pivote real, no por un stub vacío.

**Qué construir:**
- `cristopher/huella.py` con el modelo de datos común (grafo, nodo, arista, dossier) y
  `data/huella/`.
- **Gate §8** antes de cualquier búsqueda (reutiliza el confirmador existente).
- Wrapper de subproceso aislado (timeout + entorno saneado, patrón `delegate.py`).
- Envolver el pivote **USERNAME**: **Maigret** (amplitud, 3000+ sitios, parsea perfiles) +
  **Socialscan** (verificación de alta confianza vía endpoints de registro).
- Herramienta `huella_buscar(pivote, tipo)` que confirma (§8), despacha por tipo=alias,
  normaliza los hits a nodos con procedencia, y devuelve un dossier.

**Reutiliza:** confirmador de §8, patrón subproceso de `delegate.py`, síntesis estilo
`busqueda_elite`.

**Gotchas:**
- Instala y **verifica que Maigret y Socialscan arrancan** (pip). Falla explícito si no.
- Marca procedencia desde el minuto uno (la necesitarás en la Tanda C).
- Distingue ya "confirmado" (ambos coinciden) de "tentativo" (uno solo), aunque la puntuación
  fina llegue en la Tanda C.

**Prueba de aceptación:**
1. `huella_buscar` pide confirmación y la respeta (sin OK, no busca).
2. Dado un alias, devuelve cuentas contrastando Maigret + Socialscan, marcando confirmado vs
   tentativo, con fuentes. 100% gratis, sin keys.

---

## TANDA B — Colectores de pivote: email, brechas y teléfono

**Objetivo:** cubrir los pivotes semilla restantes con la misma forma que la Tanda A
(envolver + normalizar). Sin bucle todavía.

**Qué construir (cada uno una tool del registro, normalizando a nodos):**
- **EMAIL:** **user-scanner** (sucesor MANTENIDO de holehe — mapa de servicios donde está
  registrado el correo) + **GHunt** (si Gmail: nombre, foto, servicios Google) + **Gravatar**
  (hash del email → avatar/perfil, HTTP trivial sin key).
- **BRECHAS:** **XposedOrNot** (API gratis, sin key, incluye stealer logs) — el "pegamento".
- **TELÉFONO:** **PhoneInfoga** (país/operadora/tipo de línea + huella en buscadores) +
  **ignorant** (en qué apps está registrado el número).

**Gotchas:**
- **NO uses holehe** (medio abandonado, módulos rotos): usa **user-scanner**.
- **GHunt requiere autenticación una vez** (credenciales base64 de una cuenta Google de usar y
  tirar). Documenta el setup; si no está configurado, la tool falla explícito sin romper el
  resto.
- Verifica que XposedOrNot responde sin key. PhoneInfoga puede querer Numverify (free tier) —
  opcional, detrás de config.
- La cara/imagen queda FUERA de esta tanda (sensible; va en la D si acaso).

**Prueba de aceptación:**
1. Dado un email, mapea servicios registrados + (si Gmail) datos Google + exposición en
   brechas, con fuentes y sin keys de pago.
2. Dado un teléfono, devuelve país/operadora/uso en apps.

---

## TANDA C — El motor: bucle de pivote + extracción + puntuación

**Objetivo:** convertir "varias tools sueltas" en un sistema. **Esta es la tanda que da la
precisión** — el bucle y la puntuación nacen JUNTOS (un bucle sin contraste es un generador de
falsos positivos, y esa versión mala no debe llegar a existir).

**Qué construir:**
- Integrar **socid-extractor** (el motor detrás de Maigret): convierte cualquier URL de perfil
  en un registro estructurado (150+ sitios) y extrae nuevos identificadores.
- **Bucle de pivote (BFS):** cada resultado extrae nuevos identificadores (email en una bio,
  alias enlazado, teléfono) → se encolan como pivotes nuevos, **deduplicados**, hasta
  profundidad N (config, arranca en 2). Encadena las Tandas A–B automáticamente.
- **Capa de contraste/puntuación** (modelo de Parte I §5): pesos por independencia real,
  umbrales tentativo/probable/confirmado, confianza por entidad. El dossier nunca funde
  niveles.

**Gotchas:**
- **Corta el bucle:** profundidad máxima + dedup + tope de nodos, para que no explote ni entre
  en bucle infinito.
- **Independencia de fuentes:** dos módulos que consultan la misma API NO son dos
  confirmaciones. Modélalo explícito en el peso.
- Marca la procedencia de cada nodo/arista (viene de la Tanda A); aquí es lo que hace fiable la
  puntuación.

**Prueba de aceptación:**
1. Una semilla (alias o email) descubre por su cuenta un pivote nuevo y lo investiga en la
   siguiente iteración, sin bucle infinito ni duplicados.
2. Un alias en 2 fuentes independientes sale "confirmado"; un match suelto sale "tentativo" y
   marcado como tal.

---

## TANDA D — Correlación avanzada (pHash + pivote nombre) + cara opcional

**Objetivo:** los correlacionadores que suben la precisión por encima del alias literal.

**Qué construir:**
- **Enlace por foto (pHash):** `imagehash` (dep ligera, offline) sobre los avatares que
  devuelven Maigret/socid-extractor. Cuentas con distancia de hash pequeña se enlazan **aunque
  cambie el alias**. Encaja con el módulo de visión.
- **Pivote NOMBRE:** **permutador** de alias/email (`nombre.apellido`, `napellido`, iniciales,
  `+año`) por las tools de username/email; un candidato confirmado en 2+ sitios se promueve a
  pivote real. **Dorking automático** (`"Nombre Apellido" ciudad`, `site:linkedin.com
  "Nombre"`) por el navegador Playwright existente.
- **Cara (OPCIONAL, gate reforzado):** Yandex/TinEye vía navegador + visión. Solo si lo pides;
  es lo más potente y lo más sensible.

**Gotchas:**
- pHash: distancia de Hamming con umbral prudente (evita falsos positivos por avatares por
  defecto/genéricos).Ñ
- Nombre: la permutación genera ruido — NO promuevas un candidato a pivote sin ≥2 fuentes.
- Cara: gate más estricto que el general; nunca en el flujo automático de auto-auditoría sin OK.

**Prueba de aceptación:**
1. Dos cuentas con el mismo avatar y distinto alias se enlazan por pHash.
2. Un nombre real produce al menos un pivote confirmado vía permutación/dorking (respetando ≥2
   fuentes).

---

## TANDA E — Protección continua: auto-auditoría recurrente + remediación

**Objetivo:** el salto de "escaneo único" a "sistema de defensa de mi huella". **Requiere la
memoria episódica ya construida.**

**Qué construir:**
- **Monitor programado:** reutiliza el planificador/demonio proactivo (`proactivo.py`) para
  escanear MIS pivotes periódicamente (p. ej. mensual). Sin hilo nuevo: engánchalo al ciclo del
  demonio que ya corre.
- **Diff:** guarda cada dossier como episodio (memoria episódica) y compara con el anterior;
  **alerta solo de lo NUEVO** (brecha nueva, cuenta que me suplanta, dato que reaparece tras
  pedir borrado).
- **Alerta nivel 3** del demonio ante algo sensible nuevo (p. ej. una contraseña mía en un
  stealer log).
- **Remediación:** tras el dossier, Cristopher propone acciones — opt-out de data brokers
  (guías), checklist de privacidad por plataforma, alertas continuas (HIBP/Mozilla Monitor), y
  **plantillas de borrado RGPD art. 17**: redactar sí, **enviar con mi OK** (§8).

**Gotchas:**
- Depende de la memoria episódica: si no está, esta tanda no arranca (dilo, no la fuerces).
- El diff necesita normalizar los dossiers para comparar manzanas con manzanas.
- La remediación **nunca envía sola** una solicitud de borrado: redacta y espera confirmación
  (§8).

**Prueba de aceptación:**
1. Un segundo escaneo solo reporta lo NUEVO respecto al primero; un hallazgo sensible dispara
   aviso nivel 3.
2. Tras un dossier, Cristopher redacta una solicitud de borrado RGPD lista para revisar, sin
   enviarla sola.

---

## Orden, dependencias y caminos

- **Dependencias duras:** C necesita A+B · D (pHash) necesita el módulo de visión · E necesita
  la memoria episódica ya construida.
- **Camino corto (protección personal):** A → B → E. Ya te da auditoría de tu huella + brechas
  + monitor con diff + remediación, SIN construir el motor de bucle completo.
- **Camino completo (motor de verdad):** añade C → D (y la cara de D solo si la necesitas).

## Checklist de cierre por tanda (antes de commit)
- [ ] Prueba de aceptación en verde, probada a mano (no hay suite de tests en el repo).
- [ ] Cada herramienta externa instala y ARRANCA; falla explícito si no.
- [ ] Gate §8 respetado; nada irreversible sin confirmación.
- [ ] Regla de ≥2 fuentes respetada; procedencia en cada dato.
- [ ] Cero keys de pago. Cambios mínimos y localizados. Commit por hito.

---

## Fuentes
- Frameworks: medevel.com/spiderfoot, nixintel.info, toolkitly.com (SpiderFoot/Recon-ng/Maltego), espectrosint.com
- Username: whatsmynameapp.us, maxintel.org, github.com/soxoj/osint-namecheckers-list
- Email/Google: medevel.com (email OSINT), holeheosint.com, ghunt.org, ghunt.sh, epieos.com, x.com/epieos
- Teléfono: github.com/FOGSEC/PhoneInfoga, github.com/The-Osint-Toolbox/Telephone-OSINT
- Brechas: github.com/XposedOrNot, xposedornot.com, haveibeenpwned.com, leakcheck.io, emailleaked.com, leaknix.com
- Imagen/cara: witanworld.com, faceseek.online, brndle.com, spotsaas.com (Yandex, TinEye, Search4Faces, Lenso.ai)
- Motor de pivote: github.com/soxoj/socid-extractor, pypi.org/project/socid-extractor
- Metodología: theosintvault.io, shadowdragon.io, lampyre.io, bitsight.com
