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
    limit: int = 10,
) -> list[dict]:
    """两类索引分值尺度不同，按每路名次交错；统一候选仍没有引用权限。"""
    if scope not in {"all", "knowledge", "material"}:
        raise ValueError("未知的资料检索范围")
    if limit <= 0:
        return []
    limit = min(limit, 30)
    knowledge_hits = [
        {"kind": "knowledge", "asset_id": item["document_id"], **item}
        for item in knowledge.search(query, limit=limit, root_id=root_id)
    ] if scope != "material" else []
    material_hits = [
        {"kind": "material", "asset_id": item["source_id"], **item}
        for item in sources.search(query, root_id=root_id, source_ids=source_ids, limit=limit)
    ] if scope != "knowledge" else []
    return [
        item for pair in zip_longest(knowledge_hits, material_hits) for item in pair
        if item is not None
    ][:limit]
