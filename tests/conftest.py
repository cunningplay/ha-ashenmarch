"""Load the pure alert-area module (geo.py) without importing Home Assistant."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
PACKAGE_DIR = ROOT / "custom_components" / "ashenmarch"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


ashenmarch_geo = _load("ashenmarch_geo", PACKAGE_DIR / "geo.py")
