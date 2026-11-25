"""从可信会话绑定生成研发运行；旧研究请求不能在新前提下执行。"""

from sqlalchemy import select

from knowledgeops_agent.app.runs.service import RunStopped
from knowledgeops_agent.persistence.models import (
    ResearchBranch,
    ResearchDelivery,
    ResearchRevision,
    ResearchTask,
    Run,
)
from knowledgeops_agent.research import methods


def admit(session, conversation_id: str):
    """调用者已持有运行控制锁，与消息、Run 和 outbox 一并提交。"""
    from knowledgeops_agent.research.service import _append, require_writable_branch

    task = session.scalar(select(ResearchTask).where(
        ResearchTask.home_conversation_id == conversation_id,
        ResearchTask.status.in_(["open", "paused", "awaiting_acceptance", "completed"]),
        ResearchTask.deleted_at.is_(None),
    ))
    if task is None:
        return None
    branch = session.get(ResearchBranch, task.writing_branch_id)
    require_writable_branch(branch)
    if branch.pending_delivery_id:
        from knowledgeops_agent.research.service import ResearchConflict

        raise ResearchConflict("delivery_finalizing", "研发成果仍在收尾，请先查看提交状态。")
    revision = session.get(ResearchRevision, branch.head_revision_id)
    review_only = task.status != "open"
    if not review_only and not revision.state.get("method_bundle_ref"):
        revision = _append(
            session, branch,
            {**revision.state, "method_bundle_ref": methods.freeze(session)},
            parent=revision, reason="首次开展研发，固定专业方法", origin="method_binding",
            actor="system",
        )
    from knowledgeops_agent.research.autonomy import attach

    binding = {
        "task_id": task.id,
        "branch_id": branch.id,
        "branch_name": branch.name,
        "research_revision_id": revision.id,
        "control_version": task.control_version,
    }
    if review_only:
        binding["review_only"] = True
    elif permit_id := attach(session, task, revision):
        binding["permit_id"] = permit_id
    return binding


def active_binding(session, binding):
    """重新核对真实任务与活动 head；已过时的 Run 不能更换目标后继续。"""
    task = session.get(ResearchTask, binding["task_id"])
    if task is None or task.deleted_at is not None or task.status == "closed":
        raise RunStopped("cancelled: 研发任务已取消或删除")
    branch = session.get(ResearchBranch, binding["branch_id"])
    expected = {"paused", "awaiting_acceptance", "completed"} if binding.get("review_only") else {"open"}
    if (
        task.status not in expected or task.control_version != binding["control_version"]
        or branch is None or branch.task_id != task.id or branch.archived_at is not None
        or branch.head_revision_id != binding["research_revision_id"]
    ):
        raise RunStopped("研发状态或研究简报已变化，请在新版本下继续。")
    if branch.pending_delivery_id:
        raise RunStopped("研发成果已提交，等待执行收尾。")
    return branch


def guard(session, root, active_ms=None, *, check_permit=True):
    if binding := root.input_data.get("research"):
        active_binding(session, binding)
        if check_permit:
            from knowledgeops_agent.research.autonomy import active_permit

            active_permit(session, root, active_ms)


def apply_control(session, jobs, task, branch, action):
    """在研究状态和回执的事务内使用原 Run 控制，避免跨事务留下半暂停。"""
    from knowledgeops_agent.research.autonomy import apply_control as control_permit

    permits = control_permit(session, task, action)
    withdrawn = []
    if action in {"pause", "cancel"}:
        from knowledgeops_agent.research.experiments import cancel_branch

        for research_branch in session.scalars(select(ResearchBranch).where(ResearchBranch.task_id == task.id)):
            cancel_branch(session, research_branch.id, "任务已暂停或结束，撤回实验授权。")
            if research_branch.pending_delivery_id:
                withdrawn.append(session.get(ResearchDelivery, research_branch.pending_delivery_id))
    withdrawn_runs = {delivery.submit_run_id for delivery in withdrawn}
    runs = session.scalars(select(Run).where(
        Run.parent_id.is_(None),
        Run.lifecycle_state == "active",
        Run.input_data["research"]["task_id"].as_string() == task.id,
        Run.status.in_(["queued", "running", "paused", "waiting_input", "waiting_children", "interrupted"]),
    ).order_by(Run.id)).all()
    for run in runs:
        # 已提交成果的图只需要停止。让它进入可恢复暂停会卡住自己的收尾屏障。
        if action == "cancel" or run.id in withdrawn_runs:
            jobs.cancel(run.id, session=session)
        elif action == "pause":
            if run.status != "interrupted":
                jobs.pause(run.id, session=session)
        elif action == "resume":
            if run.status == "running":
                raise ValueError("研发仍在安全边界收尾，请等运行暂停后再继续。")
            if run.status != "paused":
                continue
            binding = run.input_data["research"]
            owner = branch if binding["branch_id"] == branch.id else session.get(ResearchBranch, binding["branch_id"])
            permit = permits.get(binding.get("permit_id"))
            exhausted = permit is not None and permit.status == "exhausted"
            if exhausted or owner is None or binding["research_revision_id"] != owner.head_revision_id:
                jobs.cancel(run.id, session=session)
                continue
            run.input_data = {
                **run.input_data, "research": {**binding, "control_version": task.control_version},
            }
            jobs.resume(run.id, session=session)
    if withdrawn:
        from knowledgeops_agent.research.deliveries import advance

        for delivery in withdrawn:
            advance(session, jobs, delivery)


def background(session, binding):
    branch = active_binding(session, binding)
    revision = session.get(ResearchRevision, branch.head_revision_id)
    state = revision.state
    from knowledgeops_agent.persistence.models import ResearchExperiment, ResearchPermit
    from knowledgeops_agent.research.autonomy import view
    from knowledgeops_agent.research.deliveries import latest_for_branch

    permit = session.get(ResearchPermit, binding["permit_id"]) if "permit_id" in binding else None
    delivery = latest_for_branch(session, branch.id)
    attempts = session.scalars(select(ResearchExperiment).where(
        ResearchExperiment.branch_id == branch.id,
    ).order_by(ResearchExperiment.created_at.desc(), ResearchExperiment.id.desc()).limit(6))
    return {
        **binding,
        "phase": state["phase"],
        "capabilities": state["active_capabilities"],
        "brief": state["brief"],
        "material_refs": state["material_refs"],
        "method_bundle_ref": state["method_bundle_ref"],
        "has_research_map": bool(state.get("map_ref")),
        "methods": methods.for_state(session, state) if state["method_bundle_ref"] else {},
        "autonomy": view(permit),
        "experiments": {
            "enabled": session.info.get("research_experiment_exchange") is not None,
            "recent": [
                {"id": row.id, "status": row.status, "question": row.specification["question"],
                 "evidence_id": row.evidence_id} for row in attempts
            ],
        },
        "latest_delivery": {
            "status": delivery.status, "title": delivery.content["title"],
            "review_reason": delivery.review_reason,
        } if delivery else None,
    }
