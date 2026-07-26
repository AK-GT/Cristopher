"""Pruebas unitarias de la Tanda H de huella.py (dorking de teléfono + motor
`_dorking()` compartido) — sin red real: mockea `ddgs.DDGS` para poder correrlas
sin depender de la disponibilidad de DuckDuckGo.

    python -m unittest cristopher.tests.test_huella_tanda_h -v
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from cristopher.huella import _dorking, _dorking_telefono


class _FakeDDGS:
    """Doble mínimo del context manager `ddgs.DDGS()` para no golpear la red."""

    respuestas: dict[str, list[dict]] = {}
    lanza_en: set[str] = set()
    consultas_vistas: list[str] = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def text(self, consulta, max_results=None):
        _FakeDDGS.consultas_vistas.append(consulta)
        if consulta in _FakeDDGS.lanza_en:
            raise RuntimeError("fallo simulado de red/rate-limit")
        return _FakeDDGS.respuestas.get(consulta, [])


class TestDorking(unittest.TestCase):
    def setUp(self):
        _FakeDDGS.respuestas = {}
        _FakeDDGS.lanza_en = set()
        _FakeDDGS.consultas_vistas = []

    def test_consulta_exitosa_devuelve_resultados(self):
        _FakeDDGS.respuestas['"+34600123456"'] = [
            {"href": "https://ejemplo.com/perfil", "title": "Perfil"}
        ]
        with patch("ddgs.DDGS", _FakeDDGS):
            resultados = _dorking(['"+34600123456"'])
        self.assertEqual(resultados, [{"titulo": "Perfil", "url": "https://ejemplo.com/perfil"}])

    def test_fallo_en_una_consulta_no_lanza_y_sigue_con_las_demas(self):
        _FakeDDGS.lanza_en = {"consulta rota"}
        _FakeDDGS.respuestas["consulta ok"] = [{"href": "https://ok.com", "title": "OK"}]
        with patch("ddgs.DDGS", _FakeDDGS):
            resultados = _dorking(["consulta rota", "consulta ok"])
        self.assertEqual(resultados, [{"titulo": "OK", "url": "https://ok.com"}])

    def test_sin_url_en_el_resultado_se_descarta(self):
        _FakeDDGS.respuestas['"x"'] = [{"title": "Sin URL"}]
        with patch("ddgs.DDGS", _FakeDDGS):
            resultados = _dorking(['"x"'])
        self.assertEqual(resultados, [])


class TestDorkingTelefono(unittest.TestCase):
    def setUp(self):
        _FakeDDGS.respuestas = {}
        _FakeDDGS.lanza_en = set()
        _FakeDDGS.consultas_vistas = []

    def test_usa_el_numero_exacto_entre_comillas(self):
        with patch("ddgs.DDGS", _FakeDDGS):
            _dorking_telefono("+34600123456")
        self.assertEqual(_FakeDDGS.consultas_vistas, ['"+34600123456"'])

    def test_una_sola_consulta_a_diferencia_de_nombre(self):
        # Tanda H: teléfono no permuta ni añade site:linkedin.com — solo el número
        # exacto, a diferencia de `_dorking_nombre` (2 consultas).
        with patch("ddgs.DDGS", _FakeDDGS):
            _dorking_telefono("+34600123456")
        self.assertEqual(len(_FakeDDGS.consultas_vistas), 1)


if __name__ == "__main__":
    unittest.main()
