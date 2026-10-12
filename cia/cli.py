"""`cia` command line: migrate, api, worker."""

import argparse
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
        from cia.inbox import PostgresInbox
        from cia.worker.main import run_forever

        run_forever(PostgresInbox(settings.dsn()))


if __name__ == "__main__":
    main()
