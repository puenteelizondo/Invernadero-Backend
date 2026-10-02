"""Compila el código del proyecto a extensiones nativas (.so) con Cython.

Solo se usa dentro del Dockerfile.protected (etapa de compilación). No se
ejecuta en desarrollo.

NO se compilan:
  - __init__.py     (Python los necesita como archivos para reconocer paquetes)
  - manage.py, setup.py
  - migrations/     (Django las descubre por nombre; se dejan en claro)
config/asgi.py sí se compila: Daphne lo importa como módulo normal.
"""
from pathlib import Path

from Cython.Build import cythonize
from setuptools import Extension, setup

EXCLUIR = {"__init__.py", "manage.py", "setup.py"}
RAICES = ["apps", "config"]

extensiones = []
for raiz in RAICES:
    for ruta in Path(raiz).rglob("*.py"):
        if ruta.name in EXCLUIR or "migrations" in ruta.parts:
            continue
        modulo = ".".join(ruta.with_suffix("").parts)
        extensiones.append(Extension(modulo, [str(ruta)]))

setup(
    name="invernadero",
    ext_modules=cythonize(
        extensiones,
        compiler_directives={"language_level": "3", "embedsignature": False},
        build_dir="build",
        quiet=True,
    ),
    script_args=["build_ext", "--inplace"],
)
