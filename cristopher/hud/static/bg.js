/* CRISTOPHER HUD — Fase 2: malla de fondo interactiva (sustituye a .grid-bg).
   Canvas 2D a pantalla completa, detrás de los widgets (pointer-events:none). Los
   puntos cercanos al cursor se iluminan y se apartan con falloff; al alejarse vuelven
   con easing. Bloque CONFIG ajustable arriba, mismo patrón que app.js/widgets.js. */
(function () {
  "use strict";

  const CONFIG = {
    SPACING: 50,          // separación base en CSS-px (misma escala que el grid-bg anterior)
    MAX_POINTS: 1000,       // tope duro; en pantallas grandes se espacia más en vez de crecer
    RADIUS: 140,           // radio de influencia del cursor, CSS-px
    PUSH: 25,              // desplazamiento máximo en el centro del radio
    POS_EASE: 0.12,
    GLOW_EASE: 0.10,
    LINE_MAX_ALPHA: 0.35,
    DOT_R: 1.4,
    DOT_BASE_ALPHA: 0.28,   // +75% sobre el valor original (0.16) para que los puntos apagados se vean más
  };
  const EPS = 0.03;  // por debajo de esto, un punto se considera "en reposo" (no se traza)

  const REDUCE = matchMedia("(prefers-reduced-motion: reduce)").matches;

  const canvas = document.getElementById("bg-canvas");
  const ctx = canvas.getContext("2d");

  function cssVar(name, fallback) {
    const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    return v || fallback;
  }
  // Colores resueltos UNA vez (no por punto/frame): tonos ya existentes en :root.
  const COL_BASE = cssVar("--steel", "#5E7488");
  const COL_GLOW = cssVar("--cyan-glow", "#2FF3E0");
  const COL_LINE = cssVar("--cyan", "#22D3EE");

  let w = 0, h = 0, cols = 0, rows = 0;
  let restX, restY, curX, curY, glow;

  // Calcula cols/rows para cubrir width x height con SPACING; si eso supera
  // MAX_POINTS, agranda el spacing (nunca trunca: cubre toda la pantalla igual).
  function computeGrid(width, height) {
    let spacing = CONFIG.SPACING;
    let c = Math.ceil(width / spacing) + 1, r = Math.ceil(height / spacing) + 1;
    if (c * r > CONFIG.MAX_POINTS) {
      spacing *= Math.sqrt((c * r) / CONFIG.MAX_POINTS);
      c = Math.ceil(width / spacing) + 1; r = Math.ceil(height / spacing) + 1;
    }
    return { spacing, cols: c, rows: r };
  }

  function rebuild() {
    w = window.innerWidth; h = window.innerHeight;
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    canvas.width = Math.round(w * dpr);
    canvas.height = Math.round(h * dpr);
    // Todo se dibuja después en CSS-px (mismo espacio que clientX/clientY de mousemove).
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

    const g = computeGrid(w, h);
    cols = g.cols; rows = g.rows;
    const n = cols * rows;
    restX = new Float32Array(n);
    restY = new Float32Array(n);
    curX = new Float32Array(n);
    curY = new Float32Array(n);
    glow = new Float32Array(n);
    for (let row = 0; row < rows; row++) {
      for (let col = 0; col < cols; col++) {
        const i = row * cols + col;
        restX[i] = col * g.spacing;
        restY[i] = row * g.spacing;
        curX[i] = restX[i];
        curY[i] = restY[i];
        glow[i] = 0;
      }
    }
  }

  let mouseX = null, mouseY = null;
  function onMouseMove(ev) { mouseX = ev.clientX; mouseY = ev.clientY; }
  function onMouseLeave() { mouseX = null; mouseY = null; }

  // Rama estática (prefers-reduced-motion): un único dibujo, sin animación.
  function drawStatic() {
    ctx.clearRect(0, 0, w, h);
    ctx.fillStyle = COL_BASE;
    ctx.globalAlpha = CONFIG.DOT_BASE_ALPHA;
    const r = CONFIG.DOT_R;
    for (let i = 0; i < restX.length; i++) {
      ctx.fillRect(restX[i] - r, restY[i] - r, r * 2, r * 2);
    }
    ctx.globalAlpha = 1;
  }

  let rafId = null;

  function frame() {
    rafId = null;
    const n = curX.length, RADIUS = CONFIG.RADIUS, PUSH = CONFIG.PUSH, r = CONFIG.DOT_R;

    // 1) física: empuje con falloff cuadrático hacia el cursor, o vuelta a reposo.
    for (let i = 0; i < n; i++) {
      let tx = restX[i], ty = restY[i], g = 0;
      if (mouseX !== null) {
        const dx = restX[i] - mouseX, dy = restY[i] - mouseY;
        const d = Math.hypot(dx, dy);
        if (d < RADIUS && d > 0.0001) {
          const f = (1 - d / RADIUS) * (1 - d / RADIUS);
          const push = f * PUSH;
          tx = restX[i] + (dx / d) * push;
          ty = restY[i] + (dy / d) * push;
          g = f;
        }
      }
      curX[i] += (tx - curX[i]) * CONFIG.POS_EASE;
      curY[i] += (ty - curY[i]) * CONFIG.POS_EASE;
      glow[i] += (g - glow[i]) * CONFIG.GLOW_EASE;
    }

    ctx.clearRect(0, 0, w, h);

    // 2) pasada fría: todos los puntos, fillRect barato, alpha base fija.
    ctx.fillStyle = COL_BASE;
    ctx.globalAlpha = CONFIG.DOT_BASE_ALPHA;
    for (let i = 0; i < n; i++) {
      ctx.fillRect(curX[i] - r, curY[i] - r, r * 2, r * 2);
    }

    // 3) pasada caliente: solo puntos con brillo por encima del umbral.
    ctx.fillStyle = COL_GLOW;
    for (let i = 0; i < n; i++) {
      if (glow[i] <= EPS) continue;
      ctx.globalAlpha = glow[i];
      ctx.beginPath();
      ctx.arc(curX[i], curY[i], r * 1.8, 0, Math.PI * 2);
      ctx.fill();
    }

    // 4) líneas de constellation: solo vecino-derecha/vecino-abajo de la rejilla.
    ctx.strokeStyle = COL_LINE;
    ctx.lineWidth = 1;
    for (let row = 0; row < rows; row++) {
      for (let col = 0; col < cols; col++) {
        const i = row * cols + col;
        if (glow[i] <= EPS) continue;
        if (col < cols - 1) drawEdge(i, i + 1);
        if (row < rows - 1) drawEdge(i, i + cols);
      }
    }
    ctx.globalAlpha = 1;

    if (!document.hidden) rafId = requestAnimationFrame(frame);
  }

  function drawEdge(i, j) {
    const a = Math.min(glow[i], glow[j]);
    if (a <= EPS) return;
    ctx.globalAlpha = a * CONFIG.LINE_MAX_ALPHA;
    ctx.beginPath();
    ctx.moveTo(curX[i], curY[i]);
    ctx.lineTo(curX[j], curY[j]);
    ctx.stroke();
  }

  // Sin comprobar document.hidden aquí a propósito: el primer arranque debe dibujar
  // al menos un frame siempre (algunos entornos reportan "hidden" de forma poco
  // fiable al cargar aunque la pestaña sea visible). La propia frame() ya deja de
  // reprogramarse cuando document.hidden es cierto, así que la pausa real ocurre ahí.
  function startLoop() {
    if (rafId === null) rafId = requestAnimationFrame(frame);
  }

  let resizeScheduled = false;
  function onResize() {
    if (resizeScheduled) return;
    resizeScheduled = true;
    requestAnimationFrame(() => {
      resizeScheduled = false;
      rebuild();
      if (REDUCE) drawStatic();
    });
  }

  function init() {
    rebuild();
    window.addEventListener("resize", onResize);
    if (REDUCE) {
      drawStatic();
      return;
    }
    window.addEventListener("mousemove", onMouseMove, { passive: true });
    document.documentElement.addEventListener("mouseleave", onMouseLeave);
    window.addEventListener("blur", onMouseLeave);
    document.addEventListener("visibilitychange", () => { if (!document.hidden) startLoop(); });
    startLoop();
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
