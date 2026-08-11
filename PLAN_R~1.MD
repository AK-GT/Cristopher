# Plan · Integración de redes sociales en Cristopher

**Objetivo:** que Cristopher esté al tanto de TODAS tus cuentas de TikTok, Instagram y
YouTube Shorts — métricas y estadísticas — con la puerta abierta a publicar/crear
contenido más adelante.

**Alcance de esta fase:** solo lectura (consultar). Nada de publicar todavía, pero el
diseño se deja preparado para añadirlo sin reescribir nada.

**Mentalidad (esencia del proyecto):** simple primero, una pieza a la vez, fallos
explícitos, encapsular cada API en su módulo. Reutilizamos el patrón OAuth que ya
funciona con Google en el repo, no inventamos uno nuevo.

---

## 1. Realidad de cada plataforma (verificada, agosto 2026)

| Plataforma | API oficial | Tipo de cuenta que necesitas | Qué obtenemos | Trabajo previo tuyo |
|---|---|---|---|---|
| **YouTube Shorts** | YouTube Data API v3 + YouTube Analytics API | Tu cuenta actual sirve (personal o no) | Vistas, retención, suscriptores ganados, likes, comentarios, lista de shorts | Reutiliza tu OAuth de Google ya montado |
| **Instagram** | Instagram Graph API (Meta) | **Creador o Empresa** (personal = 0 acceso desde dic-2024) | Alcance, impresiones, guardados, crecimiento de seguidores, métricas por post, comentarios | Convertir a Creador + vincular página de Facebook (puede estar vacía) |
| **TikTok** | Display API (+ Business API opcional) | Cuenta normal basta para stats básicas | Vistas, likes, comentarios, compartidos y lista de videos por cuenta | Registrar app de desarrollador y esperar aprobación (3-7 días) |

**Decisiones tomadas contigo:**

- **Instagram → convertir a Creador.** Gratis, reversible, no cambia seguidores ni cómo
  te ve el público, y mantiene la biblioteca de música completa (a diferencia de Empresa).
  Sin esto no hay métricas oficiales de ningún tipo.
- **TikTok → se queda como está.** La Display API da las stats básicas por video con tu
  cuenta normal. Business solo haría falta para analítica avanzada, y tiene un costo real
  (te limita a música comercial, pierdes audios en tendencia). Lo dejamos para más
  adelante si de verdad lo necesitas.
- **YouTube → sin cambios.** Tu cuenta actual ya sirve.

---

## 2. Arquitectura propuesta

### Enfoque elegido (A): un módulo por plataforma + helper de auth compartido

Es el que mejor encaja con tu repo, que ya separa `google_tools.py` (las tools que ve el
modelo) de `google_auth.py` (el OAuth y los clientes). Replicamos esa separación.

```
cristopher/
  redes_auth.py                # OAuth + clientes por plataforma y por cuenta (como google_auth.py)
  tools/
    youtube_tools.py           # tools de YouTube que ve Gemini
    instagram_tools.py         # tools de Instagram que ve Gemini
    tiktok_tools.py            # tools de TikTok que ve Gemini
    redes_tools.py             # tools "transversales": resumen agregado de TODAS las cuentas
data/
  redes/                       # gitignored, igual que data/google/
    youtube/<cuenta>/token.json
    instagram/<cuenta>/token.json
    tiktok/<cuenta>/token.json
    credentials/               # los credentials.json de cada app de desarrollador
```

**Por qué A y no otras:**

- **Enfoque B — un solo módulo `redes_tools.py` con abstracción de "proveedor".** Más
  "elegante" en teoría, pero cada API (Google/Meta/TikTok) tiene auth, formatos y límites
  muy distintos; forzar una interfaz común ahora es abstracción prematura. Descartado por
  la regla "menos piezas móviles, sin abstracciones por si acaso".
- **Enfoque C — scraping no oficial (como Baileys en WhatsApp).** Rápido de montar pero
  frágil, contra los términos de uso y con riesgo de bloqueo. Solo tendría sentido si te
  negaras a convertir Instagram, y ya decidimos no ir por ahí. Descartado.

### Almacén multi-cuenta ("muchas cuentas")

El punto clave de tu pedido. Cada plataforma guarda un `token.json` por cuenta, indexado
por un alias que tú eliges (p. ej. `humor`, `fitness`, `principal`). Un pequeño índice
`data/redes/cuentas.json` lista qué cuentas hay y en qué plataforma:

```json
{
  "youtube":   [{"alias": "principal", "canal_id": "UC..."}],
  "instagram": [{"alias": "humor", "ig_user_id": "1784..."}],
  "tiktok":    [{"alias": "fitness", "open_id": "..."}]
}
```

Añadir una cuenta = correr un flujo de consentimiento una vez y se guarda su token. A
partir de ahí Cristopher las recorre todas solo.

---

## 3. Tools nuevas que verá Gemini

Siguiendo tu registro declarativo en `tools/__init__.py` (una entrada = una tool):

| Tool | Qué hace |
|---|---|
| `redes_resumen` | Panorama de TODAS las cuentas y plataformas de un vistazo (seguidores, vistas del periodo, top video). El "que esté al tanto de todo". |
| `redes_metricas_video` | Métricas detalladas de un video/short concreto. |
| `youtube_listar_shorts` | Lista los shorts de un canal con sus vistas/likes. |
| `youtube_metricas_canal` | Vistas, suscriptores ganados, retención de un canal en un rango de fechas. |
| `instagram_listar_publicaciones` | Reels/publicaciones de una cuenta Creador con sus insights. |
| `instagram_metricas_cuenta` | Alcance, impresiones, crecimiento de seguidores. |
| `tiktok_listar_videos` | Videos de una cuenta con vistas/likes/comentarios/compartidos. |
| `redes_cuenta_conectar` | Lanza el consentimiento para añadir una cuenta nueva. |
| `redes_cuentas_listar` | Muestra qué cuentas están conectadas. |

Los nombres explican intención (regla del proyecto) y el modelo nunca miente sobre lo que
puede hacer porque todo sale del mismo registro.

---

## 4. Cambios en config y entorno

En `config.py`, una sección nueva análoga a la de Google:

```python
REDES_DIR = DATA / "redes"
REDES_CUENTAS = REDES_DIR / "cuentas.json"
# YouTube reutiliza el OAuth de Google; solo se añaden scopes de lectura:
#   youtube.readonly + yt-analytics.readonly
# Instagram (Meta) y TikTok llevan su propio credentials.json de app de desarrollador.
```

En `.env.example`, variables opcionales para los IDs de app (los secretos NUNCA en
código, igual que hoy):

```
# Instagram / Meta (app de desarrollador)
META_APP_ID=
META_APP_SECRET=
# TikTok (app de desarrollador)
TIKTOK_CLIENT_KEY=
TIKTOK_CLIENT_SECRET=
```

---

## 5. Lo que tienes que hacer TÚ (yo no puedo, Cristopher corre en tu máquina)

Checklist de prerequisitos, en orden:

1. **Instagram:** convertir cada cuenta a **Creador** (Configuración → Tipo de cuenta) y
   vincularla a una página de Facebook (puede crearse vacía).
2. **Meta for Developers:** crear una app, añadir el producto "Instagram Graph API",
   obtener `App ID` y `App Secret`.
3. **Google Cloud:** en el proyecto que ya usas para Gmail/Calendar, activar *YouTube Data
   API v3* y *YouTube Analytics API*, y añadir los scopes de lectura.
4. **TikTok for Developers:** registrar la app, pedir los scopes `user.info.basic`,
   `user.info.stats`, `video.list`, y esperar aprobación (3-7 días).
5. **Consentir por cuenta:** una vez tenga el código, correr `redes_cuenta_conectar` una
   vez por cada cuenta de cada plataforma.

Yo te doy la guía paso a paso con capturas de dónde hacer clic cuando lleguemos a la
implementación.

---

## 6. Fases de implementación (entregar algo que funcione pronto)

- **Fase 1 — YouTube.** Es la que funciona ya con tu cuenta actual y reutiliza tu OAuth.
  Deja el almacén multi-cuenta y `redes_resumen` montados. Prueba real end-to-end.
- **Fase 2 — Instagram.** Cuando tengas las cuentas en Creador y la app de Meta.
- **Fase 3 — TikTok.** Cuando TikTok apruebe la app.
- **Fase 4 (opcional, futuro) — Publicar/crear contenido.** El almacén de tokens y los
  scopes de escritura se añaden encima sin tocar lo de lectura.

---

## 7. Riesgos y límites a tener en cuenta

- **Cuotas de API.** YouTube Data API tiene cuota diaria; con consultas cacheadas y
  agregadas (no una llamada por video cada vez) sobra para uso personal.
- **Aprobación de TikTok.** Es el cuello de botella de calendario (3-7 días). Por eso va
  al final.
- **Instagram exige la vinculación con Facebook.** Es el paso que más gente olvida; sin la
  página vinculada, la Graph API devuelve vacío.
- **Tokens caducan.** Instagram y TikTok usan tokens de larga duración que hay que
  refrescar; el helper `redes_auth.py` lo maneja solo (fallo explícito si expira sin poder
  refrescar, nunca silencio).

---

## 8. Siguiente paso

Cuando digas, empiezo por la **Fase 1 (YouTube)**: escribo `redes_auth.py`,
`youtube_tools.py`, el almacén multi-cuenta y `redes_resumen`, los registro en
`tools/__init__.py`, actualizo `config.py` y `.env.example`, y te paso la guía de setup de
Google Cloud. Es la que te da resultado visible más rápido.
