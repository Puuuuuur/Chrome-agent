"""RAG 知识库对外导出。"""

from __future__ import annotations

from 智能体配置 import RAG_ENABLED

from .service import RagKnowledgeService

__all__ = [
    "RagKnowledgeService",
    "get_rag_knowledge_service",
]

_SERVICE: RagKnowledgeService | None | bool = None


def get_rag_knowledge_service() -> RagKnowledgeService | None:
    global _SERVICE
    if not RAG_ENABLED:
        return None
    if _SERVICE is False:
        return None
    if _SERVICE is None:
        try:
            _SERVICE = RagKnowledgeService()
        except Exception:
            _SERVICE = False
            return None
    return _SERVICE if isinstance(_SERVICE, RagKnowledgeService) else None
