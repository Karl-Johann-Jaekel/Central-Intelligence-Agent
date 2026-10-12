# PLAN.md – Central-Intelligence-Agent

Stand: 2026-10-09 · Version 3 · ersetzt Version 2 vom 2026-10-08 · abgestimmt mit dem Plan des Business-Intelligence-Systems, Version 3

> Maßgeblich ist diese Kopie im CIA-Repo (seit 2026-10-12). Im BI-Repo bleibt unter `docs/central-intelligence-agent-plan.md` nur ein Verweis ([docs/bis-checklist.md](docs/bis-checklist.md)).

## 0. Änderungen gegenüber Version 2

- **Rolle erweitert:** Der CIA ist das ganze Agentensystem des Agentic BI, nicht nur die Handlungsschicht: zentraler Agent, Bereichs-Agenten, kontrolliertes Spawning, Maßnahmen und Monitoring der Agenten. Das BI-System enthält keine Agenten.
- **Kein Ollama, kein lokales Modell.** LLM ist Mistral (EU, kostenlose Stufe). Alle Daten des Referenzunternehmens sind synthetisch und `public`; das Routing nach `data_class` gilt weiter.
- **Kein eigenes Cockpit.** Das BI-Frontend ist das einzige Portal und liest die CIA-API über `/cia/` auf derselben Origin. Freigaben liegen im Admin-Bereich des Portals.
- **Kein n8n zum Start.** Router und Ausführung als Python-Dienst; n8n optional, wenn der RAM es zulässt (offene Entscheidung 2 aus Version 2 entschieden).
- **Neu: Monitoring der Agenten** mit Kennzahlen, Evaluator gegen Ground Truth und Export als `agent_metrics.v1` an das BI-System (Bereich „AI Operations“).
- **Neu: Phasen nach der Agentic-BI-Architektur** (zentraler Agent → Bereichs-Agenten → Spawning → Maßnahmen), verzahnt mit den BI-Phasen A0–A5.
- **Ticket-Ziel:** zuerst das simulierte Ticket-System des BI-Systems, echte Ziele später (offene Entscheidung 1 vorläufig entschieden).

## 1. Zweck

Agentensystem über dem Business-Intelligence-System. Ein zentraler Agent bewertet den Gesamtzustand des simulierten Unternehmens, priorisiert Auffälligkeiten und beauftragt spezialisierte Bereichs-Agenten mit klar abgegrenzten Aufgaben. Ergebnisse kommen mit Belegen zurück, werden zu einem Lagebericht zusammengeführt und können nach Freigabe zu Maßnahmen werden.

**Leitsatz:** Das BI-System weiß, der CIA denkt und handelt.

**Architekturgedanke:** Der zentrale Agent analysiert nicht alles selbst. Er bewertet, priorisiert und delegiert. Jeder Auftrag hat ein Ziel, einen Bereich, eine Werkzeugliste, ein Budget und ein Ausgabeschema.

**Warum ein eigenes Repo:** Trennung von Lesen und Handeln. Nur der CIA darf Maßnahmen auslösen, mit eigener Freigabe, eigenem Audit-Log und eigenen Zugangsdaten. Ein Fehler im Agenten kann im BI-System nichts verändern außer über die protokollierten Endpunkte `facts` und `actions/sim-ticket`.

**Nicht-Ziele**
- Keine KPI-Berechnung, keine Datenpipeline, kein Wissensspeicher, keine Suche (alles im BI-System)
- Kein direkter Zugriff auf die Warehouse-Datenbank; nur die BI-API
- Keine eigene Oberfläche für Gäste; das Portal ist das BI-Frontend
- Keine Änderung an Hive Mind

## 2. Systemlandschaft

```
                 Business-Intelligence-System (Daten, Simulation, Wissen, Portal)
   insight.v1 · daily_snapshot.v1 (Webhook)      │        ▲  POST /facts · /actions/sim-ticket
   Werkzeug-API (lesend, Client Credentials)     │        │  agent_metrics.v1 (BI zieht täglich)
   Gast-Token (JWKS) · Portal liest /cia/…       ▼        │
 ┌─────────────────────────── Central-Intelligence-Agent ───────────────────────────┐
 │ api/        FastAPI: Läufe, Aufträge, Befunde, Lagebericht, Fragen, Freigaben,    │
 │             Konfiguration, Metriken                                               │
 │ agents/     LangGraph: zentraler Agent, Bereichs-Agenten (gemeinsame              │
 │             Implementierung, Konfiguration je Bereich)                            │
 │ policy/     Spawn-Policy, Budgets, Werkzeugrechte, Leitplanken                    │
 │ actions/    action.v1, Freigabe, Ausführung, Audit-Log                            │
 │ telemetry/  Traces, Kennzahlen, Evaluator                                         │
 │ worker/     Event-Eingang, Warteschlange, Nachtlauf                               │
 └──────────────────────────────────────────────────────────────────────────────────┘
```

**Infrastruktur auf dem VPS**
- Eigenes Compose-Projekt `cia`: `cia-api`, `cia-worker` (beide aus einem Image, Speicherlimit zusammen ≤ 1 GB)
- Datenbank `cia` auf der Postgres-Instanz des BI-Stacks, eigene Rolle ohne Zugriff auf `warehouse`; im Backup des BI-Stacks enthalten
- Kein eigener Port nach außen: erreichbar nur über das Frontend-nginx des BI-Systems unter `/cia/`
- Keycloak-Clients im Realm `bis`: `cia-agent`, `cia-eval` (Client Credentials), Audience `cia-api` für Admin-Tokens

## 3. Eintrittskriterien

- [x] BI-System M1 (Pipeline, Insights, Outbox mit Webhook, MCP, `insight.v1` getaggt)
- [x] BI A0: `daily_snapshot.v1`, `task.v1`, `finding.v1`, `action.v1`, `agent_metrics.v1` getaggt (`contracts-v3-draft`)
- [ ] Mistral-Konto und Schlüssel (kostenlose Stufe), Limits bekannt

C0 und C1 laufen gegen die vorhandene BI-API. Für C2 braucht es die Simulation (A2), für M2 die Werkzeug-API und Agenten-Seiten im Portal (A3).

## 4. Verträge

Quelle der Wahrheit ist `contracts/` im BI-Repo. Dieses Repo bindet eine getaggte Version ein (Git-Submodule oder Release-Artefakt) und führt dieselben Vertragstests in seiner CI aus.

| Vertrag | Inhalt | Von → An |
|---|---|---|
| `insight.v1` | Anomalie, Briefing, Datenqualität | BI → CIA |
| `daily_snapshot.v1` | Tagesabschluss: Simulationsdatum, Ampel je Bereich, offene Insights | BI → CIA |
| `task.v1` | Auftrag: Bereich, Ziel, Bezüge, erlaubte Werkzeuge, Budget, Tiefe, Eltern-Auftrag, Ausgabeschema | CIA intern; Portal liest |
| `finding.v1` | Befunde mit Zahlen und Belegen, Ursachen-Hypothesen mit Konfidenz, Handlungsvorschläge, offene Fragen | CIA intern; Portal liest |
| `action.v1` | Vorschlag, Freigabe, Ausführung, Ergebnis (Kern wie Version 2, Typen siehe §5.5) | CIA → BI |
| `agent_metrics.v1` | Tageswerte je Agentenrolle (§5.6) | CIA → BI |
| `decision.v1`, `context.v1` | Entscheidungen, Kontextpaket (ab BI A4) | BI → CIA |
| Entitäts-Namensraum | `<typ>:<quellschlüssel>` | beide |

## 5. Komponenten

### 5.1 Zentraler Agent

Auslöser: `daily_snapshot.v1` (Nachtlauf), kritisches `insight.v1` (sofort), Admin-Start, Gastfrage.

1. **Lagebild lesen:** Ampel je Bereich, offene Insights, offene und kürzlich abgeschlossene Aufträge.
2. **Priorisieren:** Vorsortierung per Formel `Schwere × Geschäftswirkung × Konfidenz × Neuheit` (deterministisch), dann Auswahl durch das LLM: höchstens N Themen pro Tag (Start: 3), zusammenhängende Insights bereichsübergreifend gebündelt.
3. **Delegieren:** je Thema ein `task.v1` an den zuständigen Bereichs-Agenten.
4. **Zusammenführen:** Lagebericht: was ist wichtig, was wurde untersucht, was wird empfohlen, was ist offen. „Kein Zusammenhang gefunden“ ist ein gültiges Ergebnis.
5. **Fragen beantworten:** Gast- und Admin-Fragen mit Lesewerkzeugen, mit Belegen, im Kontingent.

### 5.2 Bereichs-Agenten

Ein Agent je Bereich des BI-Systems (Marketing, Sales, Finance, Customer, Supply, Operations, HR, Projects, IT, Management); eine gemeinsame Implementierung, Konfiguration je Bereich (Werkzeugrechte, Prompt, Budget, Fachbegriffe).

Ablauf je Auftrag: Daten prüfen → Treiber zerlegen → Hypothesen bilden und prüfen → `finding.v1`. Werkzeuge nur aus dem eigenen Bereich; Fragen an andere Bereiche nur über eine Spawn-Anfrage (§5.4).

### 5.3 Werkzeuge

`bi.departments`, `bi.kpis`, `bi.series`, `bi.breakdown`, `bi.drivers`, `bi.insights`, ab BI A4 `bi.search`, `bi.decisions`, `bi.context`; dazu `agents.request_spawn` und `actions.propose`. Alle BI-Werkzeuge sind lesend und parametrisiert. Schreibende Werkzeuge existieren nur als Vorschlag; ausgeführt wird nach Freigabe (§5.5).

### 5.4 Kontrolliertes Spawning

Jeder Agent kann über `agents.request_spawn` einen weiteren Auftrag anfordern. Der Orchestrator prüft vor der Ausführung:

| Prüfung | Regel (Startwerte) |
|---|---|
| Berechtigung | Matrix: welcher Bereich darf welchen Bereich beauftragen; der zentrale Agent darf alle |
| Tiefe | höchstens 2 Ebenen unter dem zentralen Agenten |
| Breite | höchstens 3 Unteraufträge je Auftrag, höchstens 15 Aufträge je Lauf |
| Kosten | Restbudget des Laufs (Tokens, Euro) muss den geschätzten Bedarf decken |
| Wiederholung | kein Auftrag mit gleichem Bereich, Ziel und Bezügen im selben Lauf oder in den letzten 7 Simulationstagen ohne neue Daten |
| Zyklen | kein Auftrag an einen Vorfahren mit gleichem Ziel |

Jede Entscheidung wird mit Grund gespeichert. Abgelehnte Anfragen erscheinen im Befund des anfragenden Agenten als offene Frage.

### 5.5 Maßnahmen und Autonomiestufen

| Stufe | Verhalten | Startumfang |
|---|---|---|
| L0 | Nur informieren | Lagebericht, Befunde |
| L1 | Vorschlag, Ausführung nach Freigabe durch den Admin im Portal | Ticket im simulierten Unternehmen, E-Mail an den Admin |
| L2 | Automatische Ausführung für eine feste Positivliste | zunächst leer; erst nach Evals |

`action.v1`-Typen: `create_sim_ticket` (BI `POST /actions/sim-ticket`), `notify_admin`, `write_fact` (BI `POST /facts`, Kante `TRIGGERED`), später `create_ticket` für echte Ziele. Idempotenz über `action_id`. Jede Aktion steht mit Auslöser, Begründung, Freigabe und Ergebnis im Audit-Log. Hochriskante Typen bleiben dauerhaft auf L1.

### 5.6 Monitoring der Agenten

**Traces (live, ab C1):** jeder Lauf, Auftrag, Schritt, Werkzeugaufruf, LLM-Aufruf und jede Spawn-Entscheidung mit Zeit, Dauer, Tokens, Kosten, Status, Fehler. Das Portal zeigt laufende Läufe und die Zeitleiste jedes Laufs.

**Kennzahlen (`agent_metrics.v1`, täglich je Agentenrolle):**

| Kennzahl | Bedeutung |
|---|---|
| `runs`, `tasks_started`, `tasks_completed`, `tasks_failed` | Durchsatz |
| `error_rate` | fehlgeschlagene Aufträge und Werkzeugaufrufe |
| `cost_eur`, `tokens_in`, `tokens_out` | Kosten |
| `latency_p50_s`, `latency_p95_s` | Laufzeit je Auftrag |
| `delegation_depth_avg`, `delegation_depth_max` | Delegationstiefe |
| `spawns_requested`, `spawns_rejected` (nach Grund) | Spawning |
| `budget_exceeded` | Abbrüche wegen Budget |
| `tasks_open_overdue` | nicht abgeschlossene Aufträge über ihrer Frist |
| `guardrail_violations` | verworfene Befunde (Zahl ohne Beleg, Beleg nicht auflösbar) |
| `false_findings`, `missed_scenarios` | vom Evaluator gegen Ground Truth |

**Evaluator:** eigenes Dienstkonto `cia-eval` mit `read:eval`. Er vergleicht Befunde mit den Szenarien des BI-Systems (`ops.scenarios`). Agenten haben diesen Scope nie.

**Auswertung:** Das BI-System zieht die Tageswerte (Loader `cia_metrics`) in den Bereich `ai_ops` und überwacht sie mit seiner Anomalieerkennung. Alerts dazu gehen nur an den Admin, nicht an den zentralen Agenten.

### 5.7 LLM

- Mistral über eine schmale Provider-Schicht mit Routing nach `data_class` (gleiche Regel wie im BI-System, gleicher Test: `confidential` an einen externen Provider schlägt fehl).
- Modell je Rolle konfigurierbar (zentraler Agent größer, Bereichs-Agenten klein); Admin kann einen eigenen Provider setzen.
- Strukturierte Ausgaben (JSON Schema aus den Verträgen), feste Ablaufgraphen je Rolle; frei planend nur innerhalb der Spawn-Policy.
- Leitplanken: jede Zahl muss in einem Werkzeugergebnis vorkommen; jeder Befund braucht einen auflösbaren Beleg; Hypothesen mit Konfidenz.

### 5.8 Zugang

- **Gast:** Gast-Token des BI-Systems (RS256, Prüfung per JWKS); darf lesen und im Kontingent fragen (Start: 5 pro Gast und Tag, globales Tageslimit).
- **Admin:** Keycloak-Token mit Admin-Rolle; Läufe starten, Konfiguration ändern (versioniert), Maßnahmen freigeben.
- **Dienstkonten:** `cia-agent` für die Werkzeuge, `cia-eval` für den Evaluator.

## 6. Szenarien und Evals

Die Szenarien kommen aus der Simulation des BI-Systems (Szenario-Plan und Admin-Injektion) und sind zugleich die Abnahmetests. Jede Suite enthält Gegenszenarien ohne Störung.

| Beispiel | Erwartung |
|---|---|
| Lieferantenausfall in Supply | Supply unter den Top-3-Prioritäten; Befund nennt Lieferant und Folgen für Lieferzeit und Bewertungen; Spawn an Customer für die Ticketlage |
| Krankheitswelle im Fulfillment | Operations/HR priorisiert; Befund verbindet Überstunden, Krankenquote und Durchsatz |
| Kampagnenstopp im Marketing | Befund verbindet Sessions, Conversion und Umsatz einer Kategorie |
| Gegenszenario | kein Befund behauptet eine Ursache über der Konfidenzschwelle |

| Metrik | Messung |
|---|---|
| Priorisierung | Anteil der Szenarien, deren Bereich unter den Top 3 landet |
| Ursachen-Treffer | Anteil der Befunde, die den injizierten Treiber nennen |
| Falschbehauptungen | Anteil der Gegenszenarien mit behaupteter Ursache (Ziel: 0) |
| Belegtreue | jede Aussage hat einen auflösbaren Verweis |
| Spawn-Disziplin | abgelehnte Spawns nach Grund, maximale Tiefe, Anteil unnötiger Aufträge |
| Aufwand | Laufzeit und Kosten pro Lauf und pro Szenario |
| Vorschlagsqualität | Anteil angenommener Maßnahmen |

## 7. Phasen und Definition of Done

Zeitangaben in Vollzeit-Wochen. Die Phasen folgen der Agentic-BI-Architektur: Fundament (BI) → zentraler Agent → Bereichs-Agenten → Spawning → Maßnahmen; das Monitoring wächst ab C1 mit.

### C0 – Gerüst und Verträge (ca. 3 Tage) – CIA-Seite gebaut 2026-10-12, BIS-Seite offen ([docs/bis-checklist.md](docs/bis-checklist.md))
- Repo, Compose-Projekt, Datenbank `cia`, Keycloak-Clients, nginx-Route im BI-Frontend
- Getaggte Verträge aus dem BI-Repo, Vertragstests in der CI
- Event-Eingang für `insight.v1` mit HMAC-Prüfung und Idempotenz; Trace-Tabellen

**DoD:** Die CI prüft gegen dieselbe Vertragsversion wie das BI-Repo; ein gebrochenes Payload lässt den Test fehlschlagen. Doppelte Zustellung erzeugt keinen zweiten Eingang.

### C1 – Zentraler Analyse-Agent (ca. 1 Woche)
- Lagebild, Priorisierung, Lagebericht mit Belegen; Fragen von Gast und Admin mit Kontingent
- Mistral-Provider, Leitplanken, Traces für jeden Schritt
- API für Portal: Läufe, Lagebericht, Fragen

**DoD:** Auf den vorhandenen KPIs entsteht täglich ein Lagebericht, in dem jede Zahl belegt ist. Eine Gastfrage über dem Kontingent wird abgelehnt. Jeder Lauf ist als Zeitleiste abrufbar.

### C2 – Bereichs-Agenten (ca. 1,5 Wochen, nach BI A2)
- `task.v1`/`finding.v1`, Bereichs-Agenten für alle zehn Bereiche, Werkzeugrechte je Bereich
- Zentraler Agent delegiert und führt zusammen

**DoD:** In der Szenario-Suite landet der betroffene Bereich bei ≥ 80 % unter den Top 3, der Befund nennt den injizierten Treiber bei ≥ 60 %, kein Gegenszenario hat eine behauptete Ursache. Ein Agent kann kein Werkzeug außerhalb seines Bereichs aufrufen (Test).

**Meilenstein M2 (mit BI A3):** öffentlich vorzeigbares Agentic BI.

### C3 – Kontrolliertes Spawning (ca. 1 Woche)
- `agents.request_spawn`, Spawn-Policy (§5.4), Begründung je Entscheidung

**DoD:** Tests für jede Regel (Berechtigung, Tiefe, Breite, Kosten, Wiederholung, Zyklus). In der Szenario-Suite entstehen keine Aufträge über der maximalen Tiefe, und kein Lauf überschreitet sein Budget.

### C4 – Monitoring und Evals (ca. 1 Woche)
- `agent_metrics.v1`, Evaluator mit `cia-eval`, Eval-Suite per Befehl
- Live-Monitoring im Portal (über BI A3)

**DoD:** Die Eval-Suite läuft per Befehl und berichtet alle Metriken aus §6. Das BI-System lädt die Tageswerte, und eine künstlich erhöhte Fehlerrate löst einen Alert an den Admin aus.

### C5 – Maßnahmen mit Freigabe (ca. 1 Woche, nach BI A5)
- `action.v1`, Freigabe-Queue im Admin-Bereich des Portals, Ausführung, Audit-Log
- Rückschreiben als Fakt (`TRIGGERED`), Ticket im simulierten Unternehmen

**DoD:** Der Weg Insight → Befund → Vorschlag → Freigabe → Ticket → Fakt im Graph ist für alle Szenarien lückenlos nachvollziehbar. Doppelte Freigabe erzeugt kein zweites Ticket. Ohne Freigabe passiert nichts außerhalb von L0.

**Meilenstein M3 (mit BI A5):** vollständiges Agentic BI.

### C6 – Ausbau (offen)
- L2 für eine kleine Positivliste, echte Ticket-Ziele, n8n für sichtbare Workflows
- Geschlossener Kreis mit der Simulation (angenommene Maßnahmen wirken auf die Daten)

## 8. Gemeinsame Roadmap

Maßgeblich ist die Tabelle im BI-Plan (§13, „Gemeinsame Roadmap“). Kurzform: A0 → A1 ∥ C0/C1 → A2 → A3 ∥ C2 (M2) → C3, C4 ∥ A4 → A5 ∥ C5 (M3).

## 9. Risiken

| Risiko | Gegenmaßnahme |
|---|---|
| Kostenlose LLM-Stufe zu knapp für viele Agenten | Höchstens 3 Themen pro Tag, Budgets, Nachtlauf, kleine Modelle für Bereichs-Agenten; Ausfallmodus: Lagebild ohne Agententext |
| Agenten erfinden Ursachen | Leitplanken, Hypothesen mit Konfidenz, Gegenszenarien, Falschbefund-Quote im Monitoring |
| Spawning eskaliert | Policy mit harten Grenzen, jede Entscheidung protokolliert, Kennzahlen in AI Ops |
| Agenten sehen die Lösung | Ground Truth nur für `cia-eval` |
| Vertragsbruch zwischen den Repos | getaggte Verträge, Tests in beiden CIs, nur additive Änderungen |
| Agent löst unerwünschte Aktionen aus | L1 als Standard, L2 nur per Positivliste, Idempotenz, Audit-Log |
| Zwei Repos, eine Person | Eintrittskriterien einhalten; gemeinsame Roadmap; kein Start vor BI A0 |
| RAM auf dem VPS | Speicherlimit ≤ 1 GB, kein n8n zum Start, gemeinsame Postgres-Instanz |

## 10. Offene Entscheidungen

1. Echtes Ticket-Ziel zusätzlich zum simulierten (z. B. GitHub Issues) – ab C6
2. Mistral-Modelle je Rolle nach Test der Limits
3. Endgültiger Name des Repos
