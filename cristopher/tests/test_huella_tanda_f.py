"""Pruebas unitarias de la Tanda F de huella.py (disyuntor, normalización de
teléfono y minado de pivotes de GHunt) — funciones puras, sin red ni subprocesos,
para poder correrlas sin instalar Maigret/GHunt/etc.

No hay suite de tests automatizada en el resto del proyecto (ver CLAUDE.md); este
archivo es una excepción puntual porque el usuario pidió explícitamente pruebas
para el disyuntor. Se ejecuta a mano:

    python -m unittest cristopher.tests.test_huella_tanda_f -v
"""

from __future__ import annotations

import unittest

from cristopher.huella import (
    DisyuntorFuente,
    Huella,
    _clave_pivote,
    _minar_pivotes_ghunt,
    _normalizar_telefono,
)


class TestDisyuntorFuente(unittest.TestCase):
    def setUp(self):
        # Huella.db propia en memoria (Tanda G: DisyuntorFuente ahora persiste
        # cooldowns) — nunca la `huella.db` real de producción. Fresca en cada test.
        self._huella = Huella(db_path=":memory:")

    def _disyuntor(self) -> DisyuntorFuente:
        return DisyuntorFuente(huella=self._huella)

    def test_activa_por_defecto(self):
        d = self._disyuntor()
        self.assertTrue(d.activa("maigret"))
        self.assertEqual(d.degradadas(), [])

    def test_exito_no_desactiva(self):
        d = self._disyuntor()
        d.registrar("maigret", ok=True)
        d.registrar("maigret", ok=True)
        self.assertTrue(d.activa("maigret"))

    def test_fallos_seguidos_desactivan(self):
        d = self._disyuntor()
        d.registrar("maigret", ok=False, detalle_error="ERROR: timeout")
        self.assertTrue(d.activa("maigret"), "un solo fallo genérico no debe desactivar")
        d.registrar("maigret", ok=False, detalle_error="ERROR: timeout")
        self.assertFalse(d.activa("maigret"), "dos fallos seguidos deben desactivar la fuente")
        self.assertIn("maigret", d.degradadas())

    def test_exito_intermedio_resetea_la_racha(self):
        d = self._disyuntor()
        d.registrar("socialscan", ok=False, detalle_error="ERROR: timeout")
        d.registrar("socialscan", ok=True)
        d.registrar("socialscan", ok=False, detalle_error="ERROR: timeout")
        self.assertTrue(d.activa("socialscan"), "un éxito en medio debe cortar la racha de fallos")

    def test_patron_de_bloqueo_desactiva_de_inmediato(self):
        d = self._disyuntor()
        d.registrar("ghunt", ok=False, detalle_error="HTTP 429 Too Many Requests")
        self.assertFalse(d.activa("ghunt"), "un error de rate-limit debe cortar la fuente sin esperar un segundo fallo")

    def test_fuentes_independientes(self):
        d = self._disyuntor()
        d.registrar("maigret", ok=False, detalle_error="bloqueado (403 Forbidden)")
        self.assertFalse(d.activa("maigret"))
        self.assertTrue(d.activa("socialscan"), "desactivar una fuente no debe afectar a otras")

    def test_desactivacion_es_permanente_en_la_investigacion(self):
        d = self._disyuntor()
        d.registrar("maigret", ok=False, detalle_error="banned")
        d.registrar("maigret", ok=True)  # un éxito posterior no debe "reactivar" la fuente
        self.assertFalse(d.activa("maigret"))

    def test_cooldown_persiste_entre_instancias_distintas(self):
        # Tanda G: una fuente desactivada en un DisyuntorFuente debe salir ya
        # pre-desactivada en el SIGUIENTE (misma Huella = misma "investigar()" futura).
        d1 = self._disyuntor()
        d1.registrar("maigret", ok=False, detalle_error="banned")
        d1.registrar("maigret", ok=False, detalle_error="banned")
        self.assertFalse(d1.activa("maigret"))

        d2 = self._disyuntor()
        self.assertFalse(d2.activa("maigret"), "el cooldown debe sobrevivir a una instancia nueva")
        self.assertTrue(d2.activa("socialscan"), "el cooldown es por fuente, no global")

    def test_huella_sin_cooldowns_previos_no_desactiva_nada(self):
        otra_huella = Huella(db_path=":memory:")
        d = DisyuntorFuente(huella=otra_huella)
        self.assertTrue(d.activa("maigret"))


class TestNormalizarTelefono(unittest.TestCase):
    def test_formatos_equivalentes_normalizan_igual(self):
        variantes = ["+34600123456", "+34 600 123 456", "0034600123456", "600123456"]
        normalizados = {_normalizar_telefono(v) for v in variantes}
        self.assertEqual(len(normalizados), 1, f"deberían normalizar todos igual: {normalizados}")

    def test_valor_no_parseable_se_devuelve_tal_cual(self):
        self.assertEqual(_normalizar_telefono("no-es-un-telefono"), "no-es-un-telefono")


class TestClavePivote(unittest.TestCase):
    def test_dedup_telefono_por_formato(self):
        self.assertEqual(
            _clave_pivote("telefono", "+34 600 123 456"),
            _clave_pivote("telefono", "0034600123456"),
        )

    def test_alias_no_se_normaliza_como_telefono(self):
        self.assertEqual(_clave_pivote("alias", "Juan.Perez"), ("alias", "juan.perez"))


class TestMinarPivotesGhunt(unittest.TestCase):
    def test_extrae_email_y_telefono_anidados(self):
        datos = {
            "target": {
                "email": "semilla@gmail.com",
                "phone_number": "+34600123456",
                "servicios": {"maps": {"autor_telefono": "+34611222333"}},
            }
        }
        pivotes = _minar_pivotes_ghunt(datos, "semilla@gmail.com")
        self.assertIn(("telefono", "+34600123456"), pivotes)
        self.assertIn(("telefono", "+34611222333"), pivotes)

    def test_no_reporta_el_propio_email_semilla(self):
        datos = {"email": "semilla@gmail.com"}
        pivotes = _minar_pivotes_ghunt(datos, "semilla@gmail.com")
        self.assertNotIn(("email", "semilla@gmail.com"), pivotes)

    def test_campo_con_varios_emails_separados_por_coma(self):
        datos = {"other_emails": "otro1@empresa.com, otro2@empresa.com"}
        pivotes = _minar_pivotes_ghunt(datos, "semilla@gmail.com")
        self.assertIn(("email", "otro1@empresa.com"), pivotes)
        self.assertIn(("email", "otro2@empresa.com"), pivotes)


if __name__ == "__main__":
    unittest.main()
