"""Settings from the environment. No secret has a default (see infra/.env.example)."""

import os


class ConfigError(RuntimeError):
    """Required configuration is missing."""


def dsn() -> str:
    """The `cia` database on the Postgres instance of the BIS stack, with its own role."""
    host = os.getenv("CIA_PG_HOST", "127.0.0.1")
    port = os.getenv("CIA_PG_PORT", "55432")
    database = os.getenv("CIA_PG_DB", "cia")
    user = os.getenv("CIA_PG_USER", "cia")
    password = os.getenv("CIA_PG_PASSWORD")
    if not password:
        raise ConfigError("CIA_PG_PASSWORD is not set (see infra/.env.example)")
    return f"postgresql://{user}:{password}@{host}:{port}/{database}?connect_timeout=5"


def webhook_secret() -> str:
    """Shared secret with the BIS outbox (X-BIS-Signature)."""
    secret = os.getenv("CIA_WEBHOOK_SECRET")
    if not secret:
        raise ConfigError("CIA_WEBHOOK_SECRET is not set (see infra/.env.example)")
    return secret
