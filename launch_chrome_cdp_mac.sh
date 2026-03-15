#!/usr/bin/env bash
set -euo pipefail

CHROME_BIN="${PLAYWRIGHT_AGENT_BROWSER_EXECUTABLE:-/Applications/Google Chrome.app/Contents/MacOS/Google Chrome}"
PROFILE_DIR="${HOME}/.creditchina-chrome-debug"
CDP_PORT="${PLAYWRIGHT_AGENT_CDP_PORT:-9222}"
LOG_FILE="${TMPDIR:-/tmp}/creditchina-chrome-cdp.log"

if [ ! -x "$CHROME_BIN" ]; then
  echo "未找到 Chrome 可执行文件：$CHROME_BIN"
  echo "请先安装 Google Chrome，或手动设置 PLAYWRIGHT_AGENT_BROWSER_EXECUTABLE。"
  exit 1
fi

mkdir -p "$PROFILE_DIR"

nohup "$CHROME_BIN" \
  --remote-debugging-address=127.0.0.1 \
  --remote-debugging-port="$CDP_PORT" \
  --user-data-dir="$PROFILE_DIR" \
  --no-first-run \
  --no-default-browser-check \
  >"$LOG_FILE" 2>&1 &

echo "Chrome 已在后台启动，CDP 地址：http://127.0.0.1:${CDP_PORT}"
echo "日志文件：$LOG_FILE"
