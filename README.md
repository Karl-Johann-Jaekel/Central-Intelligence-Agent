# Central-Intelligence-Agent

Agentensystem über dem [Business-Intelligence-System](https://github.com/Karl-Johann-Jaekel/Business-Intelligence-System): Ein zentraler Agent bewertet die Lage des simulierten Unternehmens, beauftragt Bereichs-Agenten und schlägt Maßnahmen vor, die erst nach Freigabe ausgeführt werden.

> Das BI-System weiß, der CIA denkt und handelt.

Plan und Phasen: [plan.md](plan.md). Stand: **C0 – Gerüst und Verträge**.

## Aufbau

| Pfad | Inhalt |
|---|---|
| `cia/api/` | FastAPI; `POST /events` nimmt `insight.v1` und `daily_snapshot.v1` an (HMAC, idempotent) |
| `cia/contracts.py` | Validierung gegen die Verträge des BI-Repos (Submodule `vendor/bis`, Tag `contracts-v3-draft`) |
| `cia/inbox.py`, `cia/worker/` | Eingang und Worker |
| `cia/agents/`, `cia/policy/`, `cia/actions/`, `cia/telemetry/` | ab C1 |
| `migrations/` | SQL-Migrationen der Datenbank `cia` |
| `infra/` | Compose-Projekt `cia` neben dem BIS-Stack |

## Start

```sh
git clone --recurse-submodules git@github.com:Karl-Johann-Jaekel/Central-Intelligence-Agent.git
py -3.13 -m venv .venv && .venv/Scripts/pip install -e ".[dev]"
.venv/Scripts/python -m pytest -q
```

Betrieb im Stack: siehe [CLAUDE.md](CLAUDE.md) und [docs/bis-checklist.md](docs/bis-checklist.md).
