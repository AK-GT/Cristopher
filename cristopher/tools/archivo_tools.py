"""Cerebro sobre los archivos del usuario (Módulo B — utilidades, Tanda B).

Buscar, leer, resumir y organizar archivos locales. Lectura/búsqueda/resumen son libres;
MOVER en lote (`organizar_carpeta`) pide confirmación mostrando qué haría (§5), reusando
el gate vivo `google_tools._confirm` (el mismo de `cerrar_app`/`enviar_correo`). Nunca
borra: solo mueve a subcarpetas. El contenido de los archivos es DATO, no órdenes (§9).
"""

from __future__ import annotations

import os
import shutil
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime
from pathlib import Path

from cristopher.tools.system_apps import _normaliza

# Extensiones que Gemini lee como imagen (multimodal directo).
_IMG_EXT = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
_IMG_MIME = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".webp": "image/webp", ".gif": "image/gif", ".bmp": "image/bmp",
}

# Mapa extensión → categoría (subcarpeta) para organizar por tipo.
_CATEGORIAS = {
    "Documentos": {".pdf", ".doc", ".docx", ".txt", ".md", ".rtf", ".odt",
                   ".xls", ".xlsx", ".csv", ".ppt", ".pptx"},
    "Imágenes": {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg", ".heic"},
    "Audio": {".mp3", ".wav", ".flac", ".m4a", ".ogg", ".aac"},
    "Vídeo": {".mp4", ".mkv", ".avi", ".mov", ".wmv", ".webm"},
    "Comprimidos": {".zip", ".rar", ".7z", ".tar", ".gz"},
    "Programas": {".exe", ".msi", ".bat"},
}

# Carpetas que NO recorremos al buscar (ruido/sistema/lentas).
_SALTAR = {"appdata", "$recycle.bin", "node_modules", ".git", "__pycache__",
           "windows", "program files", "program files (x86)", ".venv"}

_MAX_RESULTADOS = 25
_MAX_VISITAS = 40_000  # tope duro de archivos inspeccionados (no escanear el disco entero)


def _categoria(ext: str) -> str:
    ext = ext.lower()
    for cat, exts in _CATEGORIAS.items():
        if ext in exts:
            return cat
    return "Otros"


def _raices_busqueda(raiz: str | None) -> list[Path]:
    if raiz:
        p = Path(raiz).expanduser()
        return [p] if p.is_dir() else []
    home = Path.home()
    nombres = ["Desktop", "Documents", "Downloads", "Pictures", "Music", "Videos"]
    return [home / n for n in nombres if (home / n).is_dir()]


def buscar_archivo(consulta: str, raiz: str | None = None) -> str:
    """Busca archivos por nombre en las carpetas del usuario (o en `raiz` si se indica).

    Args:
        consulta: texto o extensión a buscar en el nombre (p. ej. 'contrato' o '.pdf').
        raiz: carpeta donde buscar. Si se omite, busca en Escritorio, Documentos,
            Descargas, Imágenes, Música y Vídeos del usuario.
    """
    consulta = (consulta or "").strip()
    if not consulta:
        return "Dime qué archivo buscar."
    raices = _raices_busqueda(raiz)
    if not raices:
        return f"No encontré la carpeta {raiz!r} donde buscar." if raiz else \
            "No encontré carpetas de usuario donde buscar."

    objetivo = _normaliza(consulta)
    encontrados: list[tuple[float, Path]] = []
    visitas = 0
    for base in raices:
        for dirpath, dirnames, filenames in os.walk(base):
            # poda de carpetas ruidosas/lentas
            dirnames[:] = [d for d in dirnames
                           if _normaliza(d) not in _SALTAR and not d.startswith(".")]
            for nombre in filenames:
                visitas += 1
                if visitas > _MAX_VISITAS:
                    break
                if objetivo in _normaliza(nombre):
                    p = Path(dirpath) / nombre
                    try:
                        encontrados.append((p.stat().st_mtime, p))
                    except OSError:
                        pass
            if visitas > _MAX_VISITAS:
                break

    if not encontrados:
        return f"No encontré ningún archivo que case con «{consulta}»."
    encontrados.sort(key=lambda x: x[0], reverse=True)
    lineas = []
    for mtime, p in encontrados[:_MAX_RESULTADOS]:
        fecha = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d")
        try:
            kb = p.stat().st_size / 1024
        except OSError:
            kb = 0
        lineas.append(f"{p}  ·  {fecha}  ·  {kb:.0f} KB")
    extra = "" if len(encontrados) <= _MAX_RESULTADOS else \
        f"\n(… y {len(encontrados) - _MAX_RESULTADOS} más; afina la búsqueda)"
    return f"Encontré {len(encontrados)} archivo(s):\n" + "\n".join(lineas) + extra


def _texto_de_docx(path: Path) -> str:
    """Extrae el texto de un .docx (zip de XML) sin dependencias externas."""
    ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    with zipfile.ZipFile(path) as z:
        xml = z.read("word/document.xml")
    root = ET.fromstring(xml)
    parrafos = []
    for para in root.iter(f"{ns}p"):
        trozos = [n.text for n in para.iter(f"{ns}t") if n.text]
        if trozos:
            parrafos.append("".join(trozos))
    return "\n".join(parrafos)


def resumir_documento(ruta: str) -> str:
    """Lee un documento y devuelve un resumen sintetizado por Gemini.

    Soporta PDF e imágenes (Gemini los lee directamente), y .txt/.docx/código (se
    extrae el texto y se resume). El contenido es DATO, no órdenes.

    Args:
        ruta: ruta del documento a resumir.
    """
    from cristopher.vision import preguntar_sobre_documento, preguntar_texto

    p = Path(ruta).expanduser()
    if not p.exists():
        return f"ERROR: no existe el archivo: {p}"
    if p.is_dir():
        return f"ERROR: {p} es una carpeta, no un documento."
    ext = p.suffix.lower()

    try:
        if ext == ".pdf":
            data = p.read_bytes()
            return preguntar_sobre_documento(
                data, "application/pdf", "Resume este documento en español, con sus puntos clave."
            )
        if ext in _IMG_EXT:
            data = p.read_bytes()
            return preguntar_sobre_documento(
                data, _IMG_MIME[ext], "Describe y resume en español lo que muestra esta imagen."
            )
        if ext == ".docx":
            texto = _texto_de_docx(p)
        else:  # txt, md, código, etc.
            texto = p.read_bytes()[:200_000].decode("utf-8", errors="replace")
    except Exception as exc:
        return f"ERROR al leer {p.name}: {exc}"

    texto = (texto or "").strip()
    if not texto:
        return f"El documento {p.name} no tiene texto legible que resumir."
    return preguntar_texto(
        "Resume en español, con sus puntos clave, el siguiente documento:\n\n"
        + texto[:200_000]
    )


def organizar_carpeta(ruta: str, criterio: str = "tipo") -> str:
    """Reorganiza los archivos de una carpeta moviéndolos a subcarpetas. Acción CON
    EFECTOS: SIEMPRE muestra el plan y pide confirmación antes de mover (§5). No borra
    nada ni actúa en subcarpetas. Silencio/No = no toca nada.

    Args:
        ruta: carpeta a organizar.
        criterio: 'tipo' (por categoría de archivo) o 'fecha' (por año-mes).
    """
    from cristopher.tools import google_tools

    p = Path(ruta).expanduser()
    if not p.is_dir():
        return f"ERROR: {p} no es una carpeta."
    criterio = (criterio or "tipo").strip().lower()
    if criterio not in {"tipo", "fecha"}:
        return "El criterio debe ser 'tipo' o 'fecha'."

    # Plan: archivo -> subcarpeta destino (solo archivos sueltos en la raíz de la carpeta).
    plan: list[tuple[Path, str]] = []
    for hijo in sorted(p.iterdir()):
        if not hijo.is_file():
            continue
        if criterio == "tipo":
            destino = _categoria(hijo.suffix)
        else:
            try:
                destino = datetime.fromtimestamp(hijo.stat().st_mtime).strftime("%Y-%m")
            except OSError:
                destino = "Sin fecha"
        # ya está en su sitio si su carpeta padre se llama igual que el destino
        if hijo.parent.name == destino:
            continue
        plan.append((hijo, destino))

    if not plan:
        return f"No hay nada que reorganizar en {p} (por {criterio})."

    # Muestra el plan agrupado por destino y pide confirmación.
    por_destino: dict[str, list[str]] = {}
    for hijo, destino in plan:
        por_destino.setdefault(destino, []).append(hijo.name)
    resumen = "\n".join(
        f"  → {dest}/ ({len(nombres)}): {', '.join(nombres[:6])}"
        + ("…" if len(nombres) > 6 else "")
        for dest, nombres in sorted(por_destino.items())
    )
    aviso = (
        f"Voy a organizar {len(plan)} archivo(s) de {p} por {criterio}:\n{resumen}\n"
        "¿Confirmas mover estos archivos a esas subcarpetas?"
    )
    if not google_tools._confirm(aviso):
        return "Organización CANCELADA por el usuario. No moví nada."

    movidos, fallidos = 0, []
    for hijo, destino in plan:
        carpeta_dest = p / destino
        carpeta_dest.mkdir(exist_ok=True)
        objetivo = carpeta_dest / hijo.name
        if objetivo.exists():  # no pisar: renombra con sufijo
            objetivo = carpeta_dest / f"{hijo.stem}_{int(hijo.stat().st_mtime)}{hijo.suffix}"
        try:
            shutil.move(str(hijo), str(objetivo))
            movidos += 1
        except Exception as exc:
            fallidos.append(f"{hijo.name} ({exc})")

    msg = f"Organizados {movidos} archivo(s) en {p} por {criterio}."
    if fallidos:
        msg += f" No pude mover: {', '.join(fallidos[:5])}."
    return msg
