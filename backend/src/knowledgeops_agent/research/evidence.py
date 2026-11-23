"""将已有的已读记录解析为版本、原文范围和研究内的可追溯引用。"""

import json

from sqlalchemy import select

from knowledgeops_agent.persistence.models import (
    ChatMessage,
    Evidence,
    KnowledgeVersion,
    Run,
    RunArtifact,
    Source,
    SourceSegment,
    SourceVersion,
)
from knowledgeops_agent.sources.service import SourceService

_EXCERPT = "research_excerpt"


def capture_excerpt(context, *, kind, title, text, start, end, offset_unit, **source):
    """继续使用运行产物的不可变文件与哈希；只保存真正经过原工具返回的范围。"""
    if not text.strip():
        return None
    root = context.jobs.get_run(context.root_id)
    binding = root["input"].get("research")
    if not binding:
        return None
    if kind == "history":
        message_id = source.get("message_id") or ""
        if message_id.startswith("application-task-"):
            return None
        with context.jobs.sessions() as session:
            match = ChatMessage.id == message_id
            if message_id.startswith("user-task-"):
                match = (ChatMessage.run_id == message_id.removeprefix("user-task-")) & (ChatMessage.role == "user")
            original = session.scalar(select(ChatMessage).where(
                ChatMessage.conversation_id == root["conversation_id"], match,
            ).limit(1))
            if original and context.gateway._redact(original.content)[start:end] == text:
                source["conversation_url"] = f"/chat/{original.conversation_id}?message={original.id}"
    packet = context.gateway._redact({
        "kind": kind, "title": title, "text": text, "start": start, "end": end,
        "offset_unit": offset_unit, **source,
    })
    return context.jobs.save_artifact(
        context.run_id, _EXCERPT, json.dumps(packet, ensure_ascii=False, sort_keys=True),
        worker_id=context.worker_id,
    )


def _bound_to(session, run_id, branch):
    owner = session.get(Run, run_id)
    root = session.get(Run, owner.root_id) if owner and owner.parent_id else owner
    binding = root.input_data.get("research", {}) if root else {}
    return (binding.get("task_id"), binding.get("branch_id")) == (branch.task_id, branch.id)


def _excerpt(session, identifier):
    artifact = session.get(RunArtifact, identifier)
    return artifact if artifact and artifact.kind == _EXCERPT else None


def _disclose_artifact(artifact):
    if artifact.expired_at:
        raise ValueError("固定读取摘录已清理。")
    return json.loads(artifact.content)


def artifact_description(artifact, task_id):
    try:
        data = _disclose_artifact(artifact)
    except (OSError, ValueError):
        return {"id": artifact.id, "available": False, "url": None, "quote": ""}
    return {
        "id": artifact.id, "kind": data["kind"], "title": data["title"], "start": data["start"], "end": data["end"],
        "quote": data["text"][:220], "offset_unit": data["offset_unit"], "available": True,
        "url": f"/research/{task_id}/evidence/{artifact.id}",
    }


def excerpt_content(session, identifier, branch, already_pinned=()):
    record = _excerpt(session, identifier)
    if record is None or (identifier not in already_pinned and not _bound_to(session, record.run_id, branch)):
        raise KeyError("没有找到当前研发的固定读取摘录。")
    return _disclose_artifact(record)


def projection(session, identifier):
    field = "artifact_id" if _excerpt(session, identifier) else "evidence_id"
    return {field: identifier}


def scope_checked(session, identifier, branch, already_pinned=()):
    record = session.get(Evidence, identifier)
    artifact = _excerpt(session, identifier) if record is None else None
    if record is None and artifact is None:
        raise ValueError("没有找到读取记录，请先补读原文。")
    owner = record.root_id if record else artifact.run_id
    if identifier not in already_pinned and not _bound_to(session, owner, branch):
        raise ValueError("证据未在当前研究任务中读取或固定，请先补读原文。")
    result = describe(session, record) if record else artifact_description(artifact, branch.task_id)
    if not result["available"]:
        raise ValueError("证据对应的固定内容已不可读，请重新核对。")
    return result


def describe(session, record):
    result = {
        "id": record.id, "start": record.start_offset, "end": record.end_offset,
        "quote": record.quote[:220], "url": f"/evidence/{record.id}", "available": True,
    }
    try:
        if record.source_segment_id:
            segment = session.get(SourceSegment, record.source_segment_id)
            version = session.get(SourceVersion, segment.source_version_id)
            source = session.get(Source, version.source_id)
            SourceService._verified_body(session, version)
            result.update(
                kind="material", title=source.title, source_version_id=version.id,
                source_id=source.id, page=segment.page, available=version.status == "ready",
            )
        else:
            version = session.get(KnowledgeVersion, record.knowledge_version_id)
            result.update(kind="knowledge", title=version.title, knowledge_version_id=version.id,
                          document_id=version.document_id, version=version.number)
            expected = version.body[record.start_offset:record.end_offset]
            result["available"] = expected == record.quote
    except (KeyError, OSError, ValueError):
        result["available"] = False
    if not result["available"]:
        result.update(quote="", url=None)
    return result


def views(session, identifiers, *, task_id):
    items = []
    for identifier in identifiers:
        record = session.get(Evidence, identifier)
        artifact = _excerpt(session, identifier) if record is None else None
        if record:
            items.append(describe(session, record))
        elif artifact:
            items.append(artifact_description(artifact, task_id))
        else:
            items.append({"id": identifier, "available": False, "url": None, "quote": ""})
    return items
