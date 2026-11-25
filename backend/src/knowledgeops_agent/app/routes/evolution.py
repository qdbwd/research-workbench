"""自进化设置、复盘任务和变更审核接口。"""

from typing import Literal

from fastapi import APIRouter, Query, Request
from pydantic import Field

from knowledgeops_agent.app.dependencies import svc
from knowledgeops_agent.app.schemas import EvolutionReviewInput, EvolutionSettingsUpdate, InputModel
from knowledgeops_agent.evolution.curator import preview
from knowledgeops_agent.evolution.review import resolve_change
from knowledgeops_agent.evolution.service import EvolutionService
from knowledgeops_agent.evolution.settings import settings_view, update_settings

router = APIRouter(prefix="/api/evolution")

JobStatus = Literal["pending", "running", "completed", "failed", "cancelled", "interrupted"]
ReviewState = Literal[
    "applied",
    "pending_review",
    "approved",
    "rejected",
    "superseded",
    "rolled_back",
    "skipped_conflict",
]


class CuratorInput(InputModel):
    request_key: str = Field(min_length=1, max_length=200)
    use_model_merge: bool = False


@router.get("/curator/preview")
def curator_preview(request: Request):
    return preview(svc(request))


@router.post("/curator", status_code=202)
def enqueue_curator(body: CuratorInput, request: Request):
    return EvolutionService(svc(request)).trigger_curator(**body.model_dump())


@router.get("/settings")
def get_settings(request: Request):
    services = svc(request)
    with services.sessions() as session:
        return settings_view(session, services.jobs.now())


@router.patch("/settings")
def patch_settings(body: EvolutionSettingsUpdate, request: Request):
    services = svc(request)
    return update_settings(
        services.sessions,
        body.expected_revision,
        body.model_dump(exclude={"expected_revision"}, exclude_unset=True),
        now=services.jobs.now,
    )


@router.post("/reviews", status_code=202)
def create_review(body: EvolutionReviewInput, request: Request):
    return EvolutionService(svc(request)).trigger_manual_review(**body.model_dump())


@router.get("/jobs")
def jobs(
    request: Request,
    conversation_id: str | None = None,
    status: JobStatus | None = None,
    limit: int = Query(default=50, ge=1, le=100),
):
    return EvolutionService(svc(request)).list_jobs(
        conversation_id=conversation_id,
        status=status,
        limit=limit,
    )


@router.get("/changes")
def changes(
    request: Request,
    target_kind: Literal["memory", "skill"] | None = None,
    review_state: ReviewState | None = None,
    conversation_id: str | None = None,
    limit: int = Query(default=50, ge=1, le=100),
):
    return EvolutionService(svc(request)).list_changes(
        target_kind=target_kind,
        review_state=review_state,
        conversation_id=conversation_id,
        limit=limit,
    )


@router.post("/changes/{change_id}/approve")
def approve(change_id: str, request: Request):
    return resolve_change(svc(request), change_id, "approve")


@router.post("/changes/{change_id}/reject")
def reject(change_id: str, request: Request):
    return resolve_change(svc(request), change_id, "reject")


@router.post("/changes/{change_id}/rollback")
def rollback(change_id: str, request: Request):
    return resolve_change(svc(request), change_id, "rollback")
