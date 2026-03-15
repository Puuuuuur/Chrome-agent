"""浏览器 Agent 的集中配置文件。

这个文件负责把环境变量转换成稳定的默认值，避免各模块到处散落配置读取逻辑。
"""

from __future__ import annotations

import os
import platform
import re
from pathlib import Path
from urllib.parse import quote_plus
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


def _build_chat_memory_postgres_dsn() -> str:
    """构造聊天记忆使用的 PostgreSQL DSN。"""
    explicit = str(
        os.getenv("PLAYWRIGHT_AGENT_CHAT_MEMORY_POSTGRES_DSN")
        or os.getenv("PLAYWRIGHT_AGENT_MEMORY_POSTGRES_DSN")
        or ""
    ).strip()
    if explicit:
        return explicit

    host = str(os.getenv("PLAYWRIGHT_AGENT_CHAT_MEMORY_POSTGRES_HOST") or "").strip()
    database = str(os.getenv("PLAYWRIGHT_AGENT_CHAT_MEMORY_POSTGRES_DB") or "").strip()
    user = str(os.getenv("PLAYWRIGHT_AGENT_CHAT_MEMORY_POSTGRES_USER") or "").strip()
    password = str(os.getenv("PLAYWRIGHT_AGENT_CHAT_MEMORY_POSTGRES_PASSWORD") or "")
    port = str(os.getenv("PLAYWRIGHT_AGENT_CHAT_MEMORY_POSTGRES_PORT") or "5432").strip() or "5432"
    sslmode = str(os.getenv("PLAYWRIGHT_AGENT_CHAT_MEMORY_POSTGRES_SSLMODE") or "prefer").strip() or "prefer"
    if not host or not database or not user:
        return ""
    return (
        "postgresql://"
        f"{quote_plus(user)}:{quote_plus(password)}@{host}:{port}/{quote_plus(database)}"
        f"?sslmode={quote_plus(sslmode)}"
    )


CHAT_MEMORY_ENABLED = (
    str(os.getenv("PLAYWRIGHT_AGENT_CHAT_MEMORY_ENABLED", "1")).strip().lower()
    not in {"0", "false", "off", "no"}
)

CHAT_MEMORY_POSTGRES_DSN = _build_chat_memory_postgres_dsn()
CHAT_MEMORY_POSTGRES_CONNECT_TIMEOUT = max(
    1,
    int(os.getenv("PLAYWRIGHT_AGENT_CHAT_MEMORY_POSTGRES_CONNECT_TIMEOUT", "5")),
)

CHAT_MEMORY_RECENT_MESSAGES_LIMIT = max(
    2,
    int(os.getenv("PLAYWRIGHT_AGENT_CHAT_MEMORY_RECENT_MESSAGES_LIMIT", "8")),
)
CHAT_MEMORY_SUMMARY_TRIGGER_MESSAGES = max(
    CHAT_MEMORY_RECENT_MESSAGES_LIMIT + 2,
    int(os.getenv("PLAYWRIGHT_AGENT_CHAT_MEMORY_SUMMARY_TRIGGER_MESSAGES", "12")),
)
CHAT_MEMORY_SUMMARY_KEEP_RECENT_MESSAGES = max(
    2,
    int(os.getenv("PLAYWRIGHT_AGENT_CHAT_MEMORY_SUMMARY_KEEP_RECENT_MESSAGES", "6")),
)
CHAT_MEMORY_SUMMARY_MODEL = (
    os.getenv("PLAYWRIGHT_AGENT_CHAT_MEMORY_SUMMARY_MODEL")
    or DEFAULT_MODEL
).strip() or DEFAULT_MODEL
CHAT_MEMORY_SUMMARY_MAX_CHARS = max(
    600,
    int(os.getenv("PLAYWRIGHT_AGENT_CHAT_MEMORY_SUMMARY_MAX_CHARS", "2200")),
)

def _read_bool_env(*names: str, default: bool) -> bool:
    """按顺序读取多个布尔环境变量，返回第一个命中的值。"""
    for name in names:
        raw_value = os.getenv(name)
        if raw_value is None:
            continue
        return str(raw_value).strip().lower() not in {"0", "false", "off", "no"}
    return bool(default)


def normalize_browser_mode(raw_value: str | None) -> str:
    """把浏览器模式统一规范成内部使用的固定枚举值。"""
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

def _resolve_default_cdp_url() -> str:
    """推断默认的本机 CDP 地址。"""
    explicit = (
        os.getenv("PLAYWRIGHT_AGENT_CDP_URL")
        or os.getenv("PU_PLAYWRIGHT_AGENT_CDP_URL")
        or ""
    ).strip()
    if explicit:
        return explicit
    if platform.system() == "Darwin":
        return "http://127.0.0.1:9222"
    # Linux 直接运行时默认优先直连本机浏览器。
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

RAG_ENABLED = (
    str(os.getenv("PLAYWRIGHT_AGENT_RAG_ENABLED", "1")).strip().lower()
    not in {"0", "false", "off", "no"}
)
RAG_SOURCE_DIR = Path(
    (
        os.getenv("PLAYWRIGHT_AGENT_RAG_SOURCE_DIR")
        or str(PROJECT_DIR / "rag_store" / "source_pdfs")
    ).strip()
    or str(PROJECT_DIR / "rag_store" / "source_pdfs")
).expanduser()
RAG_MILVUS_URI = (
    os.getenv("PLAYWRIGHT_AGENT_RAG_MILVUS_URI")
    or str(Path.home() / ".playwright-agent-memory" / "rag_knowledge_milvus.db")
).strip() or str(Path.home() / ".playwright-agent-memory" / "rag_knowledge_milvus.db")
RAG_MILVUS_COLLECTION = (
    os.getenv("PLAYWRIGHT_AGENT_RAG_MILVUS_COLLECTION")
    or "playwright_agent_rag_knowledge"
).strip() or "playwright_agent_rag_knowledge"
RAG_TOP_K = max(1, int(os.getenv("PLAYWRIGHT_AGENT_RAG_TOP_K", "3")))
RAG_CHUNK_SIZE = max(80, int(os.getenv("PLAYWRIGHT_AGENT_RAG_CHUNK_SIZE", "320")))
RAG_CHUNK_OVERLAP = max(0, int(os.getenv("PLAYWRIGHT_AGENT_RAG_CHUNK_OVERLAP", "60")))
RAG_EMBEDDING_PROVIDER = (
    os.getenv("PLAYWRIGHT_AGENT_RAG_EMBEDDING_PROVIDER")
    or "openai"
).strip().lower() or "openai"
RAG_EMBEDDING_MODEL = (
    os.getenv("PLAYWRIGHT_AGENT_RAG_EMBEDDING_MODEL")
    or "text-embedding-3-small"
).strip() or "text-embedding-3-small"
RAG_EMBEDDING_DIMENSION = max(
    32,
    int(os.getenv("PLAYWRIGHT_AGENT_RAG_EMBEDDING_DIMENSION", "1536")),
)

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
    """根据目标站点域名，生成该站点对应的会话文件路径集合。"""
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
