"""容器 Chromium 的 HTTP/CONNECT 出站门。

逐个连接检查真正的目标，包括重定向和子资源，直接连接校验后的 IP。
HTTPS 仍由 Chromium 端到端校验证书。此代理不用于外部宿主机的 CDP 流量。
"""

from __future__ import annotations

import asyncio
import re
from contextlib import asynccontextmanager, suppress

from knowledgeops_agent.harness.browser.errors import BrowserToolError

_FORWARD_HEADERS = {
    "accept", "accept-encoding", "accept-language", "cache-control", "cookie",
    "if-modified-since", "if-none-match", "origin", "range", "referer", "user-agent",
}


async def _pipe(reader, writer):
    while chunk := await reader.read(65536):
        writer.write(chunk)
        await writer.drain()
    if writer.can_write_eof():
        writer.write_eof()


class BrowserEgress:
    def __init__(self, policy):
        self.policy = policy
        self.denied = 0
        self.tasks: set[asyncio.Task] = set()

    @asynccontextmanager
    async def serve(self):
        server = await asyncio.start_server(self._handle, "127.0.0.1", 0, limit=32768)
        port = server.sockets[0].getsockname()[1]
        try:
            async with server:
                yield f"http://127.0.0.1:{port}"
        finally:
            for task in self.tasks:
                task.cancel()
            await asyncio.gather(*self.tasks, return_exceptions=True)

    async def _handle(self, reader, writer):
        task = asyncio.current_task()
        self.tasks.add(task)
        upstream = None
        try:
            async with asyncio.timeout(60):
                headers = (await reader.readuntil(b"\r\n\r\n")).decode("latin-1").split("\r\n")
                method, target, version = headers[0].split(" ")
                if version not in {"HTTP/1.0", "HTTP/1.1"}:
                    raise ValueError
                if method == "CONNECT":
                    if not re.fullmatch(r"(?:[A-Za-z0-9.-]+|\[[0-9a-fA-F:]+\]):[0-9]{1,5}", target):
                        raise ValueError
                    value = f"https://{target}/"
                elif method in {"GET", "HEAD", "OPTIONS"} and target.startswith("http://"):
                    value = target
                else:
                    raise BrowserToolError("origin_blocked")
                url, ips = await asyncio.to_thread(self.policy.addresses, value)
                for ip in ips:
                    try:
                        incoming, upstream = await asyncio.wait_for(
                            asyncio.open_connection(ip, url.port or (443 if method == "CONNECT" else 80)), 5
                        )
                        break
                    except (OSError, TimeoutError):
                        continue
                if upstream is None:
                    raise OSError
                if method == "CONNECT":
                    writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                    await writer.drain()
                    async with asyncio.TaskGroup() as group:
                        group.create_task(_pipe(reader, upstream))
                        group.create_task(_pipe(incoming, writer))
                else:
                    # 自己生成 Host 和消息边界。忽略浏览器给出的代理头和请求体。
                    upstream.write(
                        method.encode() + b" " + url.raw_path + b" HTTP/1.1\r\n"
                        + b"Host: " + url.netloc + b"\r\nConnection: close\r\n"
                        + b"".join(
                            line.encode("latin-1") + b"\r\n" for line in headers[1:]
                            if line.partition(":")[0].lower() in _FORWARD_HEADERS
                        )
                        + b"\r\n"
                    )
                    await upstream.drain()
                    await _pipe(incoming, writer)
        except BrowserToolError:
            self.denied += 1
            await self._reject(writer, b"403 Forbidden")
        except (OSError, ValueError, TimeoutError, asyncio.IncompleteReadError, asyncio.LimitOverrunError):
            if upstream is None:
                await self._reject(writer, b"502 Bad Gateway")
        except ExceptionGroup:
            # 隧道一端提前断开时，TaskGroup 已清理另一端。
            pass
        finally:
            if upstream is not None:
                upstream.close()
            writer.close()
            self.tasks.discard(task)

    @staticmethod
    async def _reject(writer, status: bytes):
        with suppress(OSError):
            writer.write(b"HTTP/1.1 " + status + b"\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
            await writer.drain()

