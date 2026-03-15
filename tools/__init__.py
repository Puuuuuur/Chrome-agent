"""浏览器 Agent 的底层工具包。

这个包只做惰性导出，避免在包初始化阶段触发循环导入。
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

__all__ = [
    "AsyncBrowserSession",
    "PlaywrightToolRuntime",
    "build_openai_client",
    "detect_chromium",
    "load_agent_api_key",
    "playwright_agent_is_ready",
    "runtime_metadata",
    "solve_captcha_bytes",
    "solve_captcha_file",
    "solve_captcha_image",
]


def __getattr__(name: str) -> Any:
    if name in {
        "AsyncBrowserSession",
        "PlaywrightToolRuntime",
        "detect_chromium",
        "playwright_agent_is_ready",
        "runtime_metadata",
    }:
        module = import_module(".tool_browser_runtime", __name__)
        return getattr(module, name)
    if name in {"solve_captcha_bytes", "solve_captcha_file", "solve_captcha_image"}:
        module = import_module(".tool_captcha", __name__)
        return getattr(module, name)
    if name in {"build_openai_client", "load_agent_api_key"}:
        module = import_module(".tool_model_client", __name__)
        return getattr(module, name)
    raise AttributeError(name)
