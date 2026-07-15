"""Control del PC (Módulo A — utilidades, Tanda B). El módulo de MÁS riesgo.

Volumen del sistema, bloquear, apagar/reiniciar/suspender y gestión de ventanas
(Windows). Las acciones IRREVERSIBLES/impactantes (apagar/reiniciar/suspender) van
gateadas por el confirmador vivo `google_tools._confirm` (el mismo de `cerrar_app`/
`enviar_correo`; lo inyecta la superficie activa). Volumen, bloquear y ventanas son de
bajo riesgo/reversibles: sin confirmación (§ spec: control del PC).

`abrir_app`/`cerrar_app` viven en `system_apps.py` (ya existían) y no se replican aquí.

Sin dependencias nuevas salvo pycaw (volumen); el resto es ctypes/subprocess nativos.
"""

from __future__ import annotations

import ctypes
import subprocess

_PASO_VOLUMEN = 0.10  # cuánto sube/baja un "sube/baja el volumen" (10 %)


# --- Volumen del sistema (pycaw) ---------------------------------------------
def _endpoint_volume():
    """Devuelve el IAudioEndpointVolume del dispositivo de salida por defecto.

    pycaw moderno expone el interfaz directamente en `AudioDevice.EndpointVolume`
    (ya no hace falta el `.Activate(...)` manual de recetas antiguas)."""
    from pycaw.pycaw import AudioUtilities

    return AudioUtilities.GetSpeakers().EndpointVolume


def volumen_sistema(accion: str, nivel: int | None = None) -> str:
    """Ajusta el volumen MAESTRO del sistema (distinto del volumen de la música).

    Args:
        accion: 'subir', 'bajar', 'fijar', 'silenciar' o 'activar'.
        nivel: nivel 0-100 (solo para 'fijar').
    """
    accion = (accion or "").strip().lower()
    try:
        vol = _endpoint_volume()
    except ImportError:
        return "ERROR: falta 'pycaw' (pip install -r requirements.txt)."
    except Exception as exc:
        return f"ERROR al acceder al volumen del sistema: {exc}"

    try:
        if accion in {"silenciar", "mute"}:
            vol.SetMute(1, None)
            return "Sistema silenciado."
        if accion in {"activar", "unmute", "quitar silencio"}:
            vol.SetMute(0, None)
            return "Silencio quitado."
        if accion == "fijar":
            if nivel is None:
                return "Dime a qué nivel (0-100) fijo el volumen."
            n = max(0, min(100, int(nivel)))
            vol.SetMute(0, None)
            vol.SetMasterVolumeLevelScalar(n / 100.0, None)
            return f"Volumen del sistema fijado al {n}%."
        if accion in {"subir", "bajar"}:
            actual = vol.GetMasterVolumeLevelScalar()
            nuevo = actual + (_PASO_VOLUMEN if accion == "subir" else -_PASO_VOLUMEN)
            nuevo = max(0.0, min(1.0, nuevo))
            vol.SetMute(0, None)
            vol.SetMasterVolumeLevelScalar(nuevo, None)
            return f"Volumen del sistema al {round(nuevo * 100)}%."
    except Exception as exc:
        return f"ERROR al ajustar el volumen: {exc}"
    return "Acción de volumen no reconocida (usa subir/bajar/fijar/silenciar/activar)."


# --- Bloqueo -----------------------------------------------------------------
def bloquear_pc() -> str:
    """Bloquea la sesión de Windows (pide contraseña al volver). Reversible: sin
    confirmación."""
    try:
        ok = ctypes.windll.user32.LockWorkStation()
    except Exception as exc:
        return f"ERROR al bloquear: {exc}"
    return "PC bloqueado." if ok else "No pude bloquear el PC."


# --- Apagar / reiniciar / suspender (CONFIRMACIÓN) ---------------------------
def _confirmar_power(accion_desc: str) -> bool:
    from cristopher.tools import google_tools
    return google_tools._confirm(
        f"¿Seguro que quieres {accion_desc}? Podrías perder trabajo sin guardar."
    )


def apagar() -> str:
    """Apaga el ordenador. Acción IRREVERSIBLE: pide confirmación antes (§5)."""
    if not _confirmar_power("APAGAR el ordenador"):
        return "Apagado CANCELADO por el usuario."
    try:
        subprocess.run(["shutdown", "/s", "/t", "0"], timeout=10)
    except Exception as exc:
        return f"ERROR al apagar: {exc}"
    return "Apagando el ordenador…"


def reiniciar() -> str:
    """Reinicia el ordenador. Acción IRREVERSIBLE: pide confirmación antes (§5)."""
    if not _confirmar_power("REINICIAR el ordenador"):
        return "Reinicio CANCELADO por el usuario."
    try:
        subprocess.run(["shutdown", "/r", "/t", "0"], timeout=10)
    except Exception as exc:
        return f"ERROR al reiniciar: {exc}"
    return "Reiniciando el ordenador…"


def suspender() -> str:
    """Suspende (duerme) el ordenador. Acción impactante: pide confirmación antes (§5)."""
    if not _confirmar_power("SUSPENDER el ordenador"):
        return "Suspensión CANCELADA por el usuario."
    try:
        # SetSuspendState(Hibernate=0, ForceCritical=1, DisableWakeEvent=0): suspende.
        subprocess.run(
            ["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"], timeout=10
        )
    except Exception as exc:
        return f"ERROR al suspender: {exc}"
    return "Suspendiendo el ordenador…"


# --- Ventanas ----------------------------------------------------------------
_SHOW = {"minimizar": 6, "maximizar": 3, "restaurar": 9}  # SW_MINIMIZE/MAXIMIZE/RESTORE


def gestionar_ventana(accion: str) -> str:
    """Gestiona la ventana en primer plano: minimizar, maximizar, restaurar o cambiar.

    Args:
        accion: 'minimizar', 'maximizar', 'restaurar' o 'cambiar' (a otra ventana).
    """
    accion = (accion or "").strip().lower()
    user32 = ctypes.windll.user32

    if accion in {"cambiar", "siguiente", "alt+tab", "alt tab"}:
        # Alt+Tab por eventos de teclado: cambia a la siguiente ventana.
        VK_MENU, VK_TAB, KEYUP = 0x12, 0x09, 0x0002
        try:
            user32.keybd_event(VK_MENU, 0, 0, 0)
            user32.keybd_event(VK_TAB, 0, 0, 0)
            user32.keybd_event(VK_TAB, 0, KEYUP, 0)
            user32.keybd_event(VK_MENU, 0, KEYUP, 0)
        except Exception as exc:
            return f"ERROR al cambiar de ventana: {exc}"
        return "Cambiada la ventana activa."

    sw = _SHOW.get(accion)
    if sw is None:
        return "Acción de ventana no reconocida (usa minimizar/maximizar/restaurar/cambiar)."
    try:
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return "No hay ninguna ventana activa que gestionar."
        user32.ShowWindow(hwnd, sw)
    except Exception as exc:
        return f"ERROR al gestionar la ventana: {exc}"
    return f"Ventana: {accion}."
