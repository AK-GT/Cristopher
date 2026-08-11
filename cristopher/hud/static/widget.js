/* CRISTOPHER — widget flotante (Fase 3): mismo backend/SSE/enviar que el HUD
   grande (cero estado duplicado, ver hud/__main__.py::bus). Suscripción SSE propia
   e independiente — bus.suscribir() ya soporta varios oyentes a la vez. */
(function () {
  "use strict";

  const $ = (id) => document.getElementById(id);
  function esc(s) { return String(s == null ? "" : s).replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c])); }

  const logEl = $("w-log");
  const statusEl = $("w-status");

  function setEstado(estado) {
    document.body.dataset.estado = estado || "reposo";
    if (statusEl) statusEl.textContent = estado || "reposo";
  }

  function addLog(e) {
    // Mismo criterio que app.js::addLog: las entradas del modo code se pintan en
    // su propia consola, no aquí — el widget es la conversación normal.
    if (e.modo === "code") return;
    const d = document.createElement("div");
    d.className = "l " + e.kind;
    const pref = { user: "tú", answer: "CRIS", thought: "···", tool_call: "→", observation: "obs", error: "!!" }[e.kind] || e.kind;
    d.innerHTML = `<span class="t">${esc(e.hora || "")}</span><b>${pref}</b> <span>${esc(e.text)}</span>`;
    logEl.appendChild(d);
    logEl.scrollTop = logEl.scrollHeight;
  }

  // Iconos por rol de la narración del bucle (Fase 2 control_total.bucle._emitir):
  // accion_propuesta ya trae la 'razon' en lenguaje natural en su propio texto.
  const PREF_NARRACION = {
    accion_propuesta: "→", accion_ejecutada: "✓", guardarraíl: "⚠",
    sistema: "ℹ", error_accion: "!!", error_vision: "!!",
  };
  function addNarracion(e) {
    if (!e) return;
    const d = document.createElement("div");
    const esError = String(e.rol || "").startsWith("error");
    d.className = "l" + (esError ? " error" : "");
    const pref = PREF_NARRACION[e.rol] || e.rol;
    d.innerHTML = `<span class="t">${esc(e.ts || "")}</span><b>${esc(pref)}</b> <span>${esc(e.texto)}</span>`;
    logEl.appendChild(d);
    logEl.scrollTop = logEl.scrollHeight;
  }

  function applySnapshot(s) {
    setEstado(s.estado);
    logEl.innerHTML = "";
    if (s.log) s.log.forEach(addLog);
  }

  // ---------- Modal de confirmación (Fase 4, guardarraíles.py) ----------
  const confirmOverlay = $("w-confirm-overlay");
  const confirmBody = $("w-confirm-body");
  function showConfirm(d) {
    confirmBody.textContent = (d && d.texto) || "¿Confirmar la acción?";
    confirmOverlay.hidden = false;
  }
  function resolveConfirm(ok) {
    confirmOverlay.hidden = true;
    fetch("/confirmar", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ok, texto: "" }),
    }).catch(() => {});
  }
  $("w-confirm-ok").addEventListener("click", () => resolveConfirm(true));
  $("w-confirm-cancel").addEventListener("click", () => resolveConfirm(false));

  // ---------- Auto-cierre al desactivar el Modo Control Total (Fase 4) ----------
  function cerrarPorDesactivacion() {
    if (window.pywebview && window.pywebview.api && window.pywebview.api.cerrar) {
      window.pywebview.api.cerrar().catch(() => {});
    }
  }

  function conectar() {
    const es = new EventSource("/eventos");
    es.onmessage = (ev) => {
      let m;
      try { m = JSON.parse(ev.data); } catch (_) { return; }
      switch (m.tipo) {
        case "snapshot": applySnapshot(m.datos); break;
        case "estado": setEstado(m.datos); break;
        case "log": addLog(m.datos); break;
        case "confirmacion": showConfirm(m.datos); break;
        case "control_total": if (!m.datos) cerrarPorDesactivacion(); break;
        case "control_total_entrada": addNarracion(m.datos); break;
      }
    };
    es.onerror = () => { es.close(); setTimeout(conectar, 2000); };
  }
  conectar();

  const form = $("w-form");
  const entrada = $("w-entrada");
  form.addEventListener("submit", (ev) => {
    ev.preventDefault();
    const texto = entrada.value.trim();
    if (!texto) return;
    entrada.value = "";
    fetch("/enviar", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ texto }),
    }).catch(() => {});
  });

  // ---------- Controles de ventana (minimizar/restaurar/cerrar) ----------
  // window.pywebview.api solo existe tras el evento "pywebviewready" — llamarlo
  // antes rompería con un TypeError si alguien pincha muy rápido tras cargar.
  function wireBoton(id, metodo) {
    const btn = $(id);
    if (!btn) return;
    btn.addEventListener("click", () => {
      if (window.pywebview && window.pywebview.api && window.pywebview.api[metodo]) {
        window.pywebview.api[metodo]().catch(() => {});
      }
    });
  }
  wireBoton("w-minimizar", "minimizar");
  wireBoton("w-restaurar", "restaurar");
  wireBoton("w-cerrar", "cerrar");
})();
