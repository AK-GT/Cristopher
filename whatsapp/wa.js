"use strict";
/**
 * Ciclo de vida de la conexion Baileys (WhatsApp Web no oficial) en un solo sitio,
 * para que server.js y setup_qr.js nunca dupliquen la logica de reconexion.
 *
 * Riesgo conocido: Baileys reimplementa el protocolo de WhatsApp Web sin ser oficial;
 * usarlo conlleva riesgo de que Meta limite o banee el numero. Decision ya asumida.
 */

const fs = require("fs");
const path = require("path");
const {
  default: makeWASocket,
  useMultiFileAuthState,
  DisconnectReason,
  fetchLatestBaileysVersion,
  jidNormalizedUser,
  downloadMediaMessage,
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
    (m.documentMessage && m.documentMessage.caption) ||
    null
  );
}

/**
 * Nota de voz o foto (con o sin caption) en el mensaje, o null si no trae media.
 * `extraerTexto` sigue cubriendo el texto/caption; esto es aparte porque una nota de
 * voz nunca tiene caption y antes se descartaba entera (`texto === null -> continue`).
 */
function detectarMedia(mensaje) {
  const m = mensaje.message;
  if (!m) return null;
  if (m.audioMessage) {
    return { tipo: "audio", mimetype: m.audioMessage.mimetype || "audio/ogg" };
  }
  if (m.imageMessage) {
    return { tipo: "imagen", mimetype: m.imageMessage.mimetype || "image/jpeg" };
  }
  if (m.documentMessage) {
    return {
      tipo: "documento",
      mimetype: m.documentMessage.mimetype || "application/octet-stream",
    };
  }
  return null;
}

function sanearNombreArchivo(id) {
  return String(id).replace(/[^a-zA-Z0-9._-]/g, "_");
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
    this._authState = null;
  }

  /**
   * JIDs propios normalizados (chat "Note to self"). WhatsApp esta migrando el
   * direccionamiento a "LID" (identificador de privacidad): en cuentas ya migradas,
   * los mensajes del chat "Note to self" llegan con chatId = creds.me.lid, NO con
   * creds.me.id (el JID de telefono de siempre). Se devuelven ambos si existen para
   * no depender de cual use WhatsApp en cada cuenta/momento.
   */
  getSelfIds() {
    const me = this._authState && this._authState.creds && this._authState.creds.me;
    if (!me) return [];
    return [me.lid, me.id].filter(Boolean).map(jidNormalizedUser);
  }

  /** El JID propio preferido (lid si existe, si no el de telefono), o null. */
  getSelfId() {
    const ids = this.getSelfIds();
    return ids[0] || null;
  }

  /** Carpeta donde se guardan los adjuntos descargados, hermana de sessionDir. */
  _mediaDir() {
    return path.join(path.dirname(this.sessionDir), "media");
  }

  /**
   * Descarga los bytes de una nota de voz o imagen y los guarda en disco (no inline
   * en la respuesta HTTP, para no inflar /check-new ni /read). `reuploadRequest` deja
   * que Baileys re-pida el media si el servidor de WhatsApp ya lo dio por expirado.
   */
  async _descargarMedia(mensaje, mimetype) {
    const buffer = await downloadMediaMessage(
      mensaje,
      "buffer",
      {},
      { logger: this._logger, reuploadRequest: this.sock.updateMediaMessage }
    );
    const ext = (mimetype.split(";")[0].split("/")[1] || "bin").replace(/[^a-z0-9]/gi, "");
    const dir = this._mediaDir();
    fs.mkdirSync(dir, { recursive: true });
    const ruta = path.join(dir, `${sanearNombreArchivo(mensaje.key.id)}.${ext}`);
    fs.writeFileSync(ruta, buffer);
    return ruta;
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
    this._authState = state;

    // "registered" en Baileys solo se marca true en el flujo de codigo de
    // emparejamiento (8 digitos); el flujo QR que usa setup_qr.js deja ese
    // flag en false aunque la vinculacion haya sido exitosa. La señal real de
    // "ya esta vinculado" es creds.me (se puebla en el pair-success del QR).
    if (!mostrarQr && !(state.creds && state.creds.me)) {
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
        const media = detectarMedia(m);
        if (texto === null && !media) continue; // ni texto ni media reconocida: se ignora

        const payload = {
          chatId: m.key.remoteJid,
          id: m.key.id,
          fromMe: !!m.key.fromMe,
          texto: texto || "",
          tipo: media ? media.tipo : "texto",
          ts: Number(m.messageTimestamp) || Math.floor(Date.now() / 1000),
          nombre: m.pushName || m.key.remoteJid,
        };

        if (!media) {
          this._onMensaje(payload);
          continue;
        }

        // La descarga es async: el mensaje se emite solo cuando el archivo ya está
        // en disco, para que mediaPath siempre sea válido del lado de Python. Si la
        // descarga falla (media expirado, red caída...) se degrada a solo-texto
        // cuando hay caption; si no hay nada más, este mensaje concreto se pierde.
        this._descargarMedia(m, media.mimetype)
          .then((mediaPath) => {
            this._onMensaje({ ...payload, mediaPath, mimetype: media.mimetype });
          })
          .catch((exc) => {
            // this._logger va en modo "silent" a propósito (ver constructor); console.error
            // sí llega a data/whatsapp/server.log (server.js redirige stdout/stderr ahí).
            console.error("fallo descargando media de WhatsApp:", exc);
            if (texto) this._onMensaje(payload);
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
