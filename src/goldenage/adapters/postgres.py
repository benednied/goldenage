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
    MailImportRepository,
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
    MailCandidate,
    MailConversation,
    MailMessage,
    MailParticipant,
    MailSelector,
    MailSourceSystem,
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


class _PostgresConnectionProxy:
    """Expose an active connection without allowing repository methods to commit it."""

    def __init__(self, connection) -> None:
        self._connection = connection

    def __enter__(self):
        return self

    def __exit__(self, *args: object) -> None:
        del args

    def cursor(self):
        return self._connection.cursor()

    def commit(self) -> None:
        """Defer the commit until the enclosing business transaction exits."""


class PostgresRepositoryTransaction:
    """Unit of work shared by PostgreSQL repositories during one business operation."""

    def __init__(self, dsn: str) -> None:
        if psycopg is None:
            raise PostgresRepositoryError("psycopg is not installed.")
        self._dsn = dsn
        self._connection = None

    def __enter__(self) -> "PostgresRepositoryTransaction":
        if self._connection is not None:
            raise PostgresRepositoryError("PostgreSQL transaction is already active.")
        self._connection = psycopg.connect(self._dsn, row_factory=dict_row)
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        connection = self._connection
        self._connection = None
        if connection is None:
            return
        try:
            if exc_type is None:
                connection.commit()
            else:
                connection.rollback()
        finally:
            connection.close()

    def connection(self) -> _PostgresConnectionProxy | None:
        """Return a no-commit proxy when this transaction is active."""
        if self._connection is None:
            return None
        return _PostgresConnectionProxy(self._connection)


class _PostgresRepositoryBase:
    """Base helper for raw-SQL repositories."""

    def __init__(
        self,
        dsn: str,
        *,
        transaction: PostgresRepositoryTransaction | None = None,
    ) -> None:
        if psycopg is None:
            raise PostgresRepositoryError("psycopg is not installed.")
        self._dsn = dsn
        self._transaction = transaction or PostgresRepositoryTransaction(dsn)

    def _connect(self):
        active_connection = self._transaction.connection()
        if active_connection is not None:
            return active_connection
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
            if (
                row["visible_group_id"] is not None
                and row["visible_group_id"] not in user.visible_group_ids
            ):
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


class _PostgresArtifactRepositoryCore(_PostgresRepositoryBase, ArtifactRepository):
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


class _PostgresMailImportRepositoryCore(_PostgresRepositoryBase, MailImportRepository):
    """PostgreSQL persistence for mail selectors, review queue, and dedupe state."""

    def transaction(self) -> PostgresRepositoryTransaction:
        """Return the shared transaction used by all PostgreSQL repositories."""
        return self._transaction

    def upsert_source(
        self,
        *,
        user: UserContext,
        source_system: MailSourceSystem,
        now: datetime,
    ) -> None:
        sql = """
            INSERT INTO mail_import_source (user_id, source_system, enabled_at, updated_at)
            VALUES (%(user_id)s, %(source_system)s, %(enabled_at)s, %(updated_at)s)
            ON CONFLICT (user_id, source_system) DO UPDATE SET
                updated_at = EXCLUDED.updated_at
        """
        params = {
            "user_id": user.id,
            "source_system": source_system,
            "enabled_at": now,
            "updated_at": now,
        }
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(sql, params)
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
                %(user_id)s, %(source_system)s, %(account_name)s, %(mailbox_name)s,
                %(unread_only)s, %(sender_filter)s, %(subject_filter)s, %(sent_after)s,
                %(result_limit)s, %(updated_at)s
            )
            ON CONFLICT (user_id, source_system) DO UPDATE SET
                account_name = EXCLUDED.account_name,
                mailbox_name = EXCLUDED.mailbox_name,
                unread_only = EXCLUDED.unread_only,
                sender_filter = EXCLUDED.sender_filter,
                subject_filter = EXCLUDED.subject_filter,
                sent_after = EXCLUDED.sent_after,
                result_limit = EXCLUDED.result_limit,
                updated_at = EXCLUDED.updated_at
        """
        params = {
            "user_id": user.id,
            "source_system": source_system,
            "account_name": selector.account_name,
            "mailbox_name": selector.mailbox_name,
            "unread_only": selector.unread_only,
            "sender_filter": selector.sender_filter,
            "subject_filter": selector.subject_filter,
            "sent_after": selector.sent_after,
            "result_limit": selector.result_limit,
            "updated_at": now,
        }
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(sql, params)
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
            WHERE user_id = %(user_id)s AND source_system = %(source_system)s
        """
        params = {"user_id": user.id, "source_system": source_system}
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(sql, params)
            row = cursor.fetchone()
            return _row_to_mail_selector(row) if row else None

    def replace_review_candidates(
        self,
        *,
        user: UserContext,
        source_system: MailSourceSystem,
        candidates: Sequence[MailCandidate],
        now: datetime,
    ) -> None:
        delete_sql = """
            DELETE FROM mail_import_review_candidate
            WHERE user_id = %(user_id)s AND source_system = %(source_system)s
        """
        insert_sql = """
            INSERT INTO mail_import_review_candidate (
                user_id, source_system, candidate_id, account_name, mailbox_name, subject,
                sender_name, sender_email, sent_at, preview_text, unread, rfc_message_id,
                created_at
            ) VALUES (
                %(user_id)s, %(source_system)s, %(candidate_id)s, %(account_name)s,
                %(mailbox_name)s, %(subject)s, %(sender_name)s, %(sender_email)s,
                %(sent_at)s, %(preview_text)s, %(unread)s, %(rfc_message_id)s, %(created_at)s
            )
        """
        user_id = user.id
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                delete_sql,
                {"user_id": user_id, "source_system": source_system},
            )
            for candidate in candidates:
                cursor.execute(
                    insert_sql,
                    {
                        "user_id": user_id,
                        "source_system": source_system,
                        "candidate_id": candidate.candidate_id,
                        "account_name": candidate.account_name,
                        "mailbox_name": candidate.mailbox_name,
                        "subject": candidate.subject,
                        "sender_name": candidate.sender_name,
                        "sender_email": candidate.sender_email,
                        "sent_at": candidate.sent_at,
                        "preview_text": candidate.preview_text,
                        "unread": candidate.unread,
                        "rfc_message_id": candidate.rfc_message_id,
                        "created_at": now,
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
            WHERE user_id = %(user_id)s AND source_system = %(source_system)s
            ORDER BY sent_at DESC NULLS LAST, candidate_id ASC
        """
        params = {"user_id": user.id, "source_system": source_system}
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(sql, params)
            return tuple(_row_to_mail_candidate(row) for row in cursor.fetchall())

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
            WHERE user_id = %(user_id)s
              AND source_system = %(source_system)s
              AND candidate_id = %(candidate_id)s
        """
        params = {
            "user_id": user.id,
            "source_system": source_system,
            "candidate_id": candidate_id,
        }
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(sql, params)
            row = cursor.fetchone()
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
            WHERE user_id = %(user_id)s
              AND source_system = %(source_system)s
              AND candidate_id = %(candidate_id)s
        """
        params = {
            "user_id": user.id,
            "source_system": source_system,
            "candidate_id": candidate_id,
        }
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(sql, params)
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
            WHERE user_id = %(user_id)s AND source_system = %(source_system)s
        """
        params = {"user_id": user.id, "source_system": source_system}
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(sql, params)
            rows = cursor.fetchall()
        ids: set[str] = set()
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
            ) VALUES (
                %(user_id)s, %(source_system)s, %(external_message_id)s, %(rfc_message_id)s,
                %(artifact_id)s, %(imported_at)s
            )
        """
        params = {
            "user_id": user.id,
            "source_system": source_system,
            "external_message_id": external_message_id,
            "rfc_message_id": rfc_message_id,
            "artifact_id": artifact_id,
            "imported_at": now,
        }
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(sql, params)
            connection.commit()


class PostgresArtifactRepository(_PostgresArtifactRepositoryCore, ArtifactRepository):
    """Artifact and suggestion repository backed by PostgreSQL."""

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


class PostgresMailImportRepository(_PostgresMailImportRepositoryCore):
    """PostgreSQL persistence for mail selectors, review queue, and dedupe state."""


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
                name=participant.get("name"),
                email=participant.get("email"),
            )
            for participant in row["recipients_json"]
        ),
        sent_at=row["sent_at"],
        created_at=row["created_at"],
    )


def _row_to_mail_conversation(row: dict[str, object]) -> MailConversation:
    return MailConversation(
        id=row["id"],
        source_kind=row["source_kind"],
        external_conversation_id=row["external_conversation_id"],
        normalized_subject=row["normalized_subject"],
        latest_subject=row["latest_subject"],
        latest_message_at=row["latest_message_at"],
        participants=tuple(
            MailParticipant(name=participant.get("name"), email=participant.get("email"))
            for participant in row["participants_json"]
        ),
        message_count=int(row["message_count"]),
        latest_artifact_id=row["latest_artifact_id"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        assigned_case_id=row["assigned_case_id"],
    )


def _row_to_mail_message(row: dict[str, object]) -> MailMessage:
    return MailMessage(
        artifact_id=row["artifact_id"],
        conversation_id=row["conversation_id"],
        source_kind=row["source_kind"],
        source_account_id=row["source_account_id"],
        source_folder_id=row["source_folder_id"],
        source_message_id=row["source_message_id"],
        source_conversation_id=row["source_conversation_id"],
        internet_message_id=row["internet_message_id"],
        dedupe_fingerprint=row["dedupe_fingerprint"],
        direction=row["direction"],
        received_at=row["received_at"],
        created_at=row["created_at"],
    )


def _row_to_mail_selector(row: dict[str, object]) -> MailSelector:
    return MailSelector(
        account_name=row["account_name"],
        mailbox_name=row["mailbox_name"],
        unread_only=bool(row["unread_only"]),
        sender_filter=row["sender_filter"] or "",
        subject_filter=row["subject_filter"] or "",
        sent_after=row["sent_after"],
        result_limit=int(row["result_limit"]),
    )


def _row_to_mail_candidate(row: dict[str, object]) -> MailCandidate:
    return MailCandidate(
        candidate_id=row["candidate_id"],
        source_system=row["source_system"],
        account_name=row["account_name"],
        mailbox_name=row["mailbox_name"],
        subject=row["subject"],
        sender_name=row["sender_name"],
        sender_email=row["sender_email"],
        sent_at=row["sent_at"],
        preview_text=row["preview_text"],
        unread=bool(row["unread"]),
        rfc_message_id=row["rfc_message_id"],
    )


def _row_to_mailbox_account_config(row: dict[str, object]) -> MailboxAccountConfig:
    return MailboxAccountConfig(
        id=row["id"],
        user_id=row["user_id"],
        source_kind=row["source_kind"],
        account_key=row["account_key"],
        outlook_store_name=row["outlook_store_name"],
        inbox_folder_key=row["inbox_folder_key"],
        sent_folder_key=row["sent_folder_key"],
        polling_interval_seconds=int(row["polling_interval_seconds"]),
        active=bool(row["active"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _row_to_mailbox_sync_checkpoint(row: dict[str, object]) -> MailboxSyncCheckpoint:
    return MailboxSyncCheckpoint(
        account_config_id=row["account_config_id"],
        folder_key=row["folder_key"],
        last_message_key=row["last_message_key"],
        last_message_at=row["last_message_at"],
        updated_at=row["updated_at"],
    )
