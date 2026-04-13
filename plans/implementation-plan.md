# GoldenAge MVP Implementation Plan

## Summary

- Build GoldenAge as a browser-based, server-rendered HTMX application on Python 3.14, PostgreSQL, and raw SQL with clean architecture boundaries.
- Deliver it in phases: the first release covers daily worklist handling and manual Outlook `.msg` intake; mailbox ingestion, OCR, and harder enterprise integrations follow without changing the core domain model.
- Keep the future `gisela` agent and `elizabethan` search system in this repo initially, but only behind stable adapter contracts so they can be extracted later with minimal churn.

## Implementation Changes

- Scaffold `src/goldenage/{domain,application,adapters,web}` and standardize local work on `uv`, `pytest`, `ty`, Google Python style, and explicit SQL migrations; promote these defaults into `docs/70-decisions/techstack.md` and `docs/70-decisions/styleguide.md`.
- Model the core domain around `Case` (`Vorgang`), `Activity` (`Tätigkeit/Wiedervorlage`), `Artifact` (`Eingang`), `ArtifactMailMetadata`, `Contact`, `ContactNote`, `Company`, `AssignmentSuggestion`, `UserContext`, and `AuditEvent`.
- Encode the central workflow rule in the domain layer: a due activity cannot be resolved unless the user either closes the case, creates the next activity, sets a Wiedervorlage, or explicitly records that no follow-up is needed.
- Use PostgreSQL as the only system of record, with hand-written repositories and read models for today’s worklist, case detail, upload intake state, fallback search results, assignment history, and audit logs; do not introduce an ORM anywhere.
- Build the UI as a two-pane HTMX interface: left side for overdue/today activities, right side for the large intake drop zone; use server-rendered partials plus a small no-build JavaScript helper for real drag/drop, suggestion confirmation/rejection, and next-step forms.
- Make the current intake slice Outlook-only: accept manually dropped or selected `.msg` files, reject everything else with the explicit message `not supported in this mvp for now`, and write the same text to the uvicorn console.
- Parse Outlook `.msg` artifacts through an `ArtifactContentExtractor` port, persist raw envelope metadata in `ArtifactMailMetadata`, use the normalized subject for the first single-case proposal, and keep OCR/scanned-document handling for a later phase.
- Add a pre-UAT local-first mode behind env config: when `GOLDENAGE_LOCAL_FIRST_MODE=sqlite3` and `GOLDENAGE_SQLITE_PATH` are set, bootstrap SQLite automatically, replace the fixed demo user with first-user onboarding, and persist profile pictures locally.
- Add `GiselaClient.analyze_artifact(...) -> AssignmentSuggestion` for single-case proposal generation with reasons/confidence; in the first mail slice it uses deterministic fuzzy subject matching against known case titles, and acceptance immediately transitions into the “what next?” and Wiedervorlage form and creates the new activity in the same flow.
- Add `ElizabethanSearchClient.search_cases(...)` for the reject path; back it with bounded app-owned search tools such as party match, contact match, communication-signature match, and subject similarity; never allow model-authored SQL or unrestricted database access.
- Stage authentication, but not authorization: start with local/OIDC-style auth and DB-backed user/group membership, while enforcing access limits at repository/search-tool boundaries from the first release and masking hidden-result counts in fallback search responses.
- Deliver in milestones: M1 foundation + worklist + case detail + next-step enforcement; M2 manual Outlook `.msg` upload + drag/drop + subject suggestion + unsupported-file handling; M2.5 pre-UAT local-first SQLite onboarding + profile photo + first-user credentials; M3 reject flow + bounded AI search + permission masking + auditing + sender/contact hints in search; M4 hardening for OCR, mailbox ingestion, and HTTP extraction of `gisela`/`elizabethan`; M5 company pages + contact pages + contact notes + ERP/LLM refinement endpoints.

## Public Interfaces and Types

- Web routes: `/worklist`, `/cases/{id}`, `/artifacts/upload`, `/artifacts/{id}/suggestion/accept`, `/artifacts/{id}/suggestion/reject`, `/cases/search`, `/activities/{id}/resolve`.
- Application services: `GetTodayWorklist`, `GetCaseDetail`, `UploadArtifact`, `AnalyzeArtifact`, `AcceptAssignment`, `RejectAssignmentAndSearch`, `ScheduleNextStep`, `ResolveActivity`.
- Adapter contracts: `GiselaClient`, `ElizabethanSearchClient`, `ArtifactStore`, `ArtifactContentExtractor`, `AuthProvider`, `AuditSink`.

## Test Plan

- Unit tests for due-state calculation, today/overdue filtering, assignment state changes, and the “no completion without next state” rule.
- PostgreSQL integration tests for raw-SQL repositories, permission-filtered worklist/search queries, masked-result behavior, and audit-log writes.
- Web-flow tests for: open today’s list, inspect a case inline, upload an Outlook `.msg`, reject non-`.msg` uploads with the explicit MVP message, accept a suggestion, reject a suggestion and pick a fallback result, and resolve an activity only with a valid follow-up decision.
- Contract tests with deterministic stubs for `GiselaClient` and `ElizabethanSearchClient` so later service extraction does not change app behavior.
- Manual acceptance testing for the calm two-pane UI, drag/drop affordance, accessible status indicators, HTMX partial refresh behavior, and console logging for unsupported uploads.

## Assumptions and Defaults

- The first implementation is a web app, not a desktop client.
- MVP intake is manual Outlook `.msg` first; mailbox and other event sources are designed for, but not included in the initial delivery slice.
- Python follows the Google Python Style Guide and clean-architecture conventions.
- PostgreSQL remains the single source of truth and no ORM is introduced later as a shortcut.
- `gisela` and `elizabethan` stay in-repo initially behind ports/adapters and are extracted only after their contracts stabilize.
- Company/contact pages and contact notes are planned, but they arrive after the manual `.msg` intake slice; mail-derived sender domains and participants are groundwork, not canonical master data.
