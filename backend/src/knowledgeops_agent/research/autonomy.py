"""真人授权、跨 Run 预算，以及由已完成根运行触发的有限续跑。"""

from sqlalchemy import select

from knowledgeops_agent.app.conversations import fingerprint
from knowledgeops_agent.app.runs.control import lock_control
from knowledgeops_agent.app.runs.service import RunStopped
from knowledgeops_agent.persistence.models import (
    Conversation,
    OperationReceipt,
    ResearchBranch,
    ResearchPermit,
    ResearchRevision,
    ResearchTask,
    Run,
    RunDispatch,
    new_id,
)

_UNSETTLED = ("queued", "running", "paused", "waiting_input", "waiting_children")
_EXHAUSTED_REASON = "已达到累计运行额度，请核对已保存的进展。"
SCOPE_KEYS = ("goal", "scope", "constraints", "acceptance_criteria")


def latest(session, task_id):
    return session.scalar(select(ResearchPermit).where(ResearchPermit.task_id == task_id)
                          .order_by(ResearchPermit.created_at.desc(), ResearchPermit.id.desc()).limit(1))


def view(permit):
    return {
        "id": permit.id,
        "status": permit.status,
        "scope_revision_id": permit.scope_revision_id,
        "budgets": permit.budgets,
        "usage": permit.usage,
        "run_id": permit.latest_run_id,
        "reason": permit.reason,
    } if permit else None


def _scope(revision):
    return {
        "brief": {key: revision.state["brief"].get(key, "") for key in SCOPE_KEYS},
        "material_refs": revision.state["material_refs"],
    }


def _matches_scope(session, permit, head):
    return _scope(session.get(ResearchRevision, permit.scope_revision_id)) == _scope(head)


def _available(permit):
    return (
        permit.usage["active_ms"] < permit.budgets["active_seconds"] * 1000
        and permit.usage["model_requests"] < permit.budgets["model_requests"]
        and permit.usage["tool_calls"] < permit.budgets["tool_calls"]
    )


def bound_permit(session, root):
    identifier = root.input_data.get("research", {}).get("permit_id")
    return session.get(ResearchPermit, identifier) if identifier else None


def active_permit(session, root, active_ms=None):
    identifier = root.input_data.get("research", {}).get("permit_id")
    if identifier is None:
        return None
    permit = session.get(ResearchPermit, identifier)
    if not permit or permit.status != "active":
        raise RunStopped("自主授权已暂停、结束或等待明确恢复。")
    if active_ms is not None and time_exhausted(permit, root, active_ms):
        raise RunStopped("research active time budget exhausted")
    return permit


def time_exhausted(permit, root, active_ms):
    return permit is not None and (
        permit.usage["active_ms"] + max(0, active_ms - root.research_accounted_ms)
        >= permit.budgets["active_seconds"] * 1000
    )


def attach(session, task, revision):
    permit = latest(session, task.id)
    if permit and permit.status == "active" and _matches_scope(session, permit, revision):
        return permit.id
    return None


def record_root(session, root):
    permit = bound_permit(session, root)
    if permit:
        permit.latest_run_id = root.id


def settle_time(session, root):
    if root.parent_id is not None or not (permit := bound_permit(session, root)):
        return
    elapsed = max(0, root.active_ms - root.research_accounted_ms)
    if elapsed:
        permit.usage = {**permit.usage, "active_ms": permit.usage["active_ms"] + elapsed}
        root.research_accounted_ms = root.active_ms


def reserve_model(session, jobs, root, run_id, reservation):
    from knowledgeops_agent.harness.models.gateway import ModelBudgetExceeded

    permit = active_permit(session, root, jobs._active_ms(root))
    if permit is None:
        return
    key = "research-model:" + reservation
    digest = fingerprint([permit.id, run_id])
    receipt = session.get(OperationReceipt, key)
    if receipt:
        if receipt.request_hash != digest:
            raise ValueError("研发模型预算回执身份不一致。")
        return
    used = permit.usage["model_requests"]
    if used >= permit.budgets["model_requests"]:
        raise ModelBudgetExceeded("research model request budget exhausted")
    permit.usage = {**permit.usage, "model_requests": used + 1}
    session.add(OperationReceipt(operation_key=key, request_hash=digest, result={"permit_id": permit.id}))


def reserve_tool(session, jobs, root, is_search):
    permit = active_permit(session, root, jobs._active_ms(root))
    if permit is None:
        return
    usage = permit.usage
    if usage["tool_calls"] >= permit.budgets["tool_calls"]:
        raise RunStopped("research tool budget exhausted")
    if is_search and usage["searches"] >= permit.budgets["searches"]:
        raise RunStopped("research search budget exhausted")
    permit.usage = {
        **usage, "tool_calls": usage["tool_calls"] + 1, "searches": usage["searches"] + int(is_search),
    }


def invalidate(session, task_id, reason):
    permit = latest(session, task_id)
    if permit and permit.status in {"active", "paused", "recovery_required"}:
        permit.status, permit.reason = "revoked", reason


def apply_control(session, task, action):
    permit = latest(session, task.id)
    if not permit:
        return
    if action == "cancel":
        invalidate(session, task.id, "研发任务已结束。")
    elif action == "pause" and permit.status == "active":
        permit.status, permit.reason = "paused", "用户已暂停研发。"
    elif action == "resume" and permit.status == "paused":
        if _available(permit):
            permit.status, permit.reason = "active", None
        else:
            permit.status, permit.reason = "exhausted", _EXHAUSTED_REASON
    return permit


def recover(session):
    """即使重启发生在两个 Run 之间，也必须等用户恢复授权。"""
    for permit in session.scalars(select(ResearchPermit).where(ResearchPermit.status == "active")):
        if _available(permit):
            permit.status, permit.reason = "recovery_required", "服务重启，等待用户明确恢复。"
        else:
            permit.status, permit.reason = "exhausted", _EXHAUSTED_REASON


def _blocked(session, conversation_id, skip_id=None):
    from sqlalchemy import and_, or_

    query = select(Run.id).where(
        Run.conversation_id == conversation_id, Run.parent_id.is_(None),
        or_(Run.status.in_(_UNSETTLED), and_(Run.status == "interrupted", Run.outcome["kind"].astext.in_(
            ["waiting_approval", "execution_unknown"],
        ))),
    )
    if skip_id:
        query = query.where(Run.id != skip_id)
    return session.scalar(query.limit(1)) is not None


def _schedule(session, jobs, task, permit, action, request_key):
    """Run、固定研究绑定和 outbox 在同一事务创建，不添加真人聊天消息。"""
    from knowledgeops_agent.app.lifecycle.asset_references import replace_asset_references
    from knowledgeops_agent.app.lifecycle.catalog import reference_value
    from knowledgeops_agent.harness.permissions.settings import runtime_inputs
    from knowledgeops_agent.research.execution import admit

    conversation = session.get(Conversation, task.home_conversation_id)
    binding = admit(session, conversation.id)
    identifier = new_id()
    run = Run(
        id=identifier, root_id=identifier, checkpoint_id=identifier, conversation_id=conversation.id,
        origin="automatic", request_key=request_key,
        task="研发协调器依据用户已确认的授权安排本次工作。只在固定简报和授权范围内推进。\n" + action,
        input_data={
            **runtime_inputs(session, conversation),
            "research": binding, "publication_mode": "preview", "knowledge_only": not permit.budgets["searches"],
            "delegation": True, "auxiliary": False, "source_ids": [],
        },
        budgets=permit.budgets.copy(),
        created_at=jobs.now(),
    )
    session.add(run)
    session.flush()
    session.add(RunDispatch(run_id=identifier, generation=run.generation, next_attempt_at=jobs.now()))
    replace_asset_references(session, "run_input", identifier, reference_value(session, run))
    permit.latest_run_id = identifier
    jobs._event(session, run, "queued", {"status": "queued"})
    return identifier


def control(sessions, jobs, task_id, body):
    from knowledgeops_agent.research.service import (
        ResearchConflict,
        _read_receipt,
        _save_receipt,
        task_view,
    )

    key = "research:autonomy:" + fingerprint([task_id, body.operation_id])
    request_hash = fingerprint(body.model_dump(exclude={"operation_id"}))
    with sessions.begin() as session:
        lock_control(session)
        prior = _read_receipt(session, key, request_hash)
        if prior is not None:
            return prior
        task = session.get(ResearchTask, task_id)
        if task is None or task.deleted_at is not None:
            raise KeyError("没有找到该研发任务。")
        branch = session.get(ResearchBranch, task.main_branch_id)
        if (task.control_version, branch.head_revision_id) != (body.expected_control_version, body.expected_revision_id):
            raise ResearchConflict("autonomy_conflict", "任务或简报已变化，请刷新后确认。", task=task, branch=branch)
        conversation = session.get(Conversation, task.home_conversation_id) if task.home_conversation_id else None
        if (task.status != "open" or branch.pending_delivery_id or conversation is None
                or conversation.deletion_state != "active"):
            raise ResearchConflict("task_unavailable", "请从有可用会话的进行中研发任务启动自主推进。")
        head = session.get(ResearchRevision, branch.head_revision_id)
        permit = latest(session, task.id)
        if body.action == "grant":
            if permit and permit.status in {"active", "paused", "recovery_required"}:
                raise ResearchConflict("permit_exists", "原自主授权仍有效或待恢复，请先处理。")
            if _blocked(session, conversation.id):
                raise ResearchConflict("research_busy", "请等待会话中的运行、回复或审批结束。")
            permit = ResearchPermit(
                id=new_id(), task_id=task.id, branch_id=branch.id, scope_revision_id=head.id,
                budgets=body.budgets.model_dump(),
                usage=dict(model_requests=0, tool_calls=0, searches=0, active_ms=0),
            )
            session.add(permit)
            task.control_version += 1
            _schedule(session, jobs, task, permit, head.state["brief"].get("next_action") or "按简报开展研发。",
                      f"research:{permit.id}:start")
        else:
            if not permit or permit.status != "recovery_required" or not _matches_scope(session, permit, head):
                raise ResearchConflict("permit_unavailable", "没有与当前范围相符且待恢复的授权。")
            if not _available(permit):
                raise ResearchConflict("permit_exhausted", "原自主额度已耗尽。")
            if _blocked(session, conversation.id):
                raise ResearchConflict("research_busy", "请先处理会话中待回复、审批或正在执行的任务。")
            permit.status, permit.reason = "active", None
            run = session.get(Run, permit.latest_run_id) if permit.latest_run_id else None
            if (run and run.lifecycle_state == "active" and run.status in {"interrupted", "failed"}
                    and run.input_data.get("research", {}).get("research_revision_id") == head.id):
                jobs.resume(run.id, session=session)
            else:
                _schedule(session, jobs, task, permit, head.state["brief"].get("next_action") or "核对已有进展后继续。",
                          f"research:{permit.id}:recover:{fingerprint(body.operation_id)}")
        head = session.get(ResearchRevision, branch.head_revision_id)
        return _save_receipt(session, key, request_hash, {"task": task_view(task, head), "permit": view(permit)})


def finished(session, jobs, root):
    if root.parent_id is not None or not (permit := bound_permit(session, root)):
        return
    if permit.status != "active":
        return
    if not _available(permit) or (
        root.status == "budget_exhausted" and "research " in (root.stop_reason or "")
    ):
        permit.status, permit.reason = "exhausted", _EXHAUSTED_REASON
        return
    if permit.latest_run_id != root.id:
        return
    if root.status != "completed":
        permit.status, permit.reason = "recovery_required", root.stop_reason or "研发运行已停止，请核对后继续。"
        return
    intent = root.input_data.get("research_next")
    task = session.get(ResearchTask, permit.task_id)
    branch = session.get(ResearchBranch, permit.branch_id)
    if not intent or task.status != "open" or not task.home_conversation_id:
        return
    if (intent["revision_id"] != branch.head_revision_id
            or intent["control_version"] != task.control_version):
        return
    if _blocked(session, task.home_conversation_id, skip_id=root.id):
        return
    from knowledgeops_agent.research.experiments import awaits_experiment

    if awaits_experiment(session, branch.id):
        return
    # 前一根的终态和下一根同时提交。原终态重放走原 Run 回执，不会再次入队。
    _schedule(session, jobs, task, permit, intent["action"], f"research:{permit.id}:after:{root.id}")
