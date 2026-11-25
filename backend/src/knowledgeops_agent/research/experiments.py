"""实验审批、交换目录投递与结果收录。应用只处理数据和回执。"""

import hashlib
import json
import os
from datetime import timedelta
from tempfile import NamedTemporaryFile

from sqlalchemy import case, or_, select

from knowledgeops_agent.app.conversations import fingerprint
from knowledgeops_agent.app.runs.control import lock_control
from knowledgeops_agent.core.resources import bundled_resource
from knowledgeops_agent.persistence.models import (
    ResearchBranch,
    ResearchDelivery,
    ResearchExperiment,
    ResearchRevision,
    ResearchTask,
    Run,
    new_id,
)
from knowledgeops_agent.research import evidence
from knowledgeops_agent.research.deliveries import for_task
from knowledgeops_agent.research.service import (
    ResearchConflict,
    _append,
    _read_receipt,
    _save_receipt,
    require_writable_branch,
)

IN_FLIGHT = ("dispatched", "running", "cancel_requested", "unknown")
PENDING = ("awaiting_approval", "approved")
MAX_INPUT = 128 * 1024
MAX_RECEIPT = 68 * 1024


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()


def _digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def bundled_script(specification):
    """只从受信项目资源读取该类型脚本；旧实验仅在摘要吻合时可补齐。"""
    if specification.get("kind") != "reranker-check-v1":
        return None
    script = bundled_resource("scripts/research-experiments/reranker_check.py")
    if not script.is_file():
        return None
    data = script.read_bytes()
    return data.decode("utf-8") if hashlib.sha256(data).hexdigest() == specification["script_sha256"] else None


def _exchange(session):
    return session.info.get("research_experiment_exchange")


def _revision_refs(session, revision_id):
    revision = session.get(ResearchRevision, revision_id)
    return {ref.get("evidence_id") or ref.get("artifact_id") for ref in revision.state.get("provenance_refs", [])}


def _visible(session, branch, attempt_id):
    row = session.get(ResearchExperiment, attempt_id)
    if row is None or row.branch_id != branch.id:
        raise KeyError("没有找到当前研发的实验尝试。")
    return row


_EXPIRED_APPROVAL = "研究版本已变化，原提案已关闭。可按当前研究版本重新准备。"


def view(row, *, include_dataset=False, approval_expired=False):
    spec = row.specification
    return {
        "branch_id": row.branch_id,
        "id": row.id, "status": "cancelled" if approval_expired else row.status,
        "reason": _EXPIRED_APPROVAL if approval_expired else row.reason, "evidence_id": row.evidence_id,
        "source_revision_id": row.source_revision_id, "reproduced_from": row.reproduced_from,
        "question": spec["question"], "hypothesis": spec["hypothesis"], "evidence_ids": spec["evidence_ids"],
        "image": spec["image"], "script_sha256": spec["script_sha256"], "input_sha256": spec["input_sha256"],
        "timeout_seconds": spec["timeout_seconds"], "case_count": len(spec["input"]["cases"]),
        "result": row.receipt.get("output") if row.status in {"completed", "stale"} and row.receipt else None,
        "created_at": row.created_at.isoformat(),
        **({"dataset": spec["input"]} if include_dataset else {}),
    }


def list_attempts(sessions, task_id):
    with sessions() as session:
        task, branch = for_task(session, task_id)
        rows = session.scalars(select(ResearchExperiment).where(
            ResearchExperiment.branch_id == branch.id,
        ).order_by(ResearchExperiment.created_at.desc(), ResearchExperiment.id.desc()).limit(30))
        return {
            "enabled": _exchange(session) is not None,
            "items": [view(row, approval_expired=_approval_expired(task, branch, row)) for row in rows],
        }


def get_attempt(sessions, task_id, attempt_id):
    with sessions() as session:
        row = session.get(ResearchExperiment, attempt_id)
        if row is None:
            raise KeyError("没有找到该研发实验。")
        task, branch = for_task(session, task_id, row.branch_id)
        return view(row, include_dataset=True, approval_expired=_approval_expired(task, branch, row))


def propose(jobs, run_id, worker_id, call_id, question, hypothesis, dataset, evidence_ids):
    key = "research:experiment:propose:" + fingerprint([run_id, call_id])
    inputs = dataset.model_dump()
    digest = fingerprint([question, hypothesis, inputs, evidence_ids])
    if len(canonical(inputs)) > MAX_INPUT:
        raise ValueError("实验需在 128 KiB 范围内使用代表性固定样本。")
    with jobs.sessions.begin() as session:
        lock_control(session)
        prior = _read_receipt(session, key, digest)
        if prior is not None:
            return prior
        root, current = jobs.execution_guard(session, run_id, worker_id)
        binding = root.input_data.get("research")
        if root.id != current.id or not binding or binding.get("review_only"):
            raise PermissionError("只有当前研发主运行可以提出实验。")
        if not _exchange(session):
            raise ValueError("当前部署没有配置独立实验执行器。")
        from knowledgeops_agent.research.execution import active_binding

        branch = active_binding(session, binding)
        pinned = _revision_refs(session, branch.head_revision_id)
        for identifier in evidence_ids:
            evidence.scope_checked(session, identifier, branch, pinned)
        manifest = json.loads(bundled_resource("config/research-experiment.json").read_bytes())
        script = bundled_script(manifest)
        if script is None:
            raise ValueError("当前部署的受信实验脚本与固定摘要不一致。")
        row = ResearchExperiment(
            id=new_id(), branch_id=branch.id, source_revision_id=branch.head_revision_id,
            source_control_version=binding["control_version"], propose_run_id=root.id,
            specification={
                **manifest, "script": script, "question": question, "hypothesis": hypothesis,
                "input": inputs, "input_sha256": _digest(inputs), "evidence_ids": list(dict.fromkeys(evidence_ids)),
            },
        )
        session.add(row)
        session.flush()
        jobs._event(session, root, "research_experiment_proposed", {"experiment_id": row.id})
        return _save_receipt(session, key, digest, view(row))


def _same_scope(task, branch, row):
    return (
        task.deleted_at is None and task.status == "open" and task.home_conversation_id is not None
        and task.control_version == row.source_control_version and branch.head_revision_id == row.source_revision_id
        and not branch.pending_delivery_id and branch.archived_at is None
    )


def _approval_expired(task, branch, row):
    return row.status == "awaiting_approval" and not _same_scope(task, branch, row)


def _cancel(row, message):
    if row.status in PENDING:
        row.status, row.reason = "cancelled", message
    elif row.status in ("dispatched", "running"):
        row.status, row.reason = "cancel_requested", message


def cancel_branch(session, branch_id, message):
    for row in session.scalars(select(ResearchExperiment).where(
        ResearchExperiment.branch_id == branch_id, ResearchExperiment.status.in_((*PENDING, *IN_FLIGHT)),
    )):
        _cancel(row, message)


def blockers(session, branch_id):
    return [{"kind": "experiment", "id": row.id, "status": row.status}
            for row in session.scalars(select(ResearchExperiment).where(
                ResearchExperiment.branch_id == branch_id, ResearchExperiment.status.in_(IN_FLIGHT),
            ))]


def awaits_experiment(session, branch_id):
    branch = session.get(ResearchBranch, branch_id)
    return session.scalar(select(ResearchExperiment.id).where(
        ResearchExperiment.branch_id == branch_id,
        or_(ResearchExperiment.status.in_(IN_FLIGHT),
            ResearchExperiment.status.in_(PENDING) & (ResearchExperiment.source_revision_id == branch.head_revision_id)),
    ).limit(1)) is not None


def act(sessions, task_id, attempt_id, body, *, jobs):
    key = "research:experiment:user:" + fingerprint([task_id, attempt_id, body.operation_id])
    digest = fingerprint(body.model_dump(exclude={"operation_id"}))
    with sessions.begin() as session:
        lock_control(session)
        prior = _read_receipt(session, key, digest)
        if prior is not None:
            return prior
        task, branch = for_task(session, task_id)
        row = _visible(session, branch, attempt_id)
        require_writable_branch(branch)
        if (task.control_version, branch.head_revision_id) != (body.expected_control_version, body.expected_revision_id):
            raise ResearchConflict("experiment_conflict", "任务或研究版本已变化，请重新核对。", task=task, branch=branch)
        if body.action == "approve":
            if row.status != "awaiting_approval" or not _same_scope(task, branch, row):
                raise ResearchConflict("experiment_conflict", "此实验的来源版本或审批状态已变化。")
            if not _exchange(session):
                raise ResearchConflict("experiment_unavailable", "当前部署没有配置独立实验执行器。")
            if session.scalar(select(ResearchExperiment.id).where(
                ResearchExperiment.branch_id == branch.id, ResearchExperiment.status.in_(("approved", *IN_FLIGHT)),
            ).limit(1)):
                raise ResearchConflict("experiment_busy", "请先确认同一主线的在途实验已结束。")
            for identifier in row.specification["evidence_ids"]:
                evidence.scope_checked(session, identifier, branch, _revision_refs(session, row.source_revision_id))
            row.status, row.reason = "approved", None
        elif body.action == "cancel":
            if row.status not in (*PENDING, *IN_FLIGHT):
                raise ResearchConflict("experiment_conflict", "这次实验已结束。")
            _cancel(row, "已撤回本次实验授权。")
        elif body.action == "acknowledge_stop":
            if row.status != "unknown" or not body.reason:
                raise ResearchConflict("experiment_conflict", "请先核对执行器与容器确实已停止，再记录核对依据。")
            row.status, row.reason = "cancelled", f"用户确认操作已停止，原结果仍未知。{body.reason}"
            if branch.pending_delivery_id:
                from knowledgeops_agent.research.deliveries import advance

                advance(session, jobs, session.get(ResearchDelivery, branch.pending_delivery_id))
        else:
            if _approval_expired(task, branch, row):
                _cancel(row, _EXPIRED_APPROVAL)
            if task.status != "open" or branch.pending_delivery_id or row.status not in (
                "completed", "failed", "timed_out", "cancelled", "stale",
            ):
                raise ResearchConflict("experiment_conflict", "已有实验须结束且研发开放后才可复现。")
            row = ResearchExperiment(
                id=new_id(), branch_id=branch.id, source_revision_id=branch.head_revision_id,
                source_control_version=task.control_version, propose_run_id=row.propose_run_id,
                specification=row.specification, reproduced_from=row.id,
            )
            session.add(row)
        session.flush()
        return _save_receipt(session, key, digest, view(row))


def _path(exchange, group, attempt):
    folder = exchange / group
    if folder.is_symlink():
        raise ValueError("实验交换目录不能是符号链接。")
    return folder / f"{attempt}.json"


def _publish(path, packet):
    """事务重试只重写同一身份，原子发布的报文不能换内容。"""
    raw = canonical(packet)
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=path.parent) as temporary:
        temporary.write(raw)
        temporary.flush()
        os.fsync(temporary.fileno())
        try:
            os.link(temporary.name, path)
        except FileExistsError:
            if path.is_symlink() or path.stat().st_size != len(raw) or path.read_bytes() != raw:
                raise ValueError("实验交换文件与已批准的身份不一致。") from None
    descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _read(path, limit):
    if path.is_symlink():
        raise ValueError("实验回执不能是符号链接。")
    if not path.is_file():
        return None
    with path.open("rb") as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise ValueError("实验回执超过容量。")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("实验回执格式无效。")
    return value


def _request(row):
    fields = ("kind", "image", "script_sha256", "input_sha256", "input", "timeout_seconds")
    return {"attempt_id": row.id, **{key: row.specification[key] for key in fields}}


def _verified_result(row, receipt):
    identity = _request(row)
    fields = {key: identity[key] for key in ("attempt_id", "image", "input_sha256", "script_sha256")}
    fields["request_sha256"] = _digest(identity)
    if any(receipt.get(key) != value for key, value in fields.items()):
        raise ValueError("执行器回执的身份或快照摘要不匹配。")
    status = receipt.get("status")
    if status not in {"completed", "cancelled", "failed", "timed_out", "unknown"}:
        raise ValueError("执行器返回了未知状态。")
    if status == "completed":
        from knowledgeops_agent.research.experiment_results import check_output

        check_output(identity["input"], receipt.get("output"), receipt.get("exit_code"))
    return status


def _collect(session, jobs, task, branch, row, receipt):
    status = _verified_result(row, receipt)
    can_collect = status == "completed" and row.status in {"dispatched", "running"} and _same_scope(task, branch, row)
    row.receipt = receipt
    row.status = status if status != "completed" or can_collect else "stale"
    row.reason = ("结果已迟到或授权已失效，保留回执供核对。" if row.status == "stale" else receipt.get("reason"))
    if not can_collect:
        return
    owner = session.get(Run, row.propose_run_id)
    if owner is None or owner.lifecycle_state != "active":
        row.status, row.reason = "stale", "提出实验的原运行已过保留期。"
        return
    output = receipt["output"]
    text = output["conclusion"] + "\n\n" + output["limitation"] + "\n\n" + json.dumps(output, ensure_ascii=False, indent=2)
    material = {
        "kind": "experiment", "title": row.specification["question"], "text": text,
        "start": 0, "end": len(text), "offset_unit": "characters",
        "experiment_id": row.id, "image": receipt["image"], "input_sha256": receipt["input_sha256"],
        "script_sha256": receipt["script_sha256"],
    }
    row.evidence_id = jobs.save_artifact(owner.id, "research_excerpt", canonical(material).decode(), session=session)
    head = session.get(ResearchRevision, branch.head_revision_id)
    _append(session, branch, {
        **head.state,
        "provenance_refs": [*head.state.get("provenance_refs", []), {
            "kind": "experiment_result", "artifact_id": row.evidence_id,
        }],
    }, parent=head, reason="收录经核验的独立实验结果", origin="experiment_result", actor="system")
    jobs._event(session, owner, "research_experiment_collected", {"experiment_id": row.id, "evidence_id": row.evidence_id})


def _poll(session, jobs, exchange, row):
    branch = session.get(ResearchBranch, row.branch_id)
    task = session.get(ResearchTask, branch.task_id)
    if not _same_scope(task, branch, row):
        _cancel(row, "研究版本已变化，原授权已撤回。")
    request_path = _path(exchange, "requests", row.id)
    if row.status == "dispatched" and not request_path.exists():
        _publish(request_path, _request(row))
    if (row.status == "cancel_requested" and not request_path.exists()
            and not _path(exchange, "claims", row.id).exists()
            and not _path(exchange, "results", row.id).exists()):
        row.status, row.reason = "cancelled", "实验在投递前已取消。"
    if row.status in {"cancel_requested", "unknown"}:
        _publish(_path(exchange, "cancel", row.id), {"attempt_id": row.id})
    if row.status not in IN_FLIGHT:
        return
    result = _read(_path(exchange, "results", row.id), MAX_RECEIPT)
    if result is not None:
        if row.receipt == result:
            return
        _collect(session, jobs, task, branch, row, result)
    elif row.dispatched_at and jobs.now() >= row.dispatched_at + timedelta(seconds=row.specification["timeout_seconds"] + 30):
        row.status, row.reason = "unknown", "超过执行器回执期限。请核对隔离进程是否已停止。"
        _publish(_path(exchange, "cancel", row.id), {"attempt_id": row.id})
    elif row.status == "dispatched":
        claim = _read(_path(exchange, "claims", row.id), 4096)
        if claim is not None:
            if claim.get("request_sha256") != _digest(_request(row)):
                raise ValueError("执行器领取的不是当前固定请求。")
            row.status = "running"


def tick(jobs):
    """沿用应用 outbox 的维护循环。失败保留身份，由下一次 tick 而非模型重试。"""
    with jobs.sessions() as session:
        exchange = _exchange(session)
        if not exchange:
            return
        identities = list(session.scalars(select(ResearchExperiment.id).where(
            ResearchExperiment.status.in_(("approved", *IN_FLIGHT)),
        ).order_by(
            case((ResearchExperiment.status == "unknown", 1), else_=0),
            ResearchExperiment.dispatched_at.asc().nullsfirst(), ResearchExperiment.id,
        ).limit(50)))
    for identifier in identities:
        # 先持久化投递意图，再让独立执行器看见文件。崩溃后取消仍能识别在途身份。
        with jobs.sessions.begin() as session:
            lock_control(session)
            row = session.get(ResearchExperiment, identifier)
            if row.status == "approved":
                branch = session.get(ResearchBranch, row.branch_id)
                task = session.get(ResearchTask, branch.task_id)
                if _same_scope(task, branch, row):
                    row.status, row.dispatched_at = "dispatched", jobs.now()
                else:
                    _cancel(row, "研究版本已变化，原授权已撤回。")
        with jobs.sessions.begin() as session:
            lock_control(session)
            row = session.get(ResearchExperiment, identifier)
            if row.status not in IN_FLIGHT:
                continue
            try:
                with session.begin_nested():
                    _poll(session, jobs, exchange, row)
            except (KeyError, TypeError, ValueError, OSError):
                # 文件系统暂时不可用时保留批准的规格，已可能开始的实验则标记未知。
                row.status, row.reason = "unknown", "无法确认执行器回执，等待核对。"
            branch = session.get(ResearchBranch, row.branch_id)
            if branch.pending_delivery_id:
                from knowledgeops_agent.research.deliveries import advance

                advance(session, jobs, session.get(ResearchDelivery, branch.pending_delivery_id))
