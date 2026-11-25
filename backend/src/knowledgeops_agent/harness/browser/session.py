"""活动页只存在于根 Run 持有者任务，不进入检查点，关闭时不重放动作。"""

from __future__ import annotations

import asyncio
import logging
import re
import tempfile
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

from anyio.from_thread import start_blocking_portal
from sqlalchemy import text

from knowledgeops_agent.harness.browser.errors import BrowserToolError
from knowledgeops_agent.harness.browser.policy import BrowserPolicy
from knowledgeops_agent.harness.browser.settings import ATTACH_LOCK, SESSION_LOCK
from knowledgeops_agent.harness.browser.transport import open_browser, snapshot_text

logger = logging.getLogger(__name__)
_CONTROL = re.compile(
    r"^\s*-\s+(link|button|tab|menuitem|treeitem)\b(.*?)\s+\[ref=(e\d+)\]"
    r"((?:\s+\[[^\]]+\])*)\s*:?\s*$"
)
_BLOCKED = re.compile(
    r"\b(delete|remove|submit|upload|reset|confirm|save|purchase|pay|send|publish|log\s*out)\b"
    r"|删除|移除|提交|上传|选择文件|重置|保存|购买|支付|发送|发布|确认|注销",
    re.I,
)
_MAX_CONTROLS = 200


def controls_from_snapshot(snapshot: str) -> dict[str, dict]:
    controls = {}
    for line in snapshot.splitlines():
        match = _CONTROL.fullmatch(line)
        if match:
            role, label, ref, states = match.groups()
            label = label.strip().strip('"')[:200]
            controls[ref] = {
                "ref": ref, "role": role, "label": label,
                "blocked": "[disabled]" in states or (
                    role in {"button", "menuitem"} and bool(_BLOCKED.search(label))
                ),
            }
            if len(controls) >= _MAX_CONTROLS:
                break
    return controls


@contextmanager
def browser_lease(sessions, mode, capacity):
    """使用独占 PG 会话锁跨 worker 限流。关闭连接即释放进程异常退出的孤儿锁。"""
    # 连接整个租约保持 checkout，归还前显式解锁；不在网络动作期间持有事务。
    with sessions.kw["bind"].connect() as connection:
        def acquire(key):
            return connection.scalar(text("SELECT pg_try_advisory_lock(:key)"), {"key": key})

        keys = []
        try:
            if mode == "attach":
                if not acquire(ATTACH_LOCK):
                    raise BrowserToolError("local_browser_busy")
                keys.append(ATTACH_LOCK)
            slot = next((key for key in range(SESSION_LOCK, SESSION_LOCK + capacity) if acquire(key)), None)
            if slot is None:
                raise BrowserToolError("browser_unavailable")
            keys.append(slot)
            connection.commit()
            yield
        finally:
            try:
                connection.rollback()
                for key in reversed(keys):
                    connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": key})
                connection.commit()
            except Exception:
                connection.invalidate()


@dataclass
class BrowserSession:
    root_id: str
    mode: str
    policy: BrowserPolicy
    ready: asyncio.Future
    owner: asyncio.Task | None = None
    stop: asyncio.Event = field(default_factory=asyncio.Event)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    last_used: float = field(default_factory=time.monotonic)
    adapter: object | None = None
    page: tuple[str, float] | None = None
    observation_seq: int = 0
    current_observation_id: str | None = None
    valid_refs: dict[str, dict] = field(default_factory=dict)

    def invalidate_observation(self):
        self.current_observation_id = None
        self.valid_refs.clear()


class BrowserSessionManager:
    def __init__(self, settings, sessions, *, connector=open_browser, is_live=None):
        self.settings = settings
        self.sessions = sessions
        self.connector = connector
        self.is_live = is_live
        self._lock = threading.RLock()
        self._portal_context = self._portal = None
        self._closed = False
        self._entries: dict[str, BrowserSession] = {}

    def execute(self, root_id, mode, action, arguments, *, timeout, check):
        with self._lock:
            if self._closed:
                raise BrowserToolError("browser_unavailable")
            if self._portal is None:
                self._portal_context = start_blocking_portal(name="knowledgeops-browser")
                self._portal = self._portal_context.__enter__()
                self._sweeper = self._portal.start_task_soon(self._maintain)
            portal = self._portal
        return portal.call(self._execute, root_id, mode, action, arguments, timeout, check)

    def close_run(self, root_id):
        with self._lock:
            if self._portal is not None and not self._closed:
                self._portal.call(self._close_run, root_id)

    def close(self):
        with self._lock:
            self._closed = True
            if self._portal is not None:
                self._sweeper.cancel()
                self._portal.call(self._close_all)
                self._portal_context.__exit__(None, None, None)
                self._portal_context = self._portal = None

    async def _maintain(self):
        while True:
            await asyncio.sleep(2)
            for root_id, entry in list(self._entries.items()):
                idle = not entry.lock.locked() and time.monotonic() - entry.last_used >= self.settings.browser_idle_seconds
                try:
                    orphaned = self.is_live is not None and not await asyncio.to_thread(self.is_live, root_id)
                except Exception as error:
                    logger.warning("Browser lease liveness check failed (%s)", type(error).__name__)
                    orphaned = False
                if (idle or orphaned) and self._entries.get(root_id) is entry:
                    await self._retire(entry)

    async def _own(self, entry):
        try:
            with browser_lease(self.sessions, entry.mode, self.settings.browser_max_concurrent_sessions):
                with tempfile.TemporaryDirectory(prefix="knowledgeops-browser-0064-", dir="/tmp") as temporary:
                    async with self.connector(self.settings, entry.mode, Path(temporary), entry.policy) as adapter:
                        entry.adapter = adapter
                        entry.ready.set_result(None)
                        await entry.stop.wait()
        except asyncio.CancelledError:
            if not entry.ready.done():
                entry.ready.cancel()
            raise
        except Exception as error:
            if not entry.ready.done():
                entry.ready.set_exception(error if isinstance(error, BrowserToolError) else BrowserToolError(
                    "cdp_unreachable" if entry.mode == "attach" else "browser_unavailable"
                ))
            else:
                logger.warning("Browser session cleanup failed (%s)", type(error).__name__)

    async def _retire(self, entry):
        entry.stop.set()
        if not entry.ready.done():
            entry.owner.cancel()
        try:
            done, _ = await asyncio.wait({entry.owner}, timeout=6)
            if not done:
                entry.owner.cancel()
                await asyncio.gather(entry.owner, return_exceptions=True)
        finally:
            if self._entries.get(entry.root_id) is entry and entry.owner.done():
                del self._entries[entry.root_id]

    async def _close_run(self, root_id):
        if entry := self._entries.get(root_id):
            await self._retire(entry)

    async def _close_all(self):
        for entry in list(self._entries.values()):
            await self._retire(entry)

    async def _execute(self, root_id, mode, action, arguments, timeout, check):
        if action == "close":
            await self._close_run(root_id)
            return {}
        deadline = asyncio.get_running_loop().time() + timeout
        entry = self._entries.get(root_id)
        if entry is None:
            if action != "navigate":
                raise BrowserToolError("browser_session_expired")
            policy = BrowserPolicy(self.settings, mode)
            try:
                async with asyncio.timeout(timeout):
                    await asyncio.to_thread(policy.check, arguments["url"])
            except TimeoutError:
                raise BrowserToolError("browser_timeout") from None
            # DNS 校验可异步等待，同根另一个线程可能已建立会话。
            entry = self._entries.get(root_id)
            if entry is None:
                entry = BrowserSession(root_id, mode, policy, asyncio.get_running_loop().create_future())
                self._entries[root_id] = entry
                entry.owner = asyncio.create_task(self._own(entry))
        try:
            async with asyncio.timeout_at(deadline):
                async with entry.lock:
                    await asyncio.shield(entry.ready)
                    if entry.stop.is_set() or entry.owner.done() or entry.mode != mode:
                        raise BrowserToolError("browser_session_expired")
                    await asyncio.to_thread(check)
                    operation = asyncio.create_task(self._perform(entry, action, arguments))
                    try:
                        while not operation.done():
                            await asyncio.wait({operation}, timeout=0.25)
                            await asyncio.to_thread(check)
                        return operation.result()
                    finally:
                        if not operation.done():
                            operation.cancel()
                        await asyncio.gather(operation, return_exceptions=True)
                        entry.last_used = time.monotonic()
        except TimeoutError:
            await self._retire(entry)
            raise BrowserToolError("browser_timeout") from None
        except BrowserToolError as error:
            if error.code in {"browser_timeout", "browser_disconnected", "origin_blocked"} or entry.owner.done():
                await self._retire(entry)
            raise
        except Exception:
            await self._retire(entry)
            raise

    async def _view(self, entry, limit=0):
        raw = await entry.adapter.page(limit)
        url = await asyncio.to_thread(entry.policy.check, raw["url"])
        return {**raw, "url": str(url)}

    async def _perform(self, entry, action, args):
        adapter = entry.adapter
        if action != "navigate" and entry.page is None:
            raise BrowserToolError("browser_session_expired")
        if action == "navigate":
            url = await asyncio.to_thread(entry.policy.check, args["url"])
            entry.invalidate_observation()
            await adapter.call("browser_navigate", {"url": str(url)})
        elif action == "back":
            await self._view(entry)
            entry.invalidate_observation()
            await adapter.call("browser_navigate_back", {})
        elif action == "click":
            control = entry.valid_refs.get(args["ref"])
            if entry.current_observation_id != args["observation_id"] or control is None:
                raise BrowserToolError("stale_observation")
            before = await self._view(entry)
            if entry.page != (before["url"], before["documentId"]):
                entry.invalidate_observation()
                raise BrowserToolError("stale_observation")
            if control["blocked"]:
                raise BrowserToolError("control_blocked")
            target = await adapter.click_check(control["ref"])
            if target.get("blocked"):
                raise BrowserToolError("control_blocked")
            if href := target.get("href"):
                await asyncio.to_thread(entry.policy.check, href)
            entry.invalidate_observation()
            await adapter.call("browser_click", {"target": control["ref"]})
        elif action == "wait":
            await self._view(entry)
            entry.invalidate_observation()
            await adapter.call("browser_wait_for", {key: value for key, value in args.items() if value is not None})
        elif action == "observe":
            before = await self._view(entry)
            snapshot = snapshot_text(await adapter.call("browser_snapshot", {}))
            after = await self._view(entry, 4000)
            if (before["url"], before["documentId"]) != (after["url"], after["documentId"]):
                entry.invalidate_observation()
                raise BrowserToolError("stale_observation")
            entry.observation_seq += 1
            entry.current_observation_id = f"{entry.observation_seq}-{uuid4().hex}"
            entry.valid_refs = controls_from_snapshot(snapshot)
            entry.page = (after["url"], after["documentId"])
            return {"url": after["url"], "title": after["title"], "text": after["text"],
                    "observation_id": entry.current_observation_id, "controls": list(entry.valid_refs.values())}
        elif action == "capture":
            from knowledgeops_agent.harness.browser.capture import page_capture

            return await page_capture(entry, args, self.settings, lambda limit=0: self._view(entry, limit))
        page = await self._view(entry)
        entry.page = (page["url"], page["documentId"])
        return {"url": page["url"], "title": page["title"]}

