"""用户建立正式提交，分支从固定提交继承。自动修订仍是工作进展。"""

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import aliased

from knowledgeops_agent.app.conversations import fingerprint
from knowledgeops_agent.app.lifecycle.asset_references import replace_asset_references
from knowledgeops_agent.app.runs.children import HUMAN_WAIT, OPEN
from knowledgeops_agent.app.runs.control import lock_control
from knowledgeops_agent.persistence.models import (
    KnowledgeDocument,
    KnowledgeVersion,
    ResearchBranch,
    ResearchCommit,
    ResearchDelivery,
    ResearchExperiment,
    ResearchRevision,
    ResearchTask,
    Run,
    new_id,
    utcnow,
)
from knowledgeops_agent.research import autonomy, maps
from knowledgeops_agent.research import experiments as experiment_service
from knowledgeops_agent.research.service import (
    ResearchConflict,
    _append,
    _conversation,
    _read_receipt,
    _save_receipt,
    require_writable_branch,
    revision_view,
    task_view,
)


def _task(session, task_id):
    task = session.get(ResearchTask, task_id)
    if not task or task.deleted_at is not None:
        raise KeyError("没有找到该研发任务。")
    return task


def _branch(session, task, branch_id):
    branch = session.get(ResearchBranch, branch_id)
    if not branch or branch.task_id != task.id:
        raise KeyError("没有找到该任务的研发分支。")
    return branch


def _commit(session, task, identity, branch_id=None):
    row = session.get(ResearchCommit, identity)
    if not row or (branch_id and row.branch_id != branch_id):
        raise KeyError("没有找到该研发提交。")
    _branch(session, task, row.branch_id)
    return row


def _latest(session, branch):
    return session.scalar(select(ResearchCommit).where(ResearchCommit.branch_id == branch.id)
                          .order_by(ResearchCommit.number.desc()).limit(1))


def _branch_view(session, branch):
    latest = _latest(session, branch)
    return {
        "id": branch.id, "name": branch.name, "fork_commit_id": branch.fork_commit_id,
        "latest_commit_id": latest.id if latest else None,
        "archived_at": branch.archived_at.isoformat() if branch.archived_at else None,
        "head": revision_view(session.get(ResearchRevision, branch.head_revision_id)),
    }


def branches(sessions, task_id):
    with sessions() as session:
        task = _task(session, task_id)
        rows = session.scalars(select(ResearchBranch).where(ResearchBranch.task_id == task.id)
                               .order_by((ResearchBranch.id != task.main_branch_id), ResearchBranch.name, ResearchBranch.id))
        return {"main_branch_id": task.main_branch_id, "control_version": task.control_version,
                "items": [_branch_view(session, row) for row in rows]}


def _branch_runs(task_id, branch_id=None):
    root = aliased(Run)
    binding = root.input_data["research"]
    query = select(Run.id).join(root, Run.root_id == root.id).where(binding["task_id"].as_string() == task_id)
    if branch_id is not None:
        query = query.where(binding["branch_id"].as_string() == branch_id)
    return query


def _unsettled(session, task_id, branch_id=None):
    return session.scalar(_branch_runs(task_id, branch_id).where(
        or_(Run.status.in_(OPEN), and_(Run.status == "interrupted", Run.outcome["kind"].astext.in_(HUMAN_WAIT))),
    ).limit(1))


def archive(sessions, task_id, branch_id, body):
    key = "research:archive-branch:" + fingerprint([task_id, branch_id, body.operation_id])
    request_hash = fingerprint(body.expected_revision_id)
    with sessions.begin() as session:
        lock_control(session)
        prior = _read_receipt(session, key, request_hash)
        if prior is not None:
            return prior
        task = _task(session, task_id)
        branch = _branch(session, task, branch_id)
        if branch.head_revision_id != body.expected_revision_id:
            raise ResearchConflict("branch_conflict", "该分支进展已变化，请重新核对后归档。", task=task, branch=branch)
        if task.writing_branch_id == branch.id:
            raise ResearchConflict("branch_in_use", "该分支用于后续会话写入，请先切换写入分支。")
        if branch.pending_delivery_id or _unsettled(session, task_id, branch.id) or experiment_service.blockers(session, branch.id):
            raise ResearchConflict("branch_busy", "该分支仍有运行、实验或成果在收尾，请处理后再归档。")
        if branch.archived_at is None:
            branch.archived_at = utcnow()
            experiment_service.cancel_branch(session, branch.id, "研发分支已归档。")
            autonomy.invalidate(session, task.id, "研发分支已归档。", branch_id=branch.id)
        return _save_receipt(session, key, request_hash, _branch_view(session, branch))


def unarchive(sessions, task_id, branch_id, body):
    """解除分支归档：仅清空 archived_at，不复活旧授权/旧实验，也不自动切换写入分支。"""
    key = "research:unarchive-branch:" + fingerprint([task_id, branch_id, body.operation_id])
    request_hash = fingerprint(body.expected_revision_id)
    with sessions.begin() as session:
        lock_control(session)
        prior = _read_receipt(session, key, request_hash)
        if prior is not None:
            return prior
        task = _task(session, task_id)
        branch = _branch(session, task, branch_id)
        if branch.head_revision_id != body.expected_revision_id:
            raise ResearchConflict("branch_conflict", "该分支进展已变化，请重新核对后恢复。", task=task, branch=branch)
        if branch.archived_at is not None:
            branch.archived_at = None
        return _save_receipt(session, key, request_hash, _branch_view(session, branch))


def restart(sessions, task_id, branch_id, body):
    key = "research:restart-branch:" + fingerprint([task_id, branch_id, body.operation_id])
    request_hash = fingerprint(body.expected_control_version)
    with sessions.begin() as session:
        lock_control(session)
        prior = _read_receipt(session, key, request_hash)
        if prior is not None:
            return prior
        task = _task(session, task_id)
        branch = _branch(session, task, branch_id)
        require_writable_branch(branch)
        if task.control_version != body.expected_control_version:
            raise ResearchConflict("control_conflict", "任务状态已变化，请重新核对后开始。", task=task, branch=branch)
        if task.status not in {"closed", "completed"} or not task.home_conversation_id:
            raise ResearchConflict("task_unavailable", "请从保有原会话的已取消或已验收任务开始新一轮。")
        _conversation(session, task.home_conversation_id)
        head = session.get(ResearchRevision, branch.head_revision_id)
        if (not branch.fork_commit_id or branch.id == task.writing_branch_id or head.origin != "fork"
                or session.scalar(_branch_runs(task.id, branch.id).limit(1))):
            raise ResearchConflict("branch_not_new", "请从要继承的固定提交创建一个新分支。")
        if _unsettled(session, task.id):
            raise ResearchConflict("branch_busy", "上一轮运行仍在收尾，请等待停止后再开始。")
        # 重开将改变任务的工作范围，旧分支仍存的许可也不得跨控制版本续用。
        autonomy.invalidate(session, task.id, "研发已从固定提交开始新一轮。")
        task.status = "open"
        task.control_version += 1
        task.write_branch_id = branch.id
        task.write_version += 1
        return _save_receipt(session, key, request_hash, task_view(task, head, branch))


def _asset_snapshot(session, task, branch, revision):
    """把实验及交付的可变状态按确切时点复制，原授权仅保存在原分支。"""
    inherited = session.get(ResearchCommit, branch.fork_commit_id) if branch.fork_commit_id else None
    base = inherited.snapshot if inherited else {}
    experiments = list(base.get("experiments", []))
    for row in session.scalars(select(ResearchExperiment).where(ResearchExperiment.branch_id == branch.id)
                               .order_by(ResearchExperiment.created_at, ResearchExperiment.id)):
        spec = row.specification
        script = spec.get("script") or experiment_service.bundled_script(spec)
        experiments.append({
            "id": row.id, "branch_id": row.branch_id, "research_revision_id": row.source_revision_id,
            "specification": {**spec, "script": script}, "status": row.status, "receipt": row.receipt,
            "reason": row.reason, "evidence_id": row.evidence_id, "reproduced_from": row.reproduced_from,
            "evidence": [{"evidence_id": identity} for identity in row.specification.get("evidence_ids", [])],
            "created_at": row.created_at.isoformat(),
        })
    deliveries = list(base.get("deliveries", []))
    for row in session.scalars(select(ResearchDelivery).where(ResearchDelivery.branch_id == branch.id)
                               .order_by(ResearchDelivery.created_at, ResearchDelivery.id)):
        deliveries.append({
            "id": row.id, "branch_id": row.branch_id, "research_revision_id": row.source_revision_id,
            "content": row.content, "status": row.status, "review_reason": row.review_reason,
            "evidence": [{"evidence_id": identity} for identity in row.content.get("evidence_ids", [])],
            "created_at": row.created_at.isoformat(),
        })
    knowledge = base.get("knowledge_refs", [])
    if not inherited and task.home_conversation_id:
        knowledge = [{"knowledge_version_id": row.id, "title": row.title, "version": row.number}
                     for row in session.scalars(select(KnowledgeVersion)
                     .join(KnowledgeDocument, KnowledgeDocument.id == KnowledgeVersion.document_id)
                     .where(KnowledgeDocument.trial_conversation_id == task.home_conversation_id)
                     .order_by(KnowledgeVersion.title, KnowledgeVersion.id))]
    return {"material_refs": revision.state.get("material_refs", []), "knowledge_refs": knowledge,
            "experiments": experiments, "deliveries": deliveries}


def _snapshot_view(session, revision, assets):
    return {"revision": revision_view(revision), "map": maps._map_view(session, revision), **assets}


def _summary(row):
    return {"id": row.id, "branch_id": row.branch_id, "revision_id": row.revision_id,
            "parent_commit_id": row.parent_commit_id, "number": row.number, "message": row.message,
            "created_at": row.created_at.isoformat()}


def _view(session, row):
    revision = session.get(ResearchRevision, row.revision_id)
    return {**_summary(row), "snapshot_hash": row.snapshot_hash,
            "snapshot": _snapshot_view(session, revision, row.snapshot)}


def _preview(session, task, branch):
    revision = session.get(ResearchRevision, branch.head_revision_id)
    last = _latest(session, branch)
    parent = last.id if last else branch.fork_commit_id
    assets = _asset_snapshot(session, task, branch, revision)
    digest = fingerprint([branch.id, revision.content_hash, assets, parent])
    return last, parent, revision, assets, digest


def preview(sessions, task_id, branch_id):
    with sessions.begin() as session:
        lock_control(session)
        task = _task(session, task_id)
        branch = _branch(session, task, branch_id)
        require_writable_branch(branch)
        _, parent, revision, assets, digest = _preview(session, task, branch)
        return {"parent_commit_id": parent, "preview_hash": fingerprint([digest, task.control_version]),
                "snapshot": _snapshot_view(session, revision, assets)}


def save(sessions, task_id, branch_id, body):
    key = "research:commit:" + fingerprint([task_id, branch_id, body.operation_id])
    request_hash = fingerprint(body.model_dump(exclude={"operation_id"}))
    with sessions.begin() as session:
        lock_control(session)
        prior = _read_receipt(session, key, request_hash)
        if prior is not None:
            return prior
        task = _task(session, task_id)
        branch = _branch(session, task, branch_id)
        require_writable_branch(branch)
        last, parent, revision, assets, digest = _preview(session, task, branch)
        if body.preview_hash != fingerprint([digest, task.control_version]):
            raise ResearchConflict("commit_conflict", "研究进展或资产已变化，请重新预览。", task=task, branch=branch)
        row = ResearchCommit(id=new_id(), branch_id=branch.id, revision_id=revision.id,
                             number=last.number + 1 if last else 1, message=body.message,
                             parent_commit_id=parent, snapshot=assets, snapshot_hash=digest)
        session.add(row)
        session.flush()
        replace_asset_references(session, "research_commit", row.id, {
            **assets, "research_revision_id": revision.id,
        })
        return _save_receipt(session, key, request_hash, _view(session, row))


def history(sessions, task_id, branch_id):
    with sessions() as session:
        branch = _branch(session, _task(session, task_id), branch_id)
        rows = session.scalars(select(ResearchCommit).where(ResearchCommit.branch_id == branch.id)
                               .order_by(ResearchCommit.number.desc()))
        return {"items": [_summary(row) for row in rows]}


def get(sessions, task_id, branch_id, commit_id):
    with sessions() as session:
        return _view(session, _commit(session, _task(session, task_id), commit_id, branch_id))


def fork(sessions, task_id, body):
    key = "research:fork:" + fingerprint([task_id, body.operation_id])
    request_hash = fingerprint(body.model_dump(exclude={"operation_id"}))
    with sessions.begin() as session:
        lock_control(session)
        prior = _read_receipt(session, key, request_hash)
        if prior is not None:
            return prior
        task = _task(session, task_id)
        source = _commit(session, task, body.source_commit_id)
        if session.scalar(select(ResearchBranch.id).where(ResearchBranch.task_id == task.id,
                                                           ResearchBranch.name == body.name)):
            raise ResearchConflict("branch_name_exists", "该研发分支名称已存在。")
        branch = ResearchBranch(id=new_id(), task_id=task.id, head_revision_id=new_id(),
                                fork_commit_id=source.id, name=body.name)
        session.add(branch)
        session.flush()
        initial = session.get(ResearchRevision, source.revision_id)
        _append(session, branch, initial.state, parent=None, reason=f"从提交 {source.message} 继承", origin="fork")
        return _save_receipt(session, key, request_hash, _branch_view(session, branch))


def _compare(before, after):
    assets = []
    for key, identifier in (("material_refs", "source_version_id"), ("knowledge_refs", "knowledge_version_id"),
                            ("experiments", "id"), ("deliveries", "id")):
        left, right = ({item[identifier]: item for item in side[key]} for side in (before, after))
        for identity in sorted(left.keys() | right.keys()):
            a, b = left.get(identity), right.get(identity)
            if a != b:
                assets.append({"kind": key, "id": identity, "before": a, "after": b})
    return {"map": maps.diff_maps(before["map"], after["map"]), "assets": assets,
            "brief_changed": before["revision"]["state"]["brief"] != after["revision"]["state"]["brief"]}


def _parents(session, row):
    while row:
        yield row.id
        row = session.get(ResearchCommit, row.parent_commit_id) if row.parent_commit_id else None


def compare(sessions, task_id, before, after):
    with sessions() as session:
        task = _task(session, task_id)
        left, right = (_commit(session, task, identity) for identity in (before, after))
        parent_ids = set(_parents(session, left))
        common = next((identity for identity in _parents(session, right) if identity in parent_ids), None)
        return {"common_commit_id": common, **_compare(_view(session, left)["snapshot"], _view(session, right)["snapshot"])}


def working_diff(sessions, task_id, branch_id):
    with sessions.begin() as session:
        lock_control(session)
        task = _task(session, task_id)
        branch = _branch(session, task, branch_id)
        last, parent, revision, assets, _ = _preview(session, task, branch)
        baseline = last or (session.get(ResearchCommit, parent) if parent else None)
        if baseline is None:
            return {"base_commit_id": None, "difference": None}
        return {"base_commit_id": baseline.id, "difference": _compare(
            _view(session, baseline)["snapshot"], _snapshot_view(session, revision, assets),
        )}
