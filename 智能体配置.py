from __future__ import annotations

import os
import platform
import re
from pathlib import Path
from urllib.parse import urlparse

DEFAULT_BASE_URL = (
    os.getenv("PLAYWRIGHT_AGENT_DEFAULT_BASE_URL")
    or os.getenv("PU_PLAYWRIGHT_AGENT_DEFAULT_BASE_URL")
    or "https://www.creditchina.gov.cn/"
).strip() or "https://www.creditchina.gov.cn/"

DEFAULT_CREDIT_CODE = (
    os.getenv("PLAYWRIGHT_AGENT_DEFAULT_CREDIT_CODE")
    or os.getenv("PU_PLAYWRIGHT_AGENT_DEFAULT_CREDIT_CODE")
    or "91420000177570439L"
).strip() or "91420000177570439L"

DEFAULT_SITE_PASSWORD = (
    os.getenv("PLAYWRIGHT_AGENT_DEFAULT_SITE_PASSWORD")
    or os.getenv("PU_PLAYWRIGHT_AGENT_DEFAULT_SITE_PASSWORD")
    or os.getenv("PLAYWRIGHT_SITE_PASSWORD")
    or os.getenv("LEGAL_TESTS_ACCESS_PASSWORD")
    or "260318"
).strip() or "260318"

DEFAULT_MODEL = (os.getenv("PLAYWRIGHT_AGENT_MODEL") or "gpt-5.4").strip() or "gpt-5.4"

DEFAULT_CAPTCHA_OCR_MODEL = (
    os.getenv("PLAYWRIGHT_CAPTCHA_OCR_MODEL")
    or DEFAULT_MODEL
).strip() or DEFAULT_MODEL

DEFAULT_API_BASE_URL = (
    os.getenv("PLAYWRIGHT_AGENT_BASE_URL")
    or os.getenv("OPENAI_API_BASE")
    or os.getenv("PU_AGENT_BASE_URL")
    or "https://gmn.chuangzuoli.com"
).strip().rstrip("/")

DEFAULT_MAX_STEPS = max(6, int(os.getenv("PLAYWRIGHT_AGENT_MAX_STEPS", "30")))


def _read_bool_env(*names: str, default: bool) -> bool:
    for name in names:
        raw_value = os.getenv(name)
        if raw_value is None:
            continue
        return str(raw_value).strip().lower() not in {"0", "false", "off", "no"}
    return bool(default)


def normalize_deployment_mode(raw_value: str | None) -> str:
    value = str(raw_value or "").strip().lower()
    if value in {"host", "local", "local-host", "host-local"}:
        return "host"
    if value in {"container", "docker", "sandbox"}:
        return "container"
    return "auto"


def normalize_browser_mode(raw_value: str | None) -> str:
    value = str(raw_value or "").strip().lower()
    if value in {"", "auto", "cdp-or-launch", "connect-over-cdp-or-launch"}:
        return "connect_over_cdp_or_launch"
    if value in {"cdp", "connect-over-cdp", "connect_over_cdp"}:
        return "connect_over_cdp"
    if value in {"launch", "local", "local-launch", "local_launch"}:
        return "launch"
    return "connect_over_cdp_or_launch"


DEFAULT_BROWSER_MODE = normalize_browser_mode(
    os.getenv("PLAYWRIGHT_AGENT_BROWSER_MODE")
    or os.getenv("PU_PLAYWRIGHT_AGENT_BROWSER_MODE")
    or "connect_over_cdp_or_launch"
)

DEFAULT_BROWSER_EXECUTABLE = (
    os.getenv("PLAYWRIGHT_AGENT_BROWSER_EXECUTABLE")
    or os.getenv("PU_PLAYWRIGHT_AGENT_BROWSER_EXECUTABLE")
    or ""
).strip()

DEFAULT_DEPLOYMENT_MODE = normalize_deployment_mode(
    os.getenv("PLAYWRIGHT_AGENT_DEPLOYMENT_MODE")
    or os.getenv("PU_PLAYWRIGHT_AGENT_DEPLOYMENT_MODE")
    or "auto"
)


def _resolve_default_cdp_url() -> str:
    explicit = (
        os.getenv("PLAYWRIGHT_AGENT_CDP_URL")
        or os.getenv("PU_PLAYWRIGHT_AGENT_CDP_URL")
        or ""
    ).strip()
    if explicit:
        return explicit
    if platform.system() == "Darwin":
        return "http://127.0.0.1:9222"
    if DEFAULT_DEPLOYMENT_MODE == "container":
        return "http://host.docker.internal:9222"
    # Linux 宿主部署默认优先直连本机浏览器，避免先绕容器边界。
    return "http://127.0.0.1:9222"


DEFAULT_CDP_URL = _resolve_default_cdp_url().rstrip("/")

DEFAULT_CDP_ATTACH_EXISTING_PAGE = _read_bool_env(
    "PLAYWRIGHT_AGENT_CDP_ATTACH_EXISTING_PAGE",
    "PU_PLAYWRIGHT_AGENT_CDP_ATTACH_EXISTING_PAGE",
    default=True,
)

DEFAULT_LAUNCH_HEADLESS = _read_bool_env(
    "PLAYWRIGHT_AGENT_LAUNCH_HEADLESS",
    "PU_PLAYWRIGHT_AGENT_LAUNCH_HEADLESS",
    default=False,
)

DEFAULT_AUTO_XVFB_ENABLED = _read_bool_env(
    "PLAYWRIGHT_AGENT_AUTO_XVFB",
    "PU_PLAYWRIGHT_AGENT_AUTO_XVFB",
    default=True,
)

DEFAULT_XVFB_DISPLAY = (
    os.getenv("PLAYWRIGHT_AGENT_XVFB_DISPLAY")
    or os.getenv("PU_PLAYWRIGHT_AGENT_XVFB_DISPLAY")
    or ":99"
).strip() or ":99"

DEFAULT_XVFB_SCREEN = (
    os.getenv("PLAYWRIGHT_AGENT_XVFB_SCREEN")
    or os.getenv("PU_PLAYWRIGHT_AGENT_XVFB_SCREEN")
    or "1366x900x24"
).strip() or "1366x900x24"

PROJECT_DIR = Path(__file__).resolve().parent
LEGAL_TESTS_DIR = PROJECT_DIR.parent

SESSION_DIR = Path(
    (
        os.getenv("PLAYWRIGHT_AGENT_SESSION_DIR")
        or "./.session"
    ).strip()
    or "./.session"
).expanduser()

RUN_OUTPUT_ROOT = Path(
    (
        os.getenv("PLAYWRIGHT_AGENT_OUTPUT_ROOT")
        or str(LEGAL_TESTS_DIR / "浏览器agent生成文件")
    ).strip()
    or str(LEGAL_TESTS_DIR / "浏览器agent生成文件")
).expanduser()

DEFAULT_TASK_OUTPUT_DIR_NAME = (
    os.getenv("PLAYWRIGHT_AGENT_DEFAULT_TASK_DIR_NAME")
    or "信用中国查询"
).strip() or "信用中国查询"

TASK_OUTPUT_ROOT = RUN_OUTPUT_ROOT / DEFAULT_TASK_OUTPUT_DIR_NAME

RESULTS_DIR = Path(
    (
        os.getenv("PLAYWRIGHT_AGENT_RESULTS_DIR")
        or str(TASK_OUTPUT_ROOT)
    ).strip()
    or str(TASK_OUTPUT_ROOT)
).expanduser()

ARTIFACT_DIR = Path(
    (
        os.getenv("PLAYWRIGHT_AGENT_ARTIFACT_DIR")
        or os.getenv("PU_PLAYWRIGHT_AGENT_ARTIFACT_DIR")
        or str(TASK_OUTPUT_ROOT)
    ).strip()
    or str(TASK_OUTPUT_ROOT)
).expanduser()

WELCOME_MESSAGE = (
    "你好，我是统一的 Playwright 测试智能体。平时可以正常聊天；"
    "当你需要看页面 DOM、找 selector 或跑法务测试流程时，我会自己调用页面工具。"
)

FILENAME_SANITIZER = re.compile(r"[^a-zA-Z0-9._-]+")


def build_session_file_paths(
    base_url: str | None = None,
    *,
    session_dir: Path | None = None,
) -> dict[str, Path]:
    target_dir = Path(session_dir or SESSION_DIR).expanduser()
    parsed = urlparse(str(base_url or DEFAULT_BASE_URL).strip() or DEFAULT_BASE_URL)
    host = (parsed.hostname or "default").strip().lower() or "default"
    safe_host = FILENAME_SANITIZER.sub("-", host).strip("-.") or "default"
    return {
        "session_dir": target_dir,
        "storage_state_path": target_dir / f"{safe_host}.storage_state.json",
        "cookies_path": target_dir / f"{safe_host}.cookies.json",
        "cookie_header_path": target_dir / f"{safe_host}.cookie_header.txt",
        "invalid_marker_path": target_dir / f"{safe_host}.session_invalid.json",
    }
