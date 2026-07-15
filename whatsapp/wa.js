"use strict";
/**
 * Ciclo de vida de la conexion Baileys (WhatsApp Web no oficial) en un solo sitio,
 * para que server.js y setup_qr.js nunca dupliquen la logica de reconexion.
 *
 * Riesgo conocido: Baileys reimplementa el protocolo de WhatsApp Web sin ser oficial;
 * usarlo conlleva riesgo de que Meta limite o banee el numero. Decision ya asumida.
 */

const path = require("path");
const {
  default: makeWASocket,
  useMultiFileAuthState,
  DisconnectReason,
  fetchLatestBaileysVersion,
} = require("@whiskeysockets/baileys");
const pino = require("pino");

const BACKOFF_MS = [2000, 4000, 8000, 16000, 32000];

function extraerTexto(mensaje) {
  const m = mensaje.message;
  if (!m) return null;
  return (
    m.conversation ||
    (m.extendedTextMessage && m.extendedTextMessage.text) ||
    (m.imageMessage && m.imageMessage.caption) ||
    (m.videoMessage && m.videoMessage.caption) ||
    null
  );
}

class ConexionWhatsApp {
  constructor(sessionDir) {
    this.sessionDir = sessionDir;
    this.sock = null;
    this.estado = "connecting"; // connecting | open | close | logged_out | qr_required
    this._intentos = 0;
    this._onMensaje = null;
    this._onQr = null;
    this._logger = pino({ level: "silent" });
  }

  /**
   * Arranca la conexion. En modo servidor (mostrarQr=false) NO conecta si no hay
   * sesion registrada todavia: evita generar QR en un proceso headless donde nadie
   * lo veria (y evita el "spam" de reconexion mientras nadie escanea). En ese caso
   * queda en estado "qr_required" hasta que se corra setup_qr.js.
   */
  async iniciar({ mostrarQr = false, onQr = null, onMensaje = null } = {}) {
    this._onQr = onQr;
    this._onMensaje = onMensaje;

    const { state, saveCreds } = await useMultiFileAuthState(this.sessionDir);

    if (!mostrarQr && !(state.creds && state.creds.registered)) {
      this.estado = "qr_required";
      return;
    }

    const { version } = await fetchLatestBaileysVersion();
    this.sock = makeWASocket({
      version,
      auth: state,
      logger: this._logger,
      printQRInTerminal: false,
    });

    this.sock.ev.on("creds.update", saveCreds);

    this.sock.ev.on("connection.update", (update) => {
      const { connection, lastDisconnect, qr } = update;

      if (qr) {
        if (this._onQr) {
          this.estado = "connecting";
          this._onQr(qr);
        } else {
          // Nadie va a escanear este QR (modo servidor): no seguimos reintentando.
          this.estado = "qr_required";
        }
      }

      if (connection === "open") {
        this._intentos = 0;
        this.estado = "open";
      } else if (connection === "close") {
        const razon =
          lastDisconnect &&
          lastDisconnect.error &&
          lastDisconnect.error.output &&
          lastDisconnect.error.output.statusCode;

        if (razon === DisconnectReason.loggedOut) {
          this.estado = "logged_out";
          return; // sesion invalidada: no reintentar, hace falta re-escanear
        }

        if (this._onQr === null && this.estado === "qr_required") {
          return; // ya decidimos no perseguir un QR en modo servidor
        }

        this.estado = "close";
        if (this._intentos < BACKOFF_MS.length) {
          const espera = BACKOFF_MS[this._intentos];
          this._intentos += 1;
          setTimeout(() => {
            this.iniciar({ mostrarQr, onQr: this._onQr, onMensaje: this._onMensaje }).catch(
              () => {
                this.estado = "close";
              }
            );
          }, espera);
        }
        // Agotados los reintentos: se queda en "close" sin bucle infinito.
      }
    });

    this.sock.ev.on("messages.upsert", ({ messages, type }) => {
      if (type !== "notify" || !this._onMensaje) return;
      for (const m of messages) {
        if (!m.message) continue;
        const texto = extraerTexto(m);
        if (texto === null) continue;
        this._onMensaje({
          chatId: m.key.remoteJid,
          id: m.key.id,
          fromMe: !!m.key.fromMe,
          texto,
          ts: Number(m.messageTimestamp) || Math.floor(Date.now() / 1000),
          nombre: m.pushName || m.key.remoteJid,
        });
      }
    });
  }

  async enviarMensaje(chatId, texto) {
    if (!this.sock || this.estado !== "open") {
      throw new Error("La conexion de WhatsApp no esta abierta ahora mismo.");
    }
    await this.sock.sendMessage(chatId, { text: texto });
  }
}

module.exports = { ConexionWhatsApp, extraerTexto };
