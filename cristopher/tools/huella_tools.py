"""Herramienta de huella digital por pivotes (OSINT). Tanda A: pivote username.
Tanda B: pivotes email y teléfono. Tanda C: bucle de pivote (investigación profunda)
+ puntuación de confianza fina (tentativo/probable/confirmado). Tanda D: pivote
nombre (permutación + dorking), correlación por avatar (pHash) y pivote cara.

Envuelve `cristopher.huella` con funciones finas para el registro de tools.
Devuelve texto legible (observación para el bucle ReAct); los fallos de una
herramienta externa se listan, nunca se ocultan.

Sin gate de confirmación (decisión explícita del usuario, registrada en el plan de
la Tanda A — desviación consciente de §8/HERRAMIENTA_BUSQUEDA.md, que lo marca como
obligatorio). ÚNICA excepción: `huella_buscar_cara` — el pivote más sensible del
sistema (reconocimiento facial sobre datos públicos) SÍ pasa por el gate de §8, el
mismo que usa `enviar_correo` (`google_tools.confirmar_accion`).
"""

from __future__ import annotations

from cristopher.config import HUELLA_PROFUNDIDAD_MAX
from cristopher.huella import (
    Dossier,
    Investigacion,
    buscar_cara,
    buscar_email,
    buscar_nombre,
    buscar_telefono,
    buscar_username,
    investigar,
)
from cristopher.tools.google_tools import confirmar_accion

_TIPOS_SOPORTADOS = ("alias", "email", "telefono", "nombre")


def _formatear_dossier(d: Dossier, encabezado: bool = True) -> str:
    partes = []
    if encabezado:
        partes.append(f"Huella digital de '{d.pivote}' (tipo: {d.tipo}).")

    pivote = next((n for n in d.nodos if n.id.startswith(f"{d.tipo}:")), None)
    if pivote and pivote.detalle:
        partes.append(pivote.detalle)

    perfiles = [n for n in d.nodos if n.tipo == "url_perfil"]
    # Solo el nodo de nombre real que trae GHunt (id "ghunt:...") — el propio nodo
    # pivote de un dossier tipo="nombre" también tiene tipo="nombre" y NO debe caer
    # aquí (ya se muestra como encabezado/pivote arriba).
    nombres_ghunt = [n for n in d.nodos if n.tipo == "nombre" and n.id.startswith("ghunt:")]
    brechas = [n for n in d.nodos if n.confianza == "expuesto"]
    ids_permutacion = {a.destino for a in d.aristas if a.tipo_enlace == "permutacion_validada"}
    permutaciones = [n for n in d.nodos if n.id in ids_permutacion]

    fuentes_por_nodo = {
        n.id: sorted(a.fuente for a in d.aristas if a.destino == n.id) for n in d.nodos
    }

    if perfiles:
        confirmados = [n for n in perfiles if n.confianza == "confirmado"]
        probables = [n for n in perfiles if n.confianza == "probable"]
        tentativos = [n for n in perfiles if n.confianza == "tentativo"]
        partes.append(f"\n{len(perfiles)} cuenta(s) encontrada(s).")
        if confirmados:
            partes.append(f"\nCONFIRMADAS (>0.7, {len(confirmados)}):")
            for n in confirmados:
                fuentes = ", ".join(fuentes_por_nodo.get(n.id, []))
                partes.append(f"  ✓ {n.valor} ({n.puntuacion:.2f}) [{fuentes}]")
        if probables:
            partes.append(f"\nPROBABLES (0.4-0.7, {len(probables)}):")
            for n in probables:
                fuentes = ", ".join(fuentes_por_nodo.get(n.id, []))
                partes.append(f"  ~ {n.valor} ({n.puntuacion:.2f}) [{fuentes}]")
        if tentativos:
            partes.append(f"\nTENTATIVAS (<0.4, {len(tentativos)}):")
            for n in tentativos:
                fuentes = ", ".join(fuentes_por_nodo.get(n.id, []))
                partes.append(f"  ? {n.valor} ({n.puntuacion:.2f}) [{fuentes}]")
    elif not brechas and not nombres_ghunt and not permutaciones:
        partes.append("\nNo se encontraron cuentas.")

    if permutaciones:
        partes.append(
            f"\nPERMUTACIONES DE NOMBRE VALIDADAS ({len(permutaciones)}, confirmadas por "
            "≥2 fuentes independientes, no solo generadas):"
        )
        for n in permutaciones:
            partes.append(f"  ✓ {n.valor}")

    if nombres_ghunt:
        partes.append("\nINFO DE GOOGLE (GHunt, fuente única):")
        for n in nombres_ghunt:
            partes.append(f"  {n.detalle or n.valor}")

    if brechas:
        partes.append(f"\nEXPUESTO EN BRECHAS ({len(brechas)}, hecho de base de datos, no identidad):")
        for n in brechas:
            fuentes = ", ".join(fuentes_por_nodo.get(n.id, []))
            partes.append(f"  ⚠ {n.valor} [{fuentes}]")

    cuerpo = "\n".join(partes)

    if d.errores:
        cuerpo += "\n\nAvisos (no todas las herramientas pudieron correr):\n" + "\n".join(
            f"  - {e}" for e in d.errores
        )
    return cuerpo


def _formatear_investigacion(inv: Investigacion) -> str:
    partes = [
        f"Investigación de '{inv.pivote_semilla}' (tipo: {inv.tipo_semilla}) — "
        f"{len(inv.dossiers)} pivote(s) investigado(s), profundidad alcanzada "
        f"{inv.profundidad_alcanzada}."
    ]
    for i, d in enumerate(inv.dossiers):
        marca = "SEMILLA" if i == 0 else "PIVOTE NUEVO"
        partes.append(f"\n--- {marca}: '{d.pivote}' (tipo: {d.tipo}) ---")
        partes.append(_formatear_dossier(d, encabezado=False))

    if inv.aristas_correlacion:
        valor_por_id = {n.id: n.valor for d in inv.dossiers for n in d.nodos}
        partes.append(f"\nMISMO AVATAR ({len(inv.aristas_correlacion)}, posible misma persona aunque cambie el alias):")
        for a in inv.aristas_correlacion:
            o = valor_por_id.get(a.origen, a.origen)
            dest = valor_por_id.get(a.destino, a.destino)
            partes.append(f"  🖼 {o}  ⇄  {dest}  (similitud {a.peso:.2f})")

    if inv.pivotes_nuevos:
        investigados = {d.pivote.strip().lower() for d in inv.dossiers}
        sin_investigar = [
            (t, v) for t, v in inv.pivotes_nuevos if v.strip().lower() not in investigados
        ]
        if sin_investigar:
            partes.append(f"\nPivotes descubiertos pero NO investigados (tope alcanzado, {len(sin_investigar)}):")
            for t, v in sin_investigar:
                partes.append(f"  · {v} (tipo: {t})")

    return "\n".join(partes)


def huella_buscar(pivote: str, tipo: str = "alias", ciudad: str = "") -> str:
    """Audita la huella digital de UN pivote: username, email, teléfono o nombre
    real. Rápida: un solo salto, sin seguir los hallazgos.

    Username: lanza Maigret (miles de sitios) y Socialscan (verificación de alta
    confianza) y cruza resultados. Email: user-scanner (servicios registrados) +
    GHunt (si es Gmail y está configurado) + Gravatar + XposedOrNot (brechas
    filtradas de datos, con su procedencia). Teléfono: país/operadora/tipo de línea
    (offline) + apps donde está registrado. Nombre real: genera variantes de alias
    (nombre.apellido, inicial+apellido...) y solo las da por buenas si se confirman
    con ≥2 fuentes independientes (nunca por una suposición sola), más búsqueda
    dirigida (dorking) en la web. Úsala para una consulta rápida — para seguir la
    pista a lo que encuentre, usa `huella_investigar`. Para buscar por FOTO/cara usa
    `huella_buscar_cara` (esa sí pide confirmación, es la más sensible).

    Args:
        pivote: el identificador a investigar (alias, email, teléfono o nombre real).
        tipo: 'alias' (username), 'email', 'telefono' o 'nombre'.
        ciudad: opcional, solo para tipo='nombre' (afina el dorking).
    """
    pivote = (pivote or "").strip()
    if not pivote:
        return "ERROR: falta el pivote a investigar."
    tipo = (tipo or "alias").strip().lower()
    if tipo not in _TIPOS_SOPORTADOS:
        return (
            f"ERROR: tipo de pivote {tipo!r} no soportado. Usa uno de: "
            + ", ".join(_TIPOS_SOPORTADOS)
        )
    if tipo == "alias":
        dossier = buscar_username(pivote)
    elif tipo == "email":
        dossier = buscar_email(pivote)
    elif tipo == "telefono":
        dossier = buscar_telefono(pivote)
    else:
        dossier = buscar_nombre(pivote, ciudad)
    return _formatear_dossier(dossier)


def huella_investigar(pivote: str, tipo: str = "alias", profundidad: int = 2) -> str:
    """Investigación PROFUNDA de huella digital: además de auditar el pivote, sigue
    los identificadores nuevos que encuentre (email en una bio, alias enlazado...) y
    los investiga también, encadenado, hasta la profundidad indicada — y correlaciona
    avatares (pHash) entre TODAS las cuentas descubiertas para enlazar la misma
    persona aunque cambie de alias. Operación PESADA y LENTA (varias búsquedas
    seguidas, cada una puede tardar minutos) — úsala solo cuando el usuario pida
    explícitamente una investigación completa/a fondo, no para una consulta rápida
    de un solo dato (para eso está `huella_buscar`).

    Args:
        pivote: el identificador semilla (alias, email o teléfono).
        tipo: 'alias' (username), 'email' o 'telefono'.
        profundidad: cuántos saltos de pivote seguir (1-2). Por defecto 2.
    """
    pivote = (pivote or "").strip()
    if not pivote:
        return "ERROR: falta el pivote a investigar."
    tipo = (tipo or "alias").strip().lower()
    if tipo not in _TIPOS_SOPORTADOS:
        return (
            f"ERROR: tipo de pivote {tipo!r} no soportado. Usa uno de: "
            + ", ".join(_TIPOS_SOPORTADOS)
        )
    profundidad = max(1, min(int(profundidad or 2), HUELLA_PROFUNDIDAD_MAX))
    inv = investigar(pivote, tipo, profundidad)
    return _formatear_investigacion(inv)


def huella_buscar_cara(imagen: str) -> str:
    """Busca dónde más aparece una foto en internet (reconocimiento facial/visual
    vía Yandex Images sobre datos públicos). Es el pivote MÁS POTENTE Y MÁS SENSIBLE
    del motor de huella digital — SIEMPRE pide confirmación explícita antes de
    ejecutar nada (a diferencia del resto de herramientas de huella digital, que no
    la piden). Úsala solo cuando el usuario pida explícitamente buscar/rastrear una
    foto o cara concreta, nunca por iniciativa propia dentro de otra investigación.

    Args:
        imagen: URL (http/https) o ruta local del archivo de la foto a buscar.
    """
    imagen = (imagen or "").strip()
    if not imagen:
        return "ERROR: falta la imagen a buscar (URL o ruta local)."
    if not confirmar_accion(
        f"¿Buscar dónde más aparece esta foto en internet?\n{imagen}\n\n"
        "Es la búsqueda OSINT más sensible del sistema (reconocimiento facial/visual "
        "sobre datos públicos vía Yandex Images)."
    ):
        return "Búsqueda por cara CANCELADA por el usuario. No se ejecutó ninguna búsqueda."
    dossier = buscar_cara(imagen)
    return _formatear_dossier(dossier)
