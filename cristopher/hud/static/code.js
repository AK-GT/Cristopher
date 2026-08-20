/* CRISTOPHER HUD — "Cristopher Code" (Fase 1): entrada/salida del modo, chat propio
   (mismo /enviar que el modo normal — el servidor decide el buffer de conversación
   según el flag activo), sesiones (lista + sesión activa, reutilizando /sesiones*),
   tareas (solo lectura) y un motor de drag/resize propio y deliberadamente simple
   para los 4 widgets (un único layout autoguardado, sin distribuciones con nombre
   como el modo normal). Ver CLAUDE.md (modo code). */
(function () {
  "use strict";

  const $ = (id) => document.getElementById(id);
  function esc(s) { return String(s == null ? "" : s).replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c])); }

  // ---------- Entrada/salida de modo (botón; la entrada por chat llega vía SSE) ----------
  const toggleBtn = $("code-toggle");
  if (toggleBtn) {
    toggleBtn.addEventListener("click", () => {
      const activo = document.body.dataset.modo === "code";
      fetch("/modo_code", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ activo: !activo }),
      }).catch(() => {});
    });
  }
  // Hook que app.js llama al cambiar de modo (botón, chat, o snapshot al recargar).
  // Nada que hacer aquí hoy: la visibilidad la resuelve el CSS de data-modo y los
  // widgets ya están alimentados por polling/SSE independientemente del modo activo.
  window.__hudCodeSetModo = function () {};

  // ---------- Chat de código ----------
  const codeLog = $("code-log");
  function addLogCode(e) {
    if (!codeLog) return;
    const d = document.createElement("div");
    d.className = "l " + e.kind;
    const pref = { user: "tú", answer: "CRIS", thought: "···", tool_call: "→", observation: "obs", error: "!!" }[e.kind] || e.kind;
    d.innerHTML = `<span class="t">${esc(e.hora || "")}</span><b>${pref}</b> <span>${esc(e.text)}</span>`;
    codeLog.appendChild(d);
    codeLog.scrollTop = codeLog.scrollHeight;
  }
  window.__hudCodeLog = addLogCode;

  // Helper reutilizado por el form de chat y los atajos del widget Asesor: ambos
  // mandan al mismo /enviar (mismo agente, mismo buffer de conversación de code).
  function enviarChat(texto) {
    texto = (texto || "").trim();
    if (!texto) return;
    fetch("/enviar", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ texto }),
    }).catch(() => {});
  }

  const codeForm = $("code-form");
  if (codeForm) {
    codeForm.addEventListener("submit", (ev) => {
      ev.preventDefault();
      const input = $("code-entrada");
      const texto = input.value.trim();
      if (!texto) return;
      input.value = "";
      enviarChat(texto);
    });
  }

  // ---------- Sesiones: lista + sesión activa ----------
  const listaEl = $("code-sesiones-lista");
  const transcriptEl = $("code-transcript");
  const ICONOS_ESTADO = { arrancando: "🟡", trabajando: "🔧", esperando: "🟢", pregunta: "❓", terminado: "⚪", error: "🔴" };
  const ESTADOS_FINALES = ["terminado", "error"];
  let sesionSeleccionada = null;
  let sesionesCache = [];

  // ---------- Cronómetro por sesión (desde creado_ts; congelado si ya terminó) ----------
  function fmtDuracion(seg) {
    seg = Math.max(0, Math.floor(seg));
    const h = Math.floor(seg / 3600), m = Math.floor((seg % 3600) / 60), s = seg % 60;
    if (h) return `${h}h ${m}m`;
    if (m) return `${m}m ${s}s`;
    return `${s}s`;
  }
  function duracionTexto(s) {
    if (s.creado_ts == null) return "";
    const fin = ESTADOS_FINALES.includes(s.estado) ? (s.ultima_actividad_ts || s.creado_ts) : (Date.now() / 1000);
    return fmtDuracion(fin - s.creado_ts);
  }
  setInterval(() => {
    if (!listaEl) return;
    listaEl.querySelectorAll(".code-tiempo").forEach((el) => {
      const s = sesionesCache.find((x) => x.nombre === el.dataset.nombre);
      if (s) el.textContent = duracionTexto(s);
    });
  }, 1000);

  function actualizarControles() {
    const activa = !!sesionSeleccionada;
    const mensaje = $("code-sesion-mensaje"), form = $("code-sesion-form-escribir"), parar = $("code-sesion-parar");
    if (mensaje) mensaje.disabled = !activa;
    if (form) form.querySelector('button[type="submit"]').disabled = !activa;
    if (parar) parar.disabled = !activa;
  }

  function cargarTranscript(nombre) {
    if (!transcriptEl) return;
    fetch("/sesiones/leer?nombre=" + encodeURIComponent(nombre) + "&n=200")
      .then((r) => r.json())
      .then((d) => {
        const entradas = (d && d.transcript) || [];
        transcriptEl.innerHTML = "";
        if (!entradas.length) { transcriptEl.innerHTML = '<div class="empty">todavía sin transcript</div>'; return; }
        entradas.forEach((e) => transcriptEl.appendChild(pintarEntrada(e)));
        transcriptEl.scrollTop = transcriptEl.scrollHeight;
      })
      .catch(() => {});
  }

  function pintarEntrada(e) {
    const d = document.createElement("div");
    d.className = "e";
    d.innerHTML = `<span class="t">${esc(e.ts || "")}</span><b>${esc(e.rol)}:</b> ${esc(e.texto)}`;
    return d;
  }

  function seleccionarSesion(nombre) {
    sesionSeleccionada = nombre;
    pintarSesionesCode(sesionesCache);
    actualizarControles();
    cargarTranscript(nombre);
    if (tabActiva === "diff") cargarDiff(nombre);
  }

  // ---------- Pestañas Transcript/Diff del widget "Sesión activa" ----------
  const diffEl = $("code-diff");
  const tabTranscriptBtn = $("code-tab-transcript");
  const tabDiffBtn = $("code-tab-diff");
  let tabActiva = "transcript";

  function mostrarTab(tab) {
    tabActiva = tab;
    if (tabTranscriptBtn) tabTranscriptBtn.classList.toggle("sel", tab === "transcript");
    if (tabDiffBtn) tabDiffBtn.classList.toggle("sel", tab === "diff");
    if (transcriptEl) transcriptEl.hidden = tab !== "transcript";
    if (diffEl) diffEl.hidden = tab !== "diff";
    if (tab === "diff" && sesionSeleccionada) cargarDiff(sesionSeleccionada);
  }
  if (tabTranscriptBtn) tabTranscriptBtn.addEventListener("click", () => mostrarTab("transcript"));
  if (tabDiffBtn) tabDiffBtn.addEventListener("click", () => mostrarTab("diff"));

  // Resaltado +/- simple (sin librería de sintaxis): colorea por prefijo de línea.
  function pintarDiff(texto) {
    if (!diffEl) return;
    if (!texto || !texto.trim()) {
      diffEl.innerHTML = '<div class="empty">sin cambios sin commitear</div>';
      return;
    }
    diffEl.innerHTML = texto.split("\n").map((linea) => {
      let clase = "d-ctx";
      if (linea.startsWith("+++") || linea.startsWith("---")) clase = "d-hunk";
      else if (linea.startsWith("@@")) clase = "d-hunk";
      else if (linea.startsWith("+")) clase = "d-add";
      else if (linea.startsWith("-")) clase = "d-del";
      return `<div class="${clase}">${esc(linea) || "&nbsp;"}</div>`;
    }).join("");
  }

  function cargarDiff(nombre) {
    if (!diffEl) return;
    fetch("/sesiones/diff?nombre=" + encodeURIComponent(nombre))
      .then((r) => r.json())
      .then((d) => {
        if (d && d.error) { diffEl.innerHTML = `<div class="empty">${esc(d.error)}</div>`; return; }
        pintarDiff((d && d.diff) || "");
      })
      .catch(() => {});
  }

  function pintarSesionesCode(list) {
    sesionesCache = list || [];
    if (!listaEl) return;
    if (!sesionesCache.length) { listaEl.innerHTML = '<div class="empty">sin sesiones</div>'; return; }
    listaEl.innerHTML = sesionesCache.map((s) => {
      const detalle = [s.tipo === "chat" ? "chat" : null, s.modelo, s.uso_contexto_pct != null ? `${Math.round(s.uso_contexto_pct * 100)}% ctx` : null]
        .filter(Boolean).join(" · ");
      return `
      <div class="item${s.nombre === sesionSeleccionada ? " sel" : ""}" data-nombre="${esc(s.nombre)}">
        <b>${ICONOS_ESTADO[s.estado] || "•"} ${esc(s.nombre)} [${esc(s.estado)}]</b>
        <small>${esc(s.ruta)}${detalle ? " · " + esc(detalle) : ""} · <span class="code-tiempo" data-nombre="${esc(s.nombre)}">${esc(duracionTexto(s))}</span></small>
      </div>`;
    }).join("");
    listaEl.querySelectorAll(".item").forEach((el) => {
      el.addEventListener("click", () => seleccionarSesion(el.dataset.nombre));
    });
    if (sesionSeleccionada && !sesionesCache.some((s) => s.nombre === sesionSeleccionada)) {
      sesionSeleccionada = null;
      actualizarControles();
    }
  }
  window.__hudCodeSesiones = pintarSesionesCode;

  window.__hudCodeSesionEntrada = function (datos) {
    if (!transcriptEl || !datos || datos.nombre !== sesionSeleccionada) return;
    if (transcriptEl.querySelector(".empty")) transcriptEl.innerHTML = "";
    transcriptEl.appendChild(pintarEntrada(datos.entrada));
    transcriptEl.scrollTop = transcriptEl.scrollHeight;
  };

  // tipo=chat no usa proyecto real: oculta ruta/worktree/quality-gate (no aplican,
  // ver sesiones.py::crear) en vez de mandarlos vacíos y confundir al usuario.
  const tipoSel = $("code-tipo");
  const rutaInput = $("code-ruta");
  const worktreeLabel = document.querySelector(".code-worktree-label");
  const qualityGateInput = $("code-quality-gate");
  function actualizarCamposTipo() {
    const esChat = tipoSel && tipoSel.value === "chat";
    if (rutaInput) { rutaInput.required = !esChat; rutaInput.hidden = esChat; }
    if (worktreeLabel) worktreeLabel.hidden = esChat;
    if (qualityGateInput) qualityGateInput.hidden = esChat;
  }
  if (tipoSel) { tipoSel.addEventListener("change", actualizarCamposTipo); actualizarCamposTipo(); }

  const formCrear = $("code-form-crear");
  if (formCrear) {
    formCrear.addEventListener("submit", (ev) => {
      ev.preventDefault();
      const tipo = tipoSel ? tipoSel.value : "code";
      const nombre = $("code-nombre").value.trim(), tarea = $("code-tarea").value.trim();
      const ruta = rutaInput ? rutaInput.value.trim() : "";
      if (!nombre || !tarea || (tipo === "code" && !ruta)) return;
      const worktreeChk = $("code-aislamiento-worktree");
      const aislamiento = (worktreeChk && worktreeChk.checked) ? "worktree" : "directo";
      const modelo = $("code-modelo") ? $("code-modelo").value : "sonnet";
      const quality_gate = qualityGateInput ? qualityGateInput.value.trim() : "";
      fetch("/sesiones/crear", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ nombre, ruta, tarea, aislamiento, tipo, modelo, quality_gate }),
      }).then((r) => r.json()).then((d) => {
        if (d.ok) {
          $("code-nombre").value = ""; if (rutaInput) rutaInput.value = ""; $("code-tarea").value = "";
          if (worktreeChk) worktreeChk.checked = false;
          if (qualityGateInput) qualityGateInput.value = "";
        } else alert(d.error || "No se pudo crear la sesión.");
      }).catch(() => {});
    });
  }

  const formEscribir = $("code-sesion-form-escribir");
  if (formEscribir) {
    formEscribir.addEventListener("submit", (ev) => {
      ev.preventDefault();
      const input = $("code-sesion-mensaje"); const mensaje = input.value.trim();
      if (!mensaje || !sesionSeleccionada) return;
      input.value = "";
      fetch("/sesiones/escribir", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ nombre: sesionSeleccionada, mensaje }),
      }).catch(() => {});
    });
  }

  const btnParar = $("code-sesion-parar");
  if (btnParar) {
    btnParar.addEventListener("click", () => {
      if (!sesionSeleccionada) return;
      fetch("/sesiones/parar", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ nombre: sesionSeleccionada }),
      }).catch(() => {});
    });
  }
  actualizarControles();

  // ---------- Tareas: tablero con drag & drop (Fase 2) ----------
  const COLUMNAS = ["pendiente", "en_proceso", "en_revision", "hecho"];
  const colEl = {};
  COLUMNAS.forEach((estado) => { colEl[estado] = $("code-col-" + estado); });

  // "Usar en nueva sesión": rellena el formulario de creación con el texto de la
  // tarea (pura conveniencia de UI, sin guardar ninguna relación tarea↔sesión).
  function usarTareaEnSesion(texto) {
    const tareaInput = $("code-tarea");
    if (tareaInput) tareaInput.value = texto;
    const nombreInput = $("code-nombre");
    if (nombreInput && !nombreInput.value) nombreInput.focus();
    else if (tareaInput) tareaInput.focus();
  }

  function moverTarea(id, estado) {
    fetch("/tareas/estado", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id, estado }),
    }).then(tareasTick).catch(() => {});
  }

  function pintarTarjeta(t) {
    const d = document.createElement("div");
    d.className = "code-tarjeta";
    d.draggable = true;
    d.dataset.id = t.id;
    d.innerHTML = `${esc(t.texto)}<button type="button">usar en nueva sesión</button>`;
    d.addEventListener("dragstart", (ev) => {
      ev.dataTransfer.setData("text/plain", String(t.id));
      d.classList.add("dragging");
    });
    d.addEventListener("dragend", () => d.classList.remove("dragging"));
    d.querySelector("button").addEventListener("click", () => usarTareaEnSesion(t.texto));
    return d;
  }

  function pintarTareas(d) {
    const list = (d && d.tareas) || [];
    COLUMNAS.forEach((estado) => {
      const el = colEl[estado];
      if (!el) return;
      el.innerHTML = "";
      const propias = list.filter((t) => t.estado === estado);
      if (!propias.length) { el.innerHTML = '<div class="empty">—</div>'; return; }
      propias.forEach((t) => el.appendChild(pintarTarjeta(t)));
    });
  }

  function tareasTick() {
    fetch("/tareas").then((r) => r.json()).then(pintarTareas).catch(() => {});
  }
  tareasTick();
  setInterval(tareasTick, 5000);

  // Zonas de soltar: cada columna acepta la tarjeta arrastrada y llama /tareas/estado.
  COLUMNAS.forEach((estado) => {
    const el = colEl[estado];
    if (!el) return;
    el.addEventListener("dragover", (ev) => {
      ev.preventDefault();
      el.closest(".code-columna").classList.add("drop-over");
    });
    el.addEventListener("dragleave", () => {
      el.closest(".code-columna").classList.remove("drop-over");
    });
    el.addEventListener("drop", (ev) => {
      ev.preventDefault();
      el.closest(".code-columna").classList.remove("drop-over");
      const id = parseInt(ev.dataTransfer.getData("text/plain"), 10);
      if (id) moverTarea(id, estado);
    });
  });

  // ---------- Asesor: conflictos (calculado) + atajos al chat ----------
  const conflictosEl = $("code-conflictos");
  function pintarConflictos(d) {
    if (!conflictosEl) return;
    const list = (d && d.conflictos) || [];
    if (!list.length) { conflictosEl.innerHTML = '<div class="empty">sin conflictos</div>'; return; }
    conflictosEl.innerHTML = list.map((c) =>
      `<div class="item">⚠ '${esc(c.sesion_a)}' y '${esc(c.sesion_b)}' tocan: ${esc(c.archivos.join(", "))}</div>`
    ).join("");
  }
  function conflictosTick() {
    fetch("/sesiones/conflictos").then((r) => r.json()).then(pintarConflictos).catch(() => {});
  }
  conflictosTick();
  setInterval(conflictosTick, 5000);

  const btnPrioridades = $("code-btn-prioridades");
  if (btnPrioridades) {
    btnPrioridades.addEventListener("click", () => enviarChat("¿En qué sesión debería enfocarme ahora?"));
  }
  const btnRevisar = $("code-btn-revisar");
  if (btnRevisar) {
    btnRevisar.addEventListener("click", () => {
      if (!sesionSeleccionada) { alert("Selecciona antes una sesión en el widget Sesiones."); return; }
      enviarChat(`Revisa el diff de la sesión '${sesionSeleccionada}' y dame tu veredicto.`);
    });
  }
  const btnDiscutir = $("code-btn-discutir");
  if (btnDiscutir) {
    btnDiscutir.addEventListener("click", () => {
      if (!sesionSeleccionada) { alert("Selecciona antes una sesión en el widget Sesiones."); return; }
      enviarChat(`Discute el trabajo de la sesión '${sesionSeleccionada}' con una sesión crítica y muéstrame ambas posturas.`);
    });
  }

  // ---------- Motor mínimo de drag/resize para los 4 widgets de código ----------
  // Deliberadamente más simple que widgets.js: un único layout autoguardado (sin
  // distribuciones con nombre), mismo patrón de interacción (Pointer Events, imán a
  // la cuadrícula, colisión). Satisface "cada modo guarda su propio layout" sin
  // duplicar la UI de distribuciones con nombre del modo normal.
  (function widgetsCode() {
    const cont = document.querySelector(".hud-code");
    if (!cont) return;
    const COLS = 12, ROWS = 13;
    const STORAGE_KEY = "cristopher-hud-layout-code";
    const IDS = ["code-sesiones", "code-chat", "code-activa", "code-asesor", "code-tareas"];
    const MIN = {
      "code-sesiones": { w: 2, h: 4 }, "code-chat": { w: 3, h: 4 },
      "code-activa": { w: 2, h: 3 }, "code-asesor": { w: 2, h: 3 },
      "code-tareas": { w: 4, h: 3 },
    };
    const DEFAULT = [
      { id: "code-sesiones", x: 0, y: 0, w: 3, h: 8, hidden: false },
      { id: "code-chat", x: 3, y: 0, w: 6, h: 8, hidden: false },
      { id: "code-activa", x: 9, y: 0, w: 3, h: 8, hidden: false },
      { id: "code-asesor", x: 0, y: 8, w: 4, h: 5, hidden: false },
      { id: "code-tareas", x: 4, y: 8, w: 8, h: 5, hidden: false },
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
