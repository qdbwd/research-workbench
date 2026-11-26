"""RAG 检索设置与 pgvector 向量库状态管理接口。"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from knowledgeops_agent.app.dependencies import svc
from knowledgeops_agent.app.runs.control import lock_control
from knowledgeops_agent.retrieval.collections import (
    AssetKind,
    IndexStatus,
    RagCollections,
    RagConflict,
    live_asset,
)
from knowledgeops_agent.retrieval.embeddings import EmbeddingError
from knowledgeops_agent.retrieval.indexing import enqueue_asset_index, job_view, retry_failed_jobs
from knowledgeops_agent.retrieval.vector import RagSettingsConflict

router = APIRouter()


class RagSettingsPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(ge=0)
    hybrid_enabled: bool | None = None
    auto_index_enabled: bool | None = None
    rerank_enabled: bool | None = None
    contextual_embedding_enabled: bool | None = None
    rrf_k: int | None = Field(default=None, ge=1, le=1000)
    rerank_top_k: int | None = Field(default=None, ge=1, le=100)
    active_profile_id: str | None = None


class RebuildEmbeddingsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contextual: bool | None = None
    activate: bool = True
    mark_others_stale: bool = False
    force: bool = False


@router.get("/api/retrieval/status")
def retrieval_status(request: Request):
    """查看 pgvector 扩展、向量覆盖率、档案列表及当前生效的 RAG/重排状态。"""
    return svc(request).vector.status()


@router.patch("/api/retrieval/settings")
def update_retrieval_settings(payload: RagSettingsPatch, request: Request):
    """按乐观并发修订更新 RAG 混合检索、可选重排与上下文增强开关。"""
    kwargs: dict = {"expected_revision": payload.expected_revision}
    for field in (
        "hybrid_enabled",
        "auto_index_enabled",
        "rerank_enabled",
        "contextual_embedding_enabled",
        "rrf_k",
        "rerank_top_k",
    ):
        value = getattr(payload, field)
        if value is not None:
            kwargs[field] = value
    if "active_profile_id" in payload.model_fields_set:
        kwargs["active_profile_id"] = payload.active_profile_id
    try:
        return svc(request).vector.update_settings(**kwargs)
    except RagSettingsConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc).strip("'")) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/api/retrieval/rebuild")
def rebuild_embeddings(payload: RebuildEmbeddingsInput, request: Request):
    """显式触发幂等增量/全量向量索引构建，返回构建统计与最新向量库状态。"""
    vector = svc(request).vector
    try:
        summary = vector.rebuild_embeddings(
            contextual=payload.contextual,
            activate=payload.activate,
            mark_others_stale=payload.mark_others_stale,
            force=payload.force,
        )
    except EmbeddingError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"summary": summary, "status": vector.status()}


@router.delete("/api/retrieval/profiles/{profile_id:path}")
def delete_embedding_profile(profile_id: str, request: Request):
    """删除非当前启用中的历史/陈旧向量档案（级联清理派生向量行）。"""
    vector = svc(request).vector
    try:
        removed = vector.delete_profile(profile_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc).strip("'")) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {**removed, "status": vector.status()}


class WorkbenchInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class CollectionInput(WorkbenchInput):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=500)


class CollectionPatch(WorkbenchInput):
    expected_revision: int = Field(ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=500)


class CollectionItemInput(WorkbenchInput):
    asset_kind: AssetKind
    asset_id: str = Field(min_length=1, max_length=36)


class CollectionItemsInput(WorkbenchInput):
    expected_revision: int = Field(ge=1)
    items: list[CollectionItemInput] = Field(max_length=500)


class ChunkStatusInput(WorkbenchInput):
    version_id: str = Field(min_length=1, max_length=36)
    chunk_index: int = Field(ge=0)
    segment_id: str | None = Field(default=None, min_length=1, max_length=36)
    enabled: bool


class HitTestInput(WorkbenchInput):
    query: str = Field(min_length=1, max_length=500)
    mode: Literal["bm25", "vector", "hybrid", "hybrid_rerank"] = "hybrid"
    scope: Literal["all", "knowledge", "material"] = "all"
    collection_ids: list[str] | None = Field(default=None, max_length=500)
    top_k: int = Field(default=5, ge=1, le=20)
    rrf_k: int | None = Field(default=None, ge=1, le=1000)
    rerank_top_k: int | None = Field(default=None, ge=1, le=100)
    min_vector_similarity: float | None = Field(default=None, ge=0.0, le=1.0)


@router.get("/api/retrieval/collections")
def list_collections(request: Request):
    return RagCollections(svc(request).sessions).list()


@router.post("/api/retrieval/collections", status_code=201)
def create_collection(payload: CollectionInput, request: Request):
    return RagCollections(svc(request).sessions).create(payload.name, payload.description)


@router.patch("/api/retrieval/collections/{collection_id}")
def patch_collection(collection_id: str, payload: CollectionPatch, request: Request):
    return RagCollections(svc(request).sessions).update(collection_id, **payload.model_dump())


@router.delete("/api/retrieval/collections/{collection_id}")
def delete_collection(collection_id: str, request: Request, expected_revision: int = Query(ge=1)):
    return RagCollections(svc(request).sessions).delete(collection_id, expected_revision)


@router.put("/api/retrieval/collections/{collection_id}/items")
def replace_collection_items(collection_id: str, payload: CollectionItemsInput, request: Request):
    return RagCollections(svc(request).sessions).replace(collection_id, payload.expected_revision, payload.items)


@router.get("/api/retrieval/assets")
def list_rag_assets(request: Request, collection_id: str | None = Query(default=None, max_length=36),
                    asset_kind: AssetKind | None = None, status: IndexStatus | None = None,
                    query: str = Query(default="", max_length=500), offset: int = Query(0, ge=0),
                    limit: int = Query(30, ge=1, le=100)):
    return RagCollections(svc(request).sessions).assets(
        collection_id=collection_id or None, asset_kind=asset_kind, status=status, query=query,
        offset=offset, limit=limit,
    )


@router.post("/api/retrieval/assets/{asset_kind}/{asset_id}/reindex", status_code=202)
def reindex_rag_asset(asset_kind: AssetKind, asset_id: str, request: Request):
    services = svc(request)
    with services.sessions.begin() as session:
        lock_control(session)
        asset = live_asset(session, asset_kind, asset_id, lock=True)
        if asset is None:
            raise KeyError("未找到可索引的全局资产")
        if not services.vector.embedding_provider.enabled:
            raise RagConflict("嵌入服务未配置或受 MODEL_CALLS_ENABLED 保护")
        job = enqueue_asset_index(session, asset_kind=asset_kind, asset_id=asset_id,
                                  version_id=asset[1].id, force_enqueue=True)
        return job_view(job)


@router.post("/api/retrieval/jobs/retry-failed", status_code=202)
def retry_failed_rag_jobs(request: Request):
    services = svc(request)
    if not services.vector.embedding_provider.enabled:
        raise RagConflict("嵌入服务未配置或受 MODEL_CALLS_ENABLED 保护")
    return {"retried": retry_failed_jobs(services.sessions)}


@router.get("/api/retrieval/assets/{asset_kind}/{asset_id}/chunks")
def preview_rag_chunks(asset_kind: AssetKind, asset_id: str, request: Request,
                       offset: int = Query(0, ge=0), limit: int = Query(30, ge=1, le=100),
                       query: str = Query(default="", max_length=500), enabled: bool | None = None):
    return RagCollections(svc(request).sessions).chunks(
        asset_kind, asset_id, offset=offset, limit=limit, query=query, enabled=enabled,
    )


@router.patch("/api/retrieval/assets/{asset_kind}/{asset_id}/chunks/status")
def toggle_rag_chunk(asset_kind: AssetKind, asset_id: str, payload: ChunkStatusInput, request: Request):
    return RagCollections(svc(request).sessions).toggle(asset_kind, asset_id, **payload.model_dump())


@router.post("/api/retrieval/hit-test")
def retrieval_hit_test(payload: HitTestInput, request: Request):
    services = svc(request)
    return services.vector.hit_test(services.knowledge, services.sources, **payload.model_dump())
