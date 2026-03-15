"""RAG 知识库的 Milvus 存储层。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

try:
    from pymilvus import DataType, MilvusClient
except ImportError:  # pragma: no cover
    DataType = None
    MilvusClient = None

from .models import RagChunk


class RagMilvusStore:
    """保存知识块向量并支持语义检索。"""

    def __init__(self, *, uri: str, collection_name: str, embedding_dimension: int):
        if MilvusClient is None or DataType is None:
            raise RuntimeError("缺少 pymilvus 依赖；请先安装 Milvus 客户端。")
        self._uri = str(uri or "").strip()
        if not self._uri:
            raise RuntimeError("RAG Milvus URI 不能为空。")
        if self._uri.endswith(".db"):
            Path(self._uri).expanduser().parent.mkdir(parents=True, exist_ok=True)
        self._collection_name = str(collection_name or "").strip() or "playwright_agent_rag_knowledge"
        self._embedding_dimension = max(8, int(embedding_dimension))
        self._client = MilvusClient(uri=self._uri)
        self._ready = False

    def ensure_collection(self) -> None:
        """初始化 collection。"""
        if self._ready:
            return
        if self._client.has_collection(collection_name=self._collection_name):
            self._ready = True
            return

        schema = self._client.create_schema(auto_id=False, enable_dynamic_field=False)
        schema.add_field(field_name="id", datatype=DataType.VARCHAR, is_primary=True, max_length=160)
        schema.add_field(field_name="source_path", datatype=DataType.VARCHAR, max_length=1024)
        schema.add_field(field_name="chunk_index", datatype=DataType.INT64)
        schema.add_field(field_name="content", datatype=DataType.VARCHAR, max_length=8192)
        schema.add_field(field_name="vector", datatype=DataType.FLOAT_VECTOR, dim=self._embedding_dimension)

        index_params = self._client.prepare_index_params()
        index_params.add_index(
            field_name="vector",
            index_name="idx_vector",
            metric_type="COSINE",
            index_type="AUTOINDEX",
        )

        self._client.create_collection(
            collection_name=self._collection_name,
            schema=schema,
            index_params=index_params,
        )
        self._ready = True

    def healthcheck(self) -> None:
        self.ensure_collection()
        self._client.describe_collection(collection_name=self._collection_name)

    def upsert_chunks(self, chunks: list[dict[str, Any]]) -> None:
        self.ensure_collection()
        if not chunks:
            return
        self._client.upsert(collection_name=self._collection_name, data=chunks)

    def search(self, *, vector: list[float], limit: int) -> list[RagChunk]:
        self.ensure_collection()
        search_result = self._client.search(
            collection_name=self._collection_name,
            data=[[float(item) for item in vector]],
            limit=max(1, int(limit)),
            output_fields=["id", "source_path", "chunk_index", "content"],
        )
        hits = list(search_result[0] or []) if search_result else []
        records: list[RagChunk] = []
        for hit in hits:
            entity = dict(hit.get("entity") or {})
            records.append(
                RagChunk(
                    id=str(entity.get("id") or hit.get("id") or ""),
                    source_path=str(entity.get("source_path") or ""),
                    chunk_index=int(entity.get("chunk_index") or 0),
                    content=str(entity.get("content") or ""),
                    score=float(hit.get("distance") or hit.get("score") or 0.0),
                )
            )
        return records
