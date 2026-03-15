# 风险信息浏览器 Agent

这个交付物是一个可独立运行的浏览器 Agent，当前对外交付的稳定能力主要有两类：

- 通用浏览器操作：`browser_react`
- 风险信息查询：`creditchina_query`
- 多轮会话记忆：`PostgreSQL + Milvus`
- 本地 PDF RAG：`Milvus + rag_store/source_pdfs`

对外入口：

- 页面：`/playwright-agent/`
- 聊天接口：`/api/playwright-agent/chat`
- skills 列表：`/api/playwright-agent/skills`
- 显式 skill 执行：`/api/playwright-agent/skills/run`
- 固定业务接口：`/api/creditchina/query`

## 目录

- `app.py`
  - Flask 服务入口
- `skills/`
  - 业务 skills 目录
  - `base.py`
  - `registry.py`
  - `skill_browser_react.py`
  - `skill_creditchina_query.py`
- `tools/`
  - 技术底座目录
  - `tool_browser_runtime.py`
  - `tool_captcha.py`
  - `tool_model_client.py`
- `chat_memory/`
  - 会话记忆目录
  - `models.py`
  - `postgres_store.py`
  - `service.py`
- `rag_kb/`
  - 本地 RAG 知识库目录
  - `models.py`
  - `milvus_store.py`
  - `service.py`
- `rag_store/`
  - 本地知识源目录
  - `source_pdfs/`
- `对话智能体.py`
  - 页面渲染层
- `智能体调度.py`
  - Python 侧同步调用入口
- `chat_cli.py`
  - 终端交互脚本
- `智能体配置.py`
  - 统一配置
- `docs/`
  - 说明文档
- `docs/requirements.txt`
  - 依赖清单
- `.env.example`
  - 环境变量示例
- `start_mac.sh`
  - Mac 一键启动脚本；必要时自动拉起本机 CDP Chrome
- `start_linux.sh`
  - Linux 一键启动脚本

## 推荐阅读顺序

1. `docs/代码结构总览.md`
2. `docs/信用中国查询业务逻辑说明.md`
3. `docs/会话记忆与Milvus部署说明.md`
4. `docs/RAG知识库说明.md`
5. `app.py`
6. `智能体调度.py`
7. `chat_cli.py`
8. `chat_memory/service.py`
9. `rag_kb/service.py`
10. `skills/registry.py`
11. `skills/skill_browser_react.py`
12. `skills/skill_creditchina_query.py`
13. `tools/tool_browser_runtime.py`
14. `tools/tool_model_client.py`
15. `tools/tool_captcha.py`

## 运行要求

- Python 3.10+
- Google Chrome / Chromium
- 可用的 `OPENAI_API_KEY`，或者已配置 `~/.codex/auth.json`

补充说明：

- `start_mac.sh` / `start_linux.sh` 会自动创建 `.venv` 并安装 `docs/requirements.txt`
- 可以先执行 `./start_mac.sh --check` 或 `./start_linux.sh --check` 做预检
- 如果缺少浏览器、Xvfb 或模型认证，脚本默认允许“降级启动”页面；此时页面可打开，但 `/healthz` 会返回 `ready=false`
- 如需严格模式，可设置 `PLAYWRIGHT_AGENT_ALLOW_DEGRADED_STARTUP=0`

## 启动

Mac：

```bash
chmod +x start_mac.sh
./start_mac.sh
```

仅做预检：

```bash
./start_mac.sh --check
```

Linux：

```bash
chmod +x start_linux.sh
./start_linux.sh
```

仅做预检：

```bash
./start_linux.sh --check
```

终端连续对话：

```bash
python3 chat_cli.py
```

## 推荐运行模式

推荐直接复用本机真实 Chrome：

```bash
PLAYWRIGHT_AGENT_BROWSER_MODE=connect_over_cdp_or_launch
PLAYWRIGHT_AGENT_CDP_URL=http://127.0.0.1:9222
PLAYWRIGHT_AGENT_CDP_ATTACH_EXISTING_PAGE=1
```

如果本机没有现成的 `9222` CDP 浏览器：

- `start_mac.sh` 会自动拉起一个本机 CDP Chrome
- `start_linux.sh` 会自动探测或拉起本机 CDP Chrome

## 结果目录

- 会话态：`./.session/`
- 运行结果：`../浏览器agent生成文件/信用中国查询/`

一次运行通常会生成：

- `*.json`
- `*.txt`
- `*.html`
- `*.png`

## 建议测试顺序

1. 打开 `/playwright-agent/`
2. 先发一句“你好”，确认模型链路正常
3. 再发：

```text
请访问 https://www.creditchina.gov.cn/ ，用默认统一社会信用代码完成一次信用中国查询，并告诉我最终结果。
```
