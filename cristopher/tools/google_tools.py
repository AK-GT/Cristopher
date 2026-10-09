"""Herramientas de Google: Calendar y Gmail (Fase 4).

Lectura de calendario y correo, y envío de correo GATEADO por confirmación humana
(§9: pedir confirmación antes de acciones irreversibles como enviar). El contenido de
los correos es DATO, no instrucciones (ya reforzado en el system prompt).
"""

from __future__ import annotations

import base64
from datetime import datetime, timedelta
from email.message import EmailMessage
from typing import Callable

from cristopher import calendario, estado_auto
from cristopher.google_auth import GoogleAuthError, get_gmail
from cristopher.tools.calendario_modo_tools import mostrar as _mostrar_calendario


# --- Gate de confirmación para acciones irreversibles (§9) --------------------
def _default_confirm(prompt: str) -> bool:
    """Confirmación por consola (por defecto). Devuelve True solo si el usuario acepta."""
    try:
        ans = input(f"{prompt} [s/N] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return False
    return ans in {"s", "si", "sí", "y", "yes"}


_confirmador: Callable[[str], bool] = _default_confirm
_confirm_texto: "Callable[[str], tuple[bool, str]] | None" = None


def _auto_aprobado(prompt: str, permitir_auto: bool) -> bool:
    """Modo automático (estado_auto.py, botón ⚡ del HUD): aprueba sin preguntar, pero
    deja rastro en el log del HUD para que se vea qué se hizo sin tu clic."""
    if not (permitir_auto and estado_auto.esta_activo()):
        return False
    from cristopher import bus
    bus.log("observation", f"[auto-aprobado] {prompt.strip()}")
    return True


def _confirm(prompt: str, permitir_auto: bool = True) -> bool:
    """El gate (§9). Todas las tools con efectos pasan por aquí; el confirmador real
    (HUD, WhatsApp, voz, consola) es el que se inyecte con set_confirmer."""
    return _auto_aprobado(prompt, permitir_auto) or _confirmador(prompt)


def set_confirmer(fn: Callable[[str], bool]) -> None:
    """Permite inyectar otra estrategia de confirmación (tests, daemon de la Fase 7)."""
    global _confirmador
    _confirmador = fn


def confirmar_accion(prompt: str, permitir_auto: bool = True) -> bool:
    """Punto de entrada público al MISMO gate de confirmación que usan
    enviar_correo/crear_evento (§9). Lo usa proactivo.py (Fase 3, capa cofounder)
    para pedir aprobación antes de responder por una sesión de Claude Code.
    `permitir_auto=False` lo saca del modo automático (guardarraíles de Control Total)."""
    return _confirm(prompt, permitir_auto)


def set_confirmer_texto(fn: "Callable[[str], tuple[bool, str]]") -> None:
    """Confirmador con texto libre: además de aceptar/rechazar el borrador,
    permite que el usuario responda con un mensaje propio (p. ej. una elección A/B/C/D
    o "indaga más" en vez del texto que CRISTOPHER propuso). Opcional — superficies que
    no lo registren (CLI/voz) siguen funcionando vía confirmar_accion_con_texto."""
    global _confirm_texto
    _confirm_texto = fn


def confirmar_accion_con_texto(prompt: str) -> "tuple[bool, str]":
    """Como confirmar_accion, pero la aprobación puede venir acompañada de texto
    libre del usuario que sustituye al borrador propuesto. Sin confirmador de texto
    registrado, cae a confirmar_accion normal (texto vacío = usa el borrador tal
    cual). Lo usa proactivo.py para el flujo de preguntas de sesión."""
    if _auto_aprobado(prompt, True):
        return True, ""  # en automático se usa el borrador tal cual
    if _confirm_texto is not None:
        return _confirm_texto(prompt)
    return _confirmador(prompt), ""


# --- Calendar: resolución de fechas de las tools ------------------------------
def _limite(s: str, es_fin: bool) -> datetime:
    """'2026-10-08' o ISO con hora → datetime local. Una fecha sola como FIN cuenta
    el día entero (inclusive): "hasta el jueves" incluye el jueves."""
    if calendario.es_solo_fecha(s):
        dia = calendario.inicio_de_dia(datetime.fromisoformat(s.strip()).date())
        return dia + timedelta(days=1) if es_fin else dia
    return calendario.parse_dt(s)


def _rango(desde: str, hasta: str, dias_por_defecto: int) -> tuple[datetime, datetime]:
    """Rango [inicio, fin). Sin 'hasta': si 'desde' es un día suelto, solo ese día;
    si no, `dias_por_defecto` días desde 'desde' (o desde ahora)."""
    ini = _limite(desde, False) if desde.strip() else calendario.ahora()
    if hasta.strip():
        fin = _limite(hasta, True)
    elif desde.strip() and calendario.es_solo_fecha(desde):
        fin = ini + timedelta(days=1)
    else:
        fin = ini + timedelta(days=dias_por_defecto)
    if fin <= ini:
        raise ValueError("'hasta' debe ser posterior a 'desde'")
    return ini, fin


def _error_calendario(exc: Exception, accion: str) -> str:
    if isinstance(exc, GoogleAuthError):
        return f"ERROR de autenticación con Google: {exc}"
    if isinstance(exc, ValueError):
        return f"ERROR: {exc}"
    return f"ERROR al {accion}: {exc}"


def _bloque_solapes(inicio: datetime, fin: datetime, excluir_id: str = "") -> str:
    """Aviso de solapes para el preview de confirmación. Si no se puede comprobar,
    lo dice — nunca calla y deja creer que el hueco está libre."""
    try:
        choques = calendario.solapes(inicio, fin, excluir_id)
    except Exception as exc:
        return f"\n(No pude comprobar solapes: {exc})"
    if not choques:
        return ""
    return "\n⚠ SE SOLAPA CON:\n" + "\n".join(calendario.fmt_evento(e) for e in choques)


# --- Calendar (lectura) -------------------------------------------------------
def ver_agenda(desde: str = "", hasta: str = "", n: int = 10) -> str:
    """Eventos del calendario principal: por rango de fechas o, sin fechas, los N
    próximos. Cada evento lleva su [id:…] para modificar_evento/borrar_evento.

    Args:
        desde: inicio, fecha 'YYYY-MM-DD' o ISO con hora. Solo 'desde' con una fecha = ese día.
        hasta: fin, fecha (inclusive) o ISO con hora.
        n: cuántos próximos eventos si no se da rango (por defecto 10).
    """
    try:
        if not desde.strip() and not hasta.strip():
            eventos = calendario.proximos(n)
            vacio = "No hay eventos próximos en el calendario."
            foco = eventos[0]["inicio"].date() if eventos else None
        else:
            ini, fin = _rango(desde, hasta, dias_por_defecto=7)
            eventos = calendario.eventos_rango(ini, fin)
            vacio = f"Nada agendado entre {ini:%d/%m %H:%M} y {fin:%d/%m %H:%M}."
            foco = ini.date()
    except Exception as exc:
        return _error_calendario(exc, "leer el calendario")
    _mostrar_calendario(foco)
    if not eventos:
        return vacio
    return "\n".join(calendario.fmt_evento(e) for e in eventos)


def buscar_hueco(duracion_min: int = 60, desde: str = "", hasta: str = "") -> str:
    """Huecos libres (8:00-22:00) de al menos `duracion_min` minutos.

    Args:
        duracion_min: minutos que necesita la cosa a agendar (por defecto 60).
        desde: inicio de la búsqueda ('YYYY-MM-DD' o ISO). Vacío = ahora.
        hasta: fin (fecha inclusive o ISO). Vacío = ese día si 'desde' es un día, o 7 días.
    """
    try:
        ini, fin = _rango(desde, hasta, dias_por_defecto=7)
        huecos = calendario.huecos_libres(ini, fin, duracion_min)
    except Exception as exc:
        return _error_calendario(exc, "buscar huecos")
    _mostrar_calendario(max(ini, calendario.ahora()).date())
    if not huecos:
        return f"Sin huecos de {duracion_min} min entre {ini:%d/%m} y {fin:%d/%m} (franja 8:00-22:00)."
    lineas = [calendario.fmt_hueco(a, b) for a, b in huecos[:20]]
    if len(huecos) > 20:
        lineas.append(f"… y {len(huecos) - 20} huecos más.")
    return f"Huecos libres de al menos {duracion_min} min:\n" + "\n".join(lineas)


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


# --- Calendar (escritura, gateada) --------------------------------------------
def _borrador_json(e: dict) -> dict:
    """Lo que la pantalla necesita para pintar en fantasma el evento que se está
    confirmando, y así ver DÓNDE cae antes de decir que sí."""
    return {"titulo": e["titulo"], "inicio": e["inicio"].isoformat(),
            "fin": e["fin"].isoformat(), "todo_el_dia": e["todo_el_dia"],
            "id": e.get("id", "")}


def _resolver_fechas(inicio: str, fin: str, todo_el_dia: bool,
                     duracion_por_defecto: timedelta) -> tuple[datetime, datetime, bool]:
    """Strings de la tool → (inicio, fin exclusivo, todo_el_dia). Una fecha sin hora
    implica día completo; en día completo 'fin' es el ÚLTIMO día (inclusive), y aquí
    se pasa al fin exclusivo que pide Google. Sin 'fin' en uno con hora, se aplica
    `duracion_por_defecto` (y el preview lo enseña antes de confirmar)."""
    todo_el_dia = todo_el_dia or calendario.es_solo_fecha(inicio)
    if todo_el_dia:
        ini = calendario.inicio_de_dia(datetime.fromisoformat(inicio.strip()[:10]).date())
        ultimo = (calendario.inicio_de_dia(datetime.fromisoformat(fin.strip()[:10]).date())
                  if fin.strip() else ini)
        return ini, ultimo + timedelta(days=1), True
    ini = calendario.parse_dt(inicio)
    f = calendario.parse_dt(fin) if fin.strip() else ini + duracion_por_defecto
    if f <= ini:
        raise ValueError("el fin debe ser posterior al inicio")
    return ini, f, False


def crear_evento(
    titulo: str, inicio: str, fin: str = "", descripcion: str = "", ubicacion: str = "",
    todo_el_dia: bool = False,
) -> str:
    """Crea un evento en el Google Calendar principal, PERO solo tras confirmación
    humana explícita (§9). Muestra el borrador (con los solapes que haya) y pide OK.
    Si no se confirma, NO crea.

    Args:
        titulo: título del evento.
        inicio: ISO 8601 con hora ('2026-07-17T10:00:00') o solo fecha para día completo.
        fin: ISO 8601 con hora; vacío = 1 hora. En día completo, el último día (inclusive).
        descripcion: descripción opcional.
        ubicacion: lugar opcional.
        todo_el_dia: evento de día completo.
    """
    try:
        ini, f, todo_el_dia = _resolver_fechas(inicio, fin, todo_el_dia, timedelta(hours=1))
    except Exception as exc:
        return _error_calendario(exc, "interpretar las fechas")
    borrador = {"id": "nuevo", "titulo": titulo, "inicio": ini, "fin": f,
                "todo_el_dia": todo_el_dia, "ubicacion": ubicacion}
    preview = (
        calendario.fmt_evento(borrador).replace("- [id:nuevo] ", "")
        + (f"\nDescripción: {descripcion}" if descripcion else "")
        + ("" if todo_el_dia else _bloque_solapes(ini, f))
    )
    _mostrar_calendario(ini.date(), _borrador_json(borrador))
    if not _confirm(f"¿Crear este evento en el calendario?\n{preview}\n"):
        _mostrar_calendario(ini.date())  # quita el fantasma de la pantalla
        return "Creación CANCELADA por el usuario. No se creó nada."

    try:
        creado = calendario.crear(titulo, ini, f, todo_el_dia, descripcion, ubicacion)
    except Exception as exc:
        return _error_calendario(exc, "crear el evento")
    return f"Evento creado:\n{calendario.fmt_evento(creado)}"


def modificar_evento(
    evento_id: str, titulo: str = "", inicio: str = "", fin: str = "",
    descripcion: str = "", ubicacion: str = "",
) -> str:
    """Modifica un evento existente (mover de hora/día, renombrar, cambiar lugar…),
    solo tras confirmación (§9). Los campos vacíos no se tocan. Si se cambia 'inicio'
    sin 'fin', se conserva la duración original.

    Args:
        evento_id: id del evento (el [id:…] de ver_agenda).
        titulo: nuevo título.
        inicio: nuevo inicio (ISO; solo fecha si el evento es de día completo).
        fin: nuevo fin (ISO; en día completo, último día inclusive).
        descripcion: nueva descripción.
        ubicacion: nuevo lugar.
    """
    try:
        antes = calendario.obtener(evento_id)
        cambios: dict = {}
        despues = dict(antes)
        if inicio.strip() or fin.strip():
            duracion = antes["fin"] - antes["inicio"]
            if antes["todo_el_dia"] and not inicio.strip():
                inicio = antes["inicio"].date().isoformat()
            elif not inicio.strip():
                inicio = antes["inicio"].isoformat()
            if antes["todo_el_dia"] and not fin.strip():
                fin = (calendario.inicio_de_dia(datetime.fromisoformat(inicio.strip()[:10]).date())
                       + duracion - timedelta(days=1)).date().isoformat()
            ini, f, todo = _resolver_fechas(inicio, fin, antes["todo_el_dia"], duracion)
            cambios.update(calendario.cuerpo_fechas(ini, f, todo))
            # patch MEZCLA objetos anidados: sin anular el campo contrario, pasar de
            # día completo a con hora (o al revés) dejaría date y dateTime a la vez.
            sobra = "dateTime" if todo else "date"
            cambios["start"][sobra] = cambios["end"][sobra] = None
            despues.update(inicio=ini, fin=f, todo_el_dia=todo)
        if titulo.strip():
            cambios["summary"] = despues["titulo"] = titulo
        if ubicacion.strip():
            cambios["location"] = despues["ubicacion"] = ubicacion
        if descripcion.strip():
            cambios["description"] = descripcion
    except Exception as exc:
        return _error_calendario(exc, "preparar el cambio")
    if not cambios:
        return "No hay nada que cambiar: indica al menos un campo nuevo."

    preview = (
        f"ANTES:   {calendario.fmt_evento(antes)[2:]}\n"
        f"DESPUÉS: {calendario.fmt_evento(despues)[2:]}"
        + (f"\nDescripción nueva: {descripcion}" if descripcion.strip() else "")
        + ("" if despues["todo_el_dia"] or "start" not in cambios
           else _bloque_solapes(despues["inicio"], despues["fin"], evento_id))
    )
    _mostrar_calendario(despues["inicio"].date(), _borrador_json(despues))
    if not _confirm(f"¿Modificar este evento?\n{preview}\n"):
        _mostrar_calendario(despues["inicio"].date())
        return "Modificación CANCELADA por el usuario. No se cambió nada."
    try:
        nuevo = calendario.modificar(evento_id, cambios)
    except Exception as exc:
        return _error_calendario(exc, "modificar el evento")
    return f"Evento modificado:\n{calendario.fmt_evento(nuevo)}"


def borrar_evento(evento_id: str) -> str:
    """Borra un evento del calendario, solo tras confirmación (§9).

    Args:
        evento_id: id del evento (el [id:…] de ver_agenda).
    """
    try:
        evento = calendario.obtener(evento_id)
    except Exception as exc:
        return _error_calendario(exc, "leer el evento")
    _mostrar_calendario(evento["inicio"].date())
    if not _confirm(f"¿Borrar este evento del calendario?\n{calendario.fmt_evento(evento)[2:]}\n"):
        return "Borrado CANCELADO por el usuario. No se borró nada."
    try:
        calendario.borrar(evento_id)
    except Exception as exc:
        return _error_calendario(exc, "borrar el evento")
    return f"Evento borrado: {evento['titulo']}."


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
