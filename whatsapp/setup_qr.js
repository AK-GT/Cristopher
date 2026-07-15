"use strict";
/**
 * Setup interactivo de UNA SOLA VEZ (equivalente a cristopher/login_google.py):
 * `node whatsapp/setup_qr.js`, escanea el QR con el telefono, la sesion queda
 * persistida en data/whatsapp/session/ y server.js la reutiliza sin pedir QR de nuevo.
 */

const path = require("path");
const qrcodeTerminal = require("qrcode-terminal");
const { ConexionWhatsApp } = require("./wa");

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

async function main() {
  console.log("Preparando conexion con WhatsApp...");
  const conexion = new ConexionWhatsApp(SESSION_DIR);

  await conexion.iniciar({
    mostrarQr: true,
    onQr: (qr) => {
      console.log("\nEscanea este QR desde el telefono: WhatsApp > Dispositivos vinculados\n");
      qrcodeTerminal.generate(qr, { small: true });
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
