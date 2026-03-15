# RAG 知识库说明

## 目标

当前浏览器 Agent 已支持本地 PDF RAG：

- 把 `rag_store/source_pdfs/` 里的 PDF 建立向量索引
- 用户提问时先做本地知识库检索
- 命中后把检索结果注入聊天 prompt
- 模型优先依据本地知识库回答

## 目录

- `rag_store/source_pdfs/`
  - 放原始 PDF
- `rag_kb/`
  - `service.py`
    - 负责扫描 PDF、切分文本、生成向量、检索
  - `milvus_store.py`
    - 负责 Milvus / Milvus Lite collection
  - `models.py`
    - RAG chunk 结构

## 当前示例 PDF

当前交付里已经包含：

- `rag_store/source_pdfs/shaoqun_profile.pdf`

内容是：

```text
石劭群是中国海洋大学的研二学生，住在听4号楼402房间。
```

## 环境变量

```bash
PLAYWRIGHT_AGENT_RAG_ENABLED=1
PLAYWRIGHT_AGENT_RAG_SOURCE_DIR=./rag_store/source_pdfs
PLAYWRIGHT_AGENT_RAG_MILVUS_URI=http://127.0.0.1:19530
PLAYWRIGHT_AGENT_RAG_MILVUS_COLLECTION=playwright_agent_rag_knowledge
PLAYWRIGHT_AGENT_RAG_TOP_K=3
PLAYWRIGHT_AGENT_RAG_CHUNK_SIZE=320
PLAYWRIGHT_AGENT_RAG_CHUNK_OVERLAP=60
PLAYWRIGHT_AGENT_RAG_EMBEDDING_PROVIDER=openai
PLAYWRIGHT_AGENT_RAG_EMBEDDING_MODEL=text-embedding-3-small
PLAYWRIGHT_AGENT_RAG_EMBEDDING_DIMENSION=1536
```

## 独立 Milvus 服务

当前交付改为 **独立 Milvus Standalone**，部署文件在：

- `deploy/milvus/docker-compose.yml`

宿主机 systemd 单元：

- `browseragent-milvus.service`

默认对外端口：

- gRPC / SDK：`127.0.0.1:19530`
- Health：`127.0.0.1:19091`

## 说明

- 当前会话记忆仍然使用 PostgreSQL
- 当前已把会话记忆向量检索关闭，避免和 RAG 共用同一条向量职责

## 验收问题

建议直接提问：

```text
石劭群是谁？
石劭群住在哪里？
石劭群住哪个房间？
石劭群是哪个学校的什么学生？
```

预期应答应能命中：

- 中国海洋大学
- 研二学生
- 听4号楼402房间

## 当前验证结果

这轮开发已实际验证：

- `/healthz` 显示 `rag_enabled=1`
- `rag_doc_count=1`
- 主服务可正确回答：
  - “石劭群是谁？”
  - “石劭群住在哪里？”
  - “石劭群住哪个房间？”
