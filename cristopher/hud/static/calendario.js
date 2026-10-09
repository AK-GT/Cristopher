/* CRISTOPHER HUD — Modo calendario: agenda visual en semana (por defecto) o mes.

   Los datos vienen de GET /calendario (eventos + huecos libres de Google Calendar,
   cacheados 60 s en el servidor). La pantalla se abre sola cuando CRISTOPHER usa
   una tool de calendario (SSE calendario_foco), y mientras pide confirmación para
   crear/mover un evento lo pinta en FANTASMA ámbar donde caería, para ver dónde
   encaja antes de decir que sí. Agendar es sobre todo por voz (el modo de entrada de
   siempre); el clic en una hora libre solo deja la frase preparada en el chat — pasa
   por CRISTOPHER y por la confirmación de siempre, nunca escribe en Google desde aquí.

   Persistencia: los eventos viven en Google (sobreviven a reinicios y se ven en el
   móvil); aquí solo se recuerda la vista elegida (localStorage). */
(function () {
  "use strict";

  const $ = (id) => document.getElementById(id);
  function esc(s) { return String(s == null ? "" : s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c])); }

  const DIAS = ["lun", "mar", "mié", "jue", "vie", "sáb", "dom"];
  const DIAS_LARGO = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"];
  const MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
    "septiembre", "octubre", "noviembre", "diciembre"];
  const DIA_MS = 86400000;
  const REFRESCO_MS = 60000;

  const cuerpo = $("cal-cuerpo");
  if (!cuerpo) return;

  // ---------- Estado ----------
  let vista = "semana";
  try { vista = localStorage.getItem("cal-vista") === "mes" ? "mes" : "semana"; } catch (_) {}
  let ancla = hoy0();            // cualquier día del periodo visible
  let datos = { ok: true, eventos: [], huecos: [] };
  let borrador = null;           // {titulo, inicio, fin, todo_el_dia, id} mientras se confirma
  let seq = 0;                   // descarta respuestas viejas si se navega rápido
  let scrollPendiente = true;    // recolocar el scroll de la semana tras navegar

  // ---------- Fechas ----------
  function hoy0() { const d = new Date(); d.setHours(0, 0, 0, 0); return d; }
  function masDias(d, n) { const r = new Date(d); r.setDate(r.getDate() + n); return r; }
  function lunes(d) { return masDias(d, -((d.getDay() + 6) % 7)); }
  function iso(d) {
    const p = (n) => String(n).padStart(2, "0");
    return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
  }
  function hhmm(d) { return d.toLocaleTimeString("es", { hour: "2-digit", minute: "2-digit", hour12: false }); }
  function mismoDia(a, b) { return a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate(); }
  function diaIdx(d) { return (d.getDay() + 6) % 7; }   // 0 = lunes
  function deFecha(s) { const [y, m, d] = s.split("-").map(Number); return new Date(y, m - 1, d); }

  function rango() {
    if (vista === "semana") return { desde: lunes(ancla), dias: 7 };
    return { desde: lunes(new Date(ancla.getFullYear(), ancla.getMonth(), 1)), dias: 42 };
  }

  // Eventos del servidor + el borrador, con fechas ya parseadas.
  function eventos() {
    const lista = (datos.eventos || []).map((e) => ({ ...e, ini: new Date(e.inicio), fin: new Date(e.fin) }));
    if (borrador) {
      // Al mover un evento, el original no se pinta: solo su nueva posición en fantasma.
      const sin = borrador.id ? lista.filter((e) => e.id !== borrador.id) : lista;
      sin.push({ ...borrador, ini: new Date(borrador.inicio), fin: new Date(borrador.fin), esBorrador: true, ocupa: true });
      return sin;
    }
    return lista;
  }

  // ---------- Carga ----------
  function activo() { return document.body.dataset.modo === "calendario"; }

  function cargar() {
    const { desde, dias } = rango();
    const mio = ++seq;
    pintar();   // pinta ya con lo que haya (navegación instantánea), luego refresca
    fetch(`/calendario?desde=${iso(desde)}&dias=${dias}`)
      .then((r) => r.json())
      .then((d) => { if (mio === seq) { datos = d; pintar(); } })
      .catch((err) => { if (mio === seq) { datos = { ok: false, error: String(err), eventos: [], huecos: [] }; pintar(); } });
  }

  // ---------- Pintado ----------
  function pintar() {
    const { desde } = rango();
    const titulo = $("cal-titulo");
    if (vista === "semana") {
      const fin = masDias(desde, 6);
      titulo.textContent = desde.getMonth() === fin.getMonth()
        ? `${desde.getDate()}–${fin.getDate()} ${MESES[desde.getMonth()]} ${desde.getFullYear()}`
        : `${desde.getDate()} ${MESES[desde.getMonth()].slice(0, 3)} – ${fin.getDate()} ${MESES[fin.getMonth()].slice(0, 3)} ${fin.getFullYear()}`;
    } else {
      titulo.textContent = `${MESES[ancla.getMonth()]} ${ancla.getFullYear()}`;
    }
    const aviso = $("cal-aviso");
    aviso.hidden = datos.ok !== false;
    if (datos.ok === false) { aviso.textContent = "⚠ " + (datos.error || "error leyendo el calendario"); aviso.title = aviso.textContent; }
    document.querySelectorAll(".cal-vistas [data-vista]").forEach((b) => b.classList.toggle("activo", b.dataset.vista === vista));

    // Conservar el scroll de la semana entre refrescos (si no, saltaría cada 60 s).
    const scrollPrevio = cuerpo.querySelector(".cal-sem-scroll");
    const top = scrollPrevio ? scrollPrevio.scrollTop : null;
    cuerpo.innerHTML = '<i class="bracket"></i>';
    if (vista === "semana") pintarSemana(desde, top); else pintarMes(desde);
  }

  function horaPx() {
    return parseFloat(getComputedStyle(cuerpo).getPropertyValue("--cal-hora")) || 46;
  }

  // Reparte en carriles los eventos que se pisan, para que no se tapen entre sí.
  function carriles(lista) {
    lista.sort((a, b) => a.a - b.a || b.b - a.b);
    let grupo = [], finGrupo = -1;
    const cerrar = () => { const n = Math.max(...grupo.map((x) => x.carril)) + 1; grupo.forEach((x) => (x.total = n)); };
    for (const x of lista) {
      if (grupo.length && x.a >= finGrupo) { cerrar(); grupo = []; finGrupo = -1; }
      const usados = new Set(grupo.filter((g) => g.b > x.a).map((g) => g.carril));
      let c = 0; while (usados.has(c)) c++;
      x.carril = c; grupo.push(x); finGrupo = Math.max(finGrupo, x.b);
    }
    if (grupo.length) cerrar();
    return lista;
  }

  function pintarSemana(desde, scrollPrevio) {
    const px = horaPx();
    const hoy = hoy0();
    const evs = eventos();
    const dias = [...Array(7)].map((_, i) => masDias(desde, i));

    const cab = document.createElement("div");
    cab.className = "cal-sem-cab";
    cab.innerHTML = "<div></div>" + dias.map((d, i) =>
      `<div class="${mismoDia(d, hoy) ? "hoy" : ""}">${DIAS[i]}<b>${d.getDate()}</b></div>`).join("");
    cuerpo.appendChild(cab);

    // Fila de día completo
    const entero = document.createElement("div");
    entero.className = "cal-sem-dia-entero";
    entero.innerHTML = "<div></div>" + dias.map((d) => {
      const fin = masDias(d, 1);
      return "<div>" + evs.filter((e) => e.todo_el_dia && e.ini < fin && e.fin > d).map((e) =>
        `<span class="cal-chip entero${e.esBorrador ? " borrador" : ""}" title="${esc(e.titulo)}">${esc(e.titulo)}</span>`).join("") + "</div>";
    }).join("");
    cuerpo.appendChild(entero);

    const scroll = document.createElement("div");
    scroll.className = "cal-sem-scroll";
    const rejilla = document.createElement("div");
    rejilla.className = "cal-sem-rejilla";
    rejilla.innerHTML = '<div class="cal-horas">' + [...Array(24)].map((_, h) =>
      `<div>${h ? String(h).padStart(2, "0") + ":00" : ""}</div>`).join("") + "</div>";

    dias.forEach((d, i) => {
      const ini = d.getTime(), fin = masDias(d, 1).getTime();
      const col = document.createElement("div");
      col.className = "cal-col" + (mismoDia(d, hoy) ? " hoy" : "");
      col.style.height = 24 * px + "px";
      col.dataset.fecha = iso(d);
      const y = (t) => ((t - ini) / 3600000) * px;

      for (const [a, b] of datos.huecos || []) {
        const ta = Math.max(new Date(a).getTime(), ini), tb = Math.min(new Date(b).getTime(), fin);
        if (tb <= ta) continue;
        const h = document.createElement("div");
        h.className = "cal-hueco";
        h.style.top = y(ta) + "px"; h.style.height = (y(tb) - y(ta)) + "px";
        col.appendChild(h);
      }

      const delDia = evs.filter((e) => !e.todo_el_dia && e.ini.getTime() < fin && e.fin.getTime() > ini)
        .map((e) => ({ e, a: Math.max(e.ini.getTime(), ini), b: Math.min(e.fin.getTime(), fin) }));
      for (const x of carriles(delDia)) {
        const el = document.createElement("div");
        const e = x.e;
        el.className = "cal-evento" + (e.esBorrador ? " borrador" : (e.ocupa === false ? " libre" : ""));
        el.style.top = y(x.a) + "px";
        el.style.height = Math.max(y(x.b) - y(x.a) - 2, 16) + "px";
        el.style.left = `calc(${(x.carril / x.total) * 100}% + 2px)`;
        el.style.width = `calc(${100 / x.total}% - 4px)`;
        el.innerHTML = `<span class="h">${hhmm(e.ini)}–${hhmm(e.fin)}${e.esBorrador ? " · confirmando" : ""}</span>${esc(e.titulo)}`;
        el.title = `${e.titulo}\n${hhmm(e.ini)}–${hhmm(e.fin)}${e.ubicacion ? "\n" + e.ubicacion : ""}`;
        if (!e.esBorrador) el.addEventListener("click", (ev) => { ev.stopPropagation(); prepararSobreEvento(e); });
        col.appendChild(el);
      }

      if (mismoDia(d, hoy)) {
        const ahora = document.createElement("div");
        ahora.className = "cal-ahora";
        ahora.style.top = y(Date.now()) + "px";
        col.appendChild(ahora);
      }

      col.addEventListener("click", (ev) => {
        if (ev.target.closest(".cal-evento")) return;
        const r = col.getBoundingClientRect();
        const minutos = Math.floor(((ev.clientY - r.top) / px) * 2) * 30;   // a la media hora
        const cuando = new Date(d); cuando.setMinutes(minutos);
        prepararEnHueco(cuando);
      });
      rejilla.appendChild(col);
    });

    scroll.appendChild(rejilla);
    cuerpo.appendChild(scroll);

    if (scrollPendiente || scrollPrevio == null) {
      // Al llegar a un periodo: a la hora del borrador si se está confirmando uno, si
      // no a las 7:00 — nadie quiere aterrizar viendo la madrugada.
      const h = borrador && !borrador.todo_el_dia ? new Date(borrador.inicio).getHours() - 1 : 7;
      scroll.scrollTop = Math.max(0, h) * px;
      scrollPendiente = false;
    } else {
      scroll.scrollTop = scrollPrevio;
    }
  }

  function pintarMes(desde) {
    const hoy = hoy0();
    const evs = eventos();
    const mes = document.createElement("div");
    mes.className = "cal-mes";
    let html = DIAS.map((d) => `<div class="cal-mes-cab">${d}</div>`).join("");
    for (let i = 0; i < 42; i++) {
      const d = masDias(desde, i), fin = masDias(d, 1);
      const delDia = evs.filter((e) => e.ini < fin && e.fin > d)
        .sort((a, b) => (b.todo_el_dia - a.todo_el_dia) || (a.ini - b.ini));
      const chips = delDia.slice(0, 3).map((e) =>
        `<span class="cal-chip${e.todo_el_dia ? " entero" : ""}${e.esBorrador ? " borrador" : ""}" title="${esc(e.titulo)}">`
        + `${e.todo_el_dia ? "" : hhmm(e.ini) + " "}${esc(e.titulo)}</span>`).join("");
      const mas = delDia.length > 3 ? `<span class="cal-mas">+${delDia.length - 3} más</span>` : "";
      const cls = ["cal-mes-dia", d.getMonth() !== ancla.getMonth() ? "fuera" : "", mismoDia(d, hoy) ? "hoy" : ""].join(" ");
      html += `<div class="${cls}" data-fecha="${iso(d)}"><span class="n">${d.getDate()}</span>${chips}${mas}</div>`;
    }
    mes.innerHTML = html;
    // Clic en un día del mes: abre esa semana (donde se ve la hora y se agenda).
    mes.addEventListener("click", (ev) => {
      const celda = ev.target.closest(".cal-mes-dia");
      if (!celda) return;
      ancla = deFecha(celda.dataset.fecha);
      cambiarVista("semana");
    });
    cuerpo.appendChild(mes);
  }

  // ---------- Clic → frase preparada en el chat ----------
  function diaLargo(d) {
    const idx = diaIdx(d);
    return `${DIAS_LARGO[idx]} ${d.getDate()} de ${MESES[d.getMonth()]}`;
  }
  function preparar(texto) {
    const input = $("cal-entrada");
    input.value = texto;
    input.focus();
    input.setSelectionRange(texto.length, texto.length);
  }
  function prepararEnHueco(cuando) {
    preparar(`Agéndame el ${diaLargo(cuando)} a las ${hhmm(cuando)}: `);
  }
  function prepararSobreEvento(e) {
    preparar(`Sobre «${e.titulo}» del ${diaLargo(e.ini)} a las ${hhmm(e.ini)} (id:${e.id}): `);
  }

  // ---------- Navegación ----------
  function cambiarVista(v) {
    vista = v;
    try { localStorage.setItem("cal-vista", v); } catch (_) {}
    scrollPendiente = true;
    cargar();
  }
  function mover(sentido) {
    ancla = vista === "semana" ? masDias(ancla, 7 * sentido)
      : new Date(ancla.getFullYear(), ancla.getMonth() + sentido, 1);
    scrollPendiente = true;
    cargar();
  }
  $("cal-prev").addEventListener("click", () => mover(-1));
  $("cal-next").addEventListener("click", () => mover(1));
  $("cal-hoy").addEventListener("click", () => { ancla = hoy0(); scrollPendiente = true; cargar(); });
  document.querySelectorAll(".cal-vistas [data-vista]").forEach((b) =>
    b.addEventListener("click", () => cambiarVista(b.dataset.vista)));

  const toggleBtn = $("calendario-toggle");
  if (toggleBtn) {
    toggleBtn.addEventListener("click", () => {
      fetch("/modo_calendario", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ activo: !activo() }),
      }).catch(() => {});
    });
  }

  // ---------- Chat espejo (mismo /enviar y mismo hilo, como en redes) ----------
  const chatLog = $("cal-chat-log");
  const PREFIJO = { user: "tú", answer: "CRIS", thought: "···", tool_call: "→", observation: "obs", error: "!!" };
  window.__hudCalendarioLog = function (e) {
    if (!chatLog) return;
    const d = document.createElement("div");
    d.className = "l " + e.kind;
    d.innerHTML = `<span class="t">${esc(e.hora || "")}</span><b>${esc(PREFIJO[e.kind] || e.kind)}</b> <span>${esc(e.text)}</span>`;
    chatLog.appendChild(d);
    while (chatLog.childElementCount > 80) chatLog.removeChild(chatLog.firstElementChild);
    chatLog.scrollTop = chatLog.scrollHeight;
  };
  $("cal-form").addEventListener("submit", (ev) => {
    ev.preventDefault();
    const input = $("cal-entrada");
    const texto = input.value.trim();
    if (!texto) return;
    input.value = "";
    fetch("/enviar", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ texto }),
    }).catch(() => {});
  });

  // ---------- Ganchos que llama app.js (SSE) ----------
  window.__hudCalendarioSetModo = function (on) {
    if (on) { scrollPendiente = true; cargar(); } else { borrador = null; }
  };
  window.__hudCalendarioFoco = function (f) {
    if (!f) return;
    if (f.fecha) ancla = deFecha(f.fecha);
    borrador = f.borrador || null;
    scrollPendiente = true;
    if (activo()) cargar();
  };
  window.__hudCalendarioCambio = function () {
    borrador = null;
    if (activo()) cargar();
  };

  // El snapshot SSE puede haber llegado antes de que existieran los ganchos.
  if (activo()) cargar();
  setInterval(() => { if (activo()) cargar(); }, REFRESCO_MS);
})();
