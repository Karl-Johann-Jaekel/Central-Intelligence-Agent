"""`cia` command line: migrate, api, worker, run."""

import argparse
import json
import logging

from cia import settings


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="cia")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("migrate", help="apply pending SQL migrations to the cia database")
    api = commands.add_parser("api", help="run the API (uvicorn)")
    api.add_argument("--host", default="127.0.0.1")
    api.add_argument("--port", type=int, default=8200)
    commands.add_parser("worker", help="run the inbox worker")
    run = commands.add_parser("run", help="start a run of the central agent now (admin)")
    run.add_argument(
        "--dry-run", action="store_true", help="keep traces and report in memory, print the report"
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

    if args.command == "migrate":
        from cia.migrate import migrate

        applied = migrate(settings.dsn())
        print(f"Applied: {', '.join(applied)}" if applied else "Up to date")
    elif args.command == "api":
        import uvicorn

        # /cia/ is stripped by the BIS nginx; root_path keeps the OpenAPI links right.
        uvicorn.run("cia.api.main:app", host=args.host, port=args.port, root_path="/cia")
    elif args.command == "worker":
        from cia.agents.reports import PostgresReportStore
        from cia.bi_client import get_client
        from cia.inbox import PostgresInbox
        from cia.worker.main import NightlySchedule, run_forever

        agent = central_agent(dry_run=False)
        schedule = NightlySchedule.parse(
            settings.nightly_at(), get_client(), PostgresReportStore(settings.dsn()), agent.run
        )
        run_forever(PostgresInbox(settings.dsn()), agent.run, schedule)
    elif args.command == "run":
        from cia.bi_client import BIError

        try:
            report = central_agent(dry_run=args.dry_run).run("admin", "cli")
        except BIError as exc:
            raise SystemExit(f"Run failed: {exc}") from exc
        if args.dry_run:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        else:
            print(
                f"{report['sim_date']} [{report['mode']}] {report['headline']} (report {report['report_id']})"
            )


def central_agent(dry_run: bool):
    from cia.agents.central import CentralAgent, CentralConfig
    from cia.agents.reports import MemoryReportStore, PostgresReportStore, ReportStore
    from cia.bi_client import get_client
    from cia.llm.provider import get_provider
    from cia.telemetry.tracer import MemoryTraceStore, PostgresTraceStore, TraceStore

    traces: TraceStore
    reports: ReportStore
    if dry_run:
        traces, reports = MemoryTraceStore(), MemoryReportStore()
    else:
        traces, reports = PostgresTraceStore(settings.dsn()), PostgresReportStore(settings.dsn())
    config = CentralConfig(max_topics=settings.max_topics(), token_budget=settings.run_token_budget())
    return CentralAgent(get_client(), get_provider("central"), traces, reports, config)


if __name__ == "__main__":
    main()
