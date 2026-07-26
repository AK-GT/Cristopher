"""Pruebas unitarias de la Tanda S de huella.py (Baidu Images como 2º motor de
búsqueda por cara, + dedup de hallazgos entre motores) — offline, sin red.

    python -m unittest cristopher.tests.test_huella_tanda_s -v
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from cristopher.huella import Dossier, Nodo, _anadir_hallazgo_cara, buscar_cara


class TestAnadirHallazgoCara(unittest.TestCase):
    def setUp(self):
        self.dossier = Dossier(pivote="foto.jpg", tipo="foto")
        self.nodo_pivote = Nodo(id="foto:foto.jpg", tipo="foto", valor="foto.jpg", confianza="confirmado")
        self.dossier.nodos.append(self.nodo_pivote)

    def test_primer_hallazgo_crea_nodo_y_arista(self):
        _anadir_hallazgo_cara(self.dossier, self.nodo_pivote, "https://ejemplo.com/perfil", "yandex_cara")
        nodos = [n for n in self.dossier.nodos if n.tipo == "url_perfil"]
        self.assertEqual(len(nodos), 1)
        self.assertEqual(nodos[0].valor, "https://ejemplo.com/perfil")
        aristas = [a for a in self.dossier.aristas if a.tipo_enlace == "aparece_en"]
        self.assertEqual(len(aristas), 1)
        self.assertEqual(aristas[0].fuente, "yandex_cara")

    def test_misma_url_de_dos_motores_no_duplica_el_nodo(self):
        _anadir_hallazgo_cara(self.dossier, self.nodo_pivote, "https://ejemplo.com/perfil", "yandex_cara")
        _anadir_hallazgo_cara(self.dossier, self.nodo_pivote, "https://ejemplo.com/perfil", "baidu_images")

        nodos = [n for n in self.dossier.nodos if n.tipo == "url_perfil"]
        self.assertEqual(len(nodos), 1, "misma URL de 2 motores no debe crear 2 nodos")

        aristas = [a for a in self.dossier.aristas if a.tipo_enlace == "aparece_en"]
        self.assertEqual(len(aristas), 2, "pero sí deben quedar las 2 aristas, una por fuente")
        fuentes = {a.fuente for a in aristas}
        self.assertEqual(fuentes, {"yandex_cara", "baidu_images"})

    def test_urls_distintas_crean_nodos_distintos(self):
        _anadir_hallazgo_cara(self.dossier, self.nodo_pivote, "https://a.com", "yandex_cara")
        _anadir_hallazgo_cara(self.dossier, self.nodo_pivote, "https://b.com", "baidu_images")
        nodos = [n for n in self.dossier.nodos if n.tipo == "url_perfil"]
        self.assertEqual(len(nodos), 2)


class TestBuscarCaraMultiMotor(unittest.TestCase):
    def test_fallo_de_yandex_no_impide_que_baidu_corra(self):
        # Antes de la Tanda S, un fallo de Yandex cortaba la función con un
        # return temprano -- Baidu nunca llegaba a intentarse.
        with patch("cristopher.huella._extraer_exif", return_value=None), patch(
            "cristopher.huella.get_browser"
        ) as mock_get_browser, patch("cristopher.huella.HUELLA_CARA_BAIDU_ACTIVO", True), patch(
            "cristopher.huella.get_huella"
        ):
            mock_browser = mock_get_browser.return_value
            mock_browser.buscar_por_imagen.side_effect = RuntimeError("Yandex caído")
            mock_browser.buscar_por_imagen_baidu.return_value = ([{"title": "x", "url": "https://baidu-hit.com"}], None)

            dossier = buscar_cara("https://ejemplo.com/foto.jpg")

        self.assertTrue(any("Yandex" in e for e in dossier.errores))
        valores = {n.valor for n in dossier.nodos if n.tipo == "url_perfil"}
        self.assertIn("https://baidu-hit.com", valores, "Baidu debe correr igual aunque Yandex falle")

    def test_flag_desactivado_no_llama_a_baidu(self):
        with patch("cristopher.huella._extraer_exif", return_value=None), patch(
            "cristopher.huella.get_browser"
        ) as mock_get_browser, patch("cristopher.huella.HUELLA_CARA_BAIDU_ACTIVO", False), patch(
            "cristopher.huella.get_huella"
        ):
            mock_browser = mock_get_browser.return_value
            mock_browser.buscar_por_imagen.return_value = ([], None)

            buscar_cara("https://ejemplo.com/foto.jpg")

            mock_browser.buscar_por_imagen_baidu.assert_not_called()


if __name__ == "__main__":
    unittest.main()
