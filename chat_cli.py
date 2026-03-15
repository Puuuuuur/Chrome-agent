#!/usr/bin/env python3
"""终端交互式聊天脚本。

这个脚本通过本地 HTTP 接口与浏览器 Agent 持续对话，
避免每次都手写 curl。
"""

from __future__ import annotations

import argparse
import json
import platform
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests


DEFAULT_CHAT_ENDPOINT = "http://127.0.0.1:8800/api/playwright-agent/chat"
DEFAULT_HEALTH_PATH = "/healthz"
DEFAULT_STATE_PATH = ".session/chat_cli_state.json"


@dataclass
class CliState:
    """保存当前会话里可动态调整的参数。"""

    endpoint: str
    session_id: str
    base_url: str
    credit_code: str
    skill_name: str
    model: str
    api_base_url: str
    browser_mode: str
    cdp_url: str
    cdp_attach_existing_page: bool
    storage_state_path: str
    cookies_path: str
    cookie_header_path: str
    invalid_marker_path: str
    max_steps: int
    max_captcha_attempts: int
    persist_session: bool

    def request_payload(self, message: str) -> dict[str, Any]:
        """把当前状态和用户消息组装成聊天请求体。"""
        payload: dict[str, Any] = {
            "session_id": self.session_id,
            "message": str(message or "").strip(),
            "base_url": self.base_url,
            "credit_code": self.credit_code,
            "model": self.model,
            "api_base_url": self.api_base_url,
            "browser_mode": self.browser_mode,
            "cdp_url": self.cdp_url,
            "cdp_attach_existing_page": self.cdp_attach_existing_page,
            "storage_state_path": self.storage_state_path,
            "cookies_path": self.cookies_path,
            "cookie_header_path": self.cookie_header_path,
            "invalid_marker_path": self.invalid_marker_path,
            "max_steps": self.max_steps,
            "max_captcha_attempts": self.max_captcha_attempts,
            "persist_session": self.persist_session,
        }
        if self.skill_name:
            payload["skill_name"] = self.skill_name
        return payload


def _health_endpoint_from_chat_endpoint(endpoint: str) -> str:
    """根据聊天接口地址推导健康检查地址。"""
    parsed = urlparse(str(endpoint or "").strip())
    if not parsed.scheme or not parsed.netloc:
        raise RuntimeError(f"无效的 endpoint：{endpoint}")
    return f"{parsed.scheme}://{parsed.netloc}{DEFAULT_HEALTH_PATH}"


def _probe_health(endpoint: str, *, timeout_seconds: float = 2.0) -> tuple[bool, str]:
    """探测本地 Agent 服务是否已经就绪。"""
    health_url = _health_endpoint_from_chat_endpoint(endpoint)
    try:
        response = requests.get(health_url, timeout=max(0.5, float(timeout_seconds)))
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:
        return False, str(exc)
    return bool(payload.get("ready")), str(payload.get("ready_error") or "")


def _choose_start_script(root_dir: Path) -> Path:
    """根据当前平台选择对应的启动脚本。"""
    system_name = platform.system()
    if system_name == "Darwin":
        script_path = root_dir / "start_mac.sh"
    elif system_name == "Linux":
        script_path = root_dir / "start_linux.sh"
    else:
        raise RuntimeError(f"当前平台暂不支持自动启动：{system_name}")
    if not script_path.exists():
        raise RuntimeError(f"未找到启动脚本：{script_path}")
    return script_path


def _tail_log(log_path: Path, max_lines: int = 40) -> str:
    """读取启动日志尾部，便于启动失败时快速排查。"""
    if not log_path.exists():
        return ""
    lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    return "\n".join(lines[-max(1, int(max_lines)):])


def _ensure_agent_running(endpoint: str, root_dir: Path) -> subprocess.Popen[str] | None:
    """确保本地 Agent 服务已启动；若未启动则自动拉起。"""
    ready, ready_error = _probe_health(endpoint)
    if ready:
        print("[agent] 检测到本地服务已就绪，直接进入对话。")
        return None

    script_path = _choose_start_script(root_dir)
    log_dir = root_dir / ".session"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "chat-cli-start.log"
    log_file = log_path.open("w", encoding="utf-8")

    process = subprocess.Popen(
        ["bash", str(script_path)],
        cwd=str(root_dir),
        stdout=log_file,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
    )
    print(f"[agent] 本地服务未就绪，已启动 {script_path.name} (pid={process.pid})")
    print(f"[agent] 启动日志：{log_path}")

    started_ready = False
    last_error = ready_error
    for _ in range(180):
        if process.poll() is not None:
            break
        time.sleep(1.0)
        started_ready, last_error = _probe_health(endpoint, timeout_seconds=2.0)
        if started_ready:
            print("[agent] 服务已就绪。")
            return process

    if not started_ready:
        tail = _tail_log(log_path)
        if tail:
            print("[agent] 启动日志尾部：")
            print(tail)
        raise RuntimeError(f"等待 Agent 服务就绪超时：{last_error or '未知错误'}")

    return process


def _build_parser() -> argparse.ArgumentParser:
    """构造命令行参数解析器。"""
    parser = argparse.ArgumentParser(description="浏览器 Agent 终端对话脚本。")
    parser.add_argument("--endpoint", default=DEFAULT_CHAT_ENDPOINT, help=f"聊天接口地址，默认：{DEFAULT_CHAT_ENDPOINT}")
    parser.add_argument("--base-url", default="https://www.creditchina.gov.cn/", help="默认目标站点 URL")
    parser.add_argument("--credit-code", default="91420000177570439L", help="默认统一社会信用代码")
    parser.add_argument("--skill", default="", help="默认显式 skill；留空表示自动分发")
    parser.add_argument("--model", default="", help="覆盖后端默认模型名")
    parser.add_argument("--api-base-url", default="", help="覆盖后端默认 API Base URL")
    parser.add_argument("--browser-mode", default="", help="覆盖浏览器模式")
    parser.add_argument("--cdp-url", default="", help="覆盖 CDP 地址")
    parser.add_argument("--cdp-attach-existing-page", action="store_true", default=False, help="强制附着已有页面")
    parser.add_argument("--storage-state-path", default="", help="自定义 storage state 路径")
    parser.add_argument("--cookies-path", default="", help="自定义 cookies.json 路径")
    parser.add_argument("--cookie-header-path", default="", help="自定义 cookie header 路径")
    parser.add_argument("--invalid-marker-path", default="", help="自定义 session invalid 标记路径")
    parser.add_argument("--max-steps", type=int, default=30, help="agent 最大步数")
    parser.add_argument("--max-captcha-attempts", type=int, default=6, help="验证码最大尝试次数")
    parser.add_argument("--no-persist-session", action="store_true", help="禁用会话持久化")
    return parser


def _print_help() -> None:
    """打印 REPL 内置命令帮助。"""
    print(
        "\n可用命令：\n"
        "/help                 查看帮助\n"
        "/exit                 退出\n"
        "/status               查看当前会话参数\n"
        "/new                  创建新 session_id\n"
        "/skill <name|auto>    设置显式 skill；auto 表示自动分发\n"
        "/base <url>           设置默认站点 URL\n"
        "/credit <code>        设置默认统一社会信用代码\n"
        "/endpoint <url>       设置聊天接口地址\n"
        "/persist <on|off>     开关会话持久化\n"
        "/restart              重新探测并自动拉起本地服务\n"
    )


def _print_status(state: CliState) -> None:
    """打印当前会话配置。"""
    print(
        json.dumps(
            {
                "endpoint": state.endpoint,
                "session_id": state.session_id,
                "base_url": state.base_url,
                "credit_code": state.credit_code,
                "skill_name": state.skill_name or "auto",
                "model": state.model,
                "api_base_url": state.api_base_url,
                "browser_mode": state.browser_mode,
                "cdp_url": state.cdp_url,
                "cdp_attach_existing_page": state.cdp_attach_existing_page,
                "max_steps": state.max_steps,
                "max_captcha_attempts": state.max_captcha_attempts,
                "persist_session": state.persist_session,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def _handle_command(raw_line: str, state: CliState, *, root_dir: Path) -> bool:
    """处理 REPL 命令；返回 True 表示继续会话。"""
    parts = shlex.split(raw_line)
    if not parts:
        return True

    command = parts[0].lower()
    arg = parts[1] if len(parts) > 1 else ""

    if command in {"/exit", "/quit"}:
        return False
    if command == "/help":
        _print_help()
        return True
    if command == "/status":
        _print_status(state)
        return True
    if command == "/new":
        state.session_id = f"sess_{int(time.time())}_{int(time.time_ns() % 1000000)}"
        print(f"已创建新 session_id: {state.session_id}")
        return True
    if command == "/skill":
        state.skill_name = "" if arg.lower() in {"", "auto"} else arg
        print(f"已设置 skill: {state.skill_name or 'auto'}")
        return True
    if command == "/base":
        state.base_url = arg
        print(f"已设置 base_url: {state.base_url}")
        return True
    if command == "/credit":
        state.credit_code = arg.upper()
        print(f"已设置 credit_code: {state.credit_code}")
        return True
    if command == "/endpoint":
        state.endpoint = arg
        print(f"已设置 endpoint: {state.endpoint}")
        return True
    if command == "/persist":
        normalized = arg.lower()
        if normalized in {"on", "1", "true"}:
            state.persist_session = True
        elif normalized in {"off", "0", "false"}:
            state.persist_session = False
        else:
            print("只接受 /persist on 或 /persist off")
            return True
        print(f"已设置 persist_session: {state.persist_session}")
        return True
    if command == "/restart":
        _ensure_agent_running(state.endpoint, root_dir)
        return True

    print("未知命令，输入 /help 查看帮助。")
    return True


def _print_response(result: dict[str, Any]) -> None:
    """把后端返回整理成适合终端查看的输出。"""
    reply = str(result.get("reply") or "").strip()
    skill = result.get("skill") if isinstance(result.get("skill"), dict) else {}
    used_tools = result.get("used_tools") if isinstance(result.get("used_tools"), list) else []
    query_result = result.get("query_result") if isinstance(result.get("query_result"), dict) else {}
    saved_result = query_result.get("saved_result") if isinstance(query_result.get("saved_result"), dict) else {}
    result_json_path = str(saved_result.get("result_json_path") or "").strip()

    print()
    if skill:
        print(f"[skill] {skill.get('name')} ({skill.get('dispatch_reason')})")
    if used_tools:
        print(f"[tools] {', '.join(str(item) for item in used_tools)}")
    if reply:
        print(reply)
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    if result_json_path:
        print(f"[result_json] {result_json_path}")
    print()


def main() -> int:
    """命令行入口。"""
    args = _build_parser().parse_args()
    root_dir = Path(__file__).resolve().parent
    state_path = root_dir / DEFAULT_STATE_PATH
    persisted_state: dict[str, Any] = {}
    if state_path.exists():
        try:
            persisted_state = json.loads(state_path.read_text(encoding="utf-8"))
        except Exception:
            persisted_state = {}
    state = CliState(
        endpoint=args.endpoint,
        session_id=str(persisted_state.get("session_id") or f"sess_{int(time.time())}").strip(),
        base_url=args.base_url,
        credit_code=args.credit_code.upper(),
        skill_name=args.skill.strip(),
        model=args.model,
        api_base_url=args.api_base_url,
        browser_mode=args.browser_mode,
        cdp_url=args.cdp_url,
        cdp_attach_existing_page=bool(args.cdp_attach_existing_page),
        storage_state_path=args.storage_state_path,
        cookies_path=args.cookies_path,
        cookie_header_path=args.cookie_header_path,
        invalid_marker_path=args.invalid_marker_path,
        max_steps=max(1, int(args.max_steps)),
        max_captcha_attempts=max(1, int(args.max_captcha_attempts)),
        persist_session=not args.no_persist_session,
    )

    _ensure_agent_running(state.endpoint, root_dir)
    session = requests.Session()
    print("终端对话已启动。输入 /help 查看命令，输入 /exit 退出。")
    _print_status(state)

    while True:
        try:
            raw_line = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if not raw_line:
            continue

        if raw_line.startswith("/"):
            if not _handle_command(raw_line, state, root_dir=root_dir):
                break
            state_path.parent.mkdir(parents=True, exist_ok=True)
            state_path.write_text(json.dumps({"session_id": state.session_id}, ensure_ascii=False, indent=2), encoding="utf-8")
            continue

        try:
            response = session.post(
                state.endpoint,
                headers={"Content-Type": "application/json", "Accept": "application/json"},
                json=state.request_payload(raw_line),
                timeout=600,
            )
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            print(f"\n[error] 请求失败：{exc}\n")
            continue

        if not payload.get("ok"):
            print(f"\n[error] {payload.get('error') or '未知错误'}\n")
            continue

        result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
        session_payload = result.get("session") if isinstance(result.get("session"), dict) else {}
        next_session_id = str(session_payload.get("id") or "").strip()
        if next_session_id:
            state.session_id = next_session_id
            state_path.parent.mkdir(parents=True, exist_ok=True)
            state_path.write_text(json.dumps({"session_id": state.session_id}, ensure_ascii=False, indent=2), encoding="utf-8")
        _print_response(result)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
