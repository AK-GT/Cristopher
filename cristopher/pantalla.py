"""Pantalla dedicada del HUD: qué modo la ocupa (code, redes o calendario).

Los tres modos comparten el mismo hueco del HUD (body[data-modo] en style.css) y son
mutuamente excluyentes. Antes la exclusión code↔redes estaba copiada a mano en cada
camino (botón del HUD y tools de cada modo); con un tercer modo eso eran nueve
combinaciones que mantener sincronizadas. Aquí vive una sola vez: botón y voz llaman
a lo mismo, así que hablar y pinchar hacen exactamente lo mismo.

Los flags siguen en su módulo de siempre (estado_code, estado_redes,
estado_calendario) porque otros módulos los leen directamente."""

from __future__ import annotations

from cristopher import bus, estado_calendario, estado_code, estado_redes

_MODOS = {
    "code": (estado_code, bus.set_modo_code),
    "redes": (estado_redes, bus.set_modo_redes),
    "calendario": (estado_calendario, bus.set_modo_calendario),
}


def activar(modo: str) -> None:
    for nombre, (flag, publicar) in _MODOS.items():
        if nombre != modo and flag.esta_activo():
            flag.desactivar()
            publicar(False)
    flag, publicar = _MODOS[modo]
    flag.activar()
    publicar(True)


def desactivar(modo: str) -> None:
    flag, publicar = _MODOS[modo]
    flag.desactivar()
    publicar(False)
