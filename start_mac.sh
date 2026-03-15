#!/usr/bin/env bash
set -euo pipefail

# macOS 本地启动脚本：
# 1. 准备 Python 虚拟环境
# 2. 安装 Python 依赖
# 3. 预检模型与浏览器依赖
# 4. 必要时拉起本机 CDP Chrome
# 5. 启动 Flask 服务

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT_DIR"

AUTH_FILE="${PLAYWRIGHT_AGENT_AUTH_FILE:-$HOME/.codex/auth.json}"
MODE="${1:-start}"
ALLOW_DEGRADED_STARTUP="${PLAYWRIGHT_AGENT_ALLOW_DEGRADED_STARTUP:-1}"
STARTUP_DEGRADED=0
AUTH_READY=1
BROWSER_READY=1

print_usage() {
  cat <<'EOF'
用法：
  ./start_mac.sh
  ./start_mac.sh --check

说明：
  默认会安装 Python 依赖，并在需要时自动拉起本机 CDP Chrome。
  --check 只做预检，不启动 Flask / Chrome。
EOF
}

mac_python_install_hint() {
  if command -v brew >/dev/null 2>&1; then
    cat <<'EOF'
可尝试安装：
  brew install python@3.11
EOF
  else
    cat <<'EOF'
请先安装 Python 3.10+，并确保系统可执行 `python3`。
如果已安装 Homebrew，可执行：
  brew install python@3.11
EOF
  fi
}

mac_browser_install_hint() {
  if command -v brew >/dev/null 2>&1; then
    cat <<'EOF'
可尝试安装浏览器：
  brew install --cask google-chrome
EOF
  else
    cat <<'EOF'
请先安装 Google Chrome，或设置 PLAYWRIGHT_AGENT_BROWSER_EXECUTABLE。
如果已安装 Homebrew，可执行：
  brew install --cask google-chrome
EOF
  fi
}

mark_degraded() {
  local message="$1"
  local hint="${2:-}"
  STARTUP_DEGRADED=1
  echo "警告：$message" >&2
  if [ -n "$hint" ]; then
    printf '%s\n' "$hint" >&2
  fi
}

choose_python() {
  # 选择一份可用的 Python 3.10+ 解释器来创建 / 复用虚拟环境。
  for candidate in "${PYTHON_BIN:-}" python3.12 python3.11 "$HOME/miniconda3/bin/python3" "$HOME/miniconda3/bin/python" python3; do
    if [ -z "${candidate:-}" ]; then
      continue
    fi
    if ! command -v "$candidate" >/dev/null 2>&1 && [ ! -x "$candidate" ]; then
      continue
    fi
    if "$candidate" - <<'PY' >/dev/null 2>&1
import sys
raise SystemExit(0 if sys.version_info >= (3, 10) else 1)
PY
    then
      echo "$candidate"
      return 0
    fi
  done
  return 1
}

ensure_python_has_venv() {
  local python_bin="$1"
  if ! "$python_bin" - <<'PY' >/dev/null 2>&1
try:
    import venv  # noqa: F401
except Exception:
    raise SystemExit(1)
raise SystemExit(0)
PY
  then
    echo "当前 Python 缺少 venv 模块，无法创建虚拟环境。" >&2
    echo "建议改用 python.org / Homebrew 安装的 Python 3.10+。" >&2
    exit 1
  fi
}

install_python_requirements() {
  if python -m pip install --upgrade pip && python -m pip install -r docs/requirements.txt; then
    return 0
  fi
  echo "Python 依赖安装失败：docs/requirements.txt" >&2
  cat >&2 <<'EOF'
请检查：
  1. 机器是否能联网访问 pip 源
  2. 是否需要设置 PIP_INDEX_URL / HTTPS_PROXY
  3. 当前 Python / pip 是否可正常使用
EOF
  exit 1
}

validate_model_auth() {
  python - <<'PY' >/dev/null 2>&1
import sys
from pathlib import Path

sys.path.insert(0, str(Path(".").resolve()))
from tools.tool_model_client import load_agent_api_key

load_agent_api_key()
PY
}

ensure_model_auth() {
  if validate_model_auth; then
    return 0
  fi
  mark_degraded \
    "未检测到可用的 OPENAI 认证信息，服务可以启动，但页面会显示未就绪。" \
    "请设置 OPENAI_API_KEY，或写入认证文件：$AUTH_FILE"
  return 1
}

probe_cdp() {
  # 通过 /json/version 探测指定 CDP 地址是否可用。
  python - "$1" <<'PY' >/dev/null 2>&1
import sys
import urllib.request

base = str(sys.argv[1] or "").strip().rstrip("/")
if not base:
    raise SystemExit(1)
with urllib.request.urlopen(f"{base}/json/version", timeout=1.5) as response:
    if response.status < 200 or response.status >= 300:
        raise SystemExit(1)
PY
}

wait_for_cdp() {
  # 等待一段时间，直到 CDP 端点准备完成。
  local cdp_url="$1"
  local attempts="${2:-60}"
  local delay_seconds="${3:-0.2}"
  local attempt
  for attempt in $(seq 1 "$attempts"); do
    if probe_cdp "$cdp_url"; then
      return 0
    fi
    sleep "$delay_seconds"
  done
  return 1
}

parse_cdp_host_port() {
  # 从 CDP URL 中提取 host / port，供后续启动本机浏览器使用。
  python - "$1" <<'PY'
import sys
from urllib.parse import urlparse

raw = str(sys.argv[1] or "").strip()
parsed = urlparse(raw if "://" in raw else f"http://{raw}")
host = parsed.hostname or "127.0.0.1"
port = parsed.port or 9222
print(host)
print(port)
PY
}

detect_macos_browser() {
  local explicit="${PLAYWRIGHT_AGENT_BROWSER_EXECUTABLE:-}"
  if [ -n "$explicit" ]; then
    if [ -x "$explicit" ]; then
      echo "$explicit"
      return 0
    fi
    if command -v "$explicit" >/dev/null 2>&1; then
      command -v "$explicit"
      return 0
    fi
    return 1
  fi

  for candidate in \
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
    "$HOME/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
    "/Applications/Chromium.app/Contents/MacOS/Chromium" \
    "$HOME/Applications/Chromium.app/Contents/MacOS/Chromium"
  do
    if [ -x "$candidate" ]; then
      echo "$candidate"
      return 0
    fi
  done
  return 1
}

launch_local_cdp_browser_if_needed() {
  # 如果当前模式需要 CDP，且本机没有现成浏览器，则主动拉起一个。
  local browser_mode="${PLAYWRIGHT_AGENT_BROWSER_MODE:-connect_over_cdp_or_launch}"
  if [ "$browser_mode" != "connect_over_cdp" ] && [ "$browser_mode" != "connect_over_cdp_or_launch" ]; then
    local detected_browser
    detected_browser="$(detect_macos_browser || true)"
    if [ -z "$detected_browser" ]; then
      mark_degraded \
        "未找到可用的 Chrome / Chromium。" \
        "$(mac_browser_install_hint)"
      return 1
    fi
    export PLAYWRIGHT_AGENT_BROWSER_EXECUTABLE="$detected_browser"
    return 0
  fi

  local cdp_url="${PLAYWRIGHT_AGENT_CDP_URL:-http://127.0.0.1:9222}"
  if probe_cdp "$cdp_url"; then
    echo "检测到可复用的本机 CDP 浏览器：$cdp_url"
    return 0
  fi

  mapfile -t parsed < <(parse_cdp_host_port "$cdp_url")
  local cdp_host="${parsed[0]:-127.0.0.1}"
  local cdp_port="${parsed[1]:-9222}"

  case "$cdp_host" in
    127.0.0.1|localhost)
      ;;
    *)
      mark_degraded \
        "当前 Mac 直接启动模式要求本机 CDP 地址；当前 PLAYWRIGHT_AGENT_CDP_URL=$cdp_url" \
        "请改为 http://127.0.0.1:<port>，或先自行启动并验证远端 CDP。"
      return 1
      ;;
  esac

  local chrome_bin
  chrome_bin="$(detect_macos_browser || true)"
  if [ -z "$chrome_bin" ]; then
    mark_degraded \
      "未找到 Chrome 可执行文件。" \
      "$(mac_browser_install_hint)"
    return 1
  fi
  export PLAYWRIGHT_AGENT_BROWSER_EXECUTABLE="$chrome_bin"

  if [ "$MODE" = "check" ]; then
    return 0
  fi

  local profile_dir="${PLAYWRIGHT_AGENT_BROWSER_PROFILE_DIR:-$HOME/.creditchina-chrome-debug}"
  local log_file="${TMPDIR:-/tmp}/creditchina-chrome-cdp.log"
  mkdir -p "$profile_dir"

  nohup "$chrome_bin" \
    --remote-debugging-address=127.0.0.1 \
    --remote-debugging-port="$cdp_port" \
    --user-data-dir="$profile_dir" \
    --no-first-run \
    --no-default-browser-check \
    >"$log_file" 2>&1 &

  if ! wait_for_cdp "$cdp_url" 80 0.25; then
    mark_degraded \
      "已尝试启动本机 Chrome，但 CDP 端点仍未就绪：$cdp_url" \
      "浏览器日志：$log_file"
    return 1
  fi

  echo "已启动本机 Chrome，CDP 地址：$cdp_url"
  echo "浏览器日志：$log_file"
  return 0
}

show_preflight_summary() {
  local browser_summary
  browser_summary="${PLAYWRIGHT_AGENT_BROWSER_EXECUTABLE:-}"
  if [ -z "$browser_summary" ]; then
    browser_summary="$(detect_macos_browser || true)"
  fi
  if [ -z "$browser_summary" ]; then
    browser_summary="未检测到"
  fi
  echo "==> Playwright Agent 预检摘要"
  echo "模式: $MODE"
  echo "Python: $PYTHON_BIN"
  echo "服务地址: ${HOST}:${PORT}"
  echo "浏览器模式: ${PLAYWRIGHT_AGENT_BROWSER_MODE}"
  echo "CDP 地址: ${PLAYWRIGHT_AGENT_CDP_URL}"
  echo "浏览器可执行文件: ${browser_summary}"
  echo "模型认证: $([ "$AUTH_READY" = "1" ] && echo 已就绪 || echo 缺失)"
  echo "浏览器启动链路: $([ "$BROWSER_READY" = "1" ] && echo 已就绪 || echo 缺项)"
  echo "预检结果: $([ "$STARTUP_DEGRADED" = "1" ] && echo 存在缺项 || echo 通过)"
}

report_degraded_startup() {
  cat <<'EOF'
提示：当前环境存在缺项，服务仍会启动。
页面可以打开，但 /healthz 会返回 ready=false，前端会展示具体未就绪原因。
修复缺项后，重新运行 start_mac.sh 即可。
EOF
}

case "$MODE" in
  ""|start)
    MODE="start"
    ;;
  --check|check)
    MODE="check"
    ;;
  -h|--help)
    print_usage
    exit 0
    ;;
  *)
    echo "未知参数：$MODE" >&2
    print_usage >&2
    exit 1
    ;;
esac

PYTHON_BIN="$(choose_python || true)"
if [ -z "$PYTHON_BIN" ]; then
  echo "未找到 Python 3.10+。请先安装 Python 3.10+，或设置 PYTHON_BIN。" >&2
  mac_python_install_hint >&2
  exit 1
fi
ensure_python_has_venv "$PYTHON_BIN"

if [ -x .venv/bin/python ]; then
  # 如果现有虚拟环境 Python 版本不满足要求，就重建它。
  if ! .venv/bin/python - <<'PY' >/dev/null 2>&1
import sys
raise SystemExit(0 if sys.version_info >= (3, 10) else 1)
PY
  then
    rm -rf .venv
  fi
fi

if [ ! -d .venv ]; then
  "$PYTHON_BIN" -m venv .venv
fi

source .venv/bin/activate
install_python_requirements

if [ -f .env ]; then
  set -a
  source .env
  set +a
fi

export HOST="${HOST:-127.0.0.1}"
export PORT="${PORT:-8800}"
LEGAL_TESTS_ROOT="$(cd "$ROOT_DIR/.." && pwd)"
export PLAYWRIGHT_AGENT_DEFAULT_BASE_URL="${PLAYWRIGHT_AGENT_DEFAULT_BASE_URL:-${PU_PLAYWRIGHT_AGENT_DEFAULT_BASE_URL:-https://www.creditchina.gov.cn/}}"
export PLAYWRIGHT_AGENT_DEFAULT_CREDIT_CODE="${PLAYWRIGHT_AGENT_DEFAULT_CREDIT_CODE:-${PU_PLAYWRIGHT_AGENT_DEFAULT_CREDIT_CODE:-91420000177570439L}}"
export PU_PLAYWRIGHT_AGENT_DEFAULT_BASE_URL="${PU_PLAYWRIGHT_AGENT_DEFAULT_BASE_URL:-$PLAYWRIGHT_AGENT_DEFAULT_BASE_URL}"
export PU_PLAYWRIGHT_AGENT_DEFAULT_CREDIT_CODE="${PU_PLAYWRIGHT_AGENT_DEFAULT_CREDIT_CODE:-$PLAYWRIGHT_AGENT_DEFAULT_CREDIT_CODE}"
export PLAYWRIGHT_AGENT_SESSION_DIR="${PLAYWRIGHT_AGENT_SESSION_DIR:-$ROOT_DIR/.session}"
export PLAYWRIGHT_AGENT_OUTPUT_ROOT="${PLAYWRIGHT_AGENT_OUTPUT_ROOT:-$LEGAL_TESTS_ROOT/浏览器agent生成文件}"
export PLAYWRIGHT_AGENT_DEFAULT_TASK_DIR_NAME="${PLAYWRIGHT_AGENT_DEFAULT_TASK_DIR_NAME:-信用中国查询}"
export PLAYWRIGHT_AGENT_RESULTS_DIR="${PLAYWRIGHT_AGENT_RESULTS_DIR:-$PLAYWRIGHT_AGENT_OUTPUT_ROOT/$PLAYWRIGHT_AGENT_DEFAULT_TASK_DIR_NAME}"
export PLAYWRIGHT_AGENT_ARTIFACT_DIR="${PLAYWRIGHT_AGENT_ARTIFACT_DIR:-$PLAYWRIGHT_AGENT_OUTPUT_ROOT/$PLAYWRIGHT_AGENT_DEFAULT_TASK_DIR_NAME}"
export PLAYWRIGHT_AGENT_BROWSER_MODE="${PLAYWRIGHT_AGENT_BROWSER_MODE:-connect_over_cdp_or_launch}"
export PLAYWRIGHT_AGENT_CDP_URL="${PLAYWRIGHT_AGENT_CDP_URL:-http://127.0.0.1:9222}"
export PLAYWRIGHT_AGENT_CDP_ATTACH_EXISTING_PAGE="${PLAYWRIGHT_AGENT_CDP_ATTACH_EXISTING_PAGE:-1}"
export PLAYWRIGHT_AGENT_LAUNCH_HEADLESS="${PLAYWRIGHT_AGENT_LAUNCH_HEADLESS:-0}"
export PLAYWRIGHT_AGENT_AUTO_XVFB="${PLAYWRIGHT_AGENT_AUTO_XVFB:-0}"

mkdir -p "$PLAYWRIGHT_AGENT_SESSION_DIR" "$PLAYWRIGHT_AGENT_RESULTS_DIR" "$PLAYWRIGHT_AGENT_ARTIFACT_DIR"

if ! ensure_model_auth; then
  AUTH_READY=0
fi

if ! launch_local_cdp_browser_if_needed; then
  BROWSER_READY=0
fi

show_preflight_summary

if [ "$MODE" = "check" ]; then
  if [ "$STARTUP_DEGRADED" = "1" ]; then
    exit 1
  fi
  exit 0
fi

if [ "$STARTUP_DEGRADED" = "1" ]; then
  if [ "$ALLOW_DEGRADED_STARTUP" = "1" ]; then
    report_degraded_startup
  else
    echo "检测到运行缺项，且 PLAYWRIGHT_AGENT_ALLOW_DEGRADED_STARTUP=0；已停止启动。" >&2
    exit 1
  fi
fi

# 最后直接启动 Flask 服务；其内部会继续走 skills 架构。
python app.py
