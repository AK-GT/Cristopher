"""Herramienta de huella digital por pivotes (OSINT) — Tanda A: pivote username.

Envuelve `cristopher.huella` con una función fina para el registro de tools.
Devuelve texto legible (observación para el bucle ReAct); los fallos de una
herramienta externa se listan, nunca se ocultan.

Sin gate de confirmación en esta tanda (decisión explícita del usuario, registrada
en el plan de la Tanda A — desviación consciente de §8/HERRAMIENTA_BUSQUEDA.md, que
lo marca como obligatorio para tandas futuras).
"""

from __future__ import annotations

from cristopher.huella import Dossier, buscar_username

_TIPOS_SOPORTADOS = ("alias",)


def _formatear_dossier(d: Dossier) -> str:
    perfiles = [n for n in d.nodos if n.tipo == "url_perfil"]
    if not perfiles:
        cuerpo = "No se encontraron cuentas."
    else:
        confirmados = [n for n in perfiles if n.confianza == "confirmado"]
        tentativos = [n for n in perfiles if n.confianza == "tentativo"]
        fuentes_por_nodo = {
            n.id: sorted(a.fuente for a in d.aristas if a.destino == n.id) for n in perfiles
        }
        partes = [f"Huella digital de '{d.pivote}' — {len(perfiles)} cuenta(s) encontrada(s)."]
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
        cuerpo = "\n".join(partes)

    if d.errores:
        cuerpo += "\n\nAvisos (no todas las herramientas pudieron correr):\n" + "\n".join(
            f"  - {e}" for e in d.errores
        )
    return cuerpo


def huella_buscar(pivote: str, tipo: str = "alias") -> str:
    """Audita la huella digital de un pivote (por ahora solo username/alias).

    Lanza Maigret (amplitud, miles de sitios) y Socialscan (verificación de alta
    confianza) y cruza resultados: una cuenta hallada por ambas herramientas se
    marca CONFIRMADA; por una sola, TENTATIVA. Úsala cuando el usuario pida
    investigar/auditar la presencia online de un alias.

    Args:
        pivote: el identificador a investigar (por ahora, un username/alias).
        tipo: tipo de pivote — solo "alias" implementado en esta tanda.
    """
    pivote = (pivote or "").strip()
    if not pivote:
        return "ERROR: falta el pivote a investigar."
    tipo = (tipo or "alias").strip().lower()
    if tipo not in _TIPOS_SOPORTADOS:
        return (
            f"ERROR: tipo de pivote {tipo!r} aún no soportado. Por ahora solo 'alias' "
            "(username) — el resto de pivotes (email, teléfono, nombre) llegan en tandas "
            "posteriores del motor de huella digital."
        )
    dossier = buscar_username(pivote)
    return _formatear_dossier(dossier)
