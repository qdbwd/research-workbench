"""联合检索保留各来源的固定版本和定位，并与证据读取保持分离。"""

from itertools import zip_longest
from typing import Literal

from knowledgeops_agent.knowledge.service import KnowledgeService
from knowledgeops_agent.sources.service import SourceService

ReferenceScope = Literal["all", "knowledge", "material"]


def search_references(
    knowledge: KnowledgeService,
    sources: SourceService,
    query: str,
    *,
    root_id: str,
    scope: ReferenceScope = "all",
    source_ids: list[str] | None = None,
    document_ids: list[str] | None = None,
    limit: int = 10,
    vector=None,
    use_rerank: bool | None = None,
) -> list[dict]:
    """两类索引分值尺度不同，按每路名次交错；若注入向量服务且已启用则走混合 RRF + 可选重排。"""
    if vector is not None:
        return vector.search_references(
            knowledge,
            sources,
            query,
            root_id=root_id,
            scope=scope,
            source_ids=source_ids,
            document_ids=document_ids,
            limit=limit,
            use_rerank=use_rerank,
        )
    if scope not in {"all", "knowledge", "material"}:
        raise ValueError("未知的资料检索范围")
    if limit <= 0:
        return []
    limit = min(limit, 30)
    knowledge_hits = [
        {"kind": "knowledge", "asset_id": item["document_id"], **item}
        for item in knowledge.search(query, limit=limit, root_id=root_id, document_ids=document_ids)
    ] if scope != "material" else []
    material_hits = [
        {"kind": "material", "asset_id": item["source_id"], **item}
        for item in sources.search(query, root_id=root_id, source_ids=source_ids, limit=limit)
    ] if scope != "knowledge" else []
    return [
        item for pair in zip_longest(knowledge_hits, material_hits) for item in pair
        if item is not None
    ][:limit]
