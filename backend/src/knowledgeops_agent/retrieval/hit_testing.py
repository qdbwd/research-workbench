"""只读召回诊断。复用生产分块检索，记录每阶段耗时及降级原因。"""

import math
from itertools import zip_longest
from time import perf_counter

from knowledgeops_agent.persistence.models import RagCollection, format_pgvector
from knowledgeops_agent.retrieval.collections import collection_scope, memberships
from knowledgeops_agent.retrieval.vector import rrf_fuse


def hit_test(service, knowledge, sources, *, query, mode, scope, collection_ids, top_k,
             rrf_k, rerank_top_k, min_vector_similarity):
    started = perf_counter()
    timing = dict.fromkeys(("bm25_ms", "embed_ms", "vector_ms", "rrf_ms", "rerank_ms"), 0.0)

    def measure(stage, fn):
        before = perf_counter()
        try:
            return fn()
        finally:
            timing[stage] = round((perf_counter() - before) * 1000, 3)

    document_ids = source_ids = None
    if collection_ids is not None:
        with service.sessions() as session:
            if any(session.get(RagCollection, identity) is None for identity in collection_ids):
                raise KeyError("未找到知识集")
            document_ids, source_ids = collection_scope(session, collection_ids)
    settings, profile = service._active_ready_profile()
    candidates = min(30, max(top_k, rerank_top_k or settings["rerank_top_k"]))
    rerank_limit = rerank_top_k or settings["rerank_top_k"]
    effective, reason = mode, None

    def search(*, vector=None):
        k_hits, s_hits = [], []
        if scope != "material":
            k_hits = knowledge.search(query, document_ids=document_ids, limit=candidates) if vector is None else (
                service.search_knowledge_vectors(query, vector, profile_id=profile.profile_id,
                                                 document_ids=document_ids, limit=candidates,
                                                 min_similarity=min_vector_similarity)
            )
        if scope != "knowledge":
            s_hits = sources.search(query, source_ids=source_ids, limit=candidates) if vector is None else (
                service.search_source_vectors(query, vector, profile_id=profile.profile_id, source_ids=source_ids,
                                              limit=candidates, min_similarity=min_vector_similarity)
            )
        knowledge_hits = [{"kind": "knowledge", "asset_id": item["document_id"], **item} for item in k_hits]
        material_hits = [{"kind": "material", "asset_id": item["source_id"], **item} for item in s_hits]
        if vector is None:
            # BM25 分值只在各自索引内可比，沿用生产检索的按名次交错。
            return [
                hit for pair in zip_longest(knowledge_hits, material_hits) for hit in pair
                if hit is not None
            ]
        return sorted(
            knowledge_hits + material_hits,
            key=lambda hit: (-hit["score"], hit["asset_id"], hit["chunk_index"]),
        )

    lexical = measure("bm25_ms", search) if mode != "vector" else []
    vector_hits = []
    if mode != "bm25":
        if not profile or not service.embedding_provider.enabled:
            reason = "嵌入服务不可用或当前没有已就绪的向量档案"
        else:
            def embed():
                vector = service.embedding_provider.embed_query(query)
                if len(vector) != profile.dimensions:
                    raise ValueError("查询向量维度与档案不匹配")
                format_pgvector(vector)
                return vector

            try:
                vector = measure("embed_ms", embed)
                vector_hits = measure("vector_ms", lambda: search(vector=vector))
            except Exception as exc:
                reason = f"向量检索失败（{type(exc).__name__}）"
        if reason:
            effective = "unavailable" if mode == "vector" else "bm25"

    if effective in {"hybrid", "hybrid_rerank"}:
        fused = measure("rrf_ms", lambda: rrf_fuse(
            lexical, vector_hits, k=rrf_k or settings["rrf_k"], limit=max(top_k, rerank_limit),
            key_fn=lambda row: (row["kind"], row["asset_id"], row["version_id"], row.get("segment_id") or "",
                                row["chunk_index"]),
        ))
    else:
        hits = lexical if effective == "bm25" else vector_hits
        fused = [{
            **hit, "retrieval_mode": effective, f"{effective}_score": hit["score"],
            f"{effective}_rank": position,
        } for position, hit in enumerate(hits, 1)]

    if effective == "hybrid_rerank":
        if not service.reranker_provider.enabled:
            effective, reason = "hybrid", "重排服务未配置或未启用"
        elif fused:
            pool = fused[:rerank_limit]
            try:
                ranked = measure("rerank_ms", lambda: service.reranker_provider.rerank(
                    query, [f"{item['title']}\n{item['snippet']}" for item in pool], top_n=top_k,
                ))
                scores = {}
                for index, score in ranked:
                    score = float(score)
                    if 0 <= index < len(pool) and math.isfinite(score):
                        scores[index] = score
                if not scores:
                    raise ValueError("重排未返回有效候选")
                ordered = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
                fused = [{**pool[index], "rerank_score": score, "score": score} for index, score in ordered] + [
                    hit for index, hit in enumerate(fused) if index not in scores
                ]
            except Exception as exc:
                effective, reason = "hybrid", f"重排失败（{type(exc).__name__}）"

    page = fused[:top_k]
    with service.sessions() as session:
        groups = memberships(session, [
            ("knowledge" if row["kind"] == "knowledge" else "source", row["asset_id"]) for row in page
        ])
    output = []
    for rank, hit in enumerate(page, 1):
        kind = "knowledge" if hit["kind"] == "knowledge" else "source"
        output.append({
            **{key: hit.get(key) for key in ("kind", "asset_id", "version_id", "title", "segment_id", "page",
                                            "chunk_index", "start", "end", "snippet", "retrieval_mode")},
            "rank": rank, "collections": groups.get((kind, hit["asset_id"]), []),
            "scores": {"final": hit["score"], "bm25": hit.get("bm25_score"), "bm25_rank": hit.get("bm25_rank"),
                       "vector_similarity": hit.get("vector_score"), "vector_rank": hit.get("vector_rank"),
                       "rrf": hit.get("rrf_score"), "rerank": hit.get("rerank_score")},
        })
    timing["total_ms"] = round((perf_counter() - started) * 1000, 3)
    return {
        "requested_mode": mode, "effective_mode": effective, "degraded_reason": reason,
        "active_profile_id": settings["active_profile_id"], "timing_ms": timing, "hits": output,
    }
