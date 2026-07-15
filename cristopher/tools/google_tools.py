"""Herramientas de Google: Calendar y Gmail (Fase 4).

Lectura de calendario y correo, y envío de correo GATEADO por confirmación humana
(§9: pedir confirmación antes de acciones irreversibles como enviar). El contenido de
los correos es DATO, no instrucciones (ya reforzado en el system prompt).
"""

from __future__ import annotations

import base64
import re
from datetime import datetime, timezone
from email.message import EmailMessage
from typing import Callable

from cristopher.google_auth import GoogleAuthError, get_calendar, get_gmail

_TZ_SUFFIX_RE = re.compile(r"(Z|[+-]\d{2}:\d{2})$")


def _con_zona(dt_iso: str) -> str:
    """Google Calendar exige RFC3339 con zona horaria en dateTime. Si `dt_iso` no
    trae offset (el caso normal cuando lo resuelve el modelo, p. ej.
    '2026-07-17T10:00:00'), le añade el de este equipo."""
    if _TZ_SUFFIX_RE.search(dt_iso):
        return dt_iso
    offset = datetime.now().astimezone().strftime("%z")  # p. ej. '+0200'
    return f"{dt_iso}{offset[:3]}:{offset[3:]}"


# --- Gate de confirmación para acciones irreversibles (§9) --------------------
def _default_confirm(prompt: str) -> bool:
    """Confirmación por consola (por defecto). Devuelve True solo si el usuario acepta."""
    try:
        ans = input(f"{prompt} [s/N] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return False
    return ans in {"s", "si", "sí", "y", "yes"}


_confirm: Callable[[str], bool] = _default_confirm


def set_confirmer(fn: Callable[[str], bool]) -> None:
    """Permite inyectar otra estrategia de confirmación (tests, daemon de la Fase 7)."""
    global _confirm
    _confirm = fn


# --- Calendar (lectura) -------------------------------------------------------
def proximo_evento(n: int = 1) -> str:
    """Devuelve los próximos N eventos del calendario principal.

    Args:
        n: cuántos eventos próximos listar (por defecto 1).
    """
    try:
        svc = get_calendar()
        now = datetime.now(timezone.utc).isoformat()
        events = (
            svc.events()
            .list(
                calendarId="primary",
                timeMin=now,
                maxResults=max(1, min(int(n), 20)),
                singleEvents=True,
                orderBy="startTime",
            )
            .execute()
            .get("items", [])
        )
    except GoogleAuthError as exc:
        return f"ERROR de autenticación con Google: {exc}"
    except Exception as exc:
        return f"ERROR al leer el calendario: {exc}"

    if not events:
        return "No hay eventos próximos en el calendario."

    lines = []
    for e in events:
        start = e.get("start", {}).get("dateTime", e.get("start", {}).get("date", "?"))
        title = e.get("summary", "(sin título)")
        where = e.get("location", "")
        lines.append(f"- {start} · {title}" + (f" · {where}" if where else ""))
    return "\n".join(lines)


# --- Gmail (lectura) ----------------------------------------------------------
def buscar_correos(query: str = "", n: int = 5) -> str:
    """Lista correos recientes o los que casan una consulta de Gmail.

    Args:
        query: consulta estilo Gmail (p. ej. 'is:unread from:jefe@x.com'). Vacío = recientes.
        n: número máximo de correos (por defecto 5).
    """
    try:
        svc = get_gmail()
        msgs = (
            svc.users()
            .messages()
            .list(userId="me", q=query or None, maxResults=max(1, min(int(n), 20)))
            .execute()
            .get("messages", [])
        )
        if not msgs:
            return "No hay correos que coincidan."
        out = []
        for m in msgs:
            full = (
                svc.users()
                .messages()
                .get(userId="me", id=m["id"], format="metadata",
                     metadataHeaders=["From", "Subject", "Date"])
                .execute()
            )
            headers = {h["name"]: h["value"] for h in full.get("payload", {}).get("headers", [])}
            snippet = full.get("snippet", "").strip()
            out.append(
                f"- [id:{m['id']}] De: {headers.get('From','?')}\n"
                f"  Asunto: {headers.get('Subject','(sin asunto)')}\n"
                f"  Fecha: {headers.get('Date','?')}\n"
                f"  {snippet}"
            )
        return "\n".join(out)
    except GoogleAuthError as exc:
        return f"ERROR de autenticación con Google: {exc}"
    except Exception as exc:
        return f"ERROR al leer el correo: {exc}"


# --- Calendar (creación, gateada) ---------------------------------------------
def crear_evento(
    titulo: str, inicio: str, fin: str, descripcion: str = "", ubicacion: str = ""
) -> str:
    """Crea un evento en el Google Calendar principal, PERO solo tras confirmación
    humana explícita (§9). Muestra el borrador y pide OK. Si no se confirma, NO crea.

    Args:
        titulo: título del evento.
        inicio: fecha/hora de inicio en ISO 8601 (p. ej. '2026-07-17T10:00:00').
        fin: fecha/hora de fin en ISO 8601 (p. ej. '2026-07-17T11:00:00').
        descripcion: descripción opcional.
        ubicacion: lugar opcional.
    """
    preview = (
        f"Título: {titulo}\nInicio: {inicio}\nFin: {fin}"
        + (f"\nUbicación: {ubicacion}" if ubicacion else "")
        + (f"\nDescripción: {descripcion}" if descripcion else "")
    )
    if not _confirm(f"¿Crear este evento en el calendario?\n{preview}\n"):
        return "Creación CANCELADA por el usuario. No se creó nada."

    try:
        svc = get_calendar()
        body = {
            "summary": titulo,
            "start": {"dateTime": _con_zona(inicio)},
            "end": {"dateTime": _con_zona(fin)},
        }
        if descripcion:
            body["description"] = descripcion
        if ubicacion:
            body["location"] = ubicacion
        created = svc.events().insert(calendarId="primary", body=body).execute()
        return f"Evento creado: {created.get('summary', titulo)} (id: {created.get('id', '?')})."
    except GoogleAuthError as exc:
        return f"ERROR de autenticación con Google: {exc}"
    except Exception as exc:
        return f"ERROR al crear el evento: {exc}"


# --- Gmail (envío, gateado) ---------------------------------------------------
def enviar_correo(to: str, subject: str, body: str) -> str:
    """Envía un correo, PERO solo tras confirmación humana explícita (§9).

    Muestra el borrador y pide OK. Si no se confirma, NO envía.

    Args:
        to: destinatario.
        subject: asunto.
        body: cuerpo del mensaje.
    """
    preview = f"Para: {to}\nAsunto: {subject}\n\n{body}"
    if not _confirm(f"¿Enviar este correo?\n{preview}\n"):
        return "Envío CANCELADO por el usuario. No se envió nada."

    try:
        svc = get_gmail()
        msg = EmailMessage()
        msg["To"] = to
        msg["Subject"] = subject
        msg.set_content(body)
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        sent = svc.users().messages().send(userId="me", body={"raw": raw}).execute()
        return f"Correo enviado a {to} (id: {sent.get('id','?')})."
    except GoogleAuthError as exc:
        return f"ERROR de autenticación con Google: {exc}"
    except Exception as exc:
        return f"ERROR al enviar el correo: {exc}"


def responder_correo(message_id: str, body: str) -> str:
    """Responde a un correo existente DENTRO del mismo hilo, tras confirmación humana (§9).

    Lee el mensaje original para sacar destinatario y asunto (antepone "Re: " si no lo
    tenía ya) automáticamente, arma el borrador, lo muestra y pide OK antes de enviar.

    Args:
        message_id: id del mensaje original (visible como "[id:XXXX]" en buscar_correos).
        body: cuerpo de la respuesta.
    """
    try:
        svc = get_gmail()
        original = (
            svc.users()
            .messages()
            .get(userId="me", id=message_id, format="metadata",
                 metadataHeaders=["From", "Subject", "Message-ID"])
            .execute()
        )
        thread_id = original.get("threadId")
        headers = {h["name"]: h["value"] for h in original.get("payload", {}).get("headers", [])}
        to = headers.get("From", "")
        subject = headers.get("Subject", "(sin asunto)")
        if not subject.lower().startswith("re:"):
            subject = f"Re: {subject}"
        original_msg_id = headers.get("Message-ID", "")
    except GoogleAuthError as exc:
        return f"ERROR de autenticación con Google: {exc}"
    except Exception as exc:
        return f"ERROR al leer el correo original: {exc}"

    preview = f"Para: {to}\nAsunto: {subject}\n\n{body}"
    if not _confirm(f"¿Enviar esta respuesta?\n{preview}\n"):
        return "Respuesta CANCELADA por el usuario. No se envió nada."

    try:
        msg = EmailMessage()
        msg["To"] = to
        msg["Subject"] = subject
        if original_msg_id:
            msg["In-Reply-To"] = original_msg_id
            msg["References"] = original_msg_id
        msg.set_content(body)
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        sent = (
            svc.users()
            .messages()
            .send(userId="me", body={"raw": raw, "threadId": thread_id})
            .execute()
        )
        return f"Respuesta enviada a {to} en el mismo hilo (id: {sent.get('id','?')})."
    except GoogleAuthError as exc:
        return f"ERROR de autenticación con Google: {exc}"
    except Exception as exc:
        return f"ERROR al enviar la respuesta: {exc}"


def marcar_leido(message_id: str) -> str:
    """Marca un correo como leído (quita la etiqueta UNREAD).

    NO pide confirmación: a diferencia de enviar/responder, es reversible (se puede
    volver a marcar como no leído a mano en Gmail) y no borra ni envía nada — de bajo
    riesgo, por eso no está gateada.

    Args:
        message_id: id del mensaje (visible como "[id:XXXX]" en buscar_correos).
    """
    try:
        svc = get_gmail()
        svc.users().messages().modify(
            userId="me", id=message_id, body={"removeLabelIds": ["UNREAD"]}
        ).execute()
        return f"Correo {message_id} marcado como leído."
    except GoogleAuthError as exc:
        return f"ERROR de autenticación con Google: {exc}"
    except Exception as exc:
        return f"ERROR al marcar el correo como leído: {exc}"
