"""Pruebas unitarias de la Tanda O de huella.py (pivote dominio: crt.sh +
Wayback Machine) — offline, mockeando `requests.get` para no depender de la
disponibilidad real de esos servicios.

    python -m unittest cristopher.tests.test_huella_tanda_o -v
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from cristopher.huella import _consultar_crtsh, _consultar_wayback_cdx, buscar_dominio


def _respuesta(status_code=200, json_data=None, texto=""):
    r = MagicMock()
    r.status_code = status_code
    if json_data is not None:
        r.json.return_value = json_data
    r.text = texto
    return r


class TestConsultarCrtsh(unittest.TestCase):
    def test_extrae_subdominios_de_varias_entradas_con_wildcard(self):
        datos = [
            {"name_value": "*.example.com\nexample.com"},
            {"name_value": "mail.example.com"},
            {"name_value": "MAIL.example.com"},  # mismo subdominio, distinta capitalización
        ]
        with patch("cristopher.huella.requests.get", return_value=_respuesta(json_data=datos)):
            ok, err, subdominios = _consultar_crtsh("example.com")
        self.assertTrue(ok)
        self.assertEqual(err, "")
        self.assertEqual(subdominios, sorted({"example.com", "mail.example.com"}))

    def test_descarta_entradas_con_espacios_o_arroba(self):
        # Verificado real contra crt.sh: name_value no siempre trae hostnames
        # limpios -- descripciones de CA de prueba con espacios, o un email
        # literal en vez de un dominio. Ninguno de los dos es un subdominio.
        datos = [
            {"name_value": "as207960 test intermediate - example.com"},
            {"name_value": "user@example.com"},
            {"name_value": "valido.example.com"},
        ]
        with patch("cristopher.huella.requests.get", return_value=_respuesta(json_data=datos)):
            ok, err, subdominios = _consultar_crtsh("example.com")
        self.assertTrue(ok)
        self.assertEqual(subdominios, ["valido.example.com"])

    def test_sin_certificados_devuelve_lista_vacia_sin_error(self):
        with patch("cristopher.huella.requests.get", return_value=_respuesta(json_data=[])):
            ok, err, subdominios = _consultar_crtsh("dominio-inventado-xyz.com")
        self.assertTrue(ok)
        self.assertEqual(subdominios, [])

    def test_timeout_no_lanza(self):
        import requests as requests_mod

        with patch("cristopher.huella.requests.get", side_effect=requests_mod.exceptions.Timeout("boom")):
            ok, err, subdominios = _consultar_crtsh("example.com")
        self.assertFalse(ok)
        self.assertIn("crt.sh", err)
        self.assertEqual(subdominios, [])

    def test_status_no_200_no_lanza(self):
        with patch("cristopher.huella.requests.get", return_value=_respuesta(status_code=503)):
            ok, err, subdominios = _consultar_crtsh("example.com")
        self.assertFalse(ok)
        self.assertEqual(subdominios, [])


class TestConsultarWaybackCdx(unittest.TestCase):
    def test_parsea_filas_ignorando_la_cabecera(self):
        datos = [
            ["urlkey", "timestamp", "original", "mimetype", "statuscode", "digest", "length"],
            ["com,example)/", "20020120142510", "http://example.com/", "text/html", "200", "HASH1", "100"],
            ["com,example)/", "20030101000000", "http://example.com/pagina", "text/html", "200", "HASH2", "200"],
        ]
        with patch("cristopher.huella.requests.get", return_value=_respuesta(json_data=datos)):
            ok, err, snapshots = _consultar_wayback_cdx("example.com")
        self.assertTrue(ok)
        self.assertEqual(len(snapshots), 2)
        self.assertEqual(snapshots[0], {"url": "http://example.com/", "timestamp": "20020120142510"})

    def test_sin_snapshots_devuelve_lista_vacia(self):
        with patch("cristopher.huella.requests.get", return_value=_respuesta(json_data=[])):
            ok, err, snapshots = _consultar_wayback_cdx("dominio-inventado-xyz.com")
        self.assertTrue(ok)
        self.assertEqual(snapshots, [])

    def test_fallo_de_red_no_lanza(self):
        import requests as requests_mod

        with patch("cristopher.huella.requests.get", side_effect=requests_mod.exceptions.ConnectionError("boom")):
            ok, err, snapshots = _consultar_wayback_cdx("example.com")
        self.assertFalse(ok)
        self.assertEqual(snapshots, [])


class TestBuscarDominio(unittest.TestCase):
    def test_dossier_incluye_subdominios_y_snapshots(self):
        with patch(
            "cristopher.huella._consultar_crtsh",
            return_value=(True, "", ["example.com", "mail.example.com", "www.example.com"]),
        ), patch(
            "cristopher.huella._consultar_wayback_cdx",
            return_value=(True, "", [{"url": "http://example.com/viejo", "timestamp": "20100101000000"}]),
        ):
            dossier = buscar_dominio("example.com")

        subdominios = [n for n in dossier.nodos if n.tipo == "dominio" and n.valor != "example.com"]
        self.assertEqual({n.valor for n in subdominios}, {"mail.example.com", "www.example.com"})
        for n in subdominios:
            self.assertEqual(n.confianza, "tentativo")

        snapshots = [n for n in dossier.nodos if n.tipo == "url_perfil"]
        self.assertEqual(len(snapshots), 1)
        self.assertEqual(snapshots[0].valor, "http://example.com/viejo")

        tipos_arista = {a.tipo_enlace for a in dossier.aristas}
        self.assertIn("subdominio", tipos_arista)
        self.assertIn("snapshot_historico", tipos_arista)

    def test_dominio_inventado_sin_resultados_no_da_error_de_ejecucion(self):
        with patch("cristopher.huella._consultar_crtsh", return_value=(True, "", [])), patch(
            "cristopher.huella._consultar_wayback_cdx", return_value=(True, "", [])
        ):
            dossier = buscar_dominio("dominio-inventado-xyz.com")
        self.assertEqual(dossier.errores, [])
        self.assertEqual(len(dossier.nodos), 1)  # solo el nodo pivote

    def test_fallo_de_una_fuente_se_reporta_sin_romper_la_otra(self):
        with patch("cristopher.huella._consultar_crtsh", return_value=(False, "ERROR: timeout", [])), patch(
            "cristopher.huella._consultar_wayback_cdx",
            return_value=(True, "", [{"url": "http://example.com/x", "timestamp": "20200101000000"}]),
        ):
            dossier = buscar_dominio("example.com")
        self.assertTrue(any("crt.sh" in e for e in dossier.errores))
        self.assertTrue(any(n.tipo == "url_perfil" for n in dossier.nodos))


if __name__ == "__main__":
    unittest.main()
