"""Flask 服务入口。

这个文件只做 HTTP 层的事情：
- 解析请求参数
- 选择对应的 skill / 查询入口
- 把结果包装成 JSON 响应
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from flask import Flask, jsonify, redirect, request

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from 对话智能体 import (  # noqa: E402
    invoke_agent_skill,
    invoke_creditchina_query,
    invoke_playwright_agent,
    list_registered_skills,
    playwright_agent_is_ready,
    render_playwright_agent_page,
    runtime_metadata,
)


def _coerce_bool(raw_value, default: bool) -> bool:
    """把表单 / JSON 中的布尔型配置统一规范成 Python bool。"""
    if raw_value is None:
        return bool(default)
    if isinstance(raw_value, str):
        return raw_value.strip().lower() not in {"0", "false", "off", "no"}
    return bool(raw_value)


def create_app() -> Flask:
    """创建 Flask app，并注册浏览器 Agent 的所有对外接口。"""
    app = Flask(__name__)
    app.config["JSON_AS_ASCII"] = False

    @app.get("/")
    def home():
        """把根路径重定向到浏览器 Agent 页面。"""
        return redirect("/playwright-agent/", code=302)

    @app.get("/healthz")
    def healthz():
        """返回运行时就绪状态，供页面和部署探针读取。"""
        ready, ready_error = playwright_agent_is_ready()
        return jsonify(
            {
                "ok": True,
                "ready": ready,
                "ready_error": ready_error,
                "runtime": runtime_metadata(),
            }
        )

    @app.get("/playwright-agent")
    @app.get("/playwright-agent/")
    def playwright_agent_page():
        """渲染内置的聊天 / 调试页面。"""
        embedded = request.args.get("embedded", "", type=str).strip().lower() in {"1", "true", "on", "yes"}
        return render_playwright_agent_page(embedded=embedded)

    @app.get("/api/playwright-agent/skills")
    @app.get("/api/playwright-agent/skills/")
    def api_playwright_agent_skills():
        """返回当前已注册的 skill 列表，方便前端或外部系统发现能力。"""
        return jsonify({"ok": True, "skills": list_registered_skills()})

    @app.post("/api/playwright-agent/chat")
    @app.post("/api/playwright-agent/chat/")
    def api_playwright_agent_chat():
        """聊天入口。

        这个入口会先做意图分发，再自动命中某个 skill 执行。
        """
        payload = request.get_json(silent=True) or {}
        if isinstance(payload, dict):
            message = str(payload.get("message") or "")
            session_id = str(payload.get("session_id") or "")
            base_url = str(payload.get("base_url") or "")
            credit_code = str(payload.get("credit_code") or "")
            site_password = str(payload.get("site_password") or "")
            model = str(payload.get("model") or "")
            api_base_url = str(payload.get("api_base_url") or "")
            browser_mode = str(payload.get("browser_mode") or "")
            cdp_url = str(payload.get("cdp_url") or "")
            skill_name = str(payload.get("skill_name") or "")
            cdp_attach_existing_page = _coerce_bool(payload.get("cdp_attach_existing_page", True), True)
            storage_state_path = str(payload.get("storage_state_path") or "")
            cookies_path = str(payload.get("cookies_path") or "")
            cookie_header_path = str(payload.get("cookie_header_path") or "")
            invalid_marker_path = str(payload.get("invalid_marker_path") or "")
            persist_session = _coerce_bool(payload.get("persist_session", True), True)
            max_steps_raw = payload.get("max_steps")
            max_captcha_attempts_raw = payload.get("max_captcha_attempts")
        else:
            message = request.form.get("message", "", type=str)
            session_id = request.form.get("session_id", "", type=str)
            base_url = request.form.get("base_url", "", type=str)
            credit_code = request.form.get("credit_code", "", type=str)
            site_password = request.form.get("site_password", "", type=str)
            model = request.form.get("model", "", type=str)
            api_base_url = request.form.get("api_base_url", "", type=str)
            browser_mode = request.form.get("browser_mode", "", type=str)
            cdp_url = request.form.get("cdp_url", "", type=str)
            skill_name = request.form.get("skill_name", "", type=str)
            cdp_attach_existing_page = _coerce_bool(
                request.form.get("cdp_attach_existing_page", "1", type=str),
                True,
            )
            storage_state_path = request.form.get("storage_state_path", "", type=str)
            cookies_path = request.form.get("cookies_path", "", type=str)
            cookie_header_path = request.form.get("cookie_header_path", "", type=str)
            invalid_marker_path = request.form.get("invalid_marker_path", "", type=str)
            persist_session = _coerce_bool(request.form.get("persist_session", "1", type=str), True)
            max_steps_raw = request.form.get("max_steps", 30, type=int)
            max_captcha_attempts_raw = request.form.get("max_captcha_attempts", 6, type=int)

        try:
            max_steps = int(max_steps_raw if max_steps_raw is not None else 30)
        except (TypeError, ValueError):
            max_steps = 30
        try:
            max_captcha_attempts = int(max_captcha_attempts_raw if max_captcha_attempts_raw is not None else 6)
        except (TypeError, ValueError):
            max_captcha_attempts = 6

        try:
            result = invoke_playwright_agent(
                message,
                session_id=session_id,
                base_url=base_url,
                credit_code=credit_code,
                site_password=site_password,
                model=model,
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
                skill_name=skill_name,
            )
            return jsonify({"ok": True, "result": result})
        except Exception as exc:
            return jsonify({"ok": False, "error": str(exc)}), 500

    @app.post("/api/playwright-agent/skills/run")
    @app.post("/api/playwright-agent/skills/run/")
    def api_playwright_agent_run_skill():
        """显式 skill 运行入口。

        适合外部系统绕过意图识别，直接点名运行某个 skill。
        """
        payload = request.get_json(silent=True) or {}
        if isinstance(payload, dict):
            skill_name = str(payload.get("skill_name") or "")
            input_payload = payload.get("input") if isinstance(payload.get("input"), dict) else {}
            session_id = str(payload.get("session_id") or "")
            base_url = str(payload.get("base_url") or "")
            credit_code = str(payload.get("credit_code") or "")
            site_password = str(payload.get("site_password") or "")
            model = str(payload.get("model") or "")
            api_base_url = str(payload.get("api_base_url") or "")
            browser_mode = str(payload.get("browser_mode") or "")
            cdp_url = str(payload.get("cdp_url") or "")
            cdp_attach_existing_page = _coerce_bool(payload.get("cdp_attach_existing_page", True), True)
            storage_state_path = str(payload.get("storage_state_path") or "")
            cookies_path = str(payload.get("cookies_path") or "")
            cookie_header_path = str(payload.get("cookie_header_path") or "")
            invalid_marker_path = str(payload.get("invalid_marker_path") or "")
            persist_session = _coerce_bool(payload.get("persist_session", True), True)
            max_steps_raw = payload.get("max_steps")
            max_captcha_attempts_raw = payload.get("max_captcha_attempts")
        else:
            skill_name = request.form.get("skill_name", "", type=str)
            input_payload = {}
            session_id = request.form.get("session_id", "", type=str)
            base_url = request.form.get("base_url", "", type=str)
            credit_code = request.form.get("credit_code", "", type=str)
            site_password = request.form.get("site_password", "", type=str)
            model = request.form.get("model", "", type=str)
            api_base_url = request.form.get("api_base_url", "", type=str)
            browser_mode = request.form.get("browser_mode", "", type=str)
            cdp_url = request.form.get("cdp_url", "", type=str)
            cdp_attach_existing_page = _coerce_bool(
                request.form.get("cdp_attach_existing_page", "1", type=str),
                True,
            )
            storage_state_path = request.form.get("storage_state_path", "", type=str)
            cookies_path = request.form.get("cookies_path", "", type=str)
            cookie_header_path = request.form.get("cookie_header_path", "", type=str)
            invalid_marker_path = request.form.get("invalid_marker_path", "", type=str)
            persist_session = _coerce_bool(request.form.get("persist_session", "1", type=str), True)
            max_steps_raw = request.form.get("max_steps", 30, type=int)
            max_captcha_attempts_raw = request.form.get("max_captcha_attempts", 6, type=int)

        try:
            max_steps = int(max_steps_raw if max_steps_raw is not None else 30)
        except (TypeError, ValueError):
            max_steps = 30
        try:
            max_captcha_attempts = int(max_captcha_attempts_raw if max_captcha_attempts_raw is not None else 6)
        except (TypeError, ValueError):
            max_captcha_attempts = 6

        try:
            result = invoke_agent_skill(
                skill_name,
                input_payload=input_payload,
                session_id=session_id,
                base_url=base_url,
                credit_code=credit_code,
                site_password=site_password,
                model=model,
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
                dispatch_reason="api.playwright_agent.skills.run",
            )
            return jsonify({"ok": True, "result": result})
        except Exception as exc:
            return jsonify({"ok": False, "error": str(exc)}), 500

    @app.post("/api/creditchina/query")
    @app.post("/api/creditchina/query/")
    @app.post("/api/playwright-agent/creditchina-query")
    @app.post("/api/playwright-agent/creditchina-query/")
    def api_creditchina_query():
        """给“信用中国查询”保留的固定业务入口。

        这个接口本质上仍然是对 creditchina 相关 skill 的一个外壳。
        """
        payload = request.get_json(silent=True) or {}
        if isinstance(payload, dict):
            credit_code = str(payload.get("credit_code") or "")
            session_id = str(payload.get("session_id") or "")
            base_url = str(payload.get("base_url") or "")
            site_password = str(payload.get("site_password") or "")
            browser_mode = str(payload.get("browser_mode") or "")
            cdp_url = str(payload.get("cdp_url") or "")
            cdp_attach_existing_page = _coerce_bool(payload.get("cdp_attach_existing_page", True), True)
            storage_state_path = str(payload.get("storage_state_path") or "")
            cookies_path = str(payload.get("cookies_path") or "")
            cookie_header_path = str(payload.get("cookie_header_path") or "")
            invalid_marker_path = str(payload.get("invalid_marker_path") or "")
            persist_session = _coerce_bool(payload.get("persist_session", True), True)
            max_captcha_attempts_raw = payload.get("max_captcha_attempts")
        else:
            credit_code = request.form.get("credit_code", "", type=str)
            session_id = request.form.get("session_id", "", type=str)
            base_url = request.form.get("base_url", "", type=str)
            site_password = request.form.get("site_password", "", type=str)
            browser_mode = request.form.get("browser_mode", "", type=str)
            cdp_url = request.form.get("cdp_url", "", type=str)
            cdp_attach_existing_page = _coerce_bool(
                request.form.get("cdp_attach_existing_page", "1", type=str),
                True,
            )
            storage_state_path = request.form.get("storage_state_path", "", type=str)
            cookies_path = request.form.get("cookies_path", "", type=str)
            cookie_header_path = request.form.get("cookie_header_path", "", type=str)
            invalid_marker_path = request.form.get("invalid_marker_path", "", type=str)
            persist_session = _coerce_bool(request.form.get("persist_session", "1", type=str), True)
            max_captcha_attempts_raw = request.form.get("max_captcha_attempts", 6, type=int)

        try:
            max_captcha_attempts = int(max_captcha_attempts_raw if max_captcha_attempts_raw is not None else 6)
        except (TypeError, ValueError):
            max_captcha_attempts = 6

        try:
            result = invoke_creditchina_query(
                credit_code,
                session_id=session_id,
                base_url=base_url,
                site_password=site_password,
                max_captcha_attempts=max_captcha_attempts,
                browser_mode=browser_mode,
                cdp_url=cdp_url,
                cdp_attach_existing_page=cdp_attach_existing_page,
                storage_state_path=storage_state_path,
                cookies_path=cookies_path,
                cookie_header_path=cookie_header_path,
                invalid_marker_path=invalid_marker_path,
                persist_session=persist_session,
            )
            return jsonify({"ok": True, "result": result})
        except Exception as exc:
            return jsonify({"ok": False, "error": str(exc)}), 500

    return app


app = create_app()


if __name__ == "__main__":
    host = os.getenv("HOST", "127.0.0.1")
    port = int(os.getenv("PORT", "8800"))
    app.run(host=host, port=port, threaded=True)
