import json
from datetime import UTC, datetime
from typing import Any

import pytest

from cia.contracts import CONTRACTS_DIR
from cia.inbox import InboxEvent

EXAMPLES = CONTRACTS_DIR / "examples"


def example(name: str) -> dict:
    return json.loads((EXAMPLES / name).read_text(encoding="utf-8"))


class MemoryInbox:
    """In-memory stand-in for PostgresInbox with the same duplicate semantics."""

    def __init__(self):
        self.events: dict[str, InboxEvent] = {}

    def store(self, event_id: str, event_type: str, payload: dict[str, Any]) -> bool:
        if event_id in self.events:
            return False
        self.events[event_id] = InboxEvent(event_id, event_type, payload)
        return True

    def claim_unprocessed(self, limit: int) -> list[InboxEvent]:
        return [e for e in self.events.values() if e.processed_at is None][:limit]

    def mark_processed(self, event_id: str) -> None:
        event = self.events[event_id]
        self.events[event_id] = InboxEvent(
            event.event_id, event.event_type, event.payload, processed_at=datetime.now(UTC)
        )


@pytest.fixture
def inbox() -> MemoryInbox:
    return MemoryInbox()


class FakeProvider:
    """Scripted LLM provider: returns (or raises) the queued answers in order and records prompts.
    Same routing check as the real providers."""

    name = "fake"
    model = "fake-model"

    def __init__(self, *answers, allowed=frozenset({"public"}), tokens=(100, 20)):
        self.answers = list(answers)
        self.allowed_data_classes = allowed
        self.tokens = tokens
        self.calls: list = []
        self.prompts: list[dict] = []

    def generate(self, system, user, schema, data_class):
        from cia.llm.provider import Usage, check_route

        check_route(self, data_class)
        self.prompts.append({"system": system, "user": user, "schema": schema, "data_class": data_class})
        self.calls.append(Usage(self.name, self.model, *self.tokens, 0.0))
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return schema.model_validate(answer)
