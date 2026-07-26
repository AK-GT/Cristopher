"""Pruebas unitarias de la Tanda L de huella.py (expediente reabrible) — offline,
sin red: usa una `Huella(db_path=":memory:")` propia y sustituye `_BUSCADORES` por
un colector falso para poder probar `ampliar_investigacion()` sin lanzar
Maigret/Socialscan/etc. de verdad.

    python -m unittest cristopher.tests.test_huella_tanda_l -v
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from cristopher.huella import Arista, Dossier, Huella, Investigacion, Nodo, ampliar_investigacion


def _dossier_falso(pivote: str, tipo: str) -> Dossier:
    nodo_pivote = Nodo(id=f"{tipo}:{pivote}", tipo=tipo, valor=pivote, confianza="confirmado")
    return Dossier(pivote=pivote, tipo=tipo, nodos=[nodo_pivote])


class TestRoundTripSerializacion(unittest.TestCase):
    def test_nodo_from_dict_es_simetrico_con_to_dict(self):
        n = Nodo(
            id="alias:x", tipo="alias", valor="x", confianza="probable",
            puntuacion=0.5, detalle="d", avatar_url="http://a",
        )
        self.assertEqual(Nodo.from_dict(n.to_dict()).to_dict(), n.to_dict())

    def test_arista_from_dict_es_simetrico(self):
        a = Arista(origen="x", destino="y", tipo_enlace="cuenta_en", fuente="maigret", peso=0.35)
        self.assertEqual(Arista.from_dict(a.to_dict()).to_dict(), a.to_dict())

    def test_dossier_from_dict_reconstruye_nodos_y_aristas(self):
        d = Dossier(
            pivote="x", tipo="alias",
            nodos=[Nodo(id="alias:x", tipo="alias", valor="x")],
            aristas=[Arista(origen="alias:x", destino="url_perfil:y", tipo_enlace="cuenta_en", fuente="maigret")],
        )
        self.assertEqual(Dossier.from_dict(d.to_dict()).to_dict(), d.to_dict())

    def test_investigacion_from_dict_reconstruye_todo(self):
        inv = Investigacion(
            pivote_semilla="x", tipo_semilla="alias",
            dossiers=[_dossier_falso("x", "alias")],
            pivotes_nuevos=[("email", "x@y.com")],
            motivo_corte="tope_nodos",
        )
        inv2 = Investigacion.from_dict(inv.to_dict())
        self.assertEqual(inv2.to_dict(), inv.to_dict())
        self.assertEqual(inv2.pivotes_nuevos, [("email", "x@y.com")])


class TestHuellaLecturaInvestigaciones(unittest.TestCase):
    def setUp(self):
        self.huella = Huella(db_path=":memory:")

    def test_obtener_investigacion_por_id(self):
        inv = Investigacion(pivote_semilla="x", tipo_semilla="alias", dossiers=[_dossier_falso("x", "alias")])
        id_ = self.huella.guardar_investigacion(inv)
        datos = self.huella.obtener_investigacion(id_)
        self.assertIsNotNone(datos)
        self.assertEqual(datos["pivote_semilla"], "x")

    def test_obtener_investigacion_inexistente_devuelve_none(self):
        self.assertIsNone(self.huella.obtener_investigacion(9999))

    def test_listar_investigaciones_mas_recientes_primero(self):
        self.huella.guardar_investigacion(Investigacion(pivote_semilla="uno", tipo_semilla="alias"))
        self.huella.guardar_investigacion(Investigacion(pivote_semilla="dos", tipo_semilla="alias"))
        filas = self.huella.listar_investigaciones()
        self.assertEqual(filas[0][1], "dos")
        self.assertEqual(filas[1][1], "uno")


class TestAmpliarInvestigacion(unittest.TestCase):
    def setUp(self):
        self.huella = Huella(db_path=":memory:")
        patcher = patch("cristopher.huella.get_huella", return_value=self.huella)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_id_inexistente_lanza_value_error(self):
        with self.assertRaises(ValueError):
            ampliar_investigacion(9999, "nuevo", "email")

    def test_pivote_ya_investigado_lanza_value_error(self):
        inv = Investigacion(pivote_semilla="x", tipo_semilla="alias", dossiers=[_dossier_falso("x", "alias")])
        id_ = self.huella.guardar_investigacion(inv)
        with self.assertRaises(ValueError):
            ampliar_investigacion(id_, "x", "alias")  # mismo pivote, mismo tipo -> ya visitado

    def test_amplia_preservando_lo_viejo_y_anadiendo_lo_nuevo(self):
        inv = Investigacion(
            pivote_semilla="alias_viejo", tipo_semilla="alias",
            dossiers=[_dossier_falso("alias_viejo", "alias")],
        )
        id_original = self.huella.guardar_investigacion(inv)

        def _email_falso(pivote, disyuntor=None):
            return _dossier_falso(pivote, "email")

        with patch.dict("cristopher.huella._BUSCADORES", {"email": _email_falso}):
            inv2 = ampliar_investigacion(id_original, "nuevo@correo.com", "email", profundidad=1)

        pivotes = {d.pivote for d in inv2.dossiers}
        self.assertIn("alias_viejo", pivotes)
        self.assertIn("nuevo@correo.com", pivotes)
        self.assertEqual(len(inv2.dossiers), 2)

    def test_se_guarda_como_fila_nueva_sin_tocar_la_original(self):
        inv = Investigacion(
            pivote_semilla="alias_viejo", tipo_semilla="alias",
            dossiers=[_dossier_falso("alias_viejo", "alias")],
        )
        id_original = self.huella.guardar_investigacion(inv)

        def _email_falso(pivote, disyuntor=None):
            return _dossier_falso(pivote, "email")

        with patch.dict("cristopher.huella._BUSCADORES", {"email": _email_falso}):
            ampliar_investigacion(id_original, "nuevo@correo.com", "email", profundidad=1)

        filas = self.huella.listar_investigaciones()
        self.assertEqual(len(filas), 2)  # la original + la ampliada, nunca un UPDATE
        original_intacta = self.huella.obtener_investigacion(id_original)
        self.assertEqual(len(original_intacta["dossiers"]), 1)


if __name__ == "__main__":
    unittest.main()
