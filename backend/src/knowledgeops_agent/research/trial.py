"""受控示例夹具。只在明确创建试用会话时写入独立的固定版本。"""

import json
from functools import cache

from sqlalchemy import select

from knowledgeops_agent.core.resources import bundled_resource
from knowledgeops_agent.persistence.models import (
    Conversation,
    KnowledgeDocument,
    KnowledgeVersion,
    Run,
    new_id,
)
from knowledgeops_agent.retrieval.index import add_knowledge_index

CASE = "operations-pilot-v1"
FAILURE_LOG_TITLE = "试点问答失败记录"


@cache
def _case(key: str) -> dict:
    if key != CASE:
        raise ValueError("这个试用案例版本不可用。")
    return json.loads(bundled_resource(f"config/research-trial/{CASE}.json").read_text(encoding="utf-8"))


def trial_catalog(key: str = CASE) -> dict:
    """只有展示字段可进空白会话。留出题和评测标注不进入模型语料。"""
    case = _case(key)
    return {
        "key": key, "title": case["title"], "notice": case["notice"],
        "prompts": case["prompts"],
        "knowledge": [entry["title"] for entry in case["knowledge"]],
        "materials": [*(entry["title"] for entry in case["materials"]), FAILURE_LOG_TITLE],
    }


@cache
def completed_trial() -> dict | None:
    """只读随版本发布的模拟案例，独立于任何会话和运行库。"""
    path = bundled_resource(f"config/research-trial/{CASE}.completed.json")
    if not path.is_file():
        return None
    case = json.loads(path.read_text(encoding="utf-8"))
    if case["schema_version"] != 1 or case["key"] != CASE:
        raise ValueError("已完成案例与当前版本不匹配。")
    return case


def run_trial_owner(session, root_id: str | None) -> str | None:
    if root_id is None:
        return None
    return session.scalar(select(Conversation.id).join(Run, Run.conversation_id == Conversation.id).where(
        Run.id == root_id, Conversation.trial_case.is_not(None),
    ))


def seed_trial(session, conversation: Conversation, sources) -> None:
    case = _case(conversation.trial_case)
    store = session.info.get("file_store")
    for entry in case["knowledge"]:
        document = KnowledgeDocument(id=new_id(), trial_conversation_id=conversation.id)
        session.add(document)
        session.flush()
        for edition in entry["versions"]:
            version = KnowledgeVersion(
                id=new_id(), document_id=document.id, number=edition["number"], title=entry["title"],
                body=edition["body"], tags=[], summary="", category="模拟已发布知识",
                action="create" if edition["number"] == 1 else "update", root_id=conversation.id,
            )
            if store is not None:
                version.body_storage_key = f"knowledge/versions/{version.id}.md"
                version.body_sha256 = store.put(version.body_storage_key, edition["body"], session=session, purpose="knowledge")
                version.body = ""
            session.add(version)
            session.flush()
        document.current_version_id = version.id
        add_knowledge_index(session, version)

    failures = "\n\n".join(
        f"## {item['question']}\n候选：{', '.join(item['candidates'])}\n"
        f"送入模型：{item['context']}\n回答：{item['answer']}"
        for item in case["failures"]
    )
    for entry in [*case["materials"], {"title": FAILURE_LOG_TITLE, "body": failures}]:
        sources.import_text(entry["title"], entry["body"], conversation_id=conversation.id, session=session)
