"""试用走真实会话创建、知识与材料索引，不把样例混入个人知识库。"""

import pytest
from sqlalchemy import func, select

from knowledgeops_agent.app.runs.service import RunService
from knowledgeops_agent.knowledge.service import KnowledgeService
from knowledgeops_agent.persistence.models import Conversation, KnowledgeDocument, Source
from knowledgeops_agent.research.trial import trial_catalog
from knowledgeops_agent.retrieval.references import search_references
from knowledgeops_agent.sources.service import SourceService


def test_catalog_is_read_only_and_trial_forks_both_retrieval_lanes(business_sessions, tmp_path):
    jobs = RunService(business_sessions)
    knowledge = KnowledgeService(business_sessions)
    sources = SourceService(business_sessions, tmp_path)
    catalog = trial_catalog()
    assert [item["mode"] for item in catalog["prompts"]] == ["chat", "chat", "research"]
    with business_sessions() as session:
        assert session.scalar(select(func.count()).select_from(Conversation)) == 0

    options = {"trial_case": catalog["key"], "sources": sources}
    left = jobs.create_conversation("试点问答", request_key="first", **options)
    assert jobs.create_conversation("试点问答", request_key="first", **options) == left
    with pytest.raises(ValueError):
        jobs.create_conversation("试点问答", request_key="first")
    right = jobs.create_conversation("另一次试点", request_key="second", **options)
    normal = jobs.create_conversation("普通会话")
    assert knowledge.list_documents() == []
    assert sources.list_sources() == []
    assert sources.list_sources(conversation_id=normal["id"]) == []
    sources.import_text("真实运维记录", "紧急变更与错误率探针，以实际数据为准。")

    def hits(chat, key):
        run = jobs.enqueue(chat["id"], "核对紧急变更审批和错误率探针", request_key=key)
        return run["id"], search_references(
            knowledge, sources, "紧急变更 审批 错误率探针", root_id=run["id"],
        )

    root, a = hits(left, "a")
    other, b = hits(right, "b")
    ordinary, live = hits(normal, "ordinary")
    assert {hit["kind"] for hit in a} == {"knowledge", "material"}
    assert {hit["title"] for hit in a} == {hit["title"] for hit in b}
    assert {hit["asset_id"] for hit in a}.isdisjoint(hit["asset_id"] for hit in b)
    assert {hit["title"] for hit in live} == {"真实运维记录"}
    assert len(sources.list_sources(conversation_id=left["id"])) == len(catalog["materials"])
    procedure = next(hit for hit in a if hit["title"] == "生产紧急变更规程")
    assert procedure["version"] == 2
    prior = next(row for row in knowledge.history(procedure["asset_id"], root_id=root) if row["version"] == 1)
    assert "事后补签" in knowledge.read_document(
        procedure["asset_id"], root_id=root, version_id=prior["version_id"],
    )["text"]
    for foreign in (other, ordinary):
        with pytest.raises(KeyError):
            knowledge.read_document(procedure["asset_id"], root_id=foreign)
    material = next(hit for hit in a if hit["kind"] == "material")
    with pytest.raises(ValueError):
        sources.read_segment(material["segment_id"], root_id=other)
    with pytest.raises(ValueError):
        sources.save_source(material["asset_id"])
    with pytest.raises(KeyError):
        sources.get_source(material["asset_id"], root_id=other)


def test_trial_creation_failure_does_not_leave_a_partial_conversation(business_sessions, tmp_path, monkeypatch):
    jobs = RunService(business_sessions)
    sources = SourceService(business_sessions, tmp_path)
    options = {"trial_case": trial_catalog()["key"], "sources": sources}
    original_import = sources.import_text

    def fail_after_import(*args, **kwargs):
        original_import(*args, **kwargs)
        raise RuntimeError("模拟材料装载中断")

    with monkeypatch.context() as patch:
        patch.setattr(sources, "import_text", fail_after_import)
        with pytest.raises(RuntimeError, match="模拟材料装载中断"):
            jobs.create_conversation("试点", request_key="trial-retry", **options)
    assert jobs.creation_receipt("trial-retry") == {"state": "not_accepted"}
    with business_sessions() as session:
        assert session.scalar(select(KnowledgeDocument.id).limit(1)) is None
        assert session.scalar(select(Source.id).limit(1)) is None
    created = jobs.create_conversation("试点", request_key="trial-retry", **options)
    assert jobs.creation_receipt("trial-retry")["conversation_id"] == created["id"]
    assert len(sources.list_sources(conversation_id=created["id"])) == len(trial_catalog()["materials"])
