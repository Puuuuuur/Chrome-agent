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

if [ -z "${PLAYWRIGHT_AGENT_BROWSER_EXECUTABLE:-}" ]; then
  for candidate in \
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
    "$HOME/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
    "/Applications/Chromium.app/Contents/MacOS/Chromium" \
    "$HOME/Applications/Chromium.app/Contents/MacOS/Chromium"
  do
    if [ -x "$candidate" ]; then
      export PLAYWRIGHT_AGENT_BROWSER_EXECUTABLE="$candidate"
      break
    fi
  done
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
export PLAYWRIGHT_AGENT_BROWSER_MODE="${PLAYWRIGHT_AGENT_BROWSER_MODE:-connect_over_cdp_or_launch}"
export PLAYWRIGHT_AGENT_CDP_URL="${PLAYWRIGHT_AGENT_CDP_URL:-http://127.0.0.1:9222}"
export PLAYWRIGHT_AGENT_CDP_ATTACH_EXISTING_PAGE="${PLAYWRIGHT_AGENT_CDP_ATTACH_EXISTING_PAGE:-1}"
export PLAYWRIGHT_AGENT_LAUNCH_HEADLESS="${PLAYWRIGHT_AGENT_LAUNCH_HEADLESS:-0}"
export PLAYWRIGHT_AGENT_AUTO_XVFB="${PLAYWRIGHT_AGENT_AUTO_XVFB:-0}"

mkdir -p "$PLAYWRIGHT_AGENT_SESSION_DIR" "$PLAYWRIGHT_AGENT_RESULTS_DIR" "$PLAYWRIGHT_AGENT_ARTIFACT_DIR"

python app.py
