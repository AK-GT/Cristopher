"""Pruebas unitarias de la Tanda I de huella.py (metadatos EXIF de `buscar_cara`)
— offline, sin red: construye imágenes JPEG temporales con Pillow (con y sin EXIF)
en vez de depender de una foto real.

    python -m unittest cristopher.tests.test_huella_tanda_i -v
"""

from __future__ import annotations

import os
import tempfile
import unittest
from fractions import Fraction

from PIL import Image

from cristopher.huella import _extraer_exif, _gps_a_decimal


def _crear_jpeg(exif: "Image.Exif | None" = None) -> str:
    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
        ruta = f.name
    img = Image.new("RGB", (10, 10), color="red")
    if exif is not None:
        img.save(ruta, exif=exif)
    else:
        img.save(ruta)
    return ruta


def _exif_con_camara_y_gps() -> "Image.Exif":
    exif = Image.Exif()
    exif[271] = "TestCamCorp"  # Make
    exif[272] = "TestCam 3000"  # Model
    exif[36867] = "2024:01:15 10:30:00"  # DateTimeOriginal
    exif[0x8825] = {
        1: "N",
        2: (Fraction(40, 1), Fraction(26, 1), Fraction(46, 1)),
        3: "W",
        4: (Fraction(3, 1), Fraction(42, 1), Fraction(35, 1)),
    }
    return exif


class TestExtraerExif(unittest.TestCase):
    def test_foto_con_camara_y_gps(self):
        ruta = _crear_jpeg(_exif_con_camara_y_gps())
        try:
            datos = _extraer_exif(ruta)
            self.assertIsNotNone(datos)
            self.assertEqual(datos["Make"], "TestCamCorp")
            self.assertEqual(datos["Model"], "TestCam 3000")
            self.assertEqual(datos["DateTimeOriginal"], "2024:01:15 10:30:00")
            self.assertIn("40.446111", datos["GPS"])
            self.assertIn("-3.709722", datos["GPS"])
            self.assertIn("google.com/maps", datos["GPS"])
        finally:
            os.remove(ruta)

    def test_foto_sin_exif_devuelve_none(self):
        ruta = _crear_jpeg(exif=None)
        try:
            self.assertIsNone(_extraer_exif(ruta))
        finally:
            os.remove(ruta)

    def test_url_no_intenta_leer_nada(self):
        self.assertIsNone(_extraer_exif("https://ejemplo.com/foto.jpg"))
        self.assertIsNone(_extraer_exif("http://ejemplo.com/foto.jpg"))

    def test_ruta_inexistente_no_lanza(self):
        self.assertIsNone(_extraer_exif("C:/ruta/que/no/existe/foto.jpg"))


class TestGpsADecimal(unittest.TestCase):
    def test_hemisferio_norte_este_positivo(self):
        valor = _gps_a_decimal((Fraction(40, 1), Fraction(26, 1), Fraction(46, 1)), "N")
        self.assertAlmostEqual(valor, 40.446111, places=5)

    def test_hemisferio_sur_oeste_negativo(self):
        valor = _gps_a_decimal((Fraction(40, 1), Fraction(26, 1), Fraction(46, 1)), "S")
        self.assertLess(valor, 0)

    def test_valores_invalidos_no_lanzan(self):
        self.assertIsNone(_gps_a_decimal(None, "N"))
        self.assertIsNone(_gps_a_decimal(("no", "es", "numero"), "N"))


if __name__ == "__main__":
    unittest.main()
