# CRISTOPHER — Módulo de Música (spec para Claude Code)

> Mini-fase **autocontenida**, a construir **después** del core (fases 1–8 verdes). Lee `CLAUDE.md` primero y respétalo. Diagnostica y enséñame el plan antes de picar código; no reescribas el resto del proyecto.

---

## 0. Objetivo

Un sistema de música **dentro de CRISTOPHER**: reproducir **cualquier canción bajo demanda**, más cola, listas de reproducción, favoritos y controles (pausa, siguiente, anterior, volumen), con un **panel visual integrado en el HUD**. Todo propio — sin apps externas, sin Spotify, sin depender de servicios de pago.

El usuario controla la música **por voz o texto, por intención** ("pon algo tranquilo", "guarda esta", "salta", "pon mi lista de estudiar"). El LLM elige la herramienta; **sin keywords**.

---

## 1. Decisiones fijadas (no re-litigar sin avisar)

- **Motor de reproducción:** VLC vía `python-vlc`. Control fino, cualquier formato, robusto en Windows.
- **Fuente "cualquier canción":** `yt-dlp` resuelve y extrae el audio de la web; VLC lo reproduce.
- **Biblioteca local** también soportada (archivos del usuario).
- **Persistencia:** reutilizar el **SQLite existente** (el de la memoria, Fase 2). **No** crear una base de datos nueva.
- **Herramientas elegidas por INTENCIÓN**, coherente con el resto del agente.
- **Reproductor persistente en hilo de fondo, no bloqueante** (la música sigue mientras CRISTOPHER hace otras cosas).

---

## 2. Arquitectura

- **Servicio reproductor persistente** (singleton en un hilo de fondo). Es el "cerebro musical" y mantiene:
  - Estado: qué suena, pausa/reproducción, volumen.
  - **Cola**: lista de pistas + índice actual.
  - Al terminar una pista, **avanza sola** a la siguiente de la cola.
- **Capa de resolución:** `consulta` → busca en biblioteca local o, si no, resuelve con `yt-dlp` → entrega archivo/stream a VLC.
- **Snapshot de estado** expuesto para el HUD (qué suena, progreso, cola, volumen, fuente): el panel lo lee por polling o eventos.
- **Manejo de errores** en toda la capa: canción no encontrada, red caída, formato no soportado → degrada con mensaje claro al usuario, **nunca crashea** el agente.

---

## 3. Modelo de datos (en el SQLite existente)

- `favoritos` — id, título, artista, fuente/URL, fecha_añadido.
- `listas` — id, nombre, fecha_creada.
- `lista_canciones` — lista_id, título, artista, fuente/URL, **orden** (posición en la lista).
- `historial` — título, artista, fuente/URL, fecha_reproducido.

---

## 4. Herramientas expuestas al LLM

Descríbelas por **propósito**, no por implementación. Selección por intención, sin keywords.

- **Reproducción:** `reproducir(consulta)`, `pausar()`, `reanudar()`, `siguiente()`, `anterior()`, `volumen(nivel)`, `que_suena()`.
- **Cola:** `ver_cola()`, `añadir_a_cola(consulta)`, `quitar_de_cola(pos)`, `vaciar_cola()`.
- **Favoritos:** `añadir_favorito()`, `quitar_favorito(id)`, `listar_favoritos()`, `reproducir_favoritos()`.
- **Listas:** `crear_lista(nombre)`, `añadir_a_lista(nombre, consulta)`, `quitar_de_lista(nombre, pos)`, `reproducir_lista(nombre)`, `listar_listas()`.

---

## 5. Panel visual en el HUD (integración con la estética existente)

Un panel **"NOW PLAYING"** dentro del HUD, coherente con el lenguaje visual ya definido del proyecto — no una interfaz aparte.

**Estética (heredada del HUD):**
- Base casi negra / azul carbón (`#080B12`–`#0E1420`). Acento cian eléctrico (`#22D3EE`, `#2FF3E0`) con moderación.
- Líneas finas 1px, corchetes de esquina, mucho espacio negativo — minimalista.
- Tipografía: `JetBrains Mono` para datos (tiempos, título) y versalitas con tracking para etiquetas.

**Contenido del panel:**
- **Título + artista** de lo que suena.
- **Estado** (▶ / ⏸) y **fuente** (local / web) como indicador discreto.
- **Barra de progreso** fina cian con tiempo actual / total.
- **Volumen** y **cola** (próximas pistas) de forma compacta.
- **Controles** minimalistas con iconos finos: anterior · play/pausa · siguiente; opcionalmente clic en la barra para saltar.

**Elemento "vivo":**
- Un **visualizador de audio minimalista** — barras/onda cian que reaccionan al audio real (o un pulso sutil sincronizado con el ritmo). Discreto, nunca chillón. Puede **latir en armonía con el núcleo central** de CRISTOPHER para que se sienta parte del mismo organismo.

**Reglas:**
- **Estado real, no decorado:** lee del snapshot del reproductor y se actualiza al cambiar pista, pausa o volumen.
- **En reposo** (sin música): estado discreto y elegante, no un hueco vacío.
- **Movimiento sutil:** transición suave al cambiar de tema; el visualizador respira.

---

## 6. Integración con voz (anotar para la Fase 9, no ahora)

- **Ducking:** cuando CRISTOPHER hable (Piper), la música baja sola y vuelve a subir al terminar. Es pulido — déjalo anotado, no lo construyas en esta mini-fase.

---

## 7. Criterio de aceptación

La mini-fase está verde cuando **todo** esto funciona:

1. "pon \[canción]" suena **de punta a punta** desde la web (yt-dlp → VLC).
2. Reproduce también un **archivo local**.
3. **Cola:** añades varias, `siguiente`/`anterior` funcionan, y al acabar una **avanza sola**.
4. **Favoritos:** añadir, listar y reproducir.
5. **Listas:** crear, añadir y reproducir por nombre.
6. La música **sigue sonando** mientras CRISTOPHER hace otra tarea (no bloqueante).
7. El **panel del HUD** muestra el estado **real** y se actualiza (título, progreso, pausa, volumen, cola).
8. La elección de herramienta sale **por intención, sin keywords**.

---

## 8. Seguridad, permisos y gotchas

- **yt-dlp / "cualquier canción"** es zona gris respecto a los términos de servicio de YouTube — es una decisión consciente ya tomada por el usuario. No añadas descargas masivas ni nada fuera de la reproducción bajo demanda.
- **Nunca** credenciales ni cuentas de terceros.
- **Windows:** requiere VLC instalado (para `libvlc` que usa `python-vlc`); mantener `yt-dlp` actualizable (se rompe si YouTube cambia). Verifícalo en el plan, no lo asumas.
- Cualquier acción destructiva sobre listas/favoritos (borrar, vaciar) se ejecuta directa por ser datos locales del usuario; no requiere confirmación, pero sé claro en el mensaje de lo que has hecho.

---

## 9. Orden de construcción (por tandas — verde antes de avanzar)

1. **Reproductor persistente** + `reproducir(consulta)` (web y local). *Prueba:* suena de punta a punta.
2. **Cola** + `siguiente`/`anterior` + auto-avance. *Prueba:* navegación y encadenado.
3. **SQLite:** favoritos + listas + historial. *Prueba:* persisten tras reiniciar.
4. **Panel HUD** con estado real + visualizador. *Prueba:* refleja lo que suena y se actualiza.
5. Anota el **ducking** para la Fase 9.

Recuerda la esencia: **llega a la solución aunque no sea perfecta.** MVP que suene primero; el pulido, después.
