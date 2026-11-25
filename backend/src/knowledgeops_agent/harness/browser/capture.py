"""将受控页面转为既有来源证据，PNG 作为独立不可变运行产物。"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
from dataclasses import dataclass

from sqlalchemy import select

from knowledgeops_agent.app.lifecycle.access import require_readable_run
from knowledgeops_agent.harness.browser.errors import BrowserToolError
from knowledgeops_agent.persistence.models import Run, RunArtifact

PNG = b"\x89PNG\r\n\x1a\n"
SCREENSHOT_KIND = "browser_screenshot"


@dataclass
class CapturedPage:
    url: str
    title: str
    location: str
    text: str
    png: bytes | None


async def page_capture(entry, args, settings, read_page) -> CapturedPage:
    raw = await read_page(settings.browser_max_capture_chars)
    text = "\n".join(line for line in (" ".join(line.split()) for line in raw["text"].replace("\x00", "").splitlines()) if line)
    if len("".join(text.split())) < 20:
        raise BrowserToolError("browser_empty_content")
    image = None
    if args["include_screenshot"]:
        result = await entry.adapter.call("browser_take_screenshot", {"type": "png", "fullPage": False})
        block = next((block for block in result.content if block.type == "image" and block.mime_type == "image/png"), None)
        if block is None or len(block.data) > ((settings.browser_max_screenshot_bytes + 2) // 3) * 4:
            raise BrowserToolError("browser_unavailable")
        try:
            image = base64.b64decode(block.data, validate=True)
        except (binascii.Error, ValueError):
            raise BrowserToolError("browser_unavailable") from None
        if not image.startswith(PNG) or len(image) > settings.browser_max_screenshot_bytes:
            raise BrowserToolError("browser_unavailable")
    after = await read_page()
    if (after["url"], after["documentId"]) != (raw["url"], raw["documentId"]):
        entry.invalidate_observation()
        raise BrowserToolError("stale_observation")
    title = raw["title"].replace("\x00", "").strip()[:500] or raw["url"][:500]
    return CapturedPage(raw["url"], title, " ".join(args["location_hint"].split())[:300] or title[:300], text, image)


def artifact_key(run_id, digest):
    return f"artifacts/{run_id}/browser/{digest}.png"


def persist_capture(context, page: CapturedPage, mode: str, *, event: dict) -> dict:
    """在同一控制事务中写入来源、首读和截图索引。PNG 先原子安装再提交引用。"""
    with context.jobs.sessions.begin() as session:
        root, _ = context.jobs.execution_guard(session, context.run_id, context.worker_id)
        store = session.info.get("file_store")
        if page.png is not None and store is None:
            raise BrowserToolError("browser_unavailable")
        source = context.sources.import_text(
            page.title, page.text, kind="web", origin=page.url, mime_type="text/html",
            parser="playwright-mcp-rendered-v1", body_format="markdown", root_id=root.id, session=session,
        )
        evidence = context.sources.read_segment(source["segments"][0]["id"], root_id=root.id, session=session)
        output = {
            "source": source, "source_id": source["id"], "version_id": source["version_id"],
            "source_version_id": source["version_id"], "evidence": evidence, "citation": evidence["citation"],
            "remaining_segments": source["segments"][1:], "url": page.url, "title": page.title,
            "location": page.location,
        }
        if page.png is not None:
            digest = hashlib.sha256(page.png).hexdigest()
            key = artifact_key(root.id, digest)
            metadata = {
                "storage_key": key, "sha256": digest, "bytes": len(page.png), "mime_type": "image/png",
                "url": page.url, "title": page.title, "location": page.location,
                "captured_at": context.jobs.now().isoformat(), "source_version_id": source["version_id"],
                "mode": mode,
            }
            # 相同像素可属于不同 URL 或正文版本。每次取证独立登记出处，同一 PNG 共用物理文件。
            content = json.dumps(metadata, ensure_ascii=False, sort_keys=True)
            capture_hash = hashlib.sha256(content.encode()).hexdigest()
            row = session.scalar(select(RunArtifact).where(
                RunArtifact.run_id == root.id, RunArtifact.kind == SCREENSHOT_KIND,
                RunArtifact.content_hash == capture_hash,
            ))
            if row is None:
                store.put_bytes(key, page.png, session=session, purpose=SCREENSHOT_KIND)
                row = RunArtifact(
                    run_id=root.id, kind=SCREENSHOT_KIND, content_hash=capture_hash, content_storage_key=key,
                    _content=content,
                )
                session.add(row)
                session.flush()
            output["screenshot"] = {
                "artifact_id": row.id, "image_url": f"/api/runs/{root.id}/artifacts/{row.id}/image",
                "sha256": digest, "bytes": len(page.png),
            }
        context.jobs._event(session, root, "browser_capture", {
            **event, "status": "succeeded", "source_version_id": source["version_id"],
            **({"screenshot": output["screenshot"]} if "screenshot" in output else {}),
        })
    return output


def screenshot_metadata(row) -> dict:
    try:
        if hashlib.sha256(row._content.encode()).hexdigest() != row.content_hash:
            raise ValueError
        metadata = json.loads(row._content)
        digest = metadata["sha256"]
        if (not isinstance(digest, str) or len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest)
                or row.content_storage_key != artifact_key(row.run_id, digest)
                or metadata["storage_key"] != row.content_storage_key or metadata["mime_type"] != "image/png"):
            raise ValueError
        return metadata
    except (KeyError, TypeError, ValueError):
        raise ValueError("browser screenshot metadata is invalid") from None


def read_screenshot(session, run_id: str, artifact_id: str) -> bytes:
    require_readable_run(session, session.get(Run, run_id))
    row = session.get(RunArtifact, artifact_id)
    if (
        row is None or row.run_id != run_id or row.kind != SCREENSHOT_KIND or row.expired_at
    ):
        raise KeyError("screenshot not found")
    return screenshot_bytes(session.info.get("file_store"), row)


def screenshot_bytes(store, row) -> bytes:
    if store is None:
        raise KeyError("screenshot unavailable")
    try:
        metadata = screenshot_metadata(row)
        digest = metadata["sha256"]
        path = store.path(row.content_storage_key)
        with path.open("rb") as stream:
            data = stream.read(20 * 1024 * 1024 + 1)
        if (
            not data.startswith(PNG) or len(data) > 20 * 1024 * 1024 or len(data) != metadata["bytes"]
            or hashlib.sha256(data).hexdigest() != digest
        ):
            raise ValueError
    except (OSError, KeyError, ValueError, TypeError):
        raise KeyError("screenshot unavailable") from None
    return data

