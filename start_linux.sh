#!/usr/bin/env bash
set -euo pipefail

# Linux 直接启动脚本：
# 1. 准备 Python 运行环境
# 2. 安装 Python 依赖
# 3. 预检模型、浏览器和显示环境
# 4. 必要时自动拉起 Xvfb / 本机 CDP 浏览器
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
  ./start_linux.sh
  ./start_linux.sh --check

说明：
  默认会安装 Python 依赖，并在可能的情况下自动补起 Xvfb / 本机 CDP Chrome。
  --check 只做预检，不启动 Flask / Xvfb / Chrome。
EOF
}

detect_linux_package_manager() {
  for candidate in apt-get dnf yum zypper pacman; do
    if command -v "$candidate" >/dev/null 2>&1; then
      echo "$candidate"
      return 0
    fi
  done
  return 1
}

linux_python_install_hint() {
  case "$(detect_linux_package_manager || true)" in
    apt-get)
      cat <<'EOF'
可尝试安装：
  sudo apt-get update
  sudo apt-get install -y python3 python3-venv python3-pip
EOF
      ;;
    dnf)
      cat <<'EOF'
可尝试安装：
  sudo dnf install -y python3 python3-pip
EOF
      ;;
    yum)
      cat <<'EOF'
可尝试安装：
  sudo yum install -y python3 python3-pip
EOF
      ;;
    zypper)
      cat <<'EOF'
可尝试安装：
  sudo zypper install -y python3 python3-pip
EOF
      ;;
    pacman)
      cat <<'EOF'
可尝试安装：
  sudo pacman -Sy --noconfirm python python-pip
EOF
      ;;
    *)
      cat <<'EOF'
请先安装 Python 3.10+，并确保系统可执行 `python3`。
EOF
      ;;
  esac
}

linux_venv_install_hint() {
  case "$(detect_linux_package_manager || true)" in
    apt-get)
      cat <<'EOF'
当前 Python 缺少 venv 模块，可尝试安装：
  sudo apt-get update
  sudo apt-get install -y python3-venv
EOF
      ;;
    *)
      cat <<'EOF'
当前 Python 缺少 venv 模块，请安装对应发行版的 venv / virtualenv 支持包。
EOF
      ;;
  esac
}

linux_browser_install_hint() {
  case "$(detect_linux_package_manager || true)" in
    apt-get)
      cat <<'EOF'
可尝试安装浏览器：
  sudo apt-get update
  sudo apt-get install -y chromium-browser || sudo apt-get install -y chromium
EOF
      ;;
    dnf)
      cat <<'EOF'
可尝试安装浏览器：
  sudo dnf install -y chromium
EOF
      ;;
    yum)
      cat <<'EOF'
可尝试安装浏览器：
  sudo yum install -y chromium
EOF
      ;;
    zypper)
      cat <<'EOF'
可尝试安装浏览器：
  sudo zypper install -y chromium
EOF
      ;;
    pacman)
      cat <<'EOF'
可尝试安装浏览器：
  sudo pacman -Sy --noconfirm chromium
EOF
      ;;
    *)
      cat <<'EOF'
请先安装 Google Chrome / Chromium，或设置 PLAYWRIGHT_AGENT_BROWSER_EXECUTABLE。
EOF
      ;;
  esac
}

linux_xvfb_install_hint() {
  case "$(detect_linux_package_manager || true)" in
    apt-get)
      cat <<'EOF'
可尝试安装 Xvfb：
  sudo apt-get update
  sudo apt-get install -y xvfb
或者改为无头模式：
  export PLAYWRIGHT_AGENT_LAUNCH_HEADLESS=1
EOF
      ;;
    dnf)
      cat <<'EOF'
可尝试安装 Xvfb：
  sudo dnf install -y xorg-x11-server-Xvfb
或者改为无头模式：
  export PLAYWRIGHT_AGENT_LAUNCH_HEADLESS=1
EOF
      ;;
    yum)
      cat <<'EOF'
可尝试安装 Xvfb：
  sudo yum install -y xorg-x11-server-Xvfb
或者改为无头模式：
  export PLAYWRIGHT_AGENT_LAUNCH_HEADLESS=1
EOF
      ;;
    zypper)
      cat <<'EOF'
可尝试安装 Xvfb：
  sudo zypper install -y xvfb-run xorg-x11-server
或者改为无头模式：
  export PLAYWRIGHT_AGENT_LAUNCH_HEADLESS=1
EOF
      ;;
    pacman)
      cat <<'EOF'
可尝试安装 Xvfb：
  sudo pacman -Sy --noconfirm xorg-server-xvfb
或者改为无头模式：
  export PLAYWRIGHT_AGENT_LAUNCH_HEADLESS=1
EOF
      ;;
    *)
      cat <<'EOF'
当前缺少 Xvfb。请安装 Xvfb，或设置 PLAYWRIGHT_AGENT_LAUNCH_HEADLESS=1。
EOF
      ;;
  esac
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
  # 选择一份可用的 Python 3.10+ 解释器。
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
    linux_venv_install_hint >&2
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

choose_linux_browser() {
  # 选择当前 Linux 机器上可用的 Chrome / Chromium。
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
  # root 身份运行浏览器时，需要额外关闭 sandbox。
  if [ "$(id -u)" != "0" ]; then
    return 0
  fi
  if [ -z "${PLAYWRIGHT_AGENT_BROWSER_NO_SANDBOX:-}" ]; then
    export PLAYWRIGHT_AGENT_BROWSER_NO_SANDBOX="1"
    echo "检测到当前以 root 身份运行，已自动启用 PLAYWRIGHT_AGENT_BROWSER_NO_SANDBOX=1"
  fi
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

ensure_display() {
  # 非 headless 模式下，确保 DISPLAY 存在；必要时自动启动 Xvfb。
  local headless="${PLAYWRIGHT_AGENT_LAUNCH_HEADLESS:-0}"
  if [ "$headless" = "1" ]; then
    return 0
  fi
  if [ -n "${DISPLAY:-}" ]; then
    return 0
  fi
  if ! command -v Xvfb >/dev/null 2>&1; then
    mark_degraded \
      "当前 Linux 直接启动模式要求非无头浏览器，但系统未安装 Xvfb。" \
      "$(linux_xvfb_install_hint)"
    return 1
  fi

  if [ "$MODE" = "check" ]; then
    return 0
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

  mark_degraded \
    "Xvfb 启动失败，显示 $display 未就绪。" \
    "请检查日志：$log_file"
  return 1
}

launch_local_cdp_browser_if_needed() {
  # 如果本机没有现成的 CDP 浏览器，就主动拉起一个可接管的实例。
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
      mark_degraded \
        "当前 Linux 直接启动模式要求本机 CDP 地址；当前 PLAYWRIGHT_AGENT_CDP_URL=$cdp_url" \
        "请改为 http://127.0.0.1:<port>，或先自行启动并验证远端 CDP。"
      return 1
      ;;
  esac

  local browser_bin
  browser_bin="$(choose_linux_browser || true)"
  if [ -z "$browser_bin" ]; then
    mark_degraded \
      "未找到 Linux Chrome / Chromium 可执行文件。" \
      "$(linux_browser_install_hint)"
    return 1
  fi
  export PLAYWRIGHT_AGENT_BROWSER_EXECUTABLE="$browser_bin"

  if [ "$MODE" = "check" ]; then
    return 0
  fi

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
    mark_degraded \
      "已尝试启动本机 Chrome，但 CDP 端点仍未就绪：$cdp_url" \
      "浏览器日志：$log_file"
    return 1
  fi
  echo "已启动本机 Chrome，CDP 地址：$cdp_url"
  return 0
}

show_preflight_summary() {
  local browser_summary
  browser_summary="${PLAYWRIGHT_AGENT_BROWSER_EXECUTABLE:-}"
  if [ -z "$browser_summary" ]; then
    browser_summary="$(choose_linux_browser 2>/dev/null || true)"
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
修复缺项后，重新运行 start_linux.sh 即可。
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
  linux_python_install_hint >&2
  exit 1
fi
ensure_python_has_venv "$PYTHON_BIN"

if [ -x .venv/bin/python ]; then
  # Linux 机器间迁移压缩包时，旧 .venv 很可能不可复用，这里先做版本校验。
  if ! .venv/bin/python - <<'PY' >/dev/null 2>&1
import sys
raise SystemExit(0 if sys.version_info >= (3, 10) else 1)
PY
  then
    rm -rf .venv
  fi
elif [ -d .venv ]; then
  # 压缩包可能带着其他机器上的虚拟环境；只要当前机器跑不起来，就重建。
  rm -rf .venv
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

export HOST="${HOST:-0.0.0.0}"
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
export PLAYWRIGHT_AGENT_DEPLOYMENT_MODE="${PLAYWRIGHT_AGENT_DEPLOYMENT_MODE:-local}"
export PLAYWRIGHT_AGENT_BROWSER_MODE="${PLAYWRIGHT_AGENT_BROWSER_MODE:-connect_over_cdp_or_launch}"
export PLAYWRIGHT_AGENT_CDP_URL="${PLAYWRIGHT_AGENT_CDP_URL:-http://127.0.0.1:9222}"
export PLAYWRIGHT_AGENT_CDP_ATTACH_EXISTING_PAGE="${PLAYWRIGHT_AGENT_CDP_ATTACH_EXISTING_PAGE:-1}"
export PLAYWRIGHT_AGENT_LAUNCH_HEADLESS="${PLAYWRIGHT_AGENT_LAUNCH_HEADLESS:-0}"
export PLAYWRIGHT_AGENT_AUTO_XVFB="${PLAYWRIGHT_AGENT_AUTO_XVFB:-1}"
export PLAYWRIGHT_AGENT_XVFB_DISPLAY="${PLAYWRIGHT_AGENT_XVFB_DISPLAY:-:99}"
export PLAYWRIGHT_AGENT_XVFB_SCREEN="${PLAYWRIGHT_AGENT_XVFB_SCREEN:-1366x900x24}"

ensure_root_browser_flags

mkdir -p "$PLAYWRIGHT_AGENT_SESSION_DIR" "$PLAYWRIGHT_AGENT_RESULTS_DIR" "$PLAYWRIGHT_AGENT_ARTIFACT_DIR"

if ! ensure_model_auth; then
  AUTH_READY=0
fi

if [ "${PLAYWRIGHT_AGENT_AUTO_XVFB:-1}" != "0" ]; then
  if ! ensure_display; then
    BROWSER_READY=0
  fi
elif [ "${PLAYWRIGHT_AGENT_LAUNCH_HEADLESS:-0}" != "1" ] && [ -z "${DISPLAY:-}" ]; then
  mark_degraded \
    "当前 Linux 直接启动模式要求非无头浏览器，但未设置 DISPLAY，且已禁用 PLAYWRIGHT_AGENT_AUTO_XVFB。" \
    "请设置 DISPLAY，启用 PLAYWRIGHT_AGENT_AUTO_XVFB=1，或改为 PLAYWRIGHT_AGENT_LAUNCH_HEADLESS=1。"
  BROWSER_READY=0
fi

if [ "$BROWSER_READY" = "1" ]; then
  if ! launch_local_cdp_browser_if_needed "$PLAYWRIGHT_AGENT_CDP_URL"; then
    BROWSER_READY=0
  fi
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

# 最后启动 Flask 服务；其内部会根据请求进入具体 skill。
python app.py
