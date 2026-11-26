"""RAG 工作台使用真实隔离 PostgreSQL 与确定性供应商替身。"""

from test_pgvector_retrieval import FakeEmbeddingProvider, _publish_doc
from test_pgvector_retrieval import client as _client
from test_pgvector_retrieval import services as _services
from test_runtime import runtime_services as _runtime_services

from knowledgeops_agent.retrieval.vector import VectorRetrievalService

client, services, runtime_services = _client, _services, _runtime_services


def test_workbench_permissions_chunk_toggle_and_hit_test(client, services, business_sessions):
    from sqlalchemy import func, select
    from test_pgvector_retrieval import FakeRerankerProvider

    from knowledgeops_agent.persistence.models import Evidence, Run

    with business_sessions() as session:
        counts = (session.scalar(select(func.count(Evidence.id))), session.scalar(select(func.count(Run.id))))
    provider = FakeEmbeddingProvider()
    services.vector = vector = VectorRetrievalService(services.sessions, embedding_provider=provider)
    vector.update_settings(expected_revision=0, hybrid_enabled=True)
    article = _publish_doc(services, title="回滚审核", body="回滚审核需要值班负责人批准。")
    chat = services.jobs.create_conversation()
    private = services.sources.import_text("回滚私密材料", "回滚私密资料。", conversation_id=chat["id"])
    assert provider.document_calls == []

    base = "/api/retrieval"
    created = client.post(base + "/collections", json={"name": " 运维 "})
    assert created.status_code == 201
    collection = created.json()
    path = base + "/collections/" + collection["id"]
    assert client.put(path + "/items", json={"expected_revision": 1, "items": [
        {"asset_kind": "source", "asset_id": private["id"]},
    ]}).status_code == 403
    membership = client.put(path + "/items", json={"expected_revision": 1, "items": [
        {"asset_kind": "knowledge", "asset_id": article["id"]},
    ]})
    assert membership.status_code == 200
    assert client.delete(path, params={"expected_revision": 1}).status_code == 409

    item = client.get(base + "/assets").json()["items"][0]
    assert item["index_status"] == "pending"
    vector.rebuild_embeddings()
    chunks_path = base + "/assets/knowledge/" + article["id"] + "/chunks"
    chunks = client.get(chunks_path).json()
    first = chunks["items"][0]
    args = {"version_id": chunks["version_id"], "chunk_index": first["chunk_index"]}
    stale = client.patch(chunks_path + "/status", json={**args, "version_id": "stale", "enabled": False})
    assert stale.status_code == 409
    for enabled in (False, True):
        response = client.patch(chunks_path + "/status", json={**args, "enabled": enabled})
        assert response.status_code == 200
        for mode in ("bm25", "vector", "hybrid", "hybrid_rerank"):
            result = client.post(base + "/hit-test", json={
                "query": "回滚审核", "mode": mode, "collection_ids": [collection["id"]], "min_vector_similarity": 0.0,
            }).json()
            assert set(result["timing_ms"]) == {"total_ms", "bm25_ms", "embed_ms", "vector_ms", "rrf_ms", "rerank_ms"}
            if mode == "hybrid_rerank":
                assert result["effective_mode"] == "hybrid" and result["degraded_reason"]
            assert bool(result["hits"]) is enabled
            if enabled:
                assert {hit["asset_id"] for hit in result["hits"]} == {article["id"]}
    vector.reranker_provider = FakeRerankerProvider()
    ranked = client.post(base + "/hit-test", json={"query": "回滚审核", "mode": "hybrid_rerank"}).json()
    assert ranked["effective_mode"] == "hybrid_rerank" and ranked["hits"][0]["scores"]["rerank"] is not None
    assert all(hit["asset_id"] != private["id"] for hit in ranked["hits"])
    with business_sessions() as session:
        assert (session.scalar(select(func.count(Evidence.id))), session.scalar(select(func.count(Run.id)))) == counts
    assert client.delete(path, params={"expected_revision": 2}).status_code == 200
    assert client.get(chunks_path).json()["items"][0]["vector_status"] == "ready"


def test_hit_testing_interleaves_bm25_lanes(client, services, monkeypatch):
    article = _publish_doc(services, title="回滚规程", body="部署回滚需要保留快照。")
    first = services.sources.import_text("回滚手册", "回滚前检查监控告警。")
    second = services.sources.import_text("回滚记录", "回滚后登记故障原因。")
    query = "回滚"
    search_knowledge = services.knowledge.search
    search_sources = services.sources.search

    # 保留真实检索命中，明确构造两条通道的分值尺度差异。
    def knowledge_lane(*args, **kwargs):
        return [{**hit, "score": 0.1} for hit in search_knowledge(*args, **kwargs)]

    def material_lane(*args, **kwargs):
        scores = {first["id"]: 30.0, second["id"]: 20.0}
        return sorted(
            ({**hit, "score": scores[hit["source_id"]]} for hit in search_sources(*args, **kwargs)),
            key=lambda hit: -hit["score"],
        )

    monkeypatch.setattr(services.knowledge, "search", knowledge_lane)
    monkeypatch.setattr(services.sources, "search", material_lane)
    response = client.post("/api/retrieval/hit-test", json={"query": query, "mode": "bm25"})
    assert response.status_code == 200
    hits = response.json()["hits"]
    assert [hit["asset_id"] for hit in hits] == [article["id"], first["id"], second["id"]]
    assert [hit["scores"]["bm25_rank"] for hit in hits] == [1, 2, 3]


def test_hit_testing_filters_nonfinite_and_sorts_reranker_results(client, services):
    from test_pgvector_retrieval import FakeRerankerProvider

    for topic in ("日志", "告警", "快照", "权限", "时限"):
        services.sources.import_text(f"回滚{topic}", f"回滚操作，核对{topic}。")
    vector = VectorRetrievalService(services.sessions, embedding_provider=FakeEmbeddingProvider())
    services.vector = vector
    vector.rebuild_embeddings()
    url = "/api/retrieval/hit-test"
    options = {"query": "回滚", "top_k": 5, "scope": "material"}
    hybrid = client.post(url, json={**options, "mode": "hybrid"})
    assert hybrid.status_code == 200
    candidate_ids = [hit["asset_id"] for hit in hybrid.json()["hits"]]
    assert len(candidate_ids) == 5

    class UnsortedReranker(FakeRerankerProvider):
        def rerank(self, query, documents, *, top_n=None):
            return [(3, 0.4), (4, float("inf")), (2, 0.8), (0, float("nan")), (1, 0.8)]

    vector.reranker_provider = UnsortedReranker()
    response = client.post(url, json={**options, "mode": "hybrid_rerank"})
    assert response.status_code == 200
    result = response.json()
    assert result["effective_mode"] == "hybrid_rerank"
    assert [hit["asset_id"] for hit in result["hits"]] == [candidate_ids[i] for i in (1, 2, 3, 0, 4)]
    assert [hit["scores"]["rerank"] for hit in result["hits"]] == [0.8, 0.8, 0.4, None, None]


def test_publish_import_pdf_and_archive_queue_transaction(services, business_sessions):
    from sqlalchemy import select
    from test_pdf_sources import text_pdf

    from knowledgeops_agent.persistence.models import RagIndexJob
    from knowledgeops_agent.sources.pdf import parse_pdf

    vector = services.vector
    vector.update_settings(expected_revision=0, hybrid_enabled=True, auto_index_enabled=False)
    disabled = services.sources.import_text("自动入队关闭", "入队开关关闭时的材料")
    with business_sessions() as session:
        assert session.scalar(select(RagIndexJob.id).where(RagIndexJob.asset_id == disabled["id"])) is None
    vector.update_settings(expected_revision=1, hybrid_enabled=False, auto_index_enabled=True)
    hybrid_off = services.sources.import_text("混合检索关闭", "混合检索关闭时的另一份材料")
    vector.update_settings(expected_revision=2, hybrid_enabled=True)
    source = services.sources.import_text("源材料", "回滚路径")
    pdf = services.sources.register_pdf("原件.pdf", text_pdf())
    parse_pdf(services.sources, pdf["version_id"])
    article = _publish_doc(services, title="自动索引文章", body="回滚路径需要人工审核。")
    with business_sessions() as session:
        jobs = {job.asset_id: job for job in session.scalars(select(RagIndexJob))}
        assert set(jobs) == {source["id"], pdf["id"], article["id"]}
        assert hybrid_off["id"] not in jobs
        assert all(job.status == "pending" for job in jobs.values())

    def change(action, current, **details):
        root = f"fixture-{action}"
        services.knowledge.grant_direct(root)
        draft = services.knowledge.create_draft(root, [{
            "action": action, "document_id": article["id"], "base_version_id": current, **details,
        }])
        receipt = services.knowledge.publish(draft["id"], idempotency_key=root)
        return receipt["version_ids"][0]

    updated = change("update", article["version_id"], title="自动索引文章", body="新的回滚路径。")
    with business_sessions() as session:
        job = session.scalar(select(RagIndexJob).where(RagIndexJob.asset_id == article["id"]))
        assert (job.generation, job.version_id, job.status) == (2, updated, "pending")
    archived = change("archive", updated)
    with business_sessions() as session:
        assert session.scalar(select(RagIndexJob.id).where(RagIndexJob.asset_id == article["id"])) is None
    restored = change("restore", archived, restore_version_id=article["version_id"])
    with business_sessions() as session:
        job = session.scalar(select(RagIndexJob).where(RagIndexJob.asset_id == article["id"]))
        assert (job.version_id, job.status) == (restored, "pending")


def test_worker_lease_generation_retry_and_restart(client, services, business_sessions):
    from datetime import timedelta

    from sqlalchemy import select

    from knowledgeops_agent.app.runs.worker import index_rag_asset
    from knowledgeops_agent.persistence.models import RagIndexJob, SourceChunkEmbedding, utcnow
    from knowledgeops_agent.retrieval.embeddings import (
        EmbeddingError,
        OpenAICompatibleEmbeddingProvider,
    )
    from knowledgeops_agent.retrieval.indexing import (
        RagIndexPublisher,
        recover_interrupted_rag_jobs,
    )

    services.vector.update_settings(expected_revision=0, hybrid_enabled=True)
    data = services.sources.import_text("生产手册", "回滚审核旧正文", kind="web", origin="https://example.test/guide")

    def read_job():
        with business_sessions() as session:
            return session.scalar(select(RagIndexJob).where(RagIndexJob.asset_id == data["id"]))

    class Broker:
        def __init__(self):
            self.sent = []
            self.fail = True

        def send_task(self, *args, **kwargs):
            if self.fail:
                raise ConnectionError("redis://user:hidden-secret@internal")
            self.sent.append((args, kwargs))

    def make_due():
        with business_sessions.begin() as session:
            job = session.get(RagIndexJob, read_job().id)
            job.next_attempt_at = utcnow() - timedelta(seconds=1)
            job.lease_until = None

    broker = Broker()
    publisher = RagIndexPublisher(business_sessions, broker)
    assert publisher.publish_due() == 0
    assert read_job().last_error == "ConnectionError"
    make_due()
    broker.fail = False
    assert publisher.publish_due() == 1
    assert publisher.publish_due() == 0
    assert broker.sent[0][1]["task_id"] == f"rag:{read_job().id}:1"

    class ChangingProvider(FakeEmbeddingProvider):
        def embed_documents(self, texts):
            vectors = super().embed_documents(texts)
            services.sources.import_text(
                "生产手册", "回滚审核新正文", kind="web", origin="https://example.test/guide",
            )
            return vectors

    services.vector = VectorRetrievalService(business_sessions, embedding_provider=ChangingProvider())
    assert index_rag_asset(read_job().id, 1, services=services) is None
    assert (read_job().status, read_job().generation) == ("pending", 2)
    with business_sessions() as session:
        assert not list(session.scalars(select(SourceChunkEmbedding)))

    fake = FakeEmbeddingProvider()
    services.vector = VectorRetrievalService(business_sessions, embedding_provider=fake)
    assert index_rag_asset(read_job().id, 2, services=services)["status"] == "ready"
    assert index_rag_asset(read_job().id, 2, services=services) is None
    assert len(fake.document_calls) == 1
    chunks = f"/api/retrieval/assets/source/{data['id']}/chunks"
    material = client.get(chunks).json()
    part = material["items"][0]
    toggle = {"version_id": material["version_id"], "chunk_index": part["chunk_index"],
              "segment_id": part["segment_id"]}
    for enabled in (False, True):
        assert client.patch(chunks + "/status", json={**toggle, "enabled": enabled}).status_code == 200
        for mode in ("bm25", "vector", "hybrid"):
            hit = client.post("/api/retrieval/hit-test", json={
                "query": "回滚审核", "scope": "material", "mode": mode, "min_vector_similarity": 0.0,
            }).json()
            assert hit["effective_mode"] == mode
            assert bool(hit["hits"]) is enabled
    assert len(fake.document_calls) == 1
    settings = services.vector.get_settings()
    services.vector.update_settings(expected_revision=settings["revision"], auto_index_enabled=False)
    path = f"/api/retrieval/assets/source/{data['id']}/reindex"
    response = client.post(path)
    assert response.status_code == 202
    assert index_rag_asset(read_job().id, 3, services=services)["status"] == "ready"
    assert len(fake.document_calls) == 1

    class Unavailable(FakeEmbeddingProvider):
        def embed_documents(self, texts):
            raise EmbeddingError("暂时失败 https://user:hidden-secret@internal")

    assert client.post(path).status_code == 202
    services.vector = VectorRetrievalService(business_sessions, embedding_provider=Unavailable(model="new-profile"))
    for attempts, status in ((1, "pending"), (2, "pending"), (3, "failed")):
        result = index_rag_asset(read_job().id, 4, services=services)
        assert (result["attempts"], result["status"]) == (attempts, status)
        assert result["last_error"] == "嵌入请求暂时失败"
        if status == "pending":
            assert result["next_attempt_at"] - result["updated_at"] == timedelta(seconds=5 * 2 ** (attempts - 1))
            assert index_rag_asset(read_job().id, 4, services=services) is None
            make_due()
    assert client.post("/api/retrieval/jobs/retry-failed").json() == {"retried": 1}
    with business_sessions.begin() as session:
        session.get(RagIndexJob, read_job().id).status = "indexing"
    assert recover_interrupted_rag_jobs(business_sessions) == 1
    assert read_job().status == "failed"
    assert "重启中断" in read_job().last_error

    enabled_vector = services.vector
    cloud = OpenAICompatibleEmbeddingProvider(
        base_url="https://example.test", api_key="offline-fixture", model="cloud", model_calls_enabled=False,
    )
    disabled_vector = VectorRetrievalService(business_sessions, embedding_provider=cloud)
    services.vector = disabled_vector
    before = read_job()
    blocked = client.post("/api/retrieval/jobs/retry-failed")
    assert blocked.status_code == 409
    assert "MODEL_CALLS_ENABLED" in blocked.json()["detail"]
    assert (read_job().status, read_job().generation) == ("failed", before.generation)
    assert client.post(path).status_code == 409

    services.vector = enabled_vector
    retried = client.post("/api/retrieval/jobs/retry-failed")
    assert retried.status_code == 202 and retried.json() == {"retried": 1}
    pending = read_job()
    assert (pending.status, pending.generation) == ("pending", before.generation + 1)
    services.vector = disabled_vector
    result = index_rag_asset(pending.id, pending.generation, services=services)
    assert (result["status"], result["attempts"]) == ("failed", 0)
    assert "MODEL_CALLS_ENABLED" in result["last_error"]


def test_message_mount_contract_and_evidence_recheck(client, services, business_sessions):
    from sqlalchemy import select

    from knowledgeops_agent.persistence.models import Conversation, Evidence, Run
    from knowledgeops_agent.retrieval.collections import collection_scope
    from knowledgeops_agent.retrieval.references import search_references
    from knowledgeops_agent.retrieval.request_evidence import _current_reference

    first = _publish_doc(services, title="挂载回滚手册", body="回滚请联系负责人。")
    second = _publish_doc(services, title="其他回滚手册", body="回滚必须保存快照。")
    group = client.post("/api/retrieval/collections", json={"name": "仅手册"}).json()
    memberships = f"/api/retrieval/collections/{group['id']}/items"
    assert client.put(memberships, json={"expected_revision": 1, "items": [
        {"asset_kind": "knowledge", "asset_id": first["id"]},
    ]}).status_code == 200
    chat = services.jobs.create_conversation()
    url = f"/api/conversations/{chat['id']}/messages"
    base = {"content": "回滚", "request_key": "mount"}
    assert client.post(url, json={**base, "rag_scope": "collections"}).status_code == 422
    assert client.post(url, json={**base, "collection_ids": ["missing"]}).status_code == 404
    run = client.post(url, json={**base, "collection_ids": [group["id"]]})
    assert run.status_code == 202
    root = run.json()["id"]
    assert run.json()["input"]["rag_scope"] == "collections"
    assert client.post(url, json={**base, "rag_scope": "collections", "collection_ids": [group["id"]]}).json()["id"] == root
    auto = {**base, "request_key": "auto"}
    ordinary = client.post(url, json=auto).json()
    assert "rag_scope" not in ordinary["input"]
    assert client.post(url, json={**auto, "rag_scope": "auto", "collection_ids": []}).json()["id"] == ordinary["id"]

    with business_sessions() as session:
        docs, sources = collection_scope(session, [group["id"]])
    hits = search_references(services.knowledge, services.sources, "回滚", root_id=root,
                             document_ids=docs, source_ids=sources)
    assert [hit["asset_id"] for hit in hits] == [first["id"]]
    first_read = services.knowledge.read_document(first["id"], root_id=root)
    second_read = services.knowledge.read_document(second["id"], root_id=root)
    with business_sessions() as session:
        row = session.get(Run, root)
        assert _current_reference(session, row, {**first_read, "kind": "knowledge"}, verify_text=True)
        assert not _current_reference(session, row, {**second_read, "kind": "knowledge"}, verify_text=True)
        assert len(list(session.scalars(select(Evidence.id).where(Evidence.root_id == root)))) == 2

    with business_sessions.begin() as session:
        session.get(Conversation, chat["id"]).trial_case = "operations-pilot-v1"
    trial_source = services.sources.import_text("试用材料", "固定的试用回滚操作", conversation_id=chat["id"])
    assert client.put(memberships, json={"expected_revision": 2, "items": [
        {"asset_kind": "source", "asset_id": trial_source["id"]},
    ]}).status_code == 403
    assert client.post(url, json={**base, "request_key": "trial", "rag_scope": "all"}).status_code == 409


def test_mounted_collection_reaches_real_search_tool(runtime_services):
    from langchain_core.messages import HumanMessage
    from langgraph.checkpoint.memory import InMemorySaver
    from test_reference_runtime import JointAnswerProvider
    from test_runtime import setup_run

    from knowledgeops_agent.app.routes.retrieval import CollectionItemInput
    from knowledgeops_agent.harness.tools.policy import ToolPolicyService
    from knowledgeops_agent.retrieval.collections import RagCollections

    jobs, knowledge, sources, _ = runtime_services
    draft = knowledge.create_draft("fixture", [{
        "action": "create", "title": "现行规程", "body": "生产紧急变更由当班负责人事前批准。",
    }])
    knowledge.approve(draft["id"])
    receipt = knowledge.publish(draft["id"], idempotency_key="mounted-regulation")
    sources.import_text("未挂载的运维操作手册", "生产紧急变更前须记录回滚点。")
    groups = RagCollections(jobs.sessions)
    group = groups.create("生产变更规范", "")
    groups.replace(group["id"], group["revision"], [
        CollectionItemInput(asset_kind="knowledge", asset_id=receipt["document_ids"][0]),
    ])
    ToolPolicyService(jobs.sessions).update(expected_revision=0, enabled={"builtin:search_references": True})
    module, context, run = setup_run(runtime_services, task="生产紧急变更需要谁批准？")
    with jobs.sessions.begin() as session:
        row = jobs._locked(session, run["id"])
        row.input_data = {**row.input_data, "source_ids": [], "rag_scope": "collections",
                          "collection_ids": [group["id"]]}

    class MountedProvider(JointAnswerProvider):
        def stream(self, messages, **kwargs):
            assert any("mounted_knowledge" in str(message.content) and "生产变更规范" in str(message.content)
                       for message in messages)
            yield from super().stream(messages, **kwargs)

    context.gateway.client = MountedProvider(withdrawn=True)
    graph = module.build_agent(context, checkpointer=InMemorySaver())
    state = graph.invoke({"messages": [HumanMessage(run["task"])]},
                         config={"configurable": {"thread_id": run["checkpoint_id"]}}, context=context)
    assert state["messages"][-1].content.count("/api/evidence/") == 1
    search = [event["data"] for event in jobs.events(run["id"]) if event["kind"] == "reference_search"]
    assert [hit["asset_id"] for hit in search[0]["candidates"]] == receipt["document_ids"]


def test_new_knowledge_version_supersedes_inflight_vectors(services, business_sessions):
    from sqlalchemy import select

    from knowledgeops_agent.persistence.models import KnowledgeChunkEmbedding, RagIndexJob

    article = _publish_doc(services, title="并发索引核对", body="旧版本的发布准入要求。")
    services.vector.update_settings(expected_revision=0, hybrid_enabled=True)
    root = "rag-generation-fixture"
    services.knowledge.grant_direct(root)

    def publish(base, body):
        draft = services.knowledge.create_draft(root, [{
            "action": "update", "document_id": article["id"], "base_version_id": base,
            "title": article["title"], "body": body,
        }])
        return services.knowledge.publish(draft["id"], idempotency_key=base)["version_ids"][0]

    previous = publish(article["version_id"], "在途版本的发布准入要求。")

    class ConcurrentPublish(FakeEmbeddingProvider):
        def embed_documents(self, texts):
            result = super().embed_documents(texts)
            publish(previous, "新版本的发布准入要求。")
            return result

    vector = VectorRetrievalService(business_sessions, embedding_provider=ConcurrentPublish())
    with business_sessions() as session:
        job = session.scalar(select(RagIndexJob))
    assert vector.index_asset(job.id, job.generation) is None
    with business_sessions() as session:
        current = session.get(RagIndexJob, job.id)
        assert current.generation == job.generation + 1 and current.status == "pending"
        assert current.version_id != previous
        assert not list(session.scalars(select(KnowledgeChunkEmbedding)))
