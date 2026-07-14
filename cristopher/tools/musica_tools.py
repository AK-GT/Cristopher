"""Herramientas de música (Tanda A: reproducción + cola).

Wrappers finos sobre el servicio reproductor (`cristopher.musica`). Devuelven texto
(patrón de `voz_tools.py` / `memory_tools.py`): los errores se convierten en observación
para el modelo, nunca se ocultan ni crashean (§2/§8 del spec).

Selección por INTENCIÓN, sin keywords: las descripciones (en el registro TOOLS) explican
CUÁNDO usar cada una; el modelo elige. Los nombres son ASCII (Gemini rechaza ñ/acentos en
nombres de función), pero las descripciones van en español.
"""

from __future__ import annotations

from cristopher.musica import MusicaError, get_reproductor


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
