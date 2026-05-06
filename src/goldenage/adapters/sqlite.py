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
    MailCandidate,
    MailConversation,
    MailMessage,
    MailParticipant,
    MailSelector,
    MailSourceSystem,
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

    def list_unassigned_artifacts(
        self,
        user: UserContext,
        *,
        limit: int,
    ) -> Sequence[Artifact]:
        sql = """
            SELECT id, file_name, media_type, size_bytes, content_text, storage_key,
                   uploaded_at, uploaded_by, assigned_case_id
            FROM artifact
            WHERE assigned_case_id IS NULL
              AND (uploaded_by IS NULL OR uploaded_by = ?)
            ORDER BY uploaded_at DESC
            LIMIT ?
        """
        with self._connect() as connection:
            rows = connection.execute(sql, (str(user.id), limit)).fetchall()
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
                artifact_id, source_kind, source_system, message_format, parse_status,
                external_message_id, rfc_message_id, source_account, source_mailbox, subject,
                sender_name, sender_email, sender_domain, recipients_json, sent_at, created_at
            ) VALUES (
                :artifact_id, :source_kind, :source_system, :message_format, :parse_status,
                :external_message_id, :rfc_message_id, :source_account, :source_mailbox, :subject,
                :sender_name, :sender_email, :sender_domain, :recipients_json, :sent_at, :created_at
            )
            ON CONFLICT(artifact_id) DO UPDATE SET
                source_kind = excluded.source_kind,
                source_system = excluded.source_system,
                message_format = excluded.message_format,
                parse_status = excluded.parse_status,
                external_message_id = excluded.external_message_id,
                rfc_message_id = excluded.rfc_message_id,
                source_account = excluded.source_account,
                source_mailbox = excluded.source_mailbox,
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
            "source_kind": metadata.message_format,
            "source_system": metadata.source_system,
            "message_format": metadata.message_format,
            "parse_status": metadata.parse_status,
            "external_message_id": metadata.external_message_id,
            "rfc_message_id": metadata.rfc_message_id,
            "source_account": metadata.source_account,
            "source_mailbox": metadata.source_mailbox,
            "subject": metadata.subject,
            "sender_name": metadata.sender_name,
            "sender_email": metadata.sender_email,
            "sender_domain": metadata.sender_domain,
            "recipients_json": json.dumps([asdict(recipient) for recipient in metadata.recipients]),
            "sent_at": _serialize_datetime(metadata.sent_at),
            "created_at": _serialize_datetime(metadata.created_at),
        }
        with self._connect() as connection:
            connection.execute(sql, payload)
            connection.commit()

    def get_mail_metadata(
        self, artifact_id: UUID, user: UserContext
    ) -> ArtifactMailMetadata | None:
        if self.get_artifact(artifact_id, user) is None:
            return None
        sql = """
            SELECT artifact_id, source_system, message_format, parse_status, external_message_id,
                   rfc_message_id, source_account, source_mailbox, subject, sender_name,
                   sender_email, sender_domain, recipients_json, sent_at, created_at
            FROM artifact_mail_metadata
            WHERE artifact_id = ?
        """
        with self._connect() as connection:
            row = connection.execute(sql, (str(artifact_id),)).fetchone()
        return _row_to_mail_metadata(row) if row else None

    def save_mail_conversation(self, conversation: MailConversation) -> None:
        sql = """
            INSERT INTO mail_conversation (
                id, source_kind, external_conversation_id, normalized_subject, latest_subject,
                latest_message_at, participants_json, message_count, latest_artifact_id,
                assigned_case_id, created_at, updated_at
            ) VALUES (
                :id, :source_kind, :external_conversation_id, :normalized_subject, :latest_subject,
                :latest_message_at, :participants_json, :message_count, :latest_artifact_id,
                :assigned_case_id, :created_at, :updated_at
            )
            ON CONFLICT(id) DO UPDATE SET
                source_kind = excluded.source_kind,
                external_conversation_id = excluded.external_conversation_id,
                normalized_subject = excluded.normalized_subject,
                latest_subject = excluded.latest_subject,
                latest_message_at = excluded.latest_message_at,
                participants_json = excluded.participants_json,
                message_count = excluded.message_count,
                latest_artifact_id = excluded.latest_artifact_id,
                assigned_case_id = excluded.assigned_case_id,
                created_at = excluded.created_at,
                updated_at = excluded.updated_at
        """
        payload = {
            "id": str(conversation.id),
            "source_kind": conversation.source_kind,
            "external_conversation_id": conversation.external_conversation_id,
            "normalized_subject": conversation.normalized_subject,
            "latest_subject": conversation.latest_subject,
            "latest_message_at": _serialize_datetime(conversation.latest_message_at),
            "participants_json": json.dumps([asdict(p) for p in conversation.participants]),
            "message_count": conversation.message_count,
            "latest_artifact_id": str(conversation.latest_artifact_id),
            "assigned_case_id": (
                str(conversation.assigned_case_id)
                if conversation.assigned_case_id is not None
                else None
            ),
            "created_at": _serialize_datetime(conversation.created_at),
            "updated_at": _serialize_datetime(conversation.updated_at),
        }
        with self._connect() as connection:
            connection.execute(sql, payload)
            connection.commit()

    def get_mail_conversation(
        self,
        conversation_id: UUID,
        user: UserContext,
    ) -> MailConversation | None:
        clause, params = _group_visibility_clause(user.visible_group_ids, "c.visible_group_id")
        sql = f"""
            SELECT mc.*
            FROM mail_conversation mc
            LEFT JOIN artifact a ON a.id = mc.latest_artifact_id
            LEFT JOIN case_file c ON c.id = COALESCE(mc.assigned_case_id, a.assigned_case_id)
            WHERE mc.id = ?
              AND (c.id IS NULL OR {clause})
        """
        with self._connect() as connection:
            row = connection.execute(sql, (str(conversation_id), *params)).fetchone()
        return _row_to_mail_conversation(row) if row else None

    def list_recent_mail_conversations(
        self,
        user: UserContext,
        *,
        limit: int = 10,
    ) -> Sequence[MailConversation]:
        clause, params = _group_visibility_clause(user.visible_group_ids, "c.visible_group_id")
        sql = f"""
            SELECT mc.*
            FROM mail_conversation mc
            LEFT JOIN artifact a ON a.id = mc.latest_artifact_id
            LEFT JOIN case_file c ON c.id = COALESCE(mc.assigned_case_id, a.assigned_case_id)
            WHERE c.id IS NULL OR {clause}
            ORDER BY mc.latest_message_at DESC
            LIMIT ?
        """
        with self._connect() as connection:
            rows = connection.execute(sql, (*params, limit)).fetchall()
        return tuple(_row_to_mail_conversation(row) for row in rows)

    def save_mail_message(self, message: MailMessage) -> None:
        sql = """
            INSERT INTO mail_message (
                artifact_id, conversation_id, source_kind, source_account_id, source_folder_id,
                source_message_id, source_conversation_id, internet_message_id,
                dedupe_fingerprint, direction, received_at, created_at
            ) VALUES (
                :artifact_id, :conversation_id, :source_kind, :source_account_id,
                :source_folder_id, :source_message_id, :source_conversation_id,
                :internet_message_id, :dedupe_fingerprint, :direction, :received_at, :created_at
            )
            ON CONFLICT(artifact_id) DO UPDATE SET
                conversation_id = excluded.conversation_id,
                source_kind = excluded.source_kind,
                source_account_id = excluded.source_account_id,
                source_folder_id = excluded.source_folder_id,
                source_message_id = excluded.source_message_id,
                source_conversation_id = excluded.source_conversation_id,
                internet_message_id = excluded.internet_message_id,
                dedupe_fingerprint = excluded.dedupe_fingerprint,
                direction = excluded.direction,
                received_at = excluded.received_at,
                created_at = excluded.created_at
        """
        payload = {
            "artifact_id": str(message.artifact_id),
            "conversation_id": str(message.conversation_id),
            "source_kind": message.source_kind,
            "source_account_id": message.source_account_id,
            "source_folder_id": message.source_folder_id,
            "source_message_id": message.source_message_id,
            "source_conversation_id": message.source_conversation_id,
            "internet_message_id": message.internet_message_id,
            "dedupe_fingerprint": message.dedupe_fingerprint,
            "direction": message.direction,
            "received_at": _serialize_datetime(message.received_at),
            "created_at": _serialize_datetime(message.created_at),
        }
        with self._connect() as connection:
            connection.execute(sql, payload)
            connection.commit()

    def get_mail_message(self, artifact_id: UUID, user: UserContext) -> MailMessage | None:
        if self.get_artifact(artifact_id, user) is None:
            return None
        sql = """
            SELECT artifact_id, conversation_id, source_kind, source_account_id, source_folder_id,
                   source_message_id, source_conversation_id, internet_message_id,
                   dedupe_fingerprint, direction, received_at, created_at
            FROM mail_message
            WHERE artifact_id = ?
        """
        with self._connect() as connection:
            row = connection.execute(sql, (str(artifact_id),)).fetchone()
        return _row_to_mail_message(row) if row else None

    def find_mail_message_by_source(
        self,
        *,
        source_kind: str,
        source_account_id: str | None,
        source_folder_id: str | None,
        source_message_id: str | None,
        internet_message_id: str | None,
        dedupe_fingerprint: str,
        user: UserContext,
    ) -> MailMessage | None:
        clause, params = _group_visibility_clause(user.visible_group_ids, "c.visible_group_id")
        sql = f"""
            SELECT mm.artifact_id, mm.conversation_id, mm.source_kind, mm.source_account_id,
                   mm.source_folder_id, mm.source_message_id, mm.source_conversation_id,
                   mm.internet_message_id, mm.dedupe_fingerprint, mm.direction, mm.received_at,
                   mm.created_at
            FROM mail_message mm
            JOIN artifact a ON a.id = mm.artifact_id
            LEFT JOIN case_file c ON c.id = a.assigned_case_id
            WHERE mm.source_kind = ?
              AND (
                    (? IS NOT NULL AND ? IS NOT NULL AND ? IS NOT NULL
                     AND mm.source_account_id = ? AND mm.source_folder_id = ?
                     AND mm.source_message_id = ?)
                 OR (? IS NOT NULL AND mm.internet_message_id = ?)
                 OR mm.dedupe_fingerprint = ?
              )
              AND (a.assigned_case_id IS NULL OR {clause})
            LIMIT 1
        """
        query_params = (
            source_kind,
            source_account_id,
            source_folder_id,
            source_message_id,
            source_account_id,
            source_folder_id,
            source_message_id,
            internet_message_id,
            internet_message_id,
            dedupe_fingerprint,
            *params,
        )
        with self._connect() as connection:
            row = connection.execute(sql, query_params).fetchone()
        return _row_to_mail_message(row) if row else None

    def list_conversation_artifacts(
        self,
        conversation_id: UUID,
        user: UserContext,
    ) -> Sequence[Artifact]:
        clause, params = _group_visibility_clause(user.visible_group_ids, "c.visible_group_id")
        sql = f"""
            SELECT a.id, a.file_name, a.media_type, a.size_bytes, a.content_text, a.storage_key,
                   a.uploaded_at, a.uploaded_by, a.assigned_case_id
            FROM mail_message mm
            JOIN artifact a ON a.id = mm.artifact_id
            LEFT JOIN case_file c ON c.id = a.assigned_case_id
            WHERE mm.conversation_id = ?
              AND (a.assigned_case_id IS NULL OR {clause})
            ORDER BY COALESCE(mm.received_at, a.uploaded_at) DESC, a.uploaded_at DESC
        """
        with self._connect() as connection:
            rows = connection.execute(sql, (str(conversation_id), *params)).fetchall()
        return tuple(_row_to_artifact(row) for row in rows)


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

    def get_user_by_email(self, email: str) -> LocalUserAccount | None:
        """Return the local account matching the normalized email address."""
        sql = """
            SELECT id, email, display_name, password_hash, profile_image_path
            FROM app_user
            WHERE lower(email) = lower(?)
            LIMIT 1
        """
        with self._connect() as connection:
            row = connection.execute(sql, (email.strip(),)).fetchone()
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

    def update_password_hash(self, *, account_id: UUID, password_hash: str) -> None:
        """Replace the stored password hash for a local account."""
        sql = """
            UPDATE app_user
            SET password_hash = ?
            WHERE id = ?
        """
        with self._connect() as connection:
            cursor = connection.execute(sql, (password_hash, str(account_id)))
            if cursor.rowcount == 0:
                raise SQLiteRepositoryError("Local user account not found.")
            connection.commit()


class SQLiteMailImportRepository(_SQLiteRepositoryBase):
    """SQLite persistence for mail selectors, review queue, and dedupe state."""

    def upsert_source(
        self,
        *,
        user: UserContext,
        source_system: MailSourceSystem,
        now: datetime,
    ) -> None:
        sql = """
            INSERT INTO mail_import_source (user_id, source_system, enabled_at, updated_at)
            VALUES (:user_id, :source_system, :enabled_at, :updated_at)
            ON CONFLICT(user_id, source_system) DO UPDATE SET
                updated_at = excluded.updated_at
        """
        payload = {
            "user_id": str(user.id),
            "source_system": source_system,
            "enabled_at": _serialize_datetime(now),
            "updated_at": _serialize_datetime(now),
        }
        with self._connect() as connection:
            connection.execute(sql, payload)
            connection.commit()

    def save_selector(
        self,
        *,
        user: UserContext,
        source_system: MailSourceSystem,
        selector: MailSelector,
        now: datetime,
    ) -> None:
        sql = """
            INSERT INTO mail_import_selector (
                user_id, source_system, account_name, mailbox_name, unread_only, sender_filter,
                subject_filter, sent_after, result_limit, updated_at
            ) VALUES (
                :user_id, :source_system, :account_name, :mailbox_name, :unread_only, :sender_filter,
                :subject_filter, :sent_after, :result_limit, :updated_at
            )
            ON CONFLICT(user_id, source_system) DO UPDATE SET
                account_name = excluded.account_name,
                mailbox_name = excluded.mailbox_name,
                unread_only = excluded.unread_only,
                sender_filter = excluded.sender_filter,
                subject_filter = excluded.subject_filter,
                sent_after = excluded.sent_after,
                result_limit = excluded.result_limit,
                updated_at = excluded.updated_at
        """
        payload = {
            "user_id": str(user.id),
            "source_system": source_system,
            "account_name": selector.account_name,
            "mailbox_name": selector.mailbox_name,
            "unread_only": int(selector.unread_only),
            "sender_filter": selector.sender_filter,
            "subject_filter": selector.subject_filter,
            "sent_after": _serialize_datetime(selector.sent_after),
            "result_limit": selector.result_limit,
            "updated_at": _serialize_datetime(now),
        }
        with self._connect() as connection:
            connection.execute(sql, payload)
            connection.commit()

    def get_selector(
        self,
        *,
        user: UserContext,
        source_system: MailSourceSystem,
    ) -> MailSelector | None:
        sql = """
            SELECT account_name, mailbox_name, unread_only, sender_filter, subject_filter,
                   sent_after, result_limit
            FROM mail_import_selector
            WHERE user_id = ? AND source_system = ?
        """
        with self._connect() as connection:
            row = connection.execute(sql, (str(user.id), source_system)).fetchone()
        return _row_to_mail_selector(row) if row else None

    def replace_review_candidates(
        self,
        *,
        user: UserContext,
        source_system: MailSourceSystem,
        candidates: Sequence[MailCandidate],
        now: datetime,
    ) -> None:
        delete_sql = (
            "DELETE FROM mail_import_review_candidate WHERE user_id = ? AND source_system = ?"
        )
        insert_sql = """
            INSERT INTO mail_import_review_candidate (
                user_id, source_system, candidate_id, account_name, mailbox_name, subject,
                sender_name, sender_email, sent_at, preview_text, unread, rfc_message_id,
                created_at
            ) VALUES (
                :user_id, :source_system, :candidate_id, :account_name, :mailbox_name, :subject,
                :sender_name, :sender_email, :sent_at, :preview_text, :unread, :rfc_message_id,
                :created_at
            )
        """
        with self._connect() as connection:
            connection.execute(delete_sql, (str(user.id), source_system))
            for candidate in candidates:
                connection.execute(
                    insert_sql,
                    {
                        "user_id": str(user.id),
                        "source_system": source_system,
                        "candidate_id": candidate.candidate_id,
                        "account_name": candidate.account_name,
                        "mailbox_name": candidate.mailbox_name,
                        "subject": candidate.subject,
                        "sender_name": candidate.sender_name,
                        "sender_email": candidate.sender_email,
                        "sent_at": _serialize_datetime(candidate.sent_at),
                        "preview_text": candidate.preview_text,
                        "unread": int(candidate.unread),
                        "rfc_message_id": candidate.rfc_message_id,
                        "created_at": _serialize_datetime(now),
                    },
                )
            connection.commit()

    def list_review_candidates(
        self,
        *,
        user: UserContext,
        source_system: MailSourceSystem,
    ) -> Sequence[MailCandidate]:
        sql = """
            SELECT candidate_id, source_system, account_name, mailbox_name, subject,
                   sender_name, sender_email, sent_at, preview_text, unread, rfc_message_id
            FROM mail_import_review_candidate
            WHERE user_id = ? AND source_system = ?
            ORDER BY sent_at DESC, candidate_id ASC
        """
        with self._connect() as connection:
            rows = connection.execute(sql, (str(user.id), source_system)).fetchall()
        return tuple(_row_to_mail_candidate(row) for row in rows)

    def get_review_candidate(
        self,
        *,
        user: UserContext,
        source_system: MailSourceSystem,
        candidate_id: str,
    ) -> MailCandidate | None:
        sql = """
            SELECT candidate_id, source_system, account_name, mailbox_name, subject,
                   sender_name, sender_email, sent_at, preview_text, unread, rfc_message_id
            FROM mail_import_review_candidate
            WHERE user_id = ? AND source_system = ? AND candidate_id = ?
        """
        with self._connect() as connection:
            row = connection.execute(sql, (str(user.id), source_system, candidate_id)).fetchone()
        return _row_to_mail_candidate(row) if row else None

    def discard_review_candidate(
        self,
        *,
        user: UserContext,
        source_system: MailSourceSystem,
        candidate_id: str,
    ) -> None:
        sql = """
            DELETE FROM mail_import_review_candidate
            WHERE user_id = ? AND source_system = ? AND candidate_id = ?
        """
        with self._connect() as connection:
            connection.execute(sql, (str(user.id), source_system, candidate_id))
            connection.commit()

    def list_imported_message_ids(
        self,
        *,
        user: UserContext,
        source_system: MailSourceSystem,
    ) -> frozenset[str]:
        sql = """
            SELECT external_message_id, rfc_message_id
            FROM imported_mail_message
            WHERE user_id = ? AND source_system = ?
        """
        with self._connect() as connection:
            rows = connection.execute(sql, (str(user.id), source_system)).fetchall()
        ids = set()
        for row in rows:
            ids.add(str(row["external_message_id"]))
            if row["rfc_message_id"]:
                ids.add(str(row["rfc_message_id"]))
        return frozenset(ids)

    def save_imported_message(
        self,
        *,
        user: UserContext,
        source_system: MailSourceSystem,
        external_message_id: str,
        rfc_message_id: str | None,
        artifact_id: UUID,
        now: datetime,
    ) -> None:
        sql = """
            INSERT INTO imported_mail_message (
                user_id, source_system, external_message_id, rfc_message_id, artifact_id, imported_at
            ) VALUES (?, ?, ?, ?, ?, ?)
        """
        with self._connect() as connection:
            connection.execute(
                sql,
                (
                    str(user.id),
                    source_system,
                    external_message_id,
                    rfc_message_id,
                    str(artifact_id),
                    _serialize_datetime(now),
                ),
            )
            connection.commit()


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
        source_system=row["source_system"],
        message_format=row["message_format"],
        parse_status=row["parse_status"],
        external_message_id=row["external_message_id"],
        rfc_message_id=row["rfc_message_id"],
        source_account=row["source_account"],
        source_mailbox=row["source_mailbox"],
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


def _row_to_mail_conversation(row: sqlite3.Row) -> MailConversation:
    participants = json.loads(row["participants_json"])
    return MailConversation(
        id=UUID(row["id"]),
        source_kind=row["source_kind"],
        external_conversation_id=row["external_conversation_id"],
        normalized_subject=row["normalized_subject"],
        latest_subject=row["latest_subject"],
        latest_message_at=_deserialize_datetime(row["latest_message_at"]),
        participants=tuple(
            MailParticipant(
                name=participant.get("name"),
                email=participant.get("email"),
            )
            for participant in participants
        ),
        message_count=int(row["message_count"]),
        latest_artifact_id=UUID(row["latest_artifact_id"]),
        assigned_case_id=UUID(row["assigned_case_id"]) if row["assigned_case_id"] else None,
        created_at=_deserialize_datetime(row["created_at"]),
        updated_at=_deserialize_datetime(row["updated_at"]),
    )


def _row_to_mail_message(row: sqlite3.Row) -> MailMessage:
    return MailMessage(
        artifact_id=UUID(row["artifact_id"]),
        conversation_id=UUID(row["conversation_id"]),
        source_kind=row["source_kind"],
        source_account_id=row["source_account_id"],
        source_folder_id=row["source_folder_id"],
        source_message_id=row["source_message_id"],
        source_conversation_id=row["source_conversation_id"],
        internet_message_id=row["internet_message_id"],
        dedupe_fingerprint=row["dedupe_fingerprint"],
        direction=row["direction"],
        received_at=_deserialize_datetime(row["received_at"]),
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


def _row_to_mail_selector(row: sqlite3.Row) -> MailSelector:
    return MailSelector(
        account_name=row["account_name"],
        mailbox_name=row["mailbox_name"],
        unread_only=bool(row["unread_only"]),
        sender_filter=row["sender_filter"] or "",
        subject_filter=row["subject_filter"] or "",
        sent_after=_deserialize_datetime(row["sent_after"]),
        result_limit=int(row["result_limit"]),
    )


def _row_to_mail_candidate(row: sqlite3.Row) -> MailCandidate:
    return MailCandidate(
        candidate_id=row["candidate_id"],
        source_system=row["source_system"],
        account_name=row["account_name"],
        mailbox_name=row["mailbox_name"],
        subject=row["subject"],
        sender_name=row["sender_name"],
        sender_email=row["sender_email"],
        sent_at=_deserialize_datetime(row["sent_at"]),
        preview_text=row["preview_text"],
        unread=bool(row["unread"]),
        rfc_message_id=row["rfc_message_id"],
    )
