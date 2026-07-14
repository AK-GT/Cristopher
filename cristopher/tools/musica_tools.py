"""Herramientas de música (Tanda A: reproducción + cola).

Wrappers finos sobre el servicio reproductor (`cristopher.musica`). Devuelven texto
(patrón de `voz_tools.py` / `memory_tools.py`): los errores se convierten en observación
para el modelo, nunca se ocultan ni crashean (§2/§8 del spec).

Selección por INTENCIÓN, sin keywords: las descripciones (en el registro TOOLS) explican
CUÁNDO usar cada una; el modelo elige. Los nombres son ASCII (Gemini rechaza ñ/acentos en
nombres de función), pero las descripciones van en español.
"""

from __future__ import annotations

from cristopher.musica import MusicaError, get_biblioteca, get_reproductor
from cristopher.musica.resolver import resolver


def reproducir(consulta: str) -> str:
    """Reproduce YA lo que pida el usuario (canción, artista, archivo local o URL),
    reemplazando lo que sonara. Resuelve en biblioteca local o en la web (yt-dlp→VLC)."""
    try:
        return get_reproductor().reproducir(consulta)
    except MusicaError as exc:
        return f"No pude reproducir «{consulta}»: {exc}"


def pausar() -> str:
    """Pausa la música que está sonando."""
    return get_reproductor().pausar()


def reanudar() -> str:
    """Reanuda la música que estaba en pausa."""
    return get_reproductor().reanudar()


def siguiente() -> str:
    """Salta a la siguiente pista de la cola."""
    return get_reproductor().siguiente()


def anterior() -> str:
    """Vuelve a la pista anterior de la cola."""
    return get_reproductor().anterior()


def volumen(nivel: int) -> str:
    """Ajusta el volumen de la música (0 a 100)."""
    return get_reproductor().set_volumen(nivel)


def que_suena() -> str:
    """Describe qué está sonando ahora mismo y las próximas pistas de la cola."""
    e = get_reproductor().estado()
    if not e["sonando"]:
        return "Ahora mismo no suena nada."
    titulo = e["titulo"] or "(desconocido)"
    artista = f" — {e['artista']}" if e["artista"] else ""
    estado = "en pausa" if e["pausado"] else "sonando"
    linea = f"{estado.capitalize()}: {titulo}{artista} [{e['fuente']}] · volumen {e['volumen']}%"
    # Próximas pistas de la cola (las que vienen después de la actual).
    proximas = e["cola"][e["indice"] + 1 :] if e["indice"] >= 0 else []
    if proximas:
        lista = "\n".join(f"  {i}. {t}" for i, t in enumerate(proximas, 1))
        linea += f"\nEn cola:\n{lista}"
    return linea


def ver_cola() -> str:
    """Muestra la cola de reproducción completa, marcando la pista actual."""
    e = get_reproductor().estado()
    if not e["cola"]:
        return "La cola está vacía."
    lineas = []
    for i, t in enumerate(e["cola"]):
        marca = "▶ " if i == e["indice"] else "  "
        lineas.append(f"{marca}{i + 1}. {t}")
    return "Cola de reproducción:\n" + "\n".join(lineas)


def anadir_a_cola(consulta: str) -> str:
    """Añade una canción al final de la cola (empieza a sonar si no había nada)."""
    try:
        return get_reproductor().encolar(consulta)
    except MusicaError as exc:
        return f"No pude añadir «{consulta}» a la cola: {exc}"


def quitar_de_cola(pos: int) -> str:
    """Quita de la cola la pista en la posición indicada (1 = la primera)."""
    return get_reproductor().quitar(pos)


def vaciar_cola() -> str:
    """Vacía la cola y detiene la reproducción."""
    return get_reproductor().vaciar()


# --- Favoritos (Tanda B) ------------------------------------------------------
def anadir_favorito() -> str:
    """Guarda en favoritos la canción que suena ahora mismo."""
    actual = get_reproductor().pista_actual()
    if actual is None:
        return "No hay nada sonando que guardar. Pon una canción primero."
    fav_id, ya_estaba = get_biblioteca().add_favorito(actual)
    titulo = actual.get("titulo") or actual.get("consulta") or "esta canción"
    if ya_estaba:
        return f"«{titulo}» ya estaba en favoritos (#{fav_id})."
    return f"Guardada en favoritos: «{titulo}» (#{fav_id})."


def quitar_favorito(id: int) -> str:
    """Quita un favorito por su número de id."""
    titulo = get_biblioteca().quitar_favorito(id)
    if titulo is None:
        return f"No hay ningún favorito con id {id}."
    return f"Quitado de favoritos: «{titulo}»."


def listar_favoritos() -> str:
    """Muestra la lista de canciones favoritas guardadas."""
    favs = get_biblioteca().listar_favoritos()
    if not favs:
        return "No tienes favoritos guardados todavía."
    lineas = []
    for f in favs:
        art = f" — {f['artista']}" if f["artista"] else ""
        src = f" [{f['fuente']}]" if f["fuente"] else ""
        lineas.append(f"  #{f['id']}. {f['titulo']}{art}{src}")
    return "Tus favoritos:\n" + "\n".join(lineas)


def reproducir_favoritos() -> str:
    """Reproduce todas tus canciones favoritas como una cola."""
    tracks = get_biblioteca().favoritos_tracks()
    if not tracks:
        return "No tienes favoritos que reproducir."
    try:
        return get_reproductor().cargar_cola(tracks)
    except MusicaError as exc:
        return f"No pude arrancar los favoritos: {exc}"


# --- Listas de reproducción (Tanda B) -----------------------------------------
def crear_lista(nombre: str) -> str:
    """Crea una lista de reproducción vacía con el nombre indicado."""
    nombre = (nombre or "").strip()
    if not nombre:
        return "Dime un nombre para la lista."
    _lid, ya_existia = get_biblioteca().crear_lista(nombre)
    if ya_existia:
        return f"La lista «{nombre}» ya existía."
    return f"Lista «{nombre}» creada."


def anadir_a_lista(nombre: str, consulta: str) -> str:
    """Añade una canción a una lista de reproducción (crea la lista si no existe)."""
    nombre = (nombre or "").strip()
    if not nombre:
        return "Dime a qué lista añadirla."
    try:
        track = resolver(consulta)  # resuelve metadatos (título/artista); error si falla
    except MusicaError as exc:
        return f"No pude añadir «{consulta}»: {exc}"
    creada = get_biblioteca().anadir_a_lista(nombre, track)
    titulo = track.get("titulo") or consulta
    prefijo = f"Lista «{nombre}» creada. " if creada else ""
    return f"{prefijo}Añadida a «{nombre}»: {titulo}."


def quitar_de_lista(nombre: str, pos: int) -> str:
    """Quita de una lista la canción en la posición indicada (1 = la primera)."""
    titulo = get_biblioteca().quitar_de_lista(nombre, pos)
    if titulo is None:
        return f"No pude quitar la posición {pos} de «{nombre}» (¿existe la lista y esa posición?)."
    return f"Quitada de «{nombre}»: «{titulo}»."


def reproducir_lista(nombre: str) -> str:
    """Reproduce una lista de reproducción por su nombre."""
    tracks = get_biblioteca().canciones_de(nombre)
    if tracks is None:
        return f"No existe la lista «{nombre}». Mira tus listas o créala primero."
    if not tracks:
        return f"La lista «{nombre}» está vacía."
    try:
        return get_reproductor().cargar_cola(tracks)
    except MusicaError as exc:
        return f"No pude arrancar la lista «{nombre}»: {exc}"


def listar_listas() -> str:
    """Muestra tus listas de reproducción y cuántas canciones tiene cada una."""
    listas = get_biblioteca().listar_listas()
    if not listas:
        return "No tienes listas de reproducción todavía."
    lineas = [f"  · {l['nombre']} ({l['n']} canción{'es' if l['n'] != 1 else ''})"
              for l in listas]
    return "Tus listas:\n" + "\n".join(lineas)
