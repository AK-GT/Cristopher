"""Herramienta de huella digital por pivotes (OSINT). Tanda A: pivote username.
Tanda B: pivotes email y teléfono.

Envuelve `cristopher.huella` con una función fina para el registro de tools.
Devuelve texto legible (observación para el bucle ReAct); los fallos de una
herramienta externa se listan, nunca se ocultan.

Sin gate de confirmación en esta tanda (decisión explícita del usuario, registrada
en el plan de la Tanda A — desviación consciente de §8/HERRAMIENTA_BUSQUEDA.md, que
lo marca como obligatorio para tandas futuras).
"""

from __future__ import annotations

from cristopher.huella import Dossier, buscar_email, buscar_telefono, buscar_username

_TIPOS_SOPORTADOS = ("alias", "email", "telefono")


def _formatear_dossier(d: Dossier) -> str:
    partes = [f"Huella digital de '{d.pivote}' (tipo: {d.tipo})."]

    pivote = next((n for n in d.nodos if n.id.startswith(f"{d.tipo}:")), None)
    if pivote and pivote.detalle:
        partes.append(pivote.detalle)

    perfiles = [n for n in d.nodos if n.tipo == "url_perfil"]
    nombres = [n for n in d.nodos if n.tipo == "nombre"]
    brechas = [n for n in d.nodos if n.confianza == "expuesto"]

    fuentes_por_nodo = {
        n.id: sorted(a.fuente for a in d.aristas if a.destino == n.id) for n in d.nodos
    }

    if perfiles:
        confirmados = [n for n in perfiles if n.confianza == "confirmado"]
        tentativos = [n for n in perfiles if n.confianza == "tentativo"]
        partes.append(f"\n{len(perfiles)} cuenta(s) encontrada(s).")
        if confirmados:
            partes.append(f"\nCONFIRMADAS (≥2 fuentes independientes, {len(confirmados)}):")
            for n in confirmados:
                fuentes = ", ".join(fuentes_por_nodo.get(n.id, []))
                partes.append(f"  ✓ {n.valor} [{fuentes}]")
        if tentativos:
            partes.append(f"\nTENTATIVAS (1 sola fuente, {len(tentativos)}):")
            for n in tentativos:
                fuentes = ", ".join(fuentes_por_nodo.get(n.id, []))
                partes.append(f"  ? {n.valor} [{fuentes}]")
    elif not brechas and not nombres:
        partes.append("\nNo se encontraron cuentas.")

    if nombres:
        partes.append("\nINFO DE GOOGLE (GHunt, fuente única):")
        for n in nombres:
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


def huella_buscar(pivote: str, tipo: str = "alias") -> str:
    """Audita la huella digital de un pivote: username, email o teléfono.

    Username: lanza Maigret (miles de sitios) y Socialscan (verificación de alta
    confianza) y cruza resultados. Email: user-scanner (servicios registrados) +
    GHunt (si es Gmail y está configurado) + Gravatar + XposedOrNot (brechas
    filtradas de datos, con su procedencia). Teléfono: país/operadora/tipo de línea
    (offline) + apps donde está registrado. Úsala cuando el usuario pida
    investigar/auditar la presencia online de un alias, correo o número.

    Args:
        pivote: el identificador a investigar (alias, email o teléfono).
        tipo: 'alias' (username), 'email' o 'telefono'.
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
    else:
        dossier = buscar_telefono(pivote)
    return _formatear_dossier(dossier)
