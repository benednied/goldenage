from datetime import UTC, datetime
from uuid import UUID

from goldenage.adapters import agent_http
from goldenage.domain.models import Artifact, ArtifactMailMetadata, CaseFile, MailParticipant

NOW = datetime(2026, 4, 12, 10, 0, tzinfo=UTC)
ARTIFACT_ID = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
CASE_ID = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")


def test_http_gisela_posts_visible_context_and_maps_suggestion(monkeypatch) -> None:
    captured = {}

    def fake_post_json(url, payload, *, timeout_seconds):  # noqa: ANN001
        captured["url"] = url
        captured["payload"] = payload
        captured["timeout_seconds"] = timeout_seconds
        return {
            "artifact_id": str(ARTIFACT_ID),
            "suggested_case_id": str(CASE_ID),
            "summary_reason": "Subject match",
            "confidence": 0.88,
            "created_at": NOW.isoformat(),
        }

    monkeypatch.setattr(agent_http, "_post_json", fake_post_json)

    suggestion = agent_http.HttpGiselaClient(
        base_url="https://gisela.example.test/",
        timeout_seconds=2.5,
    ).analyze_artifact(
        _artifact(),
        _mail_metadata(),
        [_case_file()],
        NOW,
    )

    assert captured["url"] == "https://gisela.example.test/analyze-artifact"
    assert captured["timeout_seconds"] == 2.5
    assert captured["payload"]["artifact"]["id"] == str(ARTIFACT_ID)
    assert captured["payload"]["mail_metadata"]["sender_email"] == "sender@example.test"
    assert captured["payload"]["visible_cases"][0]["id"] == str(CASE_ID)
    assert suggestion.suggested_case_id == CASE_ID
    assert suggestion.summary_reason == "Subject match"


def test_http_elizabethan_posts_query_and_maps_results(monkeypatch) -> None:
    captured = {}

    def fake_post_json(url, payload, *, timeout_seconds):  # noqa: ANN001
        captured["url"] = url
        captured["payload"] = payload
        captured["timeout_seconds"] = timeout_seconds
        return {
            "results": [
                {
                    "case_id": str(CASE_ID),
                    "title": "Vendor renewal",
                    "company": "Vendor Example",
                    "last_activity_at": NOW.isoformat(),
                    "reason": "Party match",
                    "score": 0.91,
                }
            ]
        }

    monkeypatch.setattr(agent_http, "_post_json", fake_post_json)

    results = agent_http.HttpElizabethanSearchClient(
        base_url="https://elizabethan.example.test",
    ).search_cases(
        "vendor",
        _artifact(),
        _mail_metadata(),
        [_case_file()],
        NOW,
    )

    assert captured["url"] == "https://elizabethan.example.test/search-cases"
    assert captured["payload"]["query"] == "vendor"
    assert results[0].case_id == CASE_ID
    assert results[0].reason == "Party match"


def _artifact() -> Artifact:
    return Artifact(
        id=ARTIFACT_ID,
        file_name="message.msg",
        media_type="application/vnd.ms-outlook",
        size_bytes=12,
        content_text="Body",
        storage_key="artifact",
        uploaded_at=NOW,
        uploaded_by=None,
    )


def _mail_metadata() -> ArtifactMailMetadata:
    return ArtifactMailMetadata(
        artifact_id=ARTIFACT_ID,
        source_system="outlook_upload",
        message_format="outlook_msg",
        parse_status="parsed",
        external_message_id=None,
        rfc_message_id="<message@example.test>",
        source_account=None,
        source_mailbox=None,
        subject="Vendor renewal",
        sender_name="Sender",
        sender_email="sender@example.test",
        sender_domain="example.test",
        recipients=(MailParticipant(name="Receiver", email="receiver@example.test"),),
        sent_at=NOW,
        created_at=NOW,
    )


def _case_file() -> CaseFile:
    return CaseFile(
        id=CASE_ID,
        title="Vendor renewal",
        company="Vendor Example",
        primary_contact="Sender",
        status="open",
        last_activity_at=NOW,
    )
