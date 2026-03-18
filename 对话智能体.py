"""内置聊天页面渲染器。

这个文件只负责把后端运行状态和聊天前端拼成一个可直接打开的 HTML 页面。
"""

from __future__ import annotations

import json

from tools.tool_browser_runtime import playwright_agent_is_ready, runtime_metadata
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
    invoke_agent_skill,
    invoke_creditchina_query,
    invoke_playwright_agent,
    list_registered_skills,
)

# 这个文件只负责页面渲染。
# 真正的 skill 分发与执行入口都在 `智能体调度.py` / `skills/`。

__all__ = [
    "invoke_agent_skill",
    "invoke_creditchina_query",
    "invoke_playwright_agent",
    "playwright_agent_is_ready",
    "render_playwright_agent_page",
    "runtime_metadata",
]


def render_playwright_agent_page(embedded: bool = False) -> str:
    """渲染浏览器 Agent 的内置聊天页面。"""
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
            "availableSkills": list_registered_skills(),
        },
        ensure_ascii=False,
    ).replace("</", "<\\/")
    body_class = "embedded" if embedded else "standalone"
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
      grid-template-rows: minmax(0, 1fr) auto auto;
    }}

    .chat-feed {{
      padding: 20px 20px 20px;
      display: grid;
      align-content: start;
      gap: 16px;
      min-height: 0;
      overflow: auto;
      overscroll-behavior-y: contain;
      -webkit-overflow-scrolling: touch;
      touch-action: pan-y;
      background:
        radial-gradient(520px 240px at 100% 0%, rgba(67, 88, 106, 0.05), transparent 58%),
        linear-gradient(180deg, rgba(255, 255, 255, 0.76), rgba(249, 247, 243, 0.72));
    }}

    .page.empty-state .chat-feed {{
      align-content: end;
      padding-top: 20px;
      padding-bottom: 12px;
    }}

    .page.empty-state .message.assistant {{
      max-width: min(96%, 1120px);
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
      padding: 12px 16px 16px;
      border-top: 1px solid rgba(36, 44, 55, 0.08);
      background: linear-gradient(180deg, rgba(255, 255, 255, 0.96), rgba(248, 247, 244, 0.94));
      display: grid;
      gap: 10px;
    }}

    .composer-box {{
      display: grid;
      gap: 10px;
      padding: 12px;
      width: min(1120px, 100%);
      margin: 0 auto;
      border-radius: 20px;
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
      gap: 8px;
    }}

    .prompt-chip {{
      display: inline-flex;
      align-items: center;
      min-height: 34px;
      padding: 6px 12px;
      border-radius: 999px;
      border: 1px solid rgba(67, 88, 106, 0.12);
      background: rgba(255, 255, 255, 0.76);
      color: #314552;
      font-size: 13px;
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

    .composer-input-row {{
      display: flex;
      align-items: flex-end;
      gap: 10px;
      padding: 10px 12px;
      border-radius: 18px;
      border: 1px solid rgba(67, 88, 106, 0.10);
      background: linear-gradient(180deg, rgba(247, 248, 250, 0.92), rgba(255, 255, 255, 0.98));
    }}

    .composer-textarea {{
      flex: 1;
      width: 100%;
      min-height: 22px;
      max-height: 120px;
      border: 0;
      resize: none;
      outline: none;
      padding: 0;
      margin: 0;
      background: transparent;
      color: var(--text);
      line-height: 1.55;
      font-size: 15px;
    }}

    .composer-textarea::placeholder {{
      color: #8b96a0;
    }}

    .composer-actions {{
      display: flex;
      align-items: center;
      justify-content: flex-start;
      gap: 8px;
    }}

    .composer-hint {{
      color: var(--muted);
      font-size: 12px;
      line-height: 1.5;
    }}

    .composer-submit {{
      flex: 0 0 auto;
      width: 42px;
      height: 42px;
      min-width: 42px;
      min-height: 42px;
      border: 0;
      border-radius: 999px;
      background: linear-gradient(135deg, #43586a, #607587);
      color: #fff;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      cursor: pointer;
      transition: transform 180ms ease, box-shadow 180ms ease, opacity 180ms ease;
      box-shadow: 0 10px 20px rgba(67, 88, 106, 0.20);
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

    body.embedded {{
      background:
        linear-gradient(180deg, rgba(240, 244, 248, 0.96), rgba(233, 239, 246, 0.98));
    }}

    body.embedded .page {{
      background: transparent;
    }}

    body.embedded .chat-feed {{
      padding: 16px 16px 12px;
      gap: 14px;
      background:
        radial-gradient(420px 180px at 100% 0%, rgba(67, 88, 106, 0.04), transparent 58%),
        linear-gradient(180deg, rgba(248, 250, 252, 0.82), rgba(241, 245, 249, 0.96));
    }}

    body.embedded .bubble {{
      border-radius: 16px;
      box-shadow: 0 10px 18px rgba(31, 39, 47, 0.04);
    }}

    body.embedded .typing {{
      padding: 0 16px 8px;
      font-size: 12px;
    }}

    body.embedded .composer {{
      padding: 8px 10px 10px;
      gap: 8px;
      background: linear-gradient(180deg, rgba(247, 249, 252, 0.98), rgba(241, 245, 249, 0.98));
    }}

    body.embedded .composer-box {{
      gap: 8px;
      padding: 8px;
      border-radius: 18px;
      border-color: rgba(67, 88, 106, 0.08);
      box-shadow:
        inset 0 1px 0 rgba(255, 255, 255, 0.9),
        0 6px 16px rgba(31, 39, 47, 0.04);
    }}

    body.embedded .prompt-chip {{
      min-height: 30px;
      padding: 4px 10px;
      font-size: 12px;
    }}

    body.embedded .composer-input-row {{
      gap: 8px;
      padding: 8px 10px;
      border-radius: 16px;
      border-color: rgba(67, 88, 106, 0.08);
      background: rgba(255, 255, 255, 0.96);
      box-shadow:
        inset 0 0 0 1px rgba(255, 255, 255, 0.72),
        0 6px 14px rgba(31, 39, 47, 0.04);
    }}

    body.embedded .composer-textarea {{
      min-height: 20px;
      max-height: 88px;
      font-size: 14px;
      line-height: 1.45;
    }}

    body.embedded .composer-submit {{
      width: 38px;
      height: 38px;
      min-width: 38px;
      min-height: 38px;
      box-shadow: 0 8px 16px rgba(67, 88, 106, 0.18);
    }}

    body.embedded .composer-hint {{
      font-size: 11px;
      color: rgba(91, 104, 116, 0.9);
    }}

    @media (max-width: 820px) {{
      .chat-feed {{
        padding: 16px 14px 16px;
      }}

      .typing {{
        padding: 0 16px 8px;
      }}

      .composer {{
        padding: 10px 12px 14px;
      }}

      .composer-box {{
        padding: 10px;
      }}

      .composer-input-row {{
        padding: 9px 10px;
      }}

      .composer-submit {{
        width: 40px;
        height: 40px;
        min-width: 40px;
        min-height: 40px;
      }}
    }}
  </style>
</head>
<body class='{body_class}'>
  <div class='page'>
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
          <button type='button' class='prompt-chip' data-prompt='进入 https://www.creditchina.gov.cn/ ，使用统一社会信用代码 91420000177570439L 完成一次完整的信用中国固定查询，并把最终结果保存到文件。要求：1. 不能把搜索结果列表页当成最终完成；如果当前只是列表页命中结果，继续进入企业详情页。2. 只有拿到详情页字段或 private-api 详情字段后，才算查询完成。3. 如果 private-api 和详情页都失败，才允许回退成列表页简版结果，并明确告诉我这是回退结果。4. 最终请告诉我企业名称、统一社会信用代码、法定代表人/负责人、成立日期、住所、登记机关，以及结果文件路径。'>信用中国固定查询</button>
        </div>
        <div class='composer-input-row'>
          <textarea id='composer-input' class='composer-textarea' placeholder='直接输入你的问题，比如：先观察页面结构，再完成一次查询。'></textarea>
          <button id='send-btn' type='submit' class='composer-submit' aria-label='发送'>
            <svg viewBox='0 0 24 24' width='18' height='18' fill='none' xmlns='http://www.w3.org/2000/svg' aria-hidden='true'>
              <path d='M4 12.5L18.5 5L15 19L11 13.5L4 12.5Z' fill='currentColor'/>
              <path d='M10.5 13.5L18.5 5' stroke='currentColor' stroke-width='1.5' stroke-linecap='round' stroke-linejoin='round'/>
            </svg>
          </button>
        </div>
        <div class='composer-actions'>
          <div class='composer-hint'>Enter 发送，Shift + Enter 换行。当前后端仍然是浏览器 Agent。</div>
        </div>
      </div>
    </form>
  </div>

  <script>
    const pageConfig = {config_json};
    const SESSION_STORAGE_KEY = 'playwright-agent-session-id';
    const pageEl = document.querySelector('.page');
    const messagesEl = document.getElementById('messages');
    const typingEl = document.getElementById('typing');
    const composerForm = document.getElementById('composer-form');
    const composerInput = document.getElementById('composer-input');
    const sendBtn = document.getElementById('send-btn');
    let inFlight = false;
    let sessionId = '';

    try {{
      sessionId = String(window.localStorage.getItem(SESSION_STORAGE_KEY) || '').trim();
    }} catch (_error) {{
      sessionId = '';
    }}

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
        const selectedSkill = meta.skill && meta.skill.name ? String(meta.skill.name) : '';
        const usedTools = Array.isArray(meta.used_tools) ? meta.used_tools : [];
        if (selectedSkill || usedTools.length) {{
          const toolRow = document.createElement('div');
          toolRow.className = 'tool-row';
          const chips = [];
          if (selectedSkill) {{
            chips.push("<span class='tool-chip'>skill:" + escapeHtml(selectedSkill) + "</span>");
          }}
          toolRow.innerHTML = chips.concat(usedTools.map((item) => (
            "<span class='tool-chip'>" + escapeHtml(item) + "</span>"
          ))).join('');
          wrapper.appendChild(toolRow);
        }}
        if (meta.page_result || meta.agent_debug) {{
          console.debug('[playwright-agent]', meta);
        }}
      }}

      messagesEl.appendChild(wrapper);
      syncEmptyState();
      scrollMessagesToBottom();
    }}

    function syncEmptyState() {{
      const messageCount = messagesEl.querySelectorAll('.message').length;
      const hasUserMessage = Boolean(messagesEl.querySelector('.message.user'));
      const isEmptyState = !inFlight && messageCount <= 1 && !hasUserMessage;
      pageEl.classList.toggle('empty-state', isEmptyState);
    }}

    function scrollMessagesToBottom() {{
      window.requestAnimationFrame(() => {{
        messagesEl.scrollTop = messagesEl.scrollHeight;
      }});
    }}

    function syncComposerHeight() {{
      const embeddedMode = document.body.classList.contains('embedded');
      const minHeight = embeddedMode ? 20 : 22;
      const maxHeight = embeddedMode ? 88 : 120;
      composerInput.style.height = '0px';
      const nextHeight = Math.max(minHeight, Math.min(composerInput.scrollHeight, maxHeight));
      composerInput.style.height = nextHeight + 'px';
    }}

    function setBusy(nextBusy) {{
      inFlight = Boolean(nextBusy);
      composerInput.disabled = inFlight || !pageConfig.ready;
      sendBtn.disabled = inFlight || !pageConfig.ready;
      typingEl.hidden = !inFlight;
      syncEmptyState();
      scrollMessagesToBottom();
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
            session_id: sessionId,
            message: text,
            base_url: String(pageConfig.defaultBaseUrl || '').trim(),
            credit_code: String(pageConfig.defaultCreditCode || '').trim(),
            site_password: String(pageConfig.defaultSitePassword || '').trim(),
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
        const nextSessionId = String(((result.session || {{}}).id) || '').trim();
        if (nextSessionId) {{
          sessionId = nextSessionId;
          try {{
            window.localStorage.setItem(SESSION_STORAGE_KEY, sessionId);
          }} catch (_error) {{
          }}
        }}
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
    syncEmptyState();
    scrollMessagesToBottom();
    composerInput.focus();
    window.addEventListener('resize', scrollMessagesToBottom);
  </script>
</body>
</html>"""
