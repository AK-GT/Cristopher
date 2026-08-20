# HERRAMIENTA DE BÚSQUEDA — Motor de Huella Digital por Pivotes (CRISTOPHER)

Sistema de búsqueda de información de una persona a partir de **cualquier pivote** (username,
email, teléfono, nombre, imagen), **gratis** y **contrastando varias fuentes**. Propósito:
auditar y **proteger mi propia huella digital**; y verificación con propósito legítimo.
Punto extra: esta herramienta tiene que poder ser utilizable con cualquier usuario.

> **Estado: construido** (`cristopher/huella.py` + `tools/huella_tools.py`, tandas A-S).
> Lo que queda aquí es el **diseño** — qué fuente cubre cada pivote y por qué, y los
> umbrales de puntuación que el código cita por sección (§1, §5, §8). El plan de
> ejecución por tandas se borró al terminarlo: el código es ahora la verdad.

---

# DISEÑO

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

## Fuentes
- Frameworks: medevel.com/spiderfoot, nixintel.info, toolkitly.com (SpiderFoot/Recon-ng/Maltego), espectrosint.com
- Username: whatsmynameapp.us, maxintel.org, github.com/soxoj/osint-namecheckers-list
- Email/Google: medevel.com (email OSINT), holeheosint.com, ghunt.org, ghunt.sh, epieos.com, x.com/epieos
- Teléfono: github.com/FOGSEC/PhoneInfoga, github.com/The-Osint-Toolbox/Telephone-OSINT
- Brechas: github.com/XposedOrNot, xposedornot.com, haveibeenpwned.com, leakcheck.io, emailleaked.com, leaknix.com
- Imagen/cara: witanworld.com, faceseek.online, brndle.com, spotsaas.com (Yandex, TinEye, Search4Faces, Lenso.ai)
- Motor de pivote: github.com/soxoj/socid-extractor, pypi.org/project/socid-extractor
- Metodología: theosintvault.io, shadowdragon.io, lampyre.io, bitsight.com
