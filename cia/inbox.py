"""Inbox for events of the BIS (insight.v1, daily_snapshot.v1). One row per event id: the BIS
outbox delivers at least once, so a second delivery must not create a second entry."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

import psycopg
from psycopg.types.json import Jsonb


@dataclass(frozen=True)
class InboxEvent:
    event_id: str
    event_type: str
    payload: dict[str, Any]
    received_at: datetime | None = None
    processed_at: datetime | None = None


class Inbox(Protocol):
    def store(self, event_id: str, event_type: str, payload: dict[str, Any]) -> bool:
        """Store the event; False when the id is already known (duplicate delivery)."""
        ...

    def claim_unprocessed(self, limit: int) -> list[InboxEvent]: ...

    def mark_processed(self, event_id: str) -> None: ...


class PostgresInbox:
    def __init__(self, dsn: str):
        self.dsn = dsn

    def store(self, event_id: str, event_type: str, payload: dict[str, Any]) -> bool:
        with psycopg.connect(self.dsn) as conn:
            cursor = conn.execute(
                """
                INSERT INTO inbox.events (event_id, event_type, payload)
                VALUES (%s, %s, %s)
                ON CONFLICT (event_id) DO NOTHING
                """,
                (event_id, event_type, Jsonb(payload)),
            )
            return cursor.rowcount == 1

    def claim_unprocessed(self, limit: int) -> list[InboxEvent]:
        with psycopg.connect(self.dsn) as conn:
            rows = conn.execute(
                """
                SELECT event_id::text, event_type, payload, received_at, processed_at
                FROM inbox.events
                WHERE processed_at IS NULL
                ORDER BY received_at
                LIMIT %s
                """,
                (limit,),
            ).fetchall()
        return [InboxEvent(*row) for row in rows]

    def mark_processed(self, event_id: str) -> None:
        with psycopg.connect(self.dsn) as conn:
            conn.execute(
                "UPDATE inbox.events SET processed_at = now() WHERE event_id = %s AND processed_at IS NULL",
                (event_id,),
            )
