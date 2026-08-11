"""Calibración de palmadas: SOLO mide y muestra el nivel de audio en vivo.

Nada de detección de onset, nada de ventana entre palmadas, nada de disparo —
el modo --calibrar de escucha.py ya intenta eso y no está dando buena lectura.
Este archivo es deliberadamente más tonto: enseña pico y dB crudos para que decidas
a ojo qué valor poner en CRISTOPHER_PALMADA_UMBRAL (.env) — umbral fijo, sin adaptación.

En vez de refrescar una sola línea en vivo (donde una palmada de milisegundos
se pierde entre refrescos), agrupa por ventanas de VENTANA_SEGUNDOS e imprime
UNA línea nueva por ventana con el pico máximo alcanzado en ese tramo — así cada
palmada (dando una por ventana) queda como su propia línea, legible después.

Uso:
  python -m cristopher.calibracion

Con el micrófono en silencio verás el nivel de ruido de fondo; luego da una
palmada por ventana (~3s) y anota el pico que marca cada línea para fijar el
umbral entre el ruido de fondo y el pico de una palmada.
Ctrl+C para salir.
"""

from __future__ import annotations

import sys
import time

import numpy as np

from cristopher.escucha import BLOQUE, SR, _importar_sounddevice

VENTANA_SEGUNDOS = 3.0


def calibrar() -> None:
    sd = _importar_sounddevice()
    print("CRISTOPHER — calibración de palmadas (Ctrl+C para salir; esto NO dispara nada)")
    print(f"Una línea nueva cada {VENTANA_SEGUNDOS:.0f}s con el pico máximo de esa ventana.")
    print("Da como mucho una palmada por ventana para poder identificarlas.\n")

    maximo_global = 0.0
    pico_ventana = 0.0
    db_ventana = -120.0
    inicio_ventana = time.monotonic()

    with sd.InputStream(samplerate=SR, channels=1, dtype="float32", blocksize=BLOQUE) as stream:
        while True:
            data, _ = stream.read(BLOQUE)
            pico = float(np.max(np.abs(data)))
            if pico > pico_ventana:
                pico_ventana = pico
                db_ventana = 20 * np.log10(max(pico, 1e-6))

            ahora = time.monotonic()
            if ahora - inicio_ventana >= VENTANA_SEGUNDOS:
                if pico_ventana > maximo_global:
                    maximo_global = pico_ventana
                marca = "  <- nuevo máximo global" if pico_ventana == maximo_global else ""
                print(f"pico={pico_ventana:6.3f} ({db_ventana:6.1f} dB)  "
                      f"máximo global={maximo_global:6.3f}{marca}")
                pico_ventana = 0.0
                db_ventana = -120.0
                inicio_ventana = ahora


def main() -> int:
    try:
        calibrar()
    except KeyboardInterrupt:
        print("\n[calibracion] Detenido.")
    except Exception as exc:
        print(f"\n[calibracion] Error: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
