"""Worker: which events start runs, nightly fallback schedule."""

from datetime import UTC, datetime, time

import httpx
from conftest import example

from cia.agents.reports import MemoryReportStore
from cia.bi_client import BIClient
from cia.worker.main import NightlySchedule, process_once


class Runs:
    def __init__(self, fail=False):
        self.started: list[tuple] = []
        self.fail = fail

    def __call__(self, trigger, trigger_ref, snapshot):
        self.started.append((trigger, trigger_ref, snapshot is not None))
        if self.fail:
            raise RuntimeError("run failed")


def _critical(n: int) -> dict:
    payload = example("insight.v1.anomaly.json")
    return payload | {"insight_id": f"00000000-0000-4000-8000-{n:012d}", "severity": "critical"}


def test_snapshot_starts_a_nightly_run(inbox):
    snapshot = example("daily_snapshot.v1.example.json")
    inbox.store(snapshot["snapshot_id"], "daily_snapshot.v1", snapshot)
    inbox.store(_critical(1)["insight_id"], "insight.v1", _critical(1))  # covered by the snapshot run
    runs = Runs()
    assert process_once(inbox, runs) == 2
    assert runs.started == [("daily_snapshot", snapshot["snapshot_id"], True)]
    assert inbox.claim_unprocessed(10) == []


def test_critical_insights_start_one_run_per_batch(inbox):
    warning = example("insight.v1.anomaly.json")
    for payload in (warning, _critical(1), _critical(2)):
        inbox.store(payload["insight_id"], "insight.v1", payload)
    runs = Runs()
    process_once(inbox, runs)
    assert runs.started == [("insight", _critical(1)["insight_id"], False)]


def test_failed_run_still_acknowledges(inbox):
    inbox.store(_critical(1)["insight_id"], "insight.v1", _critical(1))
    process_once(inbox, Runs(fail=True))
    assert inbox.claim_unprocessed(10) == []


class Clock:
    def __init__(self, now: datetime):
        self.now = now

    def __call__(self) -> datetime:
        return self.now


def _schedule(health, reports=None):
    bis_calls = []

    def handler(request):
        bis_calls.append(request.url.path)
        return health() if callable(health) else httpx.Response(200, json=health)

    bi = BIClient(
        "http://bis", client=httpx.Client(base_url="http://bis", transport=httpx.MockTransport(handler))
    )
    clock = Clock(datetime(2026, 10, 20, 2, 59, tzinfo=UTC))
    runs = Runs()
    schedule = NightlySchedule(time(3, 0), bi, reports or MemoryReportStore(), runs, clock=clock)
    return schedule, clock, runs, bis_calls


def test_nightly_run_once_per_day_when_no_report():
    schedule, clock, runs, bis_calls = _schedule({"status": "ok", "sim_date": "2018-01-10"})
    assert schedule.tick() is False and bis_calls == []  # before 03:00
    clock.now = clock.now.replace(hour=3, minute=1)
    assert schedule.tick() is True
    assert runs.started == [("schedule", None, False)]
    assert schedule.tick() is False  # same day
    clock.now = clock.now.replace(day=21)
    assert schedule.tick() is True


def test_no_nightly_run_when_the_day_has_a_report():
    reports = MemoryReportStore()
    reports.save({"report_id": "r", "run_id": "x", "sim_date": "2018-01-10", "mode": "agent", "topics": []})
    schedule, clock, runs, _ = _schedule({"status": "ok", "sim_date": "2018-01-10"}, reports)
    clock.now = clock.now.replace(hour=4)
    assert schedule.tick() is False and runs.started == []


def test_nightly_check_retries_later_when_bis_is_down():
    answers = iter(
        [httpx.ConnectError("refused"), httpx.Response(200, json={"status": "ok", "sim_date": "2018-01-10"})]
    )

    def health():
        answer = next(answers)
        if isinstance(answer, Exception):
            raise answer
        return answer

    schedule, clock, runs, bis_calls = _schedule(health)
    clock.now = clock.now.replace(hour=3, minute=0)
    assert schedule.tick() is False
    clock.now = clock.now.replace(minute=10)
    assert schedule.tick() is False and len(bis_calls) == 1  # waiting for the retry
    clock.now = clock.now.replace(minute=16)
    assert schedule.tick() is True


def test_parse():
    assert NightlySchedule.parse("", None, None, None) is None  # type: ignore[arg-type]
    schedule = NightlySchedule.parse("03:30", None, None, None)  # type: ignore[arg-type]
    assert schedule is not None and schedule.at == time(3, 30)
