"""Raw-SQL PostgreSQL adapters."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict
from datetime import datetime
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
    MailParticipant,
    UserContext,
)

try:
    import psycopg
    from psycopg.rows import dict_row
    from psycopg.types.json import Jsonb
except ModuleNotFoundError:  # pragma: no cover - exercised only without dependencies.
    psycopg = None
    dict_row = None
    Jsonb = None


class PostgresRepositoryError(RuntimeError):
    """Raised when PostgreSQL adapters cannot be used."""


class _PostgresRepositoryBase:
    """Base helper for raw-SQL repositories."""

    def __init__(self, dsn: str) -> None:
        if psycopg is None:
            raise PostgresRepositoryError("psycopg is not installed.")
        self._dsn = dsn

    def _connect(self):
        return psycopg.connect(self._dsn, row_factory=dict_row)

    def _case_visible(self, case_id: UUID, user: UserContext) -> bool:
        sql = """
            SELECT visible_group_id
            FROM case_file
            WHERE id = %(case_id)s
        """
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(sql, {"case_id": case_id})
            row = cursor.fetchone()
            if row is None:
                return False
            visible_group_id = row["visible_group_id"]
            return visible_group_id is None or visible_group_id in user.visible_group_ids


class PostgresCaseRepository(_PostgresRepositoryBase, CaseRepository):
    """Case repository backed by PostgreSQL."""

    def list_cases(self, user: UserContext) -> Sequence[CaseFile]:
        sql = """
            SELECT id, title, company, primary_contact, status, last_activity_at, visible_group_id
            FROM case_file
            WHERE visible_group_id IS NULL
               OR visible_group_id = ANY(%(group_ids)s::uuid[])
            ORDER BY last_activity_at DESC, title ASC
        """
        params = {"group_ids": list(user.visible_group_ids)}
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(sql, params)
            return tuple(_row_to_case_file(row) for row in cursor.fetchall())

    def get_case(self, case_id: UUID, user: UserContext) -> CaseFile | None:
        sql = """
            SELECT id, title, company, primary_contact, status, last_activity_at, visible_group_id
            FROM case_file
            WHERE id = %(case_id)s
              AND (visible_group_id IS NULL OR visible_group_id = ANY(%(group_ids)s::uuid[]))
        """
        params = {"case_id": case_id, "group_ids": list(user.visible_group_ids)}
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(sql, params)
            row = cursor.fetchone()
            return _row_to_case_file(row) if row else None

    def save_case(self, case_file: CaseFile) -> None:
        sql = """
            INSERT INTO case_file (
                id, title, company, primary_contact, status, last_activity_at, visible_group_id
            ) VALUES (
                %(id)s, %(title)s, %(company)s, %(primary_contact)s, %(status)s,
                %(last_activity_at)s, %(visible_group_id)s
            )
            ON CONFLICT (id) DO UPDATE SET
                title = EXCLUDED.title,
                company = EXCLUDED.company,
                primary_contact = EXCLUDED.primary_contact,
                status = EXCLUDED.status,
                last_activity_at = EXCLUDED.last_activity_at,
                visible_group_id = EXCLUDED.visible_group_id
        """
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(sql, asdict(case_file))
            connection.commit()


class PostgresActivityRepository(_PostgresRepositoryBase, ActivityRepository):
    """Activity repository backed by PostgreSQL."""

    def list_due_activities(self, user: UserContext, now: datetime) -> Sequence[Activity]:
        sql = """
            SELECT a.id, a.case_id, a.description, a.kind, a.due_at, a.created_at,
                   a.created_by, a.completed_at
            FROM activity a
            JOIN case_file c ON c.id = a.case_id
            WHERE a.completed_at IS NULL
              AND a.due_at <= %(cutoff)s
              AND (c.visible_group_id IS NULL OR c.visible_group_id = ANY(%(group_ids)s::uuid[]))
            ORDER BY a.due_at ASC, a.created_at ASC
        """
        params = {"cutoff": now, "group_ids": list(user.visible_group_ids)}
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(sql, params)
            return tuple(_row_to_activity(row) for row in cursor.fetchall())

    def list_case_activities(self, case_id: UUID, user: UserContext) -> Sequence[Activity]:
        if not self._case_visible(case_id, user):
            return ()
        sql = """
            SELECT id, case_id, description, kind, due_at, created_at, created_by, completed_at
            FROM activity
            WHERE case_id = %(case_id)s
            ORDER BY due_at ASC, created_at ASC
        """
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(sql, {"case_id": case_id})
            return tuple(_row_to_activity(row) for row in cursor.fetchall())

    def get_activity(self, activity_id: UUID, user: UserContext) -> Activity | None:
        sql = """
            SELECT a.id, a.case_id, a.description, a.kind, a.due_at, a.created_at,
                   a.created_by, a.completed_at, c.visible_group_id
            FROM activity a
            JOIN case_file c ON c.id = a.case_id
            WHERE a.id = %(activity_id)s
        """
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(sql, {"activity_id": activity_id})
            row = cursor.fetchone()
            if row is None:
                return None
            if row["visible_group_id"] is not None and row["visible_group_id"] not in user.visible_group_ids:
                return None
            return _row_to_activity(row)

    def save_activity(self, activity: Activity) -> None:
        sql = """
            INSERT INTO activity (
                id, case_id, description, kind, due_at, created_at, created_by, completed_at
            ) VALUES (
                %(id)s, %(case_id)s, %(description)s, %(kind)s, %(due_at)s,
                %(created_at)s, %(created_by)s, %(completed_at)s
            )
            ON CONFLICT (id) DO UPDATE SET
                description = EXCLUDED.description,
                kind = EXCLUDED.kind,
                due_at = EXCLUDED.due_at,
                created_at = EXCLUDED.created_at,
                created_by = EXCLUDED.created_by,
                completed_at = EXCLUDED.completed_at
        """
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(sql, asdict(activity))
            connection.commit()


class PostgresArtifactRepository(_PostgresRepositoryBase, ArtifactRepository):
    """Artifact and suggestion repository backed by PostgreSQL."""

    def save_artifact(self, artifact: Artifact) -> None:
        sql = """
            INSERT INTO artifact (
                id, file_name, media_type, size_bytes, content_text, storage_key,
                uploaded_at, uploaded_by, assigned_case_id
            ) VALUES (
                %(id)s, %(file_name)s, %(media_type)s, %(size_bytes)s, %(content_text)s,
                %(storage_key)s, %(uploaded_at)s, %(uploaded_by)s, %(assigned_case_id)s
            )
            ON CONFLICT (id) DO UPDATE SET
                file_name = EXCLUDED.file_name,
                media_type = EXCLUDED.media_type,
                size_bytes = EXCLUDED.size_bytes,
                content_text = EXCLUDED.content_text,
                storage_key = EXCLUDED.storage_key,
                uploaded_at = EXCLUDED.uploaded_at,
                uploaded_by = EXCLUDED.uploaded_by,
                assigned_case_id = EXCLUDED.assigned_case_id
        """
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(sql, asdict(artifact))
            connection.commit()

    def get_artifact(self, artifact_id: UUID, user: UserContext) -> Artifact | None:
        sql = """
            SELECT a.id, a.file_name, a.media_type, a.size_bytes, a.content_text, a.storage_key,
                   a.uploaded_at, a.uploaded_by, a.assigned_case_id, c.visible_group_id
            FROM artifact a
            LEFT JOIN case_file c ON c.id = a.assigned_case_id
            WHERE a.id = %(artifact_id)s
        """
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(sql, {"artifact_id": artifact_id})
            row = cursor.fetchone()
            if row is None:
                return None
            visible_group_id = row["visible_group_id"]
            if visible_group_id is not None and visible_group_id not in user.visible_group_ids:
                return None
            return _row_to_artifact(row)

    def list_case_artifacts(self, case_id: UUID, user: UserContext) -> Sequence[Artifact]:
        if not self._case_visible(case_id, user):
            return ()
        sql = """
            SELECT id, file_name, media_type, size_bytes, content_text, storage_key,
                   uploaded_at, uploaded_by, assigned_case_id
            FROM artifact
            WHERE assigned_case_id = %(case_id)s
            ORDER BY uploaded_at DESC
        """
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(sql, {"case_id": case_id})
            return tuple(_row_to_artifact(row) for row in cursor.fetchall())

    def save_suggestion(self, suggestion: AssignmentSuggestion) -> None:
        sql = """
            INSERT INTO assignment_suggestion (
                artifact_id, suggested_case_id, summary_reason, confidence, created_at
            ) VALUES (
                %(artifact_id)s, %(suggested_case_id)s, %(summary_reason)s, %(confidence)s, %(created_at)s
            )
            ON CONFLICT (artifact_id) DO UPDATE SET
                suggested_case_id = EXCLUDED.suggested_case_id,
                summary_reason = EXCLUDED.summary_reason,
                confidence = EXCLUDED.confidence,
                created_at = EXCLUDED.created_at
        """
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(sql, asdict(suggestion))
            connection.commit()

    def get_suggestion(self, artifact_id: UUID, user: UserContext) -> AssignmentSuggestion | None:
        if self.get_artifact(artifact_id, user) is None:
            return None
        sql = """
            SELECT artifact_id, suggested_case_id, summary_reason, confidence, created_at
            FROM assignment_suggestion
            WHERE artifact_id = %(artifact_id)s
        """
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(sql, {"artifact_id": artifact_id})
            row = cursor.fetchone()
            return _row_to_suggestion(row) if row else None

    def save_mail_metadata(self, metadata: ArtifactMailMetadata) -> None:
        sql = """
            INSERT INTO artifact_mail_metadata (
                artifact_id, source_kind, parse_status, subject, sender_name, sender_email,
                sender_domain, recipients_json, sent_at, created_at
            ) VALUES (
                %(artifact_id)s, %(source_kind)s, %(parse_status)s, %(subject)s, %(sender_name)s,
                %(sender_email)s, %(sender_domain)s, %(recipients_json)s, %(sent_at)s, %(created_at)s
            )
            ON CONFLICT (artifact_id) DO UPDATE SET
                source_kind = EXCLUDED.source_kind,
                parse_status = EXCLUDED.parse_status,
                subject = EXCLUDED.subject,
                sender_name = EXCLUDED.sender_name,
                sender_email = EXCLUDED.sender_email,
                sender_domain = EXCLUDED.sender_domain,
                recipients_json = EXCLUDED.recipients_json,
                sent_at = EXCLUDED.sent_at,
                created_at = EXCLUDED.created_at
        """
        payload = asdict(metadata)
        payload["recipients_json"] = Jsonb(payload.pop("recipients"))
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(sql, payload)
            connection.commit()

    def get_mail_metadata(self, artifact_id: UUID, user: UserContext) -> ArtifactMailMetadata | None:
        if self.get_artifact(artifact_id, user) is None:
            return None
        sql = """
            SELECT artifact_id, source_kind, parse_status, subject, sender_name, sender_email,
                   sender_domain, recipients_json, sent_at, created_at
            FROM artifact_mail_metadata
            WHERE artifact_id = %(artifact_id)s
        """
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(sql, {"artifact_id": artifact_id})
            row = cursor.fetchone()
            return _row_to_mail_metadata(row) if row else None


class PostgresAuditRepository(_PostgresRepositoryBase, AuditRepository):
    """Audit repository backed by PostgreSQL."""

    def save_event(self, event: AuditEvent) -> None:
        sql = """
            INSERT INTO audit_event (
                id, actor_user_id, event_type, subject_id, payload_json, created_at
            ) VALUES (
                %(id)s, %(actor_user_id)s, %(event_type)s, %(subject_id)s, %(payload_json)s, %(created_at)s
            )
        """
        payload = asdict(event)
        payload["payload_json"] = Jsonb(payload["payload_json"])
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(sql, payload)
            connection.commit()


def _row_to_case_file(row: dict[str, object]) -> CaseFile:
    return CaseFile(
        id=row["id"],
        title=row["title"],
        company=row["company"],
        primary_contact=row["primary_contact"],
        status=row["status"],
        last_activity_at=row["last_activity_at"],
        visible_group_id=row["visible_group_id"],
    )


def _row_to_activity(row: dict[str, object]) -> Activity:
    return Activity(
        id=row["id"],
        case_id=row["case_id"],
        description=row["description"],
        kind=row["kind"],
        due_at=row["due_at"],
        created_at=row["created_at"],
        created_by=row["created_by"],
        completed_at=row["completed_at"],
    )


def _row_to_artifact(row: dict[str, object]) -> Artifact:
    return Artifact(
        id=row["id"],
        file_name=row["file_name"],
        media_type=row["media_type"],
        size_bytes=row["size_bytes"],
        content_text=row["content_text"],
        storage_key=row["storage_key"],
        uploaded_at=row["uploaded_at"],
        uploaded_by=row["uploaded_by"],
        assigned_case_id=row["assigned_case_id"],
    )


def _row_to_suggestion(row: dict[str, object]) -> AssignmentSuggestion:
    return AssignmentSuggestion(
        artifact_id=row["artifact_id"],
        suggested_case_id=row["suggested_case_id"],
        summary_reason=row["summary_reason"],
        confidence=float(row["confidence"]),
        created_at=row["created_at"],
    )


def _row_to_mail_metadata(row: dict[str, object]) -> ArtifactMailMetadata:
    return ArtifactMailMetadata(
        artifact_id=row["artifact_id"],
        source_kind=row["source_kind"],
        parse_status=row["parse_status"],
        subject=row["subject"],
        sender_name=row["sender_name"],
        sender_email=row["sender_email"],
        sender_domain=row["sender_domain"],
        recipients=tuple(
            MailParticipant(
                name=participant.get("name"),
                email=participant.get("email"),
            )
            for participant in row["recipients_json"]
        ),
        sent_at=row["sent_at"],
        created_at=row["created_at"],
    )
