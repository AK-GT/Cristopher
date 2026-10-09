"use strict";
/**
 * Setup interactivo de UNA SOLA VEZ (equivalente a cristopher/login_google.py):
 * `node whatsapp/setup_qr.js`, escanea el QR con el telefono, la sesion queda
 * persistida en data/whatsapp/session/ y server.js la reutiliza sin pedir QR de nuevo.
 */

const http = require("http");
const path = require("path");
const qrcodeTerminal = require("qrcode-terminal");
const qrcode = require("qrcode");
const { ConexionWhatsApp } = require("./wa");

const QR_PNG_PATH = path.join(__dirname, "qr.png");

const SESSION_DIR =
  process.env.CRISTOPHER_WHATSAPP_SESSION_DIR ||
  path.join(__dirname, "..", "data", "whatsapp", "session");

const TIMEOUT_MS = 120000; // 2 min: tiempo de sobra para escanear, sin esperar para siempre

function esperarConexion(conexion) {
  return new Promise((resolve, reject) => {
    const inicio = Date.now();
    const intervalo = setInterval(() => {
      if (conexion.estado === "open") {
        clearInterval(intervalo);
        resolve();
      } else if (conexion.estado === "logged_out") {
        clearInterval(intervalo);
        reject(new Error("La sesion se cerro antes de completar el emparejamiento."));
      } else if (Date.now() - inicio > TIMEOUT_MS) {
        clearInterval(intervalo);
        reject(new Error("Tiempo de espera agotado esperando a que se escanee el QR."));
      }
    }, 500);
  });
}

function servidorVivo() {
  const port = parseInt(process.env.CRISTOPHER_WHATSAPP_PORT || "8766", 10);
  return new Promise((resolve) => {
    const req = http.get({ host: "127.0.0.1", port, path: "/health", timeout: 1500 }, (res) => {
      res.resume();
      resolve(true);
    });
    req.on("error", () => resolve(false));
    req.on("timeout", () => {
      req.destroy();
      resolve(false);
    });
  });
}

async function main() {
  // Dos procesos con la misma sesion se echan el uno al otro (error 440) y pisan
  // creds.json: hay que vincular con server.js parado.
  if (await servidorVivo()) {
    console.error(
      "ERROR: el servicio de WhatsApp (server.js) esta corriendo. Cierra CRISTOPHER " +
        "(o mata ese node) antes de vincular."
    );
    process.exit(1);
  }
  console.log("Preparando conexion con WhatsApp...");
  const conexion = new ConexionWhatsApp(SESSION_DIR);

  await conexion.iniciar({
    mostrarQr: true,
    onQr: (qr) => {
      console.log("\nEscanea este QR desde el telefono: WhatsApp > Dispositivos vinculados\n");
      qrcodeTerminal.generate(qr, { small: true });
      // El QR en ASCII no siempre se ve bien fuera de una terminal real (p.ej. via
      // chat/CI): tambien lo dejamos como imagen para abrir y escanear a ojo.
      qrcode.toFile(QR_PNG_PATH, qr, { width: 400 }, (err) => {
        if (!err) console.log(`(tambien guardado como imagen en: ${QR_PNG_PATH})\n`);
      });
    },
  });

  await esperarConexion(conexion);
  console.log("\nConectado a WhatsApp. Sesion guardada en:", SESSION_DIR);
  console.log(
    "Ya puedes cerrar esta ventana: el servicio de CRISTOPHER (server.js) reutilizara " +
      "esta sesion sin volver a pedir QR."
  );
  process.exit(0);
}

main().catch((exc) => {
  console.error("ERROR:", (exc && exc.message) || exc);
  process.exit(1);
});
