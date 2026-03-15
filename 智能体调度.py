from __future__ import annotations
import asyncio
import re
from typing import Any
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
try:
    from langgraph.prebuilt import create_react_agent
    from langchain_openai import ChatOpenAI
except ImportError:
    create_react_agent = None
    ChatOpenAI = None
from agent工具 import (
    AsyncBrowserSession,
    PlaywrightToolRuntime,
    load_agent_api_key,
    playwright_agent_is_ready,
    runtime_metadata,
)
from 智能体配置 import (
    DEFAULT_API_BASE_URL,
    DEFAULT_LAUNCH_HEADLESS,
    DEFAULT_BASE_URL,
    DEFAULT_BROWSER_MODE,
    DEFAULT_CAPTCHA_OCR_MODEL,
    DEFAULT_CDP_ATTACH_EXISTING_PAGE,
    DEFAULT_CDP_URL,
    DEFAULT_CREDIT_CODE,
    DEFAULT_MAX_STEPS,
    DEFAULT_MODEL,
    DEFAULT_SITE_PASSWORD,
    WELCOME_MESSAGE,
)

# 阅读顺序建议：
# 1. 先看 `invoke_playwright_agent()`：这是外部正式入口；
# 2. 再看 `_run_agent_async()`：这里创建唯一的顶层 agent；
# 3. 最后看 `PlaywrightToolRuntime.build_async_tools()`：这里是顶层 agent 直接调用的工具集合。

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
    "invoke_creditchina_query",
    "invoke_playwright_agent",
]


def _coerce_message_text(message: BaseMessage) -> str:
    content = getattr(message, "content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
                continue
            if isinstance(item, dict):
                text = item.get("text")
                if text:
                    parts.append(str(text))
        return "".join(parts)
    return str(content or "")


def _extract_used_tools(messages: list[BaseMessage]) -> list[str]:
    used: list[str] = []
    for message in messages:
        tool_calls = getattr(message, "tool_calls", None) or []
        for call in tool_calls:
            name = str(call.get("name") or "").strip()
            if name and name not in used:
                used.append(name)
    return used


def _extract_creditchina_normalized_payload(result: dict[str, Any]) -> dict[str, Any]:
    api_flow = result.get("api_flow") if isinstance(result, dict) else {}
    if isinstance(api_flow, dict):
        normalized = api_flow.get("normalized")
        if isinstance(normalized, dict):
            return {"normalized": normalized}
    saved_result = result.get("saved_result") if isinstance(result, dict) else {}
    result_payload = saved_result.get("result_payload") if isinstance(saved_result, dict) else {}
    page_result = result_payload.get("page_result") if isinstance(result_payload, dict) else {}
    if isinstance(page_result, dict):
        normalized = page_result.get("normalized")
        if isinstance(normalized, dict):
            return normalized if "normalized" in normalized else {"normalized": normalized}
    if isinstance(result, dict):
        if isinstance(result.get("normalized"), dict):
            return {"normalized": result.get("normalized")}
    return {}


def _build_creditchina_reply(result: dict[str, Any]) -> str:
    normalized_payload = _extract_creditchina_normalized_payload(result)
    normalized = normalized_payload.get("normalized") if isinstance(normalized_payload, dict) else {}
    if isinstance(normalized, dict) and normalized:
        lines = [
            "信用中国查询已完成。",
            f"企业名称：{normalized.get('enterprise_name') or '未返回'}",
            f"统一社会信用代码：{normalized.get('credit_code') or result.get('credit_code') or '未返回'}",
            f"状态：{normalized.get('status') or '未返回'}",
            f"法定代表人/负责人：{normalized.get('legal_person') or '未返回'}",
            f"企业类型：{normalized.get('enterprise_type') or '未返回'}",
            f"成立日期：{normalized.get('establish_date') or '未返回'}",
            f"住所：{normalized.get('address') or '未返回'}",
            f"登记机关：{normalized.get('registration_authority') or '未返回'}",
        ]
        return "\n".join(lines)
    if result.get("ok"):
        saved_result = result.get("saved_result") if isinstance(result, dict) else {}
        result_json_path = saved_result.get("result_json_path") if isinstance(saved_result, dict) else ""
        return f"信用中国查询已完成，但没有拿到结构化字段。结果文件：{result_json_path or '未保存'}"
    error_message = (
        result.get("error")
        or (result.get("api_flow") or {}).get("error")
        or (result.get("captcha") or {}).get("error")
        or "查询失败。"
    )
    return f"信用中国查询失败：{error_message}"


def _build_agent_model(model_name: str, api_base_url: str):
    if create_react_agent is None or ChatOpenAI is None:
        raise RuntimeError("当前环境缺少统一 agent 依赖；请先安装 langgraph 和 langchain-openai。")
    api_key = load_agent_api_key()
    return ChatOpenAI(
        model=model_name,
        temperature=0,
        api_key=api_key,
        base_url=api_base_url,
        timeout=90,
        max_retries=4,
        use_responses_api=True,
    )


_URL_PATTERN = re.compile(r"https?://[^\s'\"<>]+", re.IGNORECASE)


def _extract_first_url(text: str) -> str | None:
    match = _URL_PATTERN.search(str(text or ""))
    if not match:
        return None
    return match.group(0)

# 这个函数构建了智能体的系统提示，明确了智能体的角色、可用工具、行为规则等关键信息。这些信息会直接影响智能体的决策和行为。
def _build_agent_prompt(
    *,
    base_url: str,
    credit_code: str | None,
    site_password: str,
) -> str:
    effective_credit_code = str(credit_code or DEFAULT_CREDIT_CODE).strip() or DEFAULT_CREDIT_CODE
    password_line = (
        f"如果目标站先出现系统密码页，可使用系统密码：{site_password}"
        if str(site_password or "").strip()
        else "如果目标站先出现系统密码页，需要先观察页面再决定如何处理。"
    )
    return (
        "你是一个统一的中文 Playwright 智能体。\n"
        "你的默认行为是正常聊天、解释、讨论方案，而不是默认操作页面。\n"
        "只有在用户明确要求访问页面、查看 DOM、分析 selector、执行自动化、"
        "输入点击提交、识别验证码、查询法务测试站时，你才调用页面工具。\n"
        f"当前起始地址：{base_url}\n"
        f"当前默认统一社会信用代码：{effective_credit_code}\n"
        f"{password_line}\n"
        "规则：\n"
        "1. 普通问候、闲聊、解释架构、讨论实现方案、追问原因时，不要调用工具。\n"
        "2. 即使页面里预填了统一社会信用代码，也不能据此擅自发起查询。\n"
        "3. 页面结构未知时，先调用 `open_start_page` 或 `open_page`，再调用 `inspect_page`，不要先猜 selector。\n"
        "4. 导航、点击、提交后，优先重新 `inspect_page` 或 `wait_for_selector` 确认页面状态。\n"
        "5. 如果出现系统密码页，优先使用 `unlock_site_password`；如果出现验证码图，优先使用 `solve_captcha_and_submit`。\n"
        "6. 如果用户明确要求做查询，但没有再给新的信用代码，可以使用上面的默认统一社会信用代码。\n"
        "7. 如果用户明确要求执行“信用中国”固定查询流程（进入页面、输入统一社会信用代码、点击搜索、通过图形验证码、保存结果），优先调用 `run_creditchina_query_and_save`；若你需要直接走站内 private-api 查询流，可调用 `run_creditchina_private_api_query_and_save`。\n"
        "8. 如果 `inspect_page` 显示标题/文本/元素都为空，或页面像脚本壳页/挑战页，先用 `detect_access_challenge` 判断，再用 `wait_for_seconds`、`retry_on_access_challenge` 处理。\n"
        "9. 当需要把问题交代清楚时，可调用 `capture_page_artifacts` 保存整页截图与完整 HTML，并把路径告诉用户。\n"
        "10. 工具返回后，用中文自然总结结果；如果工具失败，解释失败点。"
    )

# 这是智能体调度的核心函数；它是一个异步函数，内部创建一个顶层 agent，并让它直接调用 Playwright 工具来完成任务。
async def _run_agent_async(
    user_message: str,
    *,
    base_url: str,
    credit_code: str | None,
    site_password: str,
    model_name: str,
    api_base_url: str,
    max_steps: int,
    browser_mode: str | None,
    cdp_url: str | None,
    cdp_attach_existing_page: bool | None,
    storage_state_path: str | None,
    cookies_path: str | None,
    cookie_header_path: str | None,
    invalid_marker_path: str | None,
    persist_session: bool,
) -> dict[str, Any]:
    # 这是唯一的 agent 调度核心：
    # 顶层 agent 直接拿到 Playwright 工具列表，自行决定聊不聊、什么时候开页、什么时候查 DOM。
    runtime = PlaywrightToolRuntime( # 先创建一个带着网站地址和站点密码的网页操作工具箱
        base_url=base_url,
        site_password=site_password,
    )
    run_artifact_dir = runtime._build_run_artifact_dir() # 给这一次智能体运行创建一个独立的“产物目录”。
    top_level_model = _build_agent_model(model_name=model_name, api_base_url=api_base_url)
    session_base_url = _extract_first_url(user_message) or base_url
    async with AsyncBrowserSession(
        headless=DEFAULT_LAUNCH_HEADLESS,
        base_url=session_base_url,
        browser_mode=browser_mode,
        cdp_url=cdp_url,
        cdp_attach_existing_page=cdp_attach_existing_page,
        storage_state_path=storage_state_path,
        cookies_path=cookies_path,
        cookie_header_path=cookie_header_path,
        invalid_marker_path=invalid_marker_path,
        persist_session=persist_session,
    ) as browser: # 打开一个异步 Playwright 浏览器会话；是否无头由配置决定，默认优先尝试真实浏览器链路。
        page = browser.page # 并拿到当前页面对象 page
        assert page is not None

        agent = create_react_agent(
            model=top_level_model,
            tools=runtime.build_async_tools(page=page, artifact_dir=run_artifact_dir), # 
            prompt=_build_agent_prompt(
                base_url=base_url,
                credit_code=credit_code,
                site_password=site_password,
            ),
            debug=False,
            version="v2",
        )
        result = await agent.ainvoke(
            {"messages": [HumanMessage(content=str(user_message or "").strip())]},
            config={"recursion_limit": max_steps}, # 限制它最多循环多少步。这里的“步”可以理解为：思考一次、调工具一次、再思考一次……防止它陷入无限调用。
        )
        messages = list(result.get("messages") or [])
        reply_text = ""
        for message in reversed(messages): # 拿出整个对话执行轨迹。这里面通常不止有人类消息和最终 AI 消息，还可能有工具调用记录。
            if isinstance(message, AIMessage): # 倒着找最后一条有实际文本内容的 AIMessage
                reply_text = _coerce_message_text(message).strip()
                if reply_text:
                    break
        if not reply_text:
            raise RuntimeError("统一 agent 没有返回可展示的最终回复。")

        used_tools = _extract_used_tools(messages) # 从整段执行轨迹里提取“这次到底调用了哪些工具”
        browser_run = None
        page_result = None
        if used_tools:
            page_result = await runtime._try_read_result_json_async(page)
            browser_run = {
                "base_url": base_url,
                "current_url": page.url,
                "artifact_dir": str(run_artifact_dir),
                "message_count": len(messages),
                "model": model_name,
                "api_base_url": api_base_url,
                "browser_mode": browser_mode or DEFAULT_BROWSER_MODE,
                "cdp_url": cdp_url or DEFAULT_CDP_URL,
            }
        session_invalid, invalid_reason, invalid_diagnosis = runtime.current_session_invalid_state()
        persisted_session_path = ""
        invalid_marker_written_path = ""
        if used_tools and persist_session:
            if session_invalid:
                invalid_marker_written_path = browser.mark_session_invalid(
                    reason=invalid_reason,
                    diagnosis=invalid_diagnosis,
                )
            else:
                persisted_session_path = await browser.persist_storage_state(target_url=page.url or base_url)
        runtime_debug = runtime.export_debug_state() if used_tools else None
        session_debug = browser.session_debug_state()
        if persisted_session_path:
            session_debug["persisted_session_path"] = persisted_session_path
        if invalid_marker_written_path:
            session_debug["invalid_marker_path"] = invalid_marker_written_path

        return {
            "mode": "unified", # 表示当前是“单 agent + tools”的统一模式。
            "reply": reply_text,
            "page_result": page_result,
            "used_tools": used_tools,
            "agent_debug": {
                "model": model_name,
                "api_base_url": api_base_url,
                "captcha_ocr_model": DEFAULT_CAPTCHA_OCR_MODEL,
                "browser_run": browser_run,
                "runtime_debug": runtime_debug,
                "session_debug": session_debug,
            },
        }


async def _run_creditchina_query_async(
    credit_code: str,
    *,
    base_url: str,
    site_password: str,
    max_captcha_attempts: int,
    browser_mode: str | None,
    cdp_url: str | None,
    cdp_attach_existing_page: bool | None,
    storage_state_path: str | None,
    cookies_path: str | None,
    cookie_header_path: str | None,
    invalid_marker_path: str | None,
    persist_session: bool,
) -> dict[str, Any]:
    normalized_credit_code = str(credit_code or "").strip().upper()
    if not normalized_credit_code:
        raise RuntimeError("统一社会信用代码不能为空。")

    async with AsyncBrowserSession(
        headless=DEFAULT_LAUNCH_HEADLESS,
        base_url=base_url,
        browser_mode=browser_mode,
        cdp_url=cdp_url,
        cdp_attach_existing_page=cdp_attach_existing_page,
        storage_state_path=storage_state_path,
        cookies_path=cookies_path,
        cookie_header_path=cookie_header_path,
        invalid_marker_path=invalid_marker_path,
        persist_session=persist_session,
    ) as browser:
        page = browser.page
        if page is None:
            raise RuntimeError("浏览器页面初始化失败。")

        runtime = PlaywrightToolRuntime(base_url=base_url, site_password=site_password)
        run_artifact_dir = runtime._build_run_artifact_dir()
        query_result = await runtime.run_creditchina_query_and_save_async(
            page,
            run_artifact_dir,
            credit_code=normalized_credit_code,
            max_captcha_attempts=max_captcha_attempts,
        )

        session_invalid, invalid_reason, invalid_diagnosis = runtime.current_session_invalid_state()
        persisted_session_path = ""
        invalid_marker_written_path = ""
        if persist_session:
            if session_invalid:
                invalid_marker_written_path = browser.mark_session_invalid(
                    reason=invalid_reason,
                    diagnosis=invalid_diagnosis,
                )
            else:
                persisted_session_path = await browser.persist_storage_state(target_url=page.url or base_url)

        session_debug = browser.session_debug_state()
        if persisted_session_path:
            session_debug["persisted_session_path"] = persisted_session_path
        if invalid_marker_written_path:
            session_debug["invalid_marker_path"] = invalid_marker_written_path

        normalized_payload = _extract_creditchina_normalized_payload(query_result)
        return {
            "mode": "creditchina_direct",
            "reply": _build_creditchina_reply(query_result),
            "credit_code": normalized_credit_code,
            "normalized": normalized_payload.get("normalized") if isinstance(normalized_payload, dict) else {},
            "query_result": query_result,
            "agent_debug": {
                "captcha_ocr_model": DEFAULT_CAPTCHA_OCR_MODEL,
                "browser_run": {
                    "base_url": base_url,
                    "current_url": page.url,
                    "artifact_dir": str(run_artifact_dir),
                    "browser_mode": browser_mode or DEFAULT_BROWSER_MODE,
                    "cdp_url": cdp_url or DEFAULT_CDP_URL,
                },
                "runtime_debug": runtime.export_debug_state(),
                "session_debug": session_debug,
            },
        }

# 这是外部正式入口；外部调用时只需要调用这个函数，传入用户消息和必要的参数，就能得到智能体的回复和工具调用结果。
def invoke_playwright_agent(
    user_message: str,
    *,
    base_url: str | None = None,
    credit_code: str | None = None,
    site_password: str | None = None,
    mode: str = "react",
    model: str | None = None,
    api_base_url: str | None = None,
    max_steps: int | None = None,
    browser_mode: str | None = None,
    cdp_url: str | None = None,
    cdp_attach_existing_page: bool | None = None,
    storage_state_path: str | None = None,
    cookies_path: str | None = None,
    cookie_header_path: str | None = None,
    invalid_marker_path: str | None = None,
    persist_session: bool = True,
) -> dict[str, Any]:
    # `mode` 是旧接口遗留字段；现在已经固定为单 agent + tools，不再区分其它模式。
    _ = mode
    return asyncio.run(
        _run_agent_async(
            str(user_message or "").strip(),
            base_url=str(base_url or DEFAULT_BASE_URL).strip() or DEFAULT_BASE_URL,
            credit_code=str(credit_code or "").strip() or None,
            site_password=str(site_password or DEFAULT_SITE_PASSWORD),
            model_name=str(model or DEFAULT_MODEL).strip() or DEFAULT_MODEL,
            api_base_url=str(api_base_url or DEFAULT_API_BASE_URL).strip().rstrip("/") or DEFAULT_API_BASE_URL,
            max_steps=max(6, int(max_steps or DEFAULT_MAX_STEPS)),
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
    return asyncio.run(
        _run_creditchina_query_async(
            str(credit_code or "").strip(),
            base_url=str(base_url or DEFAULT_BASE_URL).strip() or DEFAULT_BASE_URL,
            site_password=str(site_password or DEFAULT_SITE_PASSWORD),
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
