"""RAG 知识库数据结构。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class RagChunk:
    """单个知识块。"""

    id: str
    source_path: str
    chunk_index: int
    content: str
    score: float = 0.0
