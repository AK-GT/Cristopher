/* CRISTOPHER HUD — núcleo de partículas reactivo + cliente de estado (SSE). */
(function () {
  "use strict";

  // ---------- Núcleo neuronal (three.js · estilo JARVIS) ----------
  // Bloque de ajuste rápido: subir/bajar cada elemento tras verlo ("luego limitamos").
  const CONFIG = {
    R: 1.75,           // esfera algo más pequeña: deja hueco para que los anillos no salgan de cuadro
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
    rings: 6,           // anillos no coplanares (Fase 3)
    nodes: 48,          // nodos de la red neuronal
    nodeLinks: 3,       // vecinos más cercanos por nodo
    orbitLayers: 3,     // capas de partículas orbitando
    orbitPoints: 50,    // partículas por capa
    show: { shell: true, lines: true, points: true, tendrils: true, core: true,
            neural: true, coreHalo: true, orbit: true, veinPulse: true },
  };
  const R = CONFIG.R;
  const GOLDEN = Math.PI * (3 - Math.sqrt(5));  // ángulo áureo, reutilizado por varios bloques

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
    const seen = new Set(), ep = [], epT = [], epPhase = [];
    for (const [a, b, c] of tris) {
      for (const [u, v] of [[a, b], [b, c], [c, a]]) {
        const ka = pkey(u), kb = pkey(v);
        const ek = ka < kb ? ka + "|" + kb : kb + "|" + ka;
        if (seen.has(ek)) continue; seen.add(ek);
        ep.push(pa.getX(u), pa.getY(u), pa.getZ(u), pa.getX(v), pa.getY(v), pa.getZ(v));
        const ph = Math.random();
        epT.push(0, 1); epPhase.push(ph, ph);
      }
    }
    const lg = new THREE.BufferGeometry();
    lg.setAttribute("position", new THREE.BufferAttribute(new Float32Array(ep), 3));
    const VEIN_PULSE = CONFIG.show.veinPulse;
    if (VEIN_PULSE) {
      lg.setAttribute("aT", new THREE.BufferAttribute(new Float32Array(epT), 1));
      lg.setAttribute("aPhase", new THREE.BufferAttribute(new Float32Array(epPhase), 1));
    }
    // Pulso viajero (sinapsis) opcional: mismo truco que las conexiones neuronales del
    // bloque 6 — un varying interpolado 0→1 entre los 2 vértices de cada segmento, con
    // fase por arista, da una franja de brillo que recorre la vena sin subdividirla.
    const lineMat = new THREE.ShaderMaterial({
      uniforms: Object.assign(shared(), { uOpacity: { value: 0.55 } }),
      vertexShader: LIB + `
        ${VEIN_PULSE ? "attribute float aT, aPhase;" : ""}
        varying float vB${VEIN_PULSE ? ", vT, vPhase" : ""};
        void main(){
          vec3 dir=normalize(position);
          float n; vec3 pos=displace(dir, ${(R * 1.005).toFixed(3)}, n);
          vB=0.35+0.65*(n*0.5+0.5);
          ${VEIN_PULSE ? "vT=aT; vPhase=aPhase;" : ""}
          gl_Position=projectionMatrix*modelViewMatrix*vec4(pos,1.0);
        }`,
      fragmentShader: `
        precision mediump float;
        uniform vec3 uCol; uniform float uOpacity,uBright;
        ${VEIN_PULSE ? "uniform highp float uFlow, uTime;" : ""}
        varying float vB${VEIN_PULSE ? ", vT, vPhase" : ""};
        void main(){
          ${VEIN_PULSE ? `
          float speed=0.35+1.6*uFlow;
          float travel=0.22;                                 // fracción del ciclo que dura el viaje
          float cyc=fract(uTime*speed+vPhase);                // 0..1 por arista, con descanso al final
          float on=step(cyc,travel);                          // 0 durante el descanso (sin impulso)
          float pos=clamp(cyc/travel,0.0,1.0);                // posición del impulso a lo largo de la arista
          float pulse=on*smoothstep(0.10,0.0,abs(vT-pos));     // franja estrecha que viaja de 0 a 1
          float b=vB*(0.30+0.85*pulse);` : `
          float b=vB;`}
          gl_FragColor=vec4(uCol*1.35*(0.5+0.9*uBright)*b, uOpacity*b);
        }`,
      transparent: true, depthWrite: false, blending: THREE.AdditiveBlending,
    });
    coreGroup.add(new THREE.LineSegments(lg, lineMat));
  }

  // ---- 3. Nube de puntos turbulenta (mechones hacia afuera, no uniforme) ----
  if (CONFIG.show.points) {
    const NP = CONFIG.points, pp = new Float32Array(NP * 3);
    for (let i = 0; i < NP; i++) {
      const y = 1 - (i / (NP - 1)) * 2, r = Math.sqrt(1 - y * y), th = GOLDEN * i;
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
  let coreSprite = null, coreHalo = null;
  if (CONFIG.show.core) {
    const cc = document.createElement("canvas"); cc.width = cc.height = 128;
    const g = cc.getContext("2d");
    const grad = g.createRadialGradient(64, 64, 0, 64, 64, 64);
    grad.addColorStop(0, "rgba(255,255,255,1)");
    grad.addColorStop(0.18, "rgba(180,225,255,0.95)");
    grad.addColorStop(0.5, "rgba(46,168,255,0.35)");
    grad.addColorStop(1, "rgba(46,168,255,0)");
    g.fillStyle = grad; g.fillRect(0, 0, 128, 128);
    const tex = new THREE.CanvasTexture(cc);
    const sm = new THREE.SpriteMaterial({
      map: tex, blending: THREE.AdditiveBlending, transparent: true, depthWrite: false,
    });
    coreSprite = new THREE.Sprite(sm); coreSprite.scale.set(3.4, 3.4, 1);
    scene.add(coreSprite);

    // Refuerzo: halo más grande y tenue detrás del núcleo (reutiliza la misma textura).
    if (CONFIG.show.coreHalo) {
      const hm = new THREE.SpriteMaterial({
        map: tex, blending: THREE.AdditiveBlending, transparent: true, depthWrite: false, opacity: 0.12,
      });
      coreHalo = new THREE.Sprite(hm); coreHalo.scale.set(5.5, 5.5, 1);
      scene.add(coreHalo);
    }
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

  // ---- 6. Nodos + conexiones (red neuronal: sinapsis que pulsan) ----
  if (CONFIG.show.neural) {
    const NN = CONFIG.nodes, K = CONFIG.nodeLinks, NODE_R = R * 1.02;
    const dirs = [];
    for (let i = 0; i < NN; i++) {
      const y = 1 - (i / (NN - 1)) * 2, r = Math.sqrt(Math.max(0, 1 - y * y)), th = GOLDEN * i;
      dirs.push(new THREE.Vector3(Math.cos(th) * r, y, Math.sin(th) * r));
    }
    // Vecinos más cercanos por producto punto (más alto = más cerca angularmente);
    // O(n²) sobre unas pocas docenas de nodos, cálculo único al construir la geometría.
    const edgeSeen = new Set(), edges = [];
    for (let i = 0; i < NN; i++) {
      const dots = [];
      for (let j = 0; j < NN; j++) { if (j !== i) dots.push([dirs[i].dot(dirs[j]), j]); }
      dots.sort((a, b) => b[0] - a[0]);
      for (let k = 0; k < K && k < dots.length; k++) {
        const j = dots[k][1], key = i < j ? i + "," + j : j + "," + i;
        if (edgeSeen.has(key)) continue; edgeSeen.add(key);
        edges.push([i, j]);
      }
    }
    const edgePos = [], edgeT = [], edgePhase = [];
    for (const [i, j] of edges) {
      const ph = Math.random();
      edgePos.push(dirs[i].x * NODE_R, dirs[i].y * NODE_R, dirs[i].z * NODE_R);
      edgePos.push(dirs[j].x * NODE_R, dirs[j].y * NODE_R, dirs[j].z * NODE_R);
      edgeT.push(0, 1); edgePhase.push(ph, ph);
    }
    const ng = new THREE.BufferGeometry();
    ng.setAttribute("position", new THREE.BufferAttribute(new Float32Array(edgePos), 3));
    ng.setAttribute("aT", new THREE.BufferAttribute(new Float32Array(edgeT), 1));
    ng.setAttribute("aPhase", new THREE.BufferAttribute(new Float32Array(edgePhase), 1));
    const neuralMat = new THREE.ShaderMaterial({
      uniforms: Object.assign(shared(), { uOpacity: { value: 0.6 } }),
      vertexShader: LIB + `
        attribute float aT, aPhase;
        varying float vT, vPhase, vN;
        void main(){
          vec3 dir=normalize(position);
          float n; vec3 pos=displace(dir, ${NODE_R.toFixed(3)}, n);
          vT=aT; vPhase=aPhase; vN=n;
          gl_Position=projectionMatrix*modelViewMatrix*vec4(pos,1.0);
        }`,
      fragmentShader: `
        precision mediump float;
        uniform vec3 uCol; uniform float uOpacity,uBright;
        uniform highp float uFlow, uTime;
        varying float vT, vPhase, vN;
        void main(){
          float speed=0.4+2.2*uFlow;
          float travel=0.20;                                 // fracción del ciclo que dura el viaje
          float cyc=fract(uTime*speed+vPhase);                // 0..1 por arista, con descanso al final
          float on=step(cyc,travel);                          // 0 durante el descanso (sin impulso)
          float pos=clamp(cyc/travel,0.0,1.0);                // posición del impulso a lo largo de la arista
          float pulse=on*smoothstep(0.09,0.0,abs(vT-pos));     // franja estrecha que viaja de nodo a nodo
          float base=0.10+0.10*(vN*0.5+0.5);
          float b=base+0.9*pulse;
          gl_FragColor=vec4(uCol*1.2*(0.5+0.8*uBright)*b, uOpacity*b);
        }`,
      transparent: true, depthWrite: false, blending: THREE.AdditiveBlending,
    });
    coreGroup.add(new THREE.LineSegments(ng, neuralMat));

    // Nodos: puntos brillantes en cada vértice de la red (mismo patrón que el bloque 3).
    const nodePos = new Float32Array(NN * 3), nodePhase = new Float32Array(NN);
    for (let i = 0; i < NN; i++) {
      nodePos[i * 3] = dirs[i].x * NODE_R; nodePos[i * 3 + 1] = dirs[i].y * NODE_R; nodePos[i * 3 + 2] = dirs[i].z * NODE_R;
      nodePhase[i] = Math.random() * 6.28;
    }
    const npg = new THREE.BufferGeometry();
    npg.setAttribute("position", new THREE.BufferAttribute(nodePos, 3));
    npg.setAttribute("aPhase", new THREE.BufferAttribute(nodePhase, 1));
    const nodeMat = new THREE.ShaderMaterial({
      uniforms: Object.assign(shared(), { uOpacity: { value: 0.85 }, uSize: { value: 0.16 } }),
      vertexShader: LIB + `
        attribute float aPhase;
        uniform float uSize;
        varying float vB, vPhase;
        void main(){
          vec3 dir=normalize(position);
          float n; vec3 pos=displace(dir, ${NODE_R.toFixed(3)}, n);
          vB=0.5+0.5*n; vPhase=aPhase;
          vec4 mv=modelViewMatrix*vec4(pos,1.0);
          gl_PointSize=uSize*(300.0/-mv.z);
          gl_Position=projectionMatrix*mv;
        }`,
      fragmentShader: `
        precision mediump float;
        uniform vec3 uCol; uniform float uOpacity,uBright;
        uniform highp float uTime;
        varying float vB, vPhase;
        void main(){
          float d=length(gl_PointCoord-0.5);
          float a=pow(smoothstep(0.5,0.0,d),1.5);
          float twinkle=0.6+0.4*sin(uTime*1.3+vPhase);
          vec3 c=mix(uCol*1.3, vec3(0.85,0.94,1.0), 0.4);
          gl_FragColor=vec4(c*vB*uBright*twinkle, a*uOpacity);
        }`,
      transparent: true, depthWrite: false, blending: THREE.AdditiveBlending,
    });
    coreGroup.add(new THREE.Points(npg, nodeMat));
  }

  // ---- Anillos HUD no coplanares (tubos con volumen real + textura) ----
  // LineBasicMaterial.linewidth está limitado a 1px en Chrome/Windows (ANGLE) — para
  // que tengan grosor de verdad se construyen como tubos (TubeGeometry, three.js
  // núcleo, sin dependencias nuevas) con una textura generada por canvas (mismo
  // patrón que el gradiente del núcleo caliente) y un shader con fresnel, como la
  // cáscara del bloque 1, para que se vean redondos/volumétricos sin luces reales.
  function buildRingTexture() {
    const c = document.createElement("canvas");
    c.width = 256; c.height = 32;
    const g = c.getContext("2d");
    const grad = g.createLinearGradient(0, 0, 0, 32);
    grad.addColorStop(0.0, "rgba(150,200,255,0)");
    grad.addColorStop(0.5, "rgba(230,244,255,1)");
    grad.addColorStop(1.0, "rgba(150,200,255,0)");
    g.fillStyle = grad; g.fillRect(0, 0, 256, 32);
    g.globalCompositeOperation = "destination-out";  // recorta huecos → aspecto de circuito
    g.fillStyle = "rgba(0,0,0,0.85)";
    for (let x = 0; x < 256; x += 22) g.fillRect(x, 0, 9, 32);
    g.globalCompositeOperation = "source-over";
    return new THREE.CanvasTexture(c);
  }
  const ringTexBase = buildRingTexture();

  function ringTubeMaterial(opBase, repeats) {
    const tex = ringTexBase.clone();
    tex.needsUpdate = true;
    tex.wrapS = THREE.RepeatWrapping; tex.wrapT = THREE.ClampToEdgeWrapping;
    tex.repeat.set(repeats, 1);
    return new THREE.ShaderMaterial({
      uniforms: Object.assign(shared(), { uOpacity: { value: opBase }, uTex: { value: tex } }),
      vertexShader: `
        varying vec3 vNormalW; varying vec3 vViewW; varying vec2 vUv;
        void main(){
          vUv = uv;
          vec4 wp = modelMatrix * vec4(position, 1.0);
          vNormalW = normalize(mat3(modelMatrix) * normal);
          vViewW = cameraPosition - wp.xyz;
          gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
        }`,
      fragmentShader: `
        precision mediump float;
        uniform vec3 uCol; uniform float uOpacity, uBright;
        uniform sampler2D uTex;
        varying vec3 vNormalW; varying vec3 vViewW; varying vec2 vUv;
        void main(){
          vec3 N = normalize(vNormalW), V = normalize(vViewW);
          float fres = pow(1.0 - abs(dot(N, V)), 1.8);       // realza el borde → look redondo
          vec4 tex = texture2D(uTex, vUv);
          vec3 col = mix(uCol, vec3(0.85,0.93,1.0), fres * 0.5);
          float b = (0.35 + fres * 0.9) * (0.5 + 0.7 * uBright);
          gl_FragColor = vec4(col * b * tex.rgb, tex.a * uOpacity * (0.4 + fres * 0.8));
        }`,
      transparent: true, depthWrite: false, blending: THREE.AdditiveBlending, side: THREE.DoubleSide,
    });
  }

  const ringGroup = new THREE.Group(), rings = [], ringSpins = [], ringPrecess = [];
  const RING_PRECESS_AXIS = new THREE.Vector3(0, 1, 0);  // eje de precesión, reutilizado cada frame
  const RN = CONFIG.rings, ringSeg = 128;
  for (let i = 0; i < RN; i++) {
    // Radios variados, no una progresión lineal: algunos anillos claramente más
    // grandes, otros más pequeños (hash pseudoaleatorio determinista por índice, no
    // Math.random(), para que el tamaño de cada anillo no cambie entre recargas).
    // Contenidos en 1.9..2.45 para seguir dentro del encuadre de la cámara (a
    // distancia 6.2 y fov 45°, la mitad de la vista a z=0 ronda ~2.57 unidades).
    const jitter = Math.abs(Math.sin(i * 12.9898 + 3.7) * 43758.5453) % 1;
    const rad = 1.9 + jitter * 0.55;
    const t = jitter;                                   // 0=anillo pequeño, 1=anillo grande
    const op = 0.58 - t * 0.30;                          // los pequeños (más cercanos), más brillantes
    const tubeR = 0.055 - t * 0.02;                       // y algo más gruesos
    const curvePts = [];
    for (let s = 0; s < ringSeg; s++) {
      const a = (s / ringSeg) * Math.PI * 2;
      curvePts.push(new THREE.Vector3(Math.cos(a) * rad, Math.sin(a) * rad, 0));
    }
    const curve = new THREE.CatmullRomCurve3(curvePts, true);
    const geo = new THREE.TubeGeometry(curve, ringSeg, tubeR, 8, true);
    const mat = ringTubeMaterial(op, Math.max(2, Math.round(rad * 5)));
    const mesh = new THREE.Mesh(geo, mat);
    // Orientación no coplanar: normal del plano repartida en espiral áurea sobre la
    // esfera (mismo GOLDEN que el resto del archivo), aplicada como quaternion fijo.
    // El giro por frame toca .rotation.z (spin local sobre esa misma normal) Y ADEMÁS
    // precesiona alrededor de RING_PRECESS_AXIS (rotateOnWorldAxis), así el anillo no
    // solo gira sobre sí mismo: su plano se mueve/tumba alrededor del núcleo.
    const y = 1 - ((i + 0.5) / RN) * 2, r = Math.sqrt(Math.max(0, 1 - y * y)), th = GOLDEN * i;
    const normal = new THREE.Vector3(Math.cos(th) * r, y, Math.sin(th) * r);
    mesh.quaternion.setFromUnitVectors(new THREE.Vector3(0, 0, 1), normal);
    rings.push(mesh); ringGroup.add(mesh);
    ringSpins.push((i % 2 === 0 ? 1 : -1) * (0.0004 + 0.0002 * i));       // giro propio
    ringPrecess.push((i % 2 === 0 ? -1 : 1) * (0.00018 + 0.00012 * i));  // movimiento alrededor del núcleo
  }
  scene.add(ringGroup);

  // ---- Partículas orbitando (satélites decorativos en planos adicionales) ----
  const orbitGroup = new THREE.Group(), orbitLayers = [];
  if (CONFIG.show.orbit) {
    for (let i = 0; i < CONFIG.orbitLayers; i++) {
      const rad = 3.9 + i * 0.5, NP = CONFIG.orbitPoints;
      const pos = new Float32Array(NP * 3);
      for (let k = 0; k < NP; k++) {
        const th = GOLDEN * k;
        const rr = rad * (0.94 + Math.random() * 0.12);
        pos[k * 3] = Math.cos(th) * rr;
        pos[k * 3 + 1] = Math.sin(th) * rr;
        pos[k * 3 + 2] = (Math.random() - 0.5) * 0.3;  // banda fina en el eje normal local
      }
      const og = new THREE.BufferGeometry();
      og.setAttribute("position", new THREE.BufferAttribute(pos, 3));
      const om = new THREE.PointsMaterial({
        color: CONFIG.colorRing, size: 0.05, transparent: true, opacity: 0.35,
        blending: THREE.AdditiveBlending, sizeAttenuation: true, depthWrite: false,
      });
      const pts = new THREE.Points(og, om);
      // Orientación propia (offset respecto a los anillos) para que no se vean alineadas.
      const y = 1 - ((i + 0.5) / CONFIG.orbitLayers) * 2, ry = Math.sqrt(Math.max(0, 1 - y * y));
      const th2 = GOLDEN * i + 1.7;
      const normal = new THREE.Vector3(Math.cos(th2) * ry, y, Math.sin(th2) * ry);
      pts.quaternion.setFromUnitVectors(new THREE.Vector3(0, 0, 1), normal);
      orbitLayers.push({ pts, spin: (i % 2 === 0 ? -1 : 1) * (0.0003 + 0.00015 * i) });
      orbitGroup.add(pts);
    }
    scene.add(orbitGroup);
  }

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
  let prevStateName = "reposo", flash = 0;
  // Modo code (Cristopher Code, Fase 1): el orbe converge a esta variante roja en
  // vez del color por estado mientras el modo esté activo (ver setModo más abajo).
  // El TARGETS/uCol de arriba es el único punto de color de toda la geometría (§1
  // de las correcciones al plan), así que basta este override en animate().
  let modoCode = false;
  const CODE_COL = 0xE24B4A;
  // Modo redes (dashboard YouTube/Instagram/TikTok, ver CLAUDE.md): mismo
  // mecanismo que modoCode, color morado. Backend garantiza que modoCode y
  // modoRedes nunca están a true a la vez (exclusión mutua en hud/__main__.py),
  // así que el orden de precedencia de abajo es solo defensivo.
  let modoRedes = false;
  const REDES_COL = 0xB15CFF;
  // Modo calendario: tercer modo de la misma pantalla (pantalla.py), color verde.
  let modoCalendario = false;
  const CAL_COL = 0x3DDC97;
  // Modo Control Total (Fase 4): mismo mecanismo, color azul, independiente de
  // modoCode (ver setModoControlTotal más abajo) — si algún día coexistieran,
  // modoCode manda (orden de la línea de abajo), caso raro sin resolver a propósito.
  let modoControlTotal = false;
  const CONTROL_COL = 0x2f8fff;
  function setCoreState(s) {
    if (s === "pensando" && prevStateName !== "pensando") flash = 1.0;  // destello al entrar
    prevStateName = s;
    target = TARGETS[s] || TARGETS.reposo;
  }

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
    // Color del estado: todas las capas convergen al mismo tono suavemente. En modo
    // code, el rojo manda sobre el color del estado (reposo/pensando/…); en Modo
    // Control Total (Fase 4), el azul manda igual salvo que modo code esté activo.
    targetCol.set(modoCode ? CODE_COL : (modoRedes ? REDES_COL : (modoCalendario ? CAL_COL
      : (modoControlTotal ? CONTROL_COL : target.col))));
    U.uCol.value.lerp(targetCol, 0.04);

    flash *= 0.9;  // decae solo (destello de un solo disparo al entrar en "pensando")
    coreGroup.rotation.y += cur.spin; coreGroup.rotation.x = Math.sin(t * 0.15) * 0.10;
    if (coreSprite) {
      coreCol.copy(U.uCol.value).lerp(WHITE, 0.55);   // núcleo = tono del estado, aclarado
      coreSprite.material.color.lerp(coreCol, 0.05);
      coreSprite.material.opacity = 0.20 + 0.30 * cur.bright + flash * 0.5 * damp;
      const s = 2.4 + 0.4 * Math.sin(t * 1.2) + cur.bright * 0.4 + flash * 0.6 * damp;
      coreSprite.scale.set(s, s, 1);
    }
    if (coreHalo) {
      coreCol.copy(U.uCol.value).lerp(WHITE, 0.4);
      coreHalo.material.color.lerp(coreCol, 0.04);
      coreHalo.material.opacity = 0.10 + 0.10 * cur.bright + flash * 0.25 * damp;
      const s = 5.0 + 0.5 * Math.sin(t * 0.7) + cur.bright * 0.6;
      coreHalo.scale.set(s, s, 1);
    }
    if (CONFIG.show.tendrils) {
      tendrilGroup.rotation.y -= cur.spin * 0.6; tendrilGroup.rotation.z += 0.0004;
      for (const tm of tendrilMats) {
        tm.color.lerp(U.uCol.value, 0.05);
        tm.opacity = (0.05 + 0.45 * cur.bright) * (0.6 + 0.4 * Math.sin(t * 1.5 + tm.userData.ph));
      }
    }
    ringGroup.rotation.x = 0.9;
    // El color/brillo de los anillos llega solo vía uCol/uBright compartidos (son
    // ShaderMaterial ahora, no LineBasicMaterial) — no hace falta lerp manual aquí.
    for (let i = 0; i < rings.length; i++) {
      rings[i].rotation.z += ringSpins[i] * damp;                              // giro propio
      rings[i].rotateOnWorldAxis(RING_PRECESS_AXIS, ringPrecess[i] * damp);    // se mueve alrededor del núcleo
    }

    if (CONFIG.show.orbit) {
      for (const layer of orbitLayers) {
        layer.pts.rotation.z += layer.spin * damp;
        layer.pts.material.color.lerp(U.uCol.value, 0.03);
        layer.pts.material.opacity = 0.15 + 0.25 * cur.bright;
      }
    }
    renderer.render(scene, camera);
  }

  function resize() {
    const w = canvas.clientWidth, h = canvas.clientHeight;
    if (w === 0 || h === 0) return;
    renderer.setSize(w, h, false);
    camera.aspect = w / h; camera.updateProjectionMatrix();
  }
  window.addEventListener("resize", resize);
  window.__hudCoreResize = resize;  // hook para widgets.js (drag/resize del widget del orbe)
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
  // Modo code (Fase 1): flag global que retiñe el orbe (arriba) y transforma la
  // pantalla vía CSS (body[data-modo="code"], ver style.css §12). code.js escucha
  // este mismo cambio para arrancar sus propios widgets si hace falta.
  function _actualizarDatasetModo() {
    // modoCode manda si ambos llegaran a estar true por una carrera rara — la
    // exclusión mutua real vive en el backend (hud/__main__.py), esto es defensivo.
    document.body.dataset.modo = modoCode ? "code" : (modoRedes ? "redes"
      : (modoCalendario ? "calendario" : "normal"));
  }
  function setModo(activo) {
    modoCode = !!activo;
    _actualizarDatasetModo();
    window.__hudCodeSetModo && window.__hudCodeSetModo(modoCode);
  }
  // Modo redes (ver CLAUDE.md): mismo mecanismo que setModo. redes.js
  // escucha este mismo cambio para arrancar sus propios widgets si hace falta.
  function setModoRedes(activo) {
    modoRedes = !!activo;
    _actualizarDatasetModo();
    window.__hudRedesSetModo && window.__hudRedesSetModo(modoRedes);
  }
  // Modo calendario: mismo mecanismo; calendario.js carga datos al entrar.
  function setModoCalendario(activo) {
    modoCalendario = !!activo;
    _actualizarDatasetModo();
    window.__hudCalendarioSetModo && window.__hudCalendarioSetModo(modoCalendario);
  }
  // Modo Control Total (Fase 4): mismo mecanismo que setModo, atributo booleano
  // INDEPENDIENTE (data-control-total, no data-modo — no reemplaza al modo code,
  // ver style.css §12b) porque el widget/borde viven en ventanas propias, no en
  // un layout alternativo de esta página.
  function setModoControlTotal(activo) {
    modoControlTotal = !!activo;
    document.body.dataset.controlTotal = modoControlTotal ? "1" : "0";
  }
  function setTarea(txt) { $("task").textContent = txt || "—"; }
  function setRoster(list) {
    const el = $("roster");
    if (!list || !list.length) { el.innerHTML = '<div class="empty">sin delegaciones</div>'; return; }
    el.innerHTML = list.map((s) => `<div class="item"><b>${esc(s.nombre)}</b> <small>${esc(s.hora || "")}</small></div>`).join("");
  }
  function setConcentracion(c) {
    const el = $("concentracion");
    if (!c || !c.activo) { el.textContent = "inactivo"; el.dataset.activo = "0"; return; }
    el.dataset.activo = "1";
    const mins = Math.floor((c.restante_seg || 0) / 60);
    const segs = (c.restante_seg || 0) % 60;
    let txt = (c.fase === "descanso" ? "Descanso" : "Concentración") +
      ` · ${mins}m ${String(segs).padStart(2, "0")}s`;
    if (c.ciclo) txt += ` · bloque ${c.ciclo.bloque_actual}/${c.ciclo.bloques_totales}`;
    if (c.avisos_en_cola) txt += ` · ${c.avisos_en_cola} en cola`;
    el.textContent = txt;
  }
  function concentracionTick() {
    fetch("/concentracion").then((r) => r.json()).then(setConcentracion).catch(() => {});
  }
  concentracionTick();
  setInterval(concentracionTick, 5000);

  const alertBox = $("alerts");
  function addAlerta(a) {
    if (alertBox.querySelector(".empty")) alertBox.innerHTML = "";
    const d = document.createElement("div");
    d.className = "al n" + (a.nivel || 1);
    d.innerHTML = `<small>${esc(a.hora || "")}</small>${esc(a.texto)}`;
    alertBox.prepend(d);
  }
  const logEl = $("log");

  // ---------- Respuesta en vivo (streaming) ----------
  // Burbuja provisional que se va rellenando con los trozos que manda el modelo. Es
  // efímera: cuando llega el evento `answer` definitivo (el que sí va al historial),
  // se borra y la pinta addLog como siempre. Así el log queda idéntico a antes de
  // haber streaming, y al recargar no se ven fragmentos.
  let vivaEl = null;
  let vivaTexto = "";
  function borrarViva() {
    if (vivaEl) { vivaEl.remove(); vivaEl = null; vivaTexto = ""; }
  }
  function addDelta(trozo) {
    if (!vivaEl) {
      vivaEl = document.createElement("div");
      vivaEl.className = "l answer viva";
      logEl.appendChild(vivaEl);
      vivaTexto = "";
    }
    vivaTexto += trozo;
    vivaEl.innerHTML = `<span class="t"></span><b>CRIS</b> <span>${esc(vivaTexto)}</span>`;
    logEl.scrollTop = logEl.scrollHeight;
  }

  function addLog(e) {
    // La respuesta definitiva sustituye a la que se estaba pintando en vivo.
    if (e.kind === "answer") borrarViva();
    // Cada entrada viene etiquetada con el modo en que se generó (bus.py::log). Las
    // de modo code se pintan en su propia consola (code.js), no en la de aquí, para
    // no mezclar las dos conversaciones (corrección §2 al plan original).
    if (e.modo === "code") { window.__hudCodeLog && window.__hudCodeLog(e); return; }
    // Modo redes NO tiene conversación propia (a diferencia de code): su barra de
    // abajo es un espejo del mismo hilo, porque allí la consola normal está oculta.
    // Por eso se refleja Y se sigue pintando abajo, sin el `return` de arriba.
    window.__hudRedesLog && window.__hudRedesLog(e);
    window.__hudCalendarioLog && window.__hudCalendarioLog(e);   // mismo espejo
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
    setModo(s.modo_code);
    setModoRedes(s.modo_redes);
    setModoCalendario(s.modo_calendario);
    setModoVoz(s.modo_voz);
    setModoAuto(s.modo_auto);
    setModoControlTotal(s.control_total);
    if (s.metricas) setMetricas(s.metricas);
    setTarea(s.tarea); setRoster(s.subagentes);
    if (s.alertas) s.alertas.slice().reverse().forEach(addAlerta);
    if (s.log) s.log.forEach(addLog);
    if (s.sesiones) {
      panelAgentes.pintar(s.sesiones);
      panelAutoArreglos.pintar(s.sesiones);
      window.__hudCodeSesiones && window.__hudCodeSesiones(s.sesiones);
    }
    if (s.concentracion) setConcentracion(s.concentracion);
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
        case "answer_delta": addDelta(m.datos); break;
        case "answer_reset": borrarViva(); break;
        case "metricas": setMetricas(m.datos); break;
        case "tarea": setTarea(m.datos); break;
        case "subagentes": setRoster(m.datos); break;
        case "alerta": addAlerta(m.datos); break;
        case "confirmacion": showConfirm(m.datos); break;
        case "control_total": setModoControlTotal(m.datos); break;
        case "sesiones":
          panelAgentes.pintar(m.datos);
          panelAutoArreglos.pintar(m.datos);
          window.__hudCodeSesiones && window.__hudCodeSesiones(m.datos);
          break;
        case "concentracion": setConcentracion(m.datos); break;
        case "modo_code": setModo(m.datos); break;
        case "modo_redes": setModoRedes(m.datos); break;
        case "modo_calendario": setModoCalendario(m.datos); break;
        case "calendario_foco": window.__hudCalendarioFoco && window.__hudCalendarioFoco(m.datos); break;
        case "calendario_cambio": window.__hudCalendarioCambio && window.__hudCalendarioCambio(); break;
        case "modo_voz": setModoVoz(m.datos); break;
        case "modo_auto": setModoAuto(m.datos); break;
        // Abrir/cerrar la vista de cámara pedido desde el chat o por voz (tools
        // seguro_ver_camara / seguro_ocultar_camara). Hook como el de code.js: el
        // panel vive en su propia IIFE más abajo, y el widget flotante no lo tiene.
        case "seguro_panel":
          window.__hudSeguroPanel && window.__hudSeguroPanel(m.datos);
          break;
        case "sesion_entrada":
          panelAgentes.onEntrada(m.datos);
          panelAutoArreglos.onEntrada(m.datos);
          window.__hudCodeSesionEntrada && window.__hudCodeSesionEntrada(m.datos);
          break;
      }
    };
    es.onerror = () => { es.close(); setTimeout(conectar, 2000); };
  }
  conectar();

  // ---------- Entrada ----------
  // Adjunto pendiente (imagen elegida con el picker, o audio grabado/elegido):
  // { tipo: "imagen"|"audio", mime, blob } o null si no hay nada adjunto.
  let adjuntoPendiente = null;
  const adjuntoNombreEl = $("adjunto-nombre");

  function mostrarAdjunto(etiqueta) {
    adjuntoNombreEl.textContent = etiqueta;
    adjuntoNombreEl.hidden = !etiqueta;
  }

  function limpiarAdjunto() {
    adjuntoPendiente = null;
    $("adjunto-file").value = "";
    mostrarAdjunto("");
  }

  $("btn-adjuntar").addEventListener("click", () => $("adjunto-file").click());

  $("adjunto-file").addEventListener("change", () => {
    const file = $("adjunto-file").files[0];
    if (!file) return;
    const tipo = file.type.startsWith("image/") ? "imagen" : "audio";
    adjuntoPendiente = { tipo, mime: file.type, blob: file };
    mostrarAdjunto(`📎 ${file.name}`);
  });

  function blobToBase64(blob) {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(String(reader.result).split(",")[1] || "");
      reader.onerror = reject;
      reader.readAsDataURL(blob);
    });
  }

  // /enviar_media ya transcribe el audio en el servidor y lo encola como si lo hubieras
  // tecleado (ver hud/__main__.py::_post_enviar_media), así que el push-to-talk y el
  // adjunto clásico comparten exactamente el mismo camino.
  function enviarMedia(tipo, mime, blob, texto) {
    blobToBase64(blob).then((datos_b64) => {
      fetch("/enviar_media", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ tipo, mime, datos_b64, texto }),
      }).catch(() => {});
    }).catch(() => {});
  }

  // ---------- Modo de entrada: texto | pulsando | libre ----------
  // Cómo le HABLAS. Independiente del toggle 🔊 (respuesta hablada), más abajo.
  // "libre" (VAD, sin pulsar nada) todavía no está construido: el botón va disabled.
  const MODO_ENTRADA_KEY = "cristopher.modo_entrada";
  const PTT_TECLA = "h";        // E no: widgets.js ya la usa para el modo edición
  const PTT_MIN_MS = 350;       // por debajo de esto es un roce, no una frase
  let modoEntrada = "texto";
  let micStream = null;         // stream cacheado: pedirlo en cada pulsación metería
                                // 100-300 ms de retardo justo al empezar a hablar
  let mediaRecorder = null;
  let grabando = false;
  let arrancando = false;       // hay un iniciarGrabacion() en vuelo (esperando el micro)
  let soltadoEnVuelo = false;   // ...y ya lo has soltado antes de que llegara
  let inicioGrabacion = 0;
  let destinoGrabacion = "adjunto";   // "adjunto" (queda esperando) | "envio" (se manda solo)
  const btnGrabar = $("btn-grabar");

  async function prepararMicro() {
    if (micStream) return micStream;
    try {
      micStream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch {
      micStream = null;
      mostrarAdjunto("⚠️ sin acceso al micrófono");
    }
    return micStream;
  }

  function soltarMicro() {
    if (grabando && mediaRecorder) mediaRecorder.stop();
    if (!micStream) return;
    micStream.getTracks().forEach((t) => t.stop());
    micStream = null;
  }

  async function iniciarGrabacion(destino) {
    if (grabando || arrancando) return;
    arrancando = true;
    soltadoEnVuelo = false;
    const stream = await prepararMicro();
    arrancando = false;
    if (!stream) return;
    const chunks = [];
    destinoGrabacion = destino;
    mediaRecorder = new MediaRecorder(stream);
    mediaRecorder.ondataavailable = (e) => { if (e.data.size) chunks.push(e.data); };
    mediaRecorder.onstop = () => {
      grabando = false;
      btnGrabar.classList.remove("grabando");
      const blob = new Blob(chunks, { type: "audio/webm" });
      const ms = Date.now() - inicioGrabacion;
      // En modo texto el micro no se queda abierto: solo se cachea mientras dure un
      // modo de audio, donde el retardo de pedirlo sí importa.
      if (modoEntrada === "texto") soltarMicro();
      if (destinoGrabacion === "descarte") {
        mostrarAdjunto(etiquetaReposo());
        return;
      }
      if (destinoGrabacion === "envio") {
        if (ms < PTT_MIN_MS || !blob.size) {
          // Un roce de tecla manda ruido a Whisper y CRIS acaba contestando a una
          // transcripción inventada. Mejor no mandar nada.
          mostrarAdjunto("⚠️ muy corto");
          setTimeout(() => mostrarAdjunto(etiquetaReposo()), 1500);
          return;
        }
        mostrarAdjunto(etiquetaReposo());
        enviarMedia("audio", "audio/webm", blob, "");
        return;
      }
      adjuntoPendiente = { tipo: "audio", mime: "audio/webm", blob };
      mostrarAdjunto("🎤 audio grabado");
    };
    inicioGrabacion = Date.now();
    mediaRecorder.start();
    grabando = true;
    btnGrabar.classList.add("grabando");
    mostrarAdjunto(destino === "envio" ? "🎤 escuchando…" : "🎤 grabando…");
    // Si soltaste mientras se pedía el micro (la primera vez sale el diálogo de
    // permiso y tarda), el parar llegó antes que esto: cerrar ya, o se quedaría
    // grabando para siempre.
    if (soltadoEnVuelo) pararGrabacion();
  }

  function pararGrabacion() {
    if (arrancando) { soltadoEnVuelo = true; return; }
    if (grabando && mediaRecorder) mediaRecorder.stop();
  }

  // Cortar tirando lo grabado (no se manda nada): lo usa el modo libre cuando CRIS
  // arranca a hablar en mitad de una captura.
  function abortarGrabacion() {
    if (!grabando || !mediaRecorder) return;
    destinoGrabacion = "descarte";
    mediaRecorder.stop();
  }

  // Etiqueta de la barra cuando no está grabando: en modo libre hay que recordar que
  // el micro sigue armado, o no se distingue de estar apagado.
  function etiquetaReposo() {
    return modoEntrada === "libre" && vadTimer ? "🎧 escuchando" : "";
  }

  // ---------- Modo libre: detección de voz (VAD) en el navegador ----------
  // Sin pulsar nada: la energía del micro por encima del ruido de fondo abre el turno
  // y ~800 ms de silencio lo cierran y lo mandan. Todo en el navegador — el backend no
  // se entera, le llega el mismo /enviar_media que en push-to-talk.
  const VAD_SILENCIO_MS = 800;    // silencio que da la frase por terminada
  const VAD_MAX_MS = 20000;       // tope duro: en una sala ruidosa, no grabar sin fin
  const VAD_ECO_MS = 600;         // margen tras hablar CRIS, para la cola del altavoz
  let vadCtx = null, vadAnalyser = null, vadBuf = null;
  let vadTimer = null, vadCal = null;
  let vadUmbral = 0.02, vadUltimoSonido = 0, vadFinHabla = 0;

  function rmsActual() {
    if (!vadAnalyser) return 0;
    vadAnalyser.getFloatTimeDomainData(vadBuf);
    let s = 0;
    for (let i = 0; i < vadBuf.length; i++) s += vadBuf[i] * vadBuf[i];
    return Math.sqrt(s / vadBuf.length);
  }

  async function arrancarVad() {
    if (vadTimer || vadCal) return;
    const stream = await prepararMicro();
    if (!stream || modoEntrada !== "libre") return;
    try {
      vadCtx = new (window.AudioContext || window.webkitAudioContext)();
      if (vadCtx.state === "suspended") await vadCtx.resume();
      vadAnalyser = vadCtx.createAnalyser();
      vadAnalyser.fftSize = 1024;
      vadBuf = new Float32Array(vadAnalyser.fftSize);
      vadCtx.createMediaStreamSource(stream).connect(vadAnalyser);
    } catch (_) {
      mostrarAdjunto("⚠️ no pude analizar el micrófono");
      return;
    }
    // Calibrado corto contra el ruido REAL de la sala: con un umbral fijo, o salta con
    // el ventilador o no te oye. Mismo criterio que escucha.py con las palmadas.
    mostrarAdjunto("🎧 calibrando…");
    let pico = 0, n = 0;
    vadCal = setInterval(() => {
      pico = Math.max(pico, rmsActual());
      if (++n < 16) return;   // ~1 s
      clearInterval(vadCal); vadCal = null;
      vadUmbral = Math.max(pico * 2.5, 0.015);
      mostrarAdjunto("🎧 escuchando");
      vadTimer = setInterval(vadTick, 60);
    }, 60);
  }

  function pararVad() {
    if (vadCal) { clearInterval(vadCal); vadCal = null; }
    if (vadTimer) { clearInterval(vadTimer); vadTimer = null; }
    abortarGrabacion();
    if (vadCtx) { try { vadCtx.close(); } catch (_) { /* ya cerrado */ } vadCtx = null; }
    vadAnalyser = null; vadBuf = null;
    mostrarAdjunto("");
  }

  function vadTick() {
    // Mientras CRIS habla, el micro NO cuenta: por el altavoz se oiría a sí mismo y
    // acabaría contestándose en bucle. Coste asumido: en modo libre no hay barge-in
    // (para cortarle, cambia a pulsando o teclea).
    if (document.body.dataset.estado === "hablando") {
      abortarGrabacion();
      vadFinHabla = Date.now();
      return;
    }
    if (Date.now() - vadFinHabla < VAD_ECO_MS) return;
    const ahora = Date.now();
    const rms = rmsActual();
    if (!grabando) {
      if (rms > vadUmbral && !arrancando) {
        vadUltimoSonido = ahora;
        iniciarGrabacion("envio");
      }
      return;
    }
    if (rms > vadUmbral) vadUltimoSonido = ahora;
    if (ahora - vadUltimoSonido > VAD_SILENCIO_MS || ahora - inicioGrabacion > VAD_MAX_MS) {
      pararGrabacion();
    }
  }

  function aplicarModoEntrada(modo) {
    modoEntrada = modo;
    document.body.dataset.modoEntrada = modo;
    for (const b of $("modo-entrada").querySelectorAll("button")) {
      b.classList.toggle("sel", b.dataset.modo === modo);
    }
    btnGrabar.title = { texto: "Grabar audio",
                        pulsando: "Mantener H (o este botón) para hablar",
                        libre: "Micro armado: habla cuando quieras" }[modo];
    if (modo === "texto") {
      pararVad();
      soltarMicro();
      return;
    }
    // Sin foco en el cuadro, H dispara desde el primer momento (dentro escribiría una
    // letra). Y se pide el micro ya, no en mitad de la primera frase.
    $("entrada").blur();
    if (modo === "libre") {
      arrancarVad();
    } else {
      pararVad();
      prepararMicro();
    }
  }

  $("modo-entrada").addEventListener("click", (ev) => {
    const btn = ev.target.closest("button[data-modo]");
    if (!btn || btn.disabled) return;
    aplicarModoEntrada(btn.dataset.modo);
    try { localStorage.setItem(MODO_ENTRADA_KEY, btn.dataset.modo); } catch (_) { /* cuota/privado: se ignora */ }
  });

  // El 🎤: en modo texto sigue siendo click-para-adjuntar (como siempre); en modo
  // audio es mantener-para-hablar, el respaldo para cuando el cursor está dentro del
  // cuadro de texto y H escribe letras.
  btnGrabar.addEventListener("click", () => {
    if (modoEntrada !== "texto") return;
    if (grabando) { pararGrabacion(); return; }
    iniciarGrabacion("adjunto");
  });
  // Solo en "pulsando": en libre manda el VAD y una pulsación manual se le cruzaría.
  btnGrabar.addEventListener("pointerdown", (ev) => {
    if (modoEntrada !== "pulsando") return;
    ev.preventDefault();
    iniciarGrabacion("envio");
  });
  for (const ev of ["pointerup", "pointercancel", "pointerleave"]) {
    btnGrabar.addEventListener(ev, () => { if (modoEntrada === "pulsando") pararGrabacion(); });
  }

  // Push-to-talk por teclado: mantener H mientras hablas, soltar para enviar.
  function escribiendo() {
    const tag = (document.activeElement && document.activeElement.tagName) || "";
    return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT";
  }
  document.addEventListener("keydown", (ev) => {
    if (modoEntrada !== "pulsando") return;
    if (ev.key !== PTT_TECLA && ev.key !== PTT_TECLA.toUpperCase()) return;
    // ev.repeat: mantener una tecla dispara keydown en bucle, y solo el primero cuenta.
    if (ev.repeat || escribiendo() || ev.ctrlKey || ev.altKey || ev.metaKey) return;
    ev.preventDefault();
    iniciarGrabacion("envio");
  });
  document.addEventListener("keyup", (ev) => {
    if (ev.key !== PTT_TECLA && ev.key !== PTT_TECLA.toUpperCase()) return;
    pararGrabacion();   // sin mirar el modo: si cambió a media pulsación, hay que soltar igual
  });

  let modoGuardado = "texto";
  try { modoGuardado = localStorage.getItem(MODO_ENTRADA_KEY) || "texto"; } catch (_) { /* privado: se ignora */ }
  aplicarModoEntrada(["pulsando", "libre"].includes(modoGuardado) ? modoGuardado : "texto");

  // Al restaurar "libre" en una recarga no hay gesto del usuario todavía, y el
  // navegador deja el AudioContext suspendido: el análisis daría 0 y nunca saltaría.
  // Al primer clic o tecla, se rearma.
  function rearmarTrasGesto() {
    if (modoEntrada !== "libre") return;
    if (vadCtx && vadCtx.state === "suspended") vadCtx.resume().catch(() => {});
    else if (!vadCtx && !vadCal) arrancarVad();
  }
  document.addEventListener("click", rearmarTrasGesto);
  document.addEventListener("keydown", rearmarTrasGesto);

  // ---------- Respuesta hablada (TTS) ----------
  // Ajuste aparte del modo de entrada: puedes teclear y que te conteste hablando, o
  // hablarle y leerlo. Mueve el mismo flag (estado.py) que las tools activar_modo_voz /
  // desactivar_modo_voz, así que el SSE lo repinta si lo cambia el propio CRISTOPHER.
  const btnTts = $("btn-tts");
  let ttsActivo = false;
  function setModoVoz(activo) {
    ttsActivo = !!activo;
    btnTts.textContent = ttsActivo ? "🔊" : "🔇";
    btnTts.classList.toggle("sel", ttsActivo);
    btnTts.title = ttsActivo ? "Respuesta hablada: activada" : "Respuesta hablada: desactivada";
  }
  btnTts.addEventListener("click", () => {
    const activo = !ttsActivo;
    setModoVoz(activo);   // optimista; el SSE confirma o corrige
    fetch("/modo_voz", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ activo }),
    }).catch(() => setModoVoz(!activo));
  });
  setModoVoz(false);

  // ---------- Modo automático (⚡) ----------
  // Las confirmaciones se aprueban solas (menos los guardarraíles de Control Total).
  // Solo este botón lo mueve: CRISTOPHER no tiene tool para encenderlo.
  const btnAuto = $("btn-auto");
  let autoActivo = false;
  function setModoAuto(activo) {
    autoActivo = !!activo;
    btnAuto.classList.toggle("sel", autoActivo);
    btnAuto.title = autoActivo
      ? "Modo automático: ACTIVADO (no pide confirmación)"
      : "Modo automático: desactivado";
  }
  btnAuto.addEventListener("click", () => {
    const activo = !autoActivo;
    setModoAuto(activo);   // optimista; el SSE confirma o corrige
    fetch("/modo_auto", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ activo }),
    }).catch(() => setModoAuto(!activo));
  });
  setModoAuto(false);

  $("form").addEventListener("submit", (e) => {
    e.preventDefault();
    const input = $("entrada"); const texto = input.value.trim();
    if (adjuntoPendiente) {
      const { tipo, mime, blob } = adjuntoPendiente;
      input.value = "";
      limpiarAdjunto();
      enviarMedia(tipo, mime, blob, texto);
      return;
    }
    if (!texto) return;
    input.value = "";
    fetch("/enviar", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ texto }),
    }).catch(() => {});
  });

  // ---------- Confirmación de acción irreversible ----------
  const confirmOverlay = $("confirm-overlay");
  const confirmTextoRow = $("confirm-texto-row");
  const confirmTextoInput = $("confirm-texto");
  function showConfirm(d) {
    $("confirm-body").textContent = (d && d.texto) || "¿Confirmar la acción?";
    // permite_texto (preguntas de sesión): además de Confirmar/Cancelar,
    // deja responder con un mensaje propio (p. ej. "B" o "indaga más") que sustituye
    // al borrador propuesto — vacío si el usuario solo pincha Confirmar.
    const conTexto = !!(d && d.permite_texto);
    confirmTextoRow.hidden = !conTexto;
    confirmTextoInput.value = "";
    confirmOverlay.hidden = false;
    if (conTexto) confirmTextoInput.focus();
  }
  function resolveConfirm(ok) {
    const texto = confirmTextoRow.hidden ? "" : confirmTextoInput.value.trim();
    confirmOverlay.hidden = true;
    fetch("/confirmar", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ok, texto }),
    }).catch(() => {});
  }
  $("confirm-ok").addEventListener("click", () => resolveConfirm(true));
  $("confirm-cancel").addEventListener("click", () => resolveConfirm(false));
  $("confirm-texto-row").addEventListener("submit", (ev) => {
    ev.preventDefault();
    resolveConfirm(true);
  });

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

  // ---------- NOTAS (panel discreto, por polling como música) ----------
  function pintarNotas(d) {
    const el = $("notas");
    const list = (d && d.notas) || [];
    if (!list.length) { el.innerHTML = '<div class="empty">sin notas</div>'; return; }
    el.innerHTML = list.map((n) =>
      `<div class="item"><small>#${esc(n.id)} · ${esc((n.creado || "").replace("T", " ").slice(0, 16))}</small>${esc(n.texto)}</div>`
    ).join("");
  }
  function notasTick() {
    fetch("/notas").then((r) => r.json()).then(pintarNotas).catch(() => {});
  }
  notasTick();
  setInterval(notasTick, 4000);

  // ---------- MODO SEGURO · PERSONAS (lista blanca, por polling como notas) ----------
  function pintarSeguroPersonas(d) {
    const el = $("seguro-personas");
    if (!el) return;
    const list = (d && d.personas) || [];
    if (!list.length) { el.innerHTML = '<div class="empty">nadie autorizado todavía</div>'; return; }
    el.innerHTML = list.map((p) => {
      const fecha = (p.creado || "").replace("T", " ").slice(0, 16);
      return `<div class="item"><small>${esc(p.fotos)} foto(s)${fecha ? " · desde " + esc(fecha) : ""}</small>${esc(p.nombre)}</div>`;
    }).join("");
  }
  function seguroPersonasTick() {
    fetch("/seguro/personas").then((r) => r.json()).then(pintarSeguroPersonas).catch(() => {});
  }
  seguroPersonasTick();
  setInterval(seguroPersonasTick, 5000);

  // ---------- MODO SEGURO · VISTA DE CÁMARA (panel flotante + icono que parpadea) ----------
  (function () {
    const toggle = $("seguro-preview-toggle");
    const panel = $("seguro-preview-panel");
    const close = $("seguro-preview-close");
    const img = $("seguro-preview-img");
    const empty = $("sp-empty");
    const stats = $("sp-stats");
    if (!toggle || !panel) return;

    const btnArmar = $("sp-armar");
    const modo = $("sp-modo");
    const lista = $("sp-deteccion");
    const enrol = $("sp-enrol");
    const btnPausa = $("sp-pausa");
    let enrolando = false;
    let armadoActual = false;
    let activoActual = false;
    let hayFrame = false;
    let rapido = null;   // poll acelerado, solo mientras el panel está abierto

    const post = (ruta, cuerpo) =>
      fetch(ruta, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(cuerpo || {}),
      }).then((r) => r.json());

    // Una cara por línea, con el MOTIVO escrito: es así como se ajustan los umbrales
    // viendo lo que ve el modo, en vez de tocar números a ciegas.
    function pintarDeteccion(caras) {
      if (!caras || !caras.length) {
        lista.innerHTML = '<div class="empty">sin caras a la vista</div>';
        return;
      }
      lista.innerHTML = caras.map((c) => {
        const detalle = [`${c.altura_px}px`, `mirada ${c.mirada}°`];
        if (c.sim != null) detalle.push(`sim ${c.sim.toFixed(2)}`);
        if (c.estado === "desconocido") detalle.push(`${c.frames_ok}/${c.tope} frames`);
        return `<div class="sp-cara sp-${esc(c.estado)}">${esc(c.texto)}` +
               `<small>${esc(detalle.join(" · "))}</small></div>`;
      }).join("");
    }

    // Sesión guiada en curso: instrucción + cuenta atrás. El panel se abre SOLO cuando
    // arranca una (la lanzas por chat o por voz, así que no tienes por qué tenerlo
    // abierto) — pero sin activar nada: durante el enrolamiento la cámara la tiene
    // seguro.enrolar(), y un POST /seguro/activar aquí se la disputaría.
    function pintarEnrolamiento(en) {
      enrol.hidden = !en;
      enrolando = !!en;
      if (!en) return;
      $("sp-enrol-quien").textContent = en.nombre || "";
      $("sp-enrol-paso").textContent = `pose ${en.indice}/${en.total} · ${en.hecho} guardadas`;
      $("sp-enrol-instr").textContent = en.instruccion || "";
      enrol.classList.toggle("sp-en-pausa", !!en.pausado);
      btnPausa.textContent = en.pausado ? "SEGUIR (ESPACIO)" : "PAUSA (ESPACIO)";
      const c = $("sp-enrol-cuenta");
      c.classList.toggle("sp-ya", !en.pausado && en.cuenta === 0);
      c.classList.toggle("sp-mal", !en.pausado && !!en.motivo);
      c.classList.toggle("sp-pausa", !!en.pausado);
      if (en.pausado) c.textContent = "EN PAUSA";
      else c.textContent = en.motivo ? en.motivo : (en.cuenta === 0 ? "¡FOTO!" : String(en.cuenta));
    }

    // El icono vive al día siempre (poll lento), esté o no abierto el panel: ámbar fijo
    // = cámara abierta mirando; rojo pulsante = armado, puede bloquear la sesión.
    function actualizar() {
      fetch("/seguro/estado").then((r) => r.json()).then((e) => {
        activoActual = !!e.activo;
        armadoActual = !!e.armado;
        toggle.classList.toggle("sp-armado", armadoActual);
        toggle.classList.toggle("sp-vista", activoActual && !armadoActual);
        if (e.enrolamiento && panel.hidden) abrirSoloVer();
        if (panel.hidden) return;   // el resto solo importa con el panel abierto
        pintarEnrolamiento(e.enrolamiento);

        modo.textContent = armadoActual ? "armado · bloquea" : "vista · no bloquea";
        modo.classList.toggle("sp-on", armadoActual);
        btnArmar.textContent = armadoActual ? "DESARMAR" : "ARMAR";
        btnArmar.disabled = !activoActual || e.fase === "BLOQUEADO" || e.fase === "GRACIA";

        // Durante una sesión guiada la cámara la tiene seguro.enrolar(), no el hilo
        // vigilante: hay imagen aunque el modo esté apagado.
        const hayCamara = activoActual || !!e.enrolamiento;
        img.style.display = hayCamara ? "block" : "none";
        empty.style.display = hayCamara && hayFrame ? "none" : "flex";
        if (hayCamara && !img.getAttribute("src")) {
          hayFrame = false;
          empty.textContent = "abriendo la cámara…";
          img.src = "/seguro/preview?" + Date.now();
        } else if (!hayCamara && img.getAttribute("src")) {
          img.removeAttribute("src");
          hayFrame = false;
        }

        // Durante el enrolamiento la lista de veredictos sobra: manda la instrucción.
        pintarDeteccion(activoActual && !e.enrolamiento ? e.deteccion : []);
        let linea = `fase: ${e.fase || "—"}`;
        if (e.camara_ok === false) linea += ` · cámara: ${e.motivo_camara || "error"}`;
        if (e.fase === "GRACIA" && e.gracia_restante_seg != null) linea += ` · gracia ${e.gracia_restante_seg}s`;
        stats.textContent = linea;
      }).catch(() => {});
    }

    // El primer frame del MJPEG es lo que dice que la cámara ya está dando imagen;
    // hasta entonces se deja el cartel, en vez de un rectángulo negro sin explicar.
    img.addEventListener("load", () => { hayFrame = true; empty.style.display = "none"; });
    img.addEventListener("error", () => {
      hayFrame = false;
      empty.textContent = "no llega imagen de la cámara";
      empty.style.display = "flex";
    });

    // Abrir el panel NO arma nada: enciende la cámara en modo vista. Armar es un
    // segundo gesto, dentro del panel — un clic no puede acabar en "se me ha bloqueado
    // la sesión" sin haberlo pedido.
    // Abre el panel SIN tocar el estado del modo: para mirar una sesión de enrolamiento
    // ya en marcha, que es dueña de la cámara.
    function abrirSoloVer() {
      panel.hidden = false;
      if (rapido === null) rapido = setInterval(actualizar, 700);
      if (!img.getAttribute("src")) {
        hayFrame = false;
        img.src = "/seguro/preview?" + Date.now();
      }
    }

    async function abrir() {
      panel.hidden = false;
      lista.innerHTML = '<div class="empty">abriendo la cámara…</div>';
      if (!activoActual) {
        try {
          const r = await post("/seguro/activar", { armado: false });
          if (!r.activo) stats.textContent = r.mensaje || "No se pudo abrir la cámara.";
        } catch (e) { stats.textContent = "No pude hablar con CRISTOPHER."; }
      }
      actualizar();
      if (rapido === null) rapido = setInterval(actualizar, 700);
    }

    // Cerrar en VISTA apaga la cámara (nadie mira, nada vigila). Si está ARMADO, el
    // panel se cierra pero la vigilancia sigue: es su trabajo.
    async function cerrar() {
      panel.hidden = true;
      img.removeAttribute("src");   // corta el stream MJPEG en curso
      hayFrame = false;
      if (rapido !== null) { clearInterval(rapido); rapido = null; }
      // Estado FRESCO del servidor, no las variables cacheadas: con dos clics seguidos
      // el POST de abrir() sigue en vuelo y `activoActual` aún vale false, así que el
      // "apaga la cámara" se saltaba y la webcam quedaba encendida con el panel cerrado.
      let e = null;
      try { e = await fetch("/seguro/estado").then((r) => r.json()); } catch (_) { return; }
      if (e.activo && !e.armado) {
        try { await post("/seguro/desactivar"); } catch (_) { /* el poll lo verá */ }
      }
      actualizar();
    }

    btnArmar.addEventListener("click", async () => {
      const quiero = !armadoActual;
      btnArmar.disabled = true;
      try {
        const r = await post("/seguro/armar", { armado: quiero });
        if (!!r.armado !== quiero) stats.textContent = r.mensaje || "No se pudo cambiar el armado.";
      } catch (e) {
        stats.textContent = "No pude hablar con CRISTOPHER.";
      }
      actualizar();
    });

    // Cerrojo de re-entrada: abrir/cerrar encienden y apagan una cámara de verdad, así
    // que dos clics seguidos no pueden solaparse a medio POST.
    let ocupado = false;
    async function alternar() {
      if (ocupado) return;
      ocupado = true;
      try { await (panel.hidden ? abrir() : cerrar()); } finally { ocupado = false; }
    }
    // Pausa: botón del panel y barra espaciadora. El atajo GLOBAL (pynput, en
    // seguro.py) es el que vale estando de pie lejos del teclado; este es el mismo
    // gesto cuando la ventana del HUD tiene el foco, y por eso ambos van a la misma
    // ruta. Guardas: solo durante una sesión, y nunca si estás escribiendo — si no,
    // el espacio dejaría de escribirse en la consola.
    async function alternarPausa() {
      if (!enrolando) return;
      try { await post("/seguro/enrolar/pausa"); } catch (e) { /* el poll lo verá */ }
      actualizar();
    }
    btnPausa.addEventListener("click", alternarPausa);
    document.addEventListener("keydown", (ev) => {
      if (ev.code !== "Space" || !enrolando) return;
      if (ev.repeat || escribiendo() || ev.ctrlKey || ev.altKey || ev.metaKey) return;
      ev.preventDefault();
      alternarPausa();
    });

    toggle.addEventListener("click", alternar);
    close.addEventListener("click", () => { if (!ocupado) { ocupado = true; cerrar().finally(() => { ocupado = false; }); } });

    // Orden llegada por SSE desde las tools. Al abrir se usa abrirSoloVer() y no
    // abrir(): la cámara ya la encendió el lado Python, así que el navegador solo tiene
    // que enseñar la ventana — un POST /seguro/activar de más aquí podría pisar el
    // estado de armado que la tool acaba de respetar.
    window.__hudSeguroPanel = (d) => {
      if (d && d.abierto) {
        abrirSoloVer();
        actualizar();
      } else if (!ocupado) {
        ocupado = true;
        cerrar().finally(() => { ocupado = false; });
      }
    };

    actualizar();
    setInterval(actualizar, 3000);   // continuo: es lo que mantiene el icono al día
  })();

  // ---------- PROGRAMADAS (cola del programador de tareas, por polling) ----------
  function pintarProgramadas(d) {
    const el = $("programadas");
    const list = (d && d.programadas) || [];
    if (!list.length) { el.innerHTML = '<div class="empty">sin tareas programadas</div>'; return; }
    el.innerHTML = list.map((p) => {
      const partes = [];
      if (p.grupo) partes.push(`grupo '${esc(p.grupo)}' paso ${esc(p.orden)}`);
      if (p.dispara_en_iso) partes.push(esc(p.dispara_en_iso.replace("T", " ").slice(0, 16)));
      if (p.espera_sesion) partes.push(`espera sesión '${esc(p.espera_sesion)}'`);
      partes.push(esc(p.estado));
      return `<div class="item"><small>#${esc(p.id)} · ${partes.join(" · ")}</small>${esc(p.texto)}` +
        `<button type="button" class="prog-cancelar" data-id="${esc(p.id)}" title="Cancelar">×</button></div>`;
    }).join("");
  }
  function programadasTick() {
    fetch("/programadas").then((r) => r.json()).then(pintarProgramadas).catch(() => {});
  }
  programadasTick();
  setInterval(programadasTick, 4000);
  $("programadas").addEventListener("click", (e) => {
    if (!e.target.classList.contains("prog-cancelar")) return;
    const id = parseInt(e.target.dataset.id, 10);
    fetch("/programadas/cancelar", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id }),
    }).then(programadasTick).catch(() => {});
  });

  // ---------- Panel de sesiones vivas de Claude Code (fábrica reutilizable) ----------
  // Dos instancias concretas la usan: `panelAgentes` (todas las sesiones, con
  // formulario de creación manual — modo agentes original, Fase 2) y
  // `panelAutoArreglos` (solo las que CRISTOPHER lanzó por iniciativa propia,
  // origen='auto_arreglo', sin formulario de creación: esas solo las crea él).
  const ICONOS_ESTADO = { arrancando: "🟡", trabajando: "🔧", esperando: "🟢", pregunta: "❓", terminado: "⚪", error: "🔴" };
  const ESTADOS_FINALES = ["terminado", "error"];

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
  function pintarEntradaTranscript(e) {
    const d = document.createElement("div");
    d.className = "e";
    d.innerHTML = `<span class="t">${esc(e.ts || "")}</span><b>${esc(e.rol)}:</b> ${esc(e.texto)}`;
    return d;
  }

  function crearPanelSesiones(els, opts) {
    opts = opts || {};
    const filtroOrigen = opts.filtroOrigen || "";
    const vacio = opts.vacio || "sin sesiones";
    let seleccionada = null;
    let cache = [];

    function renderLista() {
      if (!cache.length) { els.lista.innerHTML = `<div class="empty">${vacio}</div>`; return; }
      els.lista.innerHTML = cache.map((s) => `
        <div class="item${s.nombre === seleccionada ? " sel" : ""}" data-nombre="${esc(s.nombre)}">
          <b>${ICONOS_ESTADO[s.estado] || "•"} ${esc(s.nombre)} [${esc(s.estado)}]</b>
          <small>${esc(s.ruta)} · <span class="ag-tiempo" data-nombre="${esc(s.nombre)}">${esc(duracionTexto(s))}</span></small>
        </div>`).join("");
      els.lista.querySelectorAll(".item").forEach((el) => {
        el.addEventListener("click", () => seleccionar(el.dataset.nombre));
      });
    }

    function pintar(list) {
      cache = filtroOrigen ? (list || []).filter((s) => s.origen === filtroOrigen) : (list || []);
      renderLista();
      if (seleccionada && !cache.some((s) => s.nombre === seleccionada)) {
        seleccionada = null;
        actualizarControles();
      }
    }

    function actualizarControles() {
      const activa = !!seleccionada;
      if (els.mensaje) els.mensaje.disabled = !activa;
      if (els.formEscribir) els.formEscribir.querySelector('button[type="submit"]').disabled = !activa;
      if (els.parar) els.parar.disabled = !activa;
    }

    function appendTranscriptEntry(entrada) {
      if (els.transcript.querySelector(".empty")) els.transcript.innerHTML = "";
      els.transcript.appendChild(pintarEntradaTranscript(entrada));
      els.transcript.scrollTop = els.transcript.scrollHeight;
    }

    function cargarTranscript(nombre) {
      fetch("/sesiones/leer?nombre=" + encodeURIComponent(nombre) + "&n=200")
        .then((r) => r.json())
        .then((d) => {
          const entradas = (d && d.transcript) || [];
          if (!entradas.length) { els.transcript.innerHTML = '<div class="empty">todavía sin transcript</div>'; return; }
          els.transcript.innerHTML = "";
          entradas.forEach((e) => els.transcript.appendChild(pintarEntradaTranscript(e)));
          els.transcript.scrollTop = els.transcript.scrollHeight;
        })
        .catch(() => {});
    }

    function seleccionar(nombre) {
      seleccionada = nombre;
      renderLista();
      actualizarControles();
      cargarTranscript(nombre);
    }

    function tick() {
      const url = filtroOrigen ? "/sesiones?origen=" + encodeURIComponent(filtroOrigen) : "/sesiones";
      fetch(url).then((r) => r.json()).then((d) => pintar(d.sesiones || [])).catch(() => {});
    }

    setInterval(() => {
      els.lista.querySelectorAll(".ag-tiempo").forEach((el) => {
        const s = cache.find((x) => x.nombre === el.dataset.nombre);
        if (s) el.textContent = duracionTexto(s);
      });
    }, 1000);

    if (els.formCrear) {
      els.formCrear.addEventListener("submit", (e) => {
        e.preventDefault();
        const nombre = els.nombre.value.trim(), ruta = els.ruta.value.trim(), tarea = els.tarea.value.trim();
        if (!nombre || !ruta || !tarea) return;
        fetch("/sesiones/crear", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ nombre, ruta, tarea }),
        }).then((r) => r.json()).then((d) => {
          if (d.ok) { els.nombre.value = ""; els.ruta.value = ""; els.tarea.value = ""; tick(); }
          else alert(d.error || "No se pudo crear la sesión.");
        }).catch(() => {});
      });
    }

    if (els.formEscribir) {
      els.formEscribir.addEventListener("submit", (e) => {
        e.preventDefault();
        const mensaje = els.mensaje.value.trim();
        if (!mensaje || !seleccionada) return;
        els.mensaje.value = "";
        fetch("/sesiones/escribir", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ nombre: seleccionada, mensaje }),
        }).catch(() => {});
      });
    }

    if (els.parar) {
      els.parar.addEventListener("click", () => {
        if (!seleccionada) return;
        fetch("/sesiones/parar", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ nombre: seleccionada }),
        }).then(() => tick()).catch(() => {});
      });
    }

    actualizarControles();

    return {
      pintar,
      tick,
      onEntrada(datos) { if (datos.nombre === seleccionada) appendTranscriptEntry(datos.entrada); },
    };
  }

  const panelAgentes = crearPanelSesiones({
    overlay: $("agentes-overlay"), lista: $("agentes-lista"), transcript: $("agentes-transcript"),
    formEscribir: $("agentes-form-escribir"), mensaje: $("agentes-mensaje"), parar: $("agentes-parar"),
    formCrear: $("agentes-form-crear"), nombre: $("agentes-nombre"), ruta: $("agentes-ruta"), tarea: $("agentes-tarea"),
  });
  $("agentes-toggle").addEventListener("click", () => {
    $("agentes-overlay").hidden = false;
    panelAgentes.tick();
  });
  $("agentes-close").addEventListener("click", () => { $("agentes-overlay").hidden = true; });

  const panelAutoArreglos = crearPanelSesiones({
    overlay: $("autoarreglos-overlay"), lista: $("autoarreglos-lista"), transcript: $("autoarreglos-transcript"),
    formEscribir: $("autoarreglos-form-escribir"), mensaje: $("autoarreglos-mensaje"), parar: $("autoarreglos-parar"),
  }, { filtroOrigen: "auto_arreglo", vacio: "sin auto-arreglos" });
  $("autoarreglos-toggle").addEventListener("click", () => {
    $("autoarreglos-overlay").hidden = false;
    panelAutoArreglos.tick();
  });
  $("autoarreglos-close").addEventListener("click", () => { $("autoarreglos-overlay").hidden = true; });

  // ---------- Reinicio total ----------
  // Mata el proceso de CRISTOPHER y lo relanza desde cero (ver
  // hud/__main__.py::_reiniciar_total) — pensado para cuando se prueba un cambio de
  // código y no compensa cerrar la consola y volver a teclear `python -m cristopher`
  // a mano. El proceso muere en cuanto responde, así que tras el POST solo queda
  // sondear /estado hasta que el HUD nuevo levante y recargar la pestaña sola.
  $("reiniciar-toggle").addEventListener("click", () => {
    if (!window.confirm("¿Reiniciar CRISTOPHER? Mata el proceso entero y lo relanza.")) return;
    const btn = $("reiniciar-toggle");
    btn.disabled = true;
    btn.title = "Reiniciando…";
    fetch("/reiniciar", { method: "POST" }).catch(() => {});
    let intentos = 0;
    const esperar = setInterval(() => {
      intentos++;
      fetch("/estado", { cache: "no-store" })
        .then((r) => { if (r.ok) { clearInterval(esperar); location.reload(); } })
        .catch(() => {});
      if (intentos > 60) {
        clearInterval(esperar);
        btn.disabled = false;
        btn.title = "No respondió tras reiniciar — revisa data/reinicio.log";
      }
    }, 1000);
  });

  // ---------- Reloj ----------
  function tick() { $("clock").textContent = new Date().toLocaleTimeString("es-ES"); }
  setInterval(tick, 1000); tick();
})();
