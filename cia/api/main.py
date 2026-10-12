"""CIA API. Reached only through the BIS frontend nginx under /cia/ (no own public port)."""

from functools import cache

from fastapi import FastAPI

from cia import settings
from cia.api.events import build_router
from cia.contracts import CONTRACTS_TAG
from cia.inbox import Inbox, PostgresInbox


@cache
def _postgres_inbox() -> Inbox:
    return PostgresInbox(settings.dsn())


def create_app(inbox: Inbox | None = None, secret: str | None = None) -> FastAPI:
    """`inbox` and `secret` override the environment (tests)."""
    app = FastAPI(title="Central-Intelligence-Agent")
    app.include_router(
        build_router(
            get_inbox=(lambda: inbox) if inbox is not None else _postgres_inbox,
            get_secret=(lambda: secret) if secret is not None else settings.webhook_secret,
        )
    )

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok", "contracts": CONTRACTS_TAG}

    return app


app = create_app()
