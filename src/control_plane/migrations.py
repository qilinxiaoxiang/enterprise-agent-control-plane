from __future__ import annotations

from pathlib import Path

import psycopg


def run_migrations(database_url: str, path: Path = Path("infra/sql/001_init.sql")) -> None:
    if not path.is_file():
        raise FileNotFoundError(f"migration file not found: {path}")
    sql = path.read_text()
    with psycopg.connect(database_url, autocommit=True) as connection:
        connection.execute(sql, prepare=False)
