"""模型客户端辅助工具模块。

这里专门负责：
- 找到 OpenAI API Key
- 构造统一的 OpenAI client
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from openai import OpenAI

from 智能体配置 import DEFAULT_API_BASE_URL

__all__ = [
    "build_openai_client",
    "load_agent_api_key",
]


def _candidate_auth_paths() -> list[Path]:
    """给 API Key 搜索候选认证文件路径。"""
    candidates = [
        Path.home() / ".codex" / "auth.json",
        Path("/root/.codex/auth.json"),
        Path("/host-codex/auth.json"),
        Path("/hostfs/root/.codex/auth.json"),
    ]
    raw_auth_path = str(os.getenv("PLAYWRIGHT_AGENT_AUTH_FILE") or "").strip()
    if raw_auth_path:
        candidates.insert(0, Path(raw_auth_path).expanduser())
    return candidates


def load_agent_api_key() -> str:
    """读取浏览器 Agent 运行所需的 OpenAI API Key。"""
    api_key = str(os.getenv("OPENAI_API_KEY") or "").strip()
    if api_key:
        return api_key

    seen: set[str] = set()
    for candidate in _candidate_auth_paths():
        key = str(candidate)
        if key in seen:
            continue
        seen.add(key)
        if not candidate.exists():
            continue
        try:
            payload = json.loads(candidate.read_text(encoding="utf-8"))
        except Exception as exc:
            raise RuntimeError(f"读取 OpenAI 认证文件失败（{candidate}）：{exc}") from exc
        api_key = str(payload.get("OPENAI_API_KEY") or "").strip()
        if api_key:
            return api_key

    raise RuntimeError("未检测到 OPENAI_API_KEY；请设置环境变量或检查 ~/.codex/auth.json。")


def build_openai_client(base_url: str | None = None) -> OpenAI:
    """构造一个已经带好 base_url 和 api_key 的 OpenAI 客户端。"""
    resolved_base_url = str(base_url or DEFAULT_API_BASE_URL).strip().rstrip("/") or DEFAULT_API_BASE_URL
    return OpenAI(
        api_key=load_agent_api_key(),
        base_url=resolved_base_url,
    )
