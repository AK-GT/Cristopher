"""Pruebas unitarias de la Tanda J de huella.py (parseo del CSV de Sherlock, 3ª
fuente independiente de username) — offline, sin subproceso real: escribe un CSV
temporal con el esquema real verificado a mano (`username,name,url_main,url_user,
exists,http_status,response_time_s`).

    python -m unittest cristopher.tests.test_huella_tanda_j -v
"""

from __future__ import annotations

import csv
import os
import tempfile
import unittest
from pathlib import Path

from cristopher.huella import PESO_FUENTE, _parsear_csv_sherlock


def _escribir_csv(filas: list[dict]) -> Path:
    with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as f:
        ruta = Path(f.name)
    with ruta.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f, fieldnames=["username", "name", "url_main", "url_user", "exists", "http_status", "response_time_s"]
        )
        writer.writeheader()
        writer.writerows(filas)
    return ruta


class TestParsearCsvSherlock(unittest.TestCase):
    def test_solo_toma_filas_claimed(self):
        ruta = _escribir_csv(
            [
                {
                    "username": "alguien",
                    "name": "GitHub",
                    "url_main": "https://www.github.com/",
                    "url_user": "https://www.github.com/alguien",
                    "exists": "Claimed",
                    "http_status": "200",
                    "response_time_s": "1.2",
                },
                {
                    "username": "alguien",
                    "name": "SitioRaro",
                    "url_main": "https://sitioraro.com/",
                    "url_user": "https://sitioraro.com/alguien",
                    "exists": "Available",
                    "http_status": "404",
                    "response_time_s": "0.5",
                },
            ]
        )
        try:
            hallazgos = _parsear_csv_sherlock(ruta)
            self.assertEqual(hallazgos, [{"sitio": "GitHub", "url": "https://www.github.com/alguien"}])
        finally:
            os.remove(ruta)

    def test_csv_sin_hallazgos_devuelve_lista_vacia(self):
        ruta = _escribir_csv([])
        try:
            self.assertEqual(_parsear_csv_sherlock(ruta), [])
        finally:
            os.remove(ruta)

    def test_varias_filas_claimed(self):
        ruta = _escribir_csv(
            [
                {
                    "username": "alguien",
                    "name": "GitHub",
                    "url_main": "https://www.github.com/",
                    "url_user": "https://www.github.com/alguien",
                    "exists": "Claimed",
                    "http_status": "200",
                    "response_time_s": "1.2",
                },
                {
                    "username": "alguien",
                    "name": "Reddit",
                    "url_main": "https://www.reddit.com/",
                    "url_user": "https://www.reddit.com/user/alguien",
                    "exists": "Claimed",
                    "http_status": "200",
                    "response_time_s": "0.9",
                },
            ]
        )
        try:
            hallazgos = _parsear_csv_sherlock(ruta)
            self.assertEqual(len(hallazgos), 2)
            sitios = {h["sitio"] for h in hallazgos}
            self.assertEqual(sitios, {"GitHub", "Reddit"})
        finally:
            os.remove(ruta)


class TestPesoSherlock(unittest.TestCase):
    def test_sherlock_tiene_peso_propio_entre_maigret_y_socialscan(self):
        # Metodológicamente similar a Maigret (URL de perfil, no endpoint de
        # registro), pero con lista más curada -> peso intermedio, nunca igual a
        # Socialscan/user-scanner (Tanda J).
        self.assertIn("sherlock", PESO_FUENTE)
        self.assertGreater(PESO_FUENTE["sherlock"], PESO_FUENTE["maigret"])
        self.assertLess(PESO_FUENTE["sherlock"], PESO_FUENTE["socialscan"])


if __name__ == "__main__":
    unittest.main()
