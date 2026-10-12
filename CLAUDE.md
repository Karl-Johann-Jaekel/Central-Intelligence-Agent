# Central-Intelligence-Agent

Plan: `plan.md`. Agentensystem über dem Business-Intelligence-System (BIS). Python 3.13 venv in `.venv`.

## Befehle

- Klonen: `git clone --recurse-submodules …` · nachträglich: `git submodule update --init`
- Setup: `py -3.13 -m venv .venv && .venv/Scripts/pip install -e ".[dev]"`
- Tests: `.venv/Scripts/python -m pytest -q` (DB-Tests nur mit gesetztem `CIA_PG_PASSWORD`, sonst skipped)
- DB-Tests: `.venv/Scripts/python -m pytest -q -m db`
- Lint: `.venv/Scripts/ruff check . && .venv/Scripts/ruff format --check .`
- Typen: `.venv/Scripts/mypy`
- Migrationen: `.venv/Scripts/cia migrate`
- Stack (BIS-Stack muss laufen, Netzwerk `bis_default`): `docker compose -f infra/docker-compose.yml up -d`
- API lokal: http://127.0.0.1:8200/health · Docs unter `/docs`

## Fallstricke

- Verträge kommen aus dem Submodule `vendor/bis` auf Tag `contracts-v3-draft` (`cia.contracts.CONTRACTS_TAG`).
  Neuer Tag: `git -C vendor/bis fetch --tags && git -C vendor/bis checkout <tag>`, `CONTRACTS_TAG` anpassen, committen.
- Vertragsänderungen nur im BI-Repo (`contracts/`), nie in `vendor/bis` editieren.
- Host `127.0.0.1`, nicht `localhost` (IPv6-Connect hängt auf Windows).
- Kein Zugriff auf `warehouse`; Daten nur über die BIS-API.
- Event-Eingang: Signatur vor Inhalt prüfen; Duplikat antwortet 200, sonst wiederholt die BIS-Outbox.
- Secrets nur in `infra/.env` (Vorlage `infra/.env.example`), keine Defaults im Code. Inhalt nie ausgeben.
- Schemaänderung = neue Datei `migrations/NNN_*.sql`, bestehende nie ändern.
- BIS-seitige Aufgaben: `docs/bis-checklist.md`.
