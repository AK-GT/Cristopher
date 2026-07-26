"""Pruebas unitarias de la Tanda K de huella.py (correlación por texto/bio) —
offline, sin red: construye `Investigacion`/`Dossier`/`Nodo` a mano, sin pasar por
`_extraer_pivotes` (que sí necesita red).

    python -m unittest cristopher.tests.test_huella_tanda_k -v
"""

from __future__ import annotations

import unittest

from cristopher.huella import Dossier, Investigacion, Nodo, _correlacionar_texto


def _nodo_perfil(id_: str, campos: dict) -> Nodo:
    return Nodo(id=id_, tipo="url_perfil", valor=id_, confianza="tentativo", campos_correlacion=campos)


class TestCorrelacionarTexto(unittest.TestCase):
    def test_enlace_externo_identico_correlaciona_entre_dossiers_distintos(self):
        d1 = Dossier(
            pivote="alias_a", tipo="alias",
            nodos=[_nodo_perfil("url_perfil:a", {"enlace_externo": "https://miweb.com"})],
        )
        d2 = Dossier(
            pivote="alias_b", tipo="alias",
            nodos=[_nodo_perfil("url_perfil:b", {"enlace_externo": "https://miweb.com"})],
        )
        inv = Investigacion(pivote_semilla="alias_a", tipo_semilla="alias", dossiers=[d1, d2])

        aristas = _correlacionar_texto(inv)
        self.assertEqual(len(aristas), 1)
        self.assertEqual(aristas[0].tipo_enlace, "correlacion_enlace")
        self.assertEqual(aristas[0].peso, 1.0)

    def test_nodos_del_mismo_dossier_no_correlacionan(self):
        # Mismo pivote (misma búsqueda): compartir bio/enlace ahí es esperado
        # ("sus propios perfiles"), no una correlación nueva entre búsquedas.
        d1 = Dossier(
            pivote="alias_a", tipo="alias",
            nodos=[
                _nodo_perfil("url_perfil:a", {"enlace_externo": "https://miweb.com"}),
                _nodo_perfil("url_perfil:b", {"enlace_externo": "https://miweb.com"}),
            ],
        )
        inv = Investigacion(pivote_semilla="alias_a", tipo_semilla="alias", dossiers=[d1])
        self.assertEqual(_correlacionar_texto(inv), [])

    def test_bio_casi_identica_correlaciona_por_encima_del_umbral(self):
        d1 = Dossier(
            pivote="alias_a", tipo="alias",
            nodos=[_nodo_perfil("url_perfil:a", {"bio": "Desarrollador de software en Madrid, ama el ajedrez"})],
        )
        d2 = Dossier(
            pivote="alias_b", tipo="alias",
            nodos=[_nodo_perfil("url_perfil:b", {"bio": "Desarrollador de software en Madrid, ama el ajedrez."})],
        )
        inv = Investigacion(pivote_semilla="alias_a", tipo_semilla="alias", dossiers=[d1, d2])

        aristas = _correlacionar_texto(inv)
        self.assertEqual(len(aristas), 1)
        self.assertEqual(aristas[0].tipo_enlace, "correlacion_bio")
        self.assertGreaterEqual(aristas[0].peso, 0.85)

    def test_bio_generica_distinta_no_correlaciona(self):
        d1 = Dossier(pivote="alias_a", tipo="alias", nodos=[_nodo_perfil("url_perfil:a", {"bio": "Amante de la música y viajes"})])
        d2 = Dossier(pivote="alias_b", tipo="alias", nodos=[_nodo_perfil("url_perfil:b", {"bio": "Fotógrafo profesional en Barcelona"})])
        inv = Investigacion(pivote_semilla="alias_a", tipo_semilla="alias", dossiers=[d1, d2])
        self.assertEqual(_correlacionar_texto(inv), [])

    def test_nodos_sin_campos_correlacion_se_ignoran(self):
        d1 = Dossier(pivote="alias_a", tipo="alias", nodos=[Nodo(id="url_perfil:a", tipo="url_perfil", valor="a")])
        d2 = Dossier(pivote="alias_b", tipo="alias", nodos=[Nodo(id="url_perfil:b", tipo="url_perfil", valor="b")])
        inv = Investigacion(pivote_semilla="alias_a", tipo_semilla="alias", dossiers=[d1, d2])
        self.assertEqual(_correlacionar_texto(inv), [])

    def test_enlace_tiene_prioridad_sobre_bio_si_ambos_coinciden(self):
        campos_comunes = {"enlace_externo": "https://miweb.com", "bio": "la misma bio exacta"}
        d1 = Dossier(pivote="alias_a", tipo="alias", nodos=[_nodo_perfil("url_perfil:a", dict(campos_comunes))])
        d2 = Dossier(pivote="alias_b", tipo="alias", nodos=[_nodo_perfil("url_perfil:b", dict(campos_comunes))])
        inv = Investigacion(pivote_semilla="alias_a", tipo_semilla="alias", dossiers=[d1, d2])

        aristas = _correlacionar_texto(inv)
        # Una sola arista (la de enlace), no dos -- no se duplica con la de bio.
        self.assertEqual(len(aristas), 1)
        self.assertEqual(aristas[0].tipo_enlace, "correlacion_enlace")


if __name__ == "__main__":
    unittest.main()
