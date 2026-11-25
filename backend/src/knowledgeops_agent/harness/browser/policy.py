"""精确来源校验供动作层和容器浏览器出站代理共用。"""

from __future__ import annotations

import ipaddress
import re
import socket

import httpx

from knowledgeops_agent.core.config import Settings
from knowledgeops_agent.harness.browser.errors import BrowserToolError
from knowledgeops_agent.sources.web import public_url, resolve_addresses

_INTERNAL_HOSTS = {
    "api", "web", "worker", "dev", "init", "postgres", "redis", "tei-embedding",
    "metadata.google.internal", "metadata", "instance-data", "host.docker.internal",
    "gateway.docker.internal",
}
_INTERNAL_SUFFIXES = (".local", ".internal", ".localhost", ".lan", ".home.arpa")
_INTERNAL_PORTS = {5432, 6379, 8000, 5173, 8787}
_DEFAULT_PORTS = {
    "http": 80, "https": 443, "ws": 80, "wss": 443,
    "postgres": 5432, "postgresql": 5432, "redis": 6379, "rediss": 6379,
    "amqp": 5672, "amqps": 5671,
}
_METADATA = {ipaddress.ip_address(value) for value in ("169.254.169.254", "fd00:ec2::254")}


def _url(value: str) -> httpx.URL:
    """拒绝 Chromium 与服务端解析可能不一致的 URL 及不可信协议。"""
    if len(value) > 4096 or re.search(r"[\x00-\x20\x7f\\]", value):
        raise ValueError("invalid browser URL")
    url = httpx.URL(value)
    if url.scheme not in {"http", "https"} or not url.host or url.userinfo:
        raise ValueError("invalid browser URL")
    authority = value.split("://", 1)[-1].split("/", 1)[0].split("?", 1)[0].split("#", 1)[0]
    if "@" in authority or "%" in authority:
        raise ValueError("invalid browser authority")
    return url.copy_with(host=_canonical_host(url))


def _canonical_host(url: httpx.URL) -> str:
    host = url.raw_host.decode("ascii").lower().rstrip(".")
    try:
        # Chromium 同样会将八进制、十六进制及缩写 IPv4 视为 IP。
        return socket.inet_ntoa(socket.inet_aton(host))
    except OSError:
        return host


def _origin(url: httpx.URL) -> tuple[str, str, int]:
    return url.scheme, url.raw_host.decode(), url.port or _DEFAULT_PORTS[url.scheme]


def _configured_endpoint(value: str) -> tuple[str, int] | None:
    url = httpx.URL(value)
    port = url.port or _DEFAULT_PORTS.get(url.scheme.partition("+")[0])
    if url.raw_host and port is not None:
        return _canonical_host(url), port
    return None


def origin_set(raw: str) -> set[tuple[str, str, int]]:
    origins = set()
    for value in raw.split(","):
        if value.strip():
            url = _url(value.strip())
            if url.path not in {"", "/"} or url.query or url.fragment:
                raise ValueError("browser origin must contain only scheme, host and port")
            origins.add(_origin(url))
    return origins


class BrowserPolicy:
    def __init__(self, settings: Settings, mode: str):
        self.mode = mode
        self.allowed = origin_set(
            settings.browser_allowed_origins if mode == "launch"
            else settings.browser_attach_allowed_origins
        )
        self.internal_hosts = set(_INTERNAL_HOSTS)
        self.internal_endpoints = {
            address
            for endpoint in (
                settings.database_url, settings.runtime_redis_url, settings.celery_broker_url,
                settings.embedding_base_url, settings.reranker_base_url, settings.browser_local_cdp_endpoint,
            )
            if endpoint and (address := _configured_endpoint(endpoint)) is not None
        }

    def addresses(self, value: str) -> tuple[httpx.URL, list[str]]:
        """解析并校验全部地址，出站层只连接返回的已校验 IP。"""
        try:
            url = _url(value)
            scheme, host, port = _origin(url)
            exact = (scheme, host, port) in self.allowed
            if (
                host in self.internal_hosts
                or (host.endswith(_INTERNAL_SUFFIXES) and host.partition(".")[0] in self.internal_hosts)
                or (host, port) in self.internal_endpoints
                or port in _INTERNAL_PORTS or port < 1 or port > 65535
                or (self.mode == "attach" and self.allowed and not exact)
            ):
                raise ValueError("blocked browser destination")
            if self.mode == "launch" and not exact:
                public_url(str(url))
            try:
                ips = [ipaddress.ip_address(host)]
            except ValueError:
                if self.mode == "launch" and not exact and "." not in host:
                    raise ValueError("public destination requires a domain or IP") from None
                ips = [ipaddress.ip_address(ip) for ip in resolve_addresses(host, port)]
            ips = [getattr(ip, "ipv4_mapped", None) or ip for ip in ips]
            if not ips or any(ip in _METADATA for ip in ips):
                raise ValueError("blocked browser address")
            if self.mode == "launch" and not exact and any(
                not ip.is_global or ip.is_multicast for ip in ips
            ):
                raise ValueError("nonpublic browser address")
            return url, [str(ip) for ip in ips]
        except (ValueError, OSError, httpx.HTTPError, httpx.InvalidURL, UnicodeError):
            raise BrowserToolError("origin_blocked") from None

    def check(self, value: str) -> httpx.URL:
        return self.addresses(value)[0]

