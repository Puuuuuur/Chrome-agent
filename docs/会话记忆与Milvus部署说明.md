# 会话记忆与 Milvus 部署说明

这个文档描述浏览器 Agent 新增的两层能力：

- **PostgreSQL 会话记忆**
  - 保存 `session_id`
  - 保存多轮消息
  - 保存滚动摘要与结构化槽位
- **Milvus / Milvus Lite**
  - 当前主要用于本地 PDF RAG 知识库
  - 会话记忆本身当前主要使用 PostgreSQL

## 新增目录

- `chat_memory/`
  - `models.py`
    - 记忆上下文与消息数据结构
  - `postgres_store.py`
    - PostgreSQL 会话/消息存储
  - `service.py`
    - 记忆协调层：加载上下文、写入消息、摘要
- `rag_kb/`
  - `service.py`
    - 扫描 PDF、索引到 Milvus、执行 RAG 检索

## 接入位置

- `app.py`
  - `/api/playwright-agent/chat` 现支持 `session_id`
- `智能体调度.py`
  - 聊天入口新增 `session_id`
- `skills/registry.py`
  - 在意图分发前加载记忆
  - 在 skill 执行结束后回写记忆
- `skills/skill_browser_react.py`
  - 模型输入不再只看当前一句话
  - 会注入最近多轮消息、滚动摘要、相关历史片段
- `对话智能体.py`
  - 页面端把 `session_id` 持久化在 `localStorage`
- `chat_cli.py`
  - CLI 端把 `session_id` 持久化在 `.session/chat_cli_state.json`

## PostgreSQL

表结构文件：

- `docs/会话记忆_PostgreSQL.sql`

建议先执行：

```bash
psql "postgresql://USER:PASSWORD@HOST:5432/DBNAME" -f docs/会话记忆_PostgreSQL.sql
```

### 必填环境变量

```bash
PLAYWRIGHT_AGENT_CHAT_MEMORY_ENABLED=1
PLAYWRIGHT_AGENT_CHAT_MEMORY_POSTGRES_HOST=127.0.0.1
PLAYWRIGHT_AGENT_CHAT_MEMORY_POSTGRES_PORT=5432
PLAYWRIGHT_AGENT_CHAT_MEMORY_POSTGRES_DB=browseragent_memory
PLAYWRIGHT_AGENT_CHAT_MEMORY_POSTGRES_USER=browseragent
PLAYWRIGHT_AGENT_CHAT_MEMORY_POSTGRES_PASSWORD=CHANGE_ME
PLAYWRIGHT_AGENT_CHAT_MEMORY_POSTGRES_SSLMODE=prefer
```

也可以直接给 DSN：

```bash
PLAYWRIGHT_AGENT_CHAT_MEMORY_POSTGRES_DSN=postgresql://browseragent:CHANGE_ME@127.0.0.1:5432/browseragent_memory?sslmode=prefer
```

## Milvus

当前交付改为 **独立 Milvus Standalone**。

部署文件：

- `deploy/milvus/docker-compose.yml`

宿主机 systemd 单元：

- `browseragent-milvus.service`

默认连接地址：

```bash
PLAYWRIGHT_AGENT_RAG_MILVUS_URI=http://127.0.0.1:19530
PLAYWRIGHT_AGENT_RAG_MILVUS_COLLECTION=playwright_agent_rag_knowledge
```

如果你已经有自己的 Milvus Standalone / Cluster，也可以把 URI 换成你自己的地址。

## 向量检索参数

```bash
PLAYWRIGHT_AGENT_RAG_ENABLED=1
PLAYWRIGHT_AGENT_RAG_SOURCE_DIR=./rag_store/source_pdfs
PLAYWRIGHT_AGENT_RAG_EMBEDDING_PROVIDER=openai
PLAYWRIGHT_AGENT_RAG_EMBEDDING_MODEL=text-embedding-3-small
PLAYWRIGHT_AGENT_RAG_EMBEDDING_DIMENSION=1536
PLAYWRIGHT_AGENT_RAG_TOP_K=3
```

### 说明

- 默认优先使用 OpenAI Embedding
- 如果 embedding 调用失败，会自动回退为本地哈希向量，保证 RAG 检索链路不至于直接报错
- 当前会话记忆不再依赖 Milvus，只使用 PostgreSQL

## 摘要参数

```bash
PLAYWRIGHT_AGENT_CHAT_MEMORY_RECENT_MESSAGES_LIMIT=8
PLAYWRIGHT_AGENT_CHAT_MEMORY_SUMMARY_TRIGGER_MESSAGES=12
PLAYWRIGHT_AGENT_CHAT_MEMORY_SUMMARY_KEEP_RECENT_MESSAGES=6
PLAYWRIGHT_AGENT_CHAT_MEMORY_SUMMARY_MODEL=gpt-5.4
```

逻辑是：

- 最近几轮原文直接带给模型
- 更早历史压缩进 `rolling_summary`
- 同时维护结构化槽位，如：
  - `last_credit_code`
  - `last_skill_name`
  - `last_subject_name`
  - `last_base_url`

## 页面与 CLI 行为

- 页面端：
  - 使用浏览器 `localStorage` 保存 `session_id`
  - 刷新页面后仍会复用原会话
- CLI 端：
  - 使用 `.session/chat_cli_state.json` 保存 `session_id`
  - 输入 `/new` 可生成新会话

## 交付说明

如果目标环境是 Linux 独立部署，建议交付时带上：

1. `docs/会话记忆_PostgreSQL.sql`
2. 这份 `docs/会话记忆与Milvus部署说明.md`
3. `.env.example`
4. `docs/requirements.txt`

这样到目标机器时，只需要：

1. 准备 PostgreSQL
2. 配好 `.env`
3. 选择 Milvus Lite 或远端 Milvus
4. 执行 `start_linux.sh`

## 验收建议

建议用同一个 `session_id` 连续发两轮消息验证：

```text
介绍一下中国海洋大学
是985吗
```

如果第二轮能正确承接第一轮主语，就说明记忆链路已经接通。
