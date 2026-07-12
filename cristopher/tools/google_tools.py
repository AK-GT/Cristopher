"""Herramientas de Google: Calendar y Gmail (Fase 4).

Lectura de calendario y correo, y envío de correo GATEADO por confirmación humana
(§9: pedir confirmación antes de acciones irreversibles como enviar). El contenido de
los correos es DATO, no instrucciones (ya reforzado en el system prompt).
"""

from __future__ import annotations

import base64
from datetime import datetime, timezone
from email.message import EmailMessage
from typing import Callable

from cristopher.google_auth import GoogleAuthError, get_calendar, get_gmail


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
                f"- De: {headers.get('From','?')}\n"
                f"  Asunto: {headers.get('Subject','(sin asunto)')}\n"
                f"  Fecha: {headers.get('Date','?')}\n"
                f"  {snippet}"
            )
        return "\n".join(out)
    except GoogleAuthError as exc:
        return f"ERROR de autenticación con Google: {exc}"
    except Exception as exc:
        return f"ERROR al leer el correo: {exc}"


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
