/* CRISTOPHER HUD — Fase 1: widgets movibles/redimensionables sobre una cuadrícula
   con imán, con guardado de distribuciones con nombre en localStorage y widgets que
   se pueden ocultar/mostrar. Capa puramente visual: no toca app.js (salvo
   window.__hudCoreResize, ya expuesto allí) ni el cableado SSE/polling. Los 6 paneles
   existentes conservan su id y HTML interno; aquí solo se reposicionan como widgets
   dentro de `.hud`. */
(function () {
  "use strict";

  const COLS = 12, ROWS = 14;
  const STORAGE_KEY = "cristopher-hud-layout";
  const WIDGET_IDS = ["topbar", "left", "core", "right", "nowplaying", "console"];
  const LABELS = {
    topbar: "Topbar", left: "Sistema", core: "Núcleo",
    right: "Panel derecho", nowplaying: "Now playing", console: "Consola",
  };

  // Mínimos por widget (en celdas) para que su contenido no se rompa al encoger.
  const MIN_SIZES = {
    topbar: { w: 6, h: 1 },
    left: { w: 2, h: 5 },
    core: { w: 3, h: 4 },
    right: { w: 3, h: 6 },
    nowplaying: { w: 6, h: 1 },
    console: { w: 4, h: 3 },
  };

  // Distribución original (misma silueta que el grid fijo anterior), sirve de
  // semilla inicial y de "Por defecto" siempre disponible como reset.
  const DEFAULT_LAYOUT = [
    { id: "topbar", x: 0, y: 0, w: 12, h: 1, hidden: false },
    { id: "left", x: 0, y: 1, w: 3, h: 9, hidden: false },
    { id: "core", x: 3, y: 1, w: 5, h: 9, hidden: false },
    { id: "right", x: 8, y: 1, w: 4, h: 9, hidden: false },
    { id: "nowplaying", x: 0, y: 10, w: 12, h: 1, hidden: false },
    { id: "console", x: 0, y: 11, w: 12, h: 3, hidden: false },
  ];

  const WIDGET_EL = {};
  WIDGET_IDS.forEach((id) => { WIDGET_EL[id] = document.querySelector("." + id); });

  const hud = document.querySelector(".hud");
  const toolbarEl = document.getElementById("layout-toolbar");
  const selectEl = document.getElementById("layout-select");
  const deleteBtn = document.getElementById("layout-delete");
  const hiddenListEl = document.getElementById("hidden-list");

  function clamp(v, lo, hi) { return Math.max(lo, Math.min(hi, v)); }
  function cloneLayout(layout) { return layout.map((w) => Object.assign({}, w)); }
  function escapeHtml(s) {
    return String(s == null ? "" : s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  }

  function isValidLayout(layout) {
    if (!Array.isArray(layout) || layout.length !== WIDGET_IDS.length) return false;
    const seen = new Set();
    for (const w of layout) {
      if (!w || typeof w.id !== "string" || seen.has(w.id) || WIDGET_IDS.indexOf(w.id) === -1) return false;
      seen.add(w.id);
      if (![w.x, w.y, w.w, w.h].every(Number.isInteger)) return false;
      if (w.x < 0 || w.y < 0 || w.w < 1 || w.h < 1 || w.x + w.w > COLS || w.y + w.h > ROWS) return false;
      if (w.hidden !== undefined && typeof w.hidden !== "boolean") return false;
    }
    return seen.size === WIDGET_IDS.length;
  }

  // ---------- Persistencia (localStorage) ----------
  // Esquema versionado: {version, layouts:{nombre:[...widgets]}, ultima}
  let store = null;

  function saveStore() {
    try { localStorage.setItem(STORAGE_KEY, JSON.stringify(store)); } catch (_) { /* cuota/privado: se ignora */ }
  }

  function loadStore() {
    let raw = null;
    try { raw = JSON.parse(localStorage.getItem(STORAGE_KEY)); } catch (_) { /* corrupto */ }
    const okBase = raw && raw.version === 1 && raw.layouts && typeof raw.layouts === "object" && isValidLayout(raw.layouts["Por defecto"]);
    if (!okBase) {
      store = { version: 1, layouts: { "Por defecto": cloneLayout(DEFAULT_LAYOUT) }, ultima: "Por defecto" };
    } else {
      const layouts = { "Por defecto": raw.layouts["Por defecto"] };
      for (const nombre in raw.layouts) {
        if (nombre !== "Por defecto" && isValidLayout(raw.layouts[nombre])) layouts[nombre] = raw.layouts[nombre];
      }
      store = { version: 1, layouts, ultima: (layouts[raw.ultima] ? raw.ultima : "Por defecto") };
    }
    saveStore();
  }

  function guardarLayoutComo(nombre) {
    nombre = (nombre || "").trim();
    if (!nombre || nombre === "Por defecto") return;
    store.layouts[nombre] = cloneLayout(activeLayout);
    store.ultima = nombre;
    saveStore();
    refreshToolbar();
  }

  function cargarLayout(nombre) {
    const layout = store.layouts[nombre];
    if (!layout) return;
    activeLayout = cloneLayout(layout);
    applyLayout(activeLayout);
    store.ultima = nombre;
    saveStore();
    window.__hudCoreResize && window.__hudCoreResize();
    refreshToolbar();
  }

  function borrarLayout(nombre) {
    if (nombre === "Por defecto" || !store.layouts[nombre]) return;
    delete store.layouts[nombre];
    if (store.ultima === nombre) store.ultima = "Por defecto";
    saveStore();
    cargarLayout(store.ultima);
  }

  // ---------- Layout activo ----------
  let activeLayout = cloneLayout(DEFAULT_LAYOUT);

  function applyWidget(w) {
    const el = WIDGET_EL[w.id];
    if (!el) return;
    if (w.hidden) { el.style.display = "none"; return; }
    el.style.display = "";
    el.style.gridColumn = (w.x + 1) + " / span " + w.w;
    el.style.gridRow = (w.y + 1) + " / span " + w.h;
  }
  function applyLayout(layout) { layout.forEach(applyWidget); }

  // ---------- Geometría / colisión ----------
  function getCellMetrics() {
    const rect = hud.getBoundingClientRect();
    const cs = getComputedStyle(hud);
    const gap = parseFloat(cs.columnGap || cs.gap) || 0;
    const padL = parseFloat(cs.paddingLeft) || 0, padR = parseFloat(cs.paddingRight) || 0;
    const padT = parseFloat(cs.paddingTop) || 0, padB = parseFloat(cs.paddingBottom) || 0;
    const innerW = rect.width - padL - padR, innerH = rect.height - padT - padB;
    // Paso completo (celda + gap) por columna/fila: innerW = COLS*cellW + (COLS-1)*gap
    // => innerW + gap = COLS*(cellW+gap)  =>  step = (innerW+gap)/COLS
    return { stepW: (innerW + gap) / COLS, stepH: (innerH + gap) / ROWS };
  }

  function collides(a, b) {
    return a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h;
  }
  function isValidPlacement(cand) {
    if (cand.x < 0 || cand.y < 0 || cand.x + cand.w > COLS || cand.y + cand.h > ROWS) return false;
    for (const w of activeLayout) {
      if (w.id !== cand.id && !w.hidden && collides(cand, w)) return false;
    }
    return true;
  }
  // Primer hueco libre (barrido fila a fila) para un tamaño w x h, o null si no cabe.
  function findFreeSlot(w, h) {
    for (let y = 0; y <= ROWS - h; y++) {
      for (let x = 0; x <= COLS - w; x++) {
        if (isValidPlacement({ id: "__probe__", x, y, w, h })) return { x, y };
      }
    }
    return null;
  }

  // ---------- Ocultar / mostrar widgets ----------
  function ocultarWidget(id) {
    const widget = activeLayout.find((w) => w.id === id);
    if (!widget || widget.hidden) return;
    widget.hidden = true;
    applyWidget(widget);
    populateHiddenList();
  }

  function mostrarWidget(id) {
    const widget = activeLayout.find((w) => w.id === id);
    if (!widget || !widget.hidden) return;
    const min = MIN_SIZES[id] || { w: 1, h: 1 };
    const w = Math.max(min.w, widget.w || min.w), h = Math.max(min.h, widget.h || min.h);
    const slot = findFreeSlot(w, h);
    widget.hidden = false;
    widget.w = w; widget.h = h;
    // Si no hay hueco libre exacto, se restaura en su última posición conocida
    // (puede solapar temporalmente; se reubica a mano igual que al arrastrar).
    if (slot) { widget.x = slot.x; widget.y = slot.y; }
    applyWidget(widget);
    if (id === "core") window.__hudCoreResize && window.__hudCoreResize();
    populateHiddenList();
  }

  // ---------- Modo edición ----------
  function setEditMode(on) {
    document.body.dataset.edit = on ? "1" : "0";
    toolbarEl.hidden = !on;
    if (on) refreshToolbar();
  }

  document.getElementById("edit-toggle").addEventListener("click", () => {
    setEditMode(document.body.dataset.edit !== "1");
  });
  document.getElementById("edit-exit").addEventListener("click", () => setEditMode(false));
  document.addEventListener("keydown", (ev) => {
    if (ev.key !== "e" && ev.key !== "E") return;
    const tag = (document.activeElement && document.activeElement.tagName) || "";
    if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || window.innerWidth <= 760) return;
    setEditMode(document.body.dataset.edit !== "1");
  });

  // ---------- Drag / resize (Pointer Events, delegado en .hud) ----------
  // En modo edición, arrastrar desde CUALQUIER punto del panel mueve el widget
  // completo (no hace falta acertar en el asa ⠿); el tirador de la esquina sigue
  // siendo el único punto de redimensionado, y el botón × queda excluido del drag.
  function widgetIdOf(panel) {
    for (const id of WIDGET_IDS) if (panel.classList.contains(id)) return id;
    return null;
  }

  let dragState = null;

  hud.addEventListener("pointerdown", (ev) => {
    if (document.body.dataset.edit !== "1" || window.innerWidth <= 760) return;
    if (ev.target.closest("#edit-toggle") || ev.target.closest(".widget-remove")) return;
    const panel = ev.target.closest(".panel");
    const id = panel && widgetIdOf(panel);
    const widget = id && activeLayout.find((w) => w.id === id && !w.hidden);
    if (!widget) return;
    const resizeHandle = ev.target.closest(".widget-resize");
    ev.preventDefault();
    const captureEl = resizeHandle || panel;
    captureEl.setPointerCapture(ev.pointerId);
    const mode = resizeHandle ? "resize" : "drag";
    panel.classList.add(mode === "drag" ? "dragging" : "resizing");
    dragState = {
      mode, id, handle: captureEl, panel,
      metrics: getCellMetrics(),
      startX: ev.clientX, startY: ev.clientY,
      orig: Object.assign({}, widget),
      min: MIN_SIZES[id] || { w: 1, h: 1 },
    };
    document.addEventListener("pointermove", onPointerMove);
    document.addEventListener("pointerup", onPointerUp);
  });

  function onPointerMove(ev) {
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
      const idx = activeLayout.findIndex((w) => w.id === id);
      activeLayout[idx] = Object.assign({}, activeLayout[idx], cand);
      applyWidget(activeLayout[idx]);
      dragState.panel.classList.remove("invalid-placement");
      if (id === "core") window.__hudCoreResize && window.__hudCoreResize();
    } else {
      dragState.panel.classList.add("invalid-placement");
    }
  }

  function onPointerUp(ev) {
    if (!dragState) return;
    try { dragState.handle.releasePointerCapture(ev.pointerId); } catch (_) { /* ya liberado */ }
    dragState.panel.classList.remove("dragging", "resizing", "invalid-placement");
    document.removeEventListener("pointermove", onPointerMove);
    document.removeEventListener("pointerup", onPointerUp);
    if (dragState.id === "core") window.__hudCoreResize && window.__hudCoreResize();
    dragState = null;
  }

  // Click en el botón × de un widget: lo oculta (no interfiere con el drag de
  // arriba porque el pointerdown ya lo excluye antes de armar dragState).
  hud.addEventListener("click", (ev) => {
    if (document.body.dataset.edit !== "1") return;
    const btn = ev.target.closest(".widget-remove");
    if (!btn) return;
    const panel = btn.closest(".panel");
    const id = panel && widgetIdOf(panel);
    if (id) ocultarWidget(id);
  });

  // ---------- Toolbar de distribuciones ----------
  function populateToolbar() {
    const names = Object.keys(store.layouts).sort((a, b) => (a === "Por defecto" ? -1 : b === "Por defecto" ? 1 : a.localeCompare(b)));
    selectEl.innerHTML = names.map((n) => `<option value="${escapeHtml(n)}"${n === store.ultima ? " selected" : ""}>${escapeHtml(n)}</option>`).join("");
    deleteBtn.disabled = selectEl.value === "Por defecto";
  }

  function populateHiddenList() {
    const hidden = activeLayout.filter((w) => w.hidden);
    if (!hidden.length) { hiddenListEl.innerHTML = '<span class="empty">ninguno</span>'; return; }
    hiddenListEl.innerHTML = hidden.map((w) =>
      `<span class="hidden-chip">${escapeHtml(LABELS[w.id] || w.id)}<button type="button" data-id="${escapeHtml(w.id)}" title="Mostrar">+</button></span>`
    ).join("");
  }

  function refreshToolbar() { populateToolbar(); populateHiddenList(); }

  selectEl.addEventListener("change", () => { deleteBtn.disabled = selectEl.value === "Por defecto"; });
  document.getElementById("layout-load").addEventListener("click", () => cargarLayout(selectEl.value));
  document.getElementById("layout-save").addEventListener("click", () => {
    const nombre = window.prompt("Nombre de la distribución:", "");
    if (nombre !== null) guardarLayoutComo(nombre);
  });
  deleteBtn.addEventListener("click", () => {
    if (selectEl.value === "Por defecto") return;
    if (window.confirm('¿Borrar la distribución "' + selectEl.value + '"?')) borrarLayout(selectEl.value);
  });
  hiddenListEl.addEventListener("click", (ev) => {
    const btn = ev.target.closest("button[data-id]");
    if (btn) mostrarWidget(btn.dataset.id);
  });

  // ---------- Arranque ----------
  function init() {
    loadStore();
    activeLayout = cloneLayout(store.layouts[store.ultima]);
    applyLayout(activeLayout);
    window.__hudCoreResize && window.__hudCoreResize();

    const coreEl = WIDGET_EL.core;
    if (window.ResizeObserver && coreEl) {
      let raf = null;
      new ResizeObserver(() => {
        if (raf) return;
        raf = requestAnimationFrame(() => { raf = null; window.__hudCoreResize && window.__hudCoreResize(); });
      }).observe(coreEl);
    }
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
