"""SQLite adapters for local-first GoldenAge mode."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Sequence
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from uuid import UUID

from goldenage.application.ports import (
    ActivityRepository,
    ArtifactRepository,
    AuditRepository,
    CaseRepository,
)
from goldenage.domain.models import (
    Activity,
    Artifact,
    ArtifactMailMetadata,
    AssignmentSuggestion,
    AuditEvent,
    CaseFile,
    LocalUserAccount,
    MailParticipant,
    UserContext,
)


class SQLiteRepositoryError(RuntimeError):
    """Raised when SQLite adapters cannot be used."""


class _SQLiteRepositoryBase:
    """Base helper for SQLite repositories."""

    def __init__(self, database_path: Path | str) -> None:
        self._database_path = Path(database_path)
        self._database_path.parent.mkdir(parents=True, exist_ok=True)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._database_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _case_visible(self, case_id: UUID, user: UserContext) -> bool:
        clause, params = _group_visibility_clause(user.visible_group_ids, "visible_group_id")
        sql = f"SELECT 1 FROM case_file WHERE id = ? AND {clause}"
        with self._connect() as connection:
            row = connection.execute(sql, (str(case_id), *params)).fetchone()
            return row is not None


class SQLiteCaseRepository(_SQLiteRepositoryBase, CaseRepository):
    """Case repository backed by SQLite."""

    def list_cases(self, user: UserContext) -> Sequence[CaseFile]:
        clause, params = _group_visibility_clause(user.visible_group_ids, "visible_group_id")
        sql = f"""
            SELECT id, title, company, primary_contact, status, last_activity_at, visible_group_id
            FROM case_file
            WHERE {clause}
            ORDER BY last_activity_at DESC, title ASC
        """
        with self._connect() as connection:
            rows = connection.execute(sql, params).fetchall()
        return tuple(_row_to_case_file(row) for row in rows)

    def get_case(self, case_id: UUID, user: UserContext) -> CaseFile | None:
        clause, params = _group_visibility_clause(user.visible_group_ids, "visible_group_id")
        sql = f"""
            SELECT id, title, company, primary_contact, status, last_activity_at, visible_group_id
            FROM case_file
            WHERE id = ? AND {clause}
        """
        with self._connect() as connection:
            row = connection.execute(sql, (str(case_id), *params)).fetchone()
        return _row_to_case_file(row) if row else None

    def save_case(self, case_file: CaseFile) -> None:
        sql = """
            INSERT INTO case_file (
                id, title, company, primary_contact, status, last_activity_at, visible_group_id
            ) VALUES (
                :id, :title, :company, :primary_contact, :status, :last_activity_at, :visible_group_id
            )
            ON CONFLICT(id) DO UPDATE SET
                title = excluded.title,
                company = excluded.company,
                primary_contact = excluded.primary_contact,
                status = excluded.status,
                last_activity_at = excluded.last_activity_at,
                visible_group_id = excluded.visible_group_id
        """
        payload = asdict(case_file)
        payload["id"] = str(case_file.id)
        payload["last_activity_at"] = _serialize_datetime(case_file.last_activity_at)
        payload["visible_group_id"] = (
            str(case_file.visible_group_id) if case_file.visible_group_id is not None else None
        )
        with self._connect() as connection:
            connection.execute(sql, payload)
            connection.commit()


class SQLiteActivityRepository(_SQLiteRepositoryBase, ActivityRepository):
    """Activity repository backed by SQLite."""

    def list_due_activities(self, user: UserContext, now: datetime) -> Sequence[Activity]:
        clause, params = _group_visibility_clause(user.visible_group_ids, "c.visible_group_id")
        sql = f"""
            SELECT a.id, a.case_id, a.description, a.kind, a.due_at, a.created_at,
                   a.created_by, a.completed_at
            FROM activity a
            JOIN case_file c ON c.id = a.case_id
            WHERE a.completed_at IS NULL
              AND a.due_at <= ?
              AND {clause}
            ORDER BY a.due_at ASC, a.created_at ASC
        """
        with self._connect() as connection:
            rows = connection.execute(sql, (_serialize_datetime(now), *params)).fetchall()
        return tuple(_row_to_activity(row) for row in rows)

    def list_case_activities(self, case_id: UUID, user: UserContext) -> Sequence[Activity]:
        if not self._case_visible(case_id, user):
            return ()
        sql = """
            SELECT id, case_id, description, kind, due_at, created_at, created_by, completed_at
            FROM activity
            WHERE case_id = ?
            ORDER BY due_at ASC, created_at ASC
        """
        with self._connect() as connection:
            rows = connection.execute(sql, (str(case_id),)).fetchall()
        return tuple(_row_to_activity(row) for row in rows)

    def get_activity(self, activity_id: UUID, user: UserContext) -> Activity | None:
        clause, params = _group_visibility_clause(user.visible_group_ids, "c.visible_group_id")
        sql = f"""
            SELECT a.id, a.case_id, a.description, a.kind, a.due_at, a.created_at,
                   a.created_by, a.completed_at
            FROM activity a
            JOIN case_file c ON c.id = a.case_id
            WHERE a.id = ? AND {clause}
        """
        with self._connect() as connection:
            row = connection.execute(sql, (str(activity_id), *params)).fetchone()
        return _row_to_activity(row) if row else None

    def save_activity(self, activity: Activity) -> None:
        sql = """
            INSERT INTO activity (
                id, case_id, description, kind, due_at, completed_at, created_at, created_by
            ) VALUES (
                :id, :case_id, :description, :kind, :due_at, :completed_at, :created_at, :created_by
            )
            ON CONFLICT(id) DO UPDATE SET
                case_id = excluded.case_id,
                description = excluded.description,
                kind = excluded.kind,
                due_at = excluded.due_at,
                completed_at = excluded.completed_at,
                created_at = excluded.created_at,
                created_by = excluded.created_by
        """
        payload = {
            "id": str(activity.id),
            "case_id": str(activity.case_id),
            "description": activity.description,
            "kind": activity.kind,
            "due_at": _serialize_datetime(activity.due_at),
            "completed_at": _serialize_datetime(activity.completed_at),
            "created_at": _serialize_datetime(activity.created_at),
            "created_by": str(activity.created_by) if activity.created_by is not None else None,
        }
        with self._connect() as connection:
            connection.execute(sql, payload)
            connection.commit()


class SQLiteArtifactRepository(_SQLiteRepositoryBase, ArtifactRepository):
    """Artifact repository backed by SQLite."""

    def save_artifact(self, artifact: Artifact) -> None:
        sql = """
            INSERT INTO artifact (
                id, file_name, media_type, size_bytes, content_text, storage_key,
                uploaded_at, uploaded_by, assigned_case_id
            ) VALUES (
                :id, :file_name, :media_type, :size_bytes, :content_text, :storage_key,
                :uploaded_at, :uploaded_by, :assigned_case_id
            )
            ON CONFLICT(id) DO UPDATE SET
                file_name = excluded.file_name,
                media_type = excluded.media_type,
                size_bytes = excluded.size_bytes,
                content_text = excluded.content_text,
                storage_key = excluded.storage_key,
                uploaded_at = excluded.uploaded_at,
                uploaded_by = excluded.uploaded_by,
                assigned_case_id = excluded.assigned_case_id
        """
        payload = {
            "id": str(artifact.id),
            "file_name": artifact.file_name,
            "media_type": artifact.media_type,
            "size_bytes": artifact.size_bytes,
            "content_text": artifact.content_text,
            "storage_key": artifact.storage_key,
            "uploaded_at": _serialize_datetime(artifact.uploaded_at),
            "uploaded_by": str(artifact.uploaded_by) if artifact.uploaded_by is not None else None,
            "assigned_case_id": (
                str(artifact.assigned_case_id) if artifact.assigned_case_id is not None else None
            ),
        }
        with self._connect() as connection:
            connection.execute(sql, payload)
            connection.commit()

    def get_artifact(self, artifact_id: UUID, user: UserContext) -> Artifact | None:
        clause, params = _group_visibility_clause(user.visible_group_ids, "c.visible_group_id")
        sql = f"""
            SELECT a.id, a.file_name, a.media_type, a.size_bytes, a.content_text, a.storage_key,
                   a.uploaded_at, a.uploaded_by, a.assigned_case_id, c.visible_group_id
            FROM artifact a
            LEFT JOIN case_file c ON c.id = a.assigned_case_id
            WHERE a.id = ?
              AND (a.assigned_case_id IS NULL OR {clause})
        """
        with self._connect() as connection:
            row = connection.execute(sql, (str(artifact_id), *params)).fetchone()
        return _row_to_artifact(row) if row else None

    def list_case_artifacts(self, case_id: UUID, user: UserContext) -> Sequence[Artifact]:
        if not self._case_visible(case_id, user):
            return ()
        sql = """
            SELECT id, file_name, media_type, size_bytes, content_text, storage_key,
                   uploaded_at, uploaded_by, assigned_case_id
            FROM artifact
            WHERE assigned_case_id = ?
            ORDER BY uploaded_at DESC
        """
        with self._connect() as connection:
            rows = connection.execute(sql, (str(case_id),)).fetchall()
        return tuple(_row_to_artifact(row) for row in rows)

    def save_suggestion(self, suggestion: AssignmentSuggestion) -> None:
        sql = """
            INSERT INTO assignment_suggestion (
                artifact_id, suggested_case_id, summary_reason, confidence, created_at
            ) VALUES (
                :artifact_id, :suggested_case_id, :summary_reason, :confidence, :created_at
            )
            ON CONFLICT(artifact_id) DO UPDATE SET
                suggested_case_id = excluded.suggested_case_id,
                summary_reason = excluded.summary_reason,
                confidence = excluded.confidence,
                created_at = excluded.created_at
        """
        payload = {
            "artifact_id": str(suggestion.artifact_id),
            "suggested_case_id": (
                str(suggestion.suggested_case_id)
                if suggestion.suggested_case_id is not None
                else None
            ),
            "summary_reason": suggestion.summary_reason,
            "confidence": suggestion.confidence,
            "created_at": _serialize_datetime(suggestion.created_at),
        }
        with self._connect() as connection:
            connection.execute(sql, payload)
            connection.commit()

    def get_suggestion(self, artifact_id: UUID, user: UserContext) -> AssignmentSuggestion | None:
        if self.get_artifact(artifact_id, user) is None:
            return None
        sql = """
            SELECT artifact_id, suggested_case_id, summary_reason, confidence, created_at
            FROM assignment_suggestion
            WHERE artifact_id = ?
        """
        with self._connect() as connection:
            row = connection.execute(sql, (str(artifact_id),)).fetchone()
        return _row_to_suggestion(row) if row else None

    def save_mail_metadata(self, metadata: ArtifactMailMetadata) -> None:
        sql = """
            INSERT INTO artifact_mail_metadata (
                artifact_id, source_kind, parse_status, subject, sender_name, sender_email,
                sender_domain, recipients_json, sent_at, created_at
            ) VALUES (
                :artifact_id, :source_kind, :parse_status, :subject, :sender_name, :sender_email,
                :sender_domain, :recipients_json, :sent_at, :created_at
            )
            ON CONFLICT(artifact_id) DO UPDATE SET
                source_kind = excluded.source_kind,
                parse_status = excluded.parse_status,
                subject = excluded.subject,
                sender_name = excluded.sender_name,
                sender_email = excluded.sender_email,
                sender_domain = excluded.sender_domain,
                recipients_json = excluded.recipients_json,
                sent_at = excluded.sent_at,
                created_at = excluded.created_at
        """
        payload = {
            "artifact_id": str(metadata.artifact_id),
            "source_kind": metadata.source_kind,
            "parse_status": metadata.parse_status,
            "subject": metadata.subject,
            "sender_name": metadata.sender_name,
            "sender_email": metadata.sender_email,
            "sender_domain": metadata.sender_domain,
            "recipients_json": json.dumps(
                [asdict(recipient) for recipient in metadata.recipients]
            ),
            "sent_at": _serialize_datetime(metadata.sent_at),
            "created_at": _serialize_datetime(metadata.created_at),
        }
        with self._connect() as connection:
            connection.execute(sql, payload)
            connection.commit()

    def get_mail_metadata(self, artifact_id: UUID, user: UserContext) -> ArtifactMailMetadata | None:
        if self.get_artifact(artifact_id, user) is None:
            return None
        sql = """
            SELECT artifact_id, source_kind, parse_status, subject, sender_name, sender_email,
                   sender_domain, recipients_json, sent_at, created_at
            FROM artifact_mail_metadata
            WHERE artifact_id = ?
        """
        with self._connect() as connection:
            row = connection.execute(sql, (str(artifact_id),)).fetchone()
        return _row_to_mail_metadata(row) if row else None


class SQLiteAuditRepository(_SQLiteRepositoryBase, AuditRepository):
    """Audit repository backed by SQLite."""

    def save_event(self, event: AuditEvent) -> None:
        sql = """
            INSERT INTO audit_event (
                id, actor_user_id, event_type, subject_id, payload_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
        """
        with self._connect() as connection:
            connection.execute(
                sql,
                (
                    str(event.id),
                    str(event.actor_user_id) if event.actor_user_id is not None else None,
                    event.event_type,
                    str(event.subject_id),
                    json.dumps(event.payload_json),
                    _serialize_datetime(event.created_at),
                ),
            )
            connection.commit()


class SQLiteLocalUserRepository(_SQLiteRepositoryBase):
    """SQLite persistence for the local-first onboarding account."""

    def get_first_user(self) -> LocalUserAccount | None:
        sql = """
            SELECT id, email, display_name, password_hash, profile_image_path
            FROM app_user
            ORDER BY rowid ASC
            LIMIT 1
        """
        with self._connect() as connection:
            row = connection.execute(sql).fetchone()
        return _row_to_local_user(row) if row else None

    def create_user(
        self,
        *,
        account_id: UUID,
        email: str,
        display_name: str,
        password_hash: str,
        profile_image_path: str | None,
    ) -> LocalUserAccount:
        sql = """
            INSERT INTO app_user (id, email, display_name, password_hash, profile_image_path)
            VALUES (?, ?, ?, ?, ?)
        """
        with self._connect() as connection:
            existing = connection.execute("SELECT COUNT(*) FROM app_user").fetchone()[0]
            if existing:
                raise SQLiteRepositoryError("A local-first user already exists.")
            connection.execute(
                sql,
                (
                    str(account_id),
                    email,
                    display_name,
                    password_hash,
                    profile_image_path,
                ),
            )
            connection.commit()
        return LocalUserAccount(
            id=account_id,
            email=email,
            display_name=display_name,
            password_hash=password_hash,
            profile_image_path=profile_image_path,
        )


def _group_visibility_clause(
    group_ids: frozenset[UUID],
    column_name: str,
) -> tuple[str, tuple[str, ...]]:
    if not group_ids:
        return f"{column_name} IS NULL", ()
    params = tuple(str(group_id) for group_id in group_ids)
    placeholders = ", ".join("?" for _ in params)
    return f"({column_name} IS NULL OR {column_name} IN ({placeholders}))", params


def _serialize_datetime(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat()


def _deserialize_datetime(raw_value: str | None) -> datetime | None:
    if raw_value is None:
        return None
    return datetime.fromisoformat(raw_value)


def _row_to_case_file(row: sqlite3.Row) -> CaseFile:
    return CaseFile(
        id=UUID(row["id"]),
        title=row["title"],
        company=row["company"],
        primary_contact=row["primary_contact"],
        status=row["status"],
        last_activity_at=_deserialize_datetime(row["last_activity_at"]),
        visible_group_id=UUID(row["visible_group_id"]) if row["visible_group_id"] else None,
    )


def _row_to_activity(row: sqlite3.Row) -> Activity:
    return Activity(
        id=UUID(row["id"]),
        case_id=UUID(row["case_id"]),
        description=row["description"],
        kind=row["kind"],
        due_at=_deserialize_datetime(row["due_at"]),
        created_at=_deserialize_datetime(row["created_at"]),
        created_by=UUID(row["created_by"]) if row["created_by"] else None,
        completed_at=_deserialize_datetime(row["completed_at"]),
    )


def _row_to_artifact(row: sqlite3.Row) -> Artifact:
    return Artifact(
        id=UUID(row["id"]),
        file_name=row["file_name"],
        media_type=row["media_type"],
        size_bytes=row["size_bytes"],
        content_text=row["content_text"],
        storage_key=row["storage_key"],
        uploaded_at=_deserialize_datetime(row["uploaded_at"]),
        uploaded_by=UUID(row["uploaded_by"]) if row["uploaded_by"] else None,
        assigned_case_id=UUID(row["assigned_case_id"]) if row["assigned_case_id"] else None,
    )


def _row_to_suggestion(row: sqlite3.Row) -> AssignmentSuggestion:
    return AssignmentSuggestion(
        artifact_id=UUID(row["artifact_id"]),
        suggested_case_id=UUID(row["suggested_case_id"]) if row["suggested_case_id"] else None,
        summary_reason=row["summary_reason"],
        confidence=float(row["confidence"]),
        created_at=_deserialize_datetime(row["created_at"]),
    )


def _row_to_mail_metadata(row: sqlite3.Row) -> ArtifactMailMetadata:
    recipients = json.loads(row["recipients_json"])
    return ArtifactMailMetadata(
        artifact_id=UUID(row["artifact_id"]),
        source_kind=row["source_kind"],
        parse_status=row["parse_status"],
        subject=row["subject"],
        sender_name=row["sender_name"],
        sender_email=row["sender_email"],
        sender_domain=row["sender_domain"],
        recipients=tuple(
            MailParticipant(
                name=recipient.get("name"),
                email=recipient.get("email"),
            )
            for recipient in recipients
        ),
        sent_at=_deserialize_datetime(row["sent_at"]),
        created_at=_deserialize_datetime(row["created_at"]),
    )


def _row_to_local_user(row: sqlite3.Row) -> LocalUserAccount:
    return LocalUserAccount(
        id=UUID(row["id"]),
        email=row["email"],
        display_name=row["display_name"],
        password_hash=row["password_hash"],
        profile_image_path=row["profile_image_path"],
    )
