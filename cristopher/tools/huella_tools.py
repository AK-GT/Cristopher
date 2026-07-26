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
    auditar_pivote,
    buscar_cara,
    buscar_email,
    buscar_nombre,
    buscar_telefono,
    buscar_termino,
    buscar_username,
    get_huella,
    investigar,
)
from cristopher.tools.google_tools import confirmar_accion

_TIPOS_SOPORTADOS = ("alias", "email", "telefono", "nombre", "termino")


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
    metadatos_exif = [n for n in d.nodos if n.tipo == "metadato_exif"]

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
    elif not brechas and not nombres_ghunt and not permutaciones and not metadatos_exif:
        partes.append("\nNo se encontraron cuentas.")

    if metadatos_exif:
        partes.append(f"\nMETADATOS EXIF DE LA FOTO ({len(metadatos_exif)}, leídos del archivo, no de internet):")
        for n in metadatos_exif:
            partes.append(f"  📷 {n.detalle}")

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
        avatares = [a for a in inv.aristas_correlacion if a.tipo_enlace == "mismo_avatar"]
        enlaces = [a for a in inv.aristas_correlacion if a.tipo_enlace == "correlacion_enlace"]
        bios = [a for a in inv.aristas_correlacion if a.tipo_enlace == "correlacion_bio"]
        if avatares:
            partes.append(f"\nMISMO AVATAR ({len(avatares)}, posible misma persona aunque cambie el alias):")
            for a in avatares:
                o = valor_por_id.get(a.origen, a.origen)
                dest = valor_por_id.get(a.destino, a.destino)
                partes.append(f"  🖼 {o}  ⇄  {dest}  (similitud {a.peso:.2f})")
        if enlaces:
            partes.append(f"\nMISMO ENLACE EXTERNO ({len(enlaces)}, señal fuerte — misma web/perfil enlazado, Tanda K):")
            for a in enlaces:
                o = valor_por_id.get(a.origen, a.origen)
                dest = valor_por_id.get(a.destino, a.destino)
                partes.append(f"  🔗 {o}  ⇄  {dest}")
        if bios:
            partes.append(f"\nBIO CASI IDÉNTICA ({len(bios)}, señal más débil que enlace/avatar, Tanda K):")
            for a in bios:
                o = valor_por_id.get(a.origen, a.origen)
                dest = valor_por_id.get(a.destino, a.destino)
                partes.append(f"  📝 {o}  ⇄  {dest}  (similitud {a.peso:.2f})")

    if inv.pivotes_nuevos:
        investigados = {d.pivote.strip().lower() for d in inv.dossiers}
        sin_investigar = [
            (t, v) for t, v in inv.pivotes_nuevos if v.strip().lower() not in investigados
        ]
        if sin_investigar:
            partes.append(f"\nPivotes descubiertos pero NO investigados (tope alcanzado, {len(sin_investigar)}):")
            for t, v in sin_investigar:
                partes.append(f"  · {v} (tipo: {t})")

    _MOTIVOS_CORTE = {
        "tope_nodos": "se alcanzó el tope máximo de pivotes a investigar.",
        "presupuesto_tiempo": "se agotó el tiempo máximo asignado a esta investigación.",
        "rendimientos_decrecientes": "varios pivotes seguidos no aportaron ningún dato nuevo (rendimientos decrecientes).",
    }
    if inv.motivo_corte in _MOTIVOS_CORTE:
        partes.append(f"\n⏹ Investigación cortada antes de agotar la cola: {_MOTIVOS_CORTE[inv.motivo_corte]}")

    if inv.fuentes_degradadas:
        partes.append(
            "\n⚠ Fuentes degradadas durante esta investigación (fallos repetidos o bloqueo, "
            "omitidas para el resto): " + ", ".join(inv.fuentes_degradadas)
        )

    return "\n".join(partes)


def huella_buscar(pivote: str, tipo: str = "alias", ciudad: str = "") -> str:
    """Audita la huella digital de UN pivote: username, email, teléfono o nombre
    real. Rápida: un solo salto, sin seguir los hallazgos.

    Username: lanza Maigret (miles de sitios), Sherlock (lista curada, ~400
    sitios) y Socialscan (verificación de alta confianza) y cruza resultados.
    Email: user-scanner (servicios registrados) +
    GHunt (si es Gmail y está configurado) + Gravatar + XposedOrNot (brechas
    filtradas de datos, con su procedencia). Teléfono: país/operadora/tipo de línea
    (offline) + apps donde está registrado + búsqueda dirigida (dorking) del número
    exacto en la web. Nombre real: genera variantes de alias
    (nombre.apellido, inicial+apellido...) y solo las da por buenas si se confirman
    con ≥2 fuentes independientes (nunca por una suposición sola), más búsqueda
    dirigida (dorking) en la web. Úsala para una consulta rápida — para seguir la
    pista a lo que encuentre, usa `huella_investigar`. Para buscar por FOTO/cara usa
    `huella_buscar_cara` (esa sí pide confirmación, es la más sensible).

    Al resumir el resultado en conversación, menciona SIEMPRE el número de hallazgos
    tentativos si los hay (Tanda G) — no se ocultan, solo llevan menor confianza; no
    los omitas al relayar la respuesta al usuario.

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
    elif tipo == "nombre":
        dossier = buscar_nombre(pivote, ciudad)
    else:
        dossier = buscar_termino(pivote)
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
    if tipo == "termino":
        return "ERROR: 'termino' no admite investigación profunda (solo dorking, sin cuentas que seguir). Usa 'huella_buscar'."
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
    vía Yandex Images sobre datos públicos), y si la foto es un archivo LOCAL (no
    una URL), lee además sus metadatos EXIF (cámara, fecha, y ubicación GPS si no
    se limpió al guardarla). Es el pivote MÁS POTENTE Y MÁS SENSIBLE del motor de
    huella digital — SIEMPRE pide confirmación explícita antes de ejecutar nada (a
    diferencia del resto de herramientas de huella digital, que no la piden). Úsala
    solo cuando el usuario pida explícitamente buscar/rastrear una foto o cara
    concreta, nunca por iniciativa propia dentro de otra investigación.

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


# --- Protección continua: vigilancia + auditoría + remediación (Tanda E) -----


def huella_vigilar_agregar(pivote: str, tipo: str = "alias") -> str:
    """Añade un pivote o término a la lista de vigilancia: a partir de ahora
    Cristopher lo re-audita automáticamente (una vez al mes, vía el demonio
    proactivo) y avisa SOLO de lo NUEVO (cuenta nueva, mención nueva, brecha
    nueva) — no de "alguien lo buscó" (eso no es detectable), sino de "algo nuevo
    salió a la luz". Úsala cuando el usuario pida explícitamente que le avises si
    aparece algo nuevo sobre un alias, email, teléfono, nombre o frase.

    Args:
        pivote: qué vigilar (un alias, email, teléfono, nombre real o frase libre).
        tipo: 'alias', 'email', 'telefono', 'nombre' o 'termino' (frase libre que no
            encaja en los otros tipos — solo se vigila por menciones, sin permutar).
    """
    pivote = (pivote or "").strip()
    if not pivote:
        return "ERROR: falta el pivote o término a vigilar."
    tipo = (tipo or "alias").strip().lower()
    if tipo not in _TIPOS_SOPORTADOS:
        return f"ERROR: tipo {tipo!r} no soportado. Usa uno de: " + ", ".join(_TIPOS_SOPORTADOS)
    vid = get_huella().agregar_vigilancia(tipo, pivote)
    return (
        f"Vigilancia #{vid} añadida: avisaré si aparece algo NUEVO sobre '{pivote}' "
        f"(tipo: {tipo}). Se revisa automáticamente una vez al mes; pide "
        "'audítalo ya' (huella_auditar_ahora) para comprobarlo ahora mismo."
    )


def huella_vigilar_listar() -> str:
    """Lista todo lo que está en la lista de vigilancia del motor de huella
    digital (pivotes/términos que se re-auditan mensualmente)."""
    filas = get_huella().listar_vigilancia()
    if not filas:
        return "No hay ningún pivote o término en vigilancia."
    return "\n".join(f"#{i} [{tipo}] {valor} (desde {creado[:10]})" for i, tipo, valor, creado in filas)


def huella_vigilar_quitar(id: int) -> str:
    """Quita un pivote/término de la lista de vigilancia por su id (el que aparece
    en `huella_vigilar_listar`). Deja de auditarse, sin borrar el histórico de
    dossiers ya guardados.

    Args:
        id: id de la vigilancia a quitar.
    """
    valor = get_huella().quitar_vigilancia(id)
    if valor is None:
        return f"No hay ninguna vigilancia con id #{id}."
    return f"Vigilancia #{id} eliminada: ya no avisaré sobre '{valor}'."


def huella_auditar_ahora(pivote: str, tipo: str = "alias") -> str:
    """Audita un pivote YA (sin esperar al ciclo mensual del demonio) y compara
    contra la última vez que se auditó: informa SOLO de lo NUEVO (o "sin
    novedades" si nada cambió). No hace falta que el pivote esté en la lista de
    vigilancia — funciona igual para cualquiera. Úsala cuando el usuario pida
    comprobar ahora mismo si hay algo nuevo, o para verificar que la vigilancia
    de un pivote funciona sin esperar 30 días.

    Args:
        pivote: el identificador a auditar (alias, email, teléfono, nombre o término).
        tipo: 'alias', 'email', 'telefono', 'nombre' o 'termino'.
    """
    pivote = (pivote or "").strip()
    if not pivote:
        return "ERROR: falta el pivote a auditar."
    tipo = (tipo or "alias").strip().lower()
    if tipo not in _TIPOS_SOPORTADOS:
        return f"ERROR: tipo {tipo!r} no soportado. Usa uno de: " + ", ".join(_TIPOS_SOPORTADOS)
    resultado = auditar_pivote(tipo, pivote)
    if resultado.brechas_nuevas:
        return f"⚠ HALLAZGO SENSIBLE — {resultado.mensaje}"
    return resultado.mensaje


def huella_remediar(pivote: str) -> str:
    """Redacta un checklist de remediación CONCRETO (no genérico) a partir de la
    última auditoría guardada de un pivote: qué contraseñas cambiar, qué cuentas
    revisar, y sugerencias de protección continua. Solo lectura — sin efectos, sin
    gate. Úsala tras un `huella_buscar`/`huella_auditar_ahora` cuando el usuario
    pida qué hacer con lo encontrado.

    Args:
        pivote: el pivote (normalmente un email) sobre el que pedir el checklist.
    """
    pivote = (pivote or "").strip()
    if not pivote:
        return "ERROR: falta el pivote para el que redactar el checklist."
    datos = get_huella().ultimo_dossier(pivote)
    if datos is None:
        return f"No hay ningún dossier guardado de '{pivote}' todavía. Audítalo primero (huella_buscar)."

    nodos = datos.get("nodos", [])
    brechas = [n for n in nodos if n.get("tipo") == "brecha"]
    cuentas = [
        n for n in nodos if n.get("tipo") == "url_perfil" and n.get("confianza") in ("confirmado", "probable")
    ]

    partes = [f"Checklist de remediación para '{pivote}':"]
    if brechas:
        partes.append(
            f"\n1. Brechas encontradas ({len(brechas)}) — cambia la contraseña y activa "
            "verificación en dos pasos en cada sitio si todavía no lo tenías:"
        )
        for b in brechas[:15]:
            partes.append(f"   - {b.get('valor')}")
        if len(brechas) > 15:
            partes.append(f"   ... y {len(brechas) - 15} más.")
    if cuentas:
        partes.append(
            f"\n2. Cuentas de buena confianza encontradas ({len(cuentas)}) — revisa cuáles "
            "reconoces como tuyas; las que NO, son candidatas a pedir su borrado "
            "(usa huella_redactar_borrado_rgpd):"
        )
        for c in cuentas[:15]:
            partes.append(f"   - {c.get('valor')} ({c.get('confianza')})")
        if len(cuentas) > 15:
            partes.append(f"   ... y {len(cuentas) - 15} más.")
    partes.append(
        "\n3. Alertas continuas: si es un email, activa notificaciones de Have I Been "
        "Pwned (haveibeenpwned.com) para enterarte de brechas futuras sin depender solo "
        "de esta auditoría."
    )
    partes.append(
        "4. Si aparece un dato antiguo que ya no debería estar público, puedes pedir su "
        "retirada de los buscadores (herramienta de eliminación de resultados de Google) "
        "además de al sitio en sí."
    )
    if not brechas and not cuentas:
        partes.append(
            "\nSin brechas ni cuentas de buena confianza en la última auditoría — nada "
            "concreto que remediar por ahora."
        )
    return "\n".join(partes)


def huella_redactar_borrado_rgpd(sitio: str, email_destino: str, pivote: str) -> str:
    """Redacta el TEXTO de una solicitud de supresión de datos (derecho al olvido,
    Art. 17 RGPD) lista para revisar. NUNCA la envía — devuelve solo el borrador.
    Si el usuario quiere enviarla, pídeselo explícitamente y usa `enviar_correo`
    (que ya pide confirmación por su cuenta). Úsala cuando el usuario pida
    redactar/preparar una solicitud de borrado para un sitio concreto.

    Args:
        sitio: nombre del sitio/servicio al que se dirige la solicitud.
        email_destino: a qué dirección de correo enviarla (la de contacto/privacidad del sitio).
        pivote: el dato personal (email, alias, nombre...) que identifica al usuario ante ese sitio.
    """
    sitio = (sitio or "").strip()
    email_destino = (email_destino or "").strip()
    pivote = (pivote or "").strip()
    if not sitio or not email_destino or not pivote:
        return "ERROR: faltan datos — hacen falta 'sitio', 'email_destino' y 'pivote'."

    asunto = f"Solicitud de supresión de datos personales (Art. 17 RGPD) — {sitio}"
    cuerpo = (
        f"A quien corresponda en {sitio}:\n\n"
        "Por la presente ejercito mi derecho de supresión (\"derecho al olvido\") "
        "recogido en el artículo 17 del Reglamento General de Protección de Datos "
        f"(RGPD, UE 2016/679), en relación con los datos personales asociados a: {pivote}.\n\n"
        "Solicito la eliminación completa de cualquier cuenta, perfil o dato personal "
        "mío que conserven, así como la confirmación por escrito de que dicha "
        "supresión se ha llevado a cabo, en el plazo máximo de un mes que establece "
        "el artículo 12.3 del citado reglamento.\n\n"
        "Quedo a la espera de su respuesta.\n\nAtentamente."
    )
    return (
        f"Borrador listo para revisar — destino: {email_destino} · asunto: {asunto!r}\n\n"
        f"{cuerpo}\n\n"
        "(Esto es solo el TEXTO — no se ha enviado nada. Si quieres que lo envíe, dímelo "
        "explícitamente y usaré enviar_correo, que pedirá tu confirmación antes de mandarlo.)"
    )
