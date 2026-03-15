# 法务浏览器 Agent

这个目录现在是独立运行的法务浏览器 Agent，目标是在本地或 Linux 宿主环境直接验证：

- `/playwright-agent/` 页面
- `/api/playwright-agent/chat` 接口
- `/api/creditchina/query` 固定查询接口
- `run_creditchina_query_and_save`
- `run_creditchina_private_api_query_and_save`

## 目录

- `app.py`
  - 最小 Flask 启动器
- `对话智能体.py`、`智能体调度.py`、`agent工具.py`、`模型工具.py`、`验证码工具.py`、`智能体配置.py`
  - 法务智能体核心代码，直接放在 `浏览器agent/` 根目录
- `docs/`
  - 学习和设计文档；先看 `docs/代码结构总览.md`，再看 `docs/信用中国_API化改造方案.md`
- `requirements.txt`
  - Python 依赖
- `.env.example`
  - 环境变量示例
- `start_mac.sh`
  - 一键本地启动
- `start_linux_host.sh`
  - Linux 宿主部署：在同一宿主环境内拉起浏览器和智能体
- `launch_chrome_cdp_mac.sh`
  - 可选：以 CDP 模式启动本机 Chrome

## 推荐阅读顺序

1. `docs/代码结构总览.md`
2. `app.py`
3. `对话智能体.py`
4. `智能体调度.py`
5. `agent工具.py`
6. `模型工具.py`、`验证码工具.py`、`智能体配置.py`
7. `docs/信用中国_API化改造方案.md`

## 前置要求

1. macOS 上已安装 `Python 3.11+`
2. 已安装 `Google Chrome`
3. 你有可用的 `OPENAI_API_KEY`，或已经配置 `~/.codex/auth.json`

## 启动步骤

1. 解压 zip
2. 复制 `.env.example` 为 `.env`
3. 在 `.env` 里填入你的 `OPENAI_API_KEY`（如果你已经有 `~/.codex/auth.json`，这一步也可以跳过）
4. 执行：

```bash
chmod +x start_mac.sh launch_chrome_cdp_mac.sh
./start_mac.sh
```

5. 打开：

```text
http://127.0.0.1:8800/playwright-agent/
```

## 推荐运行模式

推荐在 Mac 上直接接管你常驻的真实 Chrome：

- `PLAYWRIGHT_AGENT_BROWSER_MODE=connect_over_cdp_or_launch`
- `PLAYWRIGHT_AGENT_CDP_URL=http://127.0.0.1:9222`
- `PLAYWRIGHT_AGENT_CDP_ATTACH_EXISTING_PAGE=1`
- `PLAYWRIGHT_AGENT_LAUNCH_HEADLESS=0`
- `PLAYWRIGHT_AGENT_AUTO_XVFB=0`

如果 `9222` 上已经有你常驻的 Chrome，智能体会优先接管它；如果没有，再自动回退到本地直接拉起 Chrome。

## 可选：接管已打开的真实 Chrome

如果你希望尽量复用真实浏览器资料、登录态、插件和本地环境，直接用 CDP 模式即可：

1. 先运行：

```bash
./launch_chrome_cdp_mac.sh
```

2. 然后确保 `.env` 里是：

```bash
PLAYWRIGHT_AGENT_BROWSER_MODE=connect_over_cdp_or_launch
PLAYWRIGHT_AGENT_CDP_URL=http://127.0.0.1:9222
PLAYWRIGHT_AGENT_CDP_ATTACH_EXISTING_PAGE=1
```

3. 再执行：

```bash
./start_mac.sh
```

## Linux 宿主部署

如果你希望在 Linux 宿主机上直接部署，并让法务智能体优先接管同机 Chrome/Chromium：

1. 确保已安装：
   - `Python 3.10+`
   - `Google Chrome` 或 `Chromium`
   - 如果宿主机没有桌面显示环境，额外安装 `Xvfb`
2. 复制 `.env.example` 为 `.env`，并按需改成 Linux 路径，例如：

```bash
PLAYWRIGHT_AGENT_DEPLOYMENT_MODE=host
PLAYWRIGHT_AGENT_BROWSER_MODE=connect_over_cdp_or_launch
PLAYWRIGHT_AGENT_CDP_URL=http://127.0.0.1:9222
PLAYWRIGHT_AGENT_LAUNCH_HEADLESS=0
PLAYWRIGHT_AGENT_AUTO_XVFB=1
PLAYWRIGHT_AGENT_BROWSER_EXECUTABLE=/usr/bin/google-chrome
```

3. 执行：

```bash
chmod +x start_linux_host.sh
./start_linux_host.sh
```

这个脚本会按顺序：

- 在宿主环境里准备 Python/依赖
- 在没有 `DISPLAY` 时自动启动 `Xvfb`
- 优先探测 `http://127.0.0.1:9222`
- 如果本机没有现成 CDP 浏览器，就直接在同一宿主环境里启动一个带 `--remote-debugging-port` 的 Chrome/Chromium
- 最后启动 Flask 智能体服务

这样部署后，Linux 默认优先走本机 `127.0.0.1:9222`，不再先绕 `host.docker.internal`。

## 直接固定查询接口

如果你不想先走聊天页面，也可以直接调用固定查询接口：

```bash
curl -X POST http://127.0.0.1:8800/api/creditchina/query \
  -H 'Content-Type: application/json' \
  -d '{
    "credit_code": "91420000177570439L",
    "browser_mode": "connect_over_cdp_or_launch",
    "cdp_url": "http://127.0.0.1:9222"
  }'
```

返回里会包含：

- 查询摘要文本
- 结构化企业信息
- 调试信息
- 保存下来的结果文件路径

## 结果文件位置

- 会话态：`./.session/`
- `.session/*.storage_state.json` 会保存浏览器会话 cookie / storage state，不适合外发
- 结果总目录：`../浏览器agent生成文件/`
- 当前固定任务目录：`../浏览器agent生成文件/信用中国查询/`
- 保存结构：`../浏览器agent生成文件/信用中国查询/YYYY-MM-DD/本次运行目录/`
- 旧的 `.session/results/` 如果还存在，属于历史目录遗留，不再是当前默认输出位置
- 每次目录内会包含：
  - `*.json`
  - `*.txt`
  - `*.html`
  - `*.png`
- `json/txt` 里的敏感调试字段会做基础脱敏；`html` 仍是原始快照，外发前需要自行检查

## 已知结论

这次在云服务器里没有跑通 `creditchina`，核心原因不是代码链路，而是目标站对当前云出口/IP/环境有风控拦截。

把法务智能体搬到你的 Mac 上，本质上是在换：

- 浏览器环境
- 网络出口
- 用户画像

这比“继续留在阿里云宿主机/容器里”更有希望跑通。

## 建议测试顺序

1. 先打开 `/playwright-agent/`，确认页面状态是“已就绪”
2. 先发一句“你好”，确认模型链路正常
3. 再测：

```text
请直接调用 run_creditchina_query_and_save，访问 https://www.creditchina.gov.cn/ ，用默认统一社会信用代码完成一次固定查询，并告诉我最终结果。
```

4. 如果仍被挑战，再切到 CDP 模式重试
