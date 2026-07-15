"""Tools de WhatsApp: leer y enviar mensajes del WhatsApp PERSONAL del usuario, bajo
petición explícita — NUNCA un bot autónomo ni una respuesta automática.

Riesgo conocido (ya asumido, no re-litigar): esta integración usa Baileys, una
librería NO OFICIAL que reimplementa WhatsApp Web; su uso conlleva riesgo de que Meta
limite o banee el número.

A diferencia de `enviar_correo`, `whatsapp_send` NO pasa por el gate de confirmación
de `google_tools._confirm`: el propio encargo de esta integración pide control
puramente conversacional (se llama solo cuando el usuario lo pide explícitamente en
ese turno), sin panel de aprobación por botón. No añadir esa llamada aquí.
"""

from __future__ import annotations

from cristopher import whatsapp_client
from cristopher.whatsapp_client import WhatsAppError

_ESTADOS_SESION_CAIDA = ("logged_out", "qr_required")
_MSG_SESION_CAIDA = (
    "La sesión de WhatsApp no está enlazada (o se cerró). Hace falta escanear el QR: "
    "ejecuta 'node whatsapp/setup_qr.js' en la carpeta whatsapp/ del proyecto."
)


def whatsapp_check_new() -> str:
    """Comprueba si han llegado mensajes nuevos de WhatsApp (personal) y de quién.

    Úsala para saber si te han escrito, o antes de leer un chat concreto.
    """
    try:
        data = whatsapp_client.check_new()
    except WhatsAppError as exc:
        return f"ERROR: {exc}"

    if data.get("estado") in _ESTADOS_SESION_CAIDA:
        return f"ERROR: {_MSG_SESION_CAIDA}"

    chats = data.get("chats") or []
    if not chats:
        return "No hay mensajes nuevos de WhatsApp."

    lineas = [
        f"- {c['nombre']} ({c['n_nuevos']} nuevo(s), chat_id={c['chat_id']}): «{c['ultimo_texto']}»"
        for c in chats
    ]
    return "Mensajes nuevos de WhatsApp:\n" + "\n".join(lineas)


def whatsapp_read(chat_id: str, n: int = 10) -> str:
    """Lee los últimos mensajes de un chat de WhatsApp por su chat_id.

    Args:
        chat_id: id del chat (el que devuelve whatsapp_check_new).
        n: cuántos mensajes recientes leer (por defecto 10).
    """
    chat_id = (chat_id or "").strip()
    if not chat_id:
        return "ERROR: falta el chat_id (usa whatsapp_check_new para obtenerlo)."
    try:
        n = max(1, min(int(n), 200))
    except (TypeError, ValueError):
        n = 10

    try:
        data = whatsapp_client.read(chat_id, n)
    except WhatsAppError as exc:
        return f"ERROR: {exc}"

    mensajes = data.get("mensajes") or []
    nombre = data.get("nombre", chat_id)
    if not mensajes:
        return f"No tengo mensajes guardados todavía de ese chat ({nombre})."

    lineas = [f"[{m['from']}] {m['texto']}" for m in mensajes]
    return f"Últimos mensajes con {nombre}:\n" + "\n".join(lineas)


def whatsapp_send(chat_id: str, texto: str) -> str:
    """Envía un mensaje de WhatsApp a un chat concreto.

    Úsala SOLO cuando el usuario pida explícitamente, en este mismo turno,
    enviar/responder algo por WhatsApp a alguien. No hay confirmación por botón
    para esta herramienta: el control lo da directamente la instrucción del usuario.

    Args:
        chat_id: id del chat destino (el que devuelve whatsapp_check_new).
        texto: texto exacto a enviar.
    """
    chat_id = (chat_id or "").strip()
    texto = (texto or "").strip()
    if not chat_id or not texto:
        return "ERROR: falta chat_id o texto para enviar."

    try:
        whatsapp_client.send(chat_id, texto)
    except WhatsAppError as exc:
        return f"ERROR: no pude enviar el mensaje de WhatsApp: {exc}"

    return f"Mensaje enviado por WhatsApp a {chat_id}."
