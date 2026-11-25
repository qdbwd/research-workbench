"""确定性的整理预览与人工批准，调用方只使用本地证据。"""

import json
from datetime import datetime, timedelta
from itertools import combinations
from pathlib import PurePosixPath

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import or_, select

from knowledgeops_agent.evolution.review import _safe, _scan
from knowledgeops_agent.evolution.security import redact_credentials
from knowledgeops_agent.evolution.service import change_view
from knowledgeops_agent.evolution.settings import DEFAULTS, EvolutionConflict, settings_values
from knowledgeops_agent.evolution.skill_review import skill_manager
from knowledgeops_agent.evolution.telemetry import telemetry_map
from knowledgeops_agent.harness.models.validation import model_output_error
from knowledgeops_agent.harness.skills.packages import SkillPackageError, package_catalog
from knowledgeops_agent.harness.tools.configuration import lock_configuration
from knowledgeops_agent.harness.tools.policy import read_policy
from knowledgeops_agent.memory.service import MemoryService, _normalized
from knowledgeops_agent.persistence.models import (
    EvolutionChange,
    EvolutionJob,
    EvolutionSettings,
    LearningTask,
    Preference,
    ResearchTask,
    Run,
    RunResourceSnapshot,
    SkillPackageState,
)
from knowledgeops_agent.vendor.deerflow.memory_relevance import tokenize


def _live_resources(session):
    return session.scalars(
        select(RunResourceSnapshot.content)
        .join(
            Run,
            Run.id == RunResourceSnapshot.root_id,
        )
        .outerjoin(LearningTask, LearningTask.id == Run.learning_task_id)
        .outerjoin(
            ResearchTask,
            ResearchTask.id == Run.input_data["research"]["task_id"].astext,
        )
        .where(
            or_(
                LearningTask.status.in_(["draft", "active", "waiting", "paused"]),
                ResearchTask.status.in_(["open", "awaiting_acceptance", "paused"]),
            )
        )
    )


def active_skill_references(session):
    names = set()
    for manifest in _live_resources(session):
        for skill in manifest.get("skills", []):
            parts = PurePosixPath(skill.get("resource", "")).parts
            if len(parts) >= 4 and parts[:2] == ("/", "skills"):
                names.add(parts[2])
    return names


def _active_memory_references(session):
    identifiers = set()
    for manifest in _live_resources(session):
        profiles = (manifest.get("learning") or {}).get("profiles") or {}
        identifiers.update(profiles.get("memory_ids", []))
        identifiers.update(row["id"] for row in manifest.get("preferences", []) if row.get("id"))
    return identifiers


def _similar(a, b):
    combined = a | b
    return len(a & b) / len(combined) if combined else 0.0


def _memory_candidates(session, gateway, now):
    from knowledgeops_agent.memory.service import preference_view

    protected = _active_memory_references(session)
    rows = [
        row
        for row in session.scalars(
            select(Preference)
            .where(
                Preference.status == "active",
                Preference.protection_level == "normal",
            )
            .order_by(Preference.id)
        )
        if row.id not in protected
    ]
    groups = {}
    result = []
    for row in rows:
        groups.setdefault((row.memory_slot, row.scope, row.topic, row.category), []).append(row)
        if (
            row.category in {"background", "goal"}
            and row.review_after
            and row.review_after <= now
            and (row.confirmed_at is None or row.confirmed_at <= row.review_after)
        ):
            result.append(
                {
                    "target_kind": "memory",
                    "target_id": row.id,
                    "action": "deprecate",
                    "base_revision": row.revision,
                    "reason_code": "memory_stale",
                    "label": _safe(gateway, row.content[:120]),
                    "diff_summary": {
                        "before": _safe(gateway, preference_view(row)),
                        "after": {"status": "inactive"},
                    },
                    "payload": {"curator": True},
                }
            )
    for group in groups.values():
        tokens = {row.id: set(tokenize(_normalized(row.content))) for row in group}
        for left, right in combinations(group, 2):
            similarity = (
                1.0
                if _normalized(left.content) == _normalized(right.content)
                else _similar(tokens[left.id], tokens[right.id])
            )
            if similarity < 0.72:
                continue
            suggested = max((left.content, right.content), key=len)
            result.append(
                {
                    "target_kind": "memory",
                    "target_id": f"{left.id}/{right.id}",
                    "action": "merge",
                    "base_revision": None,
                    "reason_code": "memory_similarity",
                    "label": _safe(gateway, suggested[:120]),
                    "similarity": round(similarity, 3),
                    "diff_summary": {
                        "before": {
                            "content": _safe(gateway, left.content + "\n\n" + right.content)
                        },
                        "after": {
                            "content": _safe(gateway, suggested),
                            "memory_slot": left.memory_slot,
                        },
                    },
                    "payload": {
                        "content": suggested,
                        "revisions": {left.id: left.revision, right.id: right.revision},
                    },
                }
            )
    return result


def _skill_candidates(session, manager, gateway, now, stale_days):
    active = active_skill_references(session)
    policy = read_policy(session).get("skill_enabled", {})
    telemetry = telemetry_map(session)
    catalog = {item["name"]: item for item in package_catalog(manager.root)}
    states = list(
        session.scalars(
            select(SkillPackageState)
            .where(
                SkillPackageState.removed.is_(False),
                SkillPackageState.archived_at.is_(None),
            )
            .order_by(SkillPackageState.name)
        )
    )
    tokens = {}
    for state in states:
        try:
            package = manager.storage.read(manager.storage.package(state.name))
        except (SkillPackageError, OSError):
            continue
        if package.digest != state.content_hash or package.missing or not package.compatible:
            continue
        headings = [
            line.lstrip("#").strip()
            for line in package.files["SKILL.md"].decode().splitlines()
            if line.startswith("#")
        ]
        tokens[state.name] = set(tokenize(_normalized(" ".join([package.description, *headings]))))
    overlaps = {}
    result = []
    for first, second in combinations(sorted(tokens), 2):
        similarity = _similar(tokens[first], tokens[second])
        if similarity >= 0.65:
            overlaps.setdefault(first, set()).add(second)
            overlaps.setdefault(second, set()).add(first)
            result.append(
                {
                    "target_kind": "skill",
                    "target_id": f"{first}/{second}",
                    "action": "overlap",
                    "label": f"{first} / {second}",
                    "reason_code": "skill_overlap",
                    "similarity": round(similarity, 3),
                    "readonly": True,
                }
            )
    cutoff = now - timedelta(days=stale_days)
    for state in states:
        name = state.name
        if (
            name not in tokens
            or not catalog.get(name, {}).get("enabled")
            or not policy.get(name, True)
            or state.protection_level != "normal"
        ):
            continue
        usage = telemetry.get(name, {})
        loads, failures, corrections, successes = (
            usage.get(key, 0)
            for key in ("load_count", "failed_run_count", "correction_count", "success_run_count")
        )
        last = usage.get("last_used_at")
        covered = any(
            (used := telemetry.get(other, {})).get("load_count", 0) > loads
            and used.get("last_used_at") is not None
            and used["last_used_at"] > cutoff
            for other in overlaps.get(name, ())
        )
        stale = (last or state.created_at) <= cutoff and (
            not successes or failures or corrections or covered
        )
        high_failure = failures + corrections >= 3 and failures + corrections >= loads * 0.5
        if not (stale or high_failure):
            continue
        if name in active and not high_failure:
            continue
        result.append(
            {
                "target_kind": "skill",
                "target_id": name,
                "action": "inspect" if name in active else "archive",
                "base_revision": state.revision,
                "label": name,
                "reason_code": "skill_high_failure" if high_failure else "skill_stale",
                "readonly": name in active,
                "telemetry": {
                    "load_count": loads,
                    "success_run_count": successes,
                    "failed_run_count": failures,
                    "correction_count": corrections,
                    "last_used_at": last.isoformat() if last else None,
                },
                "diff_summary": {"before": {"status": "active"}, "after": {"status": "archived"}},
                "payload": {},
            }
        )
    return result


def scan_candidates(session, jobs, gateway):
    manager = skill_manager(jobs.sessions)
    settings = settings_values(session)
    now = jobs.now()
    return [
        *_memory_candidates(session, gateway, now),
        *_skill_candidates(
            session,
            manager,
            gateway,
            now,
            settings["curator_stale_days"],
        ),
    ]


def preview(services):
    with services.jobs.sessions.begin() as session:
        lock_configuration(session, shared=True)
        rows = scan_candidates(session, services.jobs, services.gateway)
        settings = settings_values(session)
        last = settings["curator_last_run_at"]
        due = last is None or (
            datetime.fromisoformat(last)
            <= services.jobs.now() - timedelta(days=settings["curator_stale_days"])
        )
    return {
        "items": [{key: val for key, val in row.items() if key != "payload"} for row in rows],
        "count": len(rows),
        "curator_last_run_at": last,
        "due": due,
    }


class MergeDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)
    target_id: str
    content: str = Field(min_length=1, max_length=2000)


class MergeOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)
    drafts: list[MergeDraft] = Field(max_length=6)


def prepare_curator(context, run):
    drafts = {}
    if run["input"].get("use_model_merge"):
        with context.jobs.sessions() as session:
            candidates = [
                row
                for row in _memory_candidates(session, context.gateway, context.jobs.now())
                if row["action"] == "merge"
            ][:6]
        if candidates:
            packet = [{"target_id": row["target_id"], **row["diff_summary"]} for row in candidates]
            context.jobs.guard(context.run_id, worker_id=context.worker_id)
            response = context.gateway.invoke(
                [
                    SystemMessage(
                        '只为给定的重复记忆生成保留原意的合并草稿。输出 {"drafts":[{"target_id": "...", "content":"..."}]}。'
                    ),
                    HumanMessage(json.dumps(packet, ensure_ascii=False)),
                ],
                root_id=context.root_id,
                run_id=context.run_id,
                purpose="evolution_curator_merge",
                root_limit=run["budgets"]["model_requests"],
                call_id=f"curator-merge-v1:{context.run_id}",
                max_tokens=3500,
                response_format={"type": "json_object"},
                audit_redactor=redact_credentials,
            )
            if model_output_error(response) or response.tool_calls or response.invalid_tool_calls:
                raise ValueError("整理模型返回了无效结果")
            by_target = {row["target_id"]: row["payload"]["revisions"] for row in candidates}
            drafts = {
                row.target_id: (by_target[row.target_id], row)
                for row in MergeOutput.model_validate_json(response.text).drafts
                if row.target_id in by_target
            }

    def finish(session):
        context.jobs.execution_guard(session, context.run_id, context.worker_id)
        job = session.scalar(
            select(EvolutionJob).where(EvolutionJob.run_id == context.run_id).with_for_update()
        )
        if job is None or job.status != "running":
            raise EvolutionConflict("整理任务已结束")
        lock_configuration(session)
        skill_manager(context.jobs.sessions)._recover_in(session)
        rows = scan_candidates(session, context.jobs, context.gateway)
        summary = {"pending_review": 0, "skipped_duplicate": 0, "readonly": 0, "rejected": 0}
        now = context.jobs.now()
        for index, row in enumerate(rows):
            if row.get("readonly"):
                summary["readonly"] += 1
                continue
            keys = {key: row[key] for key in ("target_kind", "target_id", "action")}
            if session.scalar(
                select(EvolutionChange.id).filter_by(**keys, review_state="pending_review").limit(1)
            ):
                summary["skipped_duplicate"] += 1
                continue
            payload = row["payload"]
            diff = {**row["diff_summary"], "reason_code": row["reason_code"]}
            state, scan = "pending_review", {}
            if row["action"] == "merge":
                draft = drafts.get(row["target_id"])
                if draft and draft[0] == payload["revisions"]:
                    payload = {**payload, "content": draft[1].content}
                scan = _scan(
                    context.gateway,
                    MergeDraft(target_id=row["target_id"], content=payload["content"]),
                )
                if not scan["allowed"]:
                    state, payload = "rejected", {}
                    diff = {"reason_code": "security_scan"}
                else:
                    diff = {**diff, "after": {**diff["after"], "content": payload["content"]}}
            evidence = {key: row[key] for key in ("telemetry", "similarity") if key in row}
            session.add(
                EvolutionChange(
                    **keys,
                    job_id=job.id,
                    base_revision=row["base_revision"],
                    review_state=state,
                    proposal_payload=payload,
                    diff_summary=diff,
                    evidence_refs=evidence,
                    security_scan_result=scan,
                    idempotency_key=f"curator:{job.id}:{index}",
                    resolved_at=now if state == "rejected" else None,
                )
            )
            summary[state] += 1
        settings = session.get(EvolutionSettings, "default", with_for_update=True)
        if settings is None:
            settings = EvolutionSettings(id="default", **DEFAULTS)
            session.add(settings)
        settings.curator_last_run_at = now
        job.result_summary = summary
        return {"kind": "evolution_review", "job_id": job.id, "result_summary": summary}

    return finish


def resolve_memory(session, services, change, decision, resolved):
    memory = MemoryService(services.jobs.sessions)
    if decision == "approve":
        payload = change.proposal_payload
        active = _active_memory_references(session)
        identities = list(payload["revisions"]) if change.action == "merge" else [change.target_id]
        rows = [session.get(Preference, identity, with_for_update=True) for identity in identities]
        if any(
            row is None
            or row.status != "active"
            or row.protection_level != "normal"
            or row.id in active
            for row in rows
        ):
            raise EvolutionConflict("记忆状态、保护或任务引用已变化，请重新整理")
        if change.action == "merge":
            scan = _scan(
                services.gateway, MergeDraft(target_id=change.target_id, content=payload["content"])
            )
            if not scan["allowed"]:
                raise EvolutionConflict("合并稿未通过安全审查")
            result = memory.merge(**payload, session=session)
        else:
            result = memory.update(
                change.target_id,
                expected_revision=change.base_revision,
                status="inactive",
                session=session,
            )
        change.target_id, change.applied_revision = result["id"], result["revision"]
    elif decision == "rollback":
        result = memory.undo_merge(
            change.target_id, expected_revision=change.applied_revision, session=session
        )
        change.diff_summary = {**change.diff_summary, "rollback_revision": result["revision"]}
    change.review_state, change.resolved_at = resolved, services.jobs.now()
    change.proposal_payload = {}
    return change_view(change)
