"""Loads project configuration (config/config.yaml) and the DB URL (.env)."""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import yaml
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env")
# joblib/loky tries to count physical cores with `wmic`, which newer Windows builds don't ship
os.environ.setdefault("LOKY_MAX_CPU_COUNT", str(os.cpu_count() or 1))


@lru_cache
def load_config() -> dict:
    with open(PROJECT_ROOT / "config" / "config.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def database_url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL is not set. Copy .env.example to .env and fill it in.")
    return url


def path(relative: str) -> Path:
    """Resolve a path relative to the project root (keeps code free of local absolute paths)."""
    return PROJECT_ROOT / relative
