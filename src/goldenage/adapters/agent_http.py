"""HTTP adapters for extracted agent and search services."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import asdict
from datetime import datetime
from typing import Any, cast
from urllib import error, request
from uuid import UUID

from goldenage.application.ports import ElizabethanSearchClient, GiselaClient
from goldenage.domain.models import (
    Artifact,
    ArtifactMailMetadata,
    AssignmentSuggestion,
    CaseFile,
    SearchResult,
)


class AgentHttpError(RuntimeError):
    """Raised when an extracted agent service cannot satisfy a request."""


class HttpGiselaClient(GiselaClient):
    """Call an extracted Gisela assignment service over HTTP."""

    def __init__(self, *, base_url: str, timeout_seconds: float = 10.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds

    def analyze_artifact(
        self,
        artifact: Artifact,
        mail_metadata: ArtifactMailMetadata | None,
        visible_cases: Sequence[CaseFile],
        now: datetime,
    ) -> AssignmentSuggestion:
        payload = _common_payload(
            artifact=artifact,
            mail_metadata=mail_metadata,
            visible_cases=visible_cases,
            now=now,
        )
        data = _post_json(
            f"{self._base_url}/analyze-artifact",
            payload,
            timeout_seconds=self._timeout_seconds,
        )
        return AssignmentSuggestion(
            artifact_id=_uuid(data.get("artifact_id"), default=artifact.id),
            suggested_case_id=_optional_uuid(data.get("suggested_case_id")),
            summary_reason=str(data.get("summary_reason") or ""),
            confidence=float(data.get("confidence") or 0.0),
            created_at=_datetime(data.get("created_at"), default=now),
        )


class HttpElizabethanSearchClient(ElizabethanSearchClient):
    """Call an extracted Elizabethan bounded-search service over HTTP."""

    def __init__(self, *, base_url: str, timeout_seconds: float = 10.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds

    def search_cases(
        self,
        query: str,
        artifact: Artifact,
        mail_metadata: ArtifactMailMetadata | None,
        visible_cases: Sequence[CaseFile],
        now: datetime,
    ) -> Sequence[SearchResult]:
        payload = _common_payload(
            artifact=artifact,
            mail_metadata=mail_metadata,
            visible_cases=visible_cases,
            now=now,
        )
        payload["query"] = query
        data = _post_json(
            f"{self._base_url}/search-cases",
            payload,
            timeout_seconds=self._timeout_seconds,
        )
        raw_results = data.get("results", data if isinstance(data, list) else ())
        if not isinstance(raw_results, list):
            raise AgentHttpError("Elizabethan response must contain a results list.")
        return tuple(_search_result(raw) for raw in raw_results)


def _common_payload(
    *,
    artifact: Artifact,
    mail_metadata: ArtifactMailMetadata | None,
    visible_cases: Sequence[CaseFile],
    now: datetime,
) -> dict[str, Any]:
    return {
        "artifact": _jsonable(asdict(artifact)),
        "mail_metadata": _jsonable(asdict(mail_metadata)) if mail_metadata is not None else None,
        "visible_cases": [_jsonable(asdict(case_file)) for case_file in visible_cases],
        "now": now.isoformat(),
    }


def _post_json(url: str, payload: dict[str, Any], *, timeout_seconds: float) -> Any:
    body = json.dumps(payload).encode("utf-8")
    http_request = request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    try:
        with request.urlopen(http_request, timeout=timeout_seconds) as response:
            response_body = response.read()
    except error.HTTPError as exc:
        details = exc.read().decode("utf-8", errors="replace")
        raise AgentHttpError(f"Agent service rejected request: HTTP {exc.code}: {details}") from exc
    except error.URLError as exc:
        raise AgentHttpError(f"Agent service is unavailable: {exc.reason}") from exc
    try:
        return json.loads(response_body.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise AgentHttpError("Agent service returned invalid JSON.") from exc


def _jsonable(value: Any) -> Any:
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, tuple):
        return [_jsonable(item) for item in value]
    if isinstance(value, frozenset):
        return [str(item) for item in value]
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    return value


def _search_result(raw: object) -> SearchResult:
    if not isinstance(raw, dict):
        raise AgentHttpError("Elizabethan result entries must be JSON objects.")
    result = cast(dict[str, Any], raw)
    return SearchResult(
        case_id=_uuid(result.get("case_id")),
        title=str(result.get("title") or ""),
        company=str(result["company"]) if result.get("company") is not None else None,
        last_activity_at=_datetime(result.get("last_activity_at")),
        reason=str(result.get("reason") or ""),
        score=float(result.get("score") or 0.0),
    )


def _optional_uuid(value: object) -> UUID | None:
    if value in {None, ""}:
        return None
    return _uuid(value)


def _uuid(value: object, *, default: UUID | None = None) -> UUID:
    if value in {None, ""} and default is not None:
        return default
    if isinstance(value, UUID):
        return value
    if isinstance(value, str):
        return UUID(value)
    raise AgentHttpError("Expected UUID string in agent response.")


def _datetime(value: object, *, default: datetime | None = None) -> datetime:
    if value in {None, ""} and default is not None:
        return default
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        return datetime.fromisoformat(value)
    raise AgentHttpError("Expected ISO datetime string in agent response.")
