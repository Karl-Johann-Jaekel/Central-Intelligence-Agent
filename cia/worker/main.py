"""Worker: takes events from the inbox. In C0 it only acknowledges them; the central agent
(C1) starts runs from daily_snapshot.v1 and critical insight.v1 here."""

import logging
import time

from cia.inbox import Inbox

log = logging.getLogger(__name__)

POLL_SECONDS = 5
BATCH_SIZE = 50


def process_once(inbox: Inbox) -> int:
    events = inbox.claim_unprocessed(BATCH_SIZE)
    for event in events:
        log.info("Received %s %s (no handler before C1)", event.event_type, event.event_id)
        inbox.mark_processed(event.event_id)
    return len(events)


def run_forever(inbox: Inbox, poll_seconds: float = POLL_SECONDS) -> None:
    while True:
        try:
            if process_once(inbox):
                continue
        except Exception:  # noqa: BLE001 - keep the worker alive, the event stays unprocessed
            log.exception("Worker iteration failed")
        time.sleep(poll_seconds)
