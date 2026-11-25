"""手动启用研发、读写简报及查看固定版本的 HTTP 合同。"""

from typing import Annotated

from fastapi import APIRouter, Query, Request
from pydantic import Field

from knowledgeops_agent.app.dependencies import svc
from knowledgeops_agent.research import autonomy, commits, deliveries, experiments, maps
from knowledgeops_agent.research.contracts import (
    AdoptFromResearchCommit,
    AdoptResearchConclusion,
    ArchiveResearchBranch,
    CommitAdoptionPreview,
    ControlAutonomy,
    ControlResearch,
    EditResearchMap,
    ExperimentAction,
    ForkResearchCommit,
    GoalText,
    Identity,
    Input,
    MapAdoptionPreview,
    OperationId,
    ResearchCommand,
    ReviewDelivery,
    ReviseResearch,
    SaveResearchCommit,
    SetResearchFocus,
    StartResearch,
    SwitchResearchWriteBranch,
)
from knowledgeops_agent.research.service import ResearchService

router = APIRouter()


class ActivateResearch(Input):
    operation_id: OperationId
    conversation_id: Identity
    title: Annotated[str, Field(min_length=1, max_length=300)] | None = None
    goal: GoalText


def service(request: Request) -> ResearchService:
    dependencies = svc(request)
    return ResearchService(dependencies.sessions, jobs=dependencies.jobs)


@router.post("/api/research-tasks", status_code=201)
def activate(body: ActivateResearch, request: Request):
    title = body.title or body.goal.splitlines()[0][:300]
    return service(request).create(body.conversation_id, body.operation_id, title, body.goal)


@router.get("/api/research-tasks")
def research_tasks(
    request: Request,
    limit: int = Query(20, ge=1, le=100),
    cursor: str | None = Query(None, max_length=36),
):
    return service(request).tasks(limit, cursor)


@router.get("/api/conversations/{conversation_id}/research-task")
def current_for_conversation(conversation_id: str, request: Request):
    return service(request).for_conversation(conversation_id)


@router.post("/api/conversations/{conversation_id}/research-task", status_code=201)
def start_from_chat(conversation_id: str, body: StartResearch, request: Request):
    return service(request).start_from_chat(conversation_id, body)


@router.get("/api/conversations/{conversation_id}/research-task/start-receipt")
def start_receipt(conversation_id: str, operation_id: OperationId, request: Request):
    return service(request).start_receipt(conversation_id, operation_id)


@router.get("/api/research-tasks/{task_id}")
def research_task(task_id: str, request: Request):
    return service(request).get(task_id)


@router.post("/api/research-tasks/{task_id}/write-branch")
def switch_write_branch(task_id: str, body: SwitchResearchWriteBranch, request: Request):
    return service(request).switch_write_branch(task_id, body)


@router.get("/api/research-tasks/{task_id}/autonomy")
def research_autonomy(task_id: str, request: Request):
    from knowledgeops_agent.persistence.models import ResearchTask

    with svc(request).sessions() as session:
        task = session.get(ResearchTask, task_id)
        if task is None or task.deleted_at is not None:
            raise KeyError("没有找到该研发任务。")
        return autonomy.view(autonomy.latest(session, task_id, task.writing_branch_id))


@router.post("/api/research-tasks/{task_id}/autonomy")
def control_autonomy(task_id: str, body: ControlAutonomy, request: Request):
    dependencies = svc(request)
    return autonomy.control(dependencies.sessions, dependencies.jobs, task_id, body)


@router.get("/api/research-tasks/{task_id}/map")
def research_map(task_id: str, request: Request, revision_id: Identity | None = None):
    return maps.get(svc(request).sessions, task_id, revision_id)


@router.post("/api/research-tasks/{task_id}/map")
def edit_map(task_id: str, body: EditResearchMap, request: Request):
    return maps.edit(svc(request).sessions, task_id, body)


@router.get("/api/research-tasks/{task_id}/map/compare")
def compare_maps(task_id: str, before: Identity, after: Identity, request: Request):
    return maps.compare(svc(request).sessions, task_id, before, after)


@router.get("/api/research-tasks/{task_id}/map/evidence")
def map_evidence(
    task_id: str, request: Request, offset: int = Query(0, ge=0), limit: int = Query(25, ge=1, le=50),
):
    return maps.evidence_catalog(svc(request).sessions, task_id, offset, limit)


@router.get("/api/research-tasks/{task_id}/evidence/{evidence_id}")
def fixed_excerpt(task_id: str, evidence_id: str, request: Request):
    return maps.read_excerpt(svc(request).sessions, task_id, evidence_id)


@router.post("/api/research-tasks/{task_id}/map/adoption-preview")
def preview_adoption(task_id: str, body: MapAdoptionPreview, request: Request):
    return maps.preview(svc(request).sessions, task_id, body)


@router.post("/api/research-tasks/{task_id}/map/adopt")
def adopt_conclusion(task_id: str, body: AdoptResearchConclusion, request: Request):
    return maps.adopt(svc(request).sessions, task_id, body)


@router.post("/api/research-tasks/{task_id}/map/commit-adoption-preview")
def preview_commit_adoption(task_id: str, body: CommitAdoptionPreview, request: Request):
    return maps.preview(svc(request).sessions, task_id, body, from_commit=True)


@router.post("/api/research-tasks/{task_id}/map/commit-adopt")
def adopt_from_commit(task_id: str, body: AdoptFromResearchCommit, request: Request):
    return maps.adopt(svc(request).sessions, task_id, body, from_commit=True)


@router.get("/api/research-tasks/{task_id}/deliveries")
def research_deliveries(task_id: str, request: Request):
    return deliveries.list_deliveries(svc(request).sessions, task_id)


@router.get("/api/research-tasks/{task_id}/experiments")
def research_experiments(task_id: str, request: Request):
    return experiments.list_attempts(svc(request).sessions, task_id)


@router.get("/api/research-tasks/{task_id}/experiments/{attempt_id}")
def research_experiment(task_id: str, attempt_id: Identity, request: Request):
    return experiments.get_attempt(svc(request).sessions, task_id, attempt_id)


@router.post("/api/research-tasks/{task_id}/experiments/{attempt_id}")
def control_experiment(task_id: str, attempt_id: Identity, body: ExperimentAction, request: Request):
    dependencies = svc(request)
    return experiments.act(dependencies.sessions, task_id, attempt_id, body, jobs=dependencies.jobs)


@router.post("/api/research-tasks/{task_id}/deliveries/{delivery_id}/refresh")
def refresh_delivery(task_id: str, delivery_id: str, request: Request):
    dependencies = svc(request)
    return deliveries.refresh_drain(dependencies.sessions, dependencies.jobs, task_id, delivery_id)


@router.post("/api/research-tasks/{task_id}/deliveries/{delivery_id}/review")
def review_delivery(task_id: str, delivery_id: str, body: ReviewDelivery, request: Request):
    from knowledgeops_agent.evolution.service import EvolutionService

    services = svc(request)
    return deliveries.review(services.sessions, task_id, delivery_id, body, evolution=EvolutionService(services))


@router.post("/api/research-tasks/{task_id}/delete")
def delete_research(task_id: str, body: ResearchCommand, request: Request):
    return service(request).delete(task_id, body)


@router.post("/api/research-tasks/{task_id}/focus")
def set_focus(task_id: str, body: SetResearchFocus, request: Request):
    return service(request).set_focus(task_id, body)


@router.post("/api/research-tasks/{task_id}/control")
def control_research(task_id: str, body: ControlResearch, request: Request):
    return service(request).control(task_id, body)


@router.get("/api/research-tasks/{task_id}/branches")
def research_branches(task_id: str, request: Request):
    return commits.branches(svc(request).sessions, task_id)


@router.post("/api/research-tasks/{task_id}/branches", status_code=201)
def fork_research_commit(task_id: str, body: ForkResearchCommit, request: Request):
    return commits.fork(svc(request).sessions, task_id, body)


@router.post("/api/research-tasks/{task_id}/branches/{branch_id}/archive")
def archive_research_branch(task_id: str, branch_id: str, body: ArchiveResearchBranch, request: Request):
    return commits.archive(svc(request).sessions, task_id, branch_id, body)


@router.post("/api/research-tasks/{task_id}/branches/{branch_id}/unarchive")
def unarchive_research_branch(task_id: str, branch_id: str, body: ArchiveResearchBranch, request: Request):
    return commits.unarchive(svc(request).sessions, task_id, branch_id, body)


@router.post("/api/research-tasks/{task_id}/branches/{branch_id}/restart")
def restart_research_branch(task_id: str, branch_id: str, body: ResearchCommand, request: Request):
    return commits.restart(svc(request).sessions, task_id, branch_id, body)

@router.get("/api/research-tasks/{task_id}/branches/{branch_id}/commit-preview")
def preview_research_commit(task_id: str, branch_id: str, request: Request):
    return commits.preview(svc(request).sessions, task_id, branch_id)


@router.get("/api/research-tasks/{task_id}/branches/{branch_id}/commits")
def research_commits(task_id: str, branch_id: str, request: Request):
    return commits.history(svc(request).sessions, task_id, branch_id)


@router.post("/api/research-tasks/{task_id}/branches/{branch_id}/commits", status_code=201)
def save_research_commit(task_id: str, branch_id: str, body: SaveResearchCommit, request: Request):
    return commits.save(svc(request).sessions, task_id, branch_id, body)


@router.get("/api/research-tasks/{task_id}/branches/{branch_id}/commits/{commit_id}")
def fixed_research_commit(task_id: str, branch_id: str, commit_id: str, request: Request):
    return commits.get(svc(request).sessions, task_id, branch_id, commit_id)


@router.get("/api/research-tasks/{task_id}/commit-comparison")
def compare_research_commits(task_id: str, before: Identity, after: Identity, request: Request):
    return commits.compare(svc(request).sessions, task_id, before, after)


@router.get("/api/research-tasks/{task_id}/branches/{branch_id}/working-diff")
def research_working_diff(task_id: str, branch_id: str, request: Request):
    return commits.working_diff(svc(request).sessions, task_id, branch_id)


@router.get("/api/research-tasks/{task_id}/branches/{branch_id}/revisions")
def revisions(
    task_id: str,
    branch_id: str,
    request: Request,
    limit: int = Query(20, ge=1, le=100),
    before: int | None = Query(None, ge=1),
):
    return service(request).revisions(task_id, branch_id, limit, before)


@router.post("/api/research-tasks/{task_id}/branches/{branch_id}/revisions", status_code=201)
def revise(task_id: str, branch_id: str, body: ReviseResearch, request: Request):
    return service(request).update_brief(task_id, branch_id, body)


@router.get("/api/research-tasks/{task_id}/branches/{branch_id}/revisions/{revision_id}")
def fixed_revision(task_id: str, branch_id: str, revision_id: str, request: Request):
    return service(request).revision(task_id, branch_id, revision_id)
