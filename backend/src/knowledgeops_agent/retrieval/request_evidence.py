"""从实际模型输入提取联合检索已读证据，并在绑定时复查资料资格。"""

import json

from langchain_core.messages import ToolMessage
from sqlalchemy import select

from knowledgeops_agent.knowledge.citations import clickable_evidence_ids
from knowledgeops_agent.persistence.models import (
    Evidence,
    KnowledgeDocument,
    KnowledgeVersion,
    ModelStep,
)
from knowledgeops_agent.research.trial import run_trial_owner
from knowledgeops_agent.sources.service import is_current_search_segment


def _current_reference(session, root, item: dict, *, verify_text: bool) -> bool:
    identity = item.get("evidence_id")
    row = session.get(Evidence, identity) if isinstance(identity, str) else None
    if row is None or row.root_id != root.id:
        return False
    if verify_text and (
        row.quote != item.get("text") or row.start_offset != item.get("start")
        or row.end_offset != item.get("end")
    ):
        return False
    if row.source_segment_id:
        return (
            not root.input_data.get("knowledge_only") and item.get("kind") == "material"
            and is_current_search_segment(
                session, row.source_segment_id, root_id=root.id,
                source_ids=root.input_data.get("source_ids") or None,
            )
        )
    return item.get("kind") == "knowledge" and session.scalar(
        select(KnowledgeVersion.id)
        .join(KnowledgeDocument, KnowledgeDocument.id == KnowledgeVersion.document_id)
        .where(
            KnowledgeVersion.id == row.knowledge_version_id, KnowledgeVersion.archived.is_(False),
            KnowledgeDocument.current_version_id == KnowledgeVersion.id,
            KnowledgeDocument.deleted_at.is_(None),
            KnowledgeDocument.trial_conversation_id == run_trial_owner(session, root.id),
        )
    ) is not None


def prepare_reference_messages(session, root, messages, *, verify_text: bool = True):
    """只检查联合检索工具消息；被压缩掉的证据不会误计入本次模型输入。"""
    prepared, ids = [], set()
    for message in messages:
        if not isinstance(message, ToolMessage) or message.name != "search_references" or message.status == "error":
            prepared.append(message)
            continue
        try:
            result = json.loads(message.content) if isinstance(message.content, str) else {}
        except ValueError:
            result = {}
        if not isinstance(result, dict) or not isinstance(result.get("evidence"), list):
            result = {"evidence": [], "unavailable_reason": "引用已无法核验，请重新检索"}
        original = result["evidence"]
        kept = [
            item for item in original if isinstance(item, dict)
            and _current_reference(session, root, item, verify_text=verify_text)
        ]
        ids.update(item["evidence_id"] for item in kept)
        if len(kept) < len(original):
            result["unavailable_count"] = result.get("unavailable_count", 0) + len(original) - len(kept)
        result["evidence"] = kept
        prepared.append(message.model_copy(update={
            "content": json.dumps(result, ensure_ascii=False, separators=(",", ":")), "artifact": None,
        }))
    return prepared, sorted(ids)


def final_reference_ids(session, run_id: str, answer: str) -> list[str]:
    """用户看到的引用与已绑定证据求交集，三类诊断事实分别保存。"""
    cited = set(clickable_evidence_ids(answer))
    if not cited:
        return []
    bindings = session.scalars(select(ModelStep.request_snapshot["reference_evidence_ids"]).where(
        ModelStep.run_id == run_id, ModelStep.request_snapshot.has_key("reference_evidence_ids"),
    ))
    return sorted(cited.intersection(identity for ids in bindings for identity in ids))
