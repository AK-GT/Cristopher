"""Demonio de proactividad de CRISTOPHER (Fase 7).

Vigila en segundo plano calendario, correo y recordatorios, y AVISA él por su cuenta.
Cada aviso se clasifica con un mini-modelo en 3 niveles de prioridad; el nivel decide la
insistencia: 1 = terminal atenuado, 2 = terminal destacado, 3 = terminal + voz (TTS).

Uso:  python -m cristopher.proactivo
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Callable, Optional

from cristopher.config import (
    CLASSIFIER_MODEL,
    FALLBACK_MODEL,
    GMAIL_QUERY_PROACTIVO,
    LEAD_MINUTOS,
    POLL_SEGUNDOS,
)
from cristopher.recordatorios import get_recordatorios

CYAN = "\033[36m"
DIM = "\033[2m"
AMBER = "\033[33m"
RED = "\033[31m"
BOLD = "\033[1m"
RESET = "\033[0m"


def clasificar(texto: str) -> int:
    """Clasifica la urgencia del aviso en 1 (informativo), 2 (normal) o 3 (urgente).
    Usa un mini-modelo con fallback; si todo falla, devuelve 2 (§8 degrada con elegancia)."""
    from google import genai
    from google.genai import errors

    from cristopher.config import get_api_key

    prompt = (
        "Clasifica la URGENCIA de este aviso para el usuario en un dígito: "
        "1 = informativo/poco urgente, 2 = normal, 3 = urgente (requiere atención ya). "
        "Responde SOLO el dígito.\n\nAviso: " + texto
    )
    try:
        client = genai.Client(api_key=get_api_key())
    except Exception:
        return 2
    for model in [CLASSIFIER_MODEL, FALLBACK_MODEL]:
        if not model:
            continue
        try:
            r = client.models.generate_content(model=model, contents=prompt)
            for ch in (r.text or ""):
                if ch in "123":
                    return int(ch)
            return 2
        except errors.APIError as exc:
            if getattr(exc, "code", None) == 429:
                continue  # sin cuota: prueba el siguiente modelo
            return 2
        except Exception:
            return 2
    return 2


class Demonio:
    """Reúne avisos de las fuentes, los clasifica y los entrega según su nivel."""

    def __init__(
        self,
        hablar_fn: Optional[Callable[[str], None]] = None,
        clasificar_fn: Optional[Callable[[str], int]] = None,
        on_aviso: Optional[Callable[[int, str], None]] = None,
    ) -> None:
        self._hablar = hablar_fn
        self._clasificar = clasificar_fn or clasificar
        self._on_aviso = on_aviso  # callback opcional (p. ej. el HUD) por cada aviso
        self._rec = get_recordatorios()
        self._correo_base: Optional[set] = None  # ids de correo ya presentes al arrancar

    # --- Fuentes de avisos: devuelven [(clave, mensaje), ...] ------------------
    def _avisos_calendario(self) -> list[tuple[str, str]]:
        try:
            from cristopher.google_auth import get_calendar

            svc = get_calendar()
            now = datetime.now(timezone.utc)
            events = (
                svc.events()
                .list(
                    calendarId="primary", timeMin=now.isoformat(), singleEvents=True,
                    orderBy="startTime", maxResults=10,
                )
                .execute()
                .get("items", [])
            )
        except Exception:
            return []
        avisos = []
        for e in events:
            start = e.get("start", {}).get("dateTime")
            if not start:
                continue  # eventos de día completo: no son avisos "inminentes"
            try:
                inicio = datetime.fromisoformat(start)
            except ValueError:
                continue
            mins = (inicio - datetime.now(timezone.utc)).total_seconds() / 60
            if 0 <= mins <= LEAD_MINUTOS:
                titulo = e.get("summary", "(sin título)")
                hora = inicio.astimezone().strftime("%H:%M")
                avisos.append(
                    (f"cal:{e.get('id')}", f"Tienes «{titulo}» a las {hora} (en {int(mins)} min).")
                )
        return avisos

    def _avisos_correo(self) -> list[tuple[str, str]]:
        try:
            from cristopher.google_auth import get_gmail

            svc = get_gmail()
            msgs = (
                svc.users().messages()
                .list(userId="me", q=GMAIL_QUERY_PROACTIVO, maxResults=10)
                .execute()
                .get("messages", [])
            )
            ids = [m["id"] for m in msgs]
            if self._correo_base is None:
                # Primer arranque: memoriza lo ya existente, no avisa de lo viejo.
                self._correo_base = set(ids)
                return []
            avisos = []
            for mid in ids:
                if mid in self._correo_base:
                    continue
                full = (
                    svc.users().messages()
                    .get(userId="me", id=mid, format="metadata",
                         metadataHeaders=["From", "Subject"])
                    .execute()
                )
                h = {x["name"]: x["value"] for x in full.get("payload", {}).get("headers", [])}
                avisos.append(
                    (f"mail:{mid}",
                     f"Correo de {h.get('From','?')}: {h.get('Subject','(sin asunto)')}")
                )
            return avisos
        except Exception:
            return []

    def _avisos_recordatorios(self) -> list[tuple[str, str]]:
        now_iso = datetime.now().isoformat(timespec="seconds")
        avisos = []
        for rid, texto, _cuando in self._rec.pendientes(now_iso):
            avisos.append((f"rec:{rid}", f"Recordatorio: {texto}"))
        return avisos

    def _avisos_whatsapp(self) -> list[tuple[str, str]]:
        try:
            from cristopher import whatsapp_client

            data = whatsapp_client.check_new()
        except Exception:
            return []
        avisos = []
        if data.get("estado") in ("logged_out", "qr_required"):
            # Como mucho un aviso al día mientras siga desconectado (la clave se
            # "auto-limpia" sola en cuanto cambia la fecha, sin necesidad de "unseen").
            hoy = datetime.now().date().isoformat()
            avisos.append((
                f"wa:logged_out:{hoy}",
                "WhatsApp desconectado: hace falta volver a escanear el QR "
                "(ejecuta 'node whatsapp/setup_qr.js').",
            ))
        for c in data.get("chats", []):
            avisos.append((
                f"wa:{c['chat_id']}:{c['ultimo_id']}",
                f"Te han llegado {c['n_nuevos']} mensaje(s) de WhatsApp de "
                f"{c['nombre']}: «{c['ultimo_texto']}»",
            ))
        return avisos

    # --- Entrega ---------------------------------------------------------------
    def _entregar(self, nivel: int, mensaje: str) -> None:
        marca = datetime.now().strftime("%H:%M")
        if self._on_aviso is not None:
            try:
                self._on_aviso(nivel, mensaje)
            except Exception:
                pass
        if nivel >= 3:
            print(f"{RED}{BOLD}[{marca}] ⚠ URGENTE ›{RESET} {BOLD}{mensaje}{RESET}")
            hablar = self._hablar
            if hablar is None:
                from cristopher import voz
                hablar = voz.hablar
            try:
                hablar(mensaje)
                hablar(mensaje)  # nivel 3: más insistente (repite el aviso hablado)
            except Exception as exc:
                print(f"{AMBER}[VOZ] {exc}{RESET}")
            try:
                from cristopher.notificar_remoto import notificar
                notificar(mensaje)
            except Exception as exc:
                print(f"{AMBER}[REMOTO] {exc}{RESET}")
        elif nivel == 2:
            print(f"{CYAN}[{marca}] ›{RESET} {mensaje}")
        else:
            print(f"{DIM}[{marca}] · {mensaje}{RESET}")

    def revisar_una_vez(self) -> list[tuple[int, str]]:
        """Una pasada: reúne avisos nuevos, los clasifica y entrega. Devuelve
        [(nivel, mensaje)] de lo entregado (para inspección/tests)."""
        entregados = []
        avisos = (
            self._avisos_calendario()
            + self._avisos_correo()
            + self._avisos_recordatorios()
            + self._avisos_whatsapp()
        )
        for clave, mensaje in avisos:
            if self._rec.ya_visto(clave):
                continue
            nivel = self._clasificar(mensaje)
            self._entregar(nivel, mensaje)
            self._rec.marcar_visto(clave)
            if clave.startswith("rec:"):
                self._rec.marcar_hecho(int(clave.split(":")[1]))
            entregados.append((nivel, mensaje))
        return entregados

    def run(self) -> None:
        print(f"{CYAN}{BOLD}CRISTOPHER — demonio proactivo{RESET} "
              f"{DIM}(vigilando cada {POLL_SEGUNDOS}s; Ctrl+C para salir){RESET}")
        while True:
            try:
                self.revisar_una_vez()
            except Exception as exc:  # el demonio nunca debe morir por un fallo puntual
                print(f"{AMBER}[demonio] error en la revisión: {exc}{RESET}")
            time.sleep(POLL_SEGUNDOS)


def main() -> int:
    try:
        Demonio().run()
    except (KeyboardInterrupt, EOFError):
        print("\nDemonio detenido.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
