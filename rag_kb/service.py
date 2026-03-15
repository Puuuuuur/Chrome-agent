"""PDF RAG 知识库服务。"""

from __future__ import annotations

import hashlib
import math
from pathlib import Path
from typing import Any

try:
    from pypdf import PdfReader
except ImportError:  # pragma: no cover
    PdfReader = None

from tools.tool_model_client import build_openai_client
from 智能体配置 import (
    RAG_CHUNK_OVERLAP,
    RAG_CHUNK_SIZE,
    RAG_EMBEDDING_DIMENSION,
    RAG_EMBEDDING_MODEL,
    RAG_EMBEDDING_PROVIDER,
    RAG_MILVUS_COLLECTION,
    RAG_MILVUS_URI,
    RAG_SOURCE_DIR,
    RAG_TOP_K,
)

from .milvus_store import RagMilvusStore
from .models import RagChunk


class RagKnowledgeService:
    """管理本地 PDF 知识库及 Milvus 检索。"""

    def __init__(self) -> None:
        self._source_dir = Path(RAG_SOURCE_DIR).expanduser()
        self._store = RagMilvusStore(
            uri=RAG_MILVUS_URI,
            collection_name=RAG_MILVUS_COLLECTION,
            embedding_dimension=RAG_EMBEDDING_DIMENSION,
        )
        self._ready = False
        self._doc_count = 0
        self._source_signature = ""

    def ensure_ready(self) -> None:
        if self._ready:
            return
        self._source_dir.mkdir(parents=True, exist_ok=True)
        self._store.ensure_collection()
        self._sync_source_pdfs()
        self._ready = True

    def healthcheck(self) -> None:
        self.ensure_ready()
        self._store.healthcheck()

    def runtime_metadata(self) -> dict[str, str]:
        return {
            "rag_enabled": "1",
            "rag_source_dir": str(self._source_dir),
            "rag_milvus_uri": str(RAG_MILVUS_URI),
            "rag_collection": str(RAG_MILVUS_COLLECTION),
            "rag_doc_count": str(self._doc_count),
            "rag_top_k": str(RAG_TOP_K),
        }

    def search_context(self, query: str) -> tuple[str, list[RagChunk]]:
        """搜索知识库并生成 prompt 注入文本。"""
        self.ensure_ready()
        self._maybe_refresh_index()
        normalized_query = str(query or "").strip()
        if not normalized_query:
            return "", []
        vector = self._embed_text(normalized_query)
        if not vector:
            return "", []
        hits = self._store.search(vector=vector, limit=RAG_TOP_K)
        valid_hits = [item for item in hits if str(item.content or "").strip()]
        if not valid_hits:
            return "", []
        lines = ["本地 RAG 知识库命中："]
        for item in valid_hits:
            source_name = Path(item.source_path).name or item.source_path
            lines.append(f"- 来源：{source_name} / chunk {item.chunk_index}")
            lines.append(f"  内容：{item.content}")
        return "\n".join(lines).strip(), valid_hits

    def _sync_source_pdfs(self) -> None:
        if PdfReader is None:
            raise RuntimeError("缺少 pypdf 依赖；请先安装 PDF 解析库。")
        pdf_paths = sorted(self._source_dir.glob("*.pdf"))
        chunk_payloads: list[dict[str, Any]] = []
        for pdf_path in pdf_paths:
            extracted_text = self._extract_pdf_text(pdf_path)
            for chunk_index, chunk_text in enumerate(self._chunk_text(extracted_text), start=1):
                chunk_id = self._chunk_id(pdf_path, chunk_index, chunk_text)
                chunk_payloads.append(
                    {
                        "id": chunk_id,
                        "source_path": str(pdf_path),
                        "chunk_index": chunk_index,
                        "content": chunk_text[:8192],
                        "vector": self._embed_text(chunk_text),
                    }
                )
        self._store.upsert_chunks(chunk_payloads)
        self._doc_count = len(pdf_paths)
        self._source_signature = self._build_source_signature(pdf_paths)

    def _maybe_refresh_index(self) -> None:
        pdf_paths = sorted(self._source_dir.glob("*.pdf"))
        latest_signature = self._build_source_signature(pdf_paths)
        if latest_signature != self._source_signature:
            self._sync_source_pdfs()

    def _extract_pdf_text(self, pdf_path: Path) -> str:
        reader = PdfReader(str(pdf_path))
        parts: list[str] = []
        for page in reader.pages:
            try:
                text = page.extract_text() or ""
            except Exception:
                text = ""
            if text.strip():
                parts.append(text.strip())
        metadata = getattr(reader, "metadata", None)
        if metadata:
            for key in ("/Subject", "/Title", "/Keywords", "/Author", "/Creator"):
                value = metadata.get(key)
                if value:
                    parts.append(str(value).strip())
        return "\n".join(part for part in parts if part).strip()

    def _chunk_text(self, text: str) -> list[str]:
        normalized = str(text or "").strip()
        if not normalized:
            return []
        chunk_size = max(80, int(RAG_CHUNK_SIZE))
        overlap = max(0, min(int(RAG_CHUNK_OVERLAP), chunk_size // 2))
        chunks: list[str] = []
        start = 0
        while start < len(normalized):
            end = min(len(normalized), start + chunk_size)
            chunk = normalized[start:end].strip()
            if chunk:
                chunks.append(chunk)
            if end >= len(normalized):
                break
            start = max(start + 1, end - overlap)
        return chunks

    def _chunk_id(self, pdf_path: Path, chunk_index: int, chunk_text: str) -> str:
        digest = hashlib.sha1(f"{pdf_path}:{chunk_index}:{chunk_text}".encode("utf-8")).hexdigest()
        return f"rag_{digest}"

    def _build_source_signature(self, pdf_paths: list[Path]) -> str:
        payload = "|".join(
            f"{path.resolve()}:{path.stat().st_mtime_ns}:{path.stat().st_size}"
            for path in pdf_paths
            if path.exists()
        )
        return hashlib.sha1(payload.encode("utf-8")).hexdigest()

    def _embed_text(self, text: str) -> list[float]:
        normalized = str(text or "").strip()
        if not normalized:
            return []
        if RAG_EMBEDDING_PROVIDER == "openai":
            try:
                client = build_openai_client()
                response = client.embeddings.create(
                    model=RAG_EMBEDDING_MODEL,
                    input=[normalized],
                )
                data = list(getattr(response, "data", []) or [])
                if data and getattr(data[0], "embedding", None):
                    return [float(item) for item in list(data[0].embedding)]
            except Exception:
                pass
        return self._hash_embedding(normalized)

    def _hash_embedding(self, text: str) -> list[float]:
        tokens = [char for char in str(text or "").lower() if not char.isspace()]
        dimension = RAG_EMBEDDING_DIMENSION
        vector = [0.0] * dimension
        if not tokens:
            return vector
        for index in range(max(1, len(tokens) - 2)):
            ngram = "".join(tokens[index : index + 3]) or tokens[index]
            digest = hashlib.sha256(ngram.encode("utf-8")).digest()
            bucket = int.from_bytes(digest[:4], "big") % dimension
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vector[bucket] += sign
        norm = math.sqrt(sum(item * item for item in vector))
        if norm <= 0:
            return vector
        return [item / norm for item in vector]
