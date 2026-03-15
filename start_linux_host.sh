#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT_DIR"

AUTH_FILE="${PLAYWRIGHT_AGENT_AUTH_FILE:-$HOME/.codex/auth.json}"

choose_python() {
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

choose_linux_browser() {
  local explicit="${PLAYWRIGHT_AGENT_BROWSER_EXECUTABLE:-}"
  if [ -n "$explicit" ]; then
    if command -v "$explicit" >/dev/null 2>&1; then
      command -v "$explicit"
      return 0
    fi
    if [ -x "$explicit" ]; then
      echo "$explicit"
      return 0
    fi
    echo "配置的浏览器可执行文件不存在：$explicit" >&2
    return 1
  fi

  for candidate in \
    /usr/bin/google-chrome \
    /usr/bin/google-chrome-stable \
    /usr/bin/chromium \
    /usr/bin/chromium-browser \
    /snap/bin/chromium \
    google-chrome \
    google-chrome-stable \
    chromium \
    chromium-browser
  do
    if command -v "$candidate" >/dev/null 2>&1; then
      command -v "$candidate"
      return 0
    fi
    if [ -x "$candidate" ]; then
      echo "$candidate"
      return 0
    fi
  done
  return 1
}

ensure_root_browser_flags() {
  if [ "$(id -u)" != "0" ]; then
    return 0
  fi
  if [ -z "${PLAYWRIGHT_AGENT_BROWSER_NO_SANDBOX:-}" ]; then
    export PLAYWRIGHT_AGENT_BROWSER_NO_SANDBOX="1"
    echo "检测到当前以 root 身份运行，已自动启用 PLAYWRIGHT_AGENT_BROWSER_NO_SANDBOX=1"
  fi
}

probe_cdp() {
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

ensure_display() {
  local headless="${PLAYWRIGHT_AGENT_LAUNCH_HEADLESS:-0}"
  if [ "$headless" = "1" ]; then
    return 0
  fi
  if [ -n "${DISPLAY:-}" ]; then
    return 0
  fi
  if ! command -v Xvfb >/dev/null 2>&1; then
    echo "当前 Linux 宿主模式要求非无头浏览器，但未找到 Xvfb。"
    echo "请先安装 Xvfb，或显式设置 PLAYWRIGHT_AGENT_LAUNCH_HEADLESS=1。"
    exit 1
  fi

  local display="${PLAYWRIGHT_AGENT_XVFB_DISPLAY:-:99}"
  local screen="${PLAYWRIGHT_AGENT_XVFB_SCREEN:-1366x900x24}"
  local display_num="${display#:}"
  local socket_path="/tmp/.X11-unix/X${display_num}"
  local log_file="${TMPDIR:-/tmp}/creditchina-agent-xvfb.log"

  export DISPLAY="$display"
  if [ -S "$socket_path" ]; then
    return 0
  fi

  nohup Xvfb "$display" -screen 0 "$screen" -ac -nolisten tcp >"$log_file" 2>&1 &

  local attempt
  for attempt in $(seq 1 40); do
    if [ -S "$socket_path" ]; then
      return 0
    fi
    sleep 0.2
  done

  echo "Xvfb 启动失败，显示 $display 未就绪。日志：$log_file"
  exit 1
}

launch_local_cdp_browser_if_needed() {
  local cdp_url="$1"
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
      echo "宿主部署模式要求本机 CDP 地址；当前 PLAYWRIGHT_AGENT_CDP_URL=$cdp_url"
      echo "请改为 http://127.0.0.1:<port>，或先自行启动并验证远端 CDP。"
      exit 1
      ;;
  esac

  local browser_bin
  browser_bin="$(choose_linux_browser || true)"
  if [ -z "$browser_bin" ]; then
    echo "未找到 Linux Chrome/Chromium 可执行文件。"
    echo "请先安装 Google Chrome/Chromium，或设置 PLAYWRIGHT_AGENT_BROWSER_EXECUTABLE。"
    exit 1
  fi
  export PLAYWRIGHT_AGENT_BROWSER_EXECUTABLE="$browser_bin"

  local profile_dir="${PLAYWRIGHT_AGENT_BROWSER_PROFILE_DIR:-$HOME/.creditchina-chrome-debug-linux}"
  local log_file="${TMPDIR:-/tmp}/creditchina-chrome-cdp-linux.log"
  mkdir -p "$profile_dir"

  local -a chrome_args=(
    "--remote-debugging-address=127.0.0.1"
    "--remote-debugging-port=${cdp_port}"
    "--user-data-dir=${profile_dir}"
    "--no-first-run"
    "--no-default-browser-check"
    "--disable-dev-shm-usage"
    "--disable-blink-features=AutomationControlled"
    "--lang=zh-CN,zh"
    "--window-size=1366,900"
  )

  if [ "${PLAYWRIGHT_AGENT_BROWSER_NO_SANDBOX:-0}" = "1" ]; then
    chrome_args+=("--no-sandbox" "--disable-setuid-sandbox")
  fi
  if [ "${PLAYWRIGHT_AGENT_LAUNCH_HEADLESS:-0}" = "1" ]; then
    chrome_args+=("--headless=new" "--disable-gpu")
  fi

  nohup "$browser_bin" "${chrome_args[@]}" about:blank >"$log_file" 2>&1 &
  if ! wait_for_cdp "$cdp_url" 80 0.25; then
    echo "已尝试启动本机 Chrome，但 CDP 端点仍未就绪：$cdp_url"
    echo "浏览器日志：$log_file"
    exit 1
  fi
  echo "已启动本机 Chrome，CDP 地址：$cdp_url"
}

PYTHON_BIN="$(choose_python || true)"
if [ -z "$PYTHON_BIN" ]; then
  echo "未找到 Python 3.10+。请先安装 Python 3.11+，或设置 PYTHON_BIN。"
  exit 1
fi

if [ -x .venv/bin/python ]; then
  if ! .venv/bin/python - <<'PY' >/dev/null 2>&1
import sys
raise SystemExit(0 if sys.version_info >= (3, 10) else 1)
PY
  then
    rm -rf .venv
  fi
elif [ -d .venv ]; then
  # 压缩包可能带着其他机器上的虚拟环境；只要当前宿主跑不起来，就重建。
  rm -rf .venv
fi

if [ ! -d .venv ]; then
  "$PYTHON_BIN" -m venv .venv
fi

source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

if [ -f .env ]; then
  set -a
  source .env
  set +a
fi

if [ -z "${OPENAI_API_KEY:-}" ]; then
  if [ ! -f "$AUTH_FILE" ]; then
    echo "未检测到 OPENAI_API_KEY，且认证文件不存在：$AUTH_FILE"
    echo "请设置 OPENAI_API_KEY，或写入 ~/.codex/auth.json。"
    exit 1
  fi
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
export PLAYWRIGHT_AGENT_DEPLOYMENT_MODE="${PLAYWRIGHT_AGENT_DEPLOYMENT_MODE:-host}"
export PLAYWRIGHT_AGENT_BROWSER_MODE="${PLAYWRIGHT_AGENT_BROWSER_MODE:-connect_over_cdp_or_launch}"
export PLAYWRIGHT_AGENT_CDP_URL="${PLAYWRIGHT_AGENT_CDP_URL:-http://127.0.0.1:9222}"
export PLAYWRIGHT_AGENT_CDP_ATTACH_EXISTING_PAGE="${PLAYWRIGHT_AGENT_CDP_ATTACH_EXISTING_PAGE:-1}"
export PLAYWRIGHT_AGENT_LAUNCH_HEADLESS="${PLAYWRIGHT_AGENT_LAUNCH_HEADLESS:-0}"
export PLAYWRIGHT_AGENT_AUTO_XVFB="${PLAYWRIGHT_AGENT_AUTO_XVFB:-1}"
export PLAYWRIGHT_AGENT_XVFB_DISPLAY="${PLAYWRIGHT_AGENT_XVFB_DISPLAY:-:99}"
export PLAYWRIGHT_AGENT_XVFB_SCREEN="${PLAYWRIGHT_AGENT_XVFB_SCREEN:-1366x900x24}"

ensure_root_browser_flags

mkdir -p "$PLAYWRIGHT_AGENT_SESSION_DIR" "$PLAYWRIGHT_AGENT_RESULTS_DIR" "$PLAYWRIGHT_AGENT_ARTIFACT_DIR"

if [ "${PLAYWRIGHT_AGENT_AUTO_XVFB:-1}" != "0" ]; then
  ensure_display
elif [ "${PLAYWRIGHT_AGENT_LAUNCH_HEADLESS:-0}" != "1" ] && [ -z "${DISPLAY:-}" ]; then
  echo "当前 Linux 宿主模式要求非无头浏览器，但未设置 DISPLAY 且已禁用 PLAYWRIGHT_AGENT_AUTO_XVFB。"
  exit 1
fi

launch_local_cdp_browser_if_needed "$PLAYWRIGHT_AGENT_CDP_URL"

python app.py
