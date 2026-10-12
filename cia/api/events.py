"""Webhook receiver for the BIS outbox (contracts/README.md, "Delivery").

The BIS signs the raw body with HMAC-SHA256 (X-BIS-Signature), names the event id
(X-BIS-Event-Id) and the contract (X-BIS-Event-Type) and retries every non-2xx response."""

import hashlib
import hmac
import json
from collections.abc import Callable
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from fastapi.concurrency import run_in_threadpool

from cia import contracts
from cia.inbox import Inbox
from cia.settings import ConfigError

# Event type -> field that carries the idempotency key.
ACCEPTED_EVENTS = {"insight.v1": "insight_id", "daily_snapshot.v1": "snapshot_id"}


def signature(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def build_router(get_inbox: Callable[[], Inbox], get_secret: Callable[[], str]) -> APIRouter:
    router = APIRouter()

    def secret() -> str:
        try:
            return get_secret()
        except ConfigError as exc:
            raise HTTPException(503, "Event intake is not configured") from exc

    @router.post("/events", status_code=202)
    async def receive_event(
        request: Request,
        response: Response,
        secret: Annotated[str, Depends(secret)],
        event_id: Annotated[str, Header(alias="X-BIS-Event-Id")],
        event_type: Annotated[str, Header(alias="X-BIS-Event-Type")],
        sig: Annotated[str | None, Header(alias="X-BIS-Signature")] = None,
    ) -> dict:
        body = await request.body()
        # Authenticate before looking at the content.
        if sig is None or not hmac.compare_digest(sig, signature(secret, body)):
            raise HTTPException(401, "Invalid signature")
        if event_type not in ACCEPTED_EVENTS:
            raise HTTPException(422, f"Unsupported event type {event_type!r}")
        try:
            payload = json.loads(body)
        except ValueError as exc:
            raise HTTPException(422, "Body is not JSON") from exc
        errors = contracts.errors(event_type, payload)
        if errors:
            raise HTTPException(422, {"contract": event_type, "errors": errors})
        if payload[ACCEPTED_EVENTS[event_type]] != event_id:
            raise HTTPException(422, "X-BIS-Event-Id does not match the payload")

        inbox = get_inbox()
        created = await run_in_threadpool(inbox.store, event_id, event_type, payload)
        if not created:
            response.status_code = 200
        return {"event_id": event_id, "duplicate": not created}

    return router
