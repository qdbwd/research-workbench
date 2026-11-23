"""研究状态的持久化边界。真人变更与回执同事务，不触发执行。"""

from sqlalchemy import select, tuple_, update

from knowledgeops_agent.app.conversations import fingerprint, touch_messages
from knowledgeops_agent.app.lifecycle.asset_references import (
    reachable_assets,
    replace_asset_references,
)
from knowledgeops_agent.app.lifecycle.catalog import ReferenceCatalog, research_reference_value
from knowledgeops_agent.app.lifecycle.references import replace_references
from knowledgeops_agent.app.runs.control import lock_control
from knowledgeops_agent.app.runs.service import Budgets, RunService
from knowledgeops_agent.persistence.models import (
    ChatMessage,
    Conversation,
    OperationReceipt,
    ResearchBranch,
    ResearchRevision,
    ResearchTask,
    Run,
    Source,
    SourceVersion,
    new_id,
    utcnow,
)
from knowledgeops_agent.research.contracts import (
    BriefPatch,
    ControlResearch,
    ResearchCommand,
    ReviseResearch,
    SetResearchFocus,
    StartResearch,
)
from knowledgeops_agent.sources.ownership import claim_source, scope_predicate

_CONTROL_TRANSITIONS = {
    "pause": ({"open"}, "paused"),
    "resume": ({"paused"}, "open"),
    "cancel": ({"open", "paused", "awaiting_acceptance"}, "closed"),
}


class ResearchConflict(Exception):
    def __init__(self, code, message, *, task=None, branch=None):
        super().__init__(message)
        self.code = code
        self.latest = {}
        if task is not None and branch is not None:
            self.latest = {
                "current_control_version": task.control_version,
                "current_revision_id": branch.head_revision_id,
                "current_revision_url": (
                    f"/api/research-tasks/{task.id}/branches/{branch.id}/revisions/{branch.head_revision_id}"
                ),
            }


def revision_view(row):
    return {
        "id": row.id,
        "branch_id": row.branch_id,
        "number": row.number,
        "parent_revision_id": row.parent_revision_id,
        "schema_version": row.schema_version,
        "state": row.state,
        "content_hash": row.content_hash,
        "actor": row.actor,
        "origin": row.origin,
        "reason": row.reason,
        "created_at": row.created_at.isoformat(),
    }


def task_view(task, head):
    return {
        "id": task.id,
        "title": task.title,
        "status": task.status,
        "created_at": task.created_at.isoformat(),
        "home_conversation_id": task.home_conversation_id,
        "main_branch_id": task.main_branch_id,
        "control_version": task.control_version,
        "head": revision_view(head),
    }


def detach_conversation(session, conversation_id):
    """随会话删除受理解除入口绑定；任务正文及固定历史保留。"""
    from knowledgeops_agent.research.autonomy import invalidate

    task_id = session.scalar(select(ResearchTask.id).where(ResearchTask.home_conversation_id == conversation_id))
    if task_id:
        invalidate(session, task_id, "研发会话已解除绑定。")
    session.execute(
        update(ResearchTask)
        .where(
            ResearchTask.home_conversation_id == conversation_id,
        )
        .values(home_conversation_id=None)
    )


def _read_receipt(session, key, request_hash):
    receipt = session.get(OperationReceipt, key)
    if receipt is None:
        return None
    if receipt.request_hash != request_hash:
        raise ResearchConflict("operation_conflict", "同一个操作编号已用于不同的请求。")
    return receipt.result


def _save_receipt(session, key, request_hash, result):
    session.add(OperationReceipt(operation_key=key, request_hash=request_hash, result=result))
    return result


def _current_tasks():
    # 同一条查询读取控制版本和 head，避免在两次读取之间拼出跨版本状态。
    return (
        select(ResearchTask, ResearchRevision)
        .join(
            ResearchBranch,
            ResearchBranch.id == ResearchTask.main_branch_id,
        )
        .join(ResearchRevision, ResearchRevision.id == ResearchBranch.head_revision_id)
        .where(ResearchTask.deleted_at.is_(None))
    )


def _append(session, branch, state, *, parent, reason, origin, actor="user"):
    schema_version = 1
    parent_id = parent.id if parent else None
    revision = ResearchRevision(
        id=new_id() if parent else branch.head_revision_id,
        branch_id=branch.id,
        number=parent.number + 1 if parent else 1,
        parent_revision_id=parent_id,
        schema_version=schema_version,
        state=state,
        content_hash=fingerprint([schema_version, state, parent_id]),
        actor=actor,
        origin=origin,
        reason=reason,
    )
    session.add(revision)
    branch.head_revision_id = revision.id
    session.flush()
    # 研究简报在会话删除后仍存在。只把固定材料版本作为该修订的保留依赖。
    replace_asset_references(session, "research_revision", revision.id, research_reference_value(state))
    return revision


def _conversation(session, identity):
    conversation = session.get(Conversation, identity, with_for_update=True)
    if conversation is None:
        raise KeyError("没有找到该会话。")
    if conversation.deletion_state != "active":
        raise ResearchConflict("conversation_unavailable", "会话正在删除或已删除。")
    return conversation


def _new_task(session, conversation_id, title, brief, material_refs=()):
    task = ResearchTask(
        id=new_id(), title=title, home_conversation_id=conversation_id,
        main_branch_id=new_id(), status="open", control_version=1,
    )
    branch = ResearchBranch(
        id=task.main_branch_id, task_id=task.id, name="main", head_revision_id=new_id(),
    )
    # 两个指针由提交时检查：先插入任务，再插入主线与首个修订。
    session.add(task)
    session.flush()
    session.add(branch)
    session.flush()
    head = _append(
        session, branch,
        {
            "brief": brief,
            "phase": "exploration",
            "active_capabilities": [],
            "capability_source": None,
            "capability_reason": "",
            "capability_override": None,
            "context_initialized": False,
            "focus_refs": [],
            "material_refs": list(material_refs),
            "deliveries": [],
            "method_bundle_ref": None,
            "provenance_refs": [],
            "method_dependencies": [],
            "extensions": {},
        },
        parent=None, reason="用户在会话中启用研发", origin="activation",
    )
    return task_view(task, head)


def _record_goal_message(session, conversation, task, goal):
    """聊天记录需要实际关联的 Run；登记已完成的本地动作，既不投递也不借用普通 Agent 运行。"""
    now = utcnow()
    if conversation.user_sequence == 0 and conversation.title in {"新对话", "材料讨论"}:
        conversation.title = goal.splitlines()[0][:30]
    conversation.user_sequence += 1
    conversation.execution_sequence += 1
    run_id = new_id()
    tag = {"kind": "research_started", "research_task_id": task["id"]}
    session.add(Run(
        id=run_id, root_id=run_id, checkpoint_id=run_id, conversation_id=conversation.id,
        kind="research_start", status="completed", task=goal, outcome=tag,
        budgets=Budgets().model_dump(), created_at=now, finished_at=now,
        conversation_sequence=conversation.user_sequence,
        execution_sequence=conversation.execution_sequence,
    ))
    session.flush()
    message = ChatMessage(
        id=new_id(), role="user", conversation_id=conversation.id, run_id=run_id,
        content=goal, user_sequence=conversation.user_sequence, created_at=now,
    )
    session.add(message)
    session.flush()
    replace_references(session, "message", message.id, goal)
    touch_messages(session, conversation.id, activity_at=now)


class ResearchService:
    def __init__(self, sessions, jobs=None):
        self.sessions = sessions
        self.jobs = jobs

    def create(self, conversation_id: str, operation_id: str, title: str, goal: str):
        brief = BriefPatch(goal=goal).model_dump()
        key = "research:create:" + fingerprint([conversation_id, operation_id])
        request_hash = fingerprint([title, brief])
        with self.sessions.begin() as session:
            # 与会话删除受理共用顺序：控制锁后才写研究任务。
            lock_control(session)
            prior = _read_receipt(session, key, request_hash)
            if prior is not None:
                return prior
            _conversation(session, conversation_id)
            if session.scalar(
                select(ResearchTask.id).where(ResearchTask.home_conversation_id == conversation_id)
            ):
                raise ResearchConflict("conversation_bound", "该会话已启用研发，请打开已有简报。")

            return _save_receipt(session, key, request_hash, _new_task(session, conversation_id, title, brief))

    def start_from_chat(self, conversation_id: str, body: StartResearch):
        """同事务登记目标消息、固定材料、简报与回执。丢失响应可只读查回。"""
        key = "research:start:" + fingerprint([conversation_id, body.operation_id])
        request_hash = fingerprint(body.model_dump(exclude={"operation_id"}))
        with self.sessions.begin() as session:
            lock_control(session)
            conversation = _conversation(session, conversation_id)
            prior = _read_receipt(session, key, request_hash)
            if prior is not None:
                return prior
            bound = session.scalar(select(ResearchTask).where(ResearchTask.home_conversation_id == conversation_id))
            if bound is not None:
                if bound.status not in {"closed", "completed"}:
                    raise ResearchConflict("conversation_bound", "该会话已启用研发，请打开已有简报。")
                # 一次只展示一个当前任务，旧任务及简报版本仍能从工作台直接访问。
                bound.home_conversation_id = None
                session.flush()
            materials = []
            visible = scope_predicate(session, conversation_id=conversation_id)
            selected = list(dict.fromkeys(body.source_ids))
            if not selected and conversation.trial_case:
                selected = list(session.scalars(select(Source.id).where(
                    Source.deleted_at.is_(None), visible,
                ).order_by(Source.created_at, Source.id)))
            for source_id in selected:
                source = session.scalar(select(Source).where(Source.id == source_id, Source.deleted_at.is_(None), visible))
                if source is None:
                    raise ValueError("有材料已移除或不在当前会话的可用范围。")
                version = session.scalar(select(SourceVersion).where(SourceVersion.source_id == source.id)
                                         .order_by(SourceVersion.number.desc()).limit(1))
                if version is None or version.status != "ready":
                    raise ValueError(f"材料「{source.title}」还没有可用正文。")
                claim_source(session, source.id, conversation_id=conversation_id)
                materials.append({"source_id": source.id, "source_version_id": version.id, "title": source.title})
            task = _new_task(session, conversation_id, body.goal.splitlines()[0][:300],
                             BriefPatch(goal=body.goal).model_dump(), materials)
            _record_goal_message(session, conversation, task, body.goal)
            return _save_receipt(session, key, request_hash, task)

    def start_receipt(self, conversation_id, operation_id):
        with self.sessions() as session:
            conversation = session.get(Conversation, conversation_id)
            if conversation is None or conversation.deletion_state != "active":
                raise KeyError("没有找到可用的会话。")
            receipt = session.get(OperationReceipt, "research:start:" + fingerprint([conversation_id, operation_id]))
            return {"state": "accepted", "task": receipt.result} if receipt else {"state": "not_accepted"}

    def control(self, task_id: str, body: ControlResearch):
        key = "research:control:" + fingerprint([task_id, body.operation_id])
        request_hash = fingerprint(body.model_dump(exclude={"operation_id"}))
        with self.sessions.begin() as session:
            lock_control(session)
            task = session.get(ResearchTask, task_id, with_for_update=True)
            if task is None or task.deleted_at is not None:
                raise KeyError("没有找到该研发任务。")
            prior = _read_receipt(session, key, request_hash)
            if prior is not None:
                return prior
            branch = session.get(ResearchBranch, task.main_branch_id)
            if task.control_version != body.expected_control_version:
                raise ResearchConflict("control_conflict", "任务状态已更新，请确认后重试。", task=task, branch=branch)
            previous, target = _CONTROL_TRANSITIONS[body.action]
            if task.status not in previous:
                raise ResearchConflict("task_unavailable", "当前状态不支持这个操作。", task=task, branch=branch)
            task.status = target
            task.control_version += 1
            from knowledgeops_agent.research.execution import apply_control

            apply_control(session, self.jobs or RunService(self.sessions), task, branch, body.action)
            head = session.get(ResearchRevision, branch.head_revision_id)
            return _save_receipt(session, key, request_hash, task_view(task, head))

    def delete(self, task_id: str, body: ResearchCommand):
        """撤除任务入口和资产保留根，保留回执和被其他内容引用的固定版本。"""
        key = "research:delete:" + fingerprint([task_id, body.operation_id])
        request_hash = fingerprint(body.expected_control_version)
        with self.sessions.begin() as session:
            lock_control(session)
            prior = _read_receipt(session, key, request_hash)
            if prior is not None:
                return prior
            task = session.get(ResearchTask, task_id, with_for_update=True)
            if task is None:
                raise KeyError("没有找到该研发任务。")
            if task.deleted_at is None:
                if task.control_version != body.expected_control_version:
                    branch = session.get(ResearchBranch, task.main_branch_id)
                    raise ResearchConflict("control_conflict", "任务状态已更新，请复核后删除。", task=task, branch=branch)
                task.deleted_at = utcnow()
                task.status = "closed"
                task.home_conversation_id = None
                task.control_version += 1
                from knowledgeops_agent.research.execution import apply_control

                branch = session.get(ResearchBranch, task.main_branch_id)
                apply_control(session, self.jobs or RunService(self.sessions), task, branch, "cancel")
            return _save_receipt(session, key, request_hash, {"id": task.id, "deleted": True})

    def set_focus(self, task_id: str, body: SetResearchFocus):
        """用户纠正和固定能力使用同一固定版本合同，已有运行不得沿用旧判断。"""
        from knowledgeops_agent.research.methods import canonical_capabilities

        key = "research:focus:" + fingerprint([task_id, body.operation_id])
        request_hash = fingerprint(body.model_dump(exclude={"operation_id"}))
        with self.sessions.begin() as session:
            lock_control(session)
            task = session.get(ResearchTask, task_id, with_for_update=True)
            if task is None or task.deleted_at is not None:
                raise KeyError("没有找到该研发任务。")
            prior = _read_receipt(session, key, request_hash)
            if prior is not None:
                return prior
            branch = session.get(ResearchBranch, task.main_branch_id)
            if body.expected_control_version != task.control_version:
                raise ResearchConflict("control_conflict", "任务状态已变化，请核对最新版。", task=task, branch=branch)
            if body.expected_revision_id != branch.head_revision_id:
                raise ResearchConflict("revision_conflict", "研究焦点已变化，请核对最新版。", task=task, branch=branch)
            if task.status != "open":
                raise ResearchConflict("task_unavailable", "当前任务状态不允许调整研究焦点。")
            head = session.get(ResearchRevision, branch.head_revision_id)
            active = canonical_capabilities(body.capabilities)
            state = {
                **head.state,
                "phase": body.phase,
                "active_capabilities": active,
                "capability_override": active if body.pinned else None,
                "capability_source": "user",
                "capability_reason": body.reason,
            }
            if state != head.state:
                head = _append(session, branch, state, parent=head, reason=body.reason, origin="focus_edit")
            return _save_receipt(session, key, request_hash, task_view(task, head))

    def get(self, task_id):
        with self.sessions() as session:
            pair = session.execute(_current_tasks().where(ResearchTask.id == task_id)).one_or_none()
            if pair is None:
                raise KeyError("没有找到该研发任务。")
            return task_view(*pair)

    def for_conversation(self, conversation_id):
        with self.sessions() as session:
            conversation = session.get(Conversation, conversation_id)
            if conversation is None or conversation.deletion_state != "active":
                raise KeyError("没有找到可用的会话。")
            pair = session.execute(
                _current_tasks().where(
                    ResearchTask.home_conversation_id == conversation_id,
                )
            ).one_or_none()
            return task_view(*pair) if pair else None

    def tasks(self, limit, before):
        with self.sessions() as session:
            query = (
                select(ResearchTask)
                .where(ResearchTask.deleted_at.is_(None))
                .order_by(
                    ResearchTask.created_at.desc(),
                    ResearchTask.id.desc(),
                )
                .limit(limit + 1)
            )
            if before is not None:
                anchor = session.get(ResearchTask, before)
                if anchor is None:
                    raise KeyError("没有找到该研发任务的列表定位。")
                query = query.where(
                    tuple_(ResearchTask.created_at, ResearchTask.id)
                    < (anchor.created_at, anchor.id)
                )
            rows = list(session.scalars(query))
            items = [
                {
                    "id": task.id,
                    "title": task.title,
                    "home_conversation_id": task.home_conversation_id,
                    "created_at": task.created_at.isoformat(),
                    "control_version": task.control_version,
                    "status": task.status,
                }
                for task in rows[:limit]
            ]
            return {"items": items, "next_cursor": items[-1]["id"] if len(rows) > limit else None}

    def revision(self, task_id, branch_id, revision_id):
        with self.sessions() as session:
            row = session.scalar(
                select(ResearchRevision)
                .join(
                    ResearchBranch,
                    ResearchBranch.id == ResearchRevision.branch_id,
                )
                .where(
                    ResearchBranch.task_id == task_id,
                    ResearchBranch.id == branch_id,
                    ResearchRevision.id == revision_id,
                )
            )
            if row is None:
                raise KeyError("没有找到该研究分支的固定修订。")
            task = session.get(ResearchTask, task_id)
            if task.deleted_at is not None:
                # 旧聊天和正文可能在新增识别规则之前已引用该版本，回填完成前保守保留。
                if ReferenceCatalog.ready(session):
                    reachable = reachable_assets(session)
                    linked = session.scalar(select(reachable.c.node).where(
                        reachable.c.node == f"research_revision:{revision_id}",
                    ))
                    if not linked:
                        raise KeyError("该固定修订已随研发任务移除。")
                return {**revision_view(row), "task_removed": True}
            return revision_view(row)

    def revisions(self, task_id, branch_id, limit, before):
        with self.sessions() as session:
            branch = session.get(ResearchBranch, branch_id)
            task = session.get(ResearchTask, task_id)
            if branch is None or branch.task_id != task_id or task is None or task.deleted_at is not None:
                raise KeyError("没有找到该任务的研究分支。")
            query = select(ResearchRevision).where(ResearchRevision.branch_id == branch_id)
            if before is not None:
                query = query.where(ResearchRevision.number < before)
            rows = list(
                session.scalars(query.order_by(ResearchRevision.number.desc()).limit(limit + 1))
            )
            items = [
                {
                    "id": row.id,
                    "number": row.number,
                    "reason": row.reason,
                    "created_at": row.created_at.isoformat(),
                }
                for row in rows[:limit]
            ]
            return {
                "items": items,
                "next_before": items[-1]["number"] if len(rows) > limit else None,
            }

    def update_brief(self, task_id: str, branch_id: str, body: ReviseResearch):
        key = "research:update:" + fingerprint([task_id, branch_id, body.operation_id])
        request_hash = fingerprint(body.model_dump(exclude={"operation_id"}, exclude_unset=True))
        with self.sessions.begin() as session:
            lock_control(session)
            task = session.get(ResearchTask, task_id, with_for_update=True)
            branch = (
                session.scalar(
                    select(ResearchBranch)
                    .where(
                        ResearchBranch.id == branch_id,
                        ResearchBranch.task_id == task_id,
                    )
                    .with_for_update()
                )
                if task is not None and task.deleted_at is None
                else None
            )
            if branch is None:
                raise KeyError("没有找到该任务的研究分支。")
            prior = _read_receipt(session, key, request_hash)
            if prior is not None:
                return prior
            if task.control_version != body.expected_control_version:
                raise ResearchConflict(
                    "control_conflict",
                    "任务状态已更新。请核对最新版后再保存。",
                    task=task,
                    branch=branch,
                )
            if branch.head_revision_id != body.expected_revision_id:
                raise ResearchConflict(
                    "revision_conflict",
                    "简报已在另一个页面更新。请核对最新版后再保存。",
                    task=task,
                    branch=branch,
                )
            if task.status != "open":
                raise ResearchConflict("task_unavailable", "当前任务状态不允许编辑简报。")

            head = session.get(ResearchRevision, branch.head_revision_id)
            brief = {**head.state["brief"], **body.changes.brief.model_dump(exclude_unset=True)}
            if brief != head.state["brief"]:
                from knowledgeops_agent.research.autonomy import SCOPE_KEYS, invalidate

                if any(brief[key] != head.state["brief"].get(key, "") for key in SCOPE_KEYS):
                    invalidate(session, task.id, "研究范围已更新，请核对新版后重新授权。")
                head = _append(
                    session,
                    branch,
                    {**head.state, "brief": brief},
                    parent=head,
                    reason=body.reason,
                    origin="brief_edit",
                )
            return _save_receipt(session, key, request_hash, task_view(task, head))
