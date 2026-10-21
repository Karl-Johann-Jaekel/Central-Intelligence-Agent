"""Worker: takes events from the inbox and starts runs of the central agent.

- daily_snapshot.v1 -> nightly run for that simulated day
- insight.v1 with severity critical -> immediate run (one per batch; none if a snapshot run
  in the same batch already covers it)
- everything else is acknowledged; the central agent reads insights itself via the BIS API

A failed run is traced as `failed` and its event still acknowledged, otherwise the worker would
retry it every few seconds; the nightly schedule fills a day without report. Before BIS A2 there
is no daily_snapshot.v1, so the schedule also provides the daily report (DoD C1)."""

import logging
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from datetime import time as clock_time
from typing import Any, Protocol

from cia.agents.reports import ReportStore
from cia.bi_client import BIClient
from cia.inbox import Inbox
from cia.telemetry.tracer import Trigger

log = logging.getLogger(__name__)

POLL_SECONDS = 5
BATCH_SIZE = 50


class StartRun(Protocol):
    def __call__(
        self, trigger: Trigger, trigger_ref: str | None, snapshot: dict[str, Any] | None
    ) -> object: ...


def _start(start_run: StartRun, trigger: Trigger, ref: str | None, snapshot: dict | None) -> None:
    try:
        start_run(trigger, ref, snapshot)
    except Exception:  # noqa: BLE001 - the run is traced as failed; keep processing
        log.exception("Run (%s, %s) failed", trigger, ref)


def process_once(inbox: Inbox, start_run: StartRun | None = None) -> int:
    events = inbox.claim_unprocessed(BATCH_SIZE)
    snapshots = [e for e in events if e.event_type == "daily_snapshot.v1"]
    critical = [e for e in events if e.event_type == "insight.v1" and e.payload.get("severity") == "critical"]
    if start_run is not None:
        for event in snapshots:
            _start(start_run, "daily_snapshot", event.event_id, event.payload)
        if critical and not snapshots:
            _start(start_run, "insight", critical[0].event_id, None)
    for event in events:
        log.info("Processed %s %s", event.event_type, event.event_id)
        inbox.mark_processed(event.event_id)
    return len(events)


class NightlySchedule:
    """Starts a run once a day after `at` if the current simulated day has no report yet."""

    RETRY = timedelta(minutes=15)

    def __init__(
        self,
        at: clock_time,
        bi: BIClient,
        reports: ReportStore,
        start_run: StartRun,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ):
        self.at, self.bi, self.reports, self.start_run, self._clock = at, bi, reports, start_run, clock
        self._done_for: Any = None  # wall-clock date of the last completed check
        self._next_try: datetime | None = None

    @classmethod
    def parse(
        cls, value: str, bi: BIClient, reports: ReportStore, start_run: StartRun
    ) -> "NightlySchedule | None":
        if not value:
            return None
        hours, minutes = value.split(":")
        return cls(clock_time(int(hours), int(minutes)), bi, reports, start_run)

    def tick(self) -> bool:
        """True when a run was started."""
        now = self._clock()
        if now.time() < self.at or self._done_for == now.date():
            return False
        if self._next_try is not None and now < self._next_try:
            return False
        try:
            sim_date = self.bi.health().data.get("sim_date")
            exists = sim_date is not None and self.reports.for_sim_date(
                datetime.fromisoformat(sim_date).date()
            )
        except Exception:  # noqa: BLE001 - BIS or database down: try again later
            log.exception("Nightly check failed, retrying in %s", self.RETRY)
            self._next_try = now + self.RETRY
            return False
        self._done_for, self._next_try = now.date(), None
        if sim_date is None or exists:
            return False
        _start(self.start_run, "schedule", None, None)
        return True


def run_forever(
    inbox: Inbox,
    start_run: StartRun | None = None,
    schedule: NightlySchedule | None = None,
    poll_seconds: float = POLL_SECONDS,
) -> None:
    while True:
        try:
            if schedule is not None:
                schedule.tick()
            if process_once(inbox, start_run):
                continue
        except Exception:  # noqa: BLE001 - keep the worker alive, the event stays unprocessed
            log.exception("Worker iteration failed")
        time.sleep(poll_seconds)
