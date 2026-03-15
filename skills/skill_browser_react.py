"""开放式浏览器 skill 实现。"""

from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage

from 智能体配置 import DEFAULT_CAPTCHA_OCR_MODEL, DEFAULT_CREDIT_CODE

from .base import (
    AgentSkill,
    SkillExecutionContext,
    _build_agent_model,
    _coerce_message_text,
    _extract_first_url,
    _extract_used_tools,
)

try:
    from langchain.agents import create_agent
except ImportError:
    create_agent = None


class BrowserReactSkill(AgentSkill):
    """开放式浏览器技能。

    这个 skill 会把浏览器工具集暴露给 ReAct agent，自主决定观察、点击和读取页面。
    """

    name = "browser_react"
    title = "通用浏览器技能"
    description = "开放式浏览器对话 skill。适合观察页面、分析 DOM、点击输入、挑战页诊断等非固定流程任务。"
    input_schema = {
        "type": "object",
        "required": ["message"],
        "properties": {
            "message": {"type": "string", "minLength": 1},
        },
        "additionalProperties": False,
    }
    output_schema = {
        "type": "object",
        "required": ["mode", "reply", "used_tools"],
        "properties": {
            "mode": {"type": "string", "minLength": 1},
            "reply": {"type": "string"},
            "used_tools": {
                "type": "array",
                "items": {"type": "string"},
            },
            "page_result": {"type": "object", "nullable": True, "additionalProperties": True},
            "agent_debug": {"type": "object", "nullable": True, "additionalProperties": True},
        },
        "additionalProperties": True,
    }

    def build_prompt(self, *, context: SkillExecutionContext, input_payload: dict[str, object]) -> str:
        """生成 browser_react skill 的系统提示。"""
        effective_credit_code = str(context.default_credit_code or DEFAULT_CREDIT_CODE).strip() or DEFAULT_CREDIT_CODE
        password_line = (
            f"如果目标站先出现系统密码页，可使用系统密码：{context.site_password}"
            if str(context.site_password or "").strip()
            else "如果目标站先出现系统密码页，需要先观察页面再决定如何处理。"
        )
        memory_block = ""
        if context.conversation_memory is not None:
            rendered_memory = context.conversation_memory.prompt_block()
            if rendered_memory:
                memory_block = f"\n对话记忆：\n{rendered_memory}\n"
        rag_block = ""
        if str(context.rag_context or "").strip():
            rag_block = (
                "\n本地知识库检索结果：\n"
                f"{str(context.rag_context).strip()}\n"
            )
        return (
            "当前 skill：browser_react\n"
            "你是一个统一的中文 Playwright 浏览器技能执行器。\n"
            "你的默认行为是正常聊天、解释、讨论方案，而不是默认操作页面。\n"
            "只有在用户明确要求访问页面、查看 DOM、分析 selector、执行自动化、"
            "输入点击提交、识别验证码、查询法务测试站时，你才调用页面工具。\n"
            f"当前起始地址：{context.base_url}\n"
            f"当前默认统一社会信用代码：{effective_credit_code}\n"
            f"{password_line}\n"
            f"{memory_block}"
            f"{rag_block}"
            "规则：\n"
            "1. 普通问候、闲聊、解释架构、讨论实现方案、追问原因时，不要调用工具。\n"
            "2. 即使页面里预填了统一社会信用代码，也不能据此擅自发起查询。\n"
            "3. 页面结构未知时，先调用 `open_start_page` 或 `open_page`，再调用 `inspect_page`，不要先猜 selector。\n"
            "4. 导航、点击、提交后，优先重新 `inspect_page` 或 `wait_for_selector` 确认页面状态。\n"
            "5. 如果出现系统密码页，优先使用 `unlock_site_password`；如果出现验证码图，优先使用 `solve_captcha_and_submit`。\n"
            "6. 如果用户明确要求做查询，但没有再给新的信用代码，可以使用上面的默认统一社会信用代码。\n"
            "7. 如果用户明确要求执行“信用中国”固定查询流程，优先调用 `run_creditchina_query_and_save`；不要把搜索结果列表页当成最终完成，列表命中后要继续进入详情页或详情接口。\n"
            "8. 如果 `inspect_page` 显示标题/文本/元素都为空，或页面像脚本壳页/挑战页，先用 `detect_access_challenge` 判断，再用 `wait_for_seconds`、`retry_on_access_challenge` 处理。\n"
            "9. 如果本地知识库检索结果已经足以回答用户问题，优先依据知识库回答，不要编造；知识库没提到就明确说未提到。\n"
            "10. 当需要把问题交代清楚时，可调用 `capture_page_artifacts` 保存整页截图与完整 HTML，并把路径告诉用户。\n"
            "11. 工具返回后，用中文自然总结结果；如果工具失败，解释失败点。"
        )

    def resolve_session_base_url(self, *, input_payload: dict[str, object], fallback_base_url: str) -> str:
        """优先从用户消息里提取 URL 作为浏览器起始页。"""
        extracted = _extract_first_url(str(input_payload.get("message") or ""))
        return extracted or super().resolve_session_base_url(input_payload=input_payload, fallback_base_url=fallback_base_url)

    def should_persist_session(self, output_payload: dict[str, object]) -> bool:
        """只有真正调用过浏览器工具时，才值得持久化会话。"""
        return bool(output_payload.get("used_tools"))

    async def execute(self, *, input_payload: dict[str, object], context: SkillExecutionContext) -> dict[str, object]:
        """运行开放式浏览器 ReAct 流程，并返回统一 skill 输出。"""
        if create_agent is None:
            raise RuntimeError("当前环境缺少统一 agent 依赖；请先安装 langchain。")
        model = _build_agent_model(model_name=context.model_name, api_base_url=context.api_base_url)
        prompt = self.build_prompt(context=context, input_payload=input_payload)
        agent = create_agent(
            model=model,
            tools=context.runtime.build_async_tools(page=context.page, artifact_dir=context.run_artifact_dir),
            system_prompt=prompt,
            debug=False,
        )
        result = await agent.ainvoke(
            {
                "messages": [
                    *[
                        HumanMessage(content=item.content) if item.role != "assistant" else AIMessage(content=item.content)
                        for item in (context.conversation_memory.recent_messages or [])
                        if str(item.content or "").strip()
                    ],
                    HumanMessage(content=str(input_payload.get("message") or "").strip()),
                ]
            },
            config={"recursion_limit": context.max_steps},
        )
        messages = list(result.get("messages") or [])
        reply_text = ""
        for message in reversed(messages):
            if isinstance(message, AIMessage):
                reply_text = _coerce_message_text(message).strip()
                if reply_text:
                    break
        if not reply_text:
            raise RuntimeError("browser_react 没有返回可展示的最终回复。")

        used_tools = _extract_used_tools(messages)
        page_result = await context.runtime._try_read_result_json_async(context.page) if used_tools else None
        return {
            "mode": "unified",
            "reply": reply_text,
            "page_result": page_result,
            "used_tools": used_tools,
            "agent_debug": {
                "model": context.model_name,
                "api_base_url": context.api_base_url,
                "captcha_ocr_model": DEFAULT_CAPTCHA_OCR_MODEL,
                "message_count": len(messages),
                "rag_hit_count": len(context.rag_hits or []),
                "skill_prompt": prompt,
            },
        }
