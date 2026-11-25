"""同库 pgvector 派生向量索引、上下文增强嵌入、RRF 双路融合与可选重排服务。

设计红线：
1. 向量表仅为可随时清空重建的派生投影，绝不作为正文真相源，也不改变原文字符偏移。
2. 向量检索 SQL 严格复用知识与材料的版本/删除/归档/会话/试用隔离过滤条件。
3. 嵌入或重排服务未配置、超时或异常时自动平滑降级为纯 BM25 / 原 RRF 顺序，不中断业务。
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from itertools import zip_longest

from sqlalchemy import cast, delete, func, literal, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import sessionmaker

from knowledgeops_agent.persistence.models import (
    EmbeddingProfile,
    KnowledgeChunkEmbedding,
    KnowledgeDocument,
    KnowledgeSearchChunk,
    KnowledgeVersion,
    PgVector,
    RagSettings,
    Source,
    SourceChunkEmbedding,
    SourceSearchChunk,
    SourceSegment,
    SourceVersion,
    format_pgvector,
    utcnow,
)
from knowledgeops_agent.retrieval.embeddings import (
    DisabledEmbeddingProvider,
    DisabledRerankerProvider,
    EmbeddingError,
    EmbeddingProvider,
    RerankerError,
    RerankerProvider,
    build_contextual_text,
    embedding_content_hash,
)
from knowledgeops_agent.retrieval.search import LexicalQuery, excerpt

logger = logging.getLogger(__name__)

_DEFAULT_SETTINGS_ID = "default"


class RagSettingsConflict(ValueError):
    """乐观并发修订冲突。"""


def rrf_fuse(
    lexical_hits: list[dict],
    vector_hits: list[dict],
    *,
    key_fn: Callable[[dict], tuple],
    k: int = 60,
    limit: int = 10,
) -> list[dict]:
    """倒数排名融合（Reciprocal Rank Fusion）：只看各路名次，不受分值量纲差异影响。"""
    if limit <= 0:
        return []
    k = max(1, int(k))
    scores: dict[tuple, float] = {}
    items: dict[tuple, dict] = {}
    sources: dict[tuple, set[str]] = {}

    for rank, item in enumerate(lexical_hits, start=1):
        key = key_fn(item)
        scores[key] = scores.get(key, 0.0) + 1.0 / (k + rank)
        items.setdefault(key, item)
        sources.setdefault(key, set()).add("bm25")

    for rank, item in enumerate(vector_hits, start=1):
        key = key_fn(item)
        scores[key] = scores.get(key, 0.0) + 1.0 / (k + rank)
        # 优先保留词法命中（其 excerpt 围绕关键词锚点），词法未命中时使用向量命中。
        items.setdefault(key, item)
        sources.setdefault(key, set()).add("vector")

    ordered = sorted(scores.items(), key=lambda pair: (-pair[1], pair[0]))
    fused: list[dict] = []
    for key, rrf_score in ordered[:limit]:
        hit = dict(items[key])
        hit["rrf_score"] = round(rrf_score, 6)
        hit["score"] = float(rrf_score)
        hit_sources = sources[key]
        hit["retrieval_mode"] = (
            "hybrid" if len(hit_sources) == 2 else ("vector" if "vector" in hit_sources else "bm25")
        )
        fused.append(hit)
    return fused


def _serialize_settings(row: RagSettings | None) -> dict:
    if row is None:
        return {
            "hybrid_enabled": False,
            "rerank_enabled": False,
            "contextual_embedding_enabled": True,
            "rrf_k": 60,
            "rerank_top_k": 20,
            "active_profile_id": None,
            "revision": 0,
            "updated_at": None,
        }
    return {
        "hybrid_enabled": bool(row.hybrid_enabled),
        "rerank_enabled": bool(row.rerank_enabled),
        "contextual_embedding_enabled": bool(row.contextual_embedding_enabled),
        "rrf_k": int(row.rrf_k),
        "rerank_top_k": int(row.rerank_top_k),
        "active_profile_id": row.active_profile_id,
        "revision": int(row.revision),
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


class VectorRetrievalService:
    """管理 pgvector 向量库状态、RAG 开关、上下文增强嵌入构建与混合/重排检索。"""

    def __init__(
        self,
        sessions: sessionmaker,
        *,
        embedding_provider: EmbeddingProvider | None = None,
        reranker_provider: RerankerProvider | None = None,
    ):
        self.sessions = sessions
        self.embedding_provider: EmbeddingProvider = embedding_provider or DisabledEmbeddingProvider()
        self.reranker_provider: RerankerProvider = reranker_provider or DisabledRerankerProvider()

    # ------------------------------------------------------------------
    # RAG 设置与向量库状态
    # ------------------------------------------------------------------

    def get_settings(self) -> dict:
        with self.sessions() as session:
            row = session.get(RagSettings, _DEFAULT_SETTINGS_ID)
            return _serialize_settings(row)

    def update_settings(
        self,
        *,
        expected_revision: int,
        hybrid_enabled: bool | None = None,
        rerank_enabled: bool | None = None,
        contextual_embedding_enabled: bool | None = None,
        rrf_k: int | None = None,
        rerank_top_k: int | None = None,
        active_profile_id: str | None = ...,
    ) -> dict:
        with self.sessions.begin() as session:
            row = session.get(RagSettings, _DEFAULT_SETTINGS_ID, with_for_update=True)
            current_rev = int(row.revision) if row is not None else 0
            if int(expected_revision) != current_rev:
                raise RagSettingsConflict(f"RAG 设置修订已更新（当前 {current_rev}，提交 {expected_revision}）")
            if row is None:
                row = RagSettings(id=_DEFAULT_SETTINGS_ID, revision=0, updated_at=utcnow())
                session.add(row)
                session.flush()
            if hybrid_enabled is not None:
                row.hybrid_enabled = bool(hybrid_enabled)
            if rerank_enabled is not None:
                row.rerank_enabled = bool(rerank_enabled)
            if contextual_embedding_enabled is not None:
                row.contextual_embedding_enabled = bool(contextual_embedding_enabled)
            if rrf_k is not None:
                if not (1 <= int(rrf_k) <= 1000):
                    raise ValueError("rrf_k 必须在 1 到 1000 之间")
                row.rrf_k = int(rrf_k)
            if rerank_top_k is not None:
                if not (1 <= int(rerank_top_k) <= 100):
                    raise ValueError("rerank_top_k 必须在 1 到 100 之间")
                row.rerank_top_k = int(rerank_top_k)
            if active_profile_id is not ...:
                if active_profile_id is None or active_profile_id == "":
                    row.active_profile_id = None
                else:
                    profile = session.get(EmbeddingProfile, active_profile_id)
                    if profile is None:
                        raise KeyError(f"未找到向量档案: {active_profile_id}")
                    if profile.status != "ready":
                        raise ValueError("只能切换至已就绪（ready）的向量档案")
                    row.active_profile_id = profile.profile_id
            row.revision = current_rev + 1
            row.updated_at = utcnow()
        return self.status()

    def status(self) -> dict:
        """返回 pgvector 扩展、分块覆盖率、档案列表、Provider 状态与当前实际检索模式。"""
        with self.sessions() as session:
            ext_version = session.scalar(
                text("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
            )
            rag = _serialize_settings(session.get(RagSettings, _DEFAULT_SETTINGS_ID))

            latest_sources = (
                select(
                    SourceVersion.source_id.label("source_id"),
                    func.max(SourceVersion.number).label("number"),
                )
                .where(SourceVersion.status == "ready")
                .group_by(SourceVersion.source_id)
                .subquery()
            )
            knowledge_total = int(
                session.scalar(
                    select(func.count())
                    .select_from(KnowledgeSearchChunk)
                    .join(KnowledgeDocument, KnowledgeDocument.id == KnowledgeSearchChunk.document_id)
                    .join(KnowledgeVersion, KnowledgeVersion.id == KnowledgeSearchChunk.version_id)
                    .where(
                        KnowledgeDocument.current_version_id == KnowledgeSearchChunk.version_id,
                        KnowledgeDocument.deleted_at.is_(None),
                        KnowledgeVersion.archived.is_(False),
                    )
                )
                or 0
            )
            source_total = int(
                session.scalar(
                    select(func.count())
                    .select_from(SourceSearchChunk)
                    .join(SourceSegment, SourceSegment.id == SourceSearchChunk.segment_id)
                    .join(SourceVersion, SourceVersion.id == SourceSegment.source_version_id)
                    .join(
                        latest_sources,
                        (latest_sources.c.source_id == SourceVersion.source_id)
                        & (latest_sources.c.number == SourceVersion.number),
                    )
                    .join(Source, Source.id == SourceVersion.source_id)
                    .where(
                        Source.deleted_at.is_(None),
                        SourceVersion.status == "ready",
                        SourceSegment.status == "ready",
                    )
                )
                or 0
            )

            knowledge_counts = dict(
                session.execute(
                    select(
                        KnowledgeChunkEmbedding.profile_id,
                        func.count(),
                    )
                    .select_from(KnowledgeChunkEmbedding)
                    .join(
                        KnowledgeSearchChunk,
                        (KnowledgeSearchChunk.document_id == KnowledgeChunkEmbedding.document_id)
                        & (KnowledgeSearchChunk.chunk_index == KnowledgeChunkEmbedding.chunk_index),
                    )
                    .join(KnowledgeDocument, KnowledgeDocument.id == KnowledgeChunkEmbedding.document_id)
                    .join(KnowledgeVersion, KnowledgeVersion.id == KnowledgeChunkEmbedding.version_id)
                    .where(
                        KnowledgeDocument.current_version_id == KnowledgeChunkEmbedding.version_id,
                        KnowledgeDocument.deleted_at.is_(None),
                        KnowledgeVersion.archived.is_(False),
                    )
                    .group_by(KnowledgeChunkEmbedding.profile_id)
                ).all()
            )
            source_counts = dict(
                session.execute(
                    select(
                        SourceChunkEmbedding.profile_id,
                        func.count(),
                    )
                    .select_from(SourceChunkEmbedding)
                    .join(
                        SourceSearchChunk,
                        (SourceSearchChunk.segment_id == SourceChunkEmbedding.segment_id)
                        & (SourceSearchChunk.chunk_index == SourceChunkEmbedding.chunk_index),
                    )
                    .join(SourceSegment, SourceSegment.id == SourceChunkEmbedding.segment_id)
                    .join(SourceVersion, SourceVersion.id == SourceSegment.source_version_id)
                    .join(
                        latest_sources,
                        (latest_sources.c.source_id == SourceVersion.source_id)
                        & (latest_sources.c.number == SourceVersion.number),
                    )
                    .join(Source, Source.id == SourceVersion.source_id)
                    .where(
                        Source.deleted_at.is_(None),
                        SourceVersion.status == "ready",
                        SourceSegment.status == "ready",
                    )
                    .group_by(SourceChunkEmbedding.profile_id)
                ).all()
            )

            profile_rows = session.scalars(
                select(EmbeddingProfile).order_by(EmbeddingProfile.updated_at.desc(), EmbeddingProfile.profile_id)
            ).all()
            profiles = [
                {
                    "profile_id": p.profile_id,
                    "provider": p.provider,
                    "model": p.model,
                    "dimensions": int(p.dimensions),
                    "query_prefix": p.query_prefix,
                    "document_prefix": p.document_prefix,
                    "normalize": bool(p.normalize),
                    "contextual": bool(p.contextual),
                    "status": p.status,
                    "knowledge_chunks": int(knowledge_counts.get(p.profile_id, 0)),
                    "source_chunks": int(source_counts.get(p.profile_id, 0)),
                    "updated_at": p.updated_at.isoformat() if p.updated_at else None,
                    "active": p.profile_id == rag["active_profile_id"],
                }
                for p in profile_rows
            ]

        active_profile = next(
            (p for p in profiles if p["profile_id"] == rag["active_profile_id"] and p["status"] == "ready"),
            None,
        )
        emb_desc = self.embedding_provider.describe()
        rerank_desc = self.reranker_provider.describe()

        if not ext_version:
            effective_mode = "bm25_only"
            effective_reason = "数据库未安装 pgvector 扩展，当前仅使用联合 BM25"
        elif not rag["hybrid_enabled"]:
            effective_mode = "bm25_only"
            effective_reason = "向量混合检索未启用，当前使用联合 BM25"
        elif not self.embedding_provider.enabled:
            effective_mode = "bm25_only"
            effective_reason = "已开启混合检索开关，但嵌入服务未配置或受模型开关限制，自动降级为联合 BM25"
        elif active_profile is None:
            effective_mode = "bm25_only"
            effective_reason = "已开启混合检索，但尚无已就绪的向量档案，请先执行“重建/补建向量索引”"
        elif rag["rerank_enabled"] and self.reranker_provider.enabled:
            effective_mode = "hybrid_rerank"
            effective_reason = f"BM25 + pgvector RRF 混合检索已就绪（档案 {active_profile['profile_id']}），并启用可选重排"
        elif rag["rerank_enabled"] and not self.reranker_provider.enabled:
            effective_mode = "hybrid"
            effective_reason = (
                f"BM25 + pgvector RRF 混合检索已就绪（档案 {active_profile['profile_id']}）；"
                "重排服务未配置，自动跳过重排"
            )
        else:
            effective_mode = "hybrid"
            effective_reason = f"BM25 + pgvector RRF 混合检索已就绪（档案 {active_profile['profile_id']}）"

        active_k = active_profile["knowledge_chunks"] if active_profile else 0
        active_s = active_profile["source_chunks"] if active_profile else 0
        total_chunks = knowledge_total + source_total
        embedded_chunks = active_k + active_s

        return {
            "pgvector": {
                "installed": bool(ext_version),
                "version": str(ext_version) if ext_version else None,
            },
            "settings": rag,
            "effective_mode": effective_mode,
            "effective_reason": effective_reason,
            "embedding_provider": emb_desc,
            "reranker_provider": rerank_desc,
            "coverage": {
                "knowledge_total_chunks": knowledge_total,
                "knowledge_embedded_chunks": active_k,
                "source_total_chunks": source_total,
                "source_embedded_chunks": active_s,
                "total_chunks": total_chunks,
                "embedded_chunks": embedded_chunks,
                "ratio": min(1.0, round(embedded_chunks / total_chunks, 4)) if total_chunks > 0 else 1.0,
            },
            "profiles": profiles,
        }

    # ------------------------------------------------------------------
    # 上下文增强嵌入构建与增量重建
    # ------------------------------------------------------------------

    def rebuild_embeddings(
        self,
        *,
        contextual: bool | None = None,
        activate: bool = True,
        mark_others_stale: bool = False,
        force: bool = False,
    ) -> dict:
        """幂等增量构建/重建当前知识与材料分块的 pgvector 向量；外部 HTTP 调用不持有长 DB 事务。"""
        if not self.embedding_provider.enabled:
            raise EmbeddingError("嵌入服务未启用或未配置，无法构建向量索引")

        started = time.perf_counter()
        spec = self.embedding_provider.spec
        rag_settings = self.get_settings()
        use_contextual = (
            bool(contextual) if contextual is not None else bool(rag_settings["contextual_embedding_enabled"])
        )

        # 1. 先短事务拉取当前需要检查的知识与材料分块（不持锁调外部 HTTP）。
        knowledge_items: list[dict] = []
        source_items: list[dict] = []
        with self.sessions() as session:
            k_rows = session.execute(
                select(KnowledgeSearchChunk, KnowledgeVersion)
                .join(KnowledgeDocument, KnowledgeDocument.id == KnowledgeSearchChunk.document_id)
                .join(KnowledgeVersion, KnowledgeVersion.id == KnowledgeSearchChunk.version_id)
                .where(
                    KnowledgeDocument.current_version_id == KnowledgeSearchChunk.version_id,
                    KnowledgeDocument.deleted_at.is_(None),
                    KnowledgeVersion.archived.is_(False),
                )
                .order_by(KnowledgeSearchChunk.document_id, KnowledgeSearchChunk.chunk_index)
            ).all()
            for chunk, version in k_rows:
                raw = version.body[chunk.start : chunk.end]
                ctx_text = build_contextual_text(
                    raw,
                    title=version.title,
                    summary=version.summary,
                    tags=version.tags,
                    category=version.category,
                    contextual=use_contextual,
                )
                knowledge_items.append(
                    {
                        "document_id": chunk.document_id,
                        "version_id": chunk.version_id,
                        "chunk_index": chunk.chunk_index,
                        "text": ctx_text,
                        "sha256": embedding_content_hash(
                            ctx_text,
                            document_prefix=spec.document_prefix,
                            contextual=use_contextual,
                            normalize=spec.normalize,
                        ),
                    }
                )

            latest_sources = (
                select(
                    SourceVersion.source_id.label("source_id"),
                    func.max(SourceVersion.number).label("number"),
                )
                .where(SourceVersion.status == "ready")
                .group_by(SourceVersion.source_id)
                .subquery()
            )
            s_rows = session.execute(
                select(SourceSearchChunk, SourceSegment, Source)
                .join(SourceSegment, SourceSegment.id == SourceSearchChunk.segment_id)
                .join(SourceVersion, SourceVersion.id == SourceSegment.source_version_id)
                .join(
                    latest_sources,
                    (latest_sources.c.source_id == SourceVersion.source_id)
                    & (latest_sources.c.number == SourceVersion.number),
                )
                .join(Source, Source.id == SourceVersion.source_id)
                .where(
                    Source.deleted_at.is_(None),
                    SourceVersion.status == "ready",
                    SourceSegment.status == "ready",
                )
                .order_by(SourceSearchChunk.segment_id, SourceSearchChunk.chunk_index)
            ).all()
            for chunk, segment, source in s_rows:
                raw = segment.text[chunk.start : chunk.end]
                ctx_text = build_contextual_text(
                    raw,
                    title=source.title,
                    page=segment.page,
                    contextual=use_contextual,
                )
                source_items.append(
                    {
                        "segment_id": chunk.segment_id,
                        "chunk_index": chunk.chunk_index,
                        "text": ctx_text,
                        "sha256": embedding_content_hash(
                            ctx_text,
                            document_prefix=spec.document_prefix,
                            contextual=use_contextual,
                            normalize=spec.normalize,
                        ),
                    }
                )

        # 2. 探测或确认维度，确定 profile_id。
        resolved_dims = spec.dimensions
        if resolved_dims is None:
            probe_text = (
                knowledge_items[0]["text"]
                if knowledge_items
                else (source_items[0]["text"] if source_items else "probe")
            )
            probe_vec = self.embedding_provider.embed_documents([probe_text])[0]
            resolved_dims = len(probe_vec)
        profile_id = spec.profile_id(resolved_dims)

        # 3. 幂等登记 EmbeddingProfile（不覆写已有 ready 档案元数据，避免步骤 4 失败污染原档案）。
        with self.sessions.begin() as session:
            session.execute(
                insert(EmbeddingProfile)
                .values(
                    profile_id=profile_id,
                    provider=spec.provider,
                    model=spec.model,
                    dimensions=resolved_dims,
                    query_prefix=spec.query_prefix,
                    document_prefix=spec.document_prefix,
                    normalize=spec.normalize,
                    contextual=use_contextual,
                    status="building",
                    updated_at=utcnow(),
                )
                .on_conflict_do_nothing(index_elements=["profile_id"])
            )

            existing_k = {}
            existing_s = {}
            if not force:
                existing_k = {
                    (row.document_id, row.chunk_index): (row.version_id, row.content_sha256)
                    for row in session.scalars(
                        select(KnowledgeChunkEmbedding).where(KnowledgeChunkEmbedding.profile_id == profile_id)
                    )
                }
                existing_s = {
                    (row.segment_id, row.chunk_index): row.content_sha256
                    for row in session.scalars(
                        select(SourceChunkEmbedding).where(SourceChunkEmbedding.profile_id == profile_id)
                    )
                }

        pending_k = [
            item
            for item in knowledge_items
            if force
            or existing_k.get((item["document_id"], item["chunk_index"])) != (item["version_id"], item["sha256"])
        ]
        pending_s = [
            item
            for item in source_items
            if force or existing_s.get((item["segment_id"], item["chunk_index"])) != item["sha256"]
        ]

        # 4. 在事务外调用嵌入服务，避免网络延迟占用数据库连接。
        k_vectors = self.embedding_provider.embed_documents([item["text"] for item in pending_k]) if pending_k else []
        s_vectors = self.embedding_provider.embed_documents([item["text"] for item in pending_s]) if pending_s else []

        # 5. 短事务幂等落库（跳过构建期间已删除或已切版本的分块）并标记档案就绪。
        now = utcnow()
        written_k = 0
        written_s = 0
        with self.sessions.begin() as session:
            live_k_chunks: set[tuple[str, int, str]] = set()
            if pending_k:
                k_doc_ids = list({item["document_id"] for item in pending_k})
                live_k_chunks = {
                    (row[0], int(row[1]), row[2])
                    for row in session.execute(
                        select(
                            KnowledgeSearchChunk.document_id,
                            KnowledgeSearchChunk.chunk_index,
                            KnowledgeSearchChunk.version_id,
                        )
                        .join(KnowledgeDocument, KnowledgeDocument.id == KnowledgeSearchChunk.document_id)
                        .where(
                            KnowledgeSearchChunk.document_id.in_(k_doc_ids),
                            KnowledgeDocument.current_version_id == KnowledgeSearchChunk.version_id,
                            KnowledgeDocument.deleted_at.is_(None),
                        )
                    ).all()
                }

            for item, vec in zip(pending_k, k_vectors, strict=True):
                if len(vec) != resolved_dims:
                    raise EmbeddingError(f"向量维度不匹配：期望 {resolved_dims}，实际 {len(vec)}")
                if (item["document_id"], int(item["chunk_index"]), item["version_id"]) not in live_k_chunks:
                    continue
                stmt = insert(KnowledgeChunkEmbedding).values(
                    document_id=item["document_id"],
                    chunk_index=item["chunk_index"],
                    profile_id=profile_id,
                    version_id=item["version_id"],
                    embedding=vec,
                    dimensions=len(vec),
                    content_sha256=item["sha256"],
                    updated_at=now,
                )
                session.execute(
                    stmt.on_conflict_do_update(
                        index_elements=["document_id", "chunk_index", "profile_id"],
                        set_={
                            "version_id": stmt.excluded.version_id,
                            "embedding": stmt.excluded.embedding,
                            "dimensions": stmt.excluded.dimensions,
                            "content_sha256": stmt.excluded.content_sha256,
                            "updated_at": stmt.excluded.updated_at,
                        },
                    )
                )
                written_k += 1

            live_s_chunks: set[tuple[str, int]] = set()
            if pending_s:
                s_seg_ids = list({item["segment_id"] for item in pending_s})
                live_s_chunks = {
                    (row[0], int(row[1]))
                    for row in session.execute(
                        select(SourceSearchChunk.segment_id, SourceSearchChunk.chunk_index)
                        .join(SourceSegment, SourceSegment.id == SourceSearchChunk.segment_id)
                        .join(SourceVersion, SourceVersion.id == SourceSegment.source_version_id)
                        .join(Source, Source.id == SourceVersion.source_id)
                        .where(
                            SourceSearchChunk.segment_id.in_(s_seg_ids),
                            Source.deleted_at.is_(None),
                            SourceVersion.status == "ready",
                            SourceSegment.status == "ready",
                        )
                    ).all()
                }

            for item, vec in zip(pending_s, s_vectors, strict=True):
                if len(vec) != resolved_dims:
                    raise EmbeddingError(f"向量维度不匹配：期望 {resolved_dims}，实际 {len(vec)}")
                if (item["segment_id"], int(item["chunk_index"])) not in live_s_chunks:
                    continue
                stmt = insert(SourceChunkEmbedding).values(
                    segment_id=item["segment_id"],
                    chunk_index=item["chunk_index"],
                    profile_id=profile_id,
                    embedding=vec,
                    dimensions=len(vec),
                    content_sha256=item["sha256"],
                    updated_at=now,
                )
                session.execute(
                    stmt.on_conflict_do_update(
                        index_elements=["segment_id", "chunk_index", "profile_id"],
                        set_={
                            "embedding": stmt.excluded.embedding,
                            "dimensions": stmt.excluded.dimensions,
                            "content_sha256": stmt.excluded.content_sha256,
                            "updated_at": stmt.excluded.updated_at,
                        },
                    )
                )
                written_s += 1

            profile = session.get(EmbeddingProfile, profile_id, with_for_update=True)
            if profile is not None:
                profile.contextual = use_contextual
                profile.query_prefix = spec.query_prefix
                profile.document_prefix = spec.document_prefix
                profile.normalize = spec.normalize
                profile.status = "ready"
                profile.updated_at = now
            if mark_others_stale:
                for other in session.scalars(
                    select(EmbeddingProfile).where(
                        EmbeddingProfile.profile_id != profile_id,
                        EmbeddingProfile.status == "ready",
                    )
                ):
                    other.status = "stale"
                    other.updated_at = now

            rag = session.get(RagSettings, _DEFAULT_SETTINGS_ID, with_for_update=True)
            if rag is None:
                rag = RagSettings(
                    id=_DEFAULT_SETTINGS_ID,
                    active_profile_id=profile_id if activate else None,
                    revision=1 if activate else 0,
                    updated_at=now,
                )
                session.add(rag)
            elif activate and rag.active_profile_id != profile_id:
                rag.active_profile_id = profile_id
                rag.revision = int(rag.revision) + 1
                rag.updated_at = now

        elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
        return {
            "profile_id": profile_id,
            "dimensions": resolved_dims,
            "contextual": use_contextual,
            "knowledge_embedded": written_k,
            "knowledge_skipped": len(knowledge_items) - written_k,
            "source_embedded": written_s,
            "source_skipped": len(source_items) - written_s,
            "elapsed_ms": elapsed_ms,
        }

    def delete_profile(self, profile_id: str) -> dict:
        """删除非当前启用中的向量档案（级联清理派生向量行，不影响原文与 BM25）。"""
        with self.sessions.begin() as session:
            profile = session.get(EmbeddingProfile, profile_id, with_for_update=True)
            if profile is None:
                raise KeyError(f"未找到向量档案: {profile_id}")
            rag = session.get(RagSettings, _DEFAULT_SETTINGS_ID, with_for_update=True)
            if rag is not None and rag.active_profile_id == profile_id and rag.hybrid_enabled:
                raise ValueError("当前混合检索正在使用该档案，请先切换档案或关闭混合检索后再删除")
            if rag is not None and rag.active_profile_id == profile_id:
                rag.active_profile_id = None
                rag.revision = int(rag.revision) + 1
                rag.updated_at = utcnow()
            session.execute(delete(EmbeddingProfile).where(EmbeddingProfile.profile_id == profile_id))
        return {"deleted_profile_id": profile_id}

    # ------------------------------------------------------------------
    # pgvector 向量候选查询（严格复用业务权限与版本边界）
    # ------------------------------------------------------------------

    def _active_ready_profile(self) -> tuple[dict, EmbeddingProfile | None]:
        with self.sessions() as session:
            rag = _serialize_settings(session.get(RagSettings, _DEFAULT_SETTINGS_ID))
            profile_id = rag["active_profile_id"]
            profile = session.get(EmbeddingProfile, profile_id) if profile_id else None
            if profile is not None and profile.status != "ready":
                profile = None
            return rag, profile

    def search_knowledge_vectors(
        self,
        query: str,
        query_vector: list[float],
        *,
        profile_id: str,
        root_id: str | None = None,
        limit: int = 10,
    ) -> list[dict]:
        """按与 KnowledgeService.search 相同的版本/归档/试用过滤查询 pgvector 候选。"""
        from knowledgeops_agent.research.trial import run_trial_owner

        parsed = LexicalQuery.parse(query)
        vec_literal = format_pgvector(query_vector)
        emb = KnowledgeChunkEmbedding
        chunk = KnowledgeSearchChunk
        distance = emb.embedding.op("OPERATOR(public.<=>)")(cast(literal(vec_literal), PgVector()))

        with self.sessions() as session:
            trial_owner = run_trial_owner(session, root_id)
            candidates = (
                select(
                    chunk.version_id,
                    chunk.start,
                    chunk.end,
                    chunk.chunk_index,
                    distance.label("distance"),
                    func.row_number()
                    .over(
                        partition_by=chunk.document_id,
                        order_by=(distance.asc(), chunk.chunk_index),
                    )
                    .label("position"),
                )
                .join(
                    chunk,
                    (chunk.document_id == emb.document_id) & (chunk.chunk_index == emb.chunk_index),
                )
                .join(KnowledgeDocument, KnowledgeDocument.id == chunk.document_id)
                .join(KnowledgeVersion, KnowledgeVersion.id == chunk.version_id)
                .where(
                    emb.profile_id == profile_id,
                    emb.version_id == chunk.version_id,
                    KnowledgeDocument.current_version_id == chunk.version_id,
                    KnowledgeDocument.deleted_at.is_(None),
                    KnowledgeDocument.trial_conversation_id == trial_owner,
                    KnowledgeVersion.archived.is_(False),
                )
                .subquery()
            )
            statement = (
                select(
                    KnowledgeVersion,
                    candidates.c.distance,
                    candidates.c.start,
                    candidates.c.end,
                    candidates.c.chunk_index,
                )
                .join(candidates, candidates.c.version_id == KnowledgeVersion.id)
                .where(candidates.c.position == 1)
                .order_by(candidates.c.distance.asc(), KnowledgeVersion.id)
                .limit(max(1, min(limit, 30)))
            )
            results: list[dict] = []
            for version, dist, start, end, chunk_index in session.execute(statement):
                passage = excerpt(version.body, start, end, parsed, width=700)
                similarity = max(0.0, 1.0 - float(dist))
                results.append(
                    {
                        "document_id": version.document_id,
                        "version_id": version.id,
                        "version": version.number,
                        "title": version.title,
                        "score": similarity,
                        "snippet": passage.text,
                        "start": passage.start,
                        "end": passage.end,
                        "chunk_index": chunk_index,
                    }
                )
            return results

    def search_source_vectors(
        self,
        query: str,
        query_vector: list[float],
        *,
        profile_id: str,
        root_id: str | None = None,
        conversation_id: str | None = None,
        source_ids: list[str] | None = None,
        limit: int = 10,
    ) -> list[dict]:
        """按与 SourceService.search 相同的 _search_scope 权限过滤查询 pgvector 材料候选。"""
        from knowledgeops_agent.sources.service import _search_scope

        parsed = LexicalQuery.parse(query)
        vec_literal = format_pgvector(query_vector)
        emb = SourceChunkEmbedding
        chunk = SourceSearchChunk
        distance = emb.embedding.op("OPERATOR(public.<=>)")(cast(literal(vec_literal), PgVector()))

        with self.sessions() as session:
            statement = (
                select(chunk, SourceSegment, SourceVersion, Source, distance.label("distance"))
                .join(
                    chunk,
                    (chunk.segment_id == emb.segment_id) & (chunk.chunk_index == emb.chunk_index),
                )
                .join(SourceSegment, SourceSegment.id == chunk.segment_id)
                .join(SourceVersion, SourceVersion.id == SourceSegment.source_version_id)
                .join(Source, Source.id == SourceVersion.source_id)
                .where(
                    emb.profile_id == profile_id,
                    *_search_scope(
                        session,
                        root_id=root_id,
                        conversation_id=conversation_id,
                        source_ids=source_ids,
                    ),
                )
                .order_by(distance.asc(), Source.id, SourceSegment.position, chunk.chunk_index)
                .limit(min(max(1, limit), 50))
            )
            results: list[dict] = []
            for row, segment, version, source, dist in session.execute(statement):
                hit = excerpt(segment.text, row.start, row.end, parsed)
                similarity = max(0.0, 1.0 - float(dist))
                results.append(
                    {
                        "source_id": source.id,
                        "version_id": version.id,
                        "version": version.number,
                        "segment_id": segment.id,
                        "title": source.title,
                        "page": segment.page,
                        "start": segment.start_offset + hit.start,
                        "end": segment.start_offset + hit.end,
                        "snippet": hit.text,
                        "score": similarity,
                    }
                )
            return results

    # ------------------------------------------------------------------
    # 联合混合检索（BM25 + pgvector RRF + 可选 Reranker）
    # ------------------------------------------------------------------

    def search_references(
        self,
        knowledge,
        sources,
        query: str,
        *,
        root_id: str,
        scope: str = "all",
        source_ids: list[str] | None = None,
        limit: int = 10,
        use_rerank: bool | None = None,
    ) -> list[dict]:
        """联合 BM25 + pgvector RRF 混合检索；任一向量/重排故障均平滑降级为纯 BM25 / 原 RRF。"""
        if scope not in {"all", "knowledge", "material"}:
            raise ValueError("未知的资料检索范围")
        if limit <= 0 or not query.strip():
            return []
        limit = min(limit, 30)

        rag, active_profile = self._active_ready_profile()
        hybrid_active = bool(
            rag["hybrid_enabled"] and active_profile is not None and self.embedding_provider.enabled
        )
        fetch_limit = min(30, max(limit, rag["rerank_top_k"]) if hybrid_active else limit)

        k_lexical = (
            [
                {"kind": "knowledge", "asset_id": item["document_id"], **item}
                for item in knowledge.search(query, limit=fetch_limit, root_id=root_id)
            ]
            if scope != "material"
            else []
        )
        s_lexical = (
            [
                {"kind": "material", "asset_id": item["source_id"], **item}
                for item in sources.search(query, root_id=root_id, source_ids=source_ids, limit=fetch_limit)
            ]
            if scope != "knowledge"
            else []
        )

        if not hybrid_active or active_profile is None:
            return [
                item for pair in zip_longest(k_lexical, s_lexical) for item in pair if item is not None
            ][:limit]

        # 生成查询向量（严格捕获异常，绝不阻塞检索）。
        query_vector: list[float] | None = None
        try:
            query_vector = self.embedding_provider.embed_query(query)
            if len(query_vector) != active_profile.dimensions:
                raise EmbeddingError(
                    f"查询向量维度({len(query_vector)})与档案维度({active_profile.dimensions})不一致"
                )
        except Exception as exc:
            logger.warning("embedding query failed; falling back to BM25 (%s)", type(exc).__name__)
            return [
                item for pair in zip_longest(k_lexical, s_lexical) for item in pair if item is not None
            ][:limit]

        k_fused = k_lexical
        s_fused = s_lexical
        rrf_k = int(rag["rrf_k"])
        try:
            if scope != "material":
                k_vec = [
                    {"kind": "knowledge", "asset_id": item["document_id"], **item}
                    for item in self.search_knowledge_vectors(
                        query,
                        query_vector,
                        profile_id=active_profile.profile_id,
                        root_id=root_id,
                        limit=fetch_limit,
                    )
                ]
                k_fused = rrf_fuse(
                    k_lexical,
                    k_vec,
                    key_fn=lambda row: ("knowledge", row["asset_id"], row["version_id"]),
                    k=rrf_k,
                    limit=fetch_limit,
                )
            if scope != "knowledge":
                s_vec = [
                    {"kind": "material", "asset_id": item["source_id"], **item}
                    for item in self.search_source_vectors(
                        query,
                        query_vector,
                        profile_id=active_profile.profile_id,
                        root_id=root_id,
                        source_ids=source_ids,
                        limit=fetch_limit,
                    )
                ]
                s_fused = rrf_fuse(
                    s_lexical,
                    s_vec,
                    key_fn=lambda row: ("material", row["segment_id"], row["start"], row["end"]),
                    k=rrf_k,
                    limit=fetch_limit,
                )
        except Exception as exc:
            logger.warning("pgvector search failed; falling back to BM25 (%s)", type(exc).__name__)
            return [
                item for pair in zip_longest(k_lexical, s_lexical) for item in pair if item is not None
            ][:limit]

        combined = [
            item for pair in zip_longest(k_fused, s_fused) for item in pair if item is not None
        ][:fetch_limit]

        # 可选重排：仅当显式开启且 Reranker 可用时执行，失败自动回退 RRF 顺序。
        should_rerank = (
            bool(use_rerank) if use_rerank is not None else bool(rag["rerank_enabled"])
        )
        if should_rerank and self.reranker_provider.enabled and len(combined) > 1:
            top_n = min(len(combined), int(rag["rerank_top_k"]))
            pool = combined[:top_n]
            docs = [f"{item.get('title', '')}\n{item.get('snippet', '')}".strip() for item in pool]
            try:
                ranked = self.reranker_provider.rerank(query, docs, top_n=limit)
                if ranked:
                    reordered: list[dict] = []
                    seen: set[int] = set()
                    for idx, rerank_score in ranked:
                        if 0 <= idx < len(pool) and idx not in seen:
                            seen.add(idx)
                            row = dict(pool[idx])
                            row["rerank_score"] = float(rerank_score)
                            row["score"] = float(rerank_score)
                            row["reranked"] = True
                            reordered.append(row)
                    if reordered:
                        return reordered[:limit]
            except (RerankerError, Exception) as exc:
                logger.warning("reranker failed; keeping RRF order (%s)", type(exc).__name__)

        return combined[:limit]
