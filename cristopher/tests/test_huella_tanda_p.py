"""Pruebas unitarias de la Tanda P de huella_tools.py (síntesis narrativa vía
Gemini) — offline, mockeando `google.genai.Client` para no depender de una API
key real ni gastar cuota.

    python -m unittest cristopher.tests.test_huella_tanda_p -v
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from cristopher.tools.huella_tools import _con_narrativa, _sintesis_narrativa


def _cliente_falso(textos_por_modelo: dict, lanza_por_modelo: dict | None = None):
    """Construye un genai.Client falso cuyo `.models.generate_content(model=...)`
    devuelve el texto configurado para ese modelo, o lanza la excepción dada."""
    lanza_por_modelo = lanza_por_modelo or {}

    def _generate_content(model, contents):
        if model in lanza_por_modelo:
            raise lanza_por_modelo[model]
        r = MagicMock()
        r.text = textos_por_modelo.get(model, "")
        return r

    cliente = MagicMock()
    cliente.models.generate_content.side_effect = _generate_content
    return cliente


class TestSintesisNarrativa(unittest.TestCase):
    def test_flag_desactivado_devuelve_vacio_sin_tocar_genai(self):
        with patch("cristopher.tools.huella_tools.HUELLA_NARRATIVA_ACTIVA", False):
            with patch("google.genai.Client") as mock_client_cls:
                resultado = _sintesis_narrativa("texto estructurado", "pivote", "alias")
        self.assertEqual(resultado, "")
        mock_client_cls.assert_not_called()

    def test_generacion_exitosa_devuelve_el_texto(self):
        from cristopher.config import MODEL

        cliente = _cliente_falso({MODEL: "Resumen narrativo coherente."})
        with patch("cristopher.tools.huella_tools.HUELLA_NARRATIVA_ACTIVA", True), patch(
            "google.genai.Client", return_value=cliente
        ):
            resultado = _sintesis_narrativa("texto estructurado", "pivote", "alias")
        self.assertEqual(resultado, "Resumen narrativo coherente.")

    def test_429_en_el_primer_modelo_prueba_el_segundo(self):
        from google.genai import errors

        from cristopher.config import FALLBACK_MODEL, MODEL

        error_429 = errors.APIError(429, {"error": {"message": "no quota"}})
        cliente = _cliente_falso(
            {FALLBACK_MODEL: "Resumen del modelo de respaldo."},
            lanza_por_modelo={MODEL: error_429},
        )
        with patch("cristopher.tools.huella_tools.HUELLA_NARRATIVA_ACTIVA", True), patch(
            "google.genai.Client", return_value=cliente
        ):
            resultado = _sintesis_narrativa("texto", "pivote", "alias")
        self.assertEqual(resultado, "Resumen del modelo de respaldo.")

    def test_error_no_429_no_reintenta_y_devuelve_vacio(self):
        from google.genai import errors

        from cristopher.config import MODEL

        error_500 = errors.APIError(500, {"error": {"message": "server error"}})
        cliente = _cliente_falso({}, lanza_por_modelo={MODEL: error_500})
        with patch("cristopher.tools.huella_tools.HUELLA_NARRATIVA_ACTIVA", True), patch(
            "google.genai.Client", return_value=cliente
        ):
            resultado = _sintesis_narrativa("texto", "pivote", "alias")
        self.assertEqual(resultado, "")

    def test_fallo_al_construir_el_cliente_no_lanza(self):
        with patch("cristopher.tools.huella_tools.HUELLA_NARRATIVA_ACTIVA", True), patch(
            "google.genai.Client", side_effect=RuntimeError("sin API key")
        ):
            resultado = _sintesis_narrativa("texto", "pivote", "alias")
        self.assertEqual(resultado, "")

    def test_excepcion_generica_durante_la_generacion_no_lanza(self):
        cliente = MagicMock()
        cliente.models.generate_content.side_effect = RuntimeError("timeout")
        with patch("cristopher.tools.huella_tools.HUELLA_NARRATIVA_ACTIVA", True), patch(
            "google.genai.Client", return_value=cliente
        ):
            resultado = _sintesis_narrativa("texto", "pivote", "alias")
        self.assertEqual(resultado, "")


class TestConNarrativa(unittest.TestCase):
    def test_antepone_la_narrativa_si_se_genero_algo(self):
        with patch("cristopher.tools.huella_tools._sintesis_narrativa", return_value="Resumen."):
            resultado = _con_narrativa("DETALLE ESTRUCTURADO", "pivote", "alias")
        self.assertTrue(resultado.startswith("Resumen."))
        self.assertIn("DETALLE ESTRUCTURADO", resultado)

    def test_sin_narrativa_devuelve_el_texto_intacto(self):
        with patch("cristopher.tools.huella_tools._sintesis_narrativa", return_value=""):
            resultado = _con_narrativa("DETALLE ESTRUCTURADO", "pivote", "alias")
        self.assertEqual(resultado, "DETALLE ESTRUCTURADO")


if __name__ == "__main__":
    unittest.main()
