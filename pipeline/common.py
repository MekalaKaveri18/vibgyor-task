"""Shared helpers: config loading and paths."""
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "build"
OUT = ROOT / "outputs"


def load_config(path=None):
    with open(path or ROOT / "config.toml", "rb") as f:
        return tomllib.load(f)
