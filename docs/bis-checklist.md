# BIS-Seite für C0

Änderungen im Repo Business-Intelligence-System, damit der CIA im Stack läuft. Eigener Branch und PR im BI-Repo.

| # | Änderung | Datei im BI-Repo |
|---|---|---|
| 1 | Datenbank `cia` und Rolle `cia` (LOGIN, Owner von `cia`, kein `CONNECT` auf `warehouse`, `erp`, `dagster`, `keycloak`); Passwort aus `BIS`-Setup, nie im Code | `infra/postgres/init.sql` bzw. Setup-Skript für bestehende Volumes |
| 2 | Keycloak-Clients `cia-agent` und `cia-eval` (Client Credentials), Audience `cia-api` für Admin-Tokens | `infra/keycloak/realm-bis.json` |
| 3 | nginx-Route `location /cia/ { proxy_pass http://cia-api:8000/; }` (Präfix wird entfernt) | `frontend/nginx.conf` |
| 4 | Webhook-Consumer `cia`: `type: webhook`, `types: [anomaly, briefing, data_quality]`, `min_severity: info`, `url_env: BIS_CIA_WEBHOOK_URL` (`http://cia-api:8000/events`), `secret_env: BIS_CIA_WEBHOOK_SECRET` (gleich `CIA_WEBHOOK_SECRET`) | `events/consumers.yaml`, `infra/.env.example` |
| 5 | Zustellung von `daily_snapshot.v1` über dieselbe Outbox (Header `X-BIS-Event-Type: daily_snapshot.v1`) | mit BI A2 |
| 6 | `docs/central-intelligence-agent-plan.md` durch Verweis auf das CIA-Repo ersetzen | `docs/` |
| 7 | Backup des BIS-Stacks schließt die Datenbank `cia` ein | `infra/backup.sh` |

Reihenfolge: 1 → 3 → 4 (danach Ende-zu-Ende-Test), 2 erst für C1 nötig.
