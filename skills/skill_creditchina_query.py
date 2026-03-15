"""标准 creditchina 查询 skill 实现。"""

from __future__ import annotations

from 智能体配置 import DEFAULT_CAPTCHA_OCR_MODEL, DEFAULT_CREDIT_CODE

from .base import (
    AgentSkill,
    SkillExecutionContext,
    _build_creditchina_reply,
    _extract_creditchina_normalized_payload,
    _extract_creditchina_page_result,
)


class CreditChinaQuerySkill(AgentSkill):
    """标准 creditchina 查询 skill。

    策略是先走 private-api，失败时再回退到 DOM 流。
    """

    name = "creditchina_query"
    title = "信用中国固定查询"
    description = "执行信用中国固定查询 skill，优先走 private-api 流，失败后回退 DOM + 验证码流程。"
    input_schema = {
        "type": "object",
        "required": ["credit_code"],
        "properties": {
            "credit_code": {"type": "string", "minLength": 18, "maxLength": 18},
            "result_name": {"type": "string", "default": ""},
            "max_captcha_attempts": {"type": "integer", "default": 6, "minimum": 1, "maximum": 8},
        },
        "additionalProperties": False,
    }
    output_schema = {
        "type": "object",
        "required": ["mode", "reply", "credit_code", "normalized", "query_result", "used_tools"],
        "properties": {
            "mode": {"type": "string", "minLength": 1},
            "reply": {"type": "string"},
            "credit_code": {"type": "string", "minLength": 18, "maxLength": 18},
            "normalized": {"type": "object", "additionalProperties": True},
            "query_result": {"type": "object", "additionalProperties": True},
            "used_tools": {"type": "array", "items": {"type": "string"}},
            "page_result": {"type": "object", "nullable": True, "additionalProperties": True},
            "agent_debug": {"type": "object", "nullable": True, "additionalProperties": True},
        },
        "additionalProperties": True,
    }

    def build_prompt(self, *, context: SkillExecutionContext, input_payload: dict[str, object]) -> str:
        """生成标准 creditchina 查询 skill 的执行说明。"""
        credit_code = str(input_payload.get("credit_code") or context.default_credit_code or DEFAULT_CREDIT_CODE).strip().upper()
        return (
            "当前 skill：creditchina_query\n"
            "目标：执行一次完整的“信用中国固定查询”。\n"
            f"统一社会信用代码：{credit_code}\n"
            f"起始地址：{context.base_url}\n"
            "执行约束：\n"
            "1. 先复用浏览器真实会话与当前站点上下文。\n"
            "2. 优先跑 private-api 查询流；如果当前只是搜索结果列表页命中主体，不要视为完成，要继续进入详情页或详情接口。\n"
            "3. 只有拿到详情页字段或 private-api 详情字段后，才算查询完成。\n"
            "4. 只有当 private-api 和详情页都失败时，才允许回退成列表页简版结果。\n"
            "5. 最终必须把结果写到当前运行目录，并返回可直接给人阅读的结果摘要。"
        )

    async def execute(self, *, input_payload: dict[str, object], context: SkillExecutionContext) -> dict[str, object]:
        """执行标准 creditchina 查询，并返回统一结果结构。"""
        prompt = self.build_prompt(context=context, input_payload=input_payload)
        credit_code = str(input_payload.get("credit_code") or "").strip().upper()
        query_result = await context.runtime.run_creditchina_query_and_save_async(
            context.page,
            context.run_artifact_dir,
            credit_code=credit_code,
            result_name=str(input_payload.get("result_name") or "").strip(),
            max_captcha_attempts=int(input_payload.get("max_captcha_attempts") or context.max_captcha_attempts),
        )
        normalized_payload = _extract_creditchina_normalized_payload(query_result)
        return {
            "mode": "creditchina_direct",
            "reply": _build_creditchina_reply(query_result),
            "credit_code": credit_code,
            "normalized": normalized_payload.get("normalized") if isinstance(normalized_payload, dict) else {},
            "query_result": query_result,
            "page_result": _extract_creditchina_page_result(query_result),
            "used_tools": ["run_creditchina_query_and_save"],
            "agent_debug": {
                "captcha_ocr_model": DEFAULT_CAPTCHA_OCR_MODEL,
                "skill_prompt": prompt,
            },
        }
