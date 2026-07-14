/* CRISTOPHER HUD — núcleo de partículas reactivo + cliente de estado (SSE). */
(function () {
  "use strict";

  // ---------- Núcleo neuronal (three.js · estilo JARVIS) ----------
  // Bloque de ajuste rápido: subir/bajar cada elemento tras verlo ("luego limitamos").
  const CONFIG = {
    R: 2.0,
    detail: 3,          // subdivisión de la icosfera (facetas + densidad de la red de venas)
    points: 4200,       // nube turbulenta
    freq: 1.55,         // frecuencia base del ruido
    warp: 0.55,         // fuerza del domain-warping (cuánto de "anormal")
    colorShell: 0x1e7bff,
    colorLine:  0x66baff,
    colorPoint: 0x2f95ff,
    colorCore:  0x2ea8ff,
    colorRing:  0x2f8fff,
    tendrils: 6,        // mechones de energía que se salen del borde
    show: { shell: true, lines: true, points: true, tendrils: true, core: true },
  };
  const R = CONFIG.R;

  const canvas = document.getElementById("core-canvas");
  const renderer = new THREE.WebGLRenderer({ canvas, alpha: true, antialias: true });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(45, 1, 0.1, 100);
  camera.position.z = 6.2;
  const REDUCE = matchMedia("(prefers-reduced-motion: reduce)").matches;

  // Uniforms dinámicos COMPARTIDOS por todas las capas → se mueven juntas
  // (mismo campo de ruido) y se leen como un solo volumen, no capas sueltas.
  const U = {
    uTime: { value: 0 }, uAgit: { value: 0.1 }, uFlow: { value: 0.08 },
    uWarp: { value: CONFIG.warp }, uBright: { value: 0.6 }, uWave: { value: 0 },
    uFreq: { value: CONFIG.freq }, uCol: { value: new THREE.Color(CONFIG.colorShell) },
  };
  const shared = () => ({
    uTime: U.uTime, uAgit: U.uAgit, uFlow: U.uFlow, uWarp: U.uWarp,
    uBright: U.uBright, uWave: U.uWave, uFreq: U.uFreq, uCol: U.uCol,
  });

  // Simplex noise 3D (Ashima / Stefan Gustavson) — dominio público.
  const SIMPLEX = `
    vec3 mod289(vec3 x){return x-floor(x*(1.0/289.0))*289.0;}
    vec4 mod289(vec4 x){return x-floor(x*(1.0/289.0))*289.0;}
    vec4 permute(vec4 x){return mod289(((x*34.0)+1.0)*x);}
    vec4 taylorInvSqrt(vec4 r){return 1.79284291400159-0.85373472095314*r;}
    float snoise(vec3 v){
      const vec2 C=vec2(1.0/6.0,1.0/3.0); const vec4 D=vec4(0.0,0.5,1.0,2.0);
      vec3 i=floor(v+dot(v,C.yyy)); vec3 x0=v-i+dot(i,C.xxx);
      vec3 g=step(x0.yzx,x0.xyz); vec3 l=1.0-g;
      vec3 i1=min(g.xyz,l.zxy); vec3 i2=max(g.xyz,l.zxy);
      vec3 x1=x0-i1+C.xxx; vec3 x2=x0-i2+C.yyy; vec3 x3=x0-D.yyy;
      i=mod289(i);
      vec4 p=permute(permute(permute(
        i.z+vec4(0.0,i1.z,i2.z,1.0))
        +i.y+vec4(0.0,i1.y,i2.y,1.0))
        +i.x+vec4(0.0,i1.x,i2.x,1.0));
      float n_=0.142857142857; vec3 ns=n_*D.wyz-D.xzx;
      vec4 j=p-49.0*floor(p*ns.z*ns.z);
      vec4 x_=floor(j*ns.z); vec4 y_=floor(j-7.0*x_);
      vec4 x=x_*ns.x+ns.yyyy; vec4 y=y_*ns.x+ns.yyyy; vec4 h=1.0-abs(x)-abs(y);
      vec4 b0=vec4(x.xy,y.xy); vec4 b1=vec4(x.zw,y.zw);
      vec4 s0=floor(b0)*2.0+1.0; vec4 s1=floor(b1)*2.0+1.0; vec4 sh=-step(h,vec4(0.0));
      vec4 a0=b0.xzyw+s0.xzyw*sh.xxyy; vec4 a1=b1.xzyw+s1.xzyw*sh.zzww;
      vec3 p0=vec3(a0.xy,h.x); vec3 p1=vec3(a0.zw,h.y);
      vec3 p2=vec3(a1.xy,h.z); vec3 p3=vec3(a1.zw,h.w);
      vec4 norm=taylorInvSqrt(vec4(dot(p0,p0),dot(p1,p1),dot(p2,p2),dot(p3,p3)));
      p0*=norm.x; p1*=norm.y; p2*=norm.z; p3*=norm.w;
      vec4 m=max(0.6-vec4(dot(x0,x0),dot(x1,x1),dot(x2,x2),dot(x3,x3)),0.0); m=m*m;
      return 42.0*dot(m*m,vec4(dot(p0,x0),dot(p1,x1),dot(p2,x2),dot(p3,x3)));
    }
    float fbm(vec3 p){
      float f=0.0, amp=0.5;
      for(int i=0;i<3;i++){ f+=amp*snoise(p); p*=2.03; amp*=0.5; }
      return f;
    }`;

  // Campo de desplazamiento compartido: domain-warping (ruido deformado por
  // ruido) → superficie irregular/"anormal" que respira. Devuelve la posición
  // desplazada de una dirección unitaria y, por out param, el valor del campo.
  const FIELD = `
    uniform float uTime,uAgit,uFlow,uWarp,uBright,uWave,uFreq;
    vec3 warp(vec3 p){
      float t=uTime*uFlow;
      vec3 q=vec3(fbm(p+vec3(0.0,t,0.0)), fbm(p+vec3(5.2,1.3,t)), fbm(p+vec3(1.7,9.2,t*0.7)));
      return p+uWarp*q;
    }
    float field(vec3 dir){ return fbm(warp(dir*uFreq)); }
    vec3 displace(vec3 dir, float rad, out float n){
      n=field(dir);
      float breath=0.035*sin(uTime*0.8);
      float wv=uWave*0.10*(sin(uTime*0.85-dir.y*4.0)+sin(uTime*0.65+dir.x*3.5)+sin(uTime*0.52+dir.z*3.0));
      float disp=rad*(1.0+breath+uAgit*0.42*n+wv);
      vec3 up=abs(dir.y)<0.99?vec3(0.0,1.0,0.0):vec3(1.0,0.0,0.0);
      vec3 t1=normalize(cross(up,dir)); vec3 t2=cross(dir,t1);
      float na=snoise(dir*uFreq*1.3+vec3(11.0)+vec3(uTime*uFlow));
      float nb=snoise(dir*uFreq*1.3+vec3(37.0)+vec3(uTime*uFlow*0.8));
      return dir*disp + (t1*na+t2*nb)*(uAgit*0.35*rad);
    }`;
  const LIB = SIMPLEX + FIELD;

  const coreGroup = new THREE.Group();  // cáscara + venas + puntos giran juntos
  scene.add(coreGroup);

  // ---- 1. Cáscara facetada (cuerpo cristalino, borde encendido por fresnel) ----
  if (CONFIG.show.shell) {
    const ico = new THREE.IcosahedronGeometry(R, CONFIG.detail);
    const shellMat = new THREE.ShaderMaterial({
      uniforms: Object.assign(shared(), { uOpacity: { value: 0.5 } }),
      vertexShader: LIB + `
        varying vec3 vW; varying vec3 vView; varying float vN;
        void main(){
          vec3 dir=normalize(position);
          float n; vec3 pos=displace(dir, ${R.toFixed(3)}, n);
          vN=n; vec4 wp=modelMatrix*vec4(pos,1.0);
          vW=wp.xyz; vView=cameraPosition-wp.xyz;
          gl_Position=projectionMatrix*modelViewMatrix*vec4(pos,1.0);
        }`,
      fragmentShader: `
        precision mediump float;
        uniform vec3 uCol; uniform float uOpacity,uBright;
        varying vec3 vW; varying vec3 vView; varying float vN;
        void main(){
          vec3 nrm=normalize(cross(dFdx(vW),dFdy(vW)));   // normal plana por faceta
          vec3 V=normalize(vView);
          float fres=pow(1.0-abs(dot(nrm,V)),2.2);        // borde encendido
          vec3 col=mix(uCol, vec3(0.82,0.92,1.0), fres*0.6);
          col*=(0.55+0.6*uBright)*(0.7+0.5*(vN*0.5+0.5));
          gl_FragColor=vec4(col, (0.06+fres*0.9)*uOpacity);
        }`,
      transparent: true, depthWrite: false, blending: THREE.AdditiveBlending, side: THREE.DoubleSide,
    });
    shellMat.extensions = { derivatives: true };
    coreGroup.add(new THREE.Mesh(ico, shellMat));
  }

  // ---- 2. Red de filamentos (venas / sistema nervioso) — el elemento nuevo ----
  if (CONFIG.show.lines) {
    const icoE = new THREE.IcosahedronGeometry(R, CONFIG.detail);
    const pa = icoE.attributes.position;
    // La icosfera de este build es NO indexada (triángulos sueltos). Sacamos
    // los triángulos del índice si existe, o de cada terna de vértices, y
    // deduplicamos aristas por posición redondeada (vértices compartidos).
    const tris = [];
    if (icoE.index) {
      const a = icoE.index.array;
      for (let i = 0; i < a.length; i += 3) tris.push([a[i], a[i + 1], a[i + 2]]);
    } else {
      for (let i = 0; i < pa.count; i += 3) tris.push([i, i + 1, i + 2]);
    }
    const pkey = (i) => Math.round(pa.getX(i) * 1e4) + "," + Math.round(pa.getY(i) * 1e4) + "," + Math.round(pa.getZ(i) * 1e4);
    const seen = new Set(), ep = [];
    for (const [a, b, c] of tris) {
      for (const [u, v] of [[a, b], [b, c], [c, a]]) {
        const ka = pkey(u), kb = pkey(v);
        const ek = ka < kb ? ka + "|" + kb : kb + "|" + ka;
        if (seen.has(ek)) continue; seen.add(ek);
        ep.push(pa.getX(u), pa.getY(u), pa.getZ(u), pa.getX(v), pa.getY(v), pa.getZ(v));
      }
    }
    const lg = new THREE.BufferGeometry();
    lg.setAttribute("position", new THREE.BufferAttribute(new Float32Array(ep), 3));
    const lineMat = new THREE.ShaderMaterial({
      uniforms: Object.assign(shared(), { uOpacity: { value: 0.55 } }),
      vertexShader: LIB + `
        varying float vB;
        void main(){
          vec3 dir=normalize(position);
          float n; vec3 pos=displace(dir, ${(R * 1.005).toFixed(3)}, n);
          vB=0.35+0.65*(n*0.5+0.5);
          gl_Position=projectionMatrix*modelViewMatrix*vec4(pos,1.0);
        }`,
      fragmentShader: `
        precision mediump float;
        uniform vec3 uCol; uniform float uOpacity,uBright;
        varying float vB;
        void main(){ gl_FragColor=vec4(uCol*1.35*(0.5+0.9*uBright)*vB, uOpacity*vB); }`,
      transparent: true, depthWrite: false, blending: THREE.AdditiveBlending,
    });
    coreGroup.add(new THREE.LineSegments(lg, lineMat));
  }

  // ---- 3. Nube de puntos turbulenta (mechones hacia afuera, no uniforme) ----
  if (CONFIG.show.points) {
    const NP = CONFIG.points, pp = new Float32Array(NP * 3);
    const golden = Math.PI * (3 - Math.sqrt(5));
    for (let i = 0; i < NP; i++) {
      const y = 1 - (i / (NP - 1)) * 2, r = Math.sqrt(1 - y * y), th = golden * i;
      pp[i * 3] = Math.cos(th) * r * R; pp[i * 3 + 1] = y * R; pp[i * 3 + 2] = Math.sin(th) * r * R;
    }
    const pg = new THREE.BufferGeometry();
    pg.setAttribute("position", new THREE.BufferAttribute(pp, 3));
    const pointMat = new THREE.ShaderMaterial({
      uniforms: Object.assign(shared(), { uOpacity: { value: 0.9 }, uSize: { value: 0.09 } }),
      vertexShader: LIB + `
        uniform float uSize; varying float vB;
        void main(){
          vec3 dir=normalize(position);
          float n; vec3 pos=displace(dir, ${R.toFixed(3)}, n);
          pos += dir*max(0.0,n)*(0.35+uAgit*2.0);        // mechones de energía
          float e=0.5*n+0.5; vB=uBright*(0.35+0.8*e);
          vec4 mv=modelViewMatrix*vec4(pos,1.0);
          gl_PointSize=uSize*(300.0/-mv.z)*(0.5+1.0*e);
          gl_Position=projectionMatrix*mv;
        }`,
      fragmentShader: `
        precision mediump float;
        uniform vec3 uCol; uniform float uOpacity; varying float vB;
        void main(){
          float d=length(gl_PointCoord-0.5);
          float a=pow(smoothstep(0.5,0.0,d),1.5);
          vec3 c=mix(uCol*1.15, vec3(0.80,0.90,1.0), smoothstep(0.36,0.55,a)*0.45);
          gl_FragColor=vec4(c*vB, a*uOpacity);
        }`,
      transparent: true, depthWrite: false, blending: THREE.AdditiveBlending,
    });
    coreGroup.add(new THREE.Points(pg, pointMat));
  }

  // ---- 4. Núcleo caliente (sprite aditivo blanco→azul eléctrico) ----
  let coreSprite = null;
  if (CONFIG.show.core) {
    const cc = document.createElement("canvas"); cc.width = cc.height = 128;
    const g = cc.getContext("2d");
    const grad = g.createRadialGradient(64, 64, 0, 64, 64, 64);
    grad.addColorStop(0, "rgba(255,255,255,1)");
    grad.addColorStop(0.18, "rgba(180,225,255,0.95)");
    grad.addColorStop(0.5, "rgba(46,168,255,0.35)");
    grad.addColorStop(1, "rgba(46,168,255,0)");
    g.fillStyle = grad; g.fillRect(0, 0, 128, 128);
    const sm = new THREE.SpriteMaterial({
      map: new THREE.CanvasTexture(cc), blending: THREE.AdditiveBlending,
      transparent: true, depthWrite: false,
    });
    coreSprite = new THREE.Sprite(sm); coreSprite.scale.set(3.4, 3.4, 1);
    scene.add(coreSprite);
  }

  // ---- 5. Mechones / tendones de energía que se salen del borde ----
  const tendrilGroup = new THREE.Group(), tendrilMats = [];
  if (CONFIG.show.tendrils) {
    const rnd = () => new THREE.Vector3(Math.random() * 2 - 1, Math.random() * 2 - 1, Math.random() * 2 - 1).normalize();
    for (let i = 0; i < CONFIG.tendrils; i++) {
      const base = rnd(), axis = rnd(), pts = [], ST = 26;
      for (let j = 0; j <= ST; j++) {
        const tt = j / ST, rad = R * (1.0 + 0.55 * tt * tt);
        const d = base.clone().applyAxisAngle(axis, tt * 1.4 + Math.sin(tt * 6.0) * 0.15);
        pts.push(d.multiplyScalar(rad));
      }
      const curve = new THREE.CatmullRomCurve3(pts);
      const tg = new THREE.BufferGeometry().setFromPoints(curve.getPoints(70));
      const tm = new THREE.LineBasicMaterial({
        color: CONFIG.colorLine, transparent: true, opacity: 0.0,
        blending: THREE.AdditiveBlending, depthWrite: false,
      });
      tm.userData = { ph: Math.random() * 6.28 };
      tendrilMats.push(tm); tendrilGroup.add(new THREE.Line(tg, tm));
    }
    scene.add(tendrilGroup);
  }

  // ---- Anillos HUD (marco fino, azul eléctrico) ----
  const ringGroup = new THREE.Group(), rings = [];
  [[2.75, 0.16], [3.1, 0.09], [3.4, 0.05]].forEach(([rad, op]) => {
    const seg = 180, pts = [];
    for (let i = 0; i <= seg; i++) {
      const a = (i / seg) * Math.PI * 2;
      pts.push(new THREE.Vector3(Math.cos(a) * rad, Math.sin(a) * rad, 0));
    }
    const rg = new THREE.BufferGeometry().setFromPoints(pts);
    const rm = new THREE.LineBasicMaterial({ color: CONFIG.colorRing, transparent: true, opacity: op });
    const ln = new THREE.Line(rg, rm); rings.push(ln); ringGroup.add(ln);
  });
  scene.add(ringGroup);

  // ---- Estados (mismo contrato; ahora alimentan los uniforms compartidos) ----
  // Cada estado tiene su color, frecuencia y "vibración" propios (misma base).
  const TARGETS = {
    reposo:     { agit: 0.10, wave: 0.35, spin: 0.0006, bright: 0.55, flow: 0.06, warp: 0.45, freq: 1.55, col: 0x1e7bff },
    pensando:   { agit: 0.38, wave: 0.0,  spin: 0.0011, bright: 1.0,  flow: 0.20, warp: 0.82, freq: 2.05, col: 0x00d8ff },
    hablando:   { agit: 0.16, wave: 1.0,  spin: 0.0012, bright: 0.9,  flow: 0.12, warp: 0.50, freq: 1.28, col: 0x3a86ff },
    escuchando: { agit: 0.16, wave: 0.40, spin: 0.0009, bright: 0.75, flow: 0.10, warp: 0.52, freq: 1.35, col: 0x6a5cff },
  };
  let cur = Object.assign({}, TARGETS.reposo);
  let target = TARGETS.reposo;
  function setCoreState(s) { target = TARGETS[s] || TARGETS.reposo; }

  const targetCol = new THREE.Color(), coreCol = new THREE.Color(), WHITE = new THREE.Color(0xffffff);
  const clock = new THREE.Clock();
  function animate() {
    requestAnimationFrame(animate);
    const t = clock.getElapsedTime();
    for (const k in target) { if (k === "col") continue; cur[k] += (target[k] - cur[k]) * 0.05; }

    const damp = REDUCE ? 0.4 : 1.0;  // menos movimiento si el usuario lo pide
    U.uTime.value = t;
    U.uAgit.value = cur.agit * damp;
    U.uFlow.value = cur.flow * damp;
    U.uWarp.value = cur.warp * damp;
    U.uBright.value = cur.bright;
    U.uWave.value = cur.wave;
    U.uFreq.value = cur.freq;
    // Color del estado: todas las capas convergen al mismo tono suavemente.
    targetCol.set(target.col);
    U.uCol.value.lerp(targetCol, 0.04);

    coreGroup.rotation.y += cur.spin; coreGroup.rotation.x = Math.sin(t * 0.15) * 0.10;
    if (coreSprite) {
      coreCol.copy(U.uCol.value).lerp(WHITE, 0.55);   // núcleo = tono del estado, aclarado
      coreSprite.material.color.lerp(coreCol, 0.05);
      coreSprite.material.opacity = 0.20 + 0.30 * cur.bright;
      const s = 2.4 + 0.4 * Math.sin(t * 1.2) + cur.bright * 0.4;
      coreSprite.scale.set(s, s, 1);
    }
    if (CONFIG.show.tendrils) {
      tendrilGroup.rotation.y -= cur.spin * 0.6; tendrilGroup.rotation.z += 0.0004;
      for (const tm of tendrilMats) {
        tm.color.lerp(U.uCol.value, 0.05);
        tm.opacity = (0.05 + 0.45 * cur.bright) * (0.6 + 0.4 * Math.sin(t * 1.5 + tm.userData.ph));
      }
    }
    ringGroup.rotation.x = 0.9;
    rings[0].rotation.z += 0.0011; rings[1].rotation.z -= 0.0007; rings[2].rotation.z += 0.0004;
    for (const ln of rings) ln.material.color.lerp(U.uCol.value, 0.03);
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
        case "confirmacion": showConfirm(m.datos); break;
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

  // ---------- Confirmación de acción irreversible ----------
  const confirmOverlay = $("confirm-overlay");
  function showConfirm(d) {
    $("confirm-body").textContent = (d && d.texto) || "¿Confirmar la acción?";
    confirmOverlay.hidden = false;
  }
  function resolveConfirm(ok) {
    confirmOverlay.hidden = true;
    fetch("/confirmar", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ok }),
    }).catch(() => {});
  }
  $("confirm-ok").addEventListener("click", () => resolveConfirm(true));
  $("confirm-cancel").addEventListener("click", () => resolveConfirm(false));

  // ---------- NOW PLAYING (música) ----------
  const np = {
    root: $("nowplaying"), state: $("np-state"), title: $("np-title"),
    artist: $("np-artist"), src: $("np-src"), cur: $("np-cur"), dur: $("np-dur"),
    fill: $("np-fill"), bar: $("np-bar"), prev: $("np-prev"), play: $("np-play"),
    next: $("np-next"), vol: $("np-vol"), qlist: $("np-q-list"), vis: $("np-vis"),
  };
  const npAudio = { sonando: false, pausado: false };  // alimenta el visualizador
  let volEditandoHasta = 0;  // mientras el usuario arrastra el volumen, no lo pisamos

  function fmt(seg) {
    seg = Math.max(0, Math.floor(seg || 0));
    const m = Math.floor(seg / 60), s = seg % 60;
    return m + ":" + String(s).padStart(2, "0");
  }

  function pintarMusica(e) {
    const sonando = !!e.sonando;
    npAudio.sonando = sonando;
    npAudio.pausado = !!e.pausado;
    np.root.dataset.sonando = sonando ? "1" : "0";
    np.state.textContent = !sonando ? "■" : (e.pausado ? "⏸" : "▶");
    np.play.textContent = (sonando && !e.pausado) ? "⏸" : "▶";
    np.title.textContent = sonando ? (e.titulo || "—") : "—";
    np.artist.textContent = sonando ? (e.artista || "") : "";
    if (sonando && e.fuente) { np.src.textContent = e.fuente; np.src.hidden = false; }
    else { np.src.hidden = true; }
    const dur = e.dur_seg || 0, pos = e.pos_seg || 0;
    np.cur.textContent = fmt(pos); np.dur.textContent = fmt(dur);
    np.fill.style.width = (dur > 0 ? Math.min(100, (pos / dur) * 100) : 0) + "%";
    if (Date.now() > volEditandoHasta && typeof e.volumen === "number") np.vol.value = e.volumen;
    // Cola: próximas pistas tras la actual.
    const cola = e.cola || [], i = (typeof e.indice === "number" ? e.indice : -1);
    const prox = i >= 0 ? cola.slice(i + 1) : cola;
    np.qlist.textContent = prox.length ? prox.join("  ·  ") : (sonando ? "fin de la cola" : "—");
  }

  function musicaTick() {
    fetch("/musica").then((r) => r.json()).then(pintarMusica).catch(() => {});
  }

  function control(accion, valor) {
    fetch("/musica/control", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ accion, valor }),
    }).then(() => setTimeout(musicaTick, 120)).catch(() => {});
  }

  np.prev.addEventListener("click", () => control("anterior"));
  np.next.addEventListener("click", () => control("siguiente"));
  np.play.addEventListener("click", () => {
    control(npAudio.sonando && !npAudio.pausado ? "pausar" : "reanudar");
  });
  np.vol.addEventListener("input", () => {
    volEditandoHasta = Date.now() + 1500;
    control("volumen", parseInt(np.vol.value, 10));
  });
  np.bar.addEventListener("click", (ev) => {
    const rect = np.bar.getBoundingClientRect();
    const frac = Math.max(0, Math.min(1, (ev.clientX - rect.left) / rect.width));
    control("seek", frac);
  });

  // Visualizador: pulso vivo de barras cian ligado al ESTADO real (no audio real).
  // Suena → respira con energía; pausa → tenue; reposo → casi plano. Puede latir en
  // armonía con el núcleo (misma cadencia suave).
  (function visualizador() {
    const c = np.vis, ctx = c.getContext("2d");
    const N = 22;
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    function size() {
      c.width = c.clientWidth * dpr; c.height = c.clientHeight * dpr;
    }
    size(); window.addEventListener("resize", size);
    const cyan = getComputedStyle(document.documentElement).getPropertyValue("--cyan-glow").trim() || "#2FF3E0";
    let amp = 0;  // energía suavizada
    function frame() {
      requestAnimationFrame(frame);
      if (REDUCE) { ctx.clearRect(0, 0, c.width, c.height); return; }
      const objetivo = npAudio.sonando ? (npAudio.pausado ? 0.12 : 1.0) : 0.0;
      amp += (objetivo - amp) * 0.06;  // transición suave entre estados
      const t = performance.now() / 1000;
      const w = c.width, h = c.height, bw = w / N;
      ctx.clearRect(0, 0, w, h);
      ctx.fillStyle = cyan;
      for (let i = 0; i < N; i++) {
        // Onda compuesta por barra (fases distintas) → parece "vivo", no uniforme.
        const wobble = 0.5 + 0.5 * Math.sin(t * 3.1 + i * 0.7) * Math.sin(t * 1.3 + i * 0.35);
        const base = 0.08 + 0.92 * wobble;
        const hh = Math.max(h * 0.06, base * amp * h * 0.92);
        ctx.globalAlpha = 0.35 + 0.55 * amp;
        ctx.fillRect(i * bw + bw * 0.22, (h - hh) / 2, bw * 0.56, hh);
      }
      ctx.globalAlpha = 1;
    }
    frame();
  })();

  musicaTick();
  setInterval(musicaTick, 1000);

  // ---------- Reloj ----------
  function tick() { $("clock").textContent = new Date().toLocaleTimeString("es-ES"); }
  setInterval(tick, 1000); tick();
})();
