"""同步调用层。

这个文件不再承载复杂调度逻辑，而是把外部的同步调用
转发到 `skills/` 包中的异步 skills 生命周期。
"""

from __future__ import annotations

import asyncio
from typing import Any

from skills import (
    dispatch_chat_skill_async,
    list_registered_skills,
    run_named_skill_async,
)
from 智能体配置 import (
    DEFAULT_API_BASE_URL,
    DEFAULT_BASE_URL,
    DEFAULT_BROWSER_MODE,
    DEFAULT_CDP_ATTACH_EXISTING_PAGE,
    DEFAULT_CDP_URL,
    DEFAULT_CREDIT_CODE,
    DEFAULT_MAX_STEPS,
    DEFAULT_MODEL,
    DEFAULT_SITE_PASSWORD,
    WELCOME_MESSAGE,
)

__all__ = [
    "DEFAULT_API_BASE_URL",
    "DEFAULT_BASE_URL",
    "DEFAULT_BROWSER_MODE",
    "DEFAULT_CDP_ATTACH_EXISTING_PAGE",
    "DEFAULT_CDP_URL",
    "DEFAULT_CREDIT_CODE",
    "DEFAULT_MAX_STEPS",
    "DEFAULT_MODEL",
    "DEFAULT_SITE_PASSWORD",
    "WELCOME_MESSAGE",
    "invoke_agent_skill",
    "invoke_creditchina_query",
    "invoke_playwright_agent",
    "list_registered_skills",
]


def invoke_agent_skill(
    skill_name: str,
    *,
    input_payload: dict[str, Any],
    session_id: str | None = None,
    base_url: str | None = None,
    credit_code: str | None = None,
    site_password: str | None = None,
    model: str | None = None,
    api_base_url: str | None = None,
    max_steps: int | None = None,
    max_captcha_attempts: int | None = None,
    browser_mode: str | None = None,
    cdp_url: str | None = None,
    cdp_attach_existing_page: bool | None = None,
    storage_state_path: str | None = None,
    cookies_path: str | None = None,
    cookie_header_path: str | None = None,
    invalid_marker_path: str | None = None,
    persist_session: bool = True,
    dispatch_reason: str = "api.skill.invoke",
) -> dict[str, Any]:
    """同步执行一个显式指定的 skill。"""
    return asyncio.run(
        run_named_skill_async(
            str(skill_name or "").strip(),
            input_payload=dict(input_payload or {}),
            session_id=str(session_id or "").strip() or None,
            base_url=str(base_url or DEFAULT_BASE_URL).strip() or DEFAULT_BASE_URL,
            credit_code=str(credit_code or "").strip() or None,
            site_password=str(site_password or DEFAULT_SITE_PASSWORD),
            model_name=str(model or DEFAULT_MODEL).strip() or DEFAULT_MODEL,
            api_base_url=str(api_base_url or DEFAULT_API_BASE_URL).strip().rstrip("/") or DEFAULT_API_BASE_URL,
            max_steps=max(6, int(max_steps or DEFAULT_MAX_STEPS)),
            max_captcha_attempts=max(1, min(int(max_captcha_attempts or 6), 8)),
            browser_mode=str(browser_mode or DEFAULT_BROWSER_MODE).strip() or DEFAULT_BROWSER_MODE,
            cdp_url=str(cdp_url or DEFAULT_CDP_URL).strip() or DEFAULT_CDP_URL,
            cdp_attach_existing_page=(
                DEFAULT_CDP_ATTACH_EXISTING_PAGE
                if cdp_attach_existing_page is None
                else bool(cdp_attach_existing_page)
            ),
            storage_state_path=str(storage_state_path or "").strip() or None,
            cookies_path=str(cookies_path or "").strip() or None,
            cookie_header_path=str(cookie_header_path or "").strip() or None,
            invalid_marker_path=str(invalid_marker_path or "").strip() or None,
            persist_session=bool(persist_session),
            dispatch_reason=str(dispatch_reason or "api.skill.invoke").strip() or "api.skill.invoke",
        )
    )


def invoke_playwright_agent(
    user_message: str,
    *,
    session_id: str | None = None,
    base_url: str | None = None,
    credit_code: str | None = None,
    site_password: str | None = None,
    model: str | None = None,
    api_base_url: str | None = None,
    max_steps: int | None = None,
    max_captcha_attempts: int | None = None,
    browser_mode: str | None = None,
    cdp_url: str | None = None,
    cdp_attach_existing_page: bool | None = None,
    storage_state_path: str | None = None,
    cookies_path: str | None = None,
    cookie_header_path: str | None = None,
    invalid_marker_path: str | None = None,
    persist_session: bool = True,
    skill_name: str | None = None,
) -> dict[str, Any]:
    """聊天式入口。

    输入一段自然语言后，内部会先做意图分发，再命中对应的 skill。
    """
    return asyncio.run(
        dispatch_chat_skill_async(
            str(user_message or "").strip(),
            session_id=str(session_id or "").strip() or None,
            requested_skill_name=str(skill_name or "").strip() or None,
            base_url=str(base_url or DEFAULT_BASE_URL).strip() or DEFAULT_BASE_URL,
            credit_code=str(credit_code or "").strip() or None,
            site_password=str(site_password or DEFAULT_SITE_PASSWORD),
            model_name=str(model or DEFAULT_MODEL).strip() or DEFAULT_MODEL,
            api_base_url=str(api_base_url or DEFAULT_API_BASE_URL).strip().rstrip("/") or DEFAULT_API_BASE_URL,
            max_steps=max(6, int(max_steps or DEFAULT_MAX_STEPS)),
            max_captcha_attempts=max(1, min(int(max_captcha_attempts or 6), 8)),
            browser_mode=str(browser_mode or DEFAULT_BROWSER_MODE).strip() or DEFAULT_BROWSER_MODE,
            cdp_url=str(cdp_url or DEFAULT_CDP_URL).strip() or DEFAULT_CDP_URL,
            cdp_attach_existing_page=(
                DEFAULT_CDP_ATTACH_EXISTING_PAGE
                if cdp_attach_existing_page is None
                else bool(cdp_attach_existing_page)
            ),
            storage_state_path=str(storage_state_path or "").strip() or None,
            cookies_path=str(cookies_path or "").strip() or None,
            cookie_header_path=str(cookie_header_path or "").strip() or None,
            invalid_marker_path=str(invalid_marker_path or "").strip() or None,
            persist_session=bool(persist_session),
        )
    )


def invoke_creditchina_query(
    credit_code: str,
    *,
    session_id: str | None = None,
    base_url: str | None = None,
    site_password: str | None = None,
    max_captcha_attempts: int | None = None,
    browser_mode: str | None = None,
    cdp_url: str | None = None,
    cdp_attach_existing_page: bool | None = None,
    storage_state_path: str | None = None,
    cookies_path: str | None = None,
    cookie_header_path: str | None = None,
    invalid_marker_path: str | None = None,
    persist_session: bool = True,
) -> dict[str, Any]:
    """同步执行 `creditchina_query` skill。"""
    return invoke_agent_skill(
        "creditchina_query",
        input_payload={"credit_code": str(credit_code or "").strip()},
        session_id=session_id,
        base_url=base_url,
        credit_code=str(credit_code or "").strip() or None,
        site_password=site_password,
        model=DEFAULT_MODEL,
        api_base_url=DEFAULT_API_BASE_URL,
        max_steps=DEFAULT_MAX_STEPS,
        max_captcha_attempts=max_captcha_attempts,
        browser_mode=browser_mode,
        cdp_url=cdp_url,
        cdp_attach_existing_page=cdp_attach_existing_page,
        storage_state_path=storage_state_path,
        cookies_path=cookies_path,
        cookie_header_path=cookie_header_path,
        invalid_marker_path=invalid_marker_path,
        persist_session=persist_session,
        dispatch_reason="api.creditchina.query",
    )
