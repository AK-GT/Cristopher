/* CRISTOPHER HUD — Modo redes (dashboard YouTube/Instagram/TikTok, ver PLAN "Modo
   redes"): entrada/salida del modo, tarjetas de cuenta (sondeo del caché del
   backend), detalle en vivo al pinchar una cuenta, tips por reglas (ya calculados
   en el backend, aquí solo se pintan) y un motor de drag/resize propio para los 3
   widgets — mismo mecanismo simplificado que el de code.js, sin chat propio: para
   hablar con CRISTOPHER se usa el widget flotante de siempre. */
(function () {
  "use strict";

  const $ = (id) => document.getElementById(id);
  function esc(s) { return String(s == null ? "" : s).replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c])); }

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
  // Nada que hacer aquí: la visibilidad la resuelve el CSS de data-modo; el sondeo
  // de /redes/dashboard corre siempre, igual que /tareas en code.js.
  window.__hudRedesSetModo = function () {};

  // ---------- Tarjetas de cuenta (por plataforma) ----------
  const listaEl = $("redes-lista");
  const actualizadoEl = $("redes-actualizado");
  const PLATAFORMAS = [
    { id: "youtube", titulo: "YouTube" },
    { id: "instagram", titulo: "Instagram" },
    { id: "tiktok", titulo: "TikTok" },
  ];
  let cuentaSeleccionada = null; // {plataforma, alias}

  function metricaPrincipal(c) {
    const n = c.plataforma === "youtube" ? c.suscriptores : c.seguidores;
    const etiqueta = c.plataforma === "youtube" ? "suscriptores" : "seguidores";
    return (n == null ? "?" : n) + " " + etiqueta;
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

  function pintarCuentas(data) {
    if (actualizadoEl) actualizadoEl.textContent = data.actualizado_en ? "actualizado " + hace(data.actualizado_en) : "";
    if (!listaEl) return;
    const cuentas = data.cuentas || {};
    const total = PLATAFORMAS.reduce((n, p) => n + ((cuentas[p.id] || []).length), 0);
    if (!total) { listaEl.innerHTML = '<div class="empty">sin cuentas conectadas</div>'; return; }

    listaEl.innerHTML = PLATAFORMAS.map((p) => {
      const items = cuentas[p.id] || [];
      if (!items.length) return "";
      const tarjetas = items.map((c) => {
        const sel = cuentaSeleccionada && cuentaSeleccionada.plataforma === c.plataforma && cuentaSeleccionada.alias === c.alias;
        const conError = !!c.error;
        const detalle = conError ? c.error : metricaPrincipal(c);
        return `
        <div class="redes-tarjeta${sel ? " sel" : ""}${conError ? " con-error" : ""}"
             data-plataforma="${esc(c.plataforma)}" data-alias="${esc(c.alias)}">
          <b>${esc(c.nombre || c.alias)}</b>
          <small>${esc(c.alias)} · ${esc(detalle)}</small>
        </div>`;
      }).join("");
      return `<div class="redes-plataforma-titulo">${esc(p.titulo)}</div>${tarjetas}`;
    }).join("");

    listaEl.querySelectorAll(".redes-tarjeta").forEach((el) => {
      el.addEventListener("click", () => seleccionarCuenta(el.dataset.plataforma, el.dataset.alias));
    });
  }

  function redesTick() {
    fetch("/redes/dashboard").then((r) => r.json()).then((d) => { pintarCuentas(d); pintarTips(d.tips); }).catch(() => {});
  }
  redesTick();
  setInterval(redesTick, 30000);

  // ---------- Botón "Actualizar ahora" ----------
  const btnRefrescar = $("redes-refrescar");
  if (btnRefrescar) {
    btnRefrescar.addEventListener("click", () => {
      btnRefrescar.disabled = true;
      fetch("/redes/refrescar", { method: "POST" })
        .then((r) => r.json())
        .then((d) => {
          pintarCuentas(d);
          pintarTips(d.tips);
          if (cuentaSeleccionada) seleccionarCuenta(cuentaSeleccionada.plataforma, cuentaSeleccionada.alias);
        })
        .catch(() => {})
        .finally(() => { btnRefrescar.disabled = false; });
    });
  }

  // ---------- Detalle en vivo (al pinchar una tarjeta) ----------
  const detalleEl = $("redes-detalle-cuerpo");

  const CAMPOS_RESUMEN = {
    youtube: [["suscriptores", "suscriptores"], ["vistas_7d", "vistas (7 días)"], ["top_titulo", "vídeo más visto"]],
    instagram: [["username", "usuario"], ["seguidores", "seguidores"], ["reach_7d", "alcance (7 días)"]],
    tiktok: [["display_name", "nombre"], ["seguidores", "seguidores"], ["likes_totales", "likes totales"], ["videos", "vídeos"]],
  };
  const CAMPOS_PUBLICACION = {
    youtube: { titulo: "titulo", fecha: "publicado", extra: ["vistas", "likes", "comentarios"] },
    instagram: { titulo: "caption", fecha: "fecha", extra: ["likes", "comentarios", "alcance", "guardados"] },
    tiktok: { titulo: "titulo", fecha: "fecha", extra: ["vistas", "likes", "comentarios", "compartidos"] },
  };

  function pintarResumen(plataforma, resumen) {
    const campos = CAMPOS_RESUMEN[plataforma] || [];
    return campos.map(([k, etiqueta]) => `<b>${esc(etiqueta)}:</b> ${esc(resumen[k])}`).join("<br>");
  }

  function pintarPublicacion(plataforma, p) {
    const map = CAMPOS_PUBLICACION[plataforma] || {};
    const titulo = p[map.titulo] || "(sin título)";
    const fecha = p[map.fecha] || "?";
    const extra = (map.extra || []).map((k) => `${esc(p[k])} ${esc(k)}`).join(" · ");
    return `<div class="redes-detalle-item"><span class="t">${esc(titulo)}</span><small>${esc(fecha)} · ${extra}</small></div>`;
  }

  function seleccionarCuenta(plataforma, alias) {
    cuentaSeleccionada = { plataforma, alias };
    listaEl && listaEl.querySelectorAll(".redes-tarjeta").forEach((el) => {
      el.classList.toggle("sel", el.dataset.plataforma === plataforma && el.dataset.alias === alias);
    });
    if (!detalleEl) return;
    detalleEl.innerHTML = '<div class="empty">cargando…</div>';
    fetch("/redes/detalle?plataforma=" + encodeURIComponent(plataforma) + "&alias=" + encodeURIComponent(alias))
      .then((r) => r.json())
      .then((d) => {
        if (d.error) { detalleEl.innerHTML = `<div class="empty">${esc(d.error)}</div>`; return; }
        const resumenHtml = `<div class="redes-detalle-resumen">${pintarResumen(plataforma, d.resumen || {})}</div>`;
        const pubs = d.publicaciones || [];
        const pubsHtml = pubs.length
          ? pubs.map((p) => pintarPublicacion(plataforma, p)).join("")
          : '<div class="empty">sin publicaciones</div>';
        detalleEl.innerHTML = resumenHtml + pubsHtml;
      })
      .catch(() => { detalleEl.innerHTML = '<div class="empty">error al cargar el detalle</div>'; });
  }

  // ---------- Tips (calculados en el backend por reglas, aquí solo se pintan) ----------
  const tipsEl = $("redes-tips-lista");
  function pintarTips(tips) {
    if (!tipsEl) return;
    if (!tips || !tips.length) { tipsEl.innerHTML = '<div class="empty">sin avisos</div>'; return; }
    tipsEl.innerHTML = tips.map((t) => `<div class="item">${esc(t.texto)}</div>`).join("");
  }

  // ---------- Motor mínimo de drag/resize para los 3 widgets de redes ----------
  // Calco recortado del widgetsCode() de code.js: mismo mecanismo (Pointer Events,
  // imán a la cuadrícula, colisión), un único layout autoguardado propio.
  (function widgetsRedes() {
    const cont = document.querySelector(".hud-redes");
    if (!cont) return;
    const COLS = 12, ROWS = 13;
    const STORAGE_KEY = "cristopher-hud-layout-redes";
    const IDS = ["redes-cuentas", "redes-detalle", "redes-tips"];
    const MIN = {
      "redes-cuentas": { w: 2, h: 4 }, "redes-detalle": { w: 3, h: 4 }, "redes-tips": { w: 3, h: 2 },
    };
    const DEFAULT = [
      { id: "redes-cuentas", x: 0, y: 0, w: 4, h: 13, hidden: false },
      { id: "redes-detalle", x: 4, y: 0, w: 8, h: 8, hidden: false },
      { id: "redes-tips", x: 4, y: 8, w: 8, h: 5, hidden: false },
    ];
    const EL = {};
    IDS.forEach((id) => { EL[id] = document.querySelector("." + id); });

    function clamp(v, lo, hi) { return Math.max(lo, Math.min(hi, v)); }
    function cloneLayout(l) { return l.map((w) => Object.assign({}, w)); }
    function isValid(layout) {
      if (!Array.isArray(layout) || layout.length !== IDS.length) return false;
      const seen = new Set();
      for (const w of layout) {
        if (!w || typeof w.id !== "string" || seen.has(w.id) || IDS.indexOf(w.id) === -1) return false;
        seen.add(w.id);
        if (![w.x, w.y, w.w, w.h].every(Number.isInteger)) return false;
        if (w.x < 0 || w.y < 0 || w.w < 1 || w.h < 1 || w.x + w.w > COLS || w.y + w.h > ROWS) return false;
      }
      return seen.size === IDS.length;
    }

    let layout;
    try {
      const raw = JSON.parse(localStorage.getItem(STORAGE_KEY));
      layout = isValid(raw) ? raw : cloneLayout(DEFAULT);
    } catch (_) { layout = cloneLayout(DEFAULT); }

    function save() {
      try { localStorage.setItem(STORAGE_KEY, JSON.stringify(layout)); } catch (_) { /* cuota/privado: se ignora */ }
    }

    function applyWidget(w) {
      const el = EL[w.id];
      if (!el) return;
      el.style.display = w.hidden ? "none" : "";
      el.style.gridColumn = (w.x + 1) + " / span " + w.w;
      el.style.gridRow = (w.y + 1) + " / span " + w.h;
    }
    function applyLayout() { layout.forEach(applyWidget); }
    applyLayout();

    function collides(a, b) {
      return a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h;
    }
    function isValidPlacement(cand) {
      if (cand.x < 0 || cand.y < 0 || cand.x + cand.w > COLS || cand.y + cand.h > ROWS) return false;
      for (const w of layout) if (w.id !== cand.id && !w.hidden && collides(cand, w)) return false;
      return true;
    }

    function getCellMetrics() {
      const rect = cont.getBoundingClientRect();
      const cs = getComputedStyle(cont);
      const gap = parseFloat(cs.columnGap || cs.gap) || 0;
      const padL = parseFloat(cs.paddingLeft) || 0, padR = parseFloat(cs.paddingRight) || 0;
      const padT = parseFloat(cs.paddingTop) || 0, padB = parseFloat(cs.paddingBottom) || 0;
      const innerW = rect.width - padL - padR, innerH = rect.height - padT - padB;
      return { stepW: (innerW + gap) / COLS, stepH: (innerH + gap) / ROWS };
    }

    function widgetIdOf(panel) {
      for (const id of IDS) if (panel.classList.contains(id)) return id;
      return null;
    }

    let dragState = null;
    cont.addEventListener("pointerdown", (ev) => {
      if (document.body.dataset.edit !== "1" || window.innerWidth <= 760) return;
      if (ev.target.closest(".widget-remove")) return;
      const panel = ev.target.closest(".panel");
      const id = panel && widgetIdOf(panel);
      const widget = id && layout.find((w) => w.id === id && !w.hidden);
      if (!widget) return;
      const resizeHandle = ev.target.closest(".widget-resize");
      ev.preventDefault();
      const captureEl = resizeHandle || panel;
      captureEl.setPointerCapture(ev.pointerId);
      const mode = resizeHandle ? "resize" : "drag";
      panel.classList.add(mode === "drag" ? "dragging" : "resizing");
      dragState = {
        mode, id, handle: captureEl, panel,
        metrics: getCellMetrics(), startX: ev.clientX, startY: ev.clientY,
        orig: Object.assign({}, widget), min: MIN[id] || { w: 1, h: 1 },
      };
      document.addEventListener("pointermove", onMove);
      document.addEventListener("pointerup", onUp);
    });

    function onMove(ev) {
      if (!dragState) return;
      const { mode, id, metrics, orig, min } = dragState;
      const dCol = Math.round((ev.clientX - dragState.startX) / metrics.stepW);
      const dRow = Math.round((ev.clientY - dragState.startY) / metrics.stepH);
      let cand;
      if (mode === "drag") {
        cand = { id, x: clamp(orig.x + dCol, 0, COLS - orig.w), y: clamp(orig.y + dRow, 0, ROWS - orig.h), w: orig.w, h: orig.h };
      } else {
        const w = Math.min(Math.max(min.w, orig.w + dCol), COLS - orig.x);
        const h = Math.min(Math.max(min.h, orig.h + dRow), ROWS - orig.y);
        cand = { id, x: orig.x, y: orig.y, w, h };
      }
      if (isValidPlacement(cand)) {
        const idx = layout.findIndex((w) => w.id === id);
        layout[idx] = Object.assign({}, layout[idx], cand);
        applyWidget(layout[idx]);
        dragState.panel.classList.remove("invalid-placement");
      } else {
        dragState.panel.classList.add("invalid-placement");
      }
    }
    function onUp(ev) {
      if (!dragState) return;
      try { dragState.handle.releasePointerCapture(ev.pointerId); } catch (_) { /* ya liberado */ }
      dragState.panel.classList.remove("dragging", "resizing", "invalid-placement");
      document.removeEventListener("pointermove", onMove);
      document.removeEventListener("pointerup", onUp);
      save();
      dragState = null;
    }

    cont.addEventListener("click", (ev) => {
      if (document.body.dataset.edit !== "1") return;
      const btn = ev.target.closest(".widget-remove");
      if (!btn) return;
      const panel = btn.closest(".panel");
      const id = panel && widgetIdOf(panel);
      const w = id && layout.find((x) => x.id === id);
      if (w) { w.hidden = true; applyWidget(w); save(); }
    });
  })();
})();
