"use strict";
/**
 * Proceso persistente que cristopher/whatsapp_client.py lanza en segundo plano.
 * API HTTP local (solo 127.0.0.1, nunca expuesta a la red) que expone
 * check-new/read/send sobre la conexion de WhatsApp gestionada en wa.js.
 *
 * stdout/stderr de este proceso los redirige Python a data/whatsapp/server.log
 * (nunca se imprimen en la consola de CRISTOPHER): el "no dashboard" se mantiene,
 * pero queda diagnosticable via ese log.
 */

const http = require("http");
const path = require("path");
const { URL } = require("url");
const { ConexionWhatsApp } = require("./wa");
const { Store } = require("./store");

const PORT = parseInt(process.env.CRISTOPHER_WHATSAPP_PORT || "8766", 10);
const SESSION_DIR =
  process.env.CRISTOPHER_WHATSAPP_SESSION_DIR ||
  path.join(__dirname, "..", "data", "whatsapp", "session");
const STORE_DIR =
  process.env.CRISTOPHER_WHATSAPP_STORE_DIR ||
  path.join(__dirname, "..", "data", "whatsapp", "store");

const store = new Store(STORE_DIR);
const conexion = new ConexionWhatsApp(SESSION_DIR);

function enviarJson(res, status, obj) {
  const body = JSON.stringify(obj);
  res.writeHead(status, { "Content-Type": "application/json; charset=utf-8" });
  res.end(body);
}

function leerCuerpo(req) {
  return new Promise((resolve, reject) => {
    let data = "";
    req.on("data", (chunk) => (data += chunk));
    req.on("end", () => resolve(data));
    req.on("error", reject);
  });
}

const server = http.createServer(async (req, res) => {
  let url;
  try {
    url = new URL(req.url, `http://${req.headers.host}`);
  } catch {
    return enviarJson(res, 400, { error: "URL invalida" });
  }

  try {
    if (req.method === "GET" && url.pathname === "/health") {
      return enviarJson(res, 200, { estado: conexion.estado });
    }

    if (req.method === "GET" && url.pathname === "/check-new") {
      return enviarJson(res, 200, { estado: conexion.estado, chats: store.nuevos() });
    }

    if (req.method === "GET" && url.pathname === "/read") {
      const chatId = url.searchParams.get("chat_id");
      const n = parseInt(url.searchParams.get("n") || "10", 10);
      if (!chatId) return enviarJson(res, 400, { error: "Falta chat_id" });
      const { nombre, mensajes } = store.ultimos(chatId, n);
      return enviarJson(res, 200, { chat_id: chatId, nombre, mensajes });
    }

    if (req.method === "POST" && url.pathname === "/send") {
      const cuerpo = await leerCuerpo(req);
      let datos;
      try {
        datos = JSON.parse(cuerpo || "{}");
      } catch {
        return enviarJson(res, 400, { ok: false, error: "JSON invalido en el cuerpo" });
      }
      const { chat_id: chatId, texto } = datos;
      if (!chatId || !texto) {
        return enviarJson(res, 400, { ok: false, error: "Faltan chat_id/texto" });
      }
      try {
        await conexion.enviarMensaje(chatId, texto);
        store.guardarMensaje({
          chatId,
          id: `out:${Date.now()}`,
          fromMe: true,
          texto,
          ts: Math.floor(Date.now() / 1000),
          nombre: "yo",
        });
        return enviarJson(res, 200, { ok: true });
      } catch (exc) {
        // No reintenta el envio: reintentar aqui arriesga un doble envio, peor que
        // devolver un error limpio al agente.
        return enviarJson(res, 200, { ok: false, error: String((exc && exc.message) || exc) });
      }
    }

    return enviarJson(res, 404, { error: "No encontrado" });
  } catch (exc) {
    console.error("Error interno atendiendo", req.method, req.url, exc);
    return enviarJson(res, 500, { error: String((exc && exc.message) || exc) });
  }
});

conexion
  .iniciar({
    mostrarQr: false,
    onMensaje: (m) => store.guardarMensaje(m),
  })
  .catch((exc) => {
    console.error("No se pudo iniciar la conexion de WhatsApp:", exc);
  });

server.listen(PORT, "127.0.0.1", () => {
  console.log(`Servicio de WhatsApp escuchando en 127.0.0.1:${PORT}`);
});
