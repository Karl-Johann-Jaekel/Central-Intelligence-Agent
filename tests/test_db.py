"""Against a real Postgres (CIA_PG_* set): migrations and inbox idempotency in SQL."""

import os
import uuid

import psycopg
import pytest
from conftest import example

from cia import settings
from cia.inbox import PostgresInbox
from cia.migrate import migrate

pytestmark = [
    pytest.mark.db,
    pytest.mark.skipif(not os.getenv("CIA_PG_PASSWORD"), reason="CIA_PG_PASSWORD not set"),
]


@pytest.fixture(scope="module")
def dsn() -> str:
    dsn = settings.dsn()
    migrate(dsn)
    return dsn


def test_migrate_is_idempotent(dsn):
    assert migrate(dsn) == []


def test_duplicate_store_keeps_one_row(dsn):
    inbox = PostgresInbox(dsn)
    payload = example("insight.v1.anomaly.json")
    event_id = str(uuid.uuid4())
    payload["insight_id"] = event_id
    try:
        assert inbox.store(event_id, "insight.v1", payload) is True
        assert inbox.store(event_id, "insight.v1", payload) is False
        with psycopg.connect(dsn) as conn:
            [(count,)] = conn.execute("SELECT count(*) FROM inbox.events WHERE event_id = %s", (event_id,))
        assert count == 1
        assert event_id in {e.event_id for e in inbox.claim_unprocessed(1000)}
        inbox.mark_processed(event_id)
        assert event_id not in {e.event_id for e in inbox.claim_unprocessed(1000)}
    finally:
        with psycopg.connect(dsn) as conn:
            conn.execute("DELETE FROM inbox.events WHERE event_id = %s", (event_id,))
