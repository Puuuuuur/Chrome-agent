"""聊天记忆 PostgreSQL 存储层。"""

from __future__ import annotations

import json
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Iterator

try:
    import psycopg
    from psycopg.rows import dict_row
except ImportError:  # pragma: no cover - 依赖缺失时交由运行时错误处理
    psycopg = None
    dict_row = None

from .models import ChatMessageRecord

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS chat_sessions (
    id TEXT PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    title TEXT NOT NULL DEFAULT '',
    rolling_summary TEXT NOT NULL DEFAULT '',
    slots_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    turn_count INTEGER NOT NULL DEFAULT 0,
    archived_until_seq BIGINT NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS chat_messages (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE,
    seq BIGINT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    meta_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(session_id, seq)
);

CREATE INDEX IF NOT EXISTS idx_chat_sessions_updated_at
    ON chat_sessions(updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_chat_messages_session_seq
    ON chat_messages(session_id, seq DESC);
"""


@dataclass
class SessionRow:
    """会话表的轻量映射。"""

    id: str
    title: str
    rolling_summary: str
    slots: dict[str, Any]
    turn_count: int
    archived_until_seq: int


class PostgresChatMemoryStore:
    """会话记忆 PostgreSQL 存储。"""

    def __init__(self, dsn: str, *, connect_timeout: int = 5):
        if psycopg is None:
            raise RuntimeError("缺少 psycopg 依赖；请先安装 PostgreSQL 驱动。")
        self._dsn = str(dsn or "").strip()
        if not self._dsn:
            raise RuntimeError("聊天记忆 PostgreSQL DSN 为空。")
        self._connect_timeout = max(1, int(connect_timeout))
        self._schema_ready = False

    @contextmanager
    def _connect(self) -> Iterator[Any]:
        connection = psycopg.connect(
            self._dsn,
            connect_timeout=self._connect_timeout,
            row_factory=dict_row,
        )
        try:
            yield connection
        finally:
            connection.close()

    def ensure_schema(self) -> None:
        """初始化数据库表结构。"""
        if self._schema_ready:
            return
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(SCHEMA_SQL)
            connection.commit()
        self._schema_ready = True

    def healthcheck(self) -> None:
        """做一轮轻量连通性探测。"""
        self.ensure_schema()
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1 AS ok")
                cursor.fetchone()

    def get_or_create_session(self, session_id: str) -> tuple[SessionRow, bool]:
        """读取会话；不存在时自动创建。"""
        self.ensure_schema()
        normalized = str(session_id or "").strip()
        if not normalized:
            raise RuntimeError("session_id 不能为空。")
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO chat_sessions (id)
                    VALUES (%s)
                    ON CONFLICT (id) DO NOTHING
                    """,
                    (normalized,),
                )
                created = bool(cursor.rowcount)
                cursor.execute(
                    """
                    SELECT id, title, rolling_summary, slots_json, turn_count, archived_until_seq
                    FROM chat_sessions
                    WHERE id = %s
                    """,
                    (normalized,),
                )
                row = cursor.fetchone()
            connection.commit()
        if not isinstance(row, dict):
            raise RuntimeError(f"未能读取会话：{normalized}")
        return self._session_from_row(row), created

    def load_recent_messages(self, session_id: str, *, limit: int) -> list[ChatMessageRecord]:
        """读取最近 N 条消息。"""
        self.ensure_schema()
        normalized = str(session_id or "").strip()
        if not normalized:
            return []
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT id, session_id, seq, role, content, meta_json, created_at::text AS created_at
                    FROM chat_messages
                    WHERE session_id = %s
                    ORDER BY seq DESC
                    LIMIT %s
                    """,
                    (normalized, max(1, int(limit))),
                )
                rows = list(cursor.fetchall() or [])
        records = [self._message_from_row(row) for row in reversed(rows)]
        return records

    def load_unsummarized_messages(
        self,
        session_id: str,
        *,
        from_seq_exclusive: int,
        to_seq_inclusive: int,
    ) -> list[ChatMessageRecord]:
        """读取尚未进入摘要区间的消息。"""
        self.ensure_schema()
        if int(to_seq_inclusive) <= int(from_seq_exclusive):
            return []
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT id, session_id, seq, role, content, meta_json, created_at::text AS created_at
                    FROM chat_messages
                    WHERE session_id = %s
                      AND seq > %s
                      AND seq <= %s
                    ORDER BY seq ASC
                    """,
                    (
                        str(session_id or "").strip(),
                        int(from_seq_exclusive),
                        int(to_seq_inclusive),
                    ),
                )
                rows = list(cursor.fetchall() or [])
        return [self._message_from_row(row) for row in rows]

    def append_message(
        self,
        session_id: str,
        *,
        message_id: str,
        role: str,
        content: str,
        meta: dict[str, Any] | None = None,
    ) -> ChatMessageRecord:
        """追加一条会话消息，并顺带推进 session 计数。"""
        self.ensure_schema()
        normalized_session_id = str(session_id or "").strip()
        if not normalized_session_id:
            raise RuntimeError("session_id 不能为空。")
        normalized_message_id = str(message_id or "").strip()
        if not normalized_message_id:
            raise RuntimeError("message_id 不能为空。")
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO chat_sessions (id)
                    VALUES (%s)
                    ON CONFLICT (id) DO NOTHING
                    """,
                    (normalized_session_id,),
                )
                cursor.execute(
                    """
                    SELECT turn_count
                    FROM chat_sessions
                    WHERE id = %s
                    FOR UPDATE
                    """,
                    (normalized_session_id,),
                )
                session_row = cursor.fetchone()
                if not isinstance(session_row, dict):
                    raise RuntimeError(f"未能锁定会话：{normalized_session_id}")
                next_seq = int(session_row.get("turn_count") or 0) + 1
                cursor.execute(
                    """
                    INSERT INTO chat_messages (id, session_id, seq, role, content, meta_json)
                    VALUES (%s, %s, %s, %s, %s, %s::jsonb)
                    RETURNING id, session_id, seq, role, content, meta_json, created_at::text AS created_at
                    """,
                    (
                        normalized_message_id,
                        normalized_session_id,
                        next_seq,
                        str(role or "").strip() or "user",
                        str(content or ""),
                        json.dumps(meta or {}, ensure_ascii=False),
                    ),
                )
                message_row = cursor.fetchone()
                cursor.execute(
                    """
                    UPDATE chat_sessions
                    SET turn_count = %s,
                        updated_at = NOW()
                    WHERE id = %s
                    """,
                    (next_seq, normalized_session_id),
                )
            connection.commit()
        if not isinstance(message_row, dict):
            raise RuntimeError("消息写入失败。")
        return self._message_from_row(message_row)

    def update_session_state(
        self,
        session_id: str,
        *,
        rolling_summary: str | None = None,
        slots: dict[str, Any] | None = None,
        archived_until_seq: int | None = None,
        title: str | None = None,
    ) -> SessionRow:
        """更新会话摘要、槽位和摘要游标。"""
        self.ensure_schema()
        normalized = str(session_id or "").strip()
        with self._connect() as connection:
            with connection.cursor() as cursor:
                assignments: list[str] = ["updated_at = NOW()"]
                values: list[Any] = []
                if rolling_summary is not None:
                    assignments.append("rolling_summary = %s")
                    values.append(str(rolling_summary or ""))
                if slots is not None:
                    assignments.append("slots_json = %s::jsonb")
                    values.append(json.dumps(slots or {}, ensure_ascii=False))
                if archived_until_seq is not None:
                    assignments.append("archived_until_seq = %s")
                    values.append(max(0, int(archived_until_seq)))
                if title is not None:
                    assignments.append("title = %s")
                    values.append(str(title or ""))
                values.append(normalized)
                cursor.execute(
                    f"""
                    UPDATE chat_sessions
                    SET {", ".join(assignments)}
                    WHERE id = %s
                    RETURNING id, title, rolling_summary, slots_json, turn_count, archived_until_seq
                    """,
                    tuple(values),
                )
                row = cursor.fetchone()
            connection.commit()
        if not isinstance(row, dict):
            raise RuntimeError(f"更新会话状态失败：{normalized}")
        return self._session_from_row(row)

    @staticmethod
    def _session_from_row(row: dict[str, Any]) -> SessionRow:
        slots = row.get("slots_json")
        if not isinstance(slots, dict):
            slots = {}
        return SessionRow(
            id=str(row.get("id") or ""),
            title=str(row.get("title") or ""),
            rolling_summary=str(row.get("rolling_summary") or ""),
            slots=slots,
            turn_count=int(row.get("turn_count") or 0),
            archived_until_seq=int(row.get("archived_until_seq") or 0),
        )

    @staticmethod
    def _message_from_row(row: dict[str, Any]) -> ChatMessageRecord:
        meta = row.get("meta_json")
        if not isinstance(meta, dict):
            meta = {}
        return ChatMessageRecord(
            id=str(row.get("id") or ""),
            session_id=str(row.get("session_id") or ""),
            seq=int(row.get("seq") or 0),
            role=str(row.get("role") or ""),
            content=str(row.get("content") or ""),
            meta=meta,
            created_at=str(row.get("created_at") or ""),
        )
