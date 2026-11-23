"""提交固定成果，再由运行控制确认收尾，最终由用户验收。"""

from sqlalchemy import select

from knowledgeops_agent.app.conversations import fingerprint, touch_messages
from knowledgeops_agent.app.lifecycle.references import replace_references
from knowledgeops_agent.app.runs.children import unsettled_children
from knowledgeops_agent.app.runs.control import lock_control
from knowledgeops_agent.persistence.models import (
    ChatMessage,
    CommandExecution,
    FileOperation,
    MCPCall,
    ResearchBranch,
    ResearchDelivery,
    ResearchRevision,
    ResearchTask,
    Run,
    SourceVersion,
    new_id,
)
from knowledgeops_agent.research import autonomy, evidence
from knowledgeops_agent.research.service import (
    ResearchConflict,
    _append,
    _read_receipt,
    _save_receipt,
    task_view,
)

_STOPPABLE = ("queued", "running", "paused", "waiting_input", "waiting_children")


def view(session, row):
    branch = session.get(ResearchBranch, row.branch_id)
    return {
        "id": row.id, "branch_id": row.branch_id, "source_revision_id": row.source_revision_id,
        "submit_run_id": row.submit_run_id, "status": row.status, "drain_state": row.drain_state,
        "content": row.content, "blockers": row.blockers, "review_reason": row.review_reason,
        "evidence": evidence.views(session, row.content.get("evidence_ids", []), task_id=branch.task_id),
        "created_at": row.created_at.isoformat(),
        "reviewed_at": row.reviewed_at.isoformat() if row.reviewed_at else None,
    }


def for_task(session, task_id):
    task = session.get(ResearchTask, task_id)
    if task is None or task.deleted_at is not None:
        raise KeyError("没有找到该研发任务。")
    return task, session.get(ResearchBranch, task.main_branch_id)


def list_deliveries(sessions, task_id):
    with sessions() as session:
        _, branch = for_task(session, task_id)
        items = list(session.scalars(select(ResearchDelivery).where(
            ResearchDelivery.branch_id == branch.id,
        ).order_by(ResearchDelivery.created_at.desc(), ResearchDelivery.id.desc()).limit(20)))
        return {"items": [view(session, row) for row in items]}


def pending_for_run(session, root):
    binding = root.input_data.get("research")
    if not binding:
        return None
    branch = session.get(ResearchBranch, binding["branch_id"])
    if branch and branch.pending_delivery_id:
        row = session.get(ResearchDelivery, branch.pending_delivery_id)
        if row and row.submit_run_id == root.id:
            return row
    return None


def submit(jobs, run_id, worker_id, call_id, content):
    from knowledgeops_agent.research.execution import active_binding
    from knowledgeops_agent.research.experiments import cancel_branch

    key = "research:delivery:" + fingerprint([run_id, call_id])
    request_hash = fingerprint(content)
    with jobs.sessions.begin() as session:
        lock_control(session)
        prior = _read_receipt(session, key, request_hash)
        if prior is not None:
            return prior
        root, run = jobs.execution_guard(session, run_id, worker_id)
        binding = root.input_data.get("research")
        if run.id != root.id or not call_id or not binding or binding.get("review_only"):
            raise PermissionError("只有当前研发主运行可以提交成果。")
        branch = active_binding(session, binding)
        head = session.get(ResearchRevision, branch.head_revision_id)
        previous_refs = head.state.get("provenance_refs", [])
        pinned = {ref.get("evidence_id") or ref.get("artifact_id") for ref in previous_refs}
        identifiers = list(dict.fromkeys(content["evidence_ids"]))
        for identifier in identifiers:
            evidence.scope_checked(session, identifier, branch, pinned)
        index = {item["source_version_id"]: item for item in head.state["material_refs"]}
        materials = []
        for identifier in dict.fromkeys(content["source_version_ids"]):
            source = session.get(SourceVersion, identifier)
            if identifier not in index or source is None or source.status != "ready":
                raise ValueError("成果包含未关联或无法读取的资料版本。")
            materials.append(index[identifier])
        if not identifiers and not content["limitations"]:
            raise ValueError("缺少固定依据时请具体说明依据不足的限制。")
        delivery_id = new_id()
        head = _append(session, branch, {
            **head.state,
            "deliveries": [*head.state.get("deliveries", []), delivery_id],
            "provenance_refs": [
                *previous_refs,
                *({"kind": "delivery_evidence", **evidence.projection(session, identifier)}
                  for identifier in identifiers if identifier not in pinned),
            ],
        }, parent=head, reason="固定提交成果及证据", origin="delivery_prepared", actor="assistant")
        root.input_data = {
            **root.input_data, "research": {**binding, "research_revision_id": head.id},
        }
        row = ResearchDelivery(
            id=delivery_id, branch_id=branch.id, source_revision_id=head.id, submit_run_id=root.id,
            content={**content, "materials": materials, "evidence_ids": identifiers},
        )
        session.add(row)
        session.flush()
        branch.pending_delivery_id = row.id
        cancel_branch(session, branch.id, "准备交付，撤回尚未收录的实验授权。")
        autonomy.invalidate(session, branch.task_id, "已提交成果，等待收尾与用户验收。")
        for child in unsettled_children(session, root.id):
            jobs.cancel(child.id, session=session)
        for queued in session.scalars(select(Run).where(
            Run.parent_id.is_(None), Run.id != root.id, Run.status == "queued",
            Run.input_data["research"]["branch_id"].as_string() == branch.id,
        )):
            jobs.cancel(queued.id, session=session)
        jobs._event(session, root, "research_delivery_finalizing", {"delivery_id": row.id})
        return _save_receipt(session, key, request_hash, {
            "delivery_id": row.id, "source_revision_id": head.id, "status": "finalizing",
        })


def _blockers(session, root_id):
    blockers = []
    for identifier, status in session.execute(select(Run.id, Run.status).where(
        Run.root_id == root_id, Run.status.in_(_STOPPABLE),
    )):
        blockers.append({"kind": "run", "id": identifier, "status": status})
    for model, kind, statuses in (
        (CommandExecution, "command", ("running", "unknown")),
        (FileOperation, "file", ("running", "unknown", "conflict")),
        (MCPCall, "mcp", ("running", "unknown")),
    ):
        scope = model.run_id.in_(select(Run.id).where(Run.root_id == root_id))
        for identifier, status in session.execute(select(model.id, model.status).where(scope, model.status.in_(statuses))):
            blockers.append({"kind": kind, "id": identifier, "status": status})
    return sorted(blockers, key=lambda item: (item["kind"], item["id"]))


def advance(session, jobs, row):
    """由原运行的结束或显式复查询触发。重复事件不再发布同一提交消息。"""
    branch = session.get(ResearchBranch, row.branch_id)
    if branch.pending_delivery_id != row.id:
        return
    task = session.get(ResearchTask, branch.task_id)
    if task.status != "open" or branch.head_revision_id != row.source_revision_id:
        row.status = "superseded"
    session.flush()
    from knowledgeops_agent.research.experiments import blockers as experiment_blockers

    row.blockers = [*_blockers(session, row.submit_run_id), *experiment_blockers(session, branch.id)]
    if row.blockers:
        unknown = any(item["status"] in {"unknown", "conflict"} for item in row.blockers)
        row.drain_state = "blocked" if unknown else "pending"
        if row.status != "superseded":
            row.status = "blocked" if unknown else "finalizing"
        return
    row.drain_state = "settled"
    branch.pending_delivery_id = None
    if row.status == "superseded":
        return
    row.status = "submitted"
    task.status = "awaiting_acceptance"
    task.control_version += 1
    root = session.get(Run, row.submit_run_id)
    if root is None or root.lifecycle_state != "active":
        return
    answer = f"已提交研发成果《{row.content['title']}》，待验收。"
    root.status, root.stop_reason = "completed", None
    root.outcome = {**root.outcome, "kind": "research_delivery", "answer": answer, "research_delivery_id": row.id}
    root.finished_at = jobs.now()
    jobs._event(session, root, "finished", {"status": root.status, "outcome": root.outcome})
    if task.home_conversation_id:
        workbench = f"/research/{task.id}"
        detail = f"{workbench}/branches/{branch.id}/revisions/{row.source_revision_id}"
        text = answer + f" [查看研发成果]({workbench}?delivery={row.id}) · [固定研究版本]({detail})"
        session.add(ChatMessage(
            id=row.id, conversation_id=task.home_conversation_id, run_id=root.id, role="assistant",
            content=text, user_sequence=root.conversation_sequence or 0, created_at=jobs.now(),
        ))
        replace_references(session, "message", row.id, text)
        touch_messages(session, task.home_conversation_id, activity_at=jobs.now())


def recover(session, jobs):
    for row in session.scalars(select(ResearchDelivery).join(
        ResearchBranch, ResearchBranch.pending_delivery_id == ResearchDelivery.id,
    )):
        advance(session, jobs, row)


def refresh_drain(sessions, jobs, task_id, delivery_id):
    with sessions.begin() as session:
        lock_control(session)
        _, branch = for_task(session, task_id)
        row = session.get(ResearchDelivery, delivery_id)
        if row is None or row.branch_id != branch.id:
            raise KeyError("没有找到该研发成果。")
        advance(session, jobs, row)
        return view(session, row)


def review(sessions, task_id, delivery_id, body):
    key = "research:review:" + fingerprint([task_id, delivery_id, body.operation_id])
    request_hash = fingerprint(body.model_dump(exclude={"operation_id"}))
    with sessions.begin() as session:
        lock_control(session)
        prior = _read_receipt(session, key, request_hash)
        if prior is not None:
            return prior
        task, branch = for_task(session, task_id)
        row = session.get(ResearchDelivery, delivery_id)
        if row is None or row.branch_id != branch.id:
            raise KeyError("没有找到该研发成果。")
        if (
            task.status != "awaiting_acceptance" or task.control_version != body.expected_control_version
            or row.status != "submitted" or row.drain_state != "settled"
            or branch.head_revision_id != row.source_revision_id or body.source_revision_id != row.source_revision_id
        ):
            raise ResearchConflict("delivery_conflict", "待验收成果或研发状态已变化，请重新核对。", task=task, branch=branch)
        from knowledgeops_agent.persistence.models import utcnow

        row.review_reason, row.reviewed_at = body.reason, utcnow()
        if body.action == "accept":
            for identifier in row.content.get("evidence_ids", []):
                evidence.scope_checked(session, identifier, branch, row.content["evidence_ids"])
            row.status, task.status = "accepted", "completed"
        else:
            row.status, task.status = "changes_requested", "open"
        task.control_version += 1
        head = session.get(ResearchRevision, branch.head_revision_id)
        return _save_receipt(session, key, request_hash, {"task": task_view(task, head), "delivery": view(session, row)})


def latest_for_branch(session, branch_id):
    return session.scalar(select(ResearchDelivery).where(ResearchDelivery.branch_id == branch_id)
                          .order_by(ResearchDelivery.created_at.desc(), ResearchDelivery.id.desc()).limit(1))
