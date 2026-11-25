"""上游工具固定适配。外部协议内容只在此处解包，模型看不到工具目录或脚本协议。"""

from __future__ import annotations

import asyncio
import json
import re
from contextlib import asynccontextmanager

from knowledgeops_agent.harness.browser.errors import BrowserToolError
from knowledgeops_agent.harness.browser.proxy import BrowserEgress
from knowledgeops_agent.harness.browser.settings import (
    cdp_headers,
    chromium,
    connect_endpoint,
    entrypoint,
)
from knowledgeops_agent.harness.mcp.transport import MCPConnectionConfig, connected_adapter

# 函数参数完全由代码产生。读取受模型及页面控制的数据时不求值其内容。
PAGE_FUNCTION = """() => ({
  url: location.href, title: document.title.slice(0, 500),
  documentId: performance.timeOrigin,
  text: (document.querySelector('main, [role="main"], article') || document.body)?.innerText.slice(0, LIMIT) || ""
})"""
CLICK_CHECK = """element => {
  const anchor = element.closest('a');
  const type = (element.getAttribute('type') || '').toLowerCase();
  const button = element.closest('button');
  const blocked = element.matches('input, textarea, select, [contenteditable]') ||
    element.closest('[contenteditable="true"]') !== null ||
    (button && (button.type === 'submit' || button.type === 'reset')) ||
    !!element.closest('[formaction], [download], [target]:not([target="_self"])') ||
    type === 'file' || (!!anchor && !/^https?:/i.test(anchor.href));
  return { blocked: !!blocked, href: anchor?.href || null };
}"""

# 受信任的启动钩子限制 TLS 内的页面请求方法；出站代理独立校验网络目的地。
READ_ONLY_PAGE = """exports.default = async ({ page }) => {
  const context = page.context();
  if (context.__knowledgeopsReadOnly) return;
  context.__knowledgeopsReadOnly = true;
  await context.route('**/*', route =>
    ['GET', 'HEAD', 'OPTIONS'].includes(route.request().method()) ? route.continue() : route.abort()
  );
  await context.routeWebSocket('**/*', socket => socket.close());
};"""

_REQUIRED_TOOLS = {
    "browser_navigate": {"url"}, "browser_navigate_back": set(),
    "browser_snapshot": set(), "browser_click": {"target"},
    "browser_wait_for": {"text", "time"}, "browser_evaluate": {"function", "target"},
    "browser_take_screenshot": {"type"}, "browser_tabs": {"action"},
}


def result_text(result) -> str:
    return "\n".join(block.text for block in result.content if block.type == "text")


def evaluated(result) -> dict:
    text = result_text(result)
    prefix = "### Result\n"
    if not text.startswith(prefix):
        raise BrowserToolError("browser_disconnected")
    try:
        value, _ = json.JSONDecoder().raw_decode(text[len(prefix):].lstrip())
        if not isinstance(value, dict):
            raise ValueError
        return value
    except ValueError:
        raise BrowserToolError("browser_disconnected") from None


class BrowserAdapter:
    def __init__(self, client, egress):
        self.client, self.egress = client, egress

    async def call(self, name, args):
        denied = self.egress.denied if self.egress else 0
        try:
            result = await self.client.session.call_tool(name, args)
        except TimeoutError:
            raise BrowserToolError("browser_timeout") from None
        except Exception:
            raise BrowserToolError("browser_disconnected") from None
        body = result_text(result)
        failed = result.is_error or body.startswith("### Error")
        if failed:
            if "Timeout" in body or "timed out" in body:
                raise BrowserToolError("browser_timeout")
            if self.egress and self.egress.denied > denied:
                raise BrowserToolError("origin_blocked")
            if name == "browser_evaluate" and "target" in args:
                raise BrowserToolError("stale_observation")
            raise BrowserToolError("browser_disconnected")
        return result

    async def page(self, limit):
        return evaluated(await self.call("browser_evaluate", {"function": PAGE_FUNCTION.replace("LIMIT", str(limit))}))

    async def click_check(self, ref):
        return evaluated(await self.call("browser_evaluate", {"function": CLICK_CHECK, "target": ref}))


@asynccontextmanager
async def open_browser(settings, mode, output, policy):
    from contextlib import AsyncExitStack

    cli = entrypoint(settings)
    executable = chromium(settings) if mode == "launch" else None
    if not cli or (mode == "launch" and not executable):
        raise BrowserToolError("browser_unavailable")
    timeout = settings.browser_action_timeout_seconds
    ms = str(int(timeout * 1000))
    args = [
        str(cli), "--output-dir", str(output), "--ignore-roots", "--no-webmcp",
        "--image-responses", "allow", "--snapshot-mode", "none", "--codegen", "none",
        "--idle-timeout", "0", "--timeout-action", ms, "--timeout-navigation", ms,
        "--output-max-size", str(settings.browser_max_screenshot_bytes * 2),
    ]
    async with AsyncExitStack() as stack:
        egress = None
        if mode == "launch":
            egress = BrowserEgress(policy)
            proxy = await stack.enter_async_context(egress.serve())
            args += [
                "--headless", "--isolated", "--no-sandbox", "--browser", "chromium",
                "--executable-path", str(executable), "--block-service-workers",
                "--proxy-server", proxy, "--proxy-bypass", "<-loopback>",
            ]
            config_path = output / "browser-config.json"
            config_path.write_text(json.dumps({"browser": {"launchOptions": {"args": [
                "--disable-quic", "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
            ]}}}), encoding="utf-8")
            init_page = output / "read-only-page.cjs"
            init_page.write_text(READ_ONLY_PAGE, encoding="utf-8")
            args.extend(("--config", str(config_path), "--init-page", str(init_page)))
        else:
            try:
                endpoint = await asyncio.to_thread(connect_endpoint, settings)
            except Exception:
                raise BrowserToolError("cdp_unreachable") from None
            args.extend(("--cdp-endpoint", endpoint, "--cdp-timeout", ms))
            args.extend(part for key, value in cdp_headers(settings).items()
                        for part in ("--cdp-header", f"{key}:{value}"))
        config = MCPConnectionConfig(
            transport="stdio", command=settings.browser_mcp_command, args=args, cwd=str(output),
            timeout_seconds=timeout, protocol_mode="legacy", environment_refs={"TMPDIR": "BROWSER_RUN_TEMP"},
        )
        connector = await stack.enter_async_context(connected_adapter(config, secrets={"BROWSER_RUN_TEMP": str(output)}))
        tools = await connector.client.session.list_tools()
        catalog = {tool.name: tool.input_schema.get("properties", {}) for tool in tools.tools}
        if any(name not in catalog or not keys <= catalog[name].keys() for name, keys in _REQUIRED_TOOLS.items()):
            raise BrowserToolError("browser_unavailable")
        adapter = BrowserAdapter(connector.client, egress)
        if mode == "attach":
            # 使用专用新页，避免首次导航覆盖用户打开的页。退出仅断开 MCP。
            try:
                await adapter.call("browser_tabs", {"action": "new"})
            except BrowserToolError:
                raise BrowserToolError("cdp_unreachable") from None
        yield adapter


def snapshot_text(result) -> str:
    # 上游快照在 fenced yaml 中返回；剥离页面标题、URL 和工具响应的其余元数据。
    text = result_text(result)
    match = re.search(r"^### Snapshot\s*\n" + chr(96) * 3 + r"yaml\n(.*?)\n" + chr(96) * 3, text, re.M | re.S)
    if not match:
        raise BrowserToolError("browser_disconnected")
    return match.group(1)

