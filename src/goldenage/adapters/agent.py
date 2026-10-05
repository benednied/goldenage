"""Persistence adapters for Gisela conversations and execution provenance."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any, cast
from uuid import UUID

from goldenage.application.ports import AgentRepository
from goldenage.domain.models import (
    AgentConversation,
    AgentMessage,
    AgentRun,
    AgentToolCall,
    UserContext,
)


class InMemoryAgentRepository(AgentRepository):
    """Deterministic repository used by the demo runtime and contract tests."""

    def __init__(self) -> None:
        self.conversations: dict[UUID, AgentConversation] = {}
        self.messages: dict[UUID, AgentMessage] = {}
        self.runs: dict[UUID, AgentRun] = {}
        self.tool_calls: dict[UUID, AgentToolCall] = {}

    def save_conversation(self, conversation: AgentConversation) -> None:
        self.conversations[conversation.id] = conversation

    def get_conversation(
        self, conversation_id: UUID, user: UserContext
    ) -> AgentConversation | None:
        conversation = self.conversations.get(conversation_id)
        if conversation is None or conversation.acting_user_id != user.id:
            return None
        return conversation

    def list_conversations(
        self, user: UserContext, *, limit: int = 25
    ) -> Sequence[AgentConversation]:
        items = [item for item in self.conversations.values() if item.acting_user_id == user.id]
        items.sort(key=lambda item: (item.updated_at, item.id), reverse=True)
        return tuple(items[: max(limit, 0)])

    def save_message(self, message: AgentMessage) -> None:
        self.messages[message.id] = message

    def list_messages(self, conversation_id: UUID, user: UserContext) -> Sequence[AgentMessage]:
        if self.get_conversation(conversation_id, user) is None:
            return ()
        return tuple(
            sorted(
                (
                    item
                    for item in self.messages.values()
                    if item.conversation_id == conversation_id
                ),
                key=lambda item: (item.sequence, item.created_at, item.id),
            )
        )

    def save_run(self, run: AgentRun) -> None:
        self.runs[run.id] = run

    def get_run(self, run_id: UUID, user: UserContext) -> AgentRun | None:
        run = self.runs.get(run_id)
        if run is None or self.get_conversation(run.conversation_id, user) is None:
            return None
        return run

    def list_runs(self, conversation_id: UUID, user: UserContext) -> Sequence[AgentRun]:
        if self.get_conversation(conversation_id, user) is None:
            return ()
        return tuple(
            sorted(
                (item for item in self.runs.values() if item.conversation_id == conversation_id),
                key=lambda item: (item.started_at, item.id),
            )
        )

    def save_tool_call(self, tool_call: AgentToolCall) -> None:
        self.tool_calls[tool_call.id] = tool_call

    def get_tool_call_by_idempotency(
        self,
        *,
        conversation_id: UUID,
        idempotency_key: str,
        user: UserContext,
    ) -> AgentToolCall | None:
        if self.get_conversation(conversation_id, user) is None:
            return None
        return next(
            (
                item
                for item in self.tool_calls.values()
                if item.conversation_id == conversation_id
                and item.idempotency_key == idempotency_key
            ),
            None,
        )

    def list_tool_calls(self, run_id: UUID, user: UserContext) -> Sequence[AgentToolCall]:
        run = self.get_run(run_id, user)
        if run is None:
            return ()
        return tuple(
            sorted(
                (item for item in self.tool_calls.values() if item.run_id == run_id),
                key=lambda item: (item.created_at, item.id),
            )
        )


class SQLiteAgentRepository(AgentRepository):
    """SQLite persistence for restart-safe local-first conversations."""

    def __init__(self, database_path: Path | str) -> None:
        self._database_path = Path(database_path)
        self._database_path.parent.mkdir(parents=True, exist_ok=True)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._database_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def save_conversation(self, conversation: AgentConversation) -> None:
        sql = """
            INSERT INTO agent_conversation
                (id, acting_user_id, agent_name, title, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                agent_name = excluded.agent_name,
                title = excluded.title,
                updated_at = excluded.updated_at
        """
        with self._connect() as connection:
            connection.execute(
                sql,
                (
                    str(conversation.id),
                    str(conversation.acting_user_id),
                    conversation.agent_name,
                    conversation.title,
                    _serialize_datetime(conversation.created_at),
                    _serialize_datetime(conversation.updated_at),
                ),
            )
            connection.commit()

    def get_conversation(
        self, conversation_id: UUID, user: UserContext
    ) -> AgentConversation | None:
        sql = """
            SELECT id, acting_user_id, agent_name, title, created_at, updated_at
            FROM agent_conversation
            WHERE id = ? AND acting_user_id = ?
        """
        with self._connect() as connection:
            row = connection.execute(sql, (str(conversation_id), str(user.id))).fetchone()
        return _row_to_conversation(row) if row else None

    def list_conversations(
        self, user: UserContext, *, limit: int = 25
    ) -> Sequence[AgentConversation]:
        sql = """
            SELECT id, acting_user_id, agent_name, title, created_at, updated_at
            FROM agent_conversation
            WHERE acting_user_id = ?
            ORDER BY updated_at DESC, id DESC
            LIMIT ?
        """
        with self._connect() as connection:
            rows = connection.execute(sql, (str(user.id), max(limit, 0))).fetchall()
        return tuple(_row_to_conversation(row) for row in rows)

    def save_message(self, message: AgentMessage) -> None:
        sql = """
            INSERT INTO agent_message
                (id, conversation_id, run_id, role, content, created_at, sequence)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET content = excluded.content
        """
        with self._connect() as connection:
            connection.execute(
                sql,
                (
                    str(message.id),
                    str(message.conversation_id),
                    str(message.run_id) if message.run_id else None,
                    message.role,
                    message.content,
                    _serialize_datetime(message.created_at),
                    message.sequence,
                ),
            )
            connection.commit()

    def list_messages(self, conversation_id: UUID, user: UserContext) -> Sequence[AgentMessage]:
        if self.get_conversation(conversation_id, user) is None:
            return ()
        sql = """
            SELECT id, conversation_id, run_id, role, content, created_at, sequence
            FROM agent_message
            WHERE conversation_id = ?
            ORDER BY sequence ASC, created_at ASC, id ASC
        """
        with self._connect() as connection:
            rows = connection.execute(sql, (str(conversation_id),)).fetchall()
        return tuple(_row_to_message(row) for row in rows)

    def save_run(self, run: AgentRun) -> None:
        sql = """
            INSERT INTO agent_run
                (id, conversation_id, acting_user_id, actor_kind, agent_name, status,
                 started_at, completed_at, error, request_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                status = excluded.status,
                completed_at = excluded.completed_at,
                error = excluded.error,
                request_id = excluded.request_id
        """
        with self._connect() as connection:
            connection.execute(
                sql,
                (
                    str(run.id),
                    str(run.conversation_id),
                    str(run.acting_user_id),
                    run.actor_kind,
                    run.agent_name,
                    run.status,
                    _serialize_datetime(run.started_at),
                    _serialize_datetime(run.completed_at),
                    run.error,
                    run.request_id,
                ),
            )
            connection.commit()

    def get_run(self, run_id: UUID, user: UserContext) -> AgentRun | None:
        sql = """
            SELECT id, conversation_id, acting_user_id, actor_kind, agent_name, status,
                   started_at, completed_at, error, request_id
            FROM agent_run
            WHERE id = ? AND acting_user_id = ?
        """
        with self._connect() as connection:
            row = connection.execute(sql, (str(run_id), str(user.id))).fetchone()
        return _row_to_run(row) if row else None

    def list_runs(self, conversation_id: UUID, user: UserContext) -> Sequence[AgentRun]:
        if self.get_conversation(conversation_id, user) is None:
            return ()
        sql = """
            SELECT id, conversation_id, acting_user_id, actor_kind, agent_name, status,
                   started_at, completed_at, error, request_id
            FROM agent_run
            WHERE conversation_id = ?
            ORDER BY started_at ASC, id ASC
        """
        with self._connect() as connection:
            rows = connection.execute(sql, (str(conversation_id),)).fetchall()
        return tuple(_row_to_run(row) for row in rows)

    def save_tool_call(self, tool_call: AgentToolCall) -> None:
        sql = """
            INSERT INTO agent_tool_call
                (id, conversation_id, run_id, acting_user_id, actor_kind, agent_name,
                 tool_name, idempotency_key, input_json, status, output_json, error,
                 created_at, completed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                status = excluded.status,
                output_json = excluded.output_json,
                error = excluded.error,
                completed_at = excluded.completed_at
        """
        with self._connect() as connection:
            connection.execute(
                sql,
                (
                    str(tool_call.id),
                    str(tool_call.conversation_id),
                    str(tool_call.run_id),
                    str(tool_call.acting_user_id),
                    tool_call.actor_kind,
                    tool_call.agent_name,
                    tool_call.tool_name,
                    tool_call.idempotency_key,
                    json.dumps(tool_call.input_json),
                    tool_call.status,
                    json.dumps(tool_call.output_json)
                    if tool_call.output_json is not None
                    else None,
                    tool_call.error,
                    _serialize_datetime(tool_call.created_at),
                    _serialize_datetime(tool_call.completed_at),
                ),
            )
            connection.commit()

    def get_tool_call_by_idempotency(
        self,
        *,
        conversation_id: UUID,
        idempotency_key: str,
        user: UserContext,
    ) -> AgentToolCall | None:
        if self.get_conversation(conversation_id, user) is None:
            return None
        sql = """
            SELECT id, conversation_id, run_id, acting_user_id, actor_kind, agent_name,
                   tool_name, idempotency_key, input_json, status, output_json, error,
                   created_at, completed_at
            FROM agent_tool_call
            WHERE conversation_id = ? AND acting_user_id = ? AND idempotency_key = ?
            ORDER BY created_at ASC
            LIMIT 1
        """
        with self._connect() as connection:
            row = connection.execute(
                sql, (str(conversation_id), str(user.id), idempotency_key)
            ).fetchone()
        return _row_to_tool_call(row) if row else None

    def list_tool_calls(self, run_id: UUID, user: UserContext) -> Sequence[AgentToolCall]:
        run = self.get_run(run_id, user)
        if run is None:
            return ()
        sql = """
            SELECT id, conversation_id, run_id, acting_user_id, actor_kind, agent_name,
                   tool_name, idempotency_key, input_json, status, output_json, error,
                   created_at, completed_at
            FROM agent_tool_call
            WHERE run_id = ?
            ORDER BY sequence ASC, created_at ASC, id ASC
        """
        with self._connect() as connection:
            rows = connection.execute(sql, (str(run_id),)).fetchall()
        return tuple(_row_to_tool_call(row) for row in rows)


class PostgresAgentRepository(AgentRepository):
    """PostgreSQL persistence for Gisela conversations and execution provenance."""

    def __init__(self, dsn: str) -> None:
        from goldenage.adapters.postgres import PostgresRepositoryError, psycopg

        if psycopg is None:
            raise PostgresRepositoryError("psycopg is not installed.")
        self._dsn = dsn

    def _connect(self):
        from goldenage.adapters.postgres import dict_row, psycopg

        return psycopg.connect(self._dsn, row_factory=cast(Any, dict_row))

    def save_conversation(self, conversation: AgentConversation) -> None:
        sql = """
            INSERT INTO agent_conversation
                (id, acting_user_id, agent_name, title, created_at, updated_at)
            VALUES (%(id)s, %(acting_user_id)s, %(agent_name)s, %(title)s,
                    %(created_at)s, %(updated_at)s)
            ON CONFLICT (id) DO UPDATE SET
                agent_name = EXCLUDED.agent_name, title = EXCLUDED.title,
                updated_at = EXCLUDED.updated_at
        """
        self._execute(sql, _conversation_params(conversation))

    def get_conversation(
        self, conversation_id: UUID, user: UserContext
    ) -> AgentConversation | None:
        row = self._fetchone(
            """
            SELECT id, acting_user_id, agent_name, title, created_at, updated_at
            FROM agent_conversation WHERE id = %(id)s AND acting_user_id = %(user_id)s
            """,
            {"id": conversation_id, "user_id": user.id},
        )
        return _row_to_conversation(row) if row else None

    def list_conversations(
        self, user: UserContext, *, limit: int = 25
    ) -> Sequence[AgentConversation]:
        rows = self._fetchall(
            """
            SELECT id, acting_user_id, agent_name, title, created_at, updated_at
            FROM agent_conversation WHERE acting_user_id = %(user_id)s
            ORDER BY updated_at DESC, id DESC LIMIT %(limit)s
            """,
            {"user_id": user.id, "limit": max(limit, 0)},
        )
        return tuple(_row_to_conversation(row) for row in rows)

    def save_message(self, message: AgentMessage) -> None:
        self._execute(
            """
            INSERT INTO agent_message
                (id, conversation_id, run_id, role, content, created_at, sequence)
            VALUES (%(id)s, %(conversation_id)s, %(run_id)s, %(role)s, %(content)s,
                    %(created_at)s, %(sequence)s)
            ON CONFLICT (id) DO UPDATE SET content = EXCLUDED.content
            """,
            _message_params(message),
        )

    def list_messages(self, conversation_id: UUID, user: UserContext) -> Sequence[AgentMessage]:
        if self.get_conversation(conversation_id, user) is None:
            return ()
        rows = self._fetchall(
            """
            SELECT id, conversation_id, run_id, role, content, created_at, sequence
            FROM agent_message WHERE conversation_id = %(conversation_id)s
            ORDER BY created_at ASC, id ASC
            """,
            {"conversation_id": conversation_id},
        )
        return tuple(_row_to_message(row) for row in rows)

    def save_run(self, run: AgentRun) -> None:
        self._execute(
            """
            INSERT INTO agent_run
                (id, conversation_id, acting_user_id, actor_kind, agent_name, status,
                 started_at, completed_at, error, request_id)
            VALUES (%(id)s, %(conversation_id)s, %(acting_user_id)s, %(actor_kind)s,
                    %(agent_name)s, %(status)s, %(started_at)s, %(completed_at)s,
                    %(error)s, %(request_id)s)
            ON CONFLICT (id) DO UPDATE SET status = EXCLUDED.status,
                completed_at = EXCLUDED.completed_at, error = EXCLUDED.error,
                request_id = EXCLUDED.request_id
            """,
            _run_params(run),
        )

    def get_run(self, run_id: UUID, user: UserContext) -> AgentRun | None:
        row = self._fetchone(
            """
            SELECT id, conversation_id, acting_user_id, actor_kind, agent_name, status,
                   started_at, completed_at, error, request_id
            FROM agent_run WHERE id = %(id)s AND acting_user_id = %(user_id)s
            """,
            {"id": run_id, "user_id": user.id},
        )
        return _row_to_run(row) if row else None

    def list_runs(self, conversation_id: UUID, user: UserContext) -> Sequence[AgentRun]:
        if self.get_conversation(conversation_id, user) is None:
            return ()
        rows = self._fetchall(
            """
            SELECT id, conversation_id, acting_user_id, actor_kind, agent_name, status,
                   started_at, completed_at, error, request_id
            FROM agent_run WHERE conversation_id = %(conversation_id)s
            ORDER BY started_at ASC, id ASC
            """,
            {"conversation_id": conversation_id},
        )
        return tuple(_row_to_run(row) for row in rows)

    def save_tool_call(self, tool_call: AgentToolCall) -> None:
        from goldenage.adapters.postgres import Jsonb

        jsonb = cast(Any, Jsonb)

        self._execute(
            """
            INSERT INTO agent_tool_call
                (id, conversation_id, run_id, acting_user_id, actor_kind, agent_name,
                 tool_name, idempotency_key, input_json, status, output_json, error,
                 created_at, completed_at)
            VALUES (%(id)s, %(conversation_id)s, %(run_id)s, %(acting_user_id)s,
                    %(actor_kind)s, %(agent_name)s, %(tool_name)s, %(idempotency_key)s,
                    %(input_json)s, %(status)s, %(output_json)s, %(error)s,
                    %(created_at)s, %(completed_at)s)
            ON CONFLICT (id) DO UPDATE SET status = EXCLUDED.status,
                output_json = EXCLUDED.output_json, error = EXCLUDED.error,
                completed_at = EXCLUDED.completed_at
            """,
            {
                **_tool_call_params(tool_call),
                "input_json": jsonb(tool_call.input_json),
                "output_json": jsonb(tool_call.output_json)
                if tool_call.output_json is not None
                else None,
            },
        )

    def get_tool_call_by_idempotency(
        self,
        *,
        conversation_id: UUID,
        idempotency_key: str,
        user: UserContext,
    ) -> AgentToolCall | None:
        if self.get_conversation(conversation_id, user) is None:
            return None
        row = self._fetchone(
            """
            SELECT id, conversation_id, run_id, acting_user_id, actor_kind, agent_name,
                   tool_name, idempotency_key, input_json, status, output_json, error,
                   created_at, completed_at
            FROM agent_tool_call
            WHERE conversation_id = %(conversation_id)s AND acting_user_id = %(user_id)s
              AND idempotency_key = %(idempotency_key)s
            ORDER BY created_at ASC LIMIT 1
            """,
            {
                "conversation_id": conversation_id,
                "user_id": user.id,
                "idempotency_key": idempotency_key,
            },
        )
        return _row_to_tool_call(row) if row else None

    def list_tool_calls(self, run_id: UUID, user: UserContext) -> Sequence[AgentToolCall]:
        run = self.get_run(run_id, user)
        if run is None:
            return ()
        rows = self._fetchall(
            """
            SELECT id, conversation_id, run_id, acting_user_id, actor_kind, agent_name,
                   tool_name, idempotency_key, input_json, status, output_json, error,
                   created_at, completed_at
            FROM agent_tool_call WHERE run_id = %(run_id)s
            ORDER BY created_at ASC, id ASC
            """,
            {"run_id": run_id},
        )
        return tuple(_row_to_tool_call(row) for row in rows)

    def _execute(self, sql: str, params: dict[str, object]) -> None:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(sql, params)
            connection.commit()

    def _fetchone(self, sql: str, params: dict[str, object]) -> dict[str, object] | None:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(sql, params)
            return cursor.fetchone()

    def _fetchall(self, sql: str, params: dict[str, object]) -> list[dict[str, object]]:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(sql, params)
            return list(cursor.fetchall())


def _serialize_datetime(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _deserialize_datetime(value: str | datetime | None) -> datetime | None:
    if value is None or isinstance(value, datetime):
        return value
    return datetime.fromisoformat(value)


def _conversation_params(conversation: AgentConversation) -> dict[str, object]:
    return {
        "id": conversation.id,
        "acting_user_id": conversation.acting_user_id,
        "agent_name": conversation.agent_name,
        "title": conversation.title,
        "created_at": conversation.created_at,
        "updated_at": conversation.updated_at,
    }


def _message_params(message: AgentMessage) -> dict[str, object]:
    return {
        "id": message.id,
        "conversation_id": message.conversation_id,
        "run_id": message.run_id,
        "role": message.role,
        "content": message.content,
        "created_at": message.created_at,
        "sequence": message.sequence,
    }


def _run_params(run: AgentRun) -> dict[str, object]:
    return {
        "id": run.id,
        "conversation_id": run.conversation_id,
        "acting_user_id": run.acting_user_id,
        "actor_kind": run.actor_kind,
        "agent_name": run.agent_name,
        "status": run.status,
        "started_at": run.started_at,
        "completed_at": run.completed_at,
        "error": run.error,
        "request_id": run.request_id,
    }


def _tool_call_params(tool_call: AgentToolCall) -> dict[str, object]:
    return {
        "id": tool_call.id,
        "conversation_id": tool_call.conversation_id,
        "run_id": tool_call.run_id,
        "acting_user_id": tool_call.acting_user_id,
        "actor_kind": tool_call.actor_kind,
        "agent_name": tool_call.agent_name,
        "tool_name": tool_call.tool_name,
        "idempotency_key": tool_call.idempotency_key,
        "status": tool_call.status,
        "error": tool_call.error,
        "created_at": tool_call.created_at,
        "completed_at": tool_call.completed_at,
    }


def _row_to_conversation(row) -> AgentConversation:
    return AgentConversation(
        id=UUID(str(row["id"])),
        acting_user_id=UUID(str(row["acting_user_id"])),
        agent_name=row["agent_name"],
        title=row["title"],
        created_at=_required_datetime(_deserialize_datetime(row["created_at"])),
        updated_at=_required_datetime(_deserialize_datetime(row["updated_at"])),
    )


def _row_to_message(row) -> AgentMessage:
    return AgentMessage(
        id=UUID(str(row["id"])),
        conversation_id=UUID(str(row["conversation_id"])),
        run_id=UUID(str(row["run_id"])) if row["run_id"] else None,
        role=row["role"],
        content=row["content"],
        created_at=_required_datetime(_deserialize_datetime(row["created_at"])),
        sequence=int(row["sequence"]),
    )


def _row_to_run(row) -> AgentRun:
    return AgentRun(
        id=UUID(str(row["id"])),
        conversation_id=UUID(str(row["conversation_id"])),
        acting_user_id=UUID(str(row["acting_user_id"])),
        actor_kind=row["actor_kind"],
        agent_name=row["agent_name"],
        status=row["status"],
        started_at=_required_datetime(_deserialize_datetime(row["started_at"])),
        completed_at=_deserialize_datetime(row["completed_at"]),
        error=row["error"],
        request_id=row["request_id"],
    )


def _row_to_tool_call(row) -> AgentToolCall:
    return AgentToolCall(
        id=UUID(str(row["id"])),
        conversation_id=UUID(str(row["conversation_id"])),
        run_id=UUID(str(row["run_id"])),
        acting_user_id=UUID(str(row["acting_user_id"])),
        actor_kind=row["actor_kind"],
        agent_name=row["agent_name"],
        tool_name=row["tool_name"],
        idempotency_key=row["idempotency_key"],
        input_json=_decode_json(row["input_json"]),
        status=row["status"],
        output_json=_decode_json(row["output_json"]) if row["output_json"] is not None else None,
        error=row["error"],
        created_at=_required_datetime(_deserialize_datetime(row["created_at"])),
        completed_at=_deserialize_datetime(row["completed_at"]),
    )


def _decode_json(value) -> dict[str, object]:
    if isinstance(value, str):
        return json.loads(value)
    return dict(value)


def _required_datetime(value: datetime | None) -> datetime:
    if value is None:
        raise ValueError("Agent persistence returned a missing timestamp.")
    return value
