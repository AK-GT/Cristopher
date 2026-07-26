"""Pruebas unitarias de la Tanda M de huella.py (rescate de "plataforma
confirmada, sin URL") — offline, sin red: mockea `_intentar_resolver_en_plataforma`
para no lanzar Maigret de verdad.

    python -m unittest cristopher.tests.test_huella_tanda_m -v
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from cristopher.huella import Arista, Dossier, Investigacion, Nodo, _resolver_plataforma_sin_url


def _dossier_telefono_con_ignorant_sin_url(plataforma: str = "Instagram") -> Dossier:
    nodo_pivote = Nodo(id="telefono:+34600000000", tipo="telefono", valor="+34600000000", confianza="confirmado")
    nodo_sin_url = Nodo(
        id=f"url_perfil:{plataforma.lower()}", tipo="url_perfil", valor=plataforma, confianza="probable", puntuacion=0.45,
    )
    arista = Arista(origen=nodo_pivote.id, destino=nodo_sin_url.id, tipo_enlace="cuenta_en", fuente="ignorant", peso=0.45)
    return Dossier(pivote="+34600000000", tipo="telefono", nodos=[nodo_pivote, nodo_sin_url], aristas=[arista])


def _dossier_nombre(nombre: str = "Juan Perez") -> Dossier:
    nodo_pivote = Nodo(id=f"nombre:{nombre}", tipo="nombre", valor=nombre, confianza="confirmado")
    return Dossier(pivote=nombre, tipo="nombre", nodos=[nodo_pivote])


class TestResolverPlataformaSinUrl(unittest.TestCase):
    def test_sin_dossier_nombre_no_intenta_nada(self):
        inv = Investigacion(
            pivote_semilla="+34600000000", tipo_semilla="telefono",
            dossiers=[_dossier_telefono_con_ignorant_sin_url()],
        )
        with patch("cristopher.huella._intentar_resolver_en_plataforma") as mock_resolver:
            _resolver_plataforma_sin_url(inv)
        mock_resolver.assert_not_called()

    def test_sin_nodos_pendientes_no_intenta_nada(self):
        # Dossier con nombre presente, pero sin ningún nodo "sin URL" de ignorant.
        dossier_telefono = Dossier(
            pivote="+34600000000", tipo="telefono",
            nodos=[Nodo(id="telefono:+34600000000", tipo="telefono", valor="+34600000000", confianza="confirmado")],
        )
        inv = Investigacion(
            pivote_semilla="+34600000000", tipo_semilla="telefono",
            dossiers=[dossier_telefono, _dossier_nombre()],
        )
        with patch("cristopher.huella._intentar_resolver_en_plataforma") as mock_resolver:
            _resolver_plataforma_sin_url(inv)
        mock_resolver.assert_not_called()

    def test_acierto_anade_nodo_nuevo_tentativo_sin_borrar_el_original(self):
        dossier_tel = _dossier_telefono_con_ignorant_sin_url("Instagram")
        inv = Investigacion(
            pivote_semilla="+34600000000", tipo_semilla="telefono",
            dossiers=[dossier_tel, _dossier_nombre("Juan Perez")],
        )
        with patch(
            "cristopher.huella._intentar_resolver_en_plataforma",
            return_value="https://www.instagram.com/juan.perez/",
        ):
            _resolver_plataforma_sin_url(inv)

        # El nodo original de ignorant sigue intacto (nunca se sobrescribe).
        valores = [n.valor for n in dossier_tel.nodos]
        self.assertIn("Instagram", valores)
        # Y se añadió un nodo NUEVO con la URL resuelta.
        self.assertIn("https://www.instagram.com/juan.perez/", valores)

        nodo_nuevo = next(n for n in dossier_tel.nodos if n.valor == "https://www.instagram.com/juan.perez/")
        # Una sola fuente (maigret) -> nunca "confirmado", regla de ≥2 fuentes intacta.
        self.assertNotEqual(nodo_nuevo.confianza, "confirmado")

        arista_nueva = next(a for a in dossier_tel.aristas if a.tipo_enlace == "plataforma_resuelta")
        self.assertEqual(arista_nueva.destino, nodo_nuevo.id)
        self.assertEqual(arista_nueva.fuente, "maigret_rescate")

    def test_sin_acierto_no_anade_nada(self):
        dossier_tel = _dossier_telefono_con_ignorant_sin_url("Instagram")
        nodos_antes = list(dossier_tel.nodos)
        inv = Investigacion(
            pivote_semilla="+34600000000", tipo_semilla="telefono",
            dossiers=[dossier_tel, _dossier_nombre("Juan Perez")],
        )
        with patch("cristopher.huella._intentar_resolver_en_plataforma", return_value=None):
            _resolver_plataforma_sin_url(inv)
        self.assertEqual(dossier_tel.nodos, nodos_antes)

    def test_nodo_con_url_http_no_se_considera_pendiente(self):
        dossier_tel = Dossier(
            pivote="+34600000000", tipo="telefono",
            nodos=[
                Nodo(id="telefono:+34600000000", tipo="telefono", valor="+34600000000", confianza="confirmado"),
                Nodo(id="url_perfil:x", tipo="url_perfil", valor="https://instagram.com/x", confianza="tentativo"),
            ],
            aristas=[Arista(origen="telefono:+34600000000", destino="url_perfil:x", tipo_enlace="cuenta_en", fuente="ignorant")],
        )
        inv = Investigacion(pivote_semilla="+34600000000", tipo_semilla="telefono", dossiers=[dossier_tel, _dossier_nombre()])
        with patch("cristopher.huella._intentar_resolver_en_plataforma") as mock_resolver:
            _resolver_plataforma_sin_url(inv)
        mock_resolver.assert_not_called()

    def test_nodo_sin_url_pero_no_de_ignorant_se_ignora(self):
        # Un nodo sin "http" que viniera de OTRA fuente (no ignorant) no debe
        # disparar el rescate -- es específico del hueco de ignorant.
        dossier_tel = Dossier(
            pivote="+34600000000", tipo="telefono",
            nodos=[
                Nodo(id="telefono:+34600000000", tipo="telefono", valor="+34600000000", confianza="confirmado"),
                Nodo(id="url_perfil:raro", tipo="url_perfil", valor="Discord", confianza="tentativo"),
            ],
            aristas=[Arista(origen="telefono:+34600000000", destino="url_perfil:raro", tipo_enlace="cuenta_en", fuente="maigret")],
        )
        inv = Investigacion(pivote_semilla="+34600000000", tipo_semilla="telefono", dossiers=[dossier_tel, _dossier_nombre()])
        with patch("cristopher.huella._intentar_resolver_en_plataforma") as mock_resolver:
            _resolver_plataforma_sin_url(inv)
        mock_resolver.assert_not_called()


if __name__ == "__main__":
    unittest.main()
