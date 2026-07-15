"""Notificación remota para avisos de nivel 3 del demonio proactivo (Fase 7 — pulido).

Envía un correo directo cuando un aviso se clasifica como urgente, para que llegue
aunque el usuario no esté delante del PC ni escuchando la voz. A diferencia de
`tools/google_tools.py::enviar_correo`, NO pasa por el gate de confirmación: no es un
correo redactado a petición del usuario sino un aviso generado por el propio sistema
(excepción a §9 acordada explícitamente para este caso).
"""

from __future__ import annotations

import base64
from email.message import EmailMessage

from cristopher.config import NOTIFICAR_EMAIL_TO


def notificar(mensaje: str) -> None:
    """Envía `mensaje` por correo a NOTIFICAR_EMAIL_TO. Lanza en caso de fallo; el
    llamador (Demonio._entregar) decide cómo reportarlo sin morir."""
    if not NOTIFICAR_EMAIL_TO:
        raise RuntimeError(
            "CRISTOPHER_NOTIFICAR_EMAIL no está configurado; no hay a quién avisar."
        )

    from cristopher.google_auth import get_gmail

    svc = get_gmail()
    msg = EmailMessage()
    msg["To"] = NOTIFICAR_EMAIL_TO
    msg["Subject"] = "CRISTOPHER — aviso urgente"
    msg.set_content(mensaje)
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    svc.users().messages().send(userId="me", body={"raw": raw}).execute()
