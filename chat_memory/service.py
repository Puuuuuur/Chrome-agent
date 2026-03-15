"""聊天记忆服务层。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage

try:
    from langchain_openai import ChatOpenAI
except ImportError:  # pragma: no cover
    ChatOpenAI = None

from tools.tool_model_client import load_agent_api_key
from 智能体配置 import (
    CHAT_MEMORY_POSTGRES_CONNECT_TIMEOUT,
    CHAT_MEMORY_POSTGRES_DSN,
    CHAT_MEMORY_RECENT_MESSAGES_LIMIT,
    CHAT_MEMORY_SUMMARY_KEEP_RECENT_MESSAGES,
    CHAT_MEMORY_SUMMARY_MAX_CHARS,
    CHAT_MEMORY_SUMMARY_MODEL,
    CHAT_MEMORY_SUMMARY_TRIGGER_MESSAGES,
    DEFAULT_API_BASE_URL,
)

from .models import ChatMessageRecord, ConversationMemoryContext, generate_session_id
from .postgres_store import PostgresChatMemoryStore, SessionRow

_CREDIT_CODE_PATTERN = re.compile(r"\b[0-9A-Z]{18}\b", re.IGNORECASE)
_URL_PATTERN = re.compile(r"https?://[^\s'\"<>]+", re.IGNORECASE)
_INTRO_PATTERNS = (
    re.compile(r"(?:介绍一下|介绍下|聊聊|说说|讲讲|科普一下)(?P<name>[^，。！？\n]+)"),
    re.compile(r"(?P<name>[^，。！？\n]{2,60})(?:是不是|是|属于).{0,8}(?:985|211)", re.IGNORECASE),
)


@dataclass
class RecordTurnResult:
    """记录一轮对话后的返回信息。"""

    session_id: str
    turn_count: int


class ConversationMemoryService:
    """会话记忆协调器。"""

    def __init__(self) -> None:
        self._store = PostgresChatMemoryStore(
            CHAT_MEMORY_POSTGRES_DSN,
            connect_timeout=CHAT_MEMORY_POSTGRES_CONNECT_TIMEOUT,
        )
        self._summary_model = None
        self._summary_model_error = ""
        self._ready = False

    def ensure_ready(self) -> None:
        """初始化 PostgreSQL 和 Milvus 元数据。"""
        if self._ready:
            return
        self._store.ensure_schema()
        self._ready = True

    def healthcheck(self) -> None:
        """探测依赖是否可用。"""
        self.ensure_ready()
        self._store.healthcheck()

    def runtime_metadata(self) -> dict[str, str]:
        """返回记忆系统相关元数据。"""
        return {
            "chat_memory_backend": "postgresql",
            "chat_memory_enabled": "1",
            "chat_memory_recent_limit": str(CHAT_MEMORY_RECENT_MESSAGES_LIMIT),
            "chat_memory_summary_trigger": str(CHAT_MEMORY_SUMMARY_TRIGGER_MESSAGES),
        }

    def prepare_context(self, session_id: str | None, user_message: str) -> ConversationMemoryContext:
        """为当前用户输入加载会话记忆。"""
        self.ensure_ready()
        resolved_session_id = str(session_id or "").strip() or generate_session_id()
        session_row, created = self._store.get_or_create_session(resolved_session_id)
        recent_messages = self._store.load_recent_messages(
            resolved_session_id,
            limit=CHAT_MEMORY_RECENT_MESSAGES_LIMIT,
        )

        return ConversationMemoryContext(
            session_id=resolved_session_id,
            created=created,
            rolling_summary=session_row.rolling_summary,
            recent_messages=recent_messages,
            slots=dict(session_row.slots or {}),
            turn_count=session_row.turn_count,
        )

    def record_turn(
        self,
        context: ConversationMemoryContext,
        *,
        user_message: str,
        assistant_result: dict[str, Any],
    ) -> RecordTurnResult:
        """把本轮 user/assistant 消息写入数据库，并刷新摘要和向量索引。"""
        self.ensure_ready()
        user_record = self._store.append_message(
            context.session_id,
            message_id=f"msg_{uuid4().hex}",
            role="user",
            content=str(user_message or ""),
            meta={
                "type": "chat_user",
            },
        )
        assistant_text = str(assistant_result.get("reply") or "").strip()
        if not assistant_text:
            assistant_text = str(assistant_result or "")
        assistant_record = self._store.append_message(
            context.session_id,
            message_id=f"msg_{uuid4().hex}",
            role="assistant",
            content=assistant_text,
            meta=self._assistant_meta(assistant_result),
        )

        session_row, _created = self._store.get_or_create_session(context.session_id)
        next_slots = self._merge_slots(
            existing=dict(session_row.slots or {}),
            user_message=user_message,
            assistant_result=assistant_result,
        )
        title = self._derive_session_title(user_message=user_message, existing_title=session_row.title)
        self._store.update_session_state(
            context.session_id,
            slots=next_slots,
            title=title,
        )
        updated_session = self._maybe_refresh_summary(context.session_id, next_slots)
        return RecordTurnResult(
            session_id=context.session_id,
            turn_count=updated_session.turn_count if updated_session is not None else session_row.turn_count,
        )

    def recent_messages_for_model(self, context: ConversationMemoryContext) -> list[BaseMessage]:
        """把最近消息转换成 LangChain message。"""
        messages: list[BaseMessage] = []
        for item in context.recent_messages:
            text = str(item.content or "").strip()
            if not text:
                continue
            if item.role == "assistant":
                messages.append(AIMessage(content=text))
            else:
                messages.append(HumanMessage(content=text))
        return messages

    def _assistant_meta(self, assistant_result: dict[str, Any]) -> dict[str, Any]:
        skill = assistant_result.get("skill") if isinstance(assistant_result.get("skill"), dict) else {}
        normalized = assistant_result.get("normalized") if isinstance(assistant_result.get("normalized"), dict) else {}
        return {
            "type": "chat_assistant",
            "skill_name": str(skill.get("name") or ""),
            "dispatch_reason": str(skill.get("dispatch_reason") or ""),
            "used_tools": list(assistant_result.get("used_tools") or []),
            "credit_code": str(assistant_result.get("credit_code") or normalized.get("credit_code") or ""),
            "enterprise_name": str(normalized.get("enterprise_name") or ""),
        }

    def _derive_session_title(self, *, user_message: str, existing_title: str) -> str | None:
        normalized_existing = str(existing_title or "").strip()
        if normalized_existing:
            return None
        text = str(user_message or "").strip()
        if not text:
            return None
        return text[:36]

    def _merge_slots(
        self,
        *,
        existing: dict[str, Any],
        user_message: str,
        assistant_result: dict[str, Any],
    ) -> dict[str, Any]:
        slots = dict(existing or {})
        all_text = "\n".join(
            [
                str(user_message or ""),
                str(assistant_result.get("reply") or ""),
            ]
        )
        credit_code_match = _CREDIT_CODE_PATTERN.search(all_text.upper())
        if credit_code_match:
            slots["last_credit_code"] = credit_code_match.group(0)

        skill = assistant_result.get("skill") if isinstance(assistant_result.get("skill"), dict) else {}
        skill_name = str(skill.get("name") or "").strip()
        if skill_name:
            slots["last_skill_name"] = skill_name

        normalized = assistant_result.get("normalized") if isinstance(assistant_result.get("normalized"), dict) else {}
        enterprise_name = str(normalized.get("enterprise_name") or "").strip()
        if enterprise_name:
            slots["last_subject_name"] = enterprise_name
            slots["last_subject_type"] = "organization"

        base_url_match = _URL_PATTERN.search(str(user_message or ""))
        if base_url_match:
            slots["last_base_url"] = base_url_match.group(0)

        if not enterprise_name:
            user_subject = self._extract_subject_name(user_message)
            if user_subject:
                slots["last_subject_name"] = user_subject
                slots["last_subject_type"] = "general"

        return slots

    def _extract_subject_name(self, text: str) -> str:
        raw = str(text or "").strip()
        for pattern in _INTRO_PATTERNS:
            match = pattern.search(raw)
            if match:
                return str(match.group("name") or "").strip("：:，,。.!！？? ")
        return ""

    def _maybe_refresh_summary(self, session_id: str, slots: dict[str, Any]) -> SessionRow | None:
        session_row, _created = self._store.get_or_create_session(session_id)
        turn_count = int(session_row.turn_count or 0)
        summarize_until_seq = turn_count - CHAT_MEMORY_SUMMARY_KEEP_RECENT_MESSAGES
        if turn_count < CHAT_MEMORY_SUMMARY_TRIGGER_MESSAGES:
            return self._store.update_session_state(session_id, slots=slots)
        if summarize_until_seq <= int(session_row.archived_until_seq or 0):
            return self._store.update_session_state(session_id, slots=slots)

        pending_messages = self._store.load_unsummarized_messages(
            session_id,
            from_seq_exclusive=int(session_row.archived_until_seq or 0),
            to_seq_inclusive=summarize_until_seq,
        )
        if not pending_messages:
            return self._store.update_session_state(session_id, slots=slots)
        new_summary = self._summarize_messages(
            current_summary=session_row.rolling_summary,
            messages=pending_messages,
        )
        return self._store.update_session_state(
            session_id,
            rolling_summary=new_summary,
            archived_until_seq=pending_messages[-1].seq,
            slots=slots,
        )

    def _summarize_messages(
        self,
        *,
        current_summary: str,
        messages: list[ChatMessageRecord],
    ) -> str:
        raw_lines = [
            f"{item.role}#{item.seq}: {str(item.content or '').strip()[:260]}"
            for item in messages
            if str(item.content or "").strip()
        ]
        if not raw_lines:
            return str(current_summary or "").strip()

        prompt = (
            "请把下面新增的对话片段整理成简洁中文摘要，保留后续多轮对话真正有用的信息："
            "主题、结论、已确认事实、仍待处理事项、主体名称、统一社会信用代码、URL、技能名。"
            "不要写寒暄，不要编造。\n\n"
            f"已有摘要：\n{str(current_summary or '').strip() or '（无）'}\n\n"
            "新增片段：\n"
            + "\n".join(raw_lines)
        )
        generated = self._invoke_summary_model(prompt)
        if generated:
            return generated[:CHAT_MEMORY_SUMMARY_MAX_CHARS].strip()

        fallback_parts: list[str] = []
        if str(current_summary or "").strip():
            fallback_parts.append(str(current_summary or "").strip())
        fallback_parts.extend(raw_lines[-10:])
        return "\n".join(fallback_parts)[:CHAT_MEMORY_SUMMARY_MAX_CHARS].strip()

    def _invoke_summary_model(self, prompt: str) -> str:
        if ChatOpenAI is None:
            return ""
        if self._summary_model is False:
            return ""
        if self._summary_model is None:
            try:
                self._summary_model = ChatOpenAI(
                    model=CHAT_MEMORY_SUMMARY_MODEL,
                    temperature=0,
                    api_key=load_agent_api_key(),
                    base_url=DEFAULT_API_BASE_URL,
                    timeout=60,
                    max_retries=2,
                    use_responses_api=True,
                )
            except Exception as exc:
                self._summary_model = False
                self._summary_model_error = str(exc)
                return ""
        try:
            response = self._summary_model.invoke(
                [
                    SystemMessage(content="你是一个严谨的会话记忆整理器，只输出中文摘要正文。"),
                    HumanMessage(content=prompt),
                ]
            )
        except Exception:
            return ""
        content = getattr(response, "content", "")
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, dict) and item.get("text"):
                    parts.append(str(item.get("text")))
            return "".join(parts).strip()
        return str(content or "").strip()
