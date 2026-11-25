"""会话记忆投影固定所选正文与可读取的修订目录；撤回仍查询实时记忆指针。"""

import hashlib
import json

from sqlalchemy import select, text

from knowledgeops_agent.app.lifecycle.access import require_conversation
from knowledgeops_agent.harness.context.tokens import count_text_tokens
from knowledgeops_agent.memory.retrieval import freeze
from knowledgeops_agent.persistence.models import (
    ConversationMemorySnapshot,
    LearningTask,
    Run,
    utcnow,
)


def _encode(entries):
    return json.dumps(entries, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _freeze_in(session, conversation_id, query, topic, *, refresh=False):
    if not refresh and (row := session.get(ConversationMemorySnapshot, conversation_id)):
        return row
    # 首次冻结与显式刷新走同一短锁，避免两个并发请求各自返回不同首版。
    session.execute(
        text("SELECT pg_advisory_xact_lock(748616062, hashtext(:id))"), {"id": conversation_id}
    )
    row = session.get(ConversationMemorySnapshot, conversation_id, populate_existing=True)
    if row is not None and not refresh:
        return row
    memory = freeze(session, query[:500], topic=topic)
    selected = {entry["id"]: entry for entry in memory["selected"]}
    entries = sorted(
        (selected.get(ref["id"], ref) for ref in memory["catalog"]),
        key=lambda entry: (entry.get("memory_slot", "user"), entry["id"]),
    )
    entries = json.loads(_encode(entries))
    if row is None:
        row = ConversationMemorySnapshot(conversation_id=conversation_id)
        session.add(row)
    row.frozen_entries = entries
    row.snapshot_hash = hashlib.sha256(_encode(entries).encode()).hexdigest()
    row.frozen_at = utcnow()
    session.flush()
    return row


def get_or_freeze_conversation_memory(session, *, conversation_id, query, topic):
    row = _freeze_in(session, conversation_id, query, topic)
    # JSONB 的字段返回顺序不是业务合同，显式规范化后再生成 Prompt。
    entries = json.loads(_encode(row.frozen_entries))
    selected = [entry for entry in entries if "content" in entry]
    return {
        "contract": "pg-memory-v1",
        "catalog": [
            {key: entry[key] for key in ("id", "revision", "revocation_epoch", "memory_slot")}
            for entry in entries
        ],
        "selected": selected,
        "snapshot_hash": row.snapshot_hash,
        "slots": {
            slot: [entry for entry in selected if entry["memory_slot"] == slot]
            for slot in ("user", "system")
        },
        "token_count": count_text_tokens(json.dumps(selected, ensure_ascii=False)),
        "token_budget": 2000,
    }


def refresh_conversation_memory_snapshot(session, conversation_id):
    conversation = require_conversation(session, conversation_id)
    if conversation.trial_case:
        raise ValueError("试用会话不使用长期记忆快照")
    latest = session.scalar(
        select(Run)
        .where(
            Run.conversation_id == conversation_id,
            Run.kind == "agent",
            Run.parent_id.is_(None),
            Run.lifecycle_state == "active",
        )
        .order_by(Run.created_at.desc(), Run.id.desc())
        .limit(1)
    )
    learning = (
        session.get(LearningTask, latest.learning_task_id)
        if latest and latest.learning_task_id
        else None
    )
    row = _freeze_in(
        session,
        conversation_id,
        latest.task if latest else "",
        learning.topic if learning else latest.input_data.get("memory_topic", "") if latest else "",
        refresh=True,
    )
    return {
        "snapshot_hash": row.snapshot_hash,
        "entry_count": sum("content" in entry for entry in row.frozen_entries),
    }


def conversation_memory_snapshot_view(session, conversation_id):
    conversation = require_conversation(session, conversation_id)
    row = (
        None
        if conversation.trial_case
        else session.get(ConversationMemorySnapshot, conversation_id)
    )
    return {
        "frozen": row is not None,
        "snapshot_hash": row.snapshot_hash if row else None,
        "entry_count": sum("content" in entry for entry in row.frozen_entries) if row else 0,
        "frozen_at": row.frozen_at.isoformat() if row else None,
    }
