# INT-01: Branch- und PR-Sichtung mit Integrationsplan

**Stand:** 2026-10-05  
**Audit:** INT-01, Priorität P2  
**Audit-Baseline:** [`365775603d1ad8e55d116fa52357fb06de403a52`](https://github.com/benednied/goldenage/commit/365775603d1ad8e55d116fa52357fb06de403a52)
(Audit vom 04.09.2026)  
**Zu sichtender Remote-Vorgang:** [PR #1 „Sanitize fixture personal data“](https://github.com/benednied/goldenage/pull/1)

## Ergebnis in Kürze

Die vier genannten Entwicklungsstände werden nicht als Ganzes übernommen. Die
vorgesehene Reihenfolge ist:

1. Remote-Snapshot von Default-Branch, Heads, Merge-Bases und PR #1 herstellen.
2. Fixture-Bereinigung als isoliertes Paket prüfen und übernehmen, falls sie im
   aktuellen Default-Branch noch fehlt.
3. M4 in einen HTTP-Adapter-/Vertragsteil und einen separaten CI-/Konfigurationsteil
   zerlegen.
4. `fix/frontend-audit` in Workflow-Fixes und Browser-Test-Infrastruktur teilen.

Die Reihenfolge ist bewusst prüfbar: M4 baut laut Audit auf der Fixture-Bereinigung
auf; Frontend- und Browser-Änderungen dürfen keine ungeprüften Backend- oder
Fixture-Änderungen einschleppen.

## Evidenz und Grenzen der Sichtung

Der Arbeitsbaum enthält den Quellstand und die Dokumentation, aber keine lesbaren
Git-Refs. Die Datei `.git` verweist auf einen Mirror außerhalb des freigegebenen
Arbeitsbereichs. Deshalb schlagen `git status`, Branch- und Merge-Base-Abfragen in
dieser Ausführung vor dem Lesen des Repositorys fehl. Direkte GitHub-/API-Abfragen
waren ebenfalls nicht verfügbar.

Damit sind folgende Aussagen **nicht** verifiziert und dürfen nicht als aktuelle
Remote-Fakten behandelt werden:

- Head und Datum der Default-Branch;
- Heads und Merge-Bases von `fix/frontend-audit`,
  `codex-m4-implementation` und `codex-sanitize-fixture-personal-data`;
- Status, Head und Checks von PR #1;
- welche Commits bereits in den Default-Branch integriert wurden.

Der für die Übergabe auszufüllende Remote-Status lautet derzeit:

| Referenz | Head | Merge-Base mit Default | PR-/Integrationsstatus | Lokaler Beleg |
| --- | --- | --- | --- | --- |
| Default-Branch (`master` laut Checkout-Annahme) | nicht verfügbar | — | nicht verifiziert | `.github/workflows/ci.yml`, `docs/ci.md` nennen `master` |
| `fix/frontend-audit` | nicht verfügbar | nicht verfügbar | nicht verifiziert | kein lesbarer Git-Ref |
| `codex-m4-implementation` | nicht verfügbar | nicht verfügbar | nicht verifiziert | kein lesbarer Git-Ref |
| `codex-sanitize-fixture-personal-data` | nicht verfügbar | nicht verfügbar | nicht verifiziert | kein lesbarer Git-Ref |
| PR #1 | nicht verfügbar | nicht verfügbar | nicht verifiziert | GitHub/API nicht erreichbar |

`master` ist im Checkout weiterhin die dokumentierte Annahme der CI-Konfiguration
(`.github/workflows/ci.yml` und `docs/ci.md`). Die Learnings vom 15.09.2026
referenzieren ebenfalls `origin/master` bei `3657756`; das ersetzt keinen
aktuellen Remote-Snapshot.

Der Snapshot muss vor einer Code-Übernahme von einem Maintainer mit Zugriff auf
Git ausgeführt und hier ergänzt werden:

```bash
git fetch origin --no-tags
git remote show origin
git for-each-ref --format='%(refname:short) %(objectname) %(committerdate:iso8601)' \
  refs/remotes/origin refs/heads
gh repo view benednied/goldenage --json defaultBranchRef
gh pr view 1 --repo benednied/goldenage \
  --json state,isDraft,baseRefName,headRefName,headRefOid,mergeStateStatus,statusCheckRollup
```

Für jeden Branch sind anschließend Head, Merge-Base, Diff-Stat, geänderte
Pfadgruppen (`src`, `tests`, `sql`, `pyproject.toml`, `uv.lock`, `.github`) und
bereits integrierte Commit-IDs zu protokollieren. Branches werden dabei weder
gemergt noch gelöscht.

## Baseline des vorliegenden Arbeitsbaums

Diese Änderungen/Flächen sind bereits im Baum sichtbar und dürfen aus einem
Alt-Branch nicht nochmals eingebracht werden, ohne einen tatsächlichen Diff- und
Patch-ID-Abgleich:

| Fläche | Beleg im Baum | Integrationshinweis |
| --- | --- | --- |
| Mailbox-/Mail-Import | `src/goldenage/adapters/mail.py`, `outlook_mailbox.py`, `tests/test_mail_adapter.py`, `tests/test_outlook_windows_import.py` | Als Baseline behandeln; M4 darf die Ports erweitern, nicht parallel eine zweite Importstrecke einführen. |
| SQLite/PostgreSQL-Dualpfad | `src/goldenage/adapters/sqlite.py`, `postgres.py`, `sql/`, `tests/test_*adapter.py` | Änderungen müssen beide Dialekte und ihre Tests berücksichtigen. |
| Lokale Konfiguration und Onboarding | `src/goldenage/config.py`, `tests/test_config.py`, `tests/test_bootstrap_sqlite.py` | Neue M4-Variablen additive und dokumentiert einführen. |
| Artefaktspeicher und Upload-Grenzen | `src/goldenage/adapters/artifact_storage.py`, `web/upload_security.py`, `docs/learnings/2026-09-15-secure-artifact-storage.md`, `docs/learnings/2026-09-20-upload-resource-limits.md` | Nicht durch einen älteren Upload-/Storage-Stand überschreiben. |
| Gisela-/Elizabethan-Verträge | `src/goldenage/application/ports.py`, `src/goldenage/adapters/demo.py` | Ports und deterministische Heuristiken sind vorhanden; echte HTTP-Clients fehlen. |
| UX-Regressionsschutz | `tests/test_web.py`, `tests/test_web_edges.py`, `docs/learnings/2026-05-18-ux-test-smells.md` | Bestehende HTTP-/statische Tests erhalten und um echte Browserprüfungen ergänzen. |
| Migrationserkennung | `src/goldenage/migration_validation.py`, `tests/test_migration_validation.py`, `docs/70-decisions/adr-0004-migration-identifiers.md` | Vor jeder Schemaänderung Validator ausführen; keine Migration aus einem Branch blind kopieren. |

Die aktuelle Lockfile-/Dependency-Baseline enthält `httpx` nur im `dev`-Extra.
Eine produktive HTTP-Implementierung benötigt daher eine bewusst begründete
Änderung von `pyproject.toml` und `uv.lock`; ein bloßes Lockfile-Cherry-Pick ist
kein ausreichender M4-Adapter.

Die vorhandene Outlook-Fixture `tests/fixtures/minimal.msg` wurde nur als
Metadatum geprüft: 1536 Bytes, SHA-256
`ffa7e62c9745edc28eb09cc97f7f08e6b795ee8f23b6690d2c3d05dc5ee4bdbd`. Ihr Inhalt
wird hier absichtlich nicht wiedergegeben. Die Hash-Prüfung beweist keine
fachliche Bereinigung; dafür ist der PR-Diff mit einem synthetischen Parser-
Ergebnis zu prüfen.

## Entscheidungen je Entwicklungslinie

| Linie | Entscheidung | Abhängigkeiten / Konflikte | Nächster Schritt | Verantwortlich |
| --- | --- | --- | --- | --- |
| PR #1 „Sanitize fixture personal data“ | **Bedingt übernehmen**, ausschließlich wenn der Remote-Diff eine echte Bereinigung ohne Funktionsänderung zeigt. Wenn die Änderung bereits im Default-Branch enthalten ist: No-op, nicht erneut cherry-picken. | Vorrang vor M4 laut Audit. Risiko: Binärfixture kann trotz kleiner Dateigröße reale Metadaten enthalten; Tests dürfen keine personenbezogenen Werte benötigen. | PR-Diff und Parser-/Testausgabe ohne Rohinhalte prüfen; synthetische Ersatzwerte und Hash dokumentieren. | Maintainer + Fixture-Verantwortliche/r |
| `codex-sanitize-fixture-personal-data` | **Als isoliertes Paket bzw. Quelle von PR #1 behandeln**, nicht zusätzlich als vollständige Branch-Übernahme. | M4 baut darauf auf; Konflikte wahrscheinlich nur in `tests/fixtures` und fixture-bezogenen Assertions. | Nach PR-Status entscheiden: übernehmen, auf aktuellen Default rebasen oder als bereits integriert schließen. | Maintainer |
| `fix/frontend-audit` | **Selektiv übernehmen**; Branch nicht als Ganzes mergen. | Mögliches Überschneiden von `web/`, `tests/test_web*.py`, Lockfile und ggf. Fixture. Playwright ist im aktuellen Baum nicht konfiguriert. | In F1 Workflow-Fixes und F2 Browser-Test-Infrastruktur teilen; A11Y-01-Zuordnung unten beachten. | Frontend-Verantwortliche/r |
| `codex-m4-implementation` | **Selektiv übernehmen**, nach Fixture-Paket und Port-/Vertragsprüfung. | Aktuelle Ports/Heuristiken, Konfiguration und Mailboxflächen sind vorhanden; echte HTTP-Adapter sind nicht sichtbar. `httpx` ist nur dev. Migrationen sind nicht automatisch erforderlich. | M4-A und M4-B getrennt vorbereiten; Runtime-Dependency nur mit Adapter und Lockfile begründen. | Backend-/Integrationsverantwortliche/r |

Keine Linie wird aufgrund ihres Namens als vollständig integriert angenommen.
Merge-, Close- und Branch-Löschentscheidungen sind separate Maintainer-Aktionen
nach den grünen Gates; sie gehören nicht zu dieser Arbeitsbaumänderung.

## Integrationsmatrix und kleine PR-Pakete

| Reihenfolge | Paket / vorgeschlagener PR-Titel | Inhalt | Nicht enthalten | Gate / Ergebnis |
| --- | --- | --- | --- | --- |
| 0 | `INT-01: remote branch snapshot and integration plan` | Remote-Evidenz ergänzen, Diff-Stat und Merge-Basen festhalten, Entscheidungen aktualisieren. | Keine Produktivcode- oder Branch-Mutation. | Audit-Links, Commit-IDs und Abhängigkeiten nachvollziehbar. |
| 1 | `test: sanitize synthetic mail fixture data` | Nur Fixture und unmittelbar betroffene, wertneutrale Assertions aus PR #1; Rohdaten nicht in Issues/PR-Beschreibung kopieren. | M4, Frontend, Lockfile, Migrationen. | Parser-Test, `git diff --check`, vollständige Quality-Gates. |
| 2a | `feat: add bounded HTTP clients for assignment and search` | HTTP-Adapter hinter `GiselaClient`/`ElizabethanSearchClient`, Timeout, Response-Validierung, Fehlerabbildung, Konfiguration über klar benannte Variablen. | Web-Redesign, Datenbankschema, freie Modell-/SQL-Ausführung. | Deterministische Transport-Contract-Tests ohne Netzwerk; keine Secrets im Code. |
| 2b | `build: lock runtime integration dependency` | Nur falls 2a eine neue Runtime-Abhängigkeit braucht: `pyproject.toml`, `uv.lock`, minimale Betriebsdokumentation. | Unverbundene Dependency-Upgrades. | `uv sync --frozen --extra dev`, Lock-Konsistenz, Security-/CI-Prüfung. |
| 3a | `fix: land frontend workflow corrections` | Zusammengehörige DOM-/HTMX-/Status-/Tastatur-/Responsive-Fixes aus `fix/frontend-audit`; vorhandene HTTP-Tests gezielt schärfen. | Playwright-Runner, neue Backend-Adapter, Fixtures ohne Notwendigkeit. | A11Y-01-Testfälle, Screenshots/Browserflow bei UI-Änderungen, Quality-Gates. |
| 3b | `test: add browser coverage for audited workflows` | Minimaler Browser-Test-Runner und wenige stabile Smoke-Flows nach F1; keine neue Produktlogik. | Ungeprüfte Branch-Gesamtübernahme oder flächige E2E-Abdeckung. | CI reproduzierbar, Browser-Binaries dokumentiert, A11Y-01-Flows grün. |

Paket 2a kann nach Paket 1 parallel zu 3a vorbereitet werden, sofern es keine
Frontend-Dateien ändert. Für eine konfliktarme Review ist die empfohlene
Merge-Reihenfolge dennoch 1 → 2a/2b → 3a → 3b. Nach jedem Merge ist der nächste
Branch neu vom aktuellen Default-Branch zu erstellen; alte Branches bleiben bis
zur separaten Aufräumentscheidung bestehen.

## PR #1: fachliche und Datenprüfung

Die Freigabe von PR #1 hängt an diesen Nachweisen:

- Die Änderung ersetzt reale oder nicht eindeutig synthetische Fixture-Werte
  durch klar erfundene Werte und ändert nicht die Parsersemantik.
- Assertions prüfen Verhalten bzw. stabile synthetische Marker, nicht Namen,
  Adressen oder andere reale Identifikatoren.
- Der Outlook-Container bleibt parsebar; `tests/test_outlook_extractor.py`,
  Upload- und Web-Flows bleiben grün.
- Keine zusätzlichen Dependencies, Migrationen, Environment-Variablen oder
  Frontend-Änderungen werden als Nebeneffekt eingeführt.
- M4 referenziert anschließend nur die bereinigte Fixture, niemals den alten
  Rohinhalt.

Der aktuelle Arbeitsbaum kann nur die Fixture-Metadaten und die vorhandenen
Tests belegen. Ob PR #1 diese Bedingungen erfüllt oder schon integriert ist,
bleibt bis zum Remote-Snapshot offen.

## Frontend-Aufteilung und A11Y-01

`tests/test_web_edges.py::test_static_ux_contracts_keep_focus_order_and_responsive_layout`
prüft bereits DOM-Reihenfolge, fehlendes implizites Button-Rollen-Markup, keine
automatische Formularübermittlung im Dropzone-Skript und das responsive
Zusammenfallen der Settings-Spalten. `tests/test_web.py` prüft außerdem
Status-/Busy-Anzeigen in den HTMX-Flows. Diese Tests sind wertvoller
Regressionsschutz, ersetzen aber keinen Browserlauf.

Die Frontend-Pakete ordnen A11Y-01 so zu:

| A11Y-01-Flow | Bestehender Beleg | Noch zu prüfen / F1 oder F2 |
| --- | --- | --- |
| Login-Fokus und Provider-Auswahl | `login.html`, `test_web_edges.py` | Tatsächliche Tab-Reihenfolge, sichtbarer Fokus, deaktivierte Option im Browser (F1/F2). |
| Intake-Dropzone und Dateiauswahl | `intake_panel.html`, `intake.js`, Web-Flow-Tests | Tastaturbedienung, Drop-/Fehlerrückmeldung und Fokus nach HTMX-Swap (F1/F2). |
| Asynchrone Analyse/Suche/Import | `role="status"`, `aria-live`, `hx-indicator` in `intake_panel.html` | Screenreader-sichtbare Zustandswechsel und keine stale Actions (F1, danach F2). |
| Worklist-Status | Textstatus plus Farbpunkte in `partials/worklist.html` | Kontrast und echte Tastatur-/Viewport-Prüfung (F1/F2). |
| Responsive Layout | Media Queries in `style.css`, statischer Vertragstest | Mobile Browserlauf für Worklist, Intake und Login (F2). |

Damit ist A11Y-01 nicht als vollständig erledigt markiert: Die vorhandenen
Tests sind zugeordnet, die verbliebenen Browserlücken sind explizit dem
Frontend-Paket zugewiesen.

## M4: Architekturprüfung

Der aktuelle Vertrag in `application/ports.py` ist kompatibel mit einem
transportneutralen HTTP-Adapter: `GiselaClient.analyze_artifact` liefert genau
eine `AssignmentSuggestion`, `ElizabethanSearchClient.search_cases` eine
begrenzte Ergebnisfolge. Die bestehenden Heuristiken in `adapters/demo.py`
bleiben für Demo-/Offline-Modus erhalten.

Ein M4-Adapter darf daher nur:

- sichtbare, bereits normalisierte Artefakt-/Mail-/Fall-Daten senden;
- konfigurierbare HTTPS-Endpunkte, Timeouts und begrenzte Antwortgrößen nutzen;
- Antworten strikt validieren und Netzwerk-, Timeout- und Remote-Fehler in die
  bestehende Anwendungsfehlerspur abbilden;
- keine Modellantwort als SQL, Repository-Aufruf oder Berechtigung akzeptieren;
- ohne konfigurierten Endpoint deterministisch auf den bestehenden Adapterpfad
  zurückfallen oder mit einer klaren Konfigurationsfehlermeldung abbrechen.

M4 darf keine Migration voraussetzen, solange die HTTP-Verträge nur vorhandene
Domainmodelle verwenden. Falls der Branch neue Persistenz einführt, ist das ein
separates Schema-Paket mit PostgreSQL-/SQLite-Migration, Validator und beiden
Adaptertests. Eine neue Runtime-Abhängigkeit wird nur aufgenommen, wenn sie im
Adapter importiert wird; `httpx` im dev-Extra allein ist kein Beleg für einen
fertigen M4-Client.

## Validierung und offene Übergabe

Für dieses Dokument wurden keine Codeänderungen vorgenommen. Die
Dokumentationsprüfung erfolgt mit:

```bash
rg -n 'INT-01|codex-m4-implementation|codex-sanitize-fixture|fix/frontend-audit|PR #1|A11Y-01' docs
```

Nach einer Codeübernahme sind mindestens die im CI-Dokument festgelegten Gates
auszuführen:

```bash
uv run --extra dev ruff check .
uv run --extra dev ruff format --check .
uv run --extra dev ty check
uv run --extra dev pytest -q
```

Bei Migrationen kommt `uv run --extra dev python -m goldenage.migration_validation`
hinzu. Bei F1/F2 werden die betroffenen Browser-Flows mit Screenshots oder
einem kurzen Laufprotokoll ergänzt.

**Übergabe:** Ein Maintainer mit GitHub-Zugriff ergänzt zuerst die Remote-IDs und
den PR-Status in diesem Dokument. Erst danach werden die kleinen PR-Pakete auf
separaten, aktuellen Arbeitsbranches vorbereitet. Kein Branch wird in diesem
Schritt pauschal gemergt, geschlossen oder gelöscht.
