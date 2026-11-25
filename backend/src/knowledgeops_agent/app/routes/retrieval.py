"""RAG 检索设置与 pgvector 向量库状态管理接口。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from knowledgeops_agent.app.dependencies import svc
from knowledgeops_agent.retrieval.embeddings import EmbeddingError
from knowledgeops_agent.retrieval.vector import RagSettingsConflict

router = APIRouter()


class RagSettingsPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(ge=0)
    hybrid_enabled: bool | None = None
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
