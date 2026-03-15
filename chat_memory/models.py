"""聊天记忆模型定义。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4


@dataclass
class ChatMessageRecord:
    """数据库层读取出的单条聊天消息。"""

    id: str
    session_id: str
    seq: int
    role: str
    content: str
    meta: dict[str, Any]
    created_at: str


@dataclass
class ConversationMemoryContext:
    """一次对话请求对应的记忆上下文。"""

    session_id: str
    created: bool = False
    rolling_summary: str = ""
    recent_messages: list[ChatMessageRecord] = field(default_factory=list)
    slots: dict[str, Any] = field(default_factory=dict)
    turn_count: int = 0

    def prompt_block(self) -> str:
        """把摘要、槽位和语义记忆格式化成可注入 prompt 的文本。"""
        lines: list[str] = []
        if str(self.rolling_summary or "").strip():
            lines.append("历史摘要：")
            lines.append(str(self.rolling_summary).strip())

        slot_lines: list[str] = []
        for key, value in (self.slots or {}).items():
            if value is None:
                continue
            text = str(value).strip()
            if not text:
                continue
            slot_lines.append(f"- {key}: {text}")
        if slot_lines:
            lines.append("结构化记忆：")
            lines.extend(slot_lines)

        return "\n".join(lines).strip()


def generate_session_id(prefix: str = "sess") -> str:
    """生成一条稳定可读的 session id。"""
    return f"{prefix}_{uuid4().hex}"
