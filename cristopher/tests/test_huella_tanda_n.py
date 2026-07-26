"""Pruebas unitarias de la Tanda N de huella.py (paralelización del BFS por
nivel) — offline, sin red: sustituye `_BUSCADORES` por colectores falsos para
poder ejercitar lotes de más de un pivote de forma determinista.

    python -m unittest cristopher.tests.test_huella_tanda_n -v
"""

from __future__ import annotations

import threading
import time as _time
import unittest
from unittest.mock import patch

from cristopher.huella import DisyuntorFuente, Dossier, Huella, Nodo, investigar


def _dossier_falso(pivote: str, tipo: str, pivotes: list | None = None) -> Dossier:
    nodo = Nodo(id=f"{tipo}:{pivote}", tipo=tipo, valor=pivote, confianza="confirmado", pivotes=pivotes or [])
    return Dossier(pivote=pivote, tipo=tipo, nodos=[nodo])


class TestLoteMultiplePivotes(unittest.TestCase):
    def setUp(self):
        self.huella = Huella(db_path=":memory:")
        patcher = patch("cristopher.huella.get_huella", return_value=self.huella)
        patcher.start()
        self.addCleanup(patcher.stop)
        self._activos = 0
        self._maximo_concurrente = 0
        self._lock_contador = threading.Lock()

    def _colector_lento(self, tipo_resultado):
        def _fn(pivote, disyuntor=None):
            with self._lock_contador:
                self._activos += 1
                self._maximo_concurrente = max(self._maximo_concurrente, self._activos)
            _time.sleep(0.05)
            with self._lock_contador:
                self._activos -= 1
            return _dossier_falso(pivote, tipo_resultado)
        return _fn

    def test_pivotes_del_mismo_nivel_se_procesan_en_paralelo_de_verdad(self):
        # Semilla 'alias' que ya trae DOS pivotes estructurados (nivel 1): un
        # email y un teléfono. Al ser del mismo nivel, deben ir en el MISMO lote.
        def _alias_falso(pivote, disyuntor=None):
            return _dossier_falso(pivote, "alias", pivotes=[("email", "a@b.com"), ("telefono", "+34600000000")])

        with patch.dict(
            "cristopher.huella._BUSCADORES",
            {
                "alias": _alias_falso,
                "email": self._colector_lento("email"),
                "telefono": self._colector_lento("telefono"),
            },
        ):
            inv = investigar("x", "alias", profundidad=1)

        pivotes = {d.pivote for d in inv.dossiers}
        self.assertEqual(pivotes, {"x", "a@b.com", "+34600000000"})
        # Si de verdad corrieron en paralelo, hubo un instante con 2 activos a la vez;
        # si fuera secuencial (bug), el máximo se habría quedado en 1.
        self.assertGreaterEqual(self._maximo_concurrente, 2)

    def test_sin_novedad_se_evalua_por_lote_no_por_pivote(self):
        def _alias_falso(pivote, disyuntor=None):
            if pivote == "x":
                return _dossier_falso(pivote, "alias", pivotes=[("email", "a@b.com"), ("telefono", "+34600000000")])
            return _dossier_falso(pivote, "alias")  # sin más pivotes, para no bootstrapear infinito

        def _email_con_pivote_nuevo(pivote, disyuntor=None):
            return _dossier_falso(pivote, "email", pivotes=[("alias", "otro_alias")])

        def _telefono_sin_nada(pivote, disyuntor=None):
            return _dossier_falso(pivote, "telefono")

        with patch.dict(
            "cristopher.huella._BUSCADORES",
            {"alias": _alias_falso, "email": _email_con_pivote_nuevo, "telefono": _telefono_sin_nada},
        ):
            inv = investigar("x", "alias", profundidad=2)

        pivotes = {d.pivote for d in inv.dossiers}
        # El lote del nivel 1 tuvo un pivote que SÍ aportó (email) y otro que no
        # (teléfono) -- el contador de rendimientos decrecientes no debe cortar
        # el BFS solo por el teléfono; el alias nuevo del lote debe investigarse.
        self.assertIn("otro_alias", pivotes)


class TestDisyuntorConcurrencia(unittest.TestCase):
    def test_registrar_concurrente_no_pierde_incrementos(self):
        huella = Huella(db_path=":memory:")
        d = DisyuntorFuente(huella=huella)

        def _registrar_fallo():
            d.registrar("maigret", ok=False, detalle_error="ERROR: timeout")

        # UMBRAL_FALLOS_SEGUIDOS=2: 2 fallos concurrentes deben desactivar la
        # fuente. Sin el lock de la Tanda N, una carrera en el read-modify-write
        # de _fallos_seguidos podría perder un incremento y dejarla activa.
        hilos = [threading.Thread(target=_registrar_fallo) for _ in range(2)]
        for h in hilos:
            h.start()
        for h in hilos:
            h.join()

        self.assertFalse(d.activa("maigret"), "2 fallos concurrentes deben desactivar la fuente, sin perder el incremento")


if __name__ == "__main__":
    unittest.main()
