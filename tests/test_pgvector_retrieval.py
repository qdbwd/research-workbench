"""pgvector 派生向量索引、上下文增强嵌入、RRF 混合检索、可选重排与 API 隔离测试。"""

from __future__ import annotations

import pytest
from test_api_worker import client as _client
from test_api_worker import services as _services

from knowledgeops_agent.persistence.models import EmbeddingProfile, KnowledgeDocument
from knowledgeops_agent.retrieval.embeddings import (
    EmbeddingError,
    EmbeddingProfileSpec,
    OpenAICompatibleEmbeddingProvider,
    OpenAICompatibleRerankerProvider,
    RerankerError,
    build_contextual_text,
)
from knowledgeops_agent.retrieval.references import search_references
from knowledgeops_agent.retrieval.vector import VectorRetrievalService, rrf_fuse

services, client = _services, _client


class FakeEmbeddingProvider:
    """确定性离线嵌入替身：根据语义关键词映射到固定正交方向，零外部调用。"""

    def __init__(self, *, model: str = "fake-embed", dimensions: int = 4, fail: bool = False):
        self._spec = EmbeddingProfileSpec(provider="tei", model=model, dimensions=dimensions)
        self.fail = fail
        self.document_calls: list[list[str]] = []
        self.query_calls: list[str] = []

    @property
    def enabled(self) -> bool:
        return True

    @property
    def spec(self) -> EmbeddingProfileSpec:
        return self._spec

    def _encode(self, text: str) -> list[float]:
        dims = self._spec.dimensions or 4
        vec = [0.05] * dims
        lower = text.lower()
        # 维度 0: 回滚/灾备/降级语义簇
        if any(word in lower for word in ("回滚", "灾备", "撤回发布", "降级", "rollback")):
            vec[0] = 1.0
        # 维度 1: 权限/审批/门禁语义簇
        if any(word in lower for word in ("审批", "权限", "门禁", "批准", "approval")):
            vec[1] = 1.0
        # 维度 2: 监控/告警/指标语义簇
        if any(word in lower for word in ("监控", "告警", "指标", "延迟", "metrics")):
            vec[2] = 1.0
        if dims > 3 and any(word in lower for word in ("私有", "隔离", "会话")):
            vec[3] = 1.0
        norm = sum(x * x for x in vec) ** 0.5
        return [x / norm for x in vec]

    def embed_documents(self, texts):
        if self.fail:
            raise EmbeddingError("模拟嵌入服务超时")
        items = list(texts)
        self.document_calls.append(items)
        return [self._encode(text) for text in items]

    def embed_query(self, query: str) -> list[float]:
        if self.fail:
            raise EmbeddingError("模拟查询嵌入超时")
        self.query_calls.append(query)
        return self._encode(query)

    def describe(self) -> dict:
        return {
            "provider": "tei",
            "configured": True,
            "model": self._spec.model,
            "dimensions": self._spec.dimensions,
            "endpoint": "http://fake-tei:80",
            "normalize": True,
            "query_prefix": "",
        }


class FakeRerankerProvider:
    """确定性重排替身：把包含指定关键词的候选排到最前，可模拟失败降级。"""

    def __init__(self, *, prefer_keyword: str = "值班手册", fail: bool = False):
        self.prefer_keyword = prefer_keyword
        self.fail = fail
        self.calls: int = 0

    @property
    def enabled(self) -> bool:
        return True

    def rerank(self, query: str, documents, *, top_n: int | None = None):
        self.calls += 1
        if self.fail:
            raise RerankerError("模拟重排服务超时")
        scored = [
            (idx, 10.0 if self.prefer_keyword in doc else 1.0 / (idx + 1))
            for idx, doc in enumerate(documents)
        ]
        scored.sort(key=lambda pair: (-pair[1], pair[0]))
        return scored[:top_n] if top_n is not None else scored

    def describe(self) -> dict:
        return {
            "provider": "tei",
            "configured": True,
            "model": "fake-reranker",
            "endpoint": "http://fake-rerank:80",
        }


def _publish_doc(
    services, *, root_id: str = "pgvector-seed", title: str, body: str, summary: str = "", tags: list[str] | None = None
):
    services.knowledge.grant_direct(root_id)
    draft = services.knowledge.create_draft(
        root_id,
        [
            {
                "action": "create",
                "title": title,
                "body": body,
                "summary": summary,
                "tags": tags or [],
                "category": "ops",
            }
        ],
    )
    receipt = services.knowledge.publish(draft["id"], idempotency_key=f"pub-{title}")
    return services.knowledge.get_document(receipt["document_ids"][0])


def test_contextual_text_and_rrf_fusion_unit():
    ctx = build_contextual_text(
        "执行切流并检查错误率。",
        title="生产发布手册",
        summary="变更后核验清单",
        tags=["发布", "值班"],
        category="ops",
        contextual=True,
    )
    assert "标题：生产发布手册" in ctx
    assert "标签：发布、值班" in ctx
    assert ctx.endswith("执行切流并检查错误率。")
    assert build_contextual_text("正文", title="标题", contextual=False) == "正文"

    fused = rrf_fuse(
        [{"id": "a", "title": "A"}, {"id": "b", "title": "B"}],
        [{"id": "c", "title": "C"}, {"id": "a", "title": "A"}],
        key_fn=lambda row: (row["id"],),
        k=60,
        limit=3,
    )
    assert [row["id"] for row in fused] == ["a", "c", "b"]
    assert fused[0]["retrieval_mode"] == "hybrid"
    assert fused[1]["retrieval_mode"] == "vector"
    assert fused[2]["retrieval_mode"] == "bm25"


def test_paid_provider_guardrail_blocks_when_model_calls_disabled():
    emb = OpenAICompatibleEmbeddingProvider(
        base_url="https://api.example.com/v1",
        api_key="sk-test",
        model="text-embedding-3-small",
        model_calls_enabled=False,
    )
    assert emb.enabled is False
    assert emb.describe()["gated_by_model_calls"] is True
    with pytest.raises(EmbeddingError, match="MODEL_CALLS_ENABLED=false"):
        emb.embed_query("test")

    rerank = OpenAICompatibleRerankerProvider(
        base_url="https://api.example.com/v1",
        api_key="sk-test",
        model="rerank-v1",
        model_calls_enabled=False,
    )
    assert rerank.enabled is False
    assert rerank.describe()["gated_by_model_calls"] is True
    with pytest.raises(RerankerError, match="MODEL_CALLS_ENABLED=false"):
        rerank.rerank("query", ["doc1", "doc2"])


def test_pgvector_contextual_indexing_hybrid_rrf_rerank_and_isolation(services):
    # 1. 发布两篇知识文档 + 两个不同会话的材料
    doc_rollback = _publish_doc(
        services,
        title="紧急撤回发布规程",
        summary="线上故障应急处理",
        tags=["回滚", "灾备"],
        body="出现严重故障时，先冻结流量并执行撤回发布步骤，再核对数据库状态。",
    )
    _publish_doc(
        services,
        title="变更审批门禁",
        summary="发布前审批要求",
        tags=["审批"],
        body="所有生产变更必须经过双人审批和门禁校验后才能放行。",
    )

    conv_a = services.jobs.create_conversation(title="会话 A")
    conv_b = services.jobs.create_conversation(title="会话 B")
    run_a = services.jobs.enqueue(
        conversation_id=conv_a["id"],
        task="查询",
        request_key="rag-run-a",
    )
    run_b = services.jobs.enqueue(
        conversation_id=conv_b["id"],
        task="查询",
        request_key="rag-run-b",
    )
    src_a = services.sources.import_text(
        "值班手册与回滚清单",
        "发现异常延迟时立即按值班手册执行灾备降级与回滚核查。",
        conversation_id=conv_a["id"],
    )
    services.sources.import_text(
        "会话B私有回滚记录",
        "会话B的私有灾备回滚演练记录，不应泄露给会话A。",
        conversation_id=conv_b["id"],
    )

    embedder = FakeEmbeddingProvider(model="fake-harrier", dimensions=4)
    reranker = FakeRerankerProvider(prefer_keyword="值班手册")
    vector = VectorRetrievalService(
        services.sessions,
        embedding_provider=embedder,
        reranker_provider=reranker,
    )

    # 2. 增量构建向量索引（带上下文增强）并验证幂等跳过
    first_build = vector.rebuild_embeddings(contextual=True, activate=True)
    assert first_build["knowledge_embedded"] >= 2
    assert first_build["source_embedded"] >= 2
    assert any("标题：紧急撤回发布规程" in text for batch in embedder.document_calls for text in batch)

    second_build = vector.rebuild_embeddings(contextual=True, activate=True)
    assert second_build["knowledge_embedded"] == 0
    assert second_build["knowledge_skipped"] == first_build["knowledge_embedded"]
    assert second_build["source_embedded"] == 0
    assert second_build["source_skipped"] == first_build["source_embedded"]

    # 3. 验证默认关闭混合检索时只走 BM25；开启混合检索后用同义词 "rollback"（BM25 未直接命中中文）可由向量召回
    bm25_only = search_references(
        services.knowledge, services.sources, "rollback", root_id=run_a["id"], vector=vector, limit=5
    )
    assert bm25_only == []

    current_rev = vector.get_settings()["revision"]
    assert current_rev == 1
    status = vector.update_settings(expected_revision=current_rev, hybrid_enabled=True, rerank_enabled=False)
    assert status["effective_mode"] == "hybrid"
    assert (
        search_references(
            services.knowledge, services.sources, "   ", root_id=run_a["id"], vector=vector, limit=5
        )
        == []
    )

    hybrid_hits = search_references(
        services.knowledge, services.sources, "rollback", root_id=run_a["id"], vector=vector, limit=5
    )
    assert len(hybrid_hits) >= 2
    # 严格验证跨会话隔离：会话 A 绝不能召回会话 B 的私有材料
    returned_titles = {hit["title"] for hit in hybrid_hits}
    assert "紧急撤回发布规程" in returned_titles
    assert "值班手册与回滚清单" in returned_titles
    assert "会话B私有回滚记录" not in returned_titles
    assert reranker.calls == 0  # 未开启重排时不调用 Reranker

    # 4. 验证精确字符偏移对齐原文
    k_hit = next(hit for hit in hybrid_hits if hit["kind"] == "knowledge")
    assert k_hit["version_id"] == doc_rollback["version_id"]
    read_back = services.knowledge.read_document(
        k_hit["asset_id"],
        root_id=run_a["id"],
        version_id=k_hit["version_id"],
        start=k_hit["start"],
        length=k_hit["end"] - k_hit["start"],
        current_only=True,
    )
    assert k_hit["snippet"] == read_back["text"]

    # 5. 开启可选重排：值班手册应被排至第一位
    vector.update_settings(expected_revision=status["settings"]["revision"], rerank_enabled=True)
    reranked_hits = search_references(
        services.knowledge, services.sources, "rollback", root_id=run_a["id"], vector=vector, limit=5
    )
    assert reranker.calls == 1
    assert reranked_hits[0]["title"] == "值班手册与回滚清单"
    assert reranked_hits[0]["reranked"] is True

    # 6. 重排服务故障时平滑退回 RRF，不抛错
    reranker.fail = True
    degraded_rerank = search_references(
        services.knowledge, services.sources, "rollback", root_id=run_a["id"], vector=vector, limit=5
    )
    assert len(degraded_rerank) >= 2
    assert all(not hit.get("reranked") for hit in degraded_rerank)

    # 7. 嵌入服务故障时平滑退回纯 BM25，不抛错
    embedder.fail = True
    degraded_bm25 = search_references(
        services.knowledge, services.sources, "双人审批", root_id=run_a["id"], vector=vector, limit=5
    )
    assert any(hit["title"] == "变更审批门禁" for hit in degraded_bm25)
    embedder.fail = False
    reranker.fail = False

    # 8. 多维度 profile 共存不冲突（4 维与 8 维）
    embedder_8d = FakeEmbeddingProvider(model="fake-8d", dimensions=8)
    vector_8d = VectorRetrievalService(services.sessions, embedding_provider=embedder_8d)
    build_8d = vector_8d.rebuild_embeddings(activate=False)
    assert build_8d["dimensions"] == 8
    profiles = {p["profile_id"]: p for p in vector.status()["profiles"]}
    assert first_build["profile_id"] in profiles and build_8d["profile_id"] in profiles

    # 9. 未就绪（building）档案不会被在线检索使用
    with services.sessions.begin() as session:
        session.get(EmbeddingProfile, first_build["profile_id"]).status = "building"
    assert vector.status()["effective_mode"] == "bm25_only"
    with services.sessions.begin() as session:
        session.get(EmbeddingProfile, first_build["profile_id"]).status = "ready"

    # 10. 文档删除后向量候选立刻不可见，且覆盖率分子同步排除已删除文档（不会 > 100%）
    with services.sessions.begin() as session:
        doc = session.get(KnowledgeDocument, doc_rollback["document_id"])
        doc.deleted_at = doc.created_at
    cov_after_delete = vector.status()["coverage"]
    assert cov_after_delete["knowledge_embedded_chunks"] <= cov_after_delete["knowledge_total_chunks"]
    assert cov_after_delete["ratio"] <= 1.0
    after_delete = search_references(
        services.knowledge, services.sources, "rollback", root_id=run_a["id"], vector=vector, limit=5
    )
    assert all(hit["asset_id"] != doc_rollback["document_id"] for hit in after_delete)
    assert src_a["id"] in {hit["asset_id"] for hit in after_delete}
    b_hits = search_references(
        services.knowledge, services.sources, "rollback", root_id=run_b["id"], vector=vector, limit=5
    )
    assert any(hit["title"] == "会话B私有回滚记录" for hit in b_hits)


def test_retrieval_http_api_status_settings_rebuild_and_profile_lifecycle(services, client):
    embedder = FakeEmbeddingProvider(model="api-embed", dimensions=4)
    services.__dict__["vector"] = VectorRetrievalService(
        services.sessions,
        embedding_provider=embedder,
        reranker_provider=FakeRerankerProvider(),
    )

    initial = client.get("/api/retrieval/status")
    assert initial.status_code == 200, initial.text
    body = initial.json()
    assert body["pgvector"]["installed"] is True
    assert body["settings"]["hybrid_enabled"] is False
    assert body["effective_mode"] == "bm25_only"

    _publish_doc(services, title="API 向量测试文档", body="用于验证检索接口的向量重建与状态统计。")

    rebuilt = client.post("/api/retrieval/rebuild", json={"contextual": True, "activate": True})
    assert rebuilt.status_code == 200, rebuilt.text
    profile_id = rebuilt.json()["summary"]["profile_id"]
    assert rebuilt.json()["summary"]["knowledge_embedded"] >= 1

    # 更新设置 + 乐观并发修订冲突检查
    rev = rebuilt.json()["status"]["settings"]["revision"]
    updated = client.patch(
        "/api/retrieval/settings",
        json={"expected_revision": rev, "hybrid_enabled": True, "rerank_enabled": True, "rrf_k": 50},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["effective_mode"] == "hybrid_rerank"
    assert updated.json()["settings"]["rrf_k"] == 50

    conflict = client.patch(
        "/api/retrieval/settings",
        json={"expected_revision": rev, "hybrid_enabled": False},
    )
    assert conflict.status_code == 409

    # 启用中不能删除活跃档案
    refused = client.delete(f"/api/retrieval/profiles/{profile_id}")
    assert refused.status_code == 409

    # 关闭混合检索后可安全删除档案
    turned_off = client.patch(
        "/api/retrieval/settings",
        json={"expected_revision": updated.json()["settings"]["revision"], "hybrid_enabled": False},
    )
    assert turned_off.status_code == 200
    deleted = client.delete(f"/api/retrieval/profiles/{profile_id}")
    assert deleted.status_code == 200
    assert deleted.json()["deleted_profile_id"] == profile_id
    assert deleted.json()["status"]["profiles"] == []
    assert deleted.json()["status"]["settings"]["active_profile_id"] is None
    after_del_rev = deleted.json()["status"]["settings"]["revision"]
    assert after_del_rev == turned_off.json()["settings"]["revision"] + 1
    # 删除当前档案后用返回的新 revision 保存设置不会触发 404 或 409
    resaved = client.patch(
        "/api/retrieval/settings",
        json={"expected_revision": after_del_rev, "rrf_k": 55, "active_profile_id": None},
    )
    assert resaved.status_code == 200, resaved.text
    assert resaved.json()["settings"]["rrf_k"] == 55


def test_security_redaction_and_vector_literal_guards():
    from knowledgeops_agent.persistence.models import format_pgvector
    from knowledgeops_agent.retrieval.embeddings import _safe_endpoint_summary

    assert _safe_endpoint_summary("https://admin:s3cr3t@tei.internal:8080/v1/embed?key=1") == "https://tei.internal:8080"
    assert _safe_endpoint_summary("http://[::1]:8080/embed") == "http://[::1]:8080"
    with pytest.raises(ValueError, match="finite"):
        format_pgvector([0.1, float("nan"), 0.2])
    with pytest.raises(ValueError, match="finite"):
        format_pgvector([float("inf")])


def test_migration_0061_upgrade_and_downgrade(monkeypatch):
    import os
    from uuid import uuid4

    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, text

    url = os.environ["TEST_DATABASE_URL"]
    schema = "mig61_" + uuid4().hex[:8]
    eng = create_engine(url)
    with eng.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_search WITH SCHEMA public CASCADE"))
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public CASCADE"))
        conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    try:
        monkeypatch.setenv("DATABASE_URL", f"{url}?options=-csearch_path%3D{schema}")
        cfg = Config("alembic.ini")
        command.upgrade(cfg, "0061")
        command.downgrade(cfg, "0060")
        command.upgrade(cfg, "0061")
        with eng.connect() as conn:
            tables = set(
                conn.scalars(
                    text("SELECT tablename FROM pg_tables WHERE schemaname = :s"),
                    {"s": schema},
                )
            )
        assert {
            "embedding_profiles",
            "rag_settings",
            "knowledge_chunk_embeddings",
            "source_chunk_embeddings",
        } <= tables
    finally:
        with eng.begin() as conn:
            conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        eng.dispose()
