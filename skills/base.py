"""skills 包的共享基类、schema 校验和通用辅助函数。"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from langchain_core.messages import BaseMessage

from chat_memory.models import ConversationMemoryContext

try:
    from langchain_openai import ChatOpenAI
except ImportError:
    ChatOpenAI = None

from tools.tool_browser_runtime import AsyncBrowserSession, PlaywrightToolRuntime
from tools.tool_model_client import load_agent_api_key
from 智能体配置 import (
    DEFAULT_API_BASE_URL,
    DEFAULT_BASE_URL,
    DEFAULT_CDP_ATTACH_EXISTING_PAGE,
    DEFAULT_CDP_URL,
    DEFAULT_CREDIT_CODE,
    DEFAULT_LAUNCH_HEADLESS,
    DEFAULT_MODEL,
    DEFAULT_SITE_PASSWORD,
)

__all__ = [
    "AgentSkill",
    "SkillExecutionContext",
    "SkillSelection",
    "DEFAULT_API_BASE_URL",
    "DEFAULT_BASE_URL",
    "DEFAULT_CDP_ATTACH_EXISTING_PAGE",
    "DEFAULT_CDP_URL",
    "DEFAULT_CREDIT_CODE",
    "DEFAULT_LAUNCH_HEADLESS",
    "DEFAULT_MODEL",
    "DEFAULT_SITE_PASSWORD",
    "_build_agent_model",
    "_build_creditchina_reply",
    "_coerce_message_text",
    "_contains_any",
    "_explicit_skill_name_from_message",
    "_extract_credit_code",
    "_extract_creditchina_normalized_payload",
    "_extract_creditchina_page_result",
    "_extract_first_url",
    "_extract_used_tools",
    "_is_creditchina_query_intent",
    "_normalize_skill_name",
    "_validate_schema_value",
]

_URL_PATTERN = re.compile(r"https?://[^\s'\"<>]+", re.IGNORECASE)
_CREDIT_CODE_PATTERN = re.compile(r"\b[0-9A-Z]{18}\b", re.IGNORECASE)
_EXPLICIT_SKILL_PATTERN = re.compile(r"(?:^|\s)(?:skill|技能)\s*[:：=]\s*([a-z0-9_.-]+)", re.IGNORECASE)


def _coerce_message_text(message: BaseMessage) -> str:
    """把 LangChain message 的内容统一提取成纯文本。"""
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
    """从消息轨迹中提取本次真正调用过的工具名列表。"""
    used: list[str] = []
    for message in messages:
        tool_calls = getattr(message, "tool_calls", None) or []
        for call in tool_calls:
            name = str(call.get("name") or "").strip()
            if name and name not in used:
                used.append(name)
    return used


def _extract_creditchina_normalized_payload(result: dict[str, Any]) -> dict[str, Any]:
    """从不同形态的 creditchina 结果里提取统一的 normalized 字段。"""
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
    if isinstance(result, dict) and isinstance(result.get("normalized"), dict):
        return {"normalized": result.get("normalized")}
    return {}


def _extract_creditchina_page_result(result: dict[str, Any]) -> dict[str, Any] | None:
    """尽量从 skill 返回结果里找出页面侧的结构化结果。"""
    if not isinstance(result, dict):
        return None
    saved_result = result.get("saved_result") if isinstance(result.get("saved_result"), dict) else {}
    result_payload = saved_result.get("result_payload") if isinstance(saved_result, dict) else {}
    page_result = result_payload.get("page_result")
    if isinstance(page_result, dict):
        return page_result
    captcha = result.get("captcha") if isinstance(result.get("captcha"), dict) else {}
    result_ready = captcha.get("result_ready") if isinstance(captcha, dict) else {}
    if isinstance(result_ready, dict):
        ready_page_result = result_ready.get("page_result")
        if isinstance(ready_page_result, dict):
            return ready_page_result
    return None


def _build_creditchina_reply(result: dict[str, Any]) -> str:
    """把 creditchina skill 的结构化结果整理成可直接展示的中文摘要。"""
    normalized_payload = _extract_creditchina_normalized_payload(result)
    normalized = normalized_payload.get("normalized") if isinstance(normalized_payload, dict) else {}
    if isinstance(normalized, dict) and normalized:
        administrative_management = (
            normalized.get("administrative_management")
            if isinstance(normalized.get("administrative_management"), dict)
            else {}
        )
        penalty_notices = normalized.get("penalty_notices") if isinstance(normalized.get("penalty_notices"), dict) else {}
        penalty_records = list(penalty_notices.get("records") or [])
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
            f"行政管理总量：{administrative_management.get('total') if administrative_management else '未返回'}",
            f"处罚通告数量：{penalty_notices.get('total') if penalty_notices else '未返回'}",
        ]
        for index, item in enumerate(penalty_records[:3], start=1):
            record = dict(item or {})
            summary = (
                record.get("content")
                or record.get("document_number")
                or record.get("authority")
                or record.get("category_name")
                or "未返回"
            )
            lines.append(
                f"处罚通告{index}：{record.get('decision_date') or '-'} | "
                f"{record.get('category_name') or '处罚通告'} | {summary}"
            )
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
    """构造给 browser_react skill 使用的聊天模型实例。"""
    if ChatOpenAI is None:
        raise RuntimeError("当前环境缺少统一 agent 依赖；请先安装 langchain-openai。")
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


def _extract_first_url(text: str) -> str | None:
    """从用户输入里提取第一条 URL，供浏览器 skill 作为起始地址。"""
    match = _URL_PATTERN.search(str(text or ""))
    if not match:
        return None
    return match.group(0)


def _extract_credit_code(text: str, fallback: str | None = None) -> str:
    """从文本里提取统一社会信用代码；提不到就退回默认值。"""
    match = _CREDIT_CODE_PATTERN.search(str(text or "").upper())
    if match:
        return match.group(0)
    return str(fallback or "").strip().upper()


def _normalize_skill_name(raw_value: str | None) -> str:
    """把 skill 名统一规范成内部使用的标准写法。"""
    return str(raw_value or "").strip().lower().replace(" ", "_")


def _explicit_skill_name_from_message(message: str) -> str:
    """识别用户是否在消息里显式点名了某个 skill。"""
    match = _EXPLICIT_SKILL_PATTERN.search(str(message or ""))
    if not match:
        return ""
    return _normalize_skill_name(match.group(1))


def _contains_any(text: str, keywords: tuple[str, ...]) -> bool:
    """判断文本是否包含任一关键词。"""
    normalized = str(text or "").lower()
    return any(keyword in normalized for keyword in keywords)


def _is_creditchina_query_intent(message: str) -> bool:
    """判断用户是否是在请求一次标准 creditchina 查询。"""
    raw = str(message or "")
    normalized = raw.lower()
    if "run_creditchina_query_and_save" in normalized:
        return True
    if "信用中国" not in raw and "creditchina" not in normalized:
        return False
    return _contains_any(
        normalized,
        (
            "查询",
            "搜索",
            "统一社会信用代码",
            "信用代码",
            "保存结果",
            "固定查询",
            "执行一次",
            "处理验证码",
        ),
    )


def _schema_type_name(schema: dict[str, Any]) -> str:
    """读取 schema 的 type；没写时按 any 处理。"""
    schema_type = str(schema.get("type") or "").strip()
    if not schema_type:
        return "any"
    return schema_type


def _validate_schema_value(schema: dict[str, Any], value: Any, *, path: str) -> Any:
    """对 skill 输入 / 输出做轻量 schema 校验，避免脏数据混入执行链路。"""
    if value is None and schema.get("nullable"):
        return None

    schema_type = _schema_type_name(schema)
    if schema_type == "any":
        return value

    if schema_type == "object":
        if not isinstance(value, dict):
            raise RuntimeError(f"{path} 必须是对象。")
        properties = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
        required = list(schema.get("required") or [])
        allow_additional = bool(schema.get("additionalProperties", True))
        validated: dict[str, Any] = {}
        for key in required:
            if key not in value and "default" not in properties.get(key, {}):
                raise RuntimeError(f"{path}.{key} 是必填字段。")
        for key, subschema in properties.items():
            if key in value:
                validated[key] = _validate_schema_value(subschema, value[key], path=f"{path}.{key}")
            elif "default" in subschema:
                validated[key] = subschema["default"]
        unknown_keys = [key for key in value.keys() if key not in properties]
        if unknown_keys and not allow_additional:
            raise RuntimeError(f"{path} 包含未声明字段：{', '.join(sorted(unknown_keys))}")
        if allow_additional:
            for key, subvalue in value.items():
                if key not in properties:
                    validated[key] = subvalue
        return validated

    if schema_type == "array":
        if not isinstance(value, list):
            raise RuntimeError(f"{path} 必须是数组。")
        item_schema = schema.get("items") if isinstance(schema.get("items"), dict) else {"type": "any"}
        return [_validate_schema_value(item_schema, item, path=f"{path}[{index}]") for index, item in enumerate(value)]

    if schema_type == "string":
        if not isinstance(value, str):
            raise RuntimeError(f"{path} 必须是字符串。")
        min_length = schema.get("minLength")
        max_length = schema.get("maxLength")
        enum_values = schema.get("enum") or []
        if isinstance(min_length, int) and len(value) < min_length:
            raise RuntimeError(f"{path} 长度不能小于 {min_length}。")
        if isinstance(max_length, int) and len(value) > max_length:
            raise RuntimeError(f"{path} 长度不能大于 {max_length}。")
        if enum_values and value not in enum_values:
            raise RuntimeError(f"{path} 必须是 {enum_values} 之一。")
        return value

    if schema_type == "integer":
        if not isinstance(value, int) or isinstance(value, bool):
            raise RuntimeError(f"{path} 必须是整数。")
        minimum = schema.get("minimum")
        maximum = schema.get("maximum")
        if isinstance(minimum, int) and value < minimum:
            raise RuntimeError(f"{path} 不能小于 {minimum}。")
        if isinstance(maximum, int) and value > maximum:
            raise RuntimeError(f"{path} 不能大于 {maximum}。")
        return value

    if schema_type == "number":
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise RuntimeError(f"{path} 必须是数字。")
        return value

    if schema_type == "boolean":
        if not isinstance(value, bool):
            raise RuntimeError(f"{path} 必须是布尔值。")
        return value

    raise RuntimeError(f"{path} 使用了不支持的 schema type：{schema_type}")


@dataclass(frozen=True)
class SkillSelection:
    """一次分发决策的结果。"""

    skill_name: str
    input_payload: dict[str, Any]
    dispatch_reason: str
    explicit: bool = False


@dataclass
class SkillExecutionContext:
    """skill 执行时共享的运行上下文。"""

    base_url: str
    default_credit_code: str | None
    site_password: str
    model_name: str
    api_base_url: str
    max_steps: int
    max_captcha_attempts: int
    browser_mode: str
    cdp_url: str
    cdp_attach_existing_page: bool
    storage_state_path: str | None
    cookies_path: str | None
    cookie_header_path: str | None
    invalid_marker_path: str | None
    persist_session: bool
    browser: AsyncBrowserSession
    page: Any
    runtime: PlaywrightToolRuntime
    run_artifact_dir: Path
    conversation_memory: ConversationMemoryContext | None = None
    rag_context: str = ""
    rag_hits: list[dict[str, Any]] | None = None


class AgentSkill:
    """所有 skill 的抽象基类。"""

    name = ""
    title = ""
    description = ""
    input_schema: dict[str, Any] = {"type": "object", "properties": {}, "additionalProperties": True}
    output_schema: dict[str, Any] = {"type": "object", "properties": {}, "additionalProperties": True}

    def build_prompt(self, *, context: SkillExecutionContext, input_payload: dict[str, Any]) -> str:
        """为 skill 生成自己的系统提示或执行说明。"""
        return ""

    def resolve_session_base_url(self, *, input_payload: dict[str, Any], fallback_base_url: str) -> str:
        """决定 skill 启动浏览器会话时应该从哪个页面开始。"""
        return str(fallback_base_url or DEFAULT_BASE_URL).strip() or DEFAULT_BASE_URL

    def should_persist_session(self, output_payload: dict[str, Any]) -> bool:
        """决定 skill 跑完后是否要持久化浏览器会话。"""
        return True

    def should_capture_runtime_debug(self, output_payload: dict[str, Any]) -> bool:
        """决定 skill 跑完后是否要收集运行时调试信息。"""
        return self.should_persist_session(output_payload)

    async def execute(self, *, input_payload: dict[str, Any], context: SkillExecutionContext) -> dict[str, Any]:
        """执行 skill 的核心逻辑；子类必须实现。"""
        raise NotImplementedError
