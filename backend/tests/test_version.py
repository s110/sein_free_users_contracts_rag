"""La versión vive en dos sitios y además se publica por HTTP."""

from __future__ import annotations

import tomllib
from pathlib import Path

from rag import __version__

PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"


def test_version_coincide_con_pyproject():
    declared = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]["version"]
    assert __version__ == declared


def test_version_es_semver():
    partes = __version__.split(".")
    assert len(partes) == 3
    assert all(p.isdigit() for p in partes)


def test_la_api_publica_esa_misma_version():
    """/api/health y /api/meta la exponen: si se desincroniza, el operador no
    puede saber qué código está sirviendo."""
    from rag.api.main import app

    assert app.version == __version__
