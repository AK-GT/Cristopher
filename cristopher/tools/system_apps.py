"""Herramientas: abrir y cerrar aplicaciones del escritorio (Windows).

`run_shell` no sirve para lanzar apps con interfaz: captura la salida y las mata al
llegar al timeout. Aquí resolvemos un NOMBRE amistoso ("Spotify", "la calculadora", un
juego) contra el índice de `Get-StartApps` (todo lo que ve el usuario en Inicio, clásico
y UWP) y lo lanzamos DESACOPLADO con `explorer shell:AppsFolder\\<AppID>`, para que la
app sobreviva al turno del bucle (§ arquitectura: legibilidad, MVP).

`cerrar_app` es una acción CON EFECTOS (puede perderse trabajo no guardado): va gateada
por el mismo confirmador vivo que usa `enviar_correo` (§9), leído en tiempo de llamada
para tomar el de la superficie activa (HUD/voz/consola). Casa por título de ventana y
cierra la ventana (suave con `CloseMainWindow` en hosts UWP, forzado por PID en apps
clásicas).

Sin dependencias nuevas: stdlib + utilidades nativas de Windows (Get-StartApps,
Get-Process, explorer, taskkill).
"""

from __future__ import annotations

import difflib
import json
import subprocess
import unicodedata

# Umbral de similitud por debajo del cual NO lanzamos a ciegas: preferimos devolver
# candidatos para que el modelo pregunte (decisión del usuario: mejor coincidencia, pero
# sin adivinar si no hay nada claro).
_MATCH_THRESHOLD = 0.55

# Procesos "host" compartidos que reportan el título de una app UWP pero NO son la app
# (hospedan ventanas de MUCHAS apps: matarlos tumbaría ventanas ajenas). Para estos
# cerramos la VENTANA con delicadeza (CloseMainWindow: cierra solo esa ventana) en vez de
# forzar el proceso. Para una app clásica, su propio proceso tiene la ventana y sí se
# fuerza por PID.
_HOSTS_CIERRE_SUAVE = {"applicationframehost"}


def _normaliza(texto: str) -> str:
    """Minúsculas sin acentos, para casar 'Cámara' con 'camara'."""
    sin_acentos = "".join(
        c for c in unicodedata.normalize("NFD", texto) if unicodedata.category(c) != "Mn"
    )
    return sin_acentos.casefold().strip()


def _indexar_apps() -> dict[str, str]:
    """Índice {nombre_visible: AppID} de TODAS las apps del menú Inicio.

    Usa `Get-StartApps`, que enumera lo mismo que ve el usuario al buscar en Inicio:
    apps clásicas (Win32), juegos (Steam/Epic) y apps de la Store (UWP como Calculadora o
    Cámara, que NO tienen acceso directo .lnk). El AppID sirve para lanzar cualquiera de
    ellas con `shell:AppsFolder\\<AppID>`. Si un nombre aparece repetido, nos quedamos con
    el primero (basta para lanzarlo)."""
    try:
        proc = subprocess.run(
            [
                "powershell", "-NoProfile", "-Command",
                "Get-StartApps | Select-Object Name,AppID | ConvertTo-Json -Compress",
            ],
            capture_output=True,
            text=True,
            timeout=20,
        )
        datos = json.loads(proc.stdout or "[]")
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
        return {}
    if isinstance(datos, dict):  # ConvertTo-Json devuelve objeto si hay una sola app.
        datos = [datos]
    apps: dict[str, str] = {}
    for item in datos:
        nombre, appid = item.get("Name"), item.get("AppID")
        if nombre and appid:
            apps.setdefault(nombre, appid)
    return apps


def _puntua(consulta_norm: str, nombre_norm: str) -> float:
    """Puntúa 0..1 cómo de bien casa la consulta con el nombre de una app.

    Prioriza: match exacto > la consulta contenida en el nombre (o al revés) > todos los
    tokens de la consulta presentes > ratio difuso de difflib."""
    if consulta_norm == nombre_norm:
        return 1.0
    if consulta_norm in nombre_norm or nombre_norm in consulta_norm:
        return 0.9
    tokens = consulta_norm.split()
    if tokens and all(t in nombre_norm for t in tokens):
        return 0.8
    return difflib.SequenceMatcher(None, consulta_norm, nombre_norm).ratio()


def abrir_app(nombre: str) -> str:
    """Abre una aplicación o juego del escritorio por su nombre.

    Lanza la MEJOR coincidencia del menú Inicio de forma desacoplada (la app queda
    abierta y sigue viva). Si nada casa con claridad, devuelve los candidatos más
    cercanos para que preguntes cuál. Si no hay nada en el menú Inicio, intenta lanzar el
    nombre tal cual por el shell (ejecutable en PATH o URI de protocolo).

    Args:
        nombre: nombre amistoso de la app, p. ej. 'Spotify', 'calculadora', 'Steam'.
    """
    consulta = (nombre or "").strip()
    if not consulta:
        return "ERROR: no me dijiste qué app abrir."

    apps = _indexar_apps()
    consulta_norm = _normaliza(consulta)
    puntuadas = sorted(
        ((_puntua(consulta_norm, _normaliza(n)), n, appid) for n, appid in apps.items()),
        key=lambda x: x[0],
        reverse=True,
    )

    if puntuadas and puntuadas[0][0] >= _MATCH_THRESHOLD:
        score, mejor_nombre, appid = puntuadas[0]
        # Empate muy ajustado entre dos apps distintas => ambigüedad real: pregunta.
        if len(puntuadas) > 1 and (score - puntuadas[1][0]) < 0.1 and puntuadas[1][0] >= _MATCH_THRESHOLD:
            candidatos = ", ".join(f"«{n}»" for _, n, _ in puntuadas[:4])
            return (
                f"Hay varias apps que casan con «{consulta}»: {candidatos}. "
                "¿Cuál abro?"
            )
        try:
            # explorer + shell:AppsFolder lanza igual apps clásicas y UWP, desacopladas
            # (la app sobrevive al turno). explorer.exe vuelve al instante.
            subprocess.Popen(["explorer.exe", f"shell:AppsFolder\\{appid}"], close_fds=True)
        except OSError as exc:
            return f"ERROR al abrir «{mejor_nombre}»: {exc}"
        return f"He abierto «{mejor_nombre}»."

    # Nada claro en el menú Inicio: ofrece candidatos si los hay, si no prueba el shell.
    if puntuadas and puntuadas[0][0] >= 0.35:
        candidatos = ", ".join(f"«{n}»" for _, n, _ in puntuadas[:5])
        return (
            f"No encontré una app que case bien con «{consulta}». "
            f"Lo más parecido: {candidatos}. ¿Te refieres a alguna?"
        )

    try:
        # Lanzamiento desacoplado: no capturamos ni esperamos (a diferencia de run_shell).
        subprocess.Popen(
            ["cmd", "/c", "start", "", consulta],
            creationflags=getattr(subprocess, "DETACHED_PROCESS", 0),
            close_fds=True,
        )
        return f"He intentado abrir «{consulta}» directamente (no estaba en el menú Inicio)."
    except OSError as exc:
        return f"No encontré ninguna app llamada «{consulta}» y falló el lanzamiento directo: {exc}"


def _ventanas_abiertas() -> list[dict]:
    """Apps con ventana visible: {'titulo','nombre','pid'} de cada una.

    Casamos por TÍTULO DE VENTANA (localizado, 'lo que el usuario ve': 'Calculadora',
    'Spotify Premium') además del nombre de proceso, porque el nombre de imagen suele ir
    en inglés o no parecerse al nombre amistoso (p. ej. Calculadora → CalculatorApp.exe).
    Solo apps con ventana: es lo que un usuario pide cerrar, y evita tocar servicios."""
    try:
        proc = subprocess.run(
            [
                "powershell", "-NoProfile", "-Command",
                "Get-Process | Where-Object { $_.MainWindowTitle } | "
                "Select-Object Name,Id,MainWindowTitle | ConvertTo-Json -Compress",
            ],
            capture_output=True,
            text=True,
            timeout=20,
        )
        datos = json.loads(proc.stdout or "[]")
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
        return []
    if isinstance(datos, dict):
        datos = [datos]
    return [
        {"titulo": d.get("MainWindowTitle", ""), "nombre": d.get("Name", ""), "pid": d.get("Id")}
        for d in datos
        if d.get("Id")
    ]


def _cerrar_ventana(v: dict) -> bool:
    """Cierra la app de la ventana `v`. Devuelve True si tras el intento ya no vive.

    - App clásica (su propio proceso tiene la ventana): fuerza el árbol por PID.
    - Host UWP compartido (ApplicationFrameHost): cierra SOLO esa ventana con
      CloseMainWindow() para no tumbar ventanas de otras apps UWP; el proceso backing de
      la app se retira al cerrarse su ventana.
    """
    pid = v["pid"]
    if _normaliza(v["nombre"]) in _HOSTS_CIERRE_SUAVE:
        ps = (
            f"$p = Get-Process -Id {pid} -ErrorAction SilentlyContinue; "
            "if ($p) { [void]$p.CloseMainWindow() }"
        )
        try:
            subprocess.run(
                ["powershell", "-NoProfile", "-Command", ps],
                capture_output=True, text=True, timeout=15,
            )
            return True
        except (OSError, subprocess.TimeoutExpired):
            return False
    try:
        r = subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            capture_output=True, text=True, timeout=15,
        )
        # returncode 128 = el proceso ya no existía (p. ej. lo tumbó otro /T): cuenta como cerrado.
        return r.returncode in (0, 128)
    except (OSError, subprocess.TimeoutExpired):
        return False


def cerrar_app(nombre: str) -> str:
    """Cierra una aplicación en marcha por su nombre. Acción CON EFECTOS: avisa al
    usuario y pide confirmación explícita ANTES de matar el proceso (§9). Silencio, No o
    timeout = NO cierra.

    Args:
        nombre: nombre de la app a cerrar, p. ej. 'Spotify', 'calculadora'.
    """
    # Import diferido para leer el confirmador VIVO de la superficie activa en runtime
    # (HUD/voz/consola lo inyectan con set_confirmer antes de arrancar).
    from cristopher.tools import google_tools

    consulta = (nombre or "").strip()
    if not consulta:
        return "ERROR: no me dijiste qué app cerrar."

    consulta_norm = _normaliza(consulta)
    coincidencias = [
        v for v in _ventanas_abiertas()
        if consulta_norm in _normaliza(v["titulo"]) or consulta_norm in _normaliza(v["nombre"])
    ]
    if not coincidencias:
        return f"No hay ninguna app en marcha que case con «{consulta}». No cerré nada."

    etiquetas = [v["titulo"] or v["nombre"] for v in coincidencias]
    aviso = (
        f"¿Seguro que quieres CERRAR: {', '.join(etiquetas)}?\n"
        "Se forzará su cierre y podrías perder trabajo no guardado."
    )
    if not google_tools._confirm(aviso):
        return "Cierre CANCELADO por el usuario. No cerré nada."

    cerrados, fallidos = [], []
    for v in coincidencias:
        etiqueta = v["titulo"] or v["nombre"]
        if _cerrar_ventana(v):
            cerrados.append(etiqueta)
        else:
            fallidos.append(etiqueta)

    partes = []
    if cerrados:  # dict.fromkeys deduplica (host + app pueden compartir título) sin perder orden.
        partes.append(f"Cerré: {', '.join(dict.fromkeys(cerrados))}.")
    if fallidos:
        partes.append(f"No pude cerrar: {', '.join(dict.fromkeys(fallidos))}.")
    return " ".join(partes) if partes else "No se cerró nada."
