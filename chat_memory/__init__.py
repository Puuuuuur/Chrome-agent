"""聊天记忆对外导出。"""

from __future__ import annotations

from typing import Any

from 智能体配置 import CHAT_MEMORY_ENABLED, CHAT_MEMORY_POSTGRES_DSN

from .models import ConversationMemoryContext, generate_session_id
from .service import ConversationMemoryService

__all__ = [
    "ConversationMemoryContext",
    "ConversationMemoryService",
    "generate_session_id",
    "get_conversation_memory_service",
]

_SERVICE: ConversationMemoryService | None | bool = None


def get_conversation_memory_service() -> ConversationMemoryService | None:
    """懒加载聊天记忆服务；未启用时返回 None。"""
    global _SERVICE
    if not CHAT_MEMORY_ENABLED or not str(CHAT_MEMORY_POSTGRES_DSN or "").strip():
        return None
    if _SERVICE is False:
        return None
    if _SERVICE is None:
        try:
            _SERVICE = ConversationMemoryService()
        except Exception:
            _SERVICE = False
            return None
    return _SERVICE if isinstance(_SERVICE, ConversationMemoryService) else None
