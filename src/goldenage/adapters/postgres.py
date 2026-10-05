"""Raw-SQL PostgreSQL adapters."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict
from datetime import datetime
from decimal import Decimal
from typing import Protocol, TypeVar, cast
from uuid import UUID

import psycopg as _psycopg
from psycopg.rows import BaseRowFactory
from psycopg.rows import dict_row as _dict_row
from psycopg.types.json import Jsonb

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
    MailboxAccountConfig,
    MailboxSyncCheckpoint,
    MailConversation,
    MailMessage,
    MailParticipant,
    UserContext,
)


class _PsycopgModule(Protocol):
    def connect(
        self,
        dsn: str,
        *,
        row_factory: BaseRowFactory[dict[str, object]],
    ) -> _psycopg.Connection[dict[str, object]]:
        """Open a connection whose rows are mappings of validated values."""


psycopg: _PsycopgModule = cast(_PsycopgModule, _psycopg)
dict_row: BaseRowFactory[dict[str, object]] = cast(BaseRowFactory[dict[str, object]], _dict_row)


class PostgresRepositoryError(RuntimeError):
    """Raised when PostgreSQL adapters cannot be used."""


class _PostgresRepositoryBase:
    """Base helper for raw-SQL repositories."""

    def __init__(self, dsn: str) -> None:
        if psycopg is None:
            raise PostgresRepositoryError("psycopg is not installed.")
        self._dsn = dsn

    def _connect(self) -> _psycopg.Connection[dict[str, object]]:
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
            visible_group_id = _optional(row, "visible_group_id", UUID)
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
            visible_group_id = _optional(row, "visible_group_id", UUID)
            if visible_group_id is not None and visible_group_id not in user.visible_group_ids:
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
            visible_group_id = _optional(row, "visible_group_id", UUID)
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
              AND (uploaded_by IS NULL OR uploaded_by = %(user_id)s)
            ORDER BY uploaded_at DESC
            LIMIT %(limit)s
        """
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(sql, {"user_id": user.id, "limit": limit})
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
                artifact_id, source_system, message_format, parse_status, external_message_id,
                rfc_message_id, source_account, source_mailbox, subject, sender_name,
                sender_email, sender_domain, recipients_json, sent_at, created_at
            ) VALUES (
                %(artifact_id)s, %(source_system)s, %(message_format)s, %(parse_status)s,
                %(external_message_id)s, %(rfc_message_id)s, %(source_account)s, %(source_mailbox)s,
                %(subject)s, %(sender_name)s, %(sender_email)s, %(sender_domain)s,
                %(recipients_json)s, %(sent_at)s, %(created_at)s
            )
            ON CONFLICT (artifact_id) DO UPDATE SET
                source_system = EXCLUDED.source_system,
                message_format = EXCLUDED.message_format,
                parse_status = EXCLUDED.parse_status,
                external_message_id = EXCLUDED.external_message_id,
                rfc_message_id = EXCLUDED.rfc_message_id,
                source_account = EXCLUDED.source_account,
                source_mailbox = EXCLUDED.source_mailbox,
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
            WHERE artifact_id = %(artifact_id)s
        """
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(sql, {"artifact_id": artifact_id})
            row = cursor.fetchone()
            return _row_to_mail_metadata(row) if row else None

    def save_mail_conversation(self, conversation: MailConversation) -> None:
        sql = """
            INSERT INTO mail_conversation (
                id, source_kind, external_conversation_id, normalized_subject, latest_subject,
                latest_message_at, participants_json, message_count, latest_artifact_id,
                assigned_case_id, created_at, updated_at
            ) VALUES (
                %(id)s, %(source_kind)s, %(external_conversation_id)s, %(normalized_subject)s,
                %(latest_subject)s, %(latest_message_at)s, %(participants_json)s, %(message_count)s,
                %(latest_artifact_id)s, %(assigned_case_id)s, %(created_at)s, %(updated_at)s
            )
            ON CONFLICT (id) DO UPDATE SET
                source_kind = EXCLUDED.source_kind,
                external_conversation_id = EXCLUDED.external_conversation_id,
                normalized_subject = EXCLUDED.normalized_subject,
                latest_subject = EXCLUDED.latest_subject,
                latest_message_at = EXCLUDED.latest_message_at,
                participants_json = EXCLUDED.participants_json,
                message_count = EXCLUDED.message_count,
                latest_artifact_id = EXCLUDED.latest_artifact_id,
                assigned_case_id = EXCLUDED.assigned_case_id,
                created_at = EXCLUDED.created_at,
                updated_at = EXCLUDED.updated_at
        """
        payload = asdict(conversation)
        payload["participants_json"] = Jsonb(payload.pop("participants"))
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(sql, payload)
            connection.commit()

    def get_mail_conversation(
        self,
        conversation_id: UUID,
        user: UserContext,
    ) -> MailConversation | None:
        sql = """
            SELECT mc.*
            FROM mail_conversation mc
            LEFT JOIN artifact a ON a.id = mc.latest_artifact_id
            LEFT JOIN case_file c ON c.id = COALESCE(mc.assigned_case_id, a.assigned_case_id)
            WHERE mc.id = %(conversation_id)s
              AND (c.visible_group_id IS NULL OR c.visible_group_id = ANY(%(group_ids)s::uuid[]) OR c.id IS NULL)
        """
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                sql,
                {"conversation_id": conversation_id, "group_ids": list(user.visible_group_ids)},
            )
            row = cursor.fetchone()
            return _row_to_mail_conversation(row) if row else None

    def list_recent_mail_conversations(
        self,
        user: UserContext,
        *,
        limit: int = 10,
    ) -> Sequence[MailConversation]:
        sql = """
            SELECT mc.*
            FROM mail_conversation mc
            LEFT JOIN artifact a ON a.id = mc.latest_artifact_id
            LEFT JOIN case_file c ON c.id = COALESCE(mc.assigned_case_id, a.assigned_case_id)
            WHERE c.visible_group_id IS NULL OR c.visible_group_id = ANY(%(group_ids)s::uuid[]) OR c.id IS NULL
            ORDER BY mc.latest_message_at DESC
            LIMIT %(limit)s
        """
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(sql, {"group_ids": list(user.visible_group_ids), "limit": limit})
            return tuple(_row_to_mail_conversation(row) for row in cursor.fetchall())

    def save_mail_message(self, message: MailMessage) -> None:
        sql = """
            INSERT INTO mail_message (
                artifact_id, conversation_id, source_kind, source_account_id, source_folder_id,
                source_message_id, source_conversation_id, internet_message_id,
                dedupe_fingerprint, direction, received_at, created_at
            ) VALUES (
                %(artifact_id)s, %(conversation_id)s, %(source_kind)s, %(source_account_id)s,
                %(source_folder_id)s, %(source_message_id)s, %(source_conversation_id)s,
                %(internet_message_id)s, %(dedupe_fingerprint)s, %(direction)s,
                %(received_at)s, %(created_at)s
            )
            ON CONFLICT (artifact_id) DO UPDATE SET
                conversation_id = EXCLUDED.conversation_id,
                source_kind = EXCLUDED.source_kind,
                source_account_id = EXCLUDED.source_account_id,
                source_folder_id = EXCLUDED.source_folder_id,
                source_message_id = EXCLUDED.source_message_id,
                source_conversation_id = EXCLUDED.source_conversation_id,
                internet_message_id = EXCLUDED.internet_message_id,
                dedupe_fingerprint = EXCLUDED.dedupe_fingerprint,
                direction = EXCLUDED.direction,
                received_at = EXCLUDED.received_at,
                created_at = EXCLUDED.created_at
        """
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(sql, asdict(message))
            connection.commit()

    def get_mail_message(self, artifact_id: UUID, user: UserContext) -> MailMessage | None:
        if self.get_artifact(artifact_id, user) is None:
            return None
        sql = """
            SELECT artifact_id, conversation_id, source_kind, source_account_id, source_folder_id,
                   source_message_id, source_conversation_id, internet_message_id,
                   dedupe_fingerprint, direction, received_at, created_at
            FROM mail_message
            WHERE artifact_id = %(artifact_id)s
        """
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(sql, {"artifact_id": artifact_id})
            row = cursor.fetchone()
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
        sql = """
            SELECT mm.artifact_id, mm.conversation_id, mm.source_kind, mm.source_account_id,
                   mm.source_folder_id, mm.source_message_id, mm.source_conversation_id,
                   mm.internet_message_id, mm.dedupe_fingerprint, mm.direction, mm.received_at,
                   mm.created_at
            FROM mail_message mm
            JOIN artifact a ON a.id = mm.artifact_id
            LEFT JOIN case_file c ON c.id = a.assigned_case_id
            WHERE mm.source_kind = %(source_kind)s
              AND (
                    (
                        %(source_account_id)s IS NOT NULL
                    AND %(source_folder_id)s IS NOT NULL
                    AND %(source_message_id)s IS NOT NULL
                    AND mm.source_account_id = %(source_account_id)s
                    AND mm.source_folder_id = %(source_folder_id)s
                    AND mm.source_message_id = %(source_message_id)s
                    )
                 OR (%(internet_message_id)s IS NOT NULL AND mm.internet_message_id = %(internet_message_id)s)
                 OR mm.dedupe_fingerprint = %(dedupe_fingerprint)s
              )
              AND (c.visible_group_id IS NULL OR c.visible_group_id = ANY(%(group_ids)s::uuid[]) OR a.assigned_case_id IS NULL)
            LIMIT 1
        """
        params = {
            "source_kind": source_kind,
            "source_account_id": source_account_id,
            "source_folder_id": source_folder_id,
            "source_message_id": source_message_id,
            "internet_message_id": internet_message_id,
            "dedupe_fingerprint": dedupe_fingerprint,
            "group_ids": list(user.visible_group_ids),
        }
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(sql, params)
            row = cursor.fetchone()
            return _row_to_mail_message(row) if row else None

    def list_conversation_artifacts(
        self,
        conversation_id: UUID,
        user: UserContext,
    ) -> Sequence[Artifact]:
        sql = """
            SELECT a.id, a.file_name, a.media_type, a.size_bytes, a.content_text, a.storage_key,
                   a.uploaded_at, a.uploaded_by, a.assigned_case_id
            FROM mail_message mm
            JOIN artifact a ON a.id = mm.artifact_id
            LEFT JOIN case_file c ON c.id = a.assigned_case_id
            WHERE mm.conversation_id = %(conversation_id)s
              AND (c.visible_group_id IS NULL OR c.visible_group_id = ANY(%(group_ids)s::uuid[]) OR a.assigned_case_id IS NULL)
            ORDER BY COALESCE(mm.received_at, a.uploaded_at) DESC, a.uploaded_at DESC
        """
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                sql,
                {"conversation_id": conversation_id, "group_ids": list(user.visible_group_ids)},
            )
            return tuple(_row_to_artifact(row) for row in cursor.fetchall())

    def save_mailbox_account_config(self, config: MailboxAccountConfig) -> None:
        sql = """
            INSERT INTO mailbox_account_config (
                id, user_id, source_kind, account_key, outlook_store_name, inbox_folder_key,
                sent_folder_key, polling_interval_seconds, active, created_at, updated_at
            ) VALUES (
                %(id)s, %(user_id)s, %(source_kind)s, %(account_key)s, %(outlook_store_name)s,
                %(inbox_folder_key)s, %(sent_folder_key)s, %(polling_interval_seconds)s,
                %(active)s, %(created_at)s, %(updated_at)s
            )
            ON CONFLICT (id) DO UPDATE SET
                user_id = EXCLUDED.user_id,
                source_kind = EXCLUDED.source_kind,
                account_key = EXCLUDED.account_key,
                outlook_store_name = EXCLUDED.outlook_store_name,
                inbox_folder_key = EXCLUDED.inbox_folder_key,
                sent_folder_key = EXCLUDED.sent_folder_key,
                polling_interval_seconds = EXCLUDED.polling_interval_seconds,
                active = EXCLUDED.active,
                created_at = EXCLUDED.created_at,
                updated_at = EXCLUDED.updated_at
        """
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(sql, asdict(config))
            connection.commit()

    def get_active_mailbox_account_config(
        self,
        user: UserContext,
    ) -> MailboxAccountConfig | None:
        sql = """
            SELECT *
            FROM mailbox_account_config
            WHERE active = TRUE
              AND (user_id IS NULL OR user_id = %(user_id)s)
            ORDER BY updated_at DESC
            LIMIT 1
        """
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(sql, {"user_id": user.id})
            row = cursor.fetchone()
            return _row_to_mailbox_account_config(row) if row else None

    def save_mailbox_sync_checkpoint(self, checkpoint: MailboxSyncCheckpoint) -> None:
        sql = """
            INSERT INTO mailbox_sync_checkpoint (
                account_config_id, folder_key, last_message_key, last_message_at, updated_at
            ) VALUES (
                %(account_config_id)s, %(folder_key)s, %(last_message_key)s, %(last_message_at)s, %(updated_at)s
            )
            ON CONFLICT (account_config_id, folder_key) DO UPDATE SET
                last_message_key = EXCLUDED.last_message_key,
                last_message_at = EXCLUDED.last_message_at,
                updated_at = EXCLUDED.updated_at
        """
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(sql, asdict(checkpoint))
            connection.commit()

    def list_mailbox_sync_checkpoints(
        self,
        account_config_id: UUID,
    ) -> Sequence[MailboxSyncCheckpoint]:
        sql = """
            SELECT account_config_id, folder_key, last_message_key, last_message_at, updated_at
            FROM mailbox_sync_checkpoint
            WHERE account_config_id = %(account_config_id)s
            ORDER BY folder_key ASC
        """
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(sql, {"account_config_id": account_config_id})
            return tuple(_row_to_mailbox_sync_checkpoint(row) for row in cursor.fetchall())


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


ValueType = TypeVar("ValueType")


def _required_value(value: object, expected: type[ValueType], field: str) -> ValueType:
    if not isinstance(value, expected):
        raise PostgresRepositoryError(f"PostgreSQL field {field!r} has an invalid value.")
    return value


def _optional_value(
    value: object,
    expected: type[ValueType],
    field: str,
) -> ValueType | None:
    if value is None:
        return None
    return _required_value(value, expected, field)


def _required(row: Mapping[str, object], field: str, expected: type[ValueType]) -> ValueType:
    return _required_value(row.get(field), expected, field)


def _optional(
    row: Mapping[str, object],
    field: str,
    expected: type[ValueType],
) -> ValueType | None:
    return _optional_value(row.get(field), expected, field)


def _literal(
    row: Mapping[str, object],
    field: str,
    choices: tuple[ValueType, ...],
) -> ValueType:
    value = _required(row, field, str)
    if value not in choices:
        raise PostgresRepositoryError(f"PostgreSQL field {field!r} has an invalid value.")
    return cast(ValueType, value)


def _optional_literal(
    row: Mapping[str, object],
    field: str,
    choices: tuple[ValueType, ...],
) -> ValueType | None:
    value = row.get(field)
    if value is None:
        return None
    if not isinstance(value, str) or value not in choices:
        raise PostgresRepositoryError(f"PostgreSQL field {field!r} has an invalid value.")
    return cast(ValueType, value)


def _required_float(row: Mapping[str, object], field: str) -> float:
    value = row.get(field)
    if isinstance(value, bool):
        raise PostgresRepositoryError(f"PostgreSQL field {field!r} has an invalid value.")
    if isinstance(value, (int, float, Decimal)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError as exc:
            raise PostgresRepositoryError(
                f"PostgreSQL field {field!r} has an invalid value."
            ) from exc
    raise PostgresRepositoryError(f"PostgreSQL field {field!r} has an invalid value.")


def _required_int(row: Mapping[str, object], field: str) -> int:
    value = row.get(field)
    if isinstance(value, bool):
        raise PostgresRepositoryError(f"PostgreSQL field {field!r} has an invalid value.")
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError as exc:
            raise PostgresRepositoryError(
                f"PostgreSQL field {field!r} has an invalid value."
            ) from exc
    raise PostgresRepositoryError(f"PostgreSQL field {field!r} has an invalid value.")


def _required_bool(row: Mapping[str, object], field: str) -> bool:
    value = row.get(field)
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, str) and value.lower() in ("false", "true"):
        return value.lower() == "true"
    raise PostgresRepositoryError(f"PostgreSQL field {field!r} has an invalid value.")


def _participants(value: object, field: str) -> tuple[MailParticipant, ...]:
    if not isinstance(value, (list, tuple)):
        raise PostgresRepositoryError(f"PostgreSQL field {field!r} has an invalid value.")
    participants: list[MailParticipant] = []
    for item in value:
        if not isinstance(item, Mapping):
            raise PostgresRepositoryError(f"PostgreSQL field {field!r} has an invalid value.")
        participants.append(
            MailParticipant(
                name=_optional_value(item.get("name"), str, f"{field}.name"),
                email=_optional_value(item.get("email"), str, f"{field}.email"),
            )
        )
    return tuple(participants)


def _row_to_case_file(row: Mapping[str, object]) -> CaseFile:
    return CaseFile(
        id=_required(row, "id", UUID),
        title=_required(row, "title", str),
        company=_optional(row, "company", str),
        primary_contact=_optional(row, "primary_contact", str),
        status=_literal(row, "status", ("open", "closed")),
        last_activity_at=_required(row, "last_activity_at", datetime),
        visible_group_id=_optional(row, "visible_group_id", UUID),
    )


def _row_to_activity(row: Mapping[str, object]) -> Activity:
    return Activity(
        id=_required(row, "id", UUID),
        case_id=_required(row, "case_id", UUID),
        description=_required(row, "description", str),
        kind=_literal(row, "kind", ("intake", "follow_up", "question", "escalation")),
        due_at=_required(row, "due_at", datetime),
        created_at=_required(row, "created_at", datetime),
        created_by=_optional(row, "created_by", UUID),
        completed_at=_optional(row, "completed_at", datetime),
    )


def _row_to_artifact(row: Mapping[str, object]) -> Artifact:
    return Artifact(
        id=_required(row, "id", UUID),
        file_name=_required(row, "file_name", str),
        media_type=_required(row, "media_type", str),
        size_bytes=_required_int(row, "size_bytes"),
        content_text=_required(row, "content_text", str),
        storage_key=_required(row, "storage_key", str),
        uploaded_at=_required(row, "uploaded_at", datetime),
        uploaded_by=_optional(row, "uploaded_by", UUID),
        assigned_case_id=_optional(row, "assigned_case_id", UUID),
    )


def _row_to_suggestion(row: Mapping[str, object]) -> AssignmentSuggestion:
    return AssignmentSuggestion(
        artifact_id=_required(row, "artifact_id", UUID),
        suggested_case_id=_optional(row, "suggested_case_id", UUID),
        summary_reason=_required(row, "summary_reason", str),
        confidence=_required_float(row, "confidence"),
        created_at=_required(row, "created_at", datetime),
    )


def _row_to_mail_metadata(row: Mapping[str, object]) -> ArtifactMailMetadata:
    return ArtifactMailMetadata(
        artifact_id=_required(row, "artifact_id", UUID),
        source_system=_literal(
            row, "source_system", ("outlook_upload", "apple_mail_client", "desktop_mail_client")
        ),
        message_format=_literal(row, "message_format", ("outlook_msg", "rfc822_email")),
        parse_status=_literal(row, "parse_status", ("parsed",)),
        external_message_id=_optional(row, "external_message_id", str),
        rfc_message_id=_optional(row, "rfc_message_id", str),
        source_account=_optional(row, "source_account", str),
        source_mailbox=_optional(row, "source_mailbox", str),
        subject=_optional(row, "subject", str),
        sender_name=_optional(row, "sender_name", str),
        sender_email=_optional(row, "sender_email", str),
        sender_domain=_optional(row, "sender_domain", str),
        recipients=_participants(row.get("recipients_json"), "recipients_json"),
        sent_at=_optional(row, "sent_at", datetime),
        created_at=_required(row, "created_at", datetime),
    )


def _row_to_mail_conversation(row: Mapping[str, object]) -> MailConversation:
    return MailConversation(
        id=_required(row, "id", UUID),
        source_kind=_required(row, "source_kind", str),
        external_conversation_id=_optional(row, "external_conversation_id", str),
        normalized_subject=_optional(row, "normalized_subject", str),
        latest_subject=_optional(row, "latest_subject", str),
        latest_message_at=_required(row, "latest_message_at", datetime),
        participants=_participants(row.get("participants_json"), "participants_json"),
        message_count=_required_int(row, "message_count"),
        latest_artifact_id=_required(row, "latest_artifact_id", UUID),
        created_at=_required(row, "created_at", datetime),
        updated_at=_required(row, "updated_at", datetime),
        assigned_case_id=_optional(row, "assigned_case_id", UUID),
    )


def _row_to_mail_message(row: Mapping[str, object]) -> MailMessage:
    return MailMessage(
        artifact_id=_required(row, "artifact_id", UUID),
        conversation_id=_required(row, "conversation_id", UUID),
        source_kind=_required(row, "source_kind", str),
        source_account_id=_optional(row, "source_account_id", str),
        source_folder_id=_optional(row, "source_folder_id", str),
        source_message_id=_optional(row, "source_message_id", str),
        source_conversation_id=_optional(row, "source_conversation_id", str),
        internet_message_id=_optional(row, "internet_message_id", str),
        dedupe_fingerprint=_required(row, "dedupe_fingerprint", str),
        direction=_optional_literal(row, "direction", ("inbound", "outbound")),
        received_at=_optional(row, "received_at", datetime),
        created_at=_required(row, "created_at", datetime),
    )


def _row_to_mailbox_account_config(row: Mapping[str, object]) -> MailboxAccountConfig:
    return MailboxAccountConfig(
        id=_required(row, "id", UUID),
        user_id=_optional(row, "user_id", UUID),
        source_kind=_required(row, "source_kind", str),
        account_key=_required(row, "account_key", str),
        outlook_store_name=_required(row, "outlook_store_name", str),
        inbox_folder_key=_optional(row, "inbox_folder_key", str),
        sent_folder_key=_optional(row, "sent_folder_key", str),
        polling_interval_seconds=_required_int(row, "polling_interval_seconds"),
        active=_required_bool(row, "active"),
        created_at=_required(row, "created_at", datetime),
        updated_at=_required(row, "updated_at", datetime),
    )


def _row_to_mailbox_sync_checkpoint(row: Mapping[str, object]) -> MailboxSyncCheckpoint:
    return MailboxSyncCheckpoint(
        account_config_id=_required(row, "account_config_id", UUID),
        folder_key=_required(row, "folder_key", str),
        last_message_key=_optional(row, "last_message_key", str),
        last_message_at=_optional(row, "last_message_at", datetime),
        updated_at=_required(row, "updated_at", datetime),
    )
