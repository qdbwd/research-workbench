"""浏览器设置由部署和单行应用偏好共同决定，运行只冻结有效选项。"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit, urlunsplit

import httpx
from sqlalchemy import text

from knowledgeops_agent.core.config import Settings
from knowledgeops_agent.persistence.models import BrowserSettings, utcnow

BrowserMode = Literal["launch", "attach"]
ATTACH_LOCK = 748616064
SESSION_LOCK = 748616080
MCP_ENTRYPOINT = "/workspace/infra/playwright-mcp/node_modules/@playwright/mcp/cli.js"


def _safe_is_file(path: Path) -> bool:
    """无权限访问受限父目录时返回 False，不将环境探测异常透传成业务 403。"""
    try:
        return path.is_file()
    except OSError:
        return False


def _safe_glob(directory: Path, pattern: str) -> list[Path]:
    """非 root 宿主（如 CI runner）无权遍历 /root 缓存目录时静默跳过。"""
    try:
        return sorted(directory.glob(pattern), reverse=True)
    except OSError:
        return []


def _safe_which(command: Path | str) -> bool:
    try:
        return bool(shutil.which(command))
    except OSError:
        return False


def entrypoint(settings: Settings) -> Path | None:
    path = Path(settings.browser_mcp_entrypoint or MCP_ENTRYPOINT)
    if path.is_absolute() and _safe_is_file(path) and _safe_which(settings.browser_mcp_command):
        return path
    return None


def chromium(settings: Settings) -> Path | None:
    pinned = Path("/root/.cache/ms-playwright/chromium-1243/chrome-linux64/chrome")
    if settings.browser_chromium_executable:
        candidates = [Path(settings.browser_chromium_executable)]
    else:
        candidates = [
            pinned,
            *_safe_glob(Path("/root/.cache/ms-playwright"), "chromium-*/chrome-linux*/chrome"),
            Path("/usr/bin/chromium"), Path("/usr/bin/google-chrome"),
        ]
    return next((path for path in candidates if path.is_absolute() and _safe_which(path)), None)


def attach_available(settings: Settings) -> bool:
    return bool(settings.browser_enabled and settings.browser_local_attach_enabled
                and settings.browser_local_cdp_endpoint)


def task_allowed(inputs: dict) -> bool:
    return not (inputs.get("knowledge_only") or inputs.get("trial_case")
                or (inputs.get("research") or {}).get("review_only"))


def resolve_mode(session, settings: Settings, requested: BrowserMode | None, *, inputs: dict) -> BrowserMode:
    row = session.get(BrowserSettings, "default") if settings.browser_enabled or requested == "attach" else None
    allowed = attach_available(settings) and row is not None and row.local_attach_enabled and task_allowed(inputs)
    if requested == "attach" and not allowed:
        raise ValueError("当前部署、浏览器设置或任务模式未允许本机 CDP 挂接。")
    return requested or ("attach" if allowed and row.default_mode == "attach" else "launch")


def frozen_browser(session, settings: Settings, inputs: dict) -> dict:
    row = session.get(BrowserSettings, "default") if settings.browser_enabled else None
    attach_allowed = bool(attach_available(settings) and row and row.local_attach_enabled)
    mode = inputs.get("browser_mode", "launch")
    return {
        "enabled": settings.browser_enabled and task_allowed(inputs) and (mode == "launch" or attach_allowed),
        "mode": mode,
        "local_attach_allowed": attach_allowed,
        "max_actions": settings.browser_max_actions_per_run,
        "action_timeout": settings.browser_action_timeout_seconds,
        "settings_revision": row.revision if row else 1,
    }


def attach_busy(session) -> bool:
    """观察其他进程持有的锁；不会替探测请求抢占本机窗口。"""
    return bool(session.scalar(text("""
        SELECT EXISTS (
          SELECT 1 FROM pg_locks
          WHERE locktype = 'advisory' AND classid = 0 AND objid = :key
            AND objsubid = 1 AND granted
            AND database = (SELECT oid FROM pg_database WHERE datname = current_database())
        )
    """), {"key": ATTACH_LOCK}))


def _endpoint_origin(endpoint: str, *, http: bool = False) -> str:
    url = urlsplit(endpoint)
    scheme = {"ws": "http", "wss": "https"}.get(url.scheme, url.scheme) if http else url.scheme
    return urlunsplit((scheme, url.netloc, "", "", ""))


def cdp_headers(settings: Settings) -> dict[str, str]:
    return {"Host": settings.browser_local_cdp_host_header} if settings.browser_local_cdp_host_header else {}


def cdp_document(client: httpx.Client, settings: Settings, name: str):
    endpoint = _endpoint_origin(settings.browser_local_cdp_endpoint, http=True)
    response = client.get(f"{endpoint}/json/{name}", headers=cdp_headers(settings))
    response.raise_for_status()
    return response.json()


def connect_endpoint(settings: Settings) -> str:
    """调试响应可能宣告 localhost；只采用受控地址的 authority 和有效浏览器 WS 路径。"""
    url = urlsplit(settings.browser_local_cdp_endpoint)
    if url.scheme in {"ws", "wss"}:
        return settings.browser_local_cdp_endpoint
    with httpx.Client(trust_env=False, timeout=3.0) as client:
        version = cdp_document(client, settings, "version")
    path = urlsplit(version.get("webSocketDebuggerUrl", "")).path
    if not path.startswith("/devtools/browser/") or any(char in path for char in "?#%\\"):
        raise ValueError("missing browser debugger path")
    return urlunsplit(("wss" if url.scheme == "https" else "ws", url.netloc, path, "", ""))


class BrowserSettingsService:
    """控制锁下更新指针和版本，部署路径/凭据不会进入应用响应。"""

    def __init__(self, sessions, settings: Settings):
        self.sessions, self.settings = sessions, settings

    def view(self, session) -> dict:
        row = session.get(BrowserSettings, "default")
        settings = self.settings
        return {
            "revision": row.revision if row else 1,
            "local_attach_enabled": row.local_attach_enabled if row else False,
            "default_mode": row.default_mode if row else "launch",
            "updated_at": row.updated_at if row else None,
            "deployment": {
                "browser_enabled": settings.browser_enabled,
                "local_attach_available": attach_available(settings),
                "cdp_endpoint_display": _endpoint_origin(settings.browser_local_cdp_endpoint) or None,
                "entrypoint_ready": entrypoint(settings) is not None,
                "chromium_ready": chromium(settings) is not None,
                "attach_busy": attach_busy(session) if attach_available(settings) else False,
                "max_concurrent_sessions": settings.browser_max_concurrent_sessions,
                "max_actions_per_run": settings.browser_max_actions_per_run,
            },
        }

    def get(self) -> dict:
        with self.sessions() as session:
            return self.view(session)

    def update(self, *, expected_revision: int, local_attach_enabled: bool | None, default_mode: BrowserMode | None):
        from knowledgeops_agent.app.runs.control import lock_control

        with self.sessions.begin() as session:
            lock_control(session)
            row = session.get(BrowserSettings, "default")
            if row is None:
                row = BrowserSettings(id="default", revision=1, default_mode="launch", local_attach_enabled=False)
                session.add(row)
            if row.revision != expected_revision:
                raise ValueError("浏览器设置已变化，请刷新版本后再保存。")
            enabled = row.local_attach_enabled if local_attach_enabled is None else local_attach_enabled
            mode = default_mode or row.default_mode
            if local_attach_enabled is True and not attach_available(self.settings):
                raise ValueError("部署未开放本机 CDP 挂接。")
            if not enabled:
                mode = "launch"
            if mode == "attach" and not attach_available(self.settings):
                raise ValueError("部署未开放本机 CDP 挂接。")
            if enabled != row.local_attach_enabled or mode != row.default_mode:
                row.local_attach_enabled, row.default_mode = enabled, mode
                row.revision += 1
                row.updated_at = utcnow()
            session.flush()
            return self.view(session)

    def probe(self) -> dict:
        settings = self.settings
        busy = False
        if not settings.browser_local_attach_enabled or not settings.browser_local_cdp_endpoint:
            return {"ok": False, "code": "cdp_disabled", "attach_busy": busy, "message": "部署未开放本机 CDP 探测。"}
        with self.sessions() as session:
            busy = attach_busy(session)
        try:
            with httpx.Client(trust_env=False, timeout=3.0) as client:
                version = cdp_document(client, settings, "version")
                tabs = cdp_document(client, settings, "list")
            browser = version.get("Browser")
            if not isinstance(browser, str) or not isinstance(tabs, list):
                raise ValueError("invalid CDP response")
            return {"ok": True, "code": "ok", "attach_busy": busy, "browser": browser[:160],
                    "tabs_count": sum(isinstance(tab, dict) and tab.get("type") == "page" for tab in tabs),
                    "message": "已连接专用本机浏览器。"}
        except (httpx.HTTPError, ValueError, AttributeError):
            return {"ok": False, "code": "cdp_unreachable", "attach_busy": busy,
                    "message": "无法连接专用本机浏览器，请检查调试窗口和部署端点。"}

