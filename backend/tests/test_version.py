"""La versión vive en dos sitios y además se publica por HTTP."""

from __future__ import annotations

import tomllib
from pathlib import Path

from rag import __version__

PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"


def test_version_coincide_con_pyproject():
    declared = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]["version"]
    assert __version__ == declared
