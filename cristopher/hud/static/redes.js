/* CRISTOPHER HUD — Modo redes (dashboard YouTube/Instagram/TikTok, ver PLAN "Modo
   redes"): entrada/salida del modo y una rejilla donde CADA CUENTA es su propia
   tarjeta-widget con toda su información (métrica principal, variación desde el
   refresco anterior, sparkline, métricas secundarias, antigüedad de la última
   publicación y sus avisos). Al pasar el ratón por encima, la tarjeta se amplía en
   el sitio y despliega el detalle en vivo; un clic la fija para poder leerla.

   Sin chat propio (se usa el widget flotante de siempre) y sin motor de drag/resize:
   la rejilla es auto-fill y se recoloca sola, porque las cuentas se conectan y
   desconectan y un layout guardado se rompería al cambiar su número. */
(function () {
  "use strict";

  const $ = (id) => document.getElementById(id);
  function esc(s) { return String(s == null ? "" : s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c])); }

  // ---------- Entrada/salida de modo (botón; la entrada por chat llega vía SSE) ----------
  const toggleBtn = $("redes-toggle");
  if (toggleBtn) {
    toggleBtn.addEventListener("click", () => {
      const activo = document.body.dataset.modo === "redes";
      fetch("/modo_redes", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ activo: !activo }),
      }).catch(() => {});
    });
  }
  // Hook que app.js llama al cambiar de modo (botón, chat, o snapshot al recargar).
  // La visibilidad la resuelve el CSS de data-modo y el sondeo corre siempre (igual
  // que /tareas en code.js); lo único que hay que hacer es soltar la tarjeta fijada
  // al salir, para no volver a entrar con una cuenta abierta de la sesión anterior.
  window.__hudRedesSetModo = function (activo) { if (!activo) cerrar(); };

  // ---------- Barra de conversación ----------
  // Mismo /enviar y mismo hilo que la consola normal (que aquí está oculta): esto es
  // un espejo, no una conversación aparte como la de modo code.
  const chatLog = $("redes-chat-log");
  const MAX_LINEAS = 80;   // el historial completo vive en la consola normal
  const PREFIJO = { user: "tú", answer: "CRIS", thought: "···", tool_call: "→", observation: "obs", error: "!!" };

  window.__hudRedesLog = function (e) {
    if (!chatLog) return;
    const d = document.createElement("div");
    d.className = "l " + e.kind;
    d.innerHTML = `<span class="t">${esc(e.hora || "")}</span><b>${esc(PREFIJO[e.kind] || e.kind)}</b> `
      + `<span>${esc(e.text)}</span>`;
    chatLog.appendChild(d);
    while (chatLog.childElementCount > MAX_LINEAS) chatLog.removeChild(chatLog.firstElementChild);
    chatLog.scrollTop = chatLog.scrollHeight;
  };

  const chatForm = $("redes-form");
  if (chatForm) {
    chatForm.addEventListener("submit", (ev) => {
      ev.preventDefault();
      const input = $("redes-entrada");
      const texto = input.value.trim();
      if (!texto) return;
      input.value = "";
      fetch("/enviar", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ texto }),
      }).catch(() => {});
    });
  }

  // ---------- Formato ----------
  const FMT = new Intl.NumberFormat("es", { notation: "compact", maximumFractionDigits: 1 });
  function compacto(n) {
    const v = Number(n);
    return Number.isFinite(v) && n !== "" && n != null ? FMT.format(v) : "?";
  }
  function exacto(n) {
    const v = Number(n);
    return Number.isFinite(v) && n !== "" && n != null ? v.toLocaleString("es") : "";
  }
  function hace(ts) {
    if (!ts) return "";
    const seg = Math.max(0, Math.floor(Date.now() / 1000 - ts));
    if (seg < 60) return "hace instantes";
    const min = Math.floor(seg / 60);
    if (min < 60) return `hace ${min}m`;
    const h = Math.floor(min / 60);
    if (h < 24) return `hace ${h}h`;
    return `hace ${Math.floor(h / 24)}d`;
  }
  // Mismo umbral que redes_dashboard._DIAS_SIN_PUBLICAR_AVISO: por encima, en ámbar.
  const DIAS_AVISO = 14;
  function diasDesde(iso) {
    if (!iso) return null;
    const t = Date.parse(iso);
    return Number.isNaN(t) ? null : Math.max(0, Math.floor((Date.now() - t) / 86400000));
  }

  // Sparkline de seguidores a partir de la serie que manda el backend (tabla
  // redes_historial). Con menos de 2 puntos no hay línea que dibujar.
  function sparkline(serie) {
    if (!Array.isArray(serie) || serie.length < 2) return "";
    const w = 64, h = 18;
    const min = Math.min.apply(null, serie), max = Math.max.apply(null, serie);
    const span = max - min || 1;
    const puntos = serie.map((v, i) => {
      const x = (i / (serie.length - 1)) * (w - 2) + 1;
      const y = h - 1 - ((v - min) / span) * (h - 2);
      return x.toFixed(1) + "," + y.toFixed(1);
    }).join(" ");
    return `<svg class="redes-spark" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" aria-hidden="true">`
      + `<polyline points="${puntos}" fill="none" stroke="var(--acento)" stroke-width="1.2"`
      + ` stroke-linejoin="round" stroke-linecap="round"/></svg>`;
  }

  // ---------- Qué pinta cada plataforma ----------
  const PLATAFORMAS = [
    { id: "youtube", titulo: "YouTube", metrica: "suscriptores", etiqueta: "suscriptores",
      chips: [["vistas_7d", "vistas · 7 d"]] },
    { id: "instagram", titulo: "Instagram", metrica: "seguidores", etiqueta: "seguidores",
      chips: [["reach_7d", "alcance · 7 d"]] },
    { id: "tiktok", titulo: "TikTok", metrica: "seguidores", etiqueta: "seguidores",
      chips: [["likes_totales", "likes"], ["videos", "vídeos"]] },
  ];
  // Solo lo que NO está ya en la cara de la tarjeta: ampliar tiene que AÑADIR
  // información, no repetir la que ya estás mirando (el grueso de lo que aporta el
  // detalle son las publicaciones).
  const CAMPOS_RESUMEN = {
    youtube: [],
    instagram: [["username", "usuario"]],
    tiktok: [],
  };
  const CAMPOS_PUBLICACION = {
    youtube: { titulo: "titulo", fecha: "publicado",
      extra: [["vistas", "vistas"], ["likes", "likes"], ["comentarios", "com."]] },
    instagram: { titulo: "caption", fecha: "fecha",
      extra: [["likes", "likes"], ["comentarios", "com."], ["alcance", "alcance"], ["guardados", "guard."]] },
    tiktok: { titulo: "titulo", fecha: "fecha",
      extra: [["vistas", "vistas"], ["likes", "likes"], ["comentarios", "com."], ["compartidos", "comp."]] },
  };

  // ---------- Tarjetas ----------
  const gridEl = $("redes-grid");
  const resumenEl = $("redes-resumen");
  const avisosEl = $("redes-avisos");
  const actualizadoEl = $("redes-actualizado");

  function delta(d) {
    if (d == null || d === 0) return "";
    const sube = d > 0;
    return `<span class="redes-delta ${sube ? "sube" : "baja"}">${sube ? "▲" : "▼"} ${esc(compacto(Math.abs(d)))}</span>`;
  }

  function cuerpoTarjeta(p, c) {
    if (c.error) return `<div class="redes-error">${esc(c.error)}</div>`;
    const chips = p.chips
      .filter(([k]) => c[k] != null)
      .map(([k, etiqueta]) => `<span><b>${esc(compacto(c[k]))}</b> ${esc(etiqueta)}</span>`)
      .join("");
    const dias = diasDesde(c.ultima_publicacion);
    const frescura = dias == null ? ""
      : `<div class="redes-frescura${dias >= DIAS_AVISO ? " viejo" : ""}">`
        + `última publicación hace ${dias} d</div>`;
    return `
      <div class="redes-metrica">
        <span class="redes-cifra" title="${esc(exacto(c[p.metrica]))}">${esc(compacto(c[p.metrica]))}</span>
        ${delta(c.delta)}
        ${sparkline(c.serie)}
      </div>
      <div class="redes-etiqueta">${esc(p.etiqueta)}</div>
      ${chips ? `<div class="redes-chips">${chips}</div>` : ""}
      ${frescura}`;
  }

  function tarjeta(p, c, tips) {
    const avisos = tips.map((t) => `<div class="redes-aviso">${esc(t.texto)}</div>`).join("");
    // La "cara" va envuelta aparte porque al ampliar la tarjeta pasa a dos columnas:
    // la cara se queda a la izquierda y el detalle se despliega a su derecha.
    return `<article class="redes-tarjeta panel${c.error ? " con-error" : ""}"
        data-plataforma="${esc(c.plataforma)}" data-alias="${esc(c.alias)}">
      <i class="bracket"></i>
      <span class="redes-pin" aria-hidden="true">fijada</span>
      <div class="redes-cara">
        <div class="redes-cabecera">
          <span class="redes-plataforma">${esc(p.titulo)}</span>
          <span class="redes-nombre" title="${esc(c.nombre || c.alias)}">${esc(c.nombre || c.alias)}</span>
          <span class="redes-alias">${esc(c.alias)}</span>
        </div>
        ${cuerpoTarjeta(p, c)}${avisos}
      </div>
    </article>`;
  }

  // Plataforma sin ninguna cuenta: hueco informativo (dice qué falta por conectar,
  // que se sigue haciendo por chat con redes_auth, no desde aquí).
  function fantasma(p) {
    return `<article class="redes-tarjeta panel fantasma" data-plataforma="${esc(p.id)}">
      <div class="redes-cabecera"><span class="redes-plataforma">${esc(p.titulo)}</span></div>
      <div class="redes-sinconectar">sin cuentas conectadas</div>
    </article>`;
  }

  // Los tips del backend ya traen plataforma+alias, así que cada uno se pinta dentro
  // de SU tarjeta en vez de en un panel aparte.
  function agruparTips(tips) {
    const m = new Map();
    (tips || []).forEach((t) => {
      const clave = t.plataforma + "|" + t.alias;
      if (!m.has(clave)) m.set(clave, []);
      m.get(clave).push(t);
    });
    return m;
  }

  function pintar(data) {
    if (!gridEl) return;
    const cuentas = data.cuentas || {};
    const tipsPorCuenta = agruparTips(data.tips);
    let total = 0, seguidores = 0;
    // Las cuentas reales primero y los huecos al final: si no, con una sola cuenta
    // conectada la única tarjeta con datos quedaba enterrada detrás de dos fantasmas.
    const tarjetas = [], huecos = [];
    PLATAFORMAS.forEach((p) => {
      const items = cuentas[p.id] || [];
      if (!items.length) { huecos.push(fantasma(p)); return; }
      total += items.length;
      items.forEach((c) => {
        const n = Number(c[p.metrica]);
        if (Number.isFinite(n)) seguidores += n;
        tarjetas.push(tarjeta(p, c, tipsPorCuenta.get(p.id + "|" + c.alias) || []));
      });
    });
    gridEl.innerHTML = tarjetas.join("") + huecos.join("");
    pintarBarra(data, total, seguidores);
  }

  function pintarBarra(data, total, seguidores) {
    if (resumenEl) {
      resumenEl.textContent = total
        ? `${total} cuenta${total === 1 ? "" : "s"} · ${compacto(seguidores)} seguidores`
        : "ninguna cuenta conectada";
    }
    const tips = data.tips || [];
    if (avisosEl) {
      avisosEl.hidden = !tips.length;
      avisosEl.textContent = `${tips.length} aviso${tips.length === 1 ? "" : "s"}`;
      avisosEl.title = tips.map((t) => t.texto).join("\n");
    }
    if (actualizadoEl) {
      actualizadoEl.textContent = data.actualizado_en ? "actualizado " + hace(data.actualizado_en) : "";
    }
  }

  // ---------- Detalle en vivo ----------
  function dato(k, etiqueta, resumen) {
    const v = resumen[k];
    if (v == null) return "";
    const n = Number(v);
    const numerico = Number.isFinite(n) && v !== "";
    return `<div class="redes-dato"><b title="${esc(numerico ? exacto(n) : "")}">`
      + `${esc(numerico ? compacto(n) : v)}</b><span>${esc(etiqueta)}</span></div>`;
  }

  function pintarPublicacion(plataforma, p) {
    const map = CAMPOS_PUBLICACION[plataforma] || {};
    const titulo = p[map.titulo] || "(sin título)";
    const extra = (map.extra || []).map(([k, etiqueta]) => `${compacto(p[k])} ${etiqueta}`).join(" · ");
    return `<div class="redes-detalle-item">
      <span class="t" title="${esc(titulo)}">${esc(titulo)}</span>
      <span class="m">${esc(extra)}</span>
      <span class="f">${esc(p[map.fecha] || "?")}</span>
    </div>`;
  }

  function pintarDetalle(plataforma, d) {
    if (d.error) return `<div class="redes-detalle"><div class="empty">${esc(d.error)}</div></div>`;
    const resumen = d.resumen || {};
    const datos = (CAMPOS_RESUMEN[plataforma] || []).map(([k, e]) => dato(k, e, resumen)).join("");
    const destacado = plataforma === "youtube" && resumen.top_titulo
      ? `<div class="redes-destacado">más visto: ${esc(resumen.top_titulo)} · ${esc(compacto(resumen.top_vistas))} vistas</div>`
      : "";
    const pubs = d.publicaciones || [];
    const lista = pubs.length
      ? pubs.map((p) => pintarPublicacion(plataforma, p)).join("")
      : '<div class="empty">sin publicaciones</div>';
    return `<div class="redes-detalle">
      ${datos ? `<div class="redes-detalle-resumen">${datos}</div>` : ""}
      ${destacado}
      <div class="redes-detalle-titulo">últimas publicaciones</div>
      <div class="redes-pubs">${lista}</div>
    </div>`;
  }

  // ---------- Ampliación por hover (con clic para fijar) ----------
  // /redes/detalle hace fetch EN VIVO a la API de la plataforma, así que el hover no
  // puede llamar por cada roce del ratón: retardo de intención antes de abrir, caché
  // por cuenta con TTL, y retardo al cerrar para no parpadear entre tarjetas.
  const RETARDO_ABRIR = 280, RETARDO_CERRAR = 220, TTL_CACHE = 90000;
  const cacheDetalle = new Map();   // "plataforma|alias" -> {ts, html}
  let abierta = null;               // {plataforma, alias}
  let fijada = false;
  let tAbrir = null, tCerrar = null;
  let datosPendientes = null;       // sondeo llegado con una tarjeta abierta

  const clave = (o) => o.plataforma + "|" + o.alias;

  function tarjetaDe(plataforma, alias) {
    return Array.from(gridEl.querySelectorAll(".redes-tarjeta")).find(
      (el) => el.dataset.plataforma === plataforma && el.dataset.alias === alias) || null;
  }

  function inyectar(el, html) {
    const viejo = el.querySelector(".redes-detalle");
    if (viejo) viejo.remove();
    el.insertAdjacentHTML("beforeend", html);
  }

  function abrir(el) {
    const plataforma = el.dataset.plataforma, alias = el.dataset.alias;
    if (abierta) cerrarDom();
    abierta = { plataforma, alias };
    el.classList.add("abierta");
    el.classList.toggle("fijada", fijada);

    const k = clave(abierta);
    const guardado = cacheDetalle.get(k);
    if (guardado && Date.now() - guardado.ts < TTL_CACHE) { inyectar(el, guardado.html); return; }

    inyectar(el, '<div class="redes-detalle"><div class="empty">cargando…</div></div>');
    fetch("/redes/detalle?plataforma=" + encodeURIComponent(plataforma) + "&alias=" + encodeURIComponent(alias))
      .then((r) => r.json())
      .then((d) => {
        const html = pintarDetalle(plataforma, d);
        if (!d.error) cacheDetalle.set(k, { ts: Date.now(), html });  // un error no se cachea
        // El ratón puede haber cambiado de tarjeta mientras respondía la API.
        if (abierta && clave(abierta) === k) inyectar(el, html);
      })
      .catch(() => {
        if (abierta && clave(abierta) === k) {
          inyectar(el, '<div class="redes-detalle"><div class="empty">error al cargar el detalle</div></div>');
        }
      });
  }

  function cerrarDom() {
    if (!gridEl) return;
    const el = gridEl.querySelector(".redes-tarjeta.abierta");
    if (!el) return;
    el.classList.remove("abierta", "fijada");
    const d = el.querySelector(".redes-detalle");
    if (d) d.remove();
  }

  function cerrar() {
    clearTimeout(tAbrir); clearTimeout(tCerrar);
    tAbrir = tCerrar = null;
    cerrarDom();
    abierta = null;
    fijada = false;
    if (datosPendientes) { const d = datosPendientes; datosPendientes = null; pintar(d); }
  }

  if (gridEl) {
    gridEl.addEventListener("pointerover", (ev) => {
      if (ev.pointerType === "touch") return;   // en táctil manda el clic
      const el = ev.target.closest(".redes-tarjeta");
      if (!el || el.classList.contains("fantasma")) return;
      clearTimeout(tCerrar); tCerrar = null;
      if (abierta && clave(abierta) === el.dataset.plataforma + "|" + el.dataset.alias) return;
      if (fijada) return;                       // una tarjeta fijada manda hasta soltarla
      clearTimeout(tAbrir);
      tAbrir = setTimeout(() => abrir(el), RETARDO_ABRIR);
    });

    gridEl.addEventListener("pointerout", (ev) => {
      if (ev.pointerType === "touch") return;
      const el = ev.target.closest(".redes-tarjeta");
      if (!el) return;
      if (ev.relatedTarget && el.contains(ev.relatedTarget)) return;  // movimiento interno
      clearTimeout(tAbrir); tAbrir = null;
      if (fijada) return;
      clearTimeout(tCerrar);
      tCerrar = setTimeout(cerrar, RETARDO_CERRAR);
    });

    // Clic = fijar/soltar. Sin esto, mover el ratón para leer las publicaciones
    // cerraría la tarjeta; es además el único camino en táctil (no hay hover).
    gridEl.addEventListener("click", (ev) => {
      const el = ev.target.closest(".redes-tarjeta");
      if (!el || el.classList.contains("fantasma")) return;
      const k = el.dataset.plataforma + "|" + el.dataset.alias;
      if (abierta && clave(abierta) === k) {
        fijada = !fijada;
        el.classList.toggle("fijada", fijada);
        if (!fijada) { clearTimeout(tCerrar); tCerrar = setTimeout(cerrar, RETARDO_CERRAR); }
        return;
      }
      clearTimeout(tAbrir); clearTimeout(tCerrar);
      fijada = true;
      abrir(el);
    });
  }

  document.addEventListener("click", (ev) => {
    if (!fijada || ev.target.closest(".redes-tarjeta")) return;
    cerrar();
  });

  // ---------- Sondeo ----------
  function recibir(d) {
    // Con una tarjeta ampliada NO se repinta la rejilla bajo el ratón: se guarda el
    // sondeo y se aplica al cerrarla (son datos de un ciclo de 30 s, no urgen).
    if (abierta) { datosPendientes = d; return; }
    pintar(d);
  }

  function redesTick() {
    fetch("/redes/dashboard").then((r) => r.json()).then(recibir).catch(() => {});
  }
  redesTick();
  setInterval(redesTick, 30000);

  // ---------- Botón "Actualizar ahora" ----------
  const btnRefrescar = $("redes-refrescar");
  if (btnRefrescar) {
    btnRefrescar.addEventListener("click", () => {
      const previa = abierta, eraFijada = fijada;
      btnRefrescar.disabled = true;
      btnRefrescar.classList.add("girando");
      cacheDetalle.clear();               // aquí sí se quiere dato fresco, no cacheado
      fetch("/redes/refrescar", { method: "POST" })
        .then((r) => r.json())
        .then((d) => {
          datosPendientes = null;
          cerrarDom();
          abierta = null; fijada = false;
          pintar(d);
          if (previa) {
            const el = tarjetaDe(previa.plataforma, previa.alias);
            if (el) { fijada = eraFijada; abrir(el); }
          }
        })
        .catch(() => {})
        .finally(() => { btnRefrescar.disabled = false; btnRefrescar.classList.remove("girando"); });
    });
  }
})();
