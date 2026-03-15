from __future__ import annotations

import os
import sys
from pathlib import Path

from flask import Flask, jsonify, redirect, request

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from 对话智能体 import (  # noqa: E402
    invoke_creditchina_query,
    invoke_playwright_agent,
    playwright_agent_is_ready,
    render_playwright_agent_page,
    runtime_metadata,
)


def _coerce_bool(raw_value, default: bool) -> bool:
    if raw_value is None:
        return bool(default)
    if isinstance(raw_value, str):
        return raw_value.strip().lower() not in {"0", "false", "off", "no"}
    return bool(raw_value)


def create_app() -> Flask:
    app = Flask(__name__)
    app.config["JSON_AS_ASCII"] = False

    @app.get("/")
    def home():
        return redirect("/playwright-agent/", code=302)

    @app.get("/healthz")
    def healthz():
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
        embedded = request.args.get("embedded", "", type=str).strip().lower() in {"1", "true", "on", "yes"}
        return render_playwright_agent_page(embedded=embedded)

    @app.post("/api/playwright-agent/chat")
    @app.post("/api/playwright-agent/chat/")
    def api_playwright_agent_chat():
        payload = request.get_json(silent=True) or {}
        if isinstance(payload, dict):
            message = str(payload.get("message") or "")
            base_url = str(payload.get("base_url") or "")
            credit_code = str(payload.get("credit_code") or "")
            site_password = str(payload.get("site_password") or "")
            mode = str(payload.get("mode") or "react")
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
        else:
            message = request.form.get("message", "", type=str)
            base_url = request.form.get("base_url", "", type=str)
            credit_code = request.form.get("credit_code", "", type=str)
            site_password = request.form.get("site_password", "", type=str)
            mode = request.form.get("mode", "react", type=str)
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

        try:
            max_steps = int(max_steps_raw if max_steps_raw is not None else 30)
        except (TypeError, ValueError):
            max_steps = 30

        try:
            result = invoke_playwright_agent(
                message,
                base_url=base_url,
                credit_code=credit_code,
                site_password=site_password,
                mode=mode,
                model=model,
                api_base_url=api_base_url,
                max_steps=max_steps,
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

    @app.post("/api/creditchina/query")
    @app.post("/api/creditchina/query/")
    @app.post("/api/playwright-agent/creditchina-query")
    @app.post("/api/playwright-agent/creditchina-query/")
    def api_creditchina_query():
        payload = request.get_json(silent=True) or {}
        if isinstance(payload, dict):
            credit_code = str(payload.get("credit_code") or "")
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
