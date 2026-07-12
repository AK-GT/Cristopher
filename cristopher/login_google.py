"""Login único de Google en el perfil de navegador de CRISTOPHER.

Abre la ventana visible (perfil persistente propio) en la página de acceso de Google
para que el USUARIO inicie sesión a mano (§9: CRISTOPHER nunca escribe credenciales).
La sesión queda guardada en el perfil, así el navegador se comporta como un usuario
real y Google deja de pedir CAPTCHA.

Uso:  python -m cristopher.login_google
"""

from __future__ import annotations

from cristopher.browser import get_browser


def main() -> int:
    b = get_browser()
    print("Abriendo la ventana de Google… inicia sesión con tu cuenta.")
    try:
        b.ir("https://accounts.google.com/")
    except Exception as exc:
        print(f"No pude abrir la ventana: {exc}")
        return 1
    input("Cuando hayas iniciado sesión, pulsa Enter aquí para guardar la sesión… ")
    b.cerrar()
    print("Listo. La sesión de Google queda guardada en el perfil de CRISTOPHER.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
