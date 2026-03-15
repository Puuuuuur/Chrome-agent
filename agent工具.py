from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import os
import platform
import re
import shutil
import subprocess
import time
from datetime import datetime
from functools import lru_cache
from http.cookies import SimpleCookie
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, urljoin, urlparse
from uuid import uuid4

import requests
from langchain_core.tools import tool
from playwright.async_api import (
    Browser as AsyncBrowser,
    BrowserContext as AsyncBrowserContext,
    Page as AsyncPage,
    Response as AsyncResponse,
    async_playwright,
)

from 智能体配置 import (
    ARTIFACT_DIR,
    DEFAULT_API_BASE_URL,
    DEFAULT_AUTO_XVFB_ENABLED,
    DEFAULT_BASE_URL,
    DEFAULT_BROWSER_MODE,
    DEFAULT_BROWSER_EXECUTABLE,
    DEFAULT_CAPTCHA_OCR_MODEL,
    DEFAULT_CDP_ATTACH_EXISTING_PAGE,
    DEFAULT_CDP_URL,
    DEFAULT_DEPLOYMENT_MODE,
    DEFAULT_LAUNCH_HEADLESS,
    DEFAULT_MODEL,
    DEFAULT_SITE_PASSWORD,
    DEFAULT_XVFB_DISPLAY,
    DEFAULT_XVFB_SCREEN,
    FILENAME_SANITIZER,
    RESULTS_DIR,
    SESSION_DIR,
    build_session_file_paths,
    normalize_browser_mode,
)
from 模型工具 import load_agent_api_key
from 验证码工具 import solve_captcha_bytes, solve_captcha_file

__all__ = [
    "AsyncBrowserSession",
    "PlaywrightToolRuntime",
    "detect_chromium",
    "load_agent_api_key",
    "playwright_agent_is_ready",
    "runtime_metadata",
]

OPENCLAW_RELAY_AUTH_HEADER = "x-openclaw-relay-token"
OPENCLAW_RELAY_TOKEN_CONTEXT = "openclaw-extension-relay-v1"
OPENCLAW_CONFIG_CANDIDATES = (
    Path("/host-openclaw/openclaw.json"),
    Path.home() / ".openclaw" / "openclaw.json",
)
LOCAL_CDP_HOSTS = {"127.0.0.1", "localhost", "::1", "host.docker.internal"}
MACOS_CHROME_CANDIDATES = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    str(Path.home() / "Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
    str(Path.home() / "Applications/Chromium.app/Contents/MacOS/Chromium"),
)


def detect_chromium() -> str:
    # Playwright 负责浏览器控制，但在本地启动前仍需要先定位到 Chromium/Chrome 的可执行文件。
    explicit_path = DEFAULT_BROWSER_EXECUTABLE.strip()
    if explicit_path:
        resolved = shutil.which(explicit_path) or explicit_path
        if Path(resolved).exists():
            return resolved
        raise RuntimeError(f"配置的浏览器可执行文件不存在：{explicit_path}")
    for raw_path in MACOS_CHROME_CANDIDATES:
        if raw_path and Path(raw_path).exists():
            return raw_path
    for name in (
        "google-chrome",
        "google-chrome-stable",
        "chrome",
        "chromium",
        "chromium-browser",
    ):
        path = shutil.which(name)
        if path:
            return path
    raise RuntimeError("没有找到 Chromium/Chrome 可执行文件。")


def _sanitize_filename(raw_value: str, default_name: str = "artifact.png") -> str:
    text = FILENAME_SANITIZER.sub("-", str(raw_value or "").strip()).strip("-.")
    if not text:
        return default_name
    return text


def _trim_text(value: str, max_chars: int) -> str:
    return str(value or "")[:max(0, int(max_chars))]


def _mask_secret(value: str, *, keep_prefix: int = 4, keep_suffix: int = 4) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if len(text) <= keep_prefix + keep_suffix:
        return f"[redacted:{len(text)}]"
    return f"{text[:keep_prefix]}...{text[-keep_suffix:]} (len={len(text)})"


def _redact_short_secret(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return f"[redacted:{len(text)}]"


def _sanitize_sensitive_string(value: str) -> str:
    text = str(value or "")
    if not text:
        return ""

    text = re.sub(
        r"(rcwCQitg=)([^&#\s]+)",
        lambda match: f"{match.group(1)}{_mask_secret(match.group(2))}",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"(verifyInput=)([^&#\s]+)",
        lambda match: f"{match.group(1)}{_redact_short_secret(match.group(2))}",
        text,
        flags=re.IGNORECASE,
    )
    return text


def _sanitize_header_mapping(headers: dict[str, Any]) -> dict[str, Any]:
    sanitized: dict[str, Any] = {}
    for name, value in headers.items():
        lower_name = str(name or "").strip().lower()
        if lower_name == "set-cookie":
            sanitized[name] = _extract_cookie_names(str(value or ""))
            continue
        if lower_name in {"cookie", "authorization", "proxy-authorization"}:
            sanitized[name] = "[redacted]"
            continue
        sanitized[name] = _sanitize_debug_payload(value, parent_key=str(name))
    return sanitized


def _sanitize_debug_payload(value: Any, *, parent_key: str = "") -> Any:
    key = str(parent_key or "").strip().lower()
    if isinstance(value, dict):
        if key == "headers":
            return _sanitize_header_mapping(value)
        return {sub_key: _sanitize_debug_payload(sub_value, parent_key=str(sub_key)) for sub_key, sub_value in value.items()}
    if isinstance(value, list):
        return [_sanitize_debug_payload(item, parent_key=parent_key) for item in value]
    if isinstance(value, str):
        if key == "rcw_token":
            return _mask_secret(value)
        if key in {"captcha_guess", "verify_input"}:
            return _redact_short_secret(value)
        if key in {"cookie", "set-cookie", "cookie_header", "authorization", "proxy-authorization"}:
            return "[redacted]"
        return _sanitize_sensitive_string(value)
    return value


def _sanitize_text_payload(value: str) -> str:
    text = str(value or "")
    if not text.strip():
        return text
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return _sanitize_sensitive_string(text)
    return json.dumps(_sanitize_debug_payload(payload), ensure_ascii=False, indent=2)


def _extract_cookie_names(raw_header: str) -> list[str]:
    names = re.findall(r"(?:^|,\s*)([^=;,\s]+)=", str(raw_header or ""))
    deduped: list[str] = []
    for name in names:
        if name not in deduped:
            deduped.append(name)
    return deduped[:8]


def _normalize_cdp_url(raw_value: str | None) -> str:
    url = str(raw_value or "").strip()
    if not url:
        return ""
    if url.endswith("/json/version"):
        url = url[: -len("/json/version")]
    return url.rstrip("/")


def _is_local_cdp_host(hostname: str | None) -> bool:
    return (str(hostname or "").strip().lower() or "") in LOCAL_CDP_HOSTS


@lru_cache(maxsize=1)
def _load_openclaw_config() -> dict[str, Any]:
    for path in OPENCLAW_CONFIG_CANDIDATES:
        if not path.exists():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(payload, dict):
            return payload
    return {}


@lru_cache(maxsize=1)
def _load_openclaw_gateway_token() -> str:
    for name in ("OPENCLAW_GATEWAY_TOKEN", "CLAWDBOT_GATEWAY_TOKEN"):
        value = str(os.getenv(name) or "").strip()
        if value:
            return value
    config_payload = _load_openclaw_config()
    gateway = config_payload.get("gateway") if isinstance(config_payload, dict) else None
    auth = gateway.get("auth") if isinstance(gateway, dict) else None
    token = auth.get("token") if isinstance(auth, dict) else None
    return str(token or "").strip()


@lru_cache(maxsize=1)
def _load_openclaw_gateway_port() -> int:
    config_payload = _load_openclaw_config()
    gateway = config_payload.get("gateway") if isinstance(config_payload, dict) else None
    raw_port = gateway.get("port") if isinstance(gateway, dict) else None
    try:
        port = int(raw_port)
    except (TypeError, ValueError):
        return 0
    return port if 0 < port < 65536 else 0


def _derive_openclaw_relay_auth_headers(cdp_url: str) -> dict[str, str]:
    normalized = _normalize_cdp_url(cdp_url)
    if not normalized:
        return {}
    parsed = urlparse(normalized)
    if not _is_local_cdp_host(parsed.hostname):
        return {}
    port = parsed.port
    if port is None or port <= 0:
        return {}
    gateway_token = _load_openclaw_gateway_token()
    if not gateway_token:
        return {}
    digest = hmac.new(
        gateway_token.encode("utf-8"),
        f"{OPENCLAW_RELAY_TOKEN_CONTEXT}:{port}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return {OPENCLAW_RELAY_AUTH_HEADER: digest}


def _basic_auth_headers_from_url(cdp_url: str) -> dict[str, str]:
    parsed = urlparse(str(cdp_url or "").strip())
    if not (parsed.username or parsed.password):
        return {}
    raw_auth = f"{parsed.username or ''}:{parsed.password or ''}"
    token = base64.b64encode(raw_auth.encode("utf-8")).decode("ascii")
    return {"Authorization": f"Basic {token}"}


def _build_cdp_request_headers(cdp_url: str) -> dict[str, str]:
    return {
        **_derive_openclaw_relay_auth_headers(cdp_url),
        **_basic_auth_headers_from_url(cdp_url),
    }


def _probe_cdp_endpoint(
    cdp_url: str,
    *,
    timeout_seconds: float = 1.5,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    normalized = _normalize_cdp_url(cdp_url)
    if not normalized:
        raise RuntimeError("CDP URL 为空。")
    if normalized.startswith(("ws://", "wss://")):
        return {"webSocketDebuggerUrl": normalized}
    probe_url = f"{normalized}/json/version"
    response = requests.get(
        probe_url,
        headers=headers or None,
        timeout=max(0.5, float(timeout_seconds)),
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError(f"CDP 版本接口返回格式异常：{probe_url}")
    return payload


def _default_cdp_candidate_urls(primary_url: str | None) -> list[str]:
    candidates: list[str] = []

    def push(url: str | None) -> None:
        normalized = _normalize_cdp_url(url)
        if normalized and normalized not in candidates:
            candidates.append(normalized)

    primary = _normalize_cdp_url(primary_url)
    if primary:
        push(primary)

    parsed_primary = urlparse(primary) if primary else None
    preferred_hosts: list[str] = []
    primary_host = (parsed_primary.hostname or "").strip().lower() if parsed_primary else ""
    if primary_host:
        preferred_hosts.append(primary_host)
    deployment_mode = DEFAULT_DEPLOYMENT_MODE
    if deployment_mode == "container":
        preferred_hosts.extend(["host.docker.internal", "127.0.0.1", "localhost"])
    else:
        # Linux 宿主部署默认优先直连本机 9222，再把容器侧地址作为兜底候选。
        preferred_hosts.extend(["127.0.0.1", "localhost", "host.docker.internal"])
    if Path("/hostfs").exists():
        preferred_hosts.append("host.docker.internal")

    host_candidates: list[str] = []
    for host in preferred_hosts:
        normalized_host = str(host or "").strip().lower()
        if normalized_host and normalized_host not in host_candidates:
            host_candidates.append(normalized_host)

    gateway_port = _load_openclaw_gateway_port()
    if gateway_port:
        relay_port = gateway_port + 3
        for host in host_candidates:
            push(f"http://{host}:{relay_port}")

    for host in host_candidates:
        push(f"http://{host}:9222")

    return candidates


def _validate_launch_runtime(*, headless: bool, auto_xvfb_enabled: bool) -> None:
    if headless:
        return
    if platform.system() == "Darwin":
        return
    if str(os.getenv("DISPLAY") or "").strip():
        return
    if not auto_xvfb_enabled:
        raise RuntimeError("当前配置要求非无头浏览器，但 DISPLAY 未设置且已禁用 PLAYWRIGHT_AGENT_AUTO_XVFB。")
    if not shutil.which("Xvfb"):
        raise RuntimeError("当前配置要求非无头浏览器，但系统未安装 Xvfb。")


def runtime_metadata() -> dict[str, str]:
    session_paths = build_session_file_paths(DEFAULT_BASE_URL)
    return {
        "model": DEFAULT_MODEL,
        "api_base_url": DEFAULT_API_BASE_URL,
        "deployment_mode": DEFAULT_DEPLOYMENT_MODE,
        "browser_mode": DEFAULT_BROWSER_MODE,
        "browser_executable": DEFAULT_BROWSER_EXECUTABLE,
        "cdp_url": DEFAULT_CDP_URL,
        "cdp_attach_existing_page": "1" if DEFAULT_CDP_ATTACH_EXISTING_PAGE else "0",
        "launch_headless": "1" if DEFAULT_LAUNCH_HEADLESS else "0",
        "auto_xvfb_enabled": "1" if DEFAULT_AUTO_XVFB_ENABLED else "0",
        "xvfb_display": DEFAULT_XVFB_DISPLAY,
        "xvfb_screen": DEFAULT_XVFB_SCREEN,
        "captcha_ocr_model": DEFAULT_CAPTCHA_OCR_MODEL,
        "artifact_dir": str(ARTIFACT_DIR),
        "session_dir": str(SESSION_DIR),
        "results_dir": str(RESULTS_DIR),
        "default_storage_state_path": str(session_paths["storage_state_path"]),
        "default_cookies_path": str(session_paths["cookies_path"]),
        "default_cookie_header_path": str(session_paths["cookie_header_path"]),
        "default_invalid_marker_path": str(session_paths["invalid_marker_path"]),
    }


def playwright_agent_is_ready() -> tuple[bool, str]:
    try:
        browser_mode = normalize_browser_mode(DEFAULT_BROWSER_MODE)
        normalized_cdp_url = _normalize_cdp_url(DEFAULT_CDP_URL)
        if browser_mode == "connect_over_cdp":
            if not normalized_cdp_url:
                raise RuntimeError("当前浏览器模式要求 connect_over_cdp，但没有配置 PLAYWRIGHT_AGENT_CDP_URL。")
            _probe_cdp_endpoint(
                normalized_cdp_url,
                timeout_seconds=1.5,
                headers=_build_cdp_request_headers(normalized_cdp_url),
            )
        elif browser_mode == "connect_over_cdp_or_launch":
            if normalized_cdp_url:
                try:
                    _probe_cdp_endpoint(
                        normalized_cdp_url,
                        timeout_seconds=1.0,
                        headers=_build_cdp_request_headers(normalized_cdp_url),
                    )
                except Exception:
                    detect_chromium()
            else:
                detect_chromium()
            _validate_launch_runtime(
                headless=DEFAULT_LAUNCH_HEADLESS,
                auto_xvfb_enabled=DEFAULT_AUTO_XVFB_ENABLED,
            )
        else:
            detect_chromium()
            _validate_launch_runtime(
                headless=DEFAULT_LAUNCH_HEADLESS,
                auto_xvfb_enabled=DEFAULT_AUTO_XVFB_ENABLED,
            )
        load_agent_api_key()
        return True, ""
    except Exception as exc:
        return False, str(exc)


class AsyncBrowserSession:
    def __init__(
        self,
        *,
        headless: bool = True,
        base_url: str | None = None,
        browser_mode: str | None = None,
        cdp_url: str | None = None,
        cdp_attach_existing_page: bool | None = None,
        storage_state_path: str | Path | None = None,
        cookies_path: str | Path | None = None,
        cookie_header_path: str | Path | None = None,
        invalid_marker_path: str | Path | None = None,
        persist_session: bool = True,
        auto_xvfb_enabled: bool | None = None,
        xvfb_display: str | None = None,
        xvfb_screen: str | None = None,
    ):
        self.headless = headless
        self.base_url = str(base_url or DEFAULT_BASE_URL).strip() or DEFAULT_BASE_URL
        session_paths = build_session_file_paths(self.base_url)
        self.storage_state_path = Path(storage_state_path).expanduser() if storage_state_path else session_paths["storage_state_path"]
        self.cookies_path = Path(cookies_path).expanduser() if cookies_path else session_paths["cookies_path"]
        self.cookie_header_path = Path(cookie_header_path).expanduser() if cookie_header_path else session_paths["cookie_header_path"]
        self.invalid_marker_path = Path(invalid_marker_path).expanduser() if invalid_marker_path else session_paths["invalid_marker_path"]
        self.persist_session = bool(persist_session)
        self.browser_mode = normalize_browser_mode(browser_mode or DEFAULT_BROWSER_MODE)
        self.cdp_url = _normalize_cdp_url(cdp_url or DEFAULT_CDP_URL)
        self.cdp_attach_existing_page = (
            DEFAULT_CDP_ATTACH_EXISTING_PAGE
            if cdp_attach_existing_page is None
            else bool(cdp_attach_existing_page)
        )
        self.auto_xvfb_enabled = (
            DEFAULT_AUTO_XVFB_ENABLED
            if auto_xvfb_enabled is None
            else bool(auto_xvfb_enabled)
        )
        self.xvfb_display = str(xvfb_display or DEFAULT_XVFB_DISPLAY).strip() or DEFAULT_XVFB_DISPLAY
        self.xvfb_screen = str(xvfb_screen or DEFAULT_XVFB_SCREEN).strip() or DEFAULT_XVFB_SCREEN
        self._playwright = None
        self._browser = None
        self._context = None
        self._loaded_session_source = "none"
        self._loaded_session_path = ""
        self._persisted_session_path = ""
        self._session_invalid = False
        self._session_invalid_reason = ""
        self._session_invalid_marker_written = False
        self._effective_browser_mode = ""
        self._connected_over_cdp = False
        self._cdp_probe_payload: dict[str, Any] = {}
        self._cdp_connect_error = ""
        self._cdp_request_header_names: list[str] = []
        self._cdp_candidate_urls = _default_cdp_candidate_urls(self.cdp_url)
        self._cdp_attempts: list[dict[str, str]] = []
        self._effective_cdp_url = ""
        self._owns_browser = True
        self._owns_context = True
        self._owns_page = True
        self._browser_executable_path = ""
        self._launch_display = ""
        self._xvfb_started = False
        self._xvfb_process: subprocess.Popen[str] | None = None
        self.page: AsyncPage | None = None

    def _browser_profile_metadata(self) -> dict[str, Any]:
        return {
            "profile_name": "desktop_zh_cn_chrome",
            "viewport": {"width": 1366, "height": 900},
            "screen": {"width": 1366, "height": 900},
            "locale": "zh-CN",
            "timezone_id": "Asia/Shanghai",
            "color_scheme": "light",
            "user_agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/145.0.0.0 Safari/537.36"
            ),
            "accept_language": "zh-CN,zh;q=0.9,en;q=0.8",
        }

    def _cdp_profile_metadata(self) -> dict[str, Any]:
        return {
            "profile_name": "host_chrome_via_cdp",
            "target_host": (urlparse(self.base_url).hostname or "").strip().lower(),
            "cdp_url": self._effective_cdp_url or self.cdp_url,
            "attach_existing_page": self.cdp_attach_existing_page,
            "request_header_names": list(self._cdp_request_header_names),
        }

    def _browser_launch_args(self) -> list[str]:
        return [
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-blink-features=AutomationControlled",
            "--lang=zh-CN,zh",
            "--window-size=1366,900",
        ]

    def _browser_context_options(self, storage_state_payload: dict[str, Any] | None) -> dict[str, Any]:
        profile = self._browser_profile_metadata()
        return {
            "viewport": dict(profile["viewport"]),
            "screen": dict(profile["screen"]),
            "storage_state": storage_state_payload,
            "user_agent": str(profile["user_agent"]),
            "locale": str(profile["locale"]),
            "timezone_id": str(profile["timezone_id"]),
            "device_scale_factor": 1,
            "color_scheme": str(profile["color_scheme"]),
            "extra_http_headers": {
                "Accept-Language": str(profile["accept_language"]),
                "Cache-Control": "no-cache",
                "Pragma": "no-cache",
                "Upgrade-Insecure-Requests": "1",
            },
        }

    def _browser_stealth_script(self) -> str:
        return """
(() => {
  const defineGetter = (object, property, getter) => {
    try {
      Object.defineProperty(object, property, { get: getter, configurable: true });
    } catch (error) {
    }
  };
  const defineValue = (object, property, value) => {
    try {
      Object.defineProperty(object, property, { value, configurable: true });
    } catch (error) {
    }
  };
  defineGetter(Navigator.prototype, "webdriver", () => undefined);
  defineGetter(Navigator.prototype, "platform", () => "Win32");
  defineGetter(Navigator.prototype, "vendor", () => "Google Inc.");
  defineGetter(Navigator.prototype, "language", () => "zh-CN");
  defineGetter(Navigator.prototype, "languages", () => ["zh-CN", "zh", "en-US", "en"]);
  defineGetter(Navigator.prototype, "hardwareConcurrency", () => 8);
  defineGetter(Navigator.prototype, "deviceMemory", () => 8);
  defineGetter(Navigator.prototype, "maxTouchPoints", () => 0);
  defineGetter(Navigator.prototype, "pdfViewerEnabled", () => true);

  if (!window.chrome) {
    defineValue(window, "chrome", {});
  }
  if (!window.chrome.runtime) {
    defineValue(window.chrome, "runtime", {});
  }
  if (!window.chrome.app) {
    defineValue(window.chrome, "app", { isInstalled: false });
  }

  const plugins = [
    {
      name: "Chrome PDF Viewer",
      filename: "internal-pdf-viewer",
      description: "Portable Document Format",
    },
    {
      name: "Chromium PDF Viewer",
      filename: "internal-pdf-viewer",
      description: "Portable Document Format",
    },
    {
      name: "Microsoft Edge PDF Viewer",
      filename: "internal-pdf-viewer",
      description: "Portable Document Format",
    },
    {
      name: "PDF Viewer",
      filename: "internal-pdf-viewer",
      description: "Portable Document Format",
    },
  ];
  plugins.item = (index) => plugins[index] || null;
  plugins.namedItem = (name) => plugins.find((plugin) => plugin.name === name) || null;
  defineGetter(Navigator.prototype, "plugins", () => plugins);

  const mimeTypes = [
    {
      type: "application/pdf",
      suffixes: "pdf",
      description: "Portable Document Format",
      enabledPlugin: plugins[0],
    },
  ];
  mimeTypes.item = (index) => mimeTypes[index] || null;
  mimeTypes.namedItem = (name) => mimeTypes.find((item) => item.type === name) || null;
  defineGetter(Navigator.prototype, "mimeTypes", () => mimeTypes);

  const uaData = {
    brands: [
      { brand: "Chromium", version: "145" },
      { brand: "Google Chrome", version: "145" },
      { brand: "Not=A?Brand", version: "24" },
    ],
    mobile: false,
    platform: "Windows",
    getHighEntropyValues: async (hints) => {
      const values = {
        architecture: "x86",
        bitness: "64",
        brands: [
          { brand: "Chromium", version: "145" },
          { brand: "Google Chrome", version: "145" },
          { brand: "Not=A?Brand", version: "24" },
        ],
        fullVersionList: [
          { brand: "Chromium", version: "145.0.0.0" },
          { brand: "Google Chrome", version: "145.0.0.0" },
          { brand: "Not=A?Brand", version: "24.0.0.0" },
        ],
        mobile: false,
        model: "",
        platform: "Windows",
        platformVersion: "10.0.0",
        uaFullVersion: "145.0.0.0",
        wow64: false,
      };
      if (!Array.isArray(hints)) {
        return values;
      }
      return Object.fromEntries(hints.filter((hint) => hint in values).map((hint) => [hint, values[hint]]));
    },
    toJSON() {
      return {
        brands: this.brands,
        mobile: this.mobile,
        platform: this.platform,
      };
    },
  };
  defineGetter(Navigator.prototype, "userAgentData", () => uaData);

  const trackedRequests = [];
  const pushTrackedRequest = (rawUrl, method = "GET") => {
    try {
      const resolvedUrl = new URL(String(rawUrl || ""), window.location.href).href;
      if (!resolvedUrl.includes("creditchina.gov.cn")) {
        return;
      }
      trackedRequests.push({
        url: resolvedUrl,
        method: String(method || "GET").toUpperCase(),
        ts: Date.now(),
      });
      if (trackedRequests.length > 200) {
        trackedRequests.splice(0, trackedRequests.length - 200);
      }
      defineValue(window, "__creditchinaTrackedRequests", trackedRequests);
    } catch (error) {
    }
  };

  const originalFetch = window.fetch ? window.fetch.bind(window) : null;
  if (originalFetch) {
    window.fetch = function(input, init) {
      const method = init && init.method ? init.method : "GET";
      const requestUrl = typeof input === "string" ? input : (input && input.url);
      pushTrackedRequest(requestUrl, method);
      return originalFetch(input, init);
    };
  }

  const originalOpen = XMLHttpRequest.prototype.open;
  XMLHttpRequest.prototype.open = function(method, url, ...rest) {
    pushTrackedRequest(url, method);
    return originalOpen.call(this, method, url, ...rest);
  };

  if (navigator.permissions && navigator.permissions.query) {
    const originalQuery = navigator.permissions.query.bind(navigator.permissions);
    navigator.permissions.query = (parameters) => {
      if (parameters && parameters.name === "notifications") {
        return Promise.resolve({ state: Notification.permission });
      }
      return originalQuery(parameters);
    };
  }
})();
"""

    def _ensure_session_parent_dirs(self) -> None:
        for path in (
            self.storage_state_path,
            self.cookies_path,
            self.cookie_header_path,
            self.invalid_marker_path,
        ):
            path.parent.mkdir(parents=True, exist_ok=True)

    def _load_storage_state_payload(self) -> dict[str, Any] | None:
        if not self.storage_state_path.exists():
            return None
        payload = json.loads(self.storage_state_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise RuntimeError(f"storageState 文件格式不正确：{self.storage_state_path}")
        cookies = payload.get("cookies")
        origins = payload.get("origins")
        if cookies is None or origins is None:
            raise RuntimeError(f"storageState 文件缺少 cookies/origins：{self.storage_state_path}")
        self._loaded_session_source = "storage_state"
        self._loaded_session_path = str(self.storage_state_path)
        return payload

    def _prefer_cookie_source(self) -> bool:
        storage_mtime = self.storage_state_path.stat().st_mtime if self.storage_state_path.exists() else -1.0
        cookie_mtimes = [
            path.stat().st_mtime
            for path in (self.cookies_path, self.cookie_header_path)
            if path.exists()
        ]
        if not cookie_mtimes:
            return False
        return max(cookie_mtimes) > storage_mtime

    def _target_host(self) -> str:
        return (urlparse(self.base_url).hostname or "").strip().lower()

    def _is_internal_browser_page_url(self, raw_url: str) -> bool:
        normalized = str(raw_url or "").strip().lower()
        return normalized.startswith(("devtools://", "chrome-extension://", "edge-extension://"))

    def _is_blankish_browser_page_url(self, raw_url: str) -> bool:
        normalized = str(raw_url or "").strip().lower()
        return normalized in {
            "",
            "about:blank",
            "chrome://newtab/",
            "chrome://new-tab-page/",
            "edge://newtab/",
        }

    def _page_matches_target_host(self, page: AsyncPage) -> bool:
        page_host = (urlparse(page.url or "").hostname or "").strip().lower()
        target_host = self._target_host()
        return bool(page_host and target_host and page_host == target_host)

    def _iter_usable_cdp_pages(self, context: AsyncBrowserContext) -> list[AsyncPage]:
        return [page for page in context.pages if not self._is_internal_browser_page_url(page.url or "")]

    def _select_cdp_context(self, browser: AsyncBrowser) -> AsyncBrowserContext:
        contexts = list(browser.contexts)
        if not contexts:
            raise RuntimeError("connect_over_cdp 已连接到浏览器，但没有可用的 browser context。")
        target_host = self._target_host()
        if target_host:
            for context in contexts:
                if any(self._page_matches_target_host(page) for page in self._iter_usable_cdp_pages(context)):
                    return context
        return contexts[0]

    def _select_existing_cdp_page(self, context: AsyncBrowserContext) -> AsyncPage | None:
        if not self.cdp_attach_existing_page:
            return None
        pages = self._iter_usable_cdp_pages(context)
        if not pages:
            return None
        matching_pages = [page for page in pages if self._page_matches_target_host(page)]
        if matching_pages:
            return matching_pages[-1]
        blankish_pages = [page for page in pages if self._is_blankish_browser_page_url(page.url or "")]
        if blankish_pages:
            return blankish_pages[-1]
        return None

    def _build_cookie_entry(self, name: str, value: str) -> dict[str, Any]:
        return {
            "name": name,
            "value": value,
            "url": self.base_url,
        }

    def _parse_cookie_header(self, raw_text: str) -> list[dict[str, Any]]:
        cookie = SimpleCookie()
        cookie.load(str(raw_text or ""))
        cookies: list[dict[str, Any]] = []
        for morsel in cookie.values():
            cookies.append(self._build_cookie_entry(morsel.key, morsel.value))
        if cookies:
            return cookies
        fallback: list[dict[str, Any]] = []
        for chunk in str(raw_text or "").split(";"):
            if "=" not in chunk:
                continue
            name, value = chunk.split("=", 1)
            name = name.strip()
            value = value.strip()
            if not name:
                continue
            fallback.append(self._build_cookie_entry(name, value))
        return fallback

    def _load_cookies_payload(self) -> list[dict[str, Any]]:
        candidates = [
            (self.cookies_path, "cookies_json"),
            (self.cookie_header_path, "cookie_header"),
        ]
        for path, source in candidates:
            if not path.exists():
                continue
            raw_text = path.read_text(encoding="utf-8").strip()
            if not raw_text:
                continue
            cookies: list[dict[str, Any]] | None = None
            try:
                payload = json.loads(raw_text)
            except json.JSONDecodeError:
                payload = None
            if isinstance(payload, list):
                cookies = [dict(item) for item in payload if isinstance(item, dict)]
            elif isinstance(payload, dict):
                if isinstance(payload.get("cookies"), list):
                    cookies = [dict(item) for item in payload.get("cookies") if isinstance(item, dict)]
                elif isinstance(payload.get("cookie_header"), str):
                    cookies = self._parse_cookie_header(str(payload.get("cookie_header") or ""))
            if cookies is None:
                cookies = self._parse_cookie_header(raw_text)
            if cookies:
                self._loaded_session_source = source
                self._loaded_session_path = str(path)
                return cookies
        return []

    async def persist_storage_state(self, *, target_url: str | None = None) -> str:
        if self._context is None or not self.persist_session:
            return ""
        self._ensure_session_parent_dirs()
        await self._context.storage_state(path=str(self.storage_state_path))
        self._persisted_session_path = str(self.storage_state_path)
        target_host = target_url or self.base_url
        if self.invalid_marker_path.exists():
            try:
                marker_payload = json.loads(self.invalid_marker_path.read_text(encoding="utf-8"))
            except Exception:
                marker_payload = {}
            marker_target_url = str(marker_payload.get("target_url") or "")
            if not marker_target_url or target_host in marker_target_url or marker_target_url in target_host:
                self.invalid_marker_path.unlink(missing_ok=True)
        return self._persisted_session_path

    def mark_session_invalid(self, *, reason: str, diagnosis: dict[str, Any] | None = None) -> str:
        self._ensure_session_parent_dirs()
        payload = {
            "reason": reason,
            "target_url": str((diagnosis or {}).get("url") or self.base_url),
            "diagnosis": diagnosis or {},
            "loaded_session_source": self._loaded_session_source,
            "loaded_session_path": self._loaded_session_path,
        }
        self.invalid_marker_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        self._session_invalid = True
        self._session_invalid_reason = reason
        self._session_invalid_marker_written = True
        return str(self.invalid_marker_path)

    def session_debug_state(self) -> dict[str, Any]:
        return {
            "loaded_session_source": self._loaded_session_source,
            "loaded_session_path": self._loaded_session_path,
            "persisted_session_path": self._persisted_session_path,
            "persist_session": self.persist_session,
            "session_invalid": self._session_invalid,
            "session_invalid_reason": self._session_invalid_reason,
            "invalid_marker_path": str(self.invalid_marker_path),
            "invalid_marker_written": self._session_invalid_marker_written,
            "storage_state_path": str(self.storage_state_path),
            "cookies_path": str(self.cookies_path),
            "cookie_header_path": str(self.cookie_header_path),
            "browser_mode": self.browser_mode,
            "effective_browser_mode": self._effective_browser_mode or self.browser_mode,
            "launch_headless": self.headless,
            "browser_executable_path": self._browser_executable_path,
            "launch_display": self._launch_display,
            "auto_xvfb_enabled": self.auto_xvfb_enabled,
            "xvfb_display": self.xvfb_display,
            "xvfb_screen": self.xvfb_screen,
            "xvfb_started": self._xvfb_started,
            "connected_over_cdp": self._connected_over_cdp,
            "cdp_url": self.cdp_url,
            "effective_cdp_url": self._effective_cdp_url,
            "cdp_candidate_urls": list(self._cdp_candidate_urls),
            "cdp_attach_existing_page": self.cdp_attach_existing_page,
            "cdp_connect_error": self._cdp_connect_error,
            "cdp_probe_payload": self._cdp_probe_payload,
            "cdp_request_header_names": list(self._cdp_request_header_names),
            "cdp_attempts": list(self._cdp_attempts),
            "browser_profile": (
                self._cdp_profile_metadata()
                if self._connected_over_cdp
                else self._browser_profile_metadata()
            ),
        }

    def _current_cdp_candidate_urls(self) -> list[str]:
        candidates = _default_cdp_candidate_urls(self.cdp_url)
        self._cdp_candidate_urls = candidates
        return candidates

    def _prepare_launch_env(self) -> dict[str, str]:
        launch_env = {str(key): str(value) for key, value in os.environ.items()}
        if self.headless:
            self._launch_display = str(launch_env.get("DISPLAY") or "").strip()
            return launch_env
        _validate_launch_runtime(headless=False, auto_xvfb_enabled=self.auto_xvfb_enabled)
        if platform.system() == "Darwin":
            self._launch_display = ""
            return launch_env
        current_display = str(launch_env.get("DISPLAY") or "").strip()
        if current_display:
            self._launch_display = current_display
            return launch_env
        display = self.xvfb_display
        match = re.fullmatch(r":(\d+)", display)
        display_number = match.group(1) if match else ""
        socket_path = Path(f"/tmp/.X11-unix/X{display_number}") if display_number else None
        lock_path = Path(f"/tmp/.X{display_number}-lock") if display_number else None
        if socket_path is None or not socket_path.exists():
            if not (lock_path and lock_path.exists()):
                xvfb_path = shutil.which("Xvfb")
                if not xvfb_path:
                    raise RuntimeError("未找到 Xvfb，无法启动非无头浏览器。")
                self._xvfb_process = subprocess.Popen(
                    [
                        xvfb_path,
                        display,
                        "-screen",
                        "0",
                        self.xvfb_screen,
                        "-ac",
                    ],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    text=True,
                )
                self._xvfb_started = True
                started = False
                for _ in range(30):
                    if self._xvfb_process.poll() is not None:
                        raise RuntimeError(f"Xvfb 启动失败，退出码：{self._xvfb_process.returncode}")
                    if socket_path and socket_path.exists():
                        started = True
                        break
                    time.sleep(0.1)
                if not started and socket_path and not socket_path.exists():
                    raise RuntimeError(f"Xvfb 已启动但显示 {display} 未就绪。")
        launch_env["DISPLAY"] = display
        self._launch_display = display
        return launch_env

    async def _connect_existing_browser_via_cdp(self, candidate_url: str) -> None:
        if self._playwright is None:
            raise RuntimeError("Playwright 尚未初始化，无法 connect_over_cdp。")
        if not candidate_url:
            raise RuntimeError("当前浏览器模式要求 connect_over_cdp，但没有提供 CDP URL。")
        request_headers = _build_cdp_request_headers(candidate_url)
        self._cdp_probe_payload = _probe_cdp_endpoint(
            candidate_url,
            timeout_seconds=1.5,
            headers=request_headers,
        )
        browser = await self._playwright.chromium.connect_over_cdp(
            candidate_url,
            timeout=10_000,
            headers=request_headers or None,
        )
        try:
            context = self._select_cdp_context(browser)
            selected_page = self._select_existing_cdp_page(context)
            owns_page = False
            if selected_page is None:
                selected_page = await context.new_page()
                owns_page = True
        except Exception:
            raise
        self._browser = browser
        self._context = context
        self.page = selected_page
        self._owns_page = owns_page
        self._connected_over_cdp = True
        self._owns_browser = False
        self._owns_context = False
        self._effective_browser_mode = "connect_over_cdp"
        self._loaded_session_source = "cdp_browser_profile"
        self._loaded_session_path = candidate_url
        self._effective_cdp_url = candidate_url
        self._cdp_request_header_names = sorted(request_headers.keys())

    async def __aenter__(self) -> "AsyncBrowserSession":
        # LangGraph 的 react agent 在工具调用阶段可能切换 await 点；
        # 这里统一使用 Playwright async API，避免 sync API 被跨线程调用时报 greenlet 错误。
        try:
            self._ensure_session_parent_dirs()
            self._playwright = await async_playwright().start()
            if self.browser_mode in {"connect_over_cdp", "connect_over_cdp_or_launch"}:
                cdp_candidates = self._current_cdp_candidate_urls()
                if cdp_candidates:
                    last_error = ""
                    for candidate_url in cdp_candidates:
                        try:
                            await self._connect_existing_browser_via_cdp(candidate_url)
                            return self
                        except Exception as exc:
                            last_error = str(exc)
                            self._cdp_attempts.append(
                                {
                                    "url": candidate_url,
                                    "error": _trim_text(str(exc), 280),
                                }
                            )
                    self._cdp_connect_error = last_error
                    if self.browser_mode == "connect_over_cdp":
                        raise RuntimeError(f"连接宿主 Chrome 的 CDP 端点失败：{self._cdp_connect_error}") from None
            if self.browser_mode == "connect_over_cdp":
                raise RuntimeError("当前浏览器模式要求 connect_over_cdp，但没有提供 CDP URL。")
            executable_path = detect_chromium()
            launch_env = self._prepare_launch_env()
            self._browser_executable_path = executable_path
            self._browser = await self._playwright.chromium.launch(
                headless=self.headless,
                executable_path=executable_path,
                ignore_default_args=["--enable-automation"],
                args=self._browser_launch_args(),
                env=launch_env,
            )
            storage_state_payload = None if self._prefer_cookie_source() else self._load_storage_state_payload()
            self._context = await self._browser.new_context(**self._browser_context_options(storage_state_payload))
            await self._context.add_init_script(self._browser_stealth_script())
            if storage_state_payload is None:
                cookies_payload = self._load_cookies_payload()
                if cookies_payload:
                    await self._context.add_cookies(cookies_payload)
            self.page = await self._context.new_page()
            self._connected_over_cdp = False
            self._owns_browser = True
            self._owns_context = True
            self._owns_page = True
            self._effective_browser_mode = "launch"
            return self
        except Exception:
            if self._xvfb_process is not None:
                self._xvfb_process.terminate()
                try:
                    self._xvfb_process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    self._xvfb_process.kill()
                    self._xvfb_process.wait(timeout=3)
                self._xvfb_process = None
            if self._playwright is not None:
                await self._playwright.stop()
                self._playwright = None
            raise

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._owns_page and self.page is not None:
            await self.page.close()
        if self._owns_context and self._context is not None:
            await self._context.close()
        if self._owns_browser and self._browser is not None:
            await self._browser.close()
        if self._xvfb_process is not None:
            self._xvfb_process.terminate()
            try:
                self._xvfb_process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self._xvfb_process.kill()
                self._xvfb_process.wait(timeout=3)
            self._xvfb_process = None
        if self._playwright is not None:
            await self._playwright.stop()


class PlaywrightToolRuntime:
    def __init__(
        self,
        base_url: str,
        site_password: str = DEFAULT_SITE_PASSWORD,
    ):
        self.base_url = base_url.rstrip("/")
        self.site_password = site_password
        self._last_navigation: dict[str, Any] | None = None
        self._last_access_diagnosis: dict[str, Any] | None = None
        self._debug_events: list[dict[str, Any]] = []

    def _start_url(self) -> str:
        return f"{self.base_url}/"

    def _resolve_url(self, current_url: str | None, target: str) -> str:
        text = str(target or "").strip()
        if not text:
            return self._start_url()
        if text.startswith(("http://", "https://")):
            return text
        base = current_url if current_url and current_url != "about:blank" else self._start_url()
        return urljoin(base, text)

    def _build_run_artifact_dir(self) -> Path:
        timestamp = datetime.now().strftime("%H%M%S")
        day_dir = ARTIFACT_DIR / datetime.now().strftime("%Y-%m-%d")
        run_dir = day_dir / f"{timestamp}-{uuid4().hex[:10]}"
        run_dir.mkdir(parents=True, exist_ok=True)
        return run_dir

    def _push_debug_event(self, event_type: str, payload: dict[str, Any]) -> None:
        event = {"type": event_type, **payload}
        self._debug_events.append(event)
        if len(self._debug_events) > 12:
            self._debug_events = self._debug_events[-12:]

    async def _record_navigation_async(
        self,
        page: AsyncPage,
        response: AsyncResponse | None,
        *,
        source: str,
    ) -> dict[str, Any]:
        headers: dict[str, Any] = {}
        status: int | None = None
        final_url = page.url
        if response is not None:
            status = response.status
            final_url = response.url or final_url
            try:
                raw_headers = await response.all_headers()
            except Exception:
                raw_headers = {}
            for header_name in (
                "content-type",
                "location",
                "server",
                "x-via-jsl",
                "x-cache",
                "retry-after",
            ):
                header_value = raw_headers.get(header_name)
                if header_value:
                    headers[header_name] = header_value
            set_cookie_header = raw_headers.get("set-cookie")
            if set_cookie_header:
                cookie_names = _extract_cookie_names(set_cookie_header)
                if cookie_names:
                    headers["set_cookie_names"] = cookie_names

        meta = {
            "source": source,
            "status": status,
            "url": final_url,
            "headers": headers,
        }
        self._last_navigation = meta
        self._push_debug_event("navigation", meta)
        return meta

    async def _settle_page_async(self, page: AsyncPage, extra_wait_ms: int = 500) -> None:
        for state, timeout_ms in (("domcontentloaded", 5_000), ("load", 5_000), ("networkidle", 4_000)):
            try:
                await page.wait_for_load_state(state, timeout=timeout_ms)
            except Exception:
                continue
        if extra_wait_ms > 0:
            await page.wait_for_timeout(extra_wait_ms)

    async def _write_full_html_async(self, page: AsyncPage, output_path: Path) -> str:
        html = await page.content()
        output_path.write_text(html, encoding="utf-8")
        return html

    async def _capture_full_page_screenshot_async(self, page: AsyncPage, output_path: Path) -> str:
        await page.screenshot(path=str(output_path), full_page=True)
        return str(output_path)

    async def _diagnose_access_async(
        self,
        page: AsyncPage,
        *,
        max_html_chars: int = 4_000,
        max_body_chars: int = 1_200,
    ) -> dict[str, Any]:
        dom_probe = await page.evaluate(
            """
            () => {
              const normalize = (value, maxLen = 1200) =>
                String(value || "").replace(/\\s+/g, " ").trim().slice(0, maxLen);
              const isVisible = (element) => {
                if (!(element instanceof HTMLElement)) return false;
                const style = window.getComputedStyle(element);
                const rect = element.getBoundingClientRect();
                return style.display !== "none"
                  && style.visibility !== "hidden"
                  && style.opacity !== "0"
                  && rect.width > 0
                  && rect.height > 0;
              };
              const interactiveSelectors =
                "input, textarea, button, select, a, img, [role='button'], [contenteditable='true']";
              const interactiveCount = Array.from(document.querySelectorAll(interactiveSelectors))
                .filter((element) => isVisible(element))
                .length;
              const bodyText = normalize(document.body ? document.body.innerText : "", 5000);
              const bodyHtml = document.body ? document.body.innerHTML : "";
              const html = document.documentElement ? document.documentElement.outerHTML : "";
              const captchaInput = document.querySelector("#vcode, input[placeholder*='验证码'], input[name*='vcode']");
              const captchaImage = document.querySelector("#vcodeimg, img[id*='vcode'], img[src*='vcode']");
              const actionNodes = Array.from(document.querySelectorAll("button, a, input[type='button'], input[type='submit']"));
              const hasCancelAction = actionNodes.some((element) => normalize(element.innerText || element.value || "", 32).includes("取消") && isVisible(element));
              const hasVerifyAction = actionNodes.some((element) => normalize(element.innerText || element.value || "", 32).includes("验证") && isVisible(element));
              const captchaModalVisible = Boolean(
                (captchaInput && isVisible(captchaInput))
                || (captchaImage && isVisible(captchaImage))
                || (hasCancelAction && hasVerifyAction)
              );
              const captchaImageLoaded = captchaImage instanceof HTMLImageElement
                ? Boolean(captchaImage.complete && captchaImage.naturalWidth > 8 && captchaImage.naturalHeight > 8)
                : false;
              const captchaImageBroken = captchaImage instanceof HTMLImageElement
                ? Boolean(captchaImage.complete && captchaImage.naturalWidth === 0)
                : false;
              return {
                url: window.location.href,
                title: document.title || "",
                visible_text_excerpt: bodyText.slice(0, 1200),
                visible_text_length: bodyText.length,
                interactive_count: interactiveCount,
                html_length: html.length,
                body_html_length: bodyHtml.length,
                script_count: document.querySelectorAll("script").length,
                iframe_count: document.querySelectorAll("iframe").length,
                creditchina_captcha_modal_visible: captchaModalVisible,
                creditchina_captcha_image_loaded: captchaImageLoaded,
                creditchina_captcha_image_broken: captchaImageBroken,
              };
            }
            """
        )
        try:
            html = await page.content()
        except Exception:
            html = ""
        try:
            body_text = await page.locator("body").first.inner_text(timeout=2_000) or ""
        except Exception:
            body_text = ""

        navigation = dict(self._last_navigation or {})
        html_excerpt = _trim_text(html, max_html_chars)
        body_excerpt = _trim_text(body_text, max_body_chars)
        lower_text = "\n".join(
            [
                str(navigation.get("url") or ""),
                str(dom_probe.get("title") or ""),
                body_excerpt,
                html_excerpt,
                json.dumps(navigation.get("headers") or {}, ensure_ascii=False),
            ]
        ).lower()

        markers: list[str] = []
        status = navigation.get("status")
        if status in {401, 403, 412, 429, 503}:
            markers.append(f"status_{status}")
        if (navigation.get("headers") or {}).get("x-via-jsl"):
            markers.append("header_x_via_jsl")
        cookie_names = (navigation.get("headers") or {}).get("set_cookie_names") or []
        if "__jsluid_s" in cookie_names:
            markers.append("cookie___jsluid_s")
        for text_marker, marker_name in (
            ("precondition failed", "text_precondition_failed"),
            ("$_ts", "script_marker_$_ts"),
            ("_$kp()", "script_marker__$kp"),
            ("x-via-jsl", "text_x_via_jsl"),
            ("__jsluid_s", "text___jsluid_s"),
            ("安全验证", "text_security_verification"),
            ("访问验证", "text_access_verification"),
            ("checking your browser", "text_browser_check"),
            ("attention required", "text_attention_required"),
            ("cf-chl", "text_cf_chl"),
        ):
            if text_marker in lower_text:
                markers.append(marker_name)

        blank_page_like = (
            int(dom_probe.get("interactive_count") or 0) == 0
            and int(dom_probe.get("visible_text_length") or 0) == 0
        )
        if blank_page_like:
            markers.append("blank_visible_dom")
        if blank_page_like and int(dom_probe.get("script_count") or 0) > 0:
            markers.append("blank_with_scripts")

        strong_markers = {
            "status_401",
            "status_403",
            "status_412",
            "status_429",
            "status_503",
            "header_x_via_jsl",
            "cookie___jsluid_s",
            "text_precondition_failed",
            "script_marker_$_ts",
            "script_marker__$kp",
            "text_x_via_jsl",
            "text___jsluid_s",
            "text_browser_check",
            "text_attention_required",
            "text_cf_chl",
        }
        challenge_detected = any(marker in strong_markers for marker in markers)
        creditchina_captcha_modal_visible = bool(dom_probe.get("creditchina_captcha_modal_visible"))
        creditchina_captcha_image_loaded = bool(dom_probe.get("creditchina_captcha_image_loaded"))
        creditchina_captcha_image_broken = bool(dom_probe.get("creditchina_captcha_image_broken"))
        if creditchina_captcha_modal_visible and self._is_creditchina_url(page.url):
            challenge_detected = any(
                marker in {
                    "status_401",
                    "status_403",
                    "status_412",
                    "status_429",
                    "status_503",
                    "text_precondition_failed",
                    "text_browser_check",
                    "text_attention_required",
                    "text_cf_chl",
                }
                for marker in markers
            )
        retry_recommended = challenge_detected or blank_page_like
        if creditchina_captcha_modal_visible and not challenge_detected:
            reason = "当前页面是信用中国正常验证码弹窗页，可继续处理验证码。"
        elif challenge_detected:
            reason = "疑似命中反爬或安全挑战页。"
        elif blank_page_like:
            reason = "当前页面可见 DOM 为空，可能仍在延迟渲染或被脚本壳页拦截。"
        else:
            reason = "当前页面未发现明显挑战页特征。"

        diagnosis = {
            "url": page.url,
            "status": status,
            "headers": navigation.get("headers") or {},
            "title": dom_probe.get("title") or "",
            "visible_text_excerpt": body_excerpt or dom_probe.get("visible_text_excerpt") or "",
            "visible_text_length": int(dom_probe.get("visible_text_length") or 0),
            "interactive_count": int(dom_probe.get("interactive_count") or 0),
            "html_length": int(dom_probe.get("html_length") or 0),
            "body_html_length": int(dom_probe.get("body_html_length") or 0),
            "script_count": int(dom_probe.get("script_count") or 0),
            "iframe_count": int(dom_probe.get("iframe_count") or 0),
            "creditchina_captcha_modal_visible": creditchina_captcha_modal_visible,
            "creditchina_captcha_image_loaded": creditchina_captcha_image_loaded,
            "creditchina_captcha_image_broken": creditchina_captcha_image_broken,
            "challenge_detected": challenge_detected,
            "blank_page_like": blank_page_like,
            "retry_recommended": retry_recommended,
            "markers": markers,
            "reason": reason,
            "html_excerpt": html_excerpt,
        }
        self._last_access_diagnosis = diagnosis
        return diagnosis

    async def _refresh_creditchina_after_captcha_cancel_async(self, page: AsyncPage) -> dict[str, Any]:
        diagnosis = await self._diagnose_access_async(page)
        if not self._is_creditchina_url(page.url):
            return {"handled": False, "reason": "not_creditchina", "diagnosis": diagnosis}
        if not diagnosis.get("creditchina_captcha_modal_visible"):
            return {"handled": False, "reason": "captcha_modal_not_visible", "diagnosis": diagnosis}
        if not diagnosis.get("creditchina_captcha_image_broken"):
            return {"handled": False, "reason": "captcha_image_not_broken", "diagnosis": diagnosis}

        cancel_selector = await self._first_visible_selector_async(
            page,
            [
                "button:has-text('取消')",
                "a:has-text('取消')",
                "input[type='button'][value*='取消']",
                "input[type='submit'][value*='取消']",
                ".btn:has-text('取消')",
            ],
        )
        clicked_cancel = False
        cancel_error = ""
        if cancel_selector:
            try:
                await page.locator(cancel_selector).first.click()
                clicked_cancel = True
                await page.wait_for_timeout(1_200)
            except Exception as exc:
                cancel_error = str(exc)

        reload_error = ""
        response = None
        try:
            response = await page.reload(wait_until="domcontentloaded", timeout=20_000)
        except Exception as exc:
            reload_error = str(exc)
        await self._record_navigation_async(page, response, source="creditchina_captcha_cancel_refresh")
        await self._settle_page_async(page, extra_wait_ms=3_500)
        final_diagnosis = await self._diagnose_access_async(page)
        payload = {
            "handled": True,
            "clicked_cancel": clicked_cancel,
            "cancel_selector": cancel_selector,
            "cancel_error": cancel_error,
            "reload_error": reload_error,
            "diagnosis": final_diagnosis,
        }
        self._push_debug_event(
            "creditchina_captcha_cancel_refresh",
            {
                "url": page.url,
                "clicked_cancel": clicked_cancel,
                "cancel_selector": cancel_selector,
                "challenge_detected": final_diagnosis.get("challenge_detected"),
                "captcha_modal_visible": final_diagnosis.get("creditchina_captcha_modal_visible"),
            },
        )
        return payload

    async def _wait_for_creditchina_captcha_ready_async(
        self,
        page: AsyncPage,
        *,
        timeout_ms: int = 60_000,
        poll_interval_ms: int = 1_500,
        broken_grace_ms: int = 8_000,
        max_cancel_refreshes: int = 4,
    ) -> dict[str, Any]:
        wait_budget_ms = max(2_000, min(int(timeout_ms), 60_000))
        poll_budget_ms = max(250, min(int(poll_interval_ms), 2_000))
        broken_grace_budget_ms = max(0, min(int(broken_grace_ms), wait_budget_ms))
        refresh_count = 0
        elapsed_ms = 0
        observations: list[dict[str, Any]] = []
        last_diagnosis: dict[str, Any] = {}

        while elapsed_ms <= wait_budget_ms:
            last_diagnosis = await self._diagnose_access_async(page)
            observation = {
                "elapsed_ms": elapsed_ms,
                "captcha_modal_visible": bool(last_diagnosis.get("creditchina_captcha_modal_visible")),
                "captcha_image_loaded": bool(last_diagnosis.get("creditchina_captcha_image_loaded")),
                "captcha_image_broken": bool(last_diagnosis.get("creditchina_captcha_image_broken")),
                "url": page.url,
            }
            observations.append(observation)

            if last_diagnosis.get("creditchina_captcha_modal_visible") and last_diagnosis.get("creditchina_captcha_image_loaded"):
                return {
                    "ok": True,
                    "refresh_count": refresh_count,
                    "observations": observations,
                    "diagnosis": last_diagnosis,
                }

            if last_diagnosis.get("creditchina_captcha_image_broken"):
                if elapsed_ms < broken_grace_budget_ms:
                    await page.wait_for_timeout(poll_budget_ms)
                    elapsed_ms += poll_budget_ms
                    continue
                if refresh_count >= max_cancel_refreshes:
                    return {
                        "ok": False,
                        "error": "验证码图片连续刷新后仍然是图裂状态。",
                        "refresh_count": refresh_count,
                        "observations": observations,
                        "diagnosis": last_diagnosis,
                    }
                refresh_count += 1
                refresh_payload = await self._refresh_creditchina_after_captcha_cancel_async(page)
                observations.append(
                    {
                        "elapsed_ms": elapsed_ms,
                        "action": "cancel_then_refresh",
                        "handled": bool(refresh_payload.get("handled")),
                        "clicked_cancel": bool(refresh_payload.get("clicked_cancel")),
                        "cancel_selector": refresh_payload.get("cancel_selector"),
                        "reload_error": refresh_payload.get("reload_error") or "",
                    }
                )
                continue

            await page.wait_for_timeout(poll_budget_ms)
            elapsed_ms += poll_budget_ms

        return {
            "ok": False,
            "error": "等待验证码图片加载超时。",
            "refresh_count": refresh_count,
            "observations": observations,
            "diagnosis": last_diagnosis,
        }

    async def _save_page_artifacts_async(
        self,
        page: AsyncPage,
        artifact_dir: Path,
        *,
        stem: str = "page-debug",
        html_preview_chars: int = 4_000,
    ) -> dict[str, Any]:
        stem_name = _sanitize_filename(stem, default_name="page-debug")
        html_path = artifact_dir / f"{stem_name}.html"
        screenshot_path = artifact_dir / f"{stem_name}.png"
        html = await self._write_full_html_async(page, html_path)
        screenshot_error = ""
        try:
            await self._capture_full_page_screenshot_async(page, screenshot_path)
        except Exception as exc:
            screenshot_error = str(exc)
        payload = {
            "url": page.url,
            "html_path": str(html_path),
            "screenshot_path": str(screenshot_path),
            "html_preview": _trim_text(html, html_preview_chars),
            "screenshot_error": screenshot_error,
        }
        self._push_debug_event(
            "page_artifacts",
            {
                "url": payload["url"],
                "html_path": payload["html_path"],
                "screenshot_path": payload["screenshot_path"],
                "screenshot_error": screenshot_error,
            },
        )
        return payload

    def export_debug_state(self) -> dict[str, Any]:
        return {
            "last_navigation": self._last_navigation,
            "last_access_diagnosis": self._last_access_diagnosis,
            "recent_events": list(self._debug_events),
        }

    def current_session_invalid_state(self) -> tuple[bool, str, dict[str, Any] | None]:
        diagnosis = self._last_access_diagnosis or {}
        headers = diagnosis.get("headers") or {}
        is_invalid = (
            int(diagnosis.get("status") or 0) in {400, 412}
            and bool(headers.get("x-via-jsl"))
            and bool(diagnosis.get("blank_page_like"))
        )
        reason = "命中 412/400 + x-via-jsl + 空白 DOM，判定当前会话已失效。" if is_invalid else ""
        return is_invalid, reason, diagnosis if diagnosis else None

    async def _locator_visible_async(self, locator) -> bool:
        try:
            return await locator.first.is_visible(timeout=1500)
        except Exception:
            return False

    async def _first_visible_selector_async(self, page: AsyncPage, candidates: list[str]) -> str:
        for selector in candidates:
            locator = page.locator(selector)
            try:
                if await locator.count() == 0:
                    continue
            except Exception:
                continue
            if await self._locator_visible_async(locator):
                return selector
        return ""

    async def _click_first_visible_async(self, page: AsyncPage, candidates: list[str]) -> str:
        selector = await self._first_visible_selector_async(page, candidates)
        if not selector:
            raise RuntimeError("没有找到可点击的目标元素。")
        await page.locator(selector).first.click()
        return selector

    def _build_creditchina_query_url(self, credit_code: str) -> str:
        encoded = quote(str(credit_code or "").strip())
        return (
            "https://www.creditchina.gov.cn/xinyongxinxi/index.html"
            "?index=0&scenes=defaultScenario&tableName=credit_xyzx_tyshxydm"
            "&searchState=2&entityType=1,2,4,5,6,7,8"
            f"&keyword={encoded}"
        )

    def _requests_cookie_to_playwright_cookie(
        self,
        cookie,
        *,
        fallback_url: str,
    ) -> dict[str, Any]:
        parsed = urlparse(str(fallback_url or "").strip())
        payload: dict[str, Any] = {
            "name": str(cookie.name),
            "value": str(cookie.value),
            "path": str(cookie.path or "/"),
            "secure": bool(cookie.secure),
        }
        domain = str(cookie.domain or "").strip()
        if domain:
            payload["domain"] = domain
        else:
            payload["url"] = f"{parsed.scheme}://{parsed.netloc}"
        if cookie.expires is not None:
            try:
                payload["expires"] = int(cookie.expires)
            except Exception:
                pass
        http_only = str(getattr(cookie, "_rest", {}).get("HttpOnly") or "").strip().lower()
        if http_only in {"true", "1"}:
            payload["httpOnly"] = True
        return payload

    def _is_creditchina_url(self, url: str | None) -> bool:
        host = (urlparse(str(url or "")).hostname or "").strip().lower()
        return host.endswith("creditchina.gov.cn")

    def _extract_creditchina_keyword(self, url: str | None) -> str:
        parsed = urlparse(str(url or "").strip())
        keyword_values = parse_qs(parsed.query).get("keyword") or []
        if not keyword_values:
            return ""
        return str(keyword_values[0] or "").strip()

    async def _context_cookie_summary_async(self, page: AsyncPage, target_url: str) -> dict[str, Any]:
        try:
            cookies = await page.context.cookies(target_url)
        except Exception as exc:
            return {
                "cookie_count": 0,
                "cookie_names": [],
                "has_js_cookie": False,
                "error": str(exc),
            }

        cookie_names: list[str] = []
        has_js_cookie = False
        for cookie in cookies:
            name = str(cookie.get("name") or "").strip()
            if not name:
                continue
            if name not in cookie_names:
                cookie_names.append(name)
            if name.startswith("6BVI") and name.endswith("P"):
                has_js_cookie = True
        return {
            "cookie_count": len(cookies),
            "cookie_names": cookie_names[:12],
            "has_js_cookie": has_js_cookie,
        }

    async def _storage_summary_async(self, page: AsyncPage) -> dict[str, Any]:
        try:
            payload = await page.evaluate(
                """
                () => ({
                  local_keys: Object.keys(window.localStorage || {}).slice(0, 12),
                  session_keys: Object.keys(window.sessionStorage || {}).slice(0, 12),
                })
                """
            )
        except Exception as exc:
            return {
                "local_keys": [],
                "session_keys": [],
                "error": str(exc),
            }
        return {
            "local_keys": list(payload.get("local_keys") or [])[:12],
            "session_keys": list(payload.get("session_keys") or [])[:12],
        }

    async def _creditchina_tracked_requests_async(self, page: AsyncPage) -> list[dict[str, Any]]:
        try:
            payload = await page.evaluate(
                """
                () => {
                  const tracked = Array.isArray(window.__creditchinaTrackedRequests)
                    ? window.__creditchinaTrackedRequests
                    : [];
                  const performanceUrls = (performance.getEntriesByType("resource") || [])
                    .map((entry) => String(entry.name || ""))
                    .filter(Boolean)
                    .slice(-120)
                    .map((url) => ({ url, method: "GET", ts: 0 }));
                  return [...tracked, ...performanceUrls];
                }
                """
            )
        except Exception:
            return []

        tracked_requests: list[dict[str, Any]] = []
        for item in payload or []:
            if not isinstance(item, dict):
                continue
            url = str(item.get("url") or "").strip()
            if not url:
                continue
            tracked_requests.append(
                {
                    "url": url,
                    "method": str(item.get("method") or "GET").strip().upper() or "GET",
                    "ts": int(item.get("ts") or 0),
                }
            )
        return tracked_requests[-200:]

    def _extract_rcw_from_url(self, url: str | None) -> str:
        parsed = urlparse(str(url or "").strip())
        values = parse_qs(parsed.query).get("rcwCQitg") or []
        if not values:
            return ""
        return str(values[0] or "").strip()

    async def _capture_creditchina_runtime_state_async(self, page: AsyncPage) -> dict[str, Any]:
        tracked_requests = await self._creditchina_tracked_requests_async(page)
        private_api_urls: list[str] = []
        rcw_token = ""
        for item in reversed(tracked_requests):
            url = str(item.get("url") or "").strip()
            if "public.creditchina.gov.cn/private-api/" not in url:
                continue
            if url not in private_api_urls:
                private_api_urls.append(url)
            if not rcw_token:
                rcw_token = self._extract_rcw_from_url(url)

        current_url = str(page.url or "")
        parsed_page = urlparse(current_url)
        page_query = parse_qs(parsed_page.query)
        keyword = str((page_query.get("keyword") or [""])[0] or "").strip()
        tyshxydm = str((page_query.get("tyshxydm") or [""])[0] or "").strip().upper()
        page_uuid = str((page_query.get("uuid") or [""])[0] or "").strip()
        entity_type = str((page_query.get("entityType") or [""])[0] or "").strip()
        table_name = str((page_query.get("tableName") or [""])[0] or "").strip()
        search_state = str((page_query.get("searchState") or [""])[0] or "").strip()
        scenes = str((page_query.get("scenes") or [""])[0] or "").strip()
        page_index = str((page_query.get("index") or [""])[0] or "").strip()
        return {
            "page_url": current_url,
            "keyword": keyword,
            "tyshxydm": tyshxydm,
            "page_uuid": page_uuid,
            "entity_type": entity_type,
            "table_name": table_name,
            "search_state": search_state,
            "scenes": scenes,
            "page_index": page_index,
            "rcw_token": rcw_token,
            "private_api_urls": private_api_urls[:20],
        }

    async def _wait_for_creditchina_rcw_async(
        self,
        page: AsyncPage,
        *,
        timeout_ms: int = 8_000,
        poll_interval_ms: int = 300,
    ) -> dict[str, Any]:
        wait_budget_ms = max(0, min(int(timeout_ms), 20_000))
        poll_budget_ms = max(150, min(int(poll_interval_ms), 1_500))
        elapsed_ms = 0
        snapshots: list[dict[str, Any]] = []

        while True:
            runtime_state = await self._capture_creditchina_runtime_state_async(page)
            rcw_token = str(runtime_state.get("rcw_token") or "").strip()
            snapshots.append(
                {
                    "elapsed_ms": elapsed_ms,
                    "has_rcw_token": bool(rcw_token),
                    "private_api_url_count": len(runtime_state.get("private_api_urls") or []),
                    "page_url": runtime_state.get("page_url"),
                }
            )
            if rcw_token or elapsed_ms >= wait_budget_ms:
                return {
                    "ok": bool(rcw_token),
                    "elapsed_ms": elapsed_ms,
                    "runtime_state": runtime_state,
                    "snapshots": snapshots[-8:],
                }
            await page.wait_for_timeout(poll_budget_ms)
            elapsed_ms += poll_budget_ms

    def _build_creditchina_private_api_url(self, endpoint: str, rcw_token: str, extra_query: dict[str, Any] | None = None) -> str:
        normalized_endpoint = str(endpoint or "").strip()
        if not normalized_endpoint.startswith("/"):
            normalized_endpoint = f"/{normalized_endpoint}"
        if not normalized_endpoint.startswith("/private-api/"):
            normalized_endpoint = f"/private-api/{normalized_endpoint.lstrip('/')}"
        parts = [f"rcwCQitg={quote(str(rcw_token or '').strip(), safe='._-')}"]
        for key, value in (extra_query or {}).items():
            if value is None:
                continue
            parts.append(f"{quote(str(key), safe='')}={quote(str(value), safe='._-')}")
        query = "&".join(parts)
        return f"https://public.creditchina.gov.cn{normalized_endpoint}?{query}"

    async def _creditchina_browser_fetch_async(
        self,
        page: AsyncPage,
        *,
        url: str,
        method: str = "GET",
        headers: dict[str, str] | None = None,
        body: str | None = None,
        response_kind: str = "text",
    ) -> dict[str, Any]:
        payload = await page.evaluate(
            """
            async ({ url, method, headers, body, responseKind }) => {
              const response = await fetch(url, {
                method,
                headers,
                body: body || undefined,
                credentials: "include",
                mode: "cors",
              });
              const responseHeaders = {};
              for (const [name, value] of response.headers.entries()) {
                responseHeaders[name] = value;
              }
              if (responseKind === "base64") {
                const buffer = await response.arrayBuffer();
                let binary = "";
                const bytes = new Uint8Array(buffer);
                const chunkSize = 0x8000;
                for (let index = 0; index < bytes.length; index += chunkSize) {
                  binary += String.fromCharCode(...bytes.slice(index, index + chunkSize));
                }
                return {
                  ok: response.ok,
                  status: response.status,
                  headers: responseHeaders,
                  base64: btoa(binary),
                };
              }
              return {
                ok: response.ok,
                status: response.status,
                headers: responseHeaders,
                text: await response.text(),
              };
            }
            """,
            {
                "url": str(url),
                "method": str(method or "GET").upper(),
                "headers": {str(key): str(value) for key, value in (headers or {}).items()},
                "body": str(body or ""),
                "responseKind": str(response_kind or "text"),
            },
        )
        return dict(payload or {})

    async def _creditchina_api_json_request_async(
        self,
        page: AsyncPage,
        *,
        endpoint: str,
        rcw_token: str,
        method: str = "GET",
        headers: dict[str, str] | None = None,
        body: str | None = None,
        extra_query: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = self._build_creditchina_private_api_url(endpoint, rcw_token, extra_query=extra_query)
        default_headers = {"Accept": "application/json, text/javascript, */*; q=0.01"}
        if method.upper() == "POST":
            default_headers["Content-Type"] = "application/x-www-form-urlencoded; charset=UTF-8"
        response_payload = await self._creditchina_browser_fetch_async(
            page,
            url=url,
            method=method,
            headers={**default_headers, **(headers or {})},
            body=body,
            response_kind="text",
        )
        response_text = str(response_payload.get("text") or "")
        try:
            response_json = json.loads(response_text) if response_text else {}
        except json.JSONDecodeError:
            response_json = {"raw_text": response_text}
        event_payload = {
            "endpoint": endpoint,
            "method": method.upper(),
            "status": int(response_payload.get("status") or 0),
            "ok": bool(response_payload.get("ok")),
        }
        self._push_debug_event("creditchina_api_request", event_payload)
        return {
            "url": url,
            "method": method.upper(),
            "status": int(response_payload.get("status") or 0),
            "ok": bool(response_payload.get("ok")),
            "headers": dict(response_payload.get("headers") or {}),
            "json": response_json,
            "text": response_text,
        }

    async def _creditchina_api_get_verify_async(self, page: AsyncPage, *, rcw_token: str) -> dict[str, Any]:
        response_payload = await self._creditchina_browser_fetch_async(
            page,
            url=self._build_creditchina_private_api_url("verify/getVerify", rcw_token, extra_query={"_v": f"{datetime.now().timestamp():.6f}"}),
            headers={"Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8"},
            response_kind="base64",
        )
        base64_payload = str(response_payload.get("base64") or "").strip()
        image_bytes = base64.b64decode(base64_payload) if base64_payload else b""
        self._push_debug_event(
            "creditchina_api_request",
            {
                "endpoint": "verify/getVerify",
                "method": "GET",
                "status": int(response_payload.get("status") or 0),
                "ok": bool(response_payload.get("ok")),
            },
        )
        return {
            "status": int(response_payload.get("status") or 0),
            "ok": bool(response_payload.get("ok")),
            "headers": dict(response_payload.get("headers") or {}),
            "image_bytes": image_bytes,
            "base64_size": len(base64_payload),
        }

    async def _creditchina_api_check_verify_async(
        self,
        page: AsyncPage,
        *,
        rcw_token: str,
        verify_input: str,
    ) -> dict[str, Any]:
        verify_code = str(verify_input or "").strip().upper()
        payload = await self._creditchina_api_json_request_async(
            page,
            endpoint="verify/checkVerify",
            rcw_token=rcw_token,
            method="POST",
            body=f"verifyInput={quote(verify_code, safe='')}",
        )
        response_json = dict(payload.get("json") or {})
        return {
            **payload,
            "verify_input": verify_code,
            "verify_ok": int(response_json.get("code", -1)) == 0,
        }

    async def _run_creditchina_private_api_verify_async(
        self,
        page: AsyncPage,
        artifact_dir: Path,
        *,
        rcw_token: str,
        max_attempts: int,
        file_prefix: str,
    ) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
        verify_attempts: list[dict[str, Any]] = []
        verify_success_payload: dict[str, Any] | None = None
        captcha_limit = max(1, min(int(max_attempts), 8))

        for attempt in range(1, captcha_limit + 1):
            verify_image = await self._creditchina_api_get_verify_async(page, rcw_token=rcw_token)
            image_bytes = bytes(verify_image.get("image_bytes") or b"")
            if not image_bytes:
                verify_attempts.append(
                    {
                        "attempt": attempt,
                        "captcha_image_path": "",
                        "captcha_guess": "",
                        "status": verify_image.get("status"),
                        "verify_code": None,
                        "verify_msg": "验证码图片为空",
                        "verify_ok": False,
                    }
                )
                break
            image_path = artifact_dir / f"{file_prefix}-{attempt}.png"
            image_path.write_bytes(image_bytes)
            captcha_guess = solve_captcha_bytes(image_bytes)
            verify_payload = await self._creditchina_api_check_verify_async(
                page,
                rcw_token=rcw_token,
                verify_input=captcha_guess,
            )
            verify_json = dict(verify_payload.get("json") or {})
            attempt_payload = {
                "attempt": attempt,
                "captcha_image_path": str(image_path),
                "captcha_guess": captcha_guess,
                "status": verify_payload.get("status"),
                "verify_code": verify_json.get("code"),
                "verify_msg": verify_json.get("msg"),
                "verify_ok": bool(verify_payload.get("verify_ok")),
            }
            verify_attempts.append(attempt_payload)
            if not verify_payload.get("verify_ok"):
                continue
            verify_success_payload = {
                "code": verify_json.get("code"),
                "msg": verify_json.get("msg"),
                "attempt": attempt,
                "captcha_image_path": str(image_path),
                "captcha_guess": captcha_guess,
            }
            break

        return verify_success_payload, verify_attempts

    def _creditchina_detail_requires_reverify(self, detail_json: dict[str, Any] | None) -> bool:
        payload = dict(detail_json or {})
        status = int(payload.get("status") or 0)
        message = str(payload.get("message") or "")
        return status == 10001 and "验证码已失效" in message

    def _creditchina_pick_candidate(
        self,
        candidates: list[dict[str, Any]],
        *,
        credit_code: str,
    ) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
        normalized_credit_code = str(credit_code or "").strip().upper()
        normalized_candidates = [dict(item) for item in candidates if isinstance(item, dict)]
        for candidate in normalized_candidates:
            code = str(candidate.get("accurate_entity_code") or "").strip().upper()
            if code and code == normalized_credit_code:
                return candidate, normalized_candidates
        if len(normalized_candidates) == 1:
            return normalized_candidates[0], normalized_candidates
        return None, normalized_candidates

    def _build_creditchina_detail_url(self, candidate: dict[str, Any]) -> str:
        entity_type = str(candidate.get("entityType") or "1").strip() or "1"
        keyword = quote(str(candidate.get("accurate_entity_name_query") or candidate.get("accurate_entity_name") or "").strip())
        uuid_value = quote(str(candidate.get("uuid") or "").strip())
        credit_code = quote(str(candidate.get("accurate_entity_code") or "").strip().upper())
        return (
            "https://www.creditchina.gov.cn/xinyongxinxixiangqing/xyDetail.html"
            f"?searchState=1&entityType={entity_type}&keyword={keyword}&uuid={uuid_value}&tyshxydm={credit_code}"
        )

    def _build_creditchina_catalog_search_query(
        self,
        runtime_state: dict[str, Any] | None,
        *,
        credit_code: str,
    ) -> dict[str, Any]:
        state = dict(runtime_state or {})
        normalized_credit_code = str(credit_code or "").strip().upper()
        return {
            "index": str(state.get("page_index") or "0").strip() or "0",
            "scenes": str(state.get("scenes") or "defaultScenario").strip() or "defaultScenario",
            "tableName": str(state.get("table_name") or "credit_xyzx_tyshxydm").strip() or "credit_xyzx_tyshxydm",
            "searchState": str(state.get("search_state") or "2").strip() or "2",
            "entityType": str(state.get("entity_type") or "1,2,4,5,6,7,8").strip() or "1,2,4,5,6,7,8",
            "keyword": str(state.get("keyword") or normalized_credit_code).strip() or normalized_credit_code,
        }

    def _build_creditchina_detail_api_query(self, runtime_state: dict[str, Any] | None) -> dict[str, Any]:
        state = dict(runtime_state or {})
        extra_query: dict[str, Any] = {}
        for key, state_key in (
            ("searchState", "search_state"),
            ("entityType", "entity_type"),
            ("keyword", "keyword"),
            ("uuid", "page_uuid"),
            ("tyshxydm", "tyshxydm"),
        ):
            value = str(state.get(state_key) or "").strip()
            if value:
                extra_query[key] = value
        return extra_query

    def _normalize_creditchina_api_result(
        self,
        *,
        search_keyword: str,
        credit_code: str,
        selected_candidate: dict[str, Any] | None,
        detail_payload: dict[str, Any],
    ) -> dict[str, Any]:
        detail_data = dict(detail_payload.get("data") or {})
        basic_section = dict(detail_data.get("data") or {})
        basic_entity = dict(basic_section.get("entity") or {})
        head_entity = dict(detail_data.get("headEntity") or {})
        customs_section = dict(detail_data.get("hgData") or {})
        customs_entity = dict(customs_section.get("entity") or {})
        normalized = {
            "enterprise_name": str(head_entity.get("jgmc") or selected_candidate.get("accurate_entity_name") if selected_candidate else ""),
            "credit_code": str(head_entity.get("tyshxydm") or credit_code or "").strip().upper(),
            "status": str(head_entity.get("status") or ""),
            "legal_person": str(basic_entity.get("name") or ""),
            "enterprise_type": str(basic_entity.get("enttype") or ""),
            "establish_date": str(basic_entity.get("esdate") or ""),
            "address": str(basic_entity.get("dom") or ""),
            "registration_authority": str(basic_entity.get("regorg") or ""),
        }
        return {
            "input": {
                "keyword": str(search_keyword or ""),
                "credit_code": str(credit_code or "").strip().upper(),
            },
            "detail": {
                "head_entity": head_entity,
                "basic_entity": basic_entity,
                "customs_entity": customs_entity,
            },
            "normalized": normalized,
        }

    def _normalize_creditchina_dom_result(
        self,
        *,
        search_keyword: str,
        credit_code: str,
        candidate: dict[str, Any] | None,
        body_text: str,
    ) -> dict[str, Any]:
        selected_candidate = dict(candidate or {})
        normalized_credit_code = (
            str(selected_candidate.get("accurate_entity_code") or credit_code or "").strip().upper()
        )
        enterprise_name = str(
            selected_candidate.get("accurate_entity_name")
            or selected_candidate.get("accurate_entity_name_query")
            or selected_candidate.get("company_name")
            or ""
        ).strip()
        enterprise_type = str(
            selected_candidate.get("subject_type")
            or selected_candidate.get("entity_type_text")
            or selected_candidate.get("entityType")
            or ""
        ).strip()
        return {
            "input": {
                "keyword": str(search_keyword or normalized_credit_code or "").strip(),
                "credit_code": normalized_credit_code,
            },
            "detail": {
                "page_candidate": selected_candidate,
                "body_excerpt": _trim_text(body_text, 600),
            },
            "normalized": {
                "enterprise_name": enterprise_name,
                "credit_code": normalized_credit_code,
                "status": "",
                "legal_person": "",
                "enterprise_type": enterprise_type,
                "establish_date": "",
                "address": "",
                "registration_authority": "",
            },
        }

    async def _extract_creditchina_dom_result_async(
        self,
        page: AsyncPage,
        *,
        credit_code: str = "",
    ) -> dict[str, Any]:
        expected_credit_code = str(credit_code or self._extract_creditchina_keyword(page.url)).strip().upper()
        search_keyword = expected_credit_code or str(self._extract_creditchina_keyword(page.url)).strip()
        try:
            body_text = await page.locator("body").first.inner_text(timeout=3_000) or ""
        except Exception:
            body_text = ""

        normalized_body = str(body_text or "").upper()
        no_result = "很抱歉，没有找到您搜索的数据" in body_text

        dom_candidates_payload = await page.evaluate(
            """
            () => Array.from(document.querySelectorAll(".companylists li.company-item")).map((node) => ({
              dataMessage: node.getAttribute("data-message") || "",
              companyName: (node.querySelector(".company-name")?.textContent || "").trim(),
              companyMessages: (node.querySelector(".company-messages")?.textContent || "").trim(),
            }))
            """
        )

        parsed_candidates: list[dict[str, Any]] = []
        for item in dom_candidates_payload or []:
            if not isinstance(item, dict):
                continue
            data_message = str(item.get("dataMessage") or "").strip()
            candidate: dict[str, Any] = {}
            if data_message:
                try:
                    loaded = json.loads(data_message)
                except Exception:
                    loaded = {}
                if isinstance(loaded, dict):
                    candidate.update({str(key): value for key, value in loaded.items()})
            company_name = str(item.get("companyName") or "").strip()
            company_messages = str(item.get("companyMessages") or "").strip()
            code_match = re.search(r"统一社会信用代码[:：]\s*([0-9A-Z]+)", company_messages, re.IGNORECASE)
            subject_type_match = re.search(r"主体类型[:：]\s*([^\n]+)", company_messages)
            if company_name and not candidate.get("accurate_entity_name"):
                candidate["accurate_entity_name"] = company_name
            if company_name and not candidate.get("accurate_entity_name_query"):
                candidate["accurate_entity_name_query"] = company_name
            if code_match and not candidate.get("accurate_entity_code"):
                candidate["accurate_entity_code"] = str(code_match.group(1) or "").strip().upper()
            if subject_type_match:
                candidate["subject_type"] = str(subject_type_match.group(1) or "").strip()
            if candidate:
                parsed_candidates.append(candidate)

        if not parsed_candidates and body_text:
            body_match = re.search(
                r"(?P<name>[^\n]{2,120})\s*\n+\s*统一社会信用代码[:：]\s*(?P<code>[0-9A-Z]+)\s*主体类型[:：]\s*(?P<subject>[^\n]+)",
                body_text,
                re.IGNORECASE,
            )
            if body_match:
                parsed_candidates.append(
                    {
                        "accurate_entity_name": str(body_match.group("name") or "").strip(),
                        "accurate_entity_name_query": str(body_match.group("name") or "").strip(),
                        "accurate_entity_code": str(body_match.group("code") or "").strip().upper(),
                        "subject_type": str(body_match.group("subject") or "").strip(),
                    }
                )

        selected_candidate, normalized_candidates = self._creditchina_pick_candidate(
            parsed_candidates,
            credit_code=expected_credit_code,
        )
        if selected_candidate is None and len(normalized_candidates) == 1:
            selected_candidate = normalized_candidates[0]

        has_credit_code = bool(expected_credit_code) and expected_credit_code in normalized_body
        has_result_keywords = "统一社会信用代码" in body_text and (
            "主体类型" in body_text or "企业法人" in body_text or "共" in body_text
        )
        result_ready = selected_candidate is not None or (has_credit_code and has_result_keywords)
        if selected_candidate is None and result_ready:
            selected_candidate = {
                "accurate_entity_name": "",
                "accurate_entity_name_query": "",
                "accurate_entity_code": expected_credit_code,
            }
        normalized_payload = (
            self._normalize_creditchina_dom_result(
                search_keyword=search_keyword,
                credit_code=expected_credit_code,
                candidate=selected_candidate,
                body_text=body_text,
            )
            if selected_candidate is not None
            else {}
        )
        return {
            "ok": result_ready or no_result,
            "result_ready": result_ready,
            "no_result": no_result,
            "expected_credit_code": expected_credit_code,
            "candidate_count": len(normalized_candidates),
            "selected_candidate": selected_candidate,
            "candidates": normalized_candidates[:10],
            "normalized": normalized_payload,
            "body_excerpt": _trim_text(body_text, 600),
        }

    async def _wait_for_creditchina_challenge_state_async(
        self,
        page: AsyncPage,
        *,
        target_url: str,
        timeout_ms: int = 6_000,
        poll_interval_ms: int = 400,
    ) -> dict[str, Any]:
        wait_budget_ms = max(0, min(int(timeout_ms), 20_000))
        poll_budget_ms = max(150, min(int(poll_interval_ms), 1_500))
        elapsed_ms = 0
        observations: list[dict[str, Any]] = []
        last_cookie_summary: dict[str, Any] = {}
        last_storage_summary: dict[str, Any] = {}

        while True:
            last_cookie_summary = await self._context_cookie_summary_async(page, target_url)
            last_storage_summary = await self._storage_summary_async(page)
            ready = bool(last_cookie_summary.get("has_js_cookie")) and bool(
                (last_storage_summary.get("local_keys") or []) or (last_storage_summary.get("session_keys") or [])
            )
            observations.append(
                {
                    "elapsed_ms": elapsed_ms,
                    "cookie_names": list(last_cookie_summary.get("cookie_names") or []),
                    "has_js_cookie": bool(last_cookie_summary.get("has_js_cookie")),
                    "local_keys": list(last_storage_summary.get("local_keys") or []),
                    "session_keys": list(last_storage_summary.get("session_keys") or []),
                }
            )
            if ready or elapsed_ms >= wait_budget_ms:
                return {
                    "ok": ready,
                    "elapsed_ms": elapsed_ms,
                    "cookie_summary": last_cookie_summary,
                    "storage_summary": last_storage_summary,
                    "observations": observations[-6:],
                }
            await page.wait_for_timeout(poll_budget_ms)
            elapsed_ms += poll_budget_ms

    def _warm_creditchina_cookie_candidates_sync(self, targets: list[str]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        session = requests.Session()
        session.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/134.0.0.0 Safari/537.36"
                ),
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                "Cache-Control": "no-cache",
                "Pragma": "no-cache",
            }
        )

        request_logs: list[dict[str, Any]] = []
        for target in targets:
            try:
                response = session.get(target, allow_redirects=True, timeout=20)
                request_logs.append(
                    {
                        "target": target,
                        "final_url": response.url,
                        "status": response.status_code,
                        "headers": {
                            name: value
                            for name in ("content-type", "location", "server", "x-via-jsl", "retry-after")
                            if (value := response.headers.get(name))
                        },
                        "set_cookie_names": list(response.cookies.keys())[:12],
                    }
                )
            except Exception as exc:
                request_logs.append(
                    {
                        "target": target,
                        "error": str(exc),
                    }
                )

        fallback_url = targets[0] if targets else "https://www.creditchina.gov.cn/"
        cookies: list[dict[str, Any]] = []
        seen_keys: set[tuple[str, str, str, str]] = set()
        cookie_names: list[str] = []
        for cookie in session.cookies:
            converted = self._requests_cookie_to_playwright_cookie(cookie, fallback_url=fallback_url)
            dedupe_key = (
                str(converted.get("name") or ""),
                str(converted.get("domain") or ""),
                str(converted.get("path") or ""),
                str(converted.get("url") or ""),
            )
            if dedupe_key in seen_keys:
                continue
            seen_keys.add(dedupe_key)
            cookies.append(converted)
            name = str(converted.get("name") or "")
            if name and name not in cookie_names:
                cookie_names.append(name)

        safe_payload = {
            "ok": bool(cookies),
            "target_count": len(targets),
            "cookie_count": len(cookies),
            "cookie_names": cookie_names[:12],
            "requests": request_logs,
        }
        return cookies, safe_payload

    async def _refresh_creditchina_cookies_via_requests_async(
        self,
        page: AsyncPage,
        *,
        credit_code: str,
    ) -> dict[str, Any]:
        home_url = "https://www.creditchina.gov.cn/"
        query_url = self._build_creditchina_query_url(credit_code)
        targets = [home_url, query_url]
        try:
            cookies, safe_payload = await asyncio.to_thread(
                self._warm_creditchina_cookie_candidates_sync,
                targets,
            )
        except Exception as exc:
            payload = {
                "ok": False,
                "cookie_count": 0,
                "cookie_names": [],
                "targets": targets,
                "error": str(exc),
            }
            self._push_debug_event("requests_cookie_refresh", payload)
            return payload

        injected_count = 0
        inject_error = ""
        if cookies:
            try:
                await page.context.add_cookies(cookies)
                injected_count = len(cookies)
            except Exception as exc:
                inject_error = str(exc)

        payload = {
            **safe_payload,
            "targets": targets,
            "injected_count": injected_count,
            "inject_error": inject_error,
        }
        self._push_debug_event("requests_cookie_refresh", payload)
        return payload

    async def _continue_creditchina_challenge_async(
        self,
        page: AsyncPage,
        artifact_dir: Path,
        *,
        target_url: str,
        wait_seconds: float = 4,
        max_retries: int = 2,
        capture_artifacts: bool = True,
    ) -> dict[str, Any]:
        home_url = "https://www.creditchina.gov.cn/"
        resolved_target_url = str(target_url or "").strip() or home_url
        wait_budget_ms = max(500, min(int(float(wait_seconds) * 1000), 20_000))
        retry_limit = max(1, min(int(max_retries), 4))
        keyword = self._extract_creditchina_keyword(resolved_target_url)
        attempts: list[dict[str, Any]] = []

        for attempt_index in range(1, retry_limit + 1):
            request_refresh = None
            if keyword:
                request_refresh = await self._refresh_creditchina_cookies_via_requests_async(
                    page,
                    credit_code=keyword,
                )

            home_response = await page.goto(home_url, wait_until="domcontentloaded")
            await self._record_navigation_async(page, home_response, source=f"creditchina_challenge_home_{attempt_index}")
            await self._settle_page_async(page, extra_wait_ms=500)
            home_diagnosis = await self._diagnose_access_async(page)
            challenge_state = await self._wait_for_creditchina_challenge_state_async(
                page,
                target_url=home_url,
                timeout_ms=wait_budget_ms,
            )

            target_response = await page.goto(resolved_target_url, wait_until="domcontentloaded")
            await self._record_navigation_async(page, target_response, source=f"creditchina_challenge_resume_{attempt_index}")
            await self._settle_page_async(page, extra_wait_ms=1600)
            target_diagnosis = await self._diagnose_access_async(page)
            target_cookie_summary = await self._context_cookie_summary_async(page, resolved_target_url)

            attempt_payload = {
                "step": attempt_index,
                "request_refresh": request_refresh,
                "home_diagnosis": home_diagnosis,
                "challenge_state": challenge_state,
                "target_diagnosis": target_diagnosis,
                "target_cookie_summary": target_cookie_summary,
                "current_url": page.url,
            }
            attempts.append(attempt_payload)
            self._push_debug_event(
                "creditchina_challenge_resume",
                {
                    "step": attempt_index,
                    "challenge_ready": bool(challenge_state.get("ok")),
                    "challenge_detected": bool(target_diagnosis.get("challenge_detected")),
                    "blank_page_like": bool(target_diagnosis.get("blank_page_like")),
                    "cookie_names": list(target_cookie_summary.get("cookie_names") or []),
                    "url": page.url,
                },
            )

            if not target_diagnosis.get("challenge_detected") and not target_diagnosis.get("blank_page_like"):
                payload = {
                    "ok": True,
                    "retried": attempt_index,
                    "resolved": True,
                    "strategy": "creditchina_browser_bootstrap",
                    "attempts": attempts,
                    "final_diagnosis": target_diagnosis,
                }
                if capture_artifacts:
                    payload["artifacts"] = await self._save_page_artifacts_async(
                        page,
                        artifact_dir,
                        stem="creditchina-challenge-resolved",
                    )
                return payload

        final_diagnosis = attempts[-1]["target_diagnosis"] if attempts else await self._diagnose_access_async(page)
        payload = {
            "ok": False,
            "retried": len(attempts),
            "resolved": False,
            "strategy": "creditchina_browser_bootstrap",
            "attempts": attempts,
            "final_diagnosis": final_diagnosis,
        }
        if capture_artifacts:
            payload["artifacts"] = await self._save_page_artifacts_async(
                page,
                artifact_dir,
                stem="creditchina-challenge-final",
            )
        return payload

    def _build_result_output_stem(self, credit_code: str, base_name: str = "") -> str:
        if str(base_name or "").strip():
            stem_base = _sanitize_filename(base_name, default_name="creditchina-result")
        else:
            timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            credit_part = _sanitize_filename(credit_code, default_name="credit")
            stem_base = f"creditchina-{credit_part}-{timestamp}"
        return stem_base

    async def _write_creditchina_result_files_async(
        self,
        page: AsyncPage,
        artifact_dir: Path,
        *,
        credit_code: str,
        succeeded: bool,
        stage: str,
        error_message: str = "",
        base_name: str = "",
        result_payload: dict[str, Any] | None = None,
        text_payload: str = "",
    ) -> dict[str, Any]:
        stem = self._build_result_output_stem(credit_code, base_name=base_name)
        artifact_dir.mkdir(parents=True, exist_ok=True)
        result_json_path = artifact_dir / f"{stem}.json"
        result_text_path = artifact_dir / f"{stem}.txt"
        artifacts = await self._save_page_artifacts_async(page, artifact_dir, stem=stem)
        try:
            body_text = await page.locator("body").first.inner_text(timeout=3_000) or ""
        except Exception:
            body_text = ""
        diagnosis = await self._diagnose_access_async(page)
        sanitized_result_payload = _sanitize_debug_payload(result_payload or {}) if result_payload is not None else {}
        payload = {
            "ok": succeeded,
            "stage": stage,
            "credit_code": credit_code,
            "url": page.url,
            "error": error_message,
            "diagnosis": diagnosis,
            "artifacts": artifacts,
            "body_text": body_text,
            "result_payload": sanitized_result_payload,
        }
        result_json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        if str(text_payload or "").strip():
            result_text_path.write_text(_sanitize_text_payload(str(text_payload)), encoding="utf-8")
        elif result_payload is not None:
            result_text_path.write_text(json.dumps(sanitized_result_payload, ensure_ascii=False, indent=2), encoding="utf-8")
        else:
            result_text_path.write_text(body_text, encoding="utf-8")
        self._push_debug_event(
            "creditchina_result_saved",
            {
                "credit_code": credit_code,
                "ok": succeeded,
                "stage": stage,
                "run_output_dir": str(artifact_dir),
                "result_json_path": str(result_json_path),
                "result_text_path": str(result_text_path),
            },
        )
        return {
            **payload,
            "run_output_dir": str(artifact_dir),
            "result_json_path": str(result_json_path),
            "result_text_path": str(result_text_path),
        }

    async def _prepare_creditchina_query_page_async(
        self,
        page: AsyncPage,
        *,
        credit_code: str,
    ) -> dict[str, Any]:
        home_url = "https://www.creditchina.gov.cn/"
        query_url = self._build_creditchina_query_url(credit_code)
        steps: list[dict[str, Any]] = []

        cookie_refresh = await self._refresh_creditchina_cookies_via_requests_async(
            page,
            credit_code=credit_code,
        )
        steps.append(
            {
                "stage": "requests_cookie_refresh",
                **cookie_refresh,
            }
        )

        response = await page.goto(home_url, wait_until="domcontentloaded")
        await self._record_navigation_async(page, response, source="creditchina_home")
        await self._settle_page_async(page, extra_wait_ms=1200)
        home_diagnosis = await self._diagnose_access_async(page)
        steps.append({"stage": "home_open", "url": page.url, "diagnosis": home_diagnosis})
        if home_diagnosis.get("creditchina_captcha_image_broken"):
            modal_reset = await self._refresh_creditchina_after_captcha_cancel_async(page)
            steps.append({"stage": "home_captcha_cancel_refresh", **modal_reset})
            home_diagnosis = dict(modal_reset.get("diagnosis") or home_diagnosis)
        if home_diagnosis.get("challenge_detected") or home_diagnosis.get("blank_page_like"):
            challenge_state = await self._wait_for_creditchina_challenge_state_async(
                page,
                target_url=home_url,
                timeout_ms=5_000,
            )
            steps.append(
                {
                    "stage": "home_challenge_state",
                    **challenge_state,
                }
            )

        search_input_candidates = [
            "#search_input",
            "input[placeholder*='统一社会信用代码']",
            "input[placeholder*='主体名称']",
        ]
        search_button_candidates = [
            "button:has-text('搜索')",
            "a:has-text('搜索')",
            "input[type='button'][value*='搜索']",
            "input[type='submit'][value*='搜索']",
            "#search_btn",
            ".search-btn",
            ".searchBtn",
        ]

        home_input_selector = await self._first_visible_selector_async(page, search_input_candidates)
        if home_input_selector and not home_diagnosis.get("challenge_detected"):
            await page.locator(home_input_selector).first.fill(credit_code)
            steps.append({"stage": "home_fill", "selector": home_input_selector})
            try:
                search_selector = await self._click_first_visible_async(page, search_button_candidates)
                steps.append({"stage": "home_click_search", "selector": search_selector})
                await self._settle_page_async(page, extra_wait_ms=1800)
                after_home_click = await self._diagnose_access_async(page)
                if after_home_click.get("creditchina_captcha_image_broken"):
                    modal_reset = await self._refresh_creditchina_after_captcha_cancel_async(page)
                    steps.append({"stage": "home_click_captcha_cancel_refresh", **modal_reset})
                    after_home_click = dict(modal_reset.get("diagnosis") or after_home_click)
                steps.append({"stage": "home_after_click", "url": page.url, "diagnosis": after_home_click})
                if not after_home_click.get("challenge_detected") and (
                    await self._first_visible_selector_async(page, ["#vcode", "#vcodeimg"])
                ):
                    return {
                        "ok": True,
                        "used_home": True,
                        "query_url": query_url,
                        "steps": steps,
                    }
            except Exception as exc:
                steps.append({"stage": "home_click_search_error", "error": str(exc)})

        response = await page.goto(query_url, wait_until="domcontentloaded")
        await self._record_navigation_async(page, response, source="creditchina_query_direct")
        await self._settle_page_async(page, extra_wait_ms=1800)
        direct_diagnosis = await self._diagnose_access_async(page)
        if direct_diagnosis.get("creditchina_captcha_image_broken"):
            modal_reset = await self._refresh_creditchina_after_captcha_cancel_async(page)
            steps.append({"stage": "direct_query_captcha_cancel_refresh", **modal_reset})
            direct_diagnosis = dict(modal_reset.get("diagnosis") or direct_diagnosis)
        steps.append({"stage": "direct_query_open", "url": page.url, "diagnosis": direct_diagnosis})
        return {
            "ok": not direct_diagnosis.get("challenge_detected"),
            "used_home": False,
            "query_url": query_url,
            "steps": steps,
        }

    async def _run_creditchina_private_api_query_async(
        self,
        page: AsyncPage,
        artifact_dir: Path,
        *,
        credit_code: str,
        max_captcha_attempts: int = 6,
    ) -> dict[str, Any]:
        normalized_credit_code = str(credit_code or "").strip().upper()
        seed_state = await self._wait_for_creditchina_rcw_async(page, timeout_ms=8_000)
        runtime_state = dict(seed_state.get("runtime_state") or {})
        rcw_token = str(runtime_state.get("rcw_token") or "").strip()
        if not rcw_token:
            return {
                "ok": False,
                "stage": "missing_rcw",
                "error": "未能从当前浏览器会话中捕获 rcwCQitg，无法启动 private-api 查询流。",
                "seed_state": seed_state,
            }

        search_extra_query = self._build_creditchina_catalog_search_query(
            runtime_state,
            credit_code=normalized_credit_code,
        )
        initial_search_payload = await self._creditchina_api_json_request_async(
            page,
            endpoint="catalogSearchHome",
            rcw_token=rcw_token,
            extra_query=search_extra_query,
        )
        initial_search_json = dict(initial_search_payload.get("json") or {})

        verify_attempts: list[dict[str, Any]] = []
        verify_success_payload: dict[str, Any] | None = None
        search_payload = initial_search_payload
        search_json = initial_search_json
        if int(search_json.get("status") or 0) != 1:
            verify_success_payload, verify_attempts = await self._run_creditchina_private_api_verify_async(
                page,
                artifact_dir,
                rcw_token=rcw_token,
                max_attempts=max_captcha_attempts,
                file_prefix="creditchina-private-api-verify",
            )
            if verify_attempts and not any(str(item.get("captcha_image_path") or "").strip() for item in verify_attempts):
                return {
                    "ok": False,
                    "stage": "captcha_image_missing",
                    "error": "private-api 验证码接口未返回图片内容。",
                    "seed_state": seed_state,
                    "search": search_json,
                    "search_query": search_extra_query,
                    "verify_attempts": verify_attempts,
                }
            if verify_success_payload is not None:
                search_payload = await self._creditchina_api_json_request_async(
                    page,
                    endpoint="catalogSearchHome",
                    rcw_token=rcw_token,
                    extra_query=search_extra_query,
                )
                search_json = dict(search_payload.get("json") or {})

            if int(search_json.get("status") or 0) != 1 and verify_success_payload is None:
                return {
                    "ok": False,
                    "stage": "verify_failed",
                    "error": "验证码经 private-api 校验后仍未通过。",
                    "seed_state": seed_state,
                    "search": search_json,
                    "search_query": search_extra_query,
                    "verify_attempts": verify_attempts,
                }

        if int(search_json.get("status") or 0) != 1:
            return {
                "ok": False,
                "stage": "catalog_search_failed",
                "error": "catalogSearchHome 未返回成功状态。",
                "seed_state": seed_state,
                "verify": verify_success_payload,
                "search_query": search_extra_query,
                "search": search_json,
            }

        search_data = dict(search_json.get("data") or {})
        candidates = list(search_data.get("list") or [])
        selected_candidate, normalized_candidates = self._creditchina_pick_candidate(
            candidates,
            credit_code=normalized_credit_code,
        )
        if selected_candidate is None:
            return {
                "ok": False,
                "stage": "candidate_not_found",
                "error": "搜索结果中未找到与统一社会信用代码精确匹配的主体。",
                "seed_state": seed_state,
                "verify": verify_success_payload,
                "search_result": {
                    "total": search_data.get("total"),
                    "candidate_count": len(normalized_candidates),
                    "candidates": normalized_candidates[:10],
                },
            }

        detail_url = self._build_creditchina_detail_url(selected_candidate)
        response = await page.goto(detail_url, wait_until="domcontentloaded")
        await self._record_navigation_async(page, response, source="creditchina_detail_page")
        await self._settle_page_async(page, extra_wait_ms=1800)
        detail_page_diagnosis = await self._diagnose_access_async(page)
        if detail_page_diagnosis.get("challenge_detected") or detail_page_diagnosis.get("blank_page_like"):
            retry_payload = await self._retry_on_access_challenge_async(
                page,
                artifact_dir,
                wait_seconds=4,
                max_retries=2,
                capture_artifacts=True,
            )
            detail_page_diagnosis = dict(retry_payload.get("final_diagnosis") or detail_page_diagnosis)
            if detail_page_diagnosis.get("challenge_detected") or detail_page_diagnosis.get("blank_page_like"):
                return {
                    "ok": False,
                    "stage": "detail_page_access_failed",
                    "error": "详情页仍处于挑战/空白状态，无法继续 private-api 详情查询。",
                    "seed_state": seed_state,
                    "verify": verify_success_payload,
                    "search_result": {
                        "total": search_data.get("total"),
                        "selected_candidate": selected_candidate,
                    },
                    "retry": retry_payload,
                }

        detail_seed_state = await self._wait_for_creditchina_rcw_async(page, timeout_ms=6_000)
        detail_runtime_state = dict(detail_seed_state.get("runtime_state") or {})
        detail_rcw_token = str(detail_runtime_state.get("rcw_token") or rcw_token).strip()
        detail_payload = await self._creditchina_api_json_request_async(
            page,
            endpoint="getTyshxydmDetailsContent",
            rcw_token=detail_rcw_token,
            extra_query=self._build_creditchina_detail_api_query(detail_runtime_state),
        )
        detail_json = dict(detail_payload.get("json") or {})
        detail_verify_payload: dict[str, Any] | None = None
        detail_verify_attempts: list[dict[str, Any]] = []
        if self._creditchina_detail_requires_reverify(detail_json):
            detail_verify_payload, detail_verify_attempts = await self._run_creditchina_private_api_verify_async(
                page,
                artifact_dir,
                rcw_token=detail_rcw_token,
                max_attempts=min(max_captcha_attempts, 4),
                file_prefix="creditchina-private-api-detail-verify",
            )
            if detail_verify_payload is not None:
                detail_payload = await self._creditchina_api_json_request_async(
                    page,
                    endpoint="getTyshxydmDetailsContent",
                    rcw_token=detail_rcw_token,
                    extra_query=self._build_creditchina_detail_api_query(detail_runtime_state),
                )
                detail_json = dict(detail_payload.get("json") or {})
        if int(detail_json.get("status") or 0) != 1:
            return {
                "ok": False,
                "stage": "detail_api_failed",
                "error": "getTyshxydmDetailsContent 未返回成功状态。",
                "seed_state": seed_state,
                "verify": verify_success_payload,
                "detail_verify": detail_verify_payload,
                "detail_verify_attempts": detail_verify_attempts,
                "search_result": {
                    "total": search_data.get("total"),
                    "selected_candidate": selected_candidate,
                },
                "detail": detail_json,
            }

        normalized_payload = self._normalize_creditchina_api_result(
            search_keyword=str(runtime_state.get("keyword") or selected_candidate.get("accurate_entity_name_query") or ""),
            credit_code=normalized_credit_code,
            selected_candidate=selected_candidate,
            detail_payload=detail_json,
        )
        return {
            "ok": True,
            "stage": "api_result_ready",
            "verify": verify_success_payload,
            "verify_attempts": verify_attempts,
            "detail_verify": detail_verify_payload,
            "detail_verify_attempts": detail_verify_attempts,
            "search_result": {
                "page": search_data.get("page"),
                "total": search_data.get("total"),
                "total_size": search_data.get("totalSize"),
                "selected_candidate": selected_candidate,
                "candidates": normalized_candidates[:10],
            },
            "detail": detail_json.get("data") or {},
            **normalized_payload,
        }

    async def _solve_creditchina_captcha_async(
        self,
        page: AsyncPage,
        artifact_dir: Path,
        *,
        credit_code: str = "",
        max_attempts: int = 6,
    ) -> dict[str, Any]:
        existing_result = await self._detect_creditchina_result_ready_async(page, credit_code=credit_code)
        if existing_result.get("ok"):
            return {
                "ok": True,
                "skipped_captcha": True,
                "result_ready": existing_result,
                "attempts": [],
            }

        image_selector = await self._first_visible_selector_async(
            page,
            ["#vcodeimg", "img[id*='vcode']", "img[src*='vcode']"],
        )
        input_selector = await self._first_visible_selector_async(
            page,
            ["#vcode", "input[placeholder*='验证码']", "input[name*='vcode']"],
        )
        submit_selector = await self._first_visible_selector_async(
            page,
            [
                "button:has-text('验证')",
                "a:has-text('验证')",
                "input[type='button'][value*='验证']",
                "input[type='submit'][value*='验证']",
                ".btn:has-text('验证')",
            ],
        )

        if not image_selector or not input_selector or not submit_selector:
            return {
                "ok": False,
                "error": "未找到完整的验证码元素（图片/输入框/验证按钮）。",
                "image_selector": image_selector,
                "input_selector": input_selector,
                "submit_selector": submit_selector,
                "result_ready": existing_result,
            }

        attempts: list[dict[str, Any]] = []
        last_wait_payload: dict[str, Any] = {}
        for attempt in range(1, max(1, min(int(max_attempts), 8)) + 1):
            last_wait_payload = await self._wait_for_creditchina_captcha_ready_async(page)
            if not last_wait_payload.get("ok"):
                return {
                    "ok": False,
                    "error": str(last_wait_payload.get("error") or "验证码图片尚未准备好。"),
                    "wait": last_wait_payload,
                    "attempts": attempts,
                }

            image_path = artifact_dir / f"creditchina-captcha-attempt-{attempt}.png"
            await page.locator(image_selector).first.screenshot(path=str(image_path))
            guess = solve_captcha_file(image_path)
            await page.locator(input_selector).first.fill(guess)
            await page.locator(submit_selector).first.click()
            await self._settle_page_async(page, extra_wait_ms=2600)
            diagnosis = await self._diagnose_access_async(page)
            result_ready = await self._detect_creditchina_result_ready_async(page, credit_code=credit_code)
            captcha_still_visible = bool(await self._first_visible_selector_async(page, [image_selector, input_selector]))
            attempts.append(
                {
                    "attempt": attempt,
                    "guess": guess,
                    "image_path": str(image_path),
                    "url": page.url,
                    "wait_refresh_count": last_wait_payload.get("refresh_count"),
                    "result_ready": bool(result_ready.get("ok")),
                    "captcha_still_visible": captcha_still_visible,
                    "challenge_detected": diagnosis.get("challenge_detected"),
                }
            )
            if diagnosis.get("challenge_detected"):
                return {
                    "ok": False,
                    "error": "验证码提交后命中安全挑战页。",
                    "wait": last_wait_payload,
                    "attempts": attempts,
                    "diagnosis": diagnosis,
                    "result_ready": result_ready,
                }
            if result_ready.get("ok"):
                return {
                    "ok": True,
                    "wait": last_wait_payload,
                    "attempts": attempts,
                    "diagnosis": diagnosis,
                    "result_ready": result_ready,
                }
            if not captcha_still_visible:
                return {
                    "ok": True,
                    "wait": last_wait_payload,
                    "attempts": attempts,
                    "diagnosis": diagnosis,
                }
        return {
            "ok": False,
            "error": "验证码连续重试后仍未通过。",
            "wait": last_wait_payload,
            "attempts": attempts,
        }

    async def _retry_on_access_challenge_async(
        self,
        page: AsyncPage,
        artifact_dir: Path,
        *,
        wait_seconds: float = 4,
        max_retries: int = 2,
        capture_artifacts: bool = True,
    ) -> dict[str, Any]:
        attempts: list[dict[str, Any]] = []
        diagnosis = await self._diagnose_access_async(page)
        attempts.append({"step": 0, "diagnosis": diagnosis})
        retry_limit = max(0, min(int(max_retries), 4))
        if not diagnosis.get("retry_recommended"):
            payload = {
                "ok": True,
                "retried": 0,
                "resolved": not diagnosis.get("challenge_detected"),
                "attempts": attempts,
                "final_diagnosis": diagnosis,
            }
            self._push_debug_event("challenge_retry", {"retried": 0, "resolved": payload["resolved"], "url": page.url})
            return payload

        if self._is_creditchina_url(page.url or diagnosis.get("url")):
            special_payload = await self._continue_creditchina_challenge_async(
                page,
                artifact_dir,
                target_url=page.url,
                wait_seconds=wait_seconds,
                max_retries=max(1, retry_limit),
                capture_artifacts=capture_artifacts,
            )
            self._push_debug_event(
                "challenge_retry",
                {
                    "retried": special_payload.get("retried"),
                    "resolved": special_payload.get("resolved"),
                    "url": page.url,
                    "challenge_detected": (special_payload.get("final_diagnosis") or {}).get("challenge_detected"),
                    "strategy": special_payload.get("strategy"),
                },
            )
            return special_payload

        for attempt_index in range(1, retry_limit + 1):
            await page.wait_for_timeout(max(0, min(int(float(wait_seconds) * 1000), 20_000)))
            reload_error = ""
            response = None
            try:
                response = await page.reload(wait_until="domcontentloaded", timeout=20_000)
            except Exception as exc:
                reload_error = str(exc)
            await self._record_navigation_async(page, response, source=f"retry_on_access_challenge_{attempt_index}")
            await self._settle_page_async(page)
            diagnosis = await self._diagnose_access_async(page)
            attempts.append(
                {
                    "step": attempt_index,
                    "reload_error": reload_error,
                    "diagnosis": diagnosis,
                }
            )
            if not diagnosis.get("retry_recommended"):
                break

        final_diagnosis = attempts[-1]["diagnosis"]
        payload = {
            "ok": not final_diagnosis.get("challenge_detected"),
            "retried": max(0, len(attempts) - 1),
            "resolved": not final_diagnosis.get("retry_recommended"),
            "attempts": attempts,
            "final_diagnosis": final_diagnosis,
        }
        if capture_artifacts:
            payload["artifacts"] = await self._save_page_artifacts_async(page, artifact_dir, stem="challenge-retry")
        self._push_debug_event(
            "challenge_retry",
            {
                "retried": payload["retried"],
                "resolved": payload["resolved"],
                "url": page.url,
                "challenge_detected": final_diagnosis.get("challenge_detected"),
            },
        )
        return payload

    async def _try_read_result_json_async(self, page: AsyncPage) -> dict[str, Any] | None:
        result_locator = page.locator("#result-json")
        if await result_locator.count() == 0:
            return None
        try:
            text = await result_locator.first.text_content(timeout=1500) or ""
        except Exception:
            return None
        text = text.strip()
        if not text:
            return None
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {"raw_text": text}

    async def _detect_creditchina_result_ready_async(
        self,
        page: AsyncPage,
        *,
        credit_code: str = "",
    ) -> dict[str, Any]:
        page_result = await self._extract_creditchina_dom_result_async(page, credit_code=credit_code)
        return {
            "ok": bool(page_result.get("ok")),
            "result_ready": bool(page_result.get("result_ready")),
            "no_result": bool(page_result.get("no_result")),
            "expected_credit_code": page_result.get("expected_credit_code"),
            "body_excerpt": page_result.get("body_excerpt") or "",
            "page_result": page_result,
        }

    async def _wait_for_creditchina_dom_result_async(
        self,
        page: AsyncPage,
        *,
        credit_code: str,
        timeout_ms: int = 8_000,
        poll_interval_ms: int = 2_000,
    ) -> dict[str, Any]:
        wait_budget_ms = max(0, min(int(timeout_ms), 20_000))
        poll_budget_ms = max(500, min(int(poll_interval_ms), 3_000))
        elapsed_ms = 0
        observations: list[dict[str, Any]] = []
        last_result: dict[str, Any] = {}

        while True:
            last_result = await self._extract_creditchina_dom_result_async(page, credit_code=credit_code)
            observations.append(
                {
                    "elapsed_ms": elapsed_ms,
                    "ok": bool(last_result.get("ok")),
                    "result_ready": bool(last_result.get("result_ready")),
                    "no_result": bool(last_result.get("no_result")),
                    "candidate_count": int(last_result.get("candidate_count") or 0),
                }
            )
            if last_result.get("ok") or elapsed_ms >= wait_budget_ms:
                return {
                    "ok": bool(last_result.get("ok")),
                    "elapsed_ms": elapsed_ms,
                    "observations": observations,
                    "result": last_result,
                }
            await page.wait_for_timeout(poll_budget_ms)
            elapsed_ms += poll_budget_ms

    async def _captcha_error_present_async(
        self, page: AsyncPage, error_keyword: str = "验证码错误"
    ) -> tuple[bool, str]:
        try:
            body_text = await page.locator("body").first.inner_text(timeout=2000) or ""
        except Exception:
            body_text = ""
        has_error = error_keyword in body_text or error_keyword in page.url
        return has_error, body_text[:1000]

    async def _solve_and_submit_captcha_async(
        self,
        page: AsyncPage,
        artifact_dir: Path,
        *,
        image_selector: str = "#captcha-image",
        input_selector: str = "#captcha-input",
        submit_selector: str = "#captcha-confirm",
        success_selector: str = "#result-json",
        error_keyword: str = "验证码错误",
        max_attempts: int = 6,
    ) -> dict[str, Any]:
        attempts: list[dict[str, Any]] = []
        attempt_limit = max(1, min(int(max_attempts), 10))

        for attempt in range(1, attempt_limit + 1):
            output_path = artifact_dir / f"captcha-attempt-{attempt}.png"
            await page.locator(image_selector).first.screenshot(path=str(output_path))
            guess = solve_captcha_file(output_path)
            await page.locator(input_selector).first.fill(guess)
            await page.locator(submit_selector).first.click()

            try:
                await page.wait_for_load_state("domcontentloaded", timeout=8_000)
            except Exception:
                pass
            await page.wait_for_timeout(300)

            page_result = await self._try_read_result_json_async(page)
            success = page_result is not None
            if not success and success_selector:
                success = await page.locator(success_selector).count() > 0
            has_error, body_excerpt = await self._captcha_error_present_async(page, error_keyword=error_keyword)
            attempts.append(
                {
                    "attempt": attempt,
                    "guess": guess,
                    "image_path": str(output_path),
                    "current_url": page.url,
                    "has_error": has_error,
                }
            )

            if success or "/result" in page.url:
                return {
                    "ok": True,
                    "attempts": attempts,
                    "page_result": page_result,
                    "current_url": page.url,
                }

            if not has_error and await page.locator(input_selector).count() == 0:
                return {
                    "ok": False,
                    "attempts": attempts,
                    "current_url": page.url,
                    "error": "验证码提交后页面进入未知状态，请重新 inspect 页面。",
                    "body_excerpt": body_excerpt,
                }

            await page.wait_for_selector(image_selector, timeout=5_000)

        return {
            "ok": False,
            "attempts": attempts,
            "current_url": page.url,
            "error": f"验证码连续重试 {attempt_limit} 次后仍未通过。",
        }

    async def run_creditchina_private_api_flow_and_save_async(
        self,
        page: AsyncPage,
        artifact_dir: Path,
        *,
        credit_code: str,
        result_name: str = "",
        max_captcha_attempts: int = 6,
        save_failure_result: bool = True,
    ) -> dict[str, Any]:
        normalized_credit_code = str(credit_code or "").strip()
        if not normalized_credit_code:
            raise RuntimeError("统一社会信用代码不能为空。")

        current_page_result_wait = await self._wait_for_creditchina_dom_result_async(
            page,
            credit_code=normalized_credit_code,
            timeout_ms=4_000,
            poll_interval_ms=2_000,
        )
        current_page_result = dict(current_page_result_wait.get("result") or {})
        if current_page_result.get("ok"):
            text_payload = json.dumps(current_page_result.get("normalized") or current_page_result, ensure_ascii=False, indent=2)
            saved_result = await self._write_creditchina_result_files_async(
                page,
                artifact_dir,
                credit_code=normalized_credit_code,
                succeeded=True,
                stage="dom_result_saved",
                base_name=result_name,
                result_payload={
                    "page_result": current_page_result,
                    "page_result_wait": current_page_result_wait,
                    "mode": "dom_session_reuse_current_page",
                },
                text_payload=text_payload,
            )
            return {
                "ok": True,
                "credit_code": normalized_credit_code,
                "stage": "dom_result_saved",
                "saved_result": saved_result,
            }

        prepare = await self._prepare_creditchina_query_page_async(page, credit_code=normalized_credit_code)
        self._push_debug_event(
            "creditchina_prepare",
            {
                "credit_code": normalized_credit_code,
                "used_home": prepare.get("used_home"),
                "step_count": len(prepare.get("steps") or []),
                "mode": "private_api",
            },
        )

        existing_result_wait = await self._wait_for_creditchina_dom_result_async(
            page,
            credit_code=normalized_credit_code,
            timeout_ms=4_000,
            poll_interval_ms=2_000,
        )
        existing_result = dict(existing_result_wait.get("result") or {})
        if existing_result.get("ok"):
            text_payload = json.dumps(existing_result.get("normalized") or existing_result, ensure_ascii=False, indent=2)
            saved_result = await self._write_creditchina_result_files_async(
                page,
                artifact_dir,
                credit_code=normalized_credit_code,
                succeeded=True,
                stage="dom_result_saved",
                base_name=result_name,
                result_payload={
                    "prepare": prepare,
                    "page_result": existing_result,
                    "page_result_wait": existing_result_wait,
                    "mode": "dom_session_reuse",
                },
                text_payload=text_payload,
            )
            return {
                "ok": True,
                "credit_code": normalized_credit_code,
                "stage": "dom_result_saved",
                "prepare": prepare,
                "saved_result": saved_result,
            }

        diagnosis = await self._diagnose_access_async(page)
        if diagnosis.get("challenge_detected") or diagnosis.get("blank_page_like"):
            retry_payload = await self._retry_on_access_challenge_async(
                page,
                artifact_dir,
                wait_seconds=4,
                max_retries=2,
                capture_artifacts=True,
            )
            final_diagnosis = retry_payload.get("final_diagnosis") or diagnosis
            existing_result_wait = await self._wait_for_creditchina_dom_result_async(
                page,
                credit_code=normalized_credit_code,
                timeout_ms=8_000,
                poll_interval_ms=2_000,
            )
            existing_result = dict(existing_result_wait.get("result") or {})
            if existing_result.get("ok"):
                text_payload = json.dumps(existing_result.get("normalized") or existing_result, ensure_ascii=False, indent=2)
                saved_result = await self._write_creditchina_result_files_async(
                    page,
                    artifact_dir,
                    credit_code=normalized_credit_code,
                    succeeded=True,
                    stage="dom_result_saved",
                    base_name=result_name,
                    result_payload={
                        "prepare": prepare,
                        "retry": retry_payload,
                        "page_result": existing_result,
                        "page_result_wait": existing_result_wait,
                        "mode": "dom_session_reuse",
                    },
                    text_payload=text_payload,
                )
                return {
                    "ok": True,
                    "credit_code": normalized_credit_code,
                    "stage": "dom_result_saved",
                    "prepare": prepare,
                    "retry": retry_payload,
                    "saved_result": saved_result,
                }
            if final_diagnosis.get("challenge_detected") or final_diagnosis.get("blank_page_like"):
                saved_result = None
                if save_failure_result:
                    saved_result = await self._write_creditchina_result_files_async(
                        page,
                        artifact_dir,
                        credit_code=normalized_credit_code,
                        succeeded=False,
                        stage="access_failed",
                        error_message="命中安全挑战页，未进入可查询页面。",
                        base_name=result_name,
                        result_payload={"prepare": prepare, "retry": retry_payload, "mode": "private_api"},
                    )
                return {
                    "ok": False,
                    "credit_code": normalized_credit_code,
                    "stage": "access_failed",
                    "prepare": prepare,
                    "retry": retry_payload,
                    "saved_result": saved_result,
                }

        api_flow = await self._run_creditchina_private_api_query_async(
            page,
            artifact_dir,
            credit_code=normalized_credit_code,
            max_captcha_attempts=max_captcha_attempts,
        )
        if not api_flow.get("ok"):
            saved_result = None
            if save_failure_result:
                saved_result = await self._write_creditchina_result_files_async(
                    page,
                    artifact_dir,
                    credit_code=normalized_credit_code,
                    succeeded=False,
                    stage=str(api_flow.get("stage") or "private_api_failed"),
                    error_message=str(api_flow.get("error") or "private-api 查询失败。"),
                    base_name=result_name,
                    result_payload={"prepare": prepare, "api_flow": api_flow, "mode": "private_api"},
                    text_payload=json.dumps(api_flow, ensure_ascii=False, indent=2),
                )
            return {
                "ok": False,
                "credit_code": normalized_credit_code,
                "stage": str(api_flow.get("stage") or "private_api_failed"),
                "prepare": prepare,
                "api_flow": api_flow,
                "saved_result": saved_result,
            }

        text_payload = json.dumps(api_flow.get("normalized") or api_flow, ensure_ascii=False, indent=2)
        saved_result = await self._write_creditchina_result_files_async(
            page,
            artifact_dir,
            credit_code=normalized_credit_code,
            succeeded=True,
            stage="api_result_saved",
            base_name=result_name,
            result_payload={"prepare": prepare, "api_flow": api_flow, "mode": "private_api"},
            text_payload=text_payload,
        )
        return {
            "ok": True,
            "credit_code": normalized_credit_code,
            "stage": "api_result_saved",
            "prepare": prepare,
            "api_flow": api_flow,
            "saved_result": saved_result,
        }

    async def run_creditchina_query_and_save_async(
        self,
        page: AsyncPage,
        artifact_dir: Path,
        *,
        credit_code: str,
        result_name: str = "",
        max_captcha_attempts: int = 6,
    ) -> dict[str, Any]:
        normalized_credit_code = str(credit_code or "").strip()
        if not normalized_credit_code:
            raise RuntimeError("统一社会信用代码不能为空。")

        private_api_attempt = await self.run_creditchina_private_api_flow_and_save_async(
            page,
            artifact_dir,
            credit_code=normalized_credit_code,
            result_name=result_name,
            max_captcha_attempts=max_captcha_attempts,
            save_failure_result=False,
        )
        if private_api_attempt.get("ok"):
            return private_api_attempt
        if private_api_attempt.get("stage") == "access_failed":
            existing_result_wait = await self._wait_for_creditchina_dom_result_async(
                page,
                credit_code=normalized_credit_code,
                timeout_ms=8_000,
                poll_interval_ms=2_000,
            )
            existing_result = dict(existing_result_wait.get("result") or {})
            if existing_result.get("ok"):
                saved = await self._write_creditchina_result_files_async(
                    page,
                    artifact_dir,
                    credit_code=normalized_credit_code,
                    succeeded=True,
                    stage="dom_result_saved",
                    base_name=result_name,
                    result_payload={
                        "api_flow": private_api_attempt,
                        "page_result": existing_result,
                        "page_result_wait": existing_result_wait,
                        "mode": "dom_session_reuse",
                    },
                    text_payload=json.dumps(existing_result.get("normalized") or existing_result, ensure_ascii=False, indent=2),
                )
                return {
                    "ok": True,
                    "credit_code": normalized_credit_code,
                    "api_flow": private_api_attempt,
                    "saved_result": saved,
                }
            saved = await self._write_creditchina_result_files_async(
                page,
                artifact_dir,
                credit_code=normalized_credit_code,
                succeeded=False,
                stage="access_failed",
                error_message="命中安全挑战页，未进入可查询页面。",
                base_name=result_name,
                result_payload=private_api_attempt,
                text_payload=json.dumps(private_api_attempt, ensure_ascii=False, indent=2),
            )
            return {
                **private_api_attempt,
                "saved_result": saved,
            }

        self._push_debug_event(
            "creditchina_api_fallback",
            {
                "credit_code": normalized_credit_code,
                "stage": private_api_attempt.get("stage"),
                "error": private_api_attempt.get("api_flow", {}).get("error") or private_api_attempt.get("error"),
            },
        )

        prepare = await self._prepare_creditchina_query_page_async(page, credit_code=normalized_credit_code)
        self._push_debug_event(
            "creditchina_prepare",
            {
                "credit_code": normalized_credit_code,
                "used_home": prepare.get("used_home"),
                "step_count": len(prepare.get("steps") or []),
                "mode": "dom_fallback",
            },
        )

        existing_result_wait = await self._wait_for_creditchina_dom_result_async(
            page,
            credit_code=normalized_credit_code,
            timeout_ms=4_000,
            poll_interval_ms=2_000,
        )
        existing_result = dict(existing_result_wait.get("result") or {})
        if existing_result.get("ok"):
            saved = await self._write_creditchina_result_files_async(
                page,
                artifact_dir,
                credit_code=normalized_credit_code,
                succeeded=True,
                stage="dom_result_saved",
                base_name=result_name,
                result_payload={
                    "api_flow": private_api_attempt,
                    "prepare": prepare,
                    "page_result": existing_result,
                    "page_result_wait": existing_result_wait,
                    "mode": "dom_session_reuse",
                },
                text_payload=json.dumps(existing_result.get("normalized") or existing_result, ensure_ascii=False, indent=2),
            )
            return {
                "ok": True,
                "credit_code": normalized_credit_code,
                "api_flow": private_api_attempt,
                "prepare": prepare,
                "saved_result": saved,
            }

        diagnosis = await self._diagnose_access_async(page)
        if diagnosis.get("challenge_detected") or diagnosis.get("blank_page_like"):
            retry_payload = await self._retry_on_access_challenge_async(
                page,
                artifact_dir,
                wait_seconds=4,
                max_retries=2,
                capture_artifacts=True,
            )
            final_diagnosis = retry_payload.get("final_diagnosis") or diagnosis
            existing_result_wait = await self._wait_for_creditchina_dom_result_async(
                page,
                credit_code=normalized_credit_code,
                timeout_ms=8_000,
                poll_interval_ms=2_000,
            )
            existing_result = dict(existing_result_wait.get("result") or {})
            if existing_result.get("ok"):
                saved = await self._write_creditchina_result_files_async(
                    page,
                    artifact_dir,
                    credit_code=normalized_credit_code,
                    succeeded=True,
                    stage="dom_result_saved",
                    base_name=result_name,
                    result_payload={
                        "api_flow": private_api_attempt,
                        "prepare": prepare,
                        "retry": retry_payload,
                        "page_result": existing_result,
                        "page_result_wait": existing_result_wait,
                        "mode": "dom_session_reuse",
                    },
                    text_payload=json.dumps(existing_result.get("normalized") or existing_result, ensure_ascii=False, indent=2),
                )
                return {
                    "ok": True,
                    "credit_code": normalized_credit_code,
                    "api_flow": private_api_attempt,
                    "prepare": prepare,
                    "retry": retry_payload,
                    "saved_result": saved,
                }
            if final_diagnosis.get("challenge_detected") or final_diagnosis.get("blank_page_like"):
                saved = await self._write_creditchina_result_files_async(
                    page,
                    artifact_dir,
                    credit_code=normalized_credit_code,
                    succeeded=False,
                    stage="access_failed",
                    error_message="命中安全挑战页，未进入可查询页面。",
                    base_name=result_name,
                    result_payload={"api_flow": private_api_attempt, "prepare": prepare, "retry": retry_payload, "mode": "dom_fallback"},
                    text_payload=json.dumps({"api_flow": private_api_attempt, "prepare": prepare, "retry": retry_payload}, ensure_ascii=False, indent=2),
                )
                return {
                    "ok": False,
                    "credit_code": normalized_credit_code,
                    "api_flow": private_api_attempt,
                    "prepare": prepare,
                    "retry": retry_payload,
                    "saved_result": saved,
                }

        captcha_state = await self._solve_creditchina_captcha_async(
            page,
            artifact_dir,
            credit_code=normalized_credit_code,
            max_attempts=max_captcha_attempts,
        )
        if not captcha_state.get("ok"):
            fallback_result_wait = await self._wait_for_creditchina_dom_result_async(
                page,
                credit_code=normalized_credit_code,
                timeout_ms=6_000,
                poll_interval_ms=2_000,
            )
            fallback_result = dict(fallback_result_wait.get("result") or {})
            if fallback_result.get("ok"):
                saved = await self._write_creditchina_result_files_async(
                    page,
                    artifact_dir,
                    credit_code=normalized_credit_code,
                    succeeded=True,
                    stage="dom_result_saved",
                    base_name=result_name,
                    result_payload={
                        "api_flow": private_api_attempt,
                        "prepare": prepare,
                        "captcha": captcha_state,
                        "page_result": fallback_result,
                        "page_result_wait": fallback_result_wait,
                        "mode": "dom_session_reuse",
                    },
                    text_payload=json.dumps(fallback_result.get("normalized") or fallback_result, ensure_ascii=False, indent=2),
                )
                return {
                    "ok": True,
                    "credit_code": normalized_credit_code,
                    "api_flow": private_api_attempt,
                    "prepare": prepare,
                    "captcha": captcha_state,
                    "saved_result": saved,
                }
            saved = await self._write_creditchina_result_files_async(
                page,
                artifact_dir,
                credit_code=normalized_credit_code,
                succeeded=False,
                stage="captcha_failed",
                error_message=str(captcha_state.get("error") or "验证码未通过。"),
                base_name=result_name,
                result_payload={"api_flow": private_api_attempt, "prepare": prepare, "captcha": captcha_state, "mode": "dom_fallback"},
                text_payload=json.dumps({"api_flow": private_api_attempt, "captcha": captcha_state}, ensure_ascii=False, indent=2),
            )
            return {
                "ok": False,
                "credit_code": normalized_credit_code,
                "api_flow": private_api_attempt,
                "prepare": prepare,
                "captcha": captcha_state,
                "saved_result": saved,
            }

        await self._settle_page_async(page, extra_wait_ms=2500)
        saved = await self._write_creditchina_result_files_async(
            page,
            artifact_dir,
            credit_code=normalized_credit_code,
            succeeded=True,
            stage="result_saved",
            base_name=result_name,
            result_payload={
                "api_flow": private_api_attempt,
                "prepare": prepare,
                "captcha": captcha_state,
                "page_result": (captcha_state.get("result_ready") or {}).get("page_result")
                or await self._extract_creditchina_dom_result_async(page, credit_code=normalized_credit_code)
                or await self._try_read_result_json_async(page),
                "mode": "dom_fallback",
            },
        )
        return {
            "ok": True,
            "credit_code": normalized_credit_code,
            "api_flow": private_api_attempt,
            "prepare": prepare,
            "captcha": captcha_state,
            "saved_result": saved,
        }

    def build_async_tools(self, page: AsyncPage, artifact_dir: Path) -> list[Any]:
        @tool
        async def open_start_page() -> str:
            """打开配置好的起始页面。适合每次任务刚开始时调用。"""
            response = await page.goto(self._start_url(), wait_until="domcontentloaded")
            await self._record_navigation_async(page, response, source="open_start_page")
            await self._settle_page_async(page)
            return f"已打开起始页面：{page.url}"

        @tool
        async def open_page(target: str) -> str:
            """打开指定页面。支持绝对 URL，也支持相对路径，比如 ./captcha/ 或 ../result/。"""
            resolved = self._resolve_url(page.url, target)
            response = await page.goto(resolved, wait_until="domcontentloaded")
            await self._record_navigation_async(page, response, source="open_page")
            await self._settle_page_async(page)
            return f"已打开页面：{page.url}"

        @tool
        async def unlock_site_password(password: str = "") -> str:
            """如果目标站出现系统密码页，用已知系统密码解锁到查询页。"""
            password_value = str(password or self.site_password).strip()
            if not password_value:
                raise RuntimeError("当前没有可用的系统密码。")
            password_input = page.locator("#legal-demo-password-input")
            if await password_input.count() == 0:
                return f"当前页面没有系统密码输入框；当前页面：{page.url}"
            await password_input.first.fill(password_value)
            await page.locator("#legal-demo-unlock-button").first.click()
            await page.wait_for_selector("#credit-code-input", timeout=10_000)
            return f"已通过系统密码页；当前页面：{page.url}"

        @tool
        async def inspect_page(max_elements: int = 40) -> str:
            """检查当前页面的 URL、标题、可见文本摘要，以及主要元素的 selector、id、name、text、placeholder 等。"""
            payload = await page.evaluate(
                """
                (maxElements) => {
                  const normalize = (value, maxLen = 120) =>
                    String(value || "").replace(/\\s+/g, " ").trim().slice(0, maxLen);
                  const cssEscape = (value) => {
                    if (window.CSS && typeof window.CSS.escape === "function") {
                      return window.CSS.escape(String(value));
                    }
                    return String(value).replace(/[^a-zA-Z0-9_-]/g, (char) => `\\\\${char}`);
                  };
                  const isVisible = (element) => {
                    if (!(element instanceof HTMLElement)) return false;
                    const style = window.getComputedStyle(element);
                    const rect = element.getBoundingClientRect();
                    return style.display !== "none"
                      && style.visibility !== "hidden"
                      && style.opacity !== "0"
                      && rect.width > 0
                      && rect.height > 0;
                  };
                  const uniqueSelector = (element) => {
                    if (!(element instanceof Element)) return "";
                    if (element.id) {
                      const idSelector = `#${cssEscape(element.id)}`;
                      if (document.querySelectorAll(idSelector).length === 1) {
                        return idSelector;
                      }
                    }
                    const parts = [];
                    let current = element;
                    while (current && current.nodeType === Node.ELEMENT_NODE && current !== document.body) {
                      let part = current.tagName.toLowerCase();
                      if (current.id) {
                        const idSelector = `#${cssEscape(current.id)}`;
                        if (document.querySelectorAll(idSelector).length === 1) {
                          parts.unshift(idSelector);
                          return parts.join(" > ");
                        }
                      }
                      const parent = current.parentElement;
                      if (parent) {
                        const siblings = Array.from(parent.children).filter(
                          (node) => node.tagName === current.tagName
                        );
                        if (siblings.length > 1) {
                          part += `:nth-of-type(${siblings.indexOf(current) + 1})`;
                        }
                      }
                      parts.unshift(part);
                      current = parent;
                    }
                    parts.unshift("body");
                    return parts.join(" > ");
                  };
                  const labelsFor = new Map(
                    Array.from(document.querySelectorAll("label[for]")).map((label) => [
                      label.getAttribute("for"),
                      normalize(label.textContent, 80),
                    ])
                  );
                  const candidates = Array.from(
                    document.querySelectorAll(
                      "input, textarea, button, select, a, img, [role='button'], [contenteditable='true']"
                    )
                  );
                  const elements = [];
                  for (const element of candidates) {
                    if (!isVisible(element)) continue;
                    const selector = uniqueSelector(element);
                    if (!selector) continue;
                    const tag = String(element.tagName || "").toLowerCase();
                    const id = normalize(element.id);
                    const name = normalize(element.getAttribute("name"));
                    const type = normalize(element.getAttribute("type"));
                    const role = normalize(element.getAttribute("role"));
                    const text = normalize(element.innerText || element.textContent, 120);
                    const placeholder = normalize(element.getAttribute("placeholder"));
                    const ariaLabel = normalize(element.getAttribute("aria-label"));
                    const href = normalize(element.getAttribute("href"), 200);
                    const src = normalize(element.getAttribute("src"), 200);
                    const labelText = labelsFor.get(element.id) || "";
                    elements.push({
                      tag,
                      id,
                      name,
                      type,
                      role,
                      text,
                      placeholder,
                      aria_label: ariaLabel,
                      label: labelText || "",
                      selector,
                      href,
                      src,
                    });
                    if (elements.length >= maxElements) break;
                  }
                  return {
                    url: window.location.href,
                    title: document.title,
                    visible_text_excerpt: normalize(document.body.innerText, 1600),
                    visible_text_length: normalize(document.body.innerText, 5000).length,
                    interactive_count: elements.length,
                    html_length: document.documentElement ? document.documentElement.outerHTML.length : 0,
                    body_html_length: document.body ? document.body.innerHTML.length : 0,
                    script_count: document.querySelectorAll("script").length,
                    iframe_count: document.querySelectorAll("iframe").length,
                    has_password_gate: Boolean(document.querySelector("#legal-demo-password-input")),
                    has_result_json: Boolean(document.querySelector("#result-json")),
                    elements,
                  };
                }
                """,
                max_elements,
            )
            return json.dumps(payload, ensure_ascii=False, indent=2)

        @tool
        async def read_text(selector: str = "body", max_chars: int = 2000) -> str:
            """读取指定 selector 的可见文本内容。适合查看说明文案、错误提示或结果文本。"""
            text = await page.locator(selector).first.inner_text(timeout=10_000)
            return text[:max_chars]

        @tool
        async def read_html(selector: str = "body", max_chars: int = 4000) -> str:
            """读取指定 selector 的 outerHTML 片段。适合需要看原始 DOM 结构时使用。"""
            html = await page.locator(selector).first.evaluate("(node) => node.outerHTML")
            return str(html or "")[:max_chars]

        @tool
        async def wait_for_seconds(seconds: float = 3, wait_for_network_idle: bool = True) -> str:
            """等待指定秒数，再返回当前页面状态。适合脚本延迟渲染、重定向或加载过慢的网站。"""
            wait_ms = max(0, min(int(float(seconds) * 1000), 20_000))
            if wait_ms:
                await page.wait_for_timeout(wait_ms)
            if wait_for_network_idle:
                try:
                    await page.wait_for_load_state("networkidle", timeout=min(max(wait_ms, 2_000), 8_000))
                except Exception:
                    pass
            diagnosis = await self._diagnose_access_async(page)
            self._push_debug_event(
                "wait",
                {
                    "seconds": round(wait_ms / 1000, 3),
                    "url": page.url,
                    "challenge_detected": diagnosis.get("challenge_detected"),
                    "interactive_count": diagnosis.get("interactive_count"),
                },
            )
            return json.dumps(
                {
                    "waited_seconds": round(wait_ms / 1000, 3),
                    "url": page.url,
                    "diagnosis": diagnosis,
                },
                ensure_ascii=False,
            )

        @tool
        async def capture_page_artifacts(
            stem: str = "page-debug",
            html_preview_chars: int = 4000,
        ) -> str:
            """保存当前页完整 HTML 和整页截图到 artifacts 目录，并返回路径与预览。"""
            payload = await self._save_page_artifacts_async(
                page,
                artifact_dir,
                stem=stem,
                html_preview_chars=max(500, min(int(html_preview_chars), 20_000)),
            )
            diagnosis = await self._diagnose_access_async(page)
            return json.dumps(
                {
                    **payload,
                    "diagnosis": diagnosis,
                },
                ensure_ascii=False,
            )

        @tool
        async def detect_access_challenge(max_html_chars: int = 4000, max_body_chars: int = 1200) -> str:
            """检测当前页是否像反爬挑战页、空白脚本壳页或异常状态页，并给出诊断。"""
            diagnosis = await self._diagnose_access_async(
                page,
                max_html_chars=max(500, min(int(max_html_chars), 20_000)),
                max_body_chars=max(200, min(int(max_body_chars), 5_000)),
            )
            self._push_debug_event(
                "challenge_diagnosis",
                {
                    "url": diagnosis.get("url"),
                    "status": diagnosis.get("status"),
                    "markers": diagnosis.get("markers"),
                    "challenge_detected": diagnosis.get("challenge_detected"),
                },
            )
            return json.dumps(diagnosis, ensure_ascii=False)

        @tool
        async def retry_on_access_challenge(
            wait_seconds: float = 4,
            max_retries: int = 2,
            capture_artifacts: bool = True,
        ) -> str:
            """若当前页像挑战页或空白壳页，则等待并重载重试；可选保存 HTML 和整页截图。"""
            payload = await self._retry_on_access_challenge_async(
                page,
                artifact_dir,
                wait_seconds=wait_seconds,
                max_retries=max_retries,
                capture_artifacts=capture_artifacts,
            )
            return json.dumps(payload, ensure_ascii=False)

        @tool
        async def run_creditchina_private_api_query_and_save(
            credit_code: str,
            result_name: str = "",
            max_captcha_attempts: int = 6,
        ) -> str:
            """执行“信用中国”private-api 查询流程：挑战续跑、验证码 API、搜索结果 API、详情 API，并把最终结果保存到文件。"""
            payload = await self.run_creditchina_private_api_flow_and_save_async(
                page,
                artifact_dir,
                credit_code=credit_code,
                result_name=result_name,
                max_captcha_attempts=max_captcha_attempts,
                save_failure_result=True,
            )
            return json.dumps(payload, ensure_ascii=False)

        @tool
        async def run_creditchina_query_and_save(
            credit_code: str,
            result_name: str = "",
            max_captcha_attempts: int = 6,
        ) -> str:
            """执行“信用中国”固定查询流程：优先尝试 private-api 查询，若失败再回退到原 DOM+验证码流程，并把最终结果保存到文件。"""
            payload = await self.run_creditchina_query_and_save_async(
                page,
                artifact_dir,
                credit_code=credit_code,
                result_name=result_name,
                max_captcha_attempts=max_captcha_attempts,
            )
            return json.dumps(payload, ensure_ascii=False)

        @tool
        async def fill_input(selector: str, value: str) -> str:
            """向指定 selector 对应的输入元素填写文本。selector 可以来自 inspect_page 返回值。"""
            await page.locator(selector).first.fill(value)
            return f"已填写 {selector} = {value}"

        @tool
        async def click_element(selector: str) -> str:
            """点击指定 selector 对应的元素。适合按钮、链接、提交动作。"""
            await page.locator(selector).first.click()
            return f"已点击 {selector}；当前页面：{page.url}"

        @tool
        async def wait_for_selector(selector: str, state: str = "visible", timeout_ms: int = 10_000) -> str:
            """等待某个 selector 进入指定状态。适合提交后等待页面变化。state 可用 visible/attached/hidden/detached。"""
            await page.wait_for_selector(selector, state=state, timeout=timeout_ms)
            return f"{selector} 已达到状态 {state}"

        @tool
        async def capture_element_screenshot(selector: str, filename: str = "element.png") -> str:
            """对指定元素截图并保存到 artifacts 目录。适合验证码、结果卡片或调试。"""
            output_path = artifact_dir / _sanitize_filename(filename)
            await page.locator(selector).first.screenshot(path=str(output_path))
            return str(output_path)

        @tool
        async def solve_captcha_from_file(image_path: str) -> str:
            """识别本地验证码图片文件，返回猜测结果。"""
            return solve_captcha_file(image_path)

        @tool
        async def solve_captcha_from_selector(selector: str, filename: str = "captcha.png") -> str:
            """先截取指定 selector 对应的验证码元素，再做本地识别。"""
            output_path = artifact_dir / _sanitize_filename(filename, default_name="captcha.png")
            await page.locator(selector).first.screenshot(path=str(output_path))
            guess = solve_captcha_file(output_path)
            return json.dumps(
                {
                    "image_path": str(output_path),
                    "captcha_guess": guess,
                },
                ensure_ascii=False,
            )

        @tool
        async def solve_captcha_and_submit(
            image_selector: str = "#captcha-image",
            input_selector: str = "#captcha-input",
            submit_selector: str = "#captcha-confirm",
            max_attempts: int = 6,
            success_selector: str = "#result-json",
            error_keyword: str = "验证码错误",
        ) -> str:
            """自动循环处理验证码：截图识别、填写、提交；若失败会重新截取新验证码继续尝试，直到成功或达到最大次数。"""
            result = await self._solve_and_submit_captcha_async(
                page,
                artifact_dir,
                image_selector=image_selector,
                input_selector=input_selector,
                submit_selector=submit_selector,
                success_selector=success_selector,
                error_keyword=error_keyword,
                max_attempts=max_attempts,
            )
            return json.dumps(result, ensure_ascii=False)

        @tool
        async def read_result_json() -> str:
            """读取结果页里的 #result-json，并按 JSON 文本返回。"""
            payload = await self._try_read_result_json_async(page)
            return json.dumps(payload or {}, ensure_ascii=False, indent=2)

        return [
            open_start_page,
            open_page,
            unlock_site_password,
            inspect_page,
            read_text,
            read_html,
            wait_for_seconds,
            capture_page_artifacts,
            detect_access_challenge,
            retry_on_access_challenge,
            run_creditchina_private_api_query_and_save,
            run_creditchina_query_and_save,
            fill_input,
            click_element,
            wait_for_selector,
            capture_element_screenshot,
            solve_captcha_from_file,
            solve_captcha_from_selector,
            solve_captcha_and_submit,
            read_result_json,
        ]
