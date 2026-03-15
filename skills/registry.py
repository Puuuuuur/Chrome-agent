"""skills 包的注册表、分发器和统一生命周期执行器。"""

from __future__ import annotations

import time
from typing import Any

from chat_memory import get_conversation_memory_service
from rag_kb import get_rag_knowledge_service
from tools.tool_browser_runtime import AsyncBrowserSession, PlaywrightToolRuntime
from 智能体配置 import (
    DEFAULT_BROWSER_MODE,
    DEFAULT_CDP_URL,
    DEFAULT_CREDIT_CODE,
    DEFAULT_LAUNCH_HEADLESS,
)

from .base import (
    SkillExecutionContext,
    SkillSelection,
    _extract_credit_code,
    _explicit_skill_name_from_message,
    _is_creditchina_query_intent,
    _normalize_skill_name,
    _validate_schema_value,
)
from .skill_browser_react import BrowserReactSkill
from .skill_creditchina_query import CreditChinaQuerySkill

__all__ = [
    "SkillDispatcher",
    "SkillLifecycleRunner",
    "SkillRegistry",
    "dispatch_chat_skill_async",
    "get_default_skill_registry",
    "list_registered_skills",
    "run_named_skill_async",
]


class SkillRegistry:
    """保存所有已注册 skill 的注册表。"""

    def __init__(self, skills: list[Any]):
        """把 skill 列表映射成按名称可索引的注册表。"""
        self._skills = {skill.name: skill for skill in skills}

    def get(self, skill_name: str) -> Any:
        """按名称取一个已注册的 skill。"""
        normalized = _normalize_skill_name(skill_name)
        skill = self._skills.get(normalized)
        if skill is None:
            raise RuntimeError(f"未注册的 skill：{skill_name}")
        return skill

    def summaries(self) -> list[dict[str, str]]:
        """返回适合前端展示的 skill 摘要列表。"""
        return [
            {
                "name": skill.name,
                "title": skill.title,
                "description": skill.description,
            }
            for skill in self._skills.values()
        ]


class SkillDispatcher:
    """负责把用户输入分发到某个具体 skill。"""

    def __init__(self, registry: SkillRegistry):
        """记录当前使用的 skill 注册表。"""
        self._registry = registry

    def _chat_input_payload_for_skill(
        self,
        skill_name: str,
        *,
        user_message: str,
        default_credit_code: str | None,
    ) -> dict[str, Any]:
        """根据目标 skill 形态，把聊天输入转换成对应的 skill 入参。"""
        normalized = _normalize_skill_name(skill_name)
        if normalized == "browser_react":
            return {"message": user_message}
        if normalized == "creditchina_query":
            return {"credit_code": _extract_credit_code(user_message, fallback=default_credit_code or DEFAULT_CREDIT_CODE)}
        return {"message": user_message}

    def _build_named_selection(
        self,
        skill_name: str,
        *,
        input_payload: dict[str, Any],
        dispatch_reason: str,
        explicit: bool,
    ) -> SkillSelection:
        """构造一个标准化的 SkillSelection。"""
        skill = self._registry.get(skill_name)
        return SkillSelection(
            skill_name=skill.name,
            input_payload=dict(input_payload),
            dispatch_reason=dispatch_reason,
            explicit=explicit,
        )

    def dispatch_chat(
        self,
        user_message: str,
        *,
        requested_skill_name: str | None = None,
        default_credit_code: str | None = None,
    ) -> SkillSelection:
        """把一条聊天消息分发到最合适的 skill。"""
        normalized_requested = _normalize_skill_name(requested_skill_name)
        if normalized_requested:
            return self._build_named_selection(
                normalized_requested,
                input_payload=self._chat_input_payload_for_skill(
                    normalized_requested,
                    user_message=user_message,
                    default_credit_code=default_credit_code,
                ),
                dispatch_reason="payload.requested_skill_name",
                explicit=True,
            )

        explicit_from_message = _explicit_skill_name_from_message(user_message)
        if explicit_from_message:
            return self._build_named_selection(
                explicit_from_message,
                input_payload=self._chat_input_payload_for_skill(
                    explicit_from_message,
                    user_message=user_message,
                    default_credit_code=default_credit_code,
                ),
                dispatch_reason="message.skill_override",
                explicit=True,
            )

        resolved_credit_code = _extract_credit_code(user_message, fallback=default_credit_code or DEFAULT_CREDIT_CODE)
        if _is_creditchina_query_intent(user_message):
            return self._build_named_selection(
                "creditchina_query",
                input_payload={"credit_code": resolved_credit_code},
                dispatch_reason="intent.creditchina_query",
                explicit=False,
            )
        return self._build_named_selection(
            "browser_react",
            input_payload={"message": user_message},
            dispatch_reason="intent.default_browser_react",
            explicit=False,
        )

    def build_direct_selection(
        self,
        skill_name: str,
        *,
        input_payload: dict[str, Any],
        dispatch_reason: str,
    ) -> SkillSelection:
        """给显式 skill 调用场景构造一个直接选择结果。"""
        return self._build_named_selection(
            skill_name,
            input_payload=input_payload,
            dispatch_reason=dispatch_reason,
            explicit=True,
        )


class SkillLifecycleRunner:
    """统一 skill 执行生命周期。

    这里负责浏览器会话、artifact 目录、schema 校验、session 持久化和调试信息收口。
    """

    def __init__(self, registry: SkillRegistry):
        """记录当前运行器使用的 skill 注册表。"""
        self._registry = registry

    async def execute(
        self,
        selection: SkillSelection,
        *,
        base_url: str,
        credit_code: str | None,
        site_password: str,
        model_name: str,
        api_base_url: str,
        max_steps: int,
        max_captcha_attempts: int,
        browser_mode: str,
        cdp_url: str,
        cdp_attach_existing_page: bool,
        storage_state_path: str | None,
        cookies_path: str | None,
        cookie_header_path: str | None,
        invalid_marker_path: str | None,
        persist_session: bool,
        conversation_memory: Any | None = None,
        rag_context: str = "",
        rag_hits: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """按统一生命周期执行一次 skill，并返回标准结果。"""
        skill = self._registry.get(selection.skill_name)
        validated_input = _validate_schema_value(skill.input_schema, selection.input_payload, path=f"{skill.name}.input")
        session_base_url = skill.resolve_session_base_url(input_payload=validated_input, fallback_base_url=base_url)
        started_at = time.time()
        started_perf = time.perf_counter()

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
        ) as browser:
            page = browser.page
            if page is None:
                raise RuntimeError("浏览器页面初始化失败。")

            runtime = PlaywrightToolRuntime(base_url=base_url, site_password=site_password)
            run_artifact_dir = runtime._build_run_artifact_dir()
            context = SkillExecutionContext(
                base_url=base_url,
                default_credit_code=credit_code,
                site_password=site_password,
                model_name=model_name,
                api_base_url=api_base_url,
                max_steps=max_steps,
                max_captcha_attempts=max_captcha_attempts,
                browser_mode=browser_mode,
                cdp_url=cdp_url,
                cdp_attach_existing_page=cdp_attach_existing_page,
                storage_state_path=storage_state_path,
                cookies_path=cookies_path,
                cookie_header_path=cookie_header_path,
                invalid_marker_path=invalid_marker_path,
                persist_session=persist_session,
                browser=browser,
                page=page,
                runtime=runtime,
                run_artifact_dir=run_artifact_dir,
                conversation_memory=conversation_memory,
                rag_context=str(rag_context or ""),
                rag_hits=list(rag_hits or []),
            )

            raw_output = await skill.execute(input_payload=validated_input, context=context)
            validated_output = _validate_schema_value(skill.output_schema, raw_output, path=f"{skill.name}.output")

            session_invalid, invalid_reason, invalid_diagnosis = runtime.current_session_invalid_state()
            persisted_session_path = ""
            invalid_marker_written_path = ""
            if persist_session and skill.should_persist_session(validated_output):
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

            runtime_debug = runtime.export_debug_state() if skill.should_capture_runtime_debug(validated_output) else None
            browser_run = (
                {
                    "base_url": base_url,
                    "current_url": page.url,
                    "artifact_dir": str(run_artifact_dir),
                    "browser_mode": browser_mode or DEFAULT_BROWSER_MODE,
                    "cdp_url": cdp_url or DEFAULT_CDP_URL,
                    "model": model_name,
                    "api_base_url": api_base_url,
                }
                if skill.should_capture_runtime_debug(validated_output)
                else None
            )

        elapsed_ms = int((time.perf_counter() - started_perf) * 1000)
        response = dict(validated_output)
        existing_agent_debug = dict(response.get("agent_debug") or {})
        existing_agent_debug.update(
            {
                "browser_run": browser_run,
                "runtime_debug": runtime_debug,
                "session_debug": session_debug,
                "skill_runtime": {
                    "name": skill.name,
                    "title": skill.title,
                    "description": skill.description,
                    "dispatch_reason": selection.dispatch_reason,
                    "explicit": selection.explicit,
                    "input": validated_input,
                    "input_schema": skill.input_schema,
                    "output_schema": skill.output_schema,
                    "started_at": started_at,
                    "elapsed_ms": elapsed_ms,
                },
            }
        )
        response["agent_debug"] = existing_agent_debug
        response["skill"] = {
            "name": skill.name,
            "title": skill.title,
            "dispatch_reason": selection.dispatch_reason,
            "explicit": selection.explicit,
        }
        return response


_DEFAULT_SKILL_REGISTRY = SkillRegistry(
    [
        BrowserReactSkill(),
        CreditChinaQuerySkill(),
    ]
)


def get_default_skill_registry() -> SkillRegistry:
    """返回默认的全局 skill 注册表实例。"""
    return _DEFAULT_SKILL_REGISTRY


def list_registered_skills() -> list[dict[str, str]]:
    """列出当前系统已注册的全部 skill。"""
    return get_default_skill_registry().summaries()


async def dispatch_chat_skill_async(
    user_message: str,
    *,
    session_id: str | None,
    requested_skill_name: str | None,
    base_url: str,
    credit_code: str | None,
    site_password: str,
    model_name: str,
    api_base_url: str,
    max_steps: int,
    max_captcha_attempts: int,
    browser_mode: str,
    cdp_url: str,
    cdp_attach_existing_page: bool,
    storage_state_path: str | None,
    cookies_path: str | None,
    cookie_header_path: str | None,
    invalid_marker_path: str | None,
    persist_session: bool,
) -> dict[str, Any]:
    """聊天异步入口：先分发 skill，再走统一生命周期执行。"""
    registry = get_default_skill_registry()
    dispatcher = SkillDispatcher(registry)
    memory_service = get_conversation_memory_service()
    rag_service = get_rag_knowledge_service()
    memory_context = None
    rag_context = ""
    rag_hits: list[dict[str, Any]] = []
    effective_credit_code = credit_code
    if memory_service is not None:
        memory_context = memory_service.prepare_context(session_id, user_message)
        if not effective_credit_code:
            remembered_credit_code = str((memory_context.slots or {}).get("last_credit_code") or "").strip().upper()
            if remembered_credit_code:
                effective_credit_code = remembered_credit_code
    if rag_service is not None:
        try:
            rag_context, rag_records = rag_service.search_context(user_message)
            rag_hits = [
                {
                    "source_path": item.source_path,
                    "chunk_index": item.chunk_index,
                    "score": item.score,
                    "content": item.content,
                }
                for item in rag_records
            ]
        except Exception:
            rag_context = ""
            rag_hits = []
    selection = dispatcher.dispatch_chat(
        user_message,
        requested_skill_name=requested_skill_name,
        default_credit_code=effective_credit_code or DEFAULT_CREDIT_CODE,
    )
    runner = SkillLifecycleRunner(registry)
    response = await runner.execute(
        selection,
        base_url=base_url,
        credit_code=effective_credit_code,
        site_password=site_password,
        model_name=model_name,
        api_base_url=api_base_url,
        max_steps=max_steps,
        max_captcha_attempts=max_captcha_attempts,
        browser_mode=browser_mode,
        cdp_url=cdp_url,
        cdp_attach_existing_page=cdp_attach_existing_page,
        storage_state_path=storage_state_path,
        cookies_path=cookies_path,
        cookie_header_path=cookie_header_path,
        invalid_marker_path=invalid_marker_path,
        persist_session=persist_session,
        conversation_memory=memory_context,
        rag_context=rag_context,
        rag_hits=rag_hits,
    )
    if memory_service is not None and memory_context is not None:
        try:
            turn_result = memory_service.record_turn(
                memory_context,
                user_message=user_message,
                assistant_result=response,
            )
            response["session"] = {
                "id": turn_result.session_id,
                "created": bool(memory_context.created),
                "turn_count": int(turn_result.turn_count),
            }
        except Exception as exc:
            response["session"] = {
                "id": memory_context.session_id,
                "created": bool(memory_context.created),
                "turn_count": int(memory_context.turn_count),
                "memory_error": str(exc),
            }
    return response


async def run_named_skill_async(
    skill_name: str,
    *,
    input_payload: dict[str, Any],
    base_url: str,
    credit_code: str | None,
    site_password: str,
    model_name: str,
    api_base_url: str,
    max_steps: int,
    max_captcha_attempts: int,
    browser_mode: str,
    cdp_url: str,
    cdp_attach_existing_page: bool,
    storage_state_path: str | None,
    cookies_path: str | None,
    cookie_header_path: str | None,
    invalid_marker_path: str | None,
    persist_session: bool,
    dispatch_reason: str,
    session_id: str | None = None,
) -> dict[str, Any]:
    """显式 skill 异步入口：跳过意图识别，直接执行指定 skill。"""
    registry = get_default_skill_registry()
    dispatcher = SkillDispatcher(registry)
    selection = dispatcher.build_direct_selection(
        skill_name,
        input_payload=input_payload,
        dispatch_reason=dispatch_reason,
    )
    rag_context = ""
    rag_hits: list[dict[str, Any]] = []
    rag_service = get_rag_knowledge_service()
    rag_query = str(input_payload.get("message") or "").strip()
    if rag_service is not None and rag_query:
        try:
            rag_context, rag_records = rag_service.search_context(rag_query)
            rag_hits = [
                {
                    "source_path": item.source_path,
                    "chunk_index": item.chunk_index,
                    "score": item.score,
                    "content": item.content,
                }
                for item in rag_records
            ]
        except Exception:
            rag_context = ""
            rag_hits = []
    runner = SkillLifecycleRunner(registry)
    return await runner.execute(
        selection,
        base_url=base_url,
        credit_code=credit_code,
        site_password=site_password,
        model_name=model_name,
        api_base_url=api_base_url,
        max_steps=max_steps,
        max_captcha_attempts=max_captcha_attempts,
        browser_mode=browser_mode,
        cdp_url=cdp_url,
        cdp_attach_existing_page=cdp_attach_existing_page,
        storage_state_path=storage_state_path,
        cookies_path=cookies_path,
        cookie_header_path=cookie_header_path,
        invalid_marker_path=invalid_marker_path,
        persist_session=persist_session,
        rag_context=rag_context,
        rag_hits=rag_hits,
    )
