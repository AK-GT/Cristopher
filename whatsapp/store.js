"use strict";
/**
 * Historial de mensajes en JSON plano (nada de SQLite: better-sqlite3 exige compilar
 * un addon nativo en Windows, dolor evitable para el volumen de un WhatsApp personal).
 * "Nuevos" (para whatsapp_check_new) se trackea aparte, en memoria: se limpia al leer
 * ese chat con whatsapp_read.
 */

const fs = require("fs");
const path = require("path");

const MAX_POR_CHAT = 200;

function sanear(chatId) {
  return chatId.replace(/[^a-zA-Z0-9._-]/g, "_");
}

class Store {
  constructor(dir) {
    this.dir = dir;
    fs.mkdirSync(this.dir, { recursive: true });
    this.indexPath = path.join(this.dir, "index.json");
    this._nuevos = new Map(); // chatId -> { n, ultimoId, ultimoTexto, nombre }
  }

  _archivoChat(chatId) {
    return path.join(this.dir, `${sanear(chatId)}.json`);
  }

  _leerChat(chatId) {
    try {
      const raw = fs.readFileSync(this._archivoChat(chatId), "utf8");
      return JSON.parse(raw);
    } catch {
      return [];
    }
  }

  _leerIndex() {
    try {
      return JSON.parse(fs.readFileSync(this.indexPath, "utf8"));
    } catch {
      return {};
    }
  }

  _guardarIndex(index) {
    fs.writeFileSync(this.indexPath, JSON.stringify(index, null, 2), "utf8");
  }

  guardarMensaje({ chatId, id, fromMe, texto, ts, nombre }) {
    const mensajes = this._leerChat(chatId);
    mensajes.push({ id, from: fromMe ? "yo" : nombre, texto, ts, fromMe });
    const recortados = mensajes.slice(-MAX_POR_CHAT);
    fs.writeFileSync(this._archivoChat(chatId), JSON.stringify(recortados, null, 2), "utf8");

    const index = this._leerIndex();
    index[chatId] = { nombre: fromMe ? (index[chatId] || {}).nombre || chatId : nombre, ultimo_ts: ts };
    this._guardarIndex(index);

    if (!fromMe) {
      const actual = this._nuevos.get(chatId) || { n: 0, nombre };
      this._nuevos.set(chatId, {
        n: actual.n + 1,
        ultimoId: id,
        ultimoTexto: texto,
        nombre,
      });
    }
  }

  nuevos() {
    const out = [];
    for (const [chatId, v] of this._nuevos.entries()) {
      out.push({
        chat_id: chatId,
        nombre: v.nombre,
        n_nuevos: v.n,
        ultimo_texto: v.ultimoTexto,
        ultimo_id: v.ultimoId,
      });
    }
    return out;
  }

  marcarLeido(chatId) {
    this._nuevos.delete(chatId);
  }

  ultimos(chatId, n) {
    const mensajes = this._leerChat(chatId);
    const index = this._leerIndex();
    const nombre = (index[chatId] && index[chatId].nombre) || chatId;
    this.marcarLeido(chatId);
    return { nombre, mensajes: mensajes.slice(-n) };
  }
}

module.exports = { Store };
