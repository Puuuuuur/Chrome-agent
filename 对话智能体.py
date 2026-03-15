from __future__ import annotations

import json

from agent工具 import playwright_agent_is_ready, runtime_metadata
from 智能体调度 import (
    DEFAULT_API_BASE_URL,
    DEFAULT_BASE_URL,
    DEFAULT_BROWSER_MODE,
    DEFAULT_CDP_ATTACH_EXISTING_PAGE,
    DEFAULT_CDP_URL,
    DEFAULT_CREDIT_CODE,
    DEFAULT_MAX_STEPS,
    DEFAULT_MODEL,
    DEFAULT_SITE_PASSWORD,
    WELCOME_MESSAGE,
    invoke_creditchina_query,
    invoke_playwright_agent,
)

# 这个文件只负责页面渲染。
# 真正的单 agent 调度与工具调用都在 `智能体调度.py`。

__all__ = [
    "invoke_creditchina_query",
    "invoke_playwright_agent",
    "playwright_agent_is_ready",
    "render_playwright_agent_page",
    "runtime_metadata",
]


def render_playwright_agent_page(embedded: bool = False) -> str:
    ready, ready_error = playwright_agent_is_ready()
    metadata = runtime_metadata()
    config_json = json.dumps(
        {
            "ready": ready,
            "readyError": ready_error,
            "defaultBaseUrl": DEFAULT_BASE_URL,
            "defaultCreditCode": DEFAULT_CREDIT_CODE,
            "defaultSitePassword": DEFAULT_SITE_PASSWORD,
            "defaultModel": metadata.get("model") or DEFAULT_MODEL,
            "defaultApiBaseUrl": metadata.get("api_base_url") or DEFAULT_API_BASE_URL,
            "defaultMaxSteps": DEFAULT_MAX_STEPS,
            "defaultBrowserMode": metadata.get("browser_mode") or DEFAULT_BROWSER_MODE,
            "defaultCdpUrl": metadata.get("cdp_url") or DEFAULT_CDP_URL,
            "defaultCdpAttachExistingPage": (
                str(metadata.get("cdp_attach_existing_page") or "").strip() not in {"", "0", "false", "off", "no"}
                if metadata.get("cdp_attach_existing_page") is not None
                else DEFAULT_CDP_ATTACH_EXISTING_PAGE
            ),
            "defaultStorageStatePath": "",
            "defaultCookiesPath": "",
            "defaultCookieHeaderPath": "",
            "defaultInvalidMarkerPath": "",
            "sessionDir": metadata.get("session_dir") or "",
            "welcomeMessage": WELCOME_MESSAGE,
        },
        ensure_ascii=False,
    ).replace("</", "<\\/")
    body_class = "embedded" if embedded else "standalone"
    ready_copy = "已就绪" if ready else "未就绪"
    ready_class = "ready" if ready else "not-ready"

    return f"""<!doctype html>
<html lang='zh-CN'>
<head>
  <meta charset='utf-8'>
  <meta name='viewport' content='width=device-width, initial-scale=1'>
  <title>法务浏览器 Agent</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f6f5f2;
      --panel: rgba(255, 255, 255, 0.94);
      --panel-strong: #ffffff;
      --line: rgba(36, 44, 55, 0.10);
      --text: #14212b;
      --muted: #69757f;
      --brand: #43586a;
      --brand-soft: rgba(67, 88, 106, 0.10);
      --accent: #a28b67;
      --ok: #1f8f63;
      --ok-soft: rgba(31, 143, 99, 0.12);
      --danger: #b42318;
      --danger-soft: rgba(180, 35, 24, 0.10);
      --shadow: 0 20px 42px rgba(29, 37, 45, 0.08);
    }}

    * {{
      box-sizing: border-box;
    }}

    html, body {{
      margin: 0;
      height: 100%;
      min-height: 100%;
      background:
        radial-gradient(860px 360px at 0% 0%, rgba(67, 88, 106, 0.08), transparent 58%),
        radial-gradient(760px 340px at 100% 100%, rgba(162, 139, 103, 0.08), transparent 52%),
        var(--bg);
      color: var(--text);
      overflow: hidden;
      font-family:
        Inter,
        -apple-system,
        BlinkMacSystemFont,
        "Segoe UI",
        "PingFang SC",
        "Hiragino Sans GB",
        "Microsoft YaHei",
        sans-serif;
    }}

    body {{
      padding: 0;
    }}

    button, textarea {{
      font: inherit;
    }}

    .page {{
      width: 100%;
      height: 100dvh;
      margin: 0;
      overflow: hidden;
      background: var(--panel-strong);
      display: grid;
      grid-template-rows: auto minmax(0, 1fr) auto;
    }}

    .topbar {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      padding: 14px 18px;
      border-bottom: 1px solid rgba(36, 44, 55, 0.08);
      background:
        linear-gradient(180deg, rgba(255, 255, 255, 0.94), rgba(247, 245, 240, 0.88));
    }}

    .topbar-title {{
      display: grid;
      gap: 4px;
      min-width: 0;
    }}

    .eyebrow {{
      font-size: 12px;
      line-height: 1.4;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      color: var(--accent);
      font-weight: 700;
    }}

    .title {{
      font-size: 18px;
      line-height: 1.4;
      font-weight: 700;
      color: var(--text);
    }}

    .subtitle {{
      font-size: 13px;
      line-height: 1.5;
      color: var(--muted);
    }}

    .status-chip {{
      display: inline-flex;
      align-items: center;
      gap: 8px;
      min-height: 34px;
      padding: 6px 12px;
      border-radius: 999px;
      font-size: 13px;
      line-height: 1.4;
      font-weight: 700;
      width: fit-content;
      white-space: nowrap;
      flex-shrink: 0;
    }}

    .status-chip.ready {{
      background: var(--ok-soft);
      color: var(--ok);
      border: 1px solid rgba(31, 143, 99, 0.14);
    }}

    .status-chip.not-ready {{
      background: var(--danger-soft);
      color: var(--danger);
      border: 1px solid rgba(180, 35, 24, 0.14);
    }}

    .chat-feed {{
      padding: 28px 20px 20px;
      display: grid;
      align-content: start;
      gap: 16px;
      min-height: 0;
      overflow: auto;
      background:
        radial-gradient(520px 240px at 100% 0%, rgba(67, 88, 106, 0.05), transparent 58%),
        linear-gradient(180deg, rgba(255, 255, 255, 0.76), rgba(249, 247, 243, 0.72));
    }}

    .message {{
      display: grid;
      gap: 8px;
      max-width: min(92%, 980px);
    }}

    .message.user {{
      justify-self: end;
    }}

    .message.assistant {{
      justify-self: start;
    }}

    .bubble {{
      padding: 14px 16px;
      border-radius: 18px;
      border: 1px solid rgba(36, 44, 55, 0.08);
      background: rgba(255, 255, 255, 0.92);
      color: var(--text);
      white-space: pre-wrap;
      word-break: break-word;
      line-height: 1.7;
      box-shadow: 0 12px 22px rgba(31, 39, 47, 0.05);
    }}

    .message.user .bubble {{
      background: linear-gradient(135deg, #455d70, #5b7487);
      color: #fff;
      border-color: transparent;
    }}

    .tool-row {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      padding: 0 4px;
    }}

    .tool-chip {{
      display: inline-flex;
      align-items: center;
      min-height: 28px;
      padding: 4px 10px;
      border-radius: 999px;
      font-size: 12px;
      color: var(--brand);
      background: var(--brand-soft);
      border: 1px solid rgba(67, 88, 106, 0.12);
    }}

    .typing {{
      display: inline-flex;
      align-items: center;
      gap: 8px;
      color: var(--muted);
      font-size: 14px;
      padding: 0 24px 10px;
    }}

    .typing[hidden] {{
      display: none;
    }}

    .typing-dot {{
      width: 7px;
      height: 7px;
      border-radius: 999px;
      background: currentColor;
      opacity: 0.34;
      animation: pulse 1.2s ease-in-out infinite;
    }}

    .typing-dot:nth-child(2) {{
      animation-delay: 120ms;
    }}

    .typing-dot:nth-child(3) {{
      animation-delay: 240ms;
    }}

    @keyframes pulse {{
      0%, 80%, 100% {{ transform: scale(0.9); opacity: 0.24; }}
      40% {{ transform: scale(1.15); opacity: 0.8; }}
    }}

    .composer {{
      padding: 16px 20px 20px;
      border-top: 1px solid rgba(36, 44, 55, 0.08);
      background: linear-gradient(180deg, rgba(255, 255, 255, 0.96), rgba(248, 247, 244, 0.94));
      display: grid;
      gap: 12px;
    }}

    .composer-box {{
      display: grid;
      gap: 12px;
      padding: 14px;
      width: min(1120px, 100%);
      margin: 0 auto;
      border-radius: 18px;
      border: 1px solid rgba(67, 88, 106, 0.12);
      background: var(--panel-strong);
      box-shadow: 0 10px 24px rgba(31, 39, 47, 0.05);
    }}

    .composer-box:focus-within {{
      border-color: rgba(67, 88, 106, 0.22);
      box-shadow:
        0 0 0 3px rgba(67, 88, 106, 0.08),
        0 10px 24px rgba(31, 39, 47, 0.05);
    }}

    .prompt-row {{
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
    }}

    .prompt-chip {{
      display: inline-flex;
      align-items: center;
      min-height: 40px;
      padding: 8px 14px;
      border-radius: 999px;
      border: 1px solid rgba(67, 88, 106, 0.12);
      background: rgba(255, 255, 255, 0.76);
      color: #314552;
      font-size: 14px;
      line-height: 1.4;
      cursor: pointer;
      transition:
        transform 180ms ease,
        border-color 180ms ease,
        box-shadow 180ms ease,
        background-color 180ms ease;
    }}

    .prompt-chip:hover {{
      transform: translateY(-1px);
      border-color: rgba(67, 88, 106, 0.24);
      background: rgba(255, 255, 255, 0.94);
      box-shadow: 0 10px 18px rgba(33, 44, 55, 0.06);
    }}

    .prompt-chip:focus-visible,
    .composer-submit:focus-visible {{
      outline: 2px solid rgba(67, 88, 106, 0.34);
      outline-offset: 2px;
    }}

    .composer-textarea {{
      width: 100%;
      min-height: 24px;
      max-height: 220px;
      border: 0;
      resize: none;
      outline: none;
      padding: 0;
      margin: 0;
      background: transparent;
      color: var(--text);
      line-height: 1.75;
      font-size: 15px;
    }}

    .composer-textarea::placeholder {{
      color: #8b96a0;
    }}

    .composer-actions {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      flex-wrap: wrap;
    }}

    .composer-hint {{
      color: var(--muted);
      font-size: 13px;
      line-height: 1.6;
    }}

    .composer-submit {{
      min-width: 108px;
      min-height: 46px;
      border: 0;
      border-radius: 14px;
      background: linear-gradient(135deg, #43586a, #607587);
      color: #fff;
      font-weight: 700;
      cursor: pointer;
      transition: transform 180ms ease, box-shadow 180ms ease, opacity 180ms ease;
      box-shadow: 0 14px 28px rgba(67, 88, 106, 0.22);
    }}

    .composer-submit:hover {{
      transform: translateY(-1px);
    }}

    .composer-submit:disabled {{
      opacity: 0.55;
      cursor: not-allowed;
      transform: none;
      box-shadow: none;
    }}

    @media (max-width: 820px) {{
      .topbar {{
        padding: 12px 14px;
        align-items: flex-start;
      }}

      .chat-feed {{
        padding: 20px 14px 16px;
      }}

      .typing {{
        padding: 0 16px 8px;
      }}

      .composer {{
        padding: 14px 14px 16px;
      }}

      .composer-textarea {{
        min-height: 104px;
      }}
    }}
  </style>
</head>
<body class='{body_class}'>
  <div class='page'>
    <section class='topbar'>
      <div class='topbar-title'>
        <div class='eyebrow'>法务测试 / 浏览器 Agent</div>
        <div class='title'>Playwright 对话面板</div>
        <div class='subtitle'>这里只保留聊天入口；需要时会自动调用 Playwright 工具，并可处理空白页/挑战页诊断。</div>
      </div>
      <div class='status-chip {ready_class}'>{ready_copy}</div>
    </section>

    <section id='messages' class='chat-feed'></section>
    <div id='typing' class='typing' hidden>
      <span class='typing-dot'></span>
      <span class='typing-dot'></span>
      <span class='typing-dot'></span>
      <span>浏览器 Agent 正在思考或操作页面…</span>
    </div>

    <form id='composer-form' class='composer'>
      <div class='composer-box'>
        <div class='prompt-row'>
          <button type='button' class='prompt-chip' data-prompt='帮我完成一次完整的信用查询，并告诉我企业名称。'>跑完整查询</button>
          <button type='button' class='prompt-chip' data-prompt='进入 https://www.creditchina.gov.cn/ ，输入统一社会信用代码 91420000177570439L，点击搜索，处理图形验证码，并把最终信用信息查询结果保存到文件。'>信用中国固定查询</button>
          <button type='button' class='prompt-chip' data-prompt='先观察当前页面，列出主要输入框、按钮、图片和它们的 selector。'>看页面 selector</button>
          <button type='button' class='prompt-chip' data-prompt='如果页面结构变了，请你自己重新观察 DOM，再规划下一步操作。'>让它自己规划</button>
          <button type='button' class='prompt-chip' data-prompt='如果页面是空白页或可见元素为空，请继续等待、判断是否是挑战页，并保存完整 HTML 和整页截图给我。'>排查空白页</button>
        </div>
        <textarea id='composer-input' class='composer-textarea' placeholder='直接输入你的问题，比如：先观察页面结构，再完成一次查询。'></textarea>
        <div class='composer-actions'>
          <div class='composer-hint'>Enter 发送，Shift + Enter 换行。当前后端仍然是浏览器 Agent。</div>
          <button id='send-btn' type='submit' class='composer-submit'>发送</button>
        </div>
      </div>
    </form>
  </div>

  <script>
    const pageConfig = {config_json};
    const messagesEl = document.getElementById('messages');
    const typingEl = document.getElementById('typing');
    const composerForm = document.getElementById('composer-form');
    const composerInput = document.getElementById('composer-input');
    const sendBtn = document.getElementById('send-btn');
    let inFlight = false;

    function escapeHtml(value) {{
      return String(value || '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
    }}

    function appendMessage(role, content, meta) {{
      const wrapper = document.createElement('div');
      wrapper.className = 'message ' + role;

      const bubble = document.createElement('div');
      bubble.className = 'bubble';
      bubble.innerHTML = '<div>' + escapeHtml(content || '') + '</div>';
      wrapper.appendChild(bubble);

      if (meta && role === 'assistant') {{
        const usedTools = Array.isArray(meta.used_tools) ? meta.used_tools : [];
        if (usedTools.length) {{
          const toolRow = document.createElement('div');
          toolRow.className = 'tool-row';
          toolRow.innerHTML = usedTools.map((item) => (
            "<span class='tool-chip'>" + escapeHtml(item) + "</span>"
          )).join('');
          wrapper.appendChild(toolRow);
        }}
        if (meta.page_result || meta.agent_debug) {{
          console.debug('[playwright-agent]', meta);
        }}
      }}

      messagesEl.appendChild(wrapper);
      messagesEl.scrollTop = messagesEl.scrollHeight;
    }}

    function syncComposerHeight() {{
      composerInput.style.height = '0px';
      const nextHeight = Math.max(24, Math.min(composerInput.scrollHeight, 220));
      composerInput.style.height = nextHeight + 'px';
    }}

    function setBusy(nextBusy) {{
      inFlight = Boolean(nextBusy);
      composerInput.disabled = inFlight || !pageConfig.ready;
      sendBtn.disabled = inFlight || !pageConfig.ready;
      typingEl.hidden = !inFlight;
    }}

    function buildChatApiUrl() {{
      const currentUrl = new URL(window.location.href);
      currentUrl.search = '';
      currentUrl.hash = '';
      if (!currentUrl.pathname.endsWith('/')) {{
        currentUrl.pathname += '/';
      }}
      return new URL('../api/playwright-agent/chat', currentUrl).toString();
    }}

    async function sendMessage(rawText) {{
      const text = String(rawText || '').trim();
      if (!text || inFlight) {{
        return;
      }}
      if (!pageConfig.ready) {{
        appendMessage('assistant', '当前运行环境未就绪：' + String(pageConfig.readyError || '未知错误'), null);
        return;
      }}

      appendMessage('user', text, null);
      composerInput.value = '';
      syncComposerHeight();
      setBusy(true);

      try {{
        const response = await fetch(buildChatApiUrl(), {{
          method: 'POST',
          headers: {{
            'Content-Type': 'application/json',
            'Accept': 'application/json',
          }},
          body: JSON.stringify({{
            message: text,
            base_url: String(pageConfig.defaultBaseUrl || '').trim(),
            credit_code: String(pageConfig.defaultCreditCode || '').trim(),
            site_password: String(pageConfig.defaultSitePassword || '').trim(),
            mode: 'react',
            model: String(pageConfig.defaultModel || '').trim(),
            api_base_url: String(pageConfig.defaultApiBaseUrl || '').trim(),
            max_steps: Number(pageConfig.defaultMaxSteps || 30),
            browser_mode: String(pageConfig.defaultBrowserMode || '').trim(),
            cdp_url: String(pageConfig.defaultCdpUrl || '').trim(),
            cdp_attach_existing_page: Boolean(pageConfig.defaultCdpAttachExistingPage),
            storage_state_path: String(pageConfig.defaultStorageStatePath || '').trim(),
            cookies_path: String(pageConfig.defaultCookiesPath || '').trim(),
            cookie_header_path: String(pageConfig.defaultCookieHeaderPath || '').trim(),
            invalid_marker_path: String(pageConfig.defaultInvalidMarkerPath || '').trim(),
            persist_session: true,
          }}),
        }});

        const rawText = await response.text();
        let payload = null;
        try {{
          payload = JSON.parse(rawText);
        }} catch (_error) {{
          throw new Error(
            '接口返回的不是 JSON，通常说明请求打到了错误地址或上游返回了 HTML 页面：'
            + String(rawText || '').slice(0, 160)
          );
        }}
        if (!response.ok || !payload.ok) {{
          throw new Error(String((payload && payload.error) || ('HTTP ' + response.status)));
        }}

        const result = payload.result || {{}};
        appendMessage('assistant', String(result.reply || '已完成，但没有返回文字总结。'), result);
      }} catch (error) {{
        appendMessage('assistant', '这次调用失败了：' + String(error && error.message ? error.message : error), null);
      }} finally {{
        setBusy(false);
        syncComposerHeight();
        composerInput.focus();
      }}
    }}

    document.querySelectorAll('[data-prompt]').forEach((button) => {{
      button.addEventListener('click', () => {{
        composerInput.value = String(button.dataset.prompt || '');
        syncComposerHeight();
        composerInput.focus();
      }});
    }});

    composerForm.addEventListener('submit', (event) => {{
      event.preventDefault();
      sendMessage(composerInput.value);
    }});

    composerInput.addEventListener('keydown', (event) => {{
      if (event.key === 'Enter' && !event.shiftKey) {{
        event.preventDefault();
        composerForm.requestSubmit();
      }}
    }});

    composerInput.addEventListener('input', () => {{
      syncComposerHeight();
    }});

    appendMessage('assistant', pageConfig.welcomeMessage, null);
    if (!pageConfig.ready) {{
      appendMessage('assistant', '当前运行环境未就绪：' + String(pageConfig.readyError || '未知错误'), null);
    }}

    setBusy(false);
    syncComposerHeight();
    composerInput.focus();
  </script>
</body>
</html>"""
