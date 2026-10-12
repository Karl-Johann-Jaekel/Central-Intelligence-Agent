"""Applies migrations/NNN_*.sql in order, each once and in its own transaction."""

from pathlib import Path

import psycopg

MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "migrations"


def pending(conn: psycopg.Connection, directory: Path = MIGRATIONS_DIR) -> list[Path]:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS public.schema_migrations (
            name       text PRIMARY KEY,
            applied_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    conn.commit()
    applied = {row[0] for row in conn.execute("SELECT name FROM public.schema_migrations")}
    return [path for path in sorted(directory.glob("*.sql")) if path.name not in applied]


def migrate(dsn: str, directory: Path = MIGRATIONS_DIR) -> list[str]:
    """Returns the names of the migrations applied in this call."""
    done = []
    with psycopg.connect(dsn) as conn:
        for path in pending(conn, directory):
            with conn.transaction():
                conn.execute(path.read_text(encoding="utf-8"))
                conn.execute("INSERT INTO public.schema_migrations (name) VALUES (%s)", (path.name,))
            done.append(path.name)
    return done
