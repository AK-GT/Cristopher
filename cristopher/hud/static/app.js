/* CRISTOPHER HUD — núcleo de partículas reactivo + cliente de estado (SSE). */
(function () {
  "use strict";

  // ---------- Núcleo de partículas (three.js) ----------
  const canvas = document.getElementById("core-canvas");
  const renderer = new THREE.WebGLRenderer({ canvas, alpha: true, antialias: true });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(45, 1, 0.1, 100);
  camera.position.z = 6.2;

  const R = 2.0;
  const N = 3500;
  const dirs = new Float32Array(N * 3); // direcciones base (unitarias)
  const phase = new Float32Array(N);
  const positions = new Float32Array(N * 3);
  const colors = new Float32Array(N * 3);

  // Esfera de Fibonacci (puntos bien repartidos)
  const golden = Math.PI * (3 - Math.sqrt(5));
  for (let i = 0; i < N; i++) {
    const y = 1 - (i / (N - 1)) * 2;
    const r = Math.sqrt(1 - y * y);
    const th = golden * i;
    const x = Math.cos(th) * r, z = Math.sin(th) * r;
    dirs[i * 3] = x; dirs[i * 3 + 1] = y; dirs[i * 3 + 2] = z;
    phase[i] = Math.random() * Math.PI * 2;
    positions[i * 3] = x * R; positions[i * 3 + 1] = y * R; positions[i * 3 + 2] = z * R;
    colors[i * 3] = 0.13; colors[i * 3 + 1] = 0.82; colors[i * 3 + 2] = 0.93;
  }

  const geo = new THREE.BufferGeometry();
  geo.setAttribute("position", new THREE.BufferAttribute(positions, 3));
  geo.setAttribute("color", new THREE.BufferAttribute(colors, 3));

  // Sprite suave (glow) para cada partícula
  function makeSprite() {
    const c = document.createElement("canvas"); c.width = c.height = 64;
    const g = c.getContext("2d");
    const grad = g.createRadialGradient(32, 32, 0, 32, 32, 32);
    grad.addColorStop(0, "rgba(255,255,255,1)");
    grad.addColorStop(0.25, "rgba(120,240,255,0.8)");
    grad.addColorStop(1, "rgba(34,211,238,0)");
    g.fillStyle = grad; g.fillRect(0, 0, 64, 64);
    const t = new THREE.CanvasTexture(c); return t;
  }
  const mat = new THREE.PointsMaterial({
    size: 0.075, map: makeSprite(), vertexColors: true, transparent: true,
    opacity: 0.9, depthWrite: false, blending: THREE.AdditiveBlending,
  });
  const points = new THREE.Points(geo, mat);
  scene.add(points);

  // Anillos HUD concéntricos (decoración fina)
  const ringGroup = new THREE.Group();
  [2.9, 3.25].forEach((rad, k) => {
    const seg = 128, pts = [];
    for (let i = 0; i <= seg; i++) {
      const a = (i / seg) * Math.PI * 2;
      pts.push(new THREE.Vector3(Math.cos(a) * rad, Math.sin(a) * rad, 0));
    }
    const rg = new THREE.BufferGeometry().setFromPoints(pts);
    const rm = new THREE.LineBasicMaterial({ color: 0x22d3ee, transparent: true, opacity: k ? 0.12 : 0.22 });
    ringGroup.add(new THREE.Line(rg, rm));
  });
  scene.add(ringGroup);

  // Parámetros por estado (objetivo) y actuales (suavizados)
  const TARGETS = {
    reposo:     { breath: 1.0, agit: 0.02, wave: 0.0, spin: 0.0006, bright: 0.55 },
    pensando:   { breath: 1.4, agit: 0.16, wave: 0.0, spin: 0.0022, bright: 1.0 },
    hablando:   { breath: 1.1, agit: 0.05, wave: 1.0, spin: 0.0012, bright: 0.9 },
    escuchando: { breath: 1.2, agit: 0.08, wave: 0.3, spin: 0.0012, bright: 0.8 },
  };
  let cur = Object.assign({}, TARGETS.reposo);
  let target = TARGETS.reposo;
  function setCoreState(s) { target = TARGETS[s] || TARGETS.reposo; }

  const clock = new THREE.Clock();
  function animate() {
    requestAnimationFrame(animate);
    const t = clock.getElapsedTime();
    // easing hacia el objetivo
    for (const k in target) cur[k] += (target[k] - cur[k]) * 0.05;

    const pos = geo.attributes.position.array;
    const col = geo.attributes.color.array;
    for (let i = 0; i < N; i++) {
      const ix = i * 3, dx = dirs[ix], dy = dirs[ix + 1], dz = dirs[ix + 2], ph = phase[i];
      let rad = R * (1 + 0.05 * cur.breath * Math.sin(t * 0.9 + ph));
      rad += cur.wave * 0.22 * R * Math.sin(t * 5.0 - dy * 4.0 + ph);
      const jit = cur.agit * R * 0.5 * Math.sin(t * 3.0 + ph * 1.7);
      pos[ix] = dx * (rad + jit) + cur.agit * R * 0.25 * Math.sin(t * 2.1 + ph * 2.3);
      pos[ix + 1] = dy * (rad + jit);
      pos[ix + 2] = dz * (rad + jit) + cur.agit * R * 0.25 * Math.cos(t * 1.7 + ph * 1.9);
      const b = cur.bright * (0.7 + 0.3 * Math.sin(t * 2.5 + ph));
      col[ix] = 0.13 * b + 0.05; col[ix + 1] = 0.85 * b; col[ix + 2] = 0.93 * b;
    }
    geo.attributes.position.needsUpdate = true;
    geo.attributes.color.needsUpdate = true;
    points.rotation.y += cur.spin; points.rotation.x = Math.sin(t * 0.15) * 0.12;
    ringGroup.rotation.z += 0.0009; ringGroup.rotation.x = 0.9;
    mat.opacity = 0.55 + 0.4 * cur.bright;
    renderer.render(scene, camera);
  }

  function resize() {
    const w = canvas.clientWidth, h = canvas.clientHeight;
    if (w === 0 || h === 0) return;
    renderer.setSize(w, h, false);
    camera.aspect = w / h; camera.updateProjectionMatrix();
  }
  window.addEventListener("resize", resize);
  setTimeout(resize, 0); resize(); animate();

  // ---------- DOM / estado ----------
  const $ = (id) => document.getElementById(id);
  const CAPTION = { reposo: "presencia en reposo", pensando: "procesando…", hablando: "hablando", escuchando: "escuchando" };

  function setEstado(s) {
    document.body.dataset.estado = s;
    $("status-text").textContent = s;
    $("core-state").textContent = s;
    $("core-caption").textContent = CAPTION[s] || s;
    setCoreState(s);
  }
  function setMetricas(m) {
    $("cpu-val").textContent = Math.round(m.cpu) + "%";
    $("ram-val").textContent = Math.round(m.ram) + "%";
    $("cpu-bar").style.width = Math.min(100, m.cpu) + "%";
    $("ram-bar").style.width = Math.min(100, m.ram) + "%";
  }
  function setTarea(txt) { $("task").textContent = txt || "—"; }
  function setRoster(list) {
    const el = $("roster");
    if (!list || !list.length) { el.innerHTML = '<div class="empty">sin delegaciones</div>'; return; }
    el.innerHTML = list.map((s) => `<div class="item"><b>${esc(s.nombre)}</b> <small>${esc(s.hora || "")}</small></div>`).join("");
  }
  const alertBox = $("alerts");
  function addAlerta(a) {
    if (alertBox.querySelector(".empty")) alertBox.innerHTML = "";
    const d = document.createElement("div");
    d.className = "al n" + (a.nivel || 1);
    d.innerHTML = `<small>${esc(a.hora || "")}</small>${esc(a.texto)}`;
    alertBox.prepend(d);
  }
  const logEl = $("log");
  function addLog(e) {
    const d = document.createElement("div");
    d.className = "l " + e.kind;
    const pref = { user: "tú", answer: "CRIS", thought: "···", tool_call: "→", observation: "obs", error: "!!" }[e.kind] || e.kind;
    d.innerHTML = `<span class="t">${esc(e.hora || "")}</span><b>${pref}</b> <span>${esc(e.text)}</span>`;
    logEl.appendChild(d);
    logEl.scrollTop = logEl.scrollHeight;
  }
  function esc(s) { return String(s == null ? "" : s).replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c])); }

  function applySnapshot(s) {
    setEstado(s.estado || "reposo");
    if (s.metricas) setMetricas(s.metricas);
    setTarea(s.tarea); setRoster(s.subagentes);
    if (s.alertas) s.alertas.slice().reverse().forEach(addAlerta);
    if (s.log) s.log.forEach(addLog);
  }

  // ---------- SSE ----------
  function conectar() {
    const es = new EventSource("/eventos");
    es.onmessage = (ev) => {
      let m; try { m = JSON.parse(ev.data); } catch (_) { return; }
      switch (m.tipo) {
        case "snapshot": applySnapshot(m.datos); break;
        case "estado": setEstado(m.datos); break;
        case "log": addLog(m.datos); break;
        case "metricas": setMetricas(m.datos); break;
        case "tarea": setTarea(m.datos); break;
        case "subagentes": setRoster(m.datos); break;
        case "alerta": addAlerta(m.datos); break;
      }
    };
    es.onerror = () => { es.close(); setTimeout(conectar, 2000); };
  }
  conectar();

  // ---------- Entrada ----------
  $("form").addEventListener("submit", (e) => {
    e.preventDefault();
    const input = $("entrada"); const texto = input.value.trim();
    if (!texto) return;
    input.value = "";
    fetch("/enviar", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ texto }),
    }).catch(() => {});
  });

  // ---------- Reloj ----------
  function tick() { $("clock").textContent = new Date().toLocaleTimeString("es-ES"); }
  setInterval(tick, 1000); tick();
})();
