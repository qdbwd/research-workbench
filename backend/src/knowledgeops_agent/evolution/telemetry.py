"""从已完成真人运行的实际读取结算长期遥测，调用方持运行控制锁。"""

from pathlib import PurePosixPath

from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert

from knowledgeops_agent.persistence.models import (
    Run,
    RunSkillUse,
    SkillPackageState,
    SkillUsageTelemetry,
)

_COUNTERS = ("load_count", "success_run_count", "failed_run_count", "correction_count")


def telemetry_map(session):
    """读投影，不从已过期的运行事件重建。"""
    return {
        row.skill_name: {
            **{key: getattr(row, key) for key in _COUNTERS},
            "last_used_at": row.last_used_at,
            "last_updated_at": row.last_updated_at,
        }
        for row in session.scalars(select(SkillUsageTelemetry))
    }


def mark_updated(session, skill_name, now):
    session.execute(
        insert(SkillUsageTelemetry)
        .values(
            skill_name=skill_name,
            last_updated_at=now,
            updated_at=now,
        )
        .on_conflict_do_update(
            index_elements=[SkillUsageTelemetry.skill_name],
            set_={"last_updated_at": now, "updated_at": now},
        )
    )


def used_managed_skills(session, run_id):
    paths = session.scalars(
        select(RunSkillUse.resource)
        .join(Run, Run.id == RunSkillUse.run_id)
        .where(or_(Run.id == run_id, Run.parent_id == run_id))
    )
    names = set()
    for path in paths:
        parts = PurePosixPath(path).parts
        if len(parts) >= 4 and parts[:2] == ("/", "skills") and ".." not in parts:
            names.add(parts[2])
    return set(
        session.scalars(select(SkillPackageState.name).where(SkillPackageState.name.in_(names)))
    )


def settle_usage(session, run, *, correction: bool, now) -> set[str]:
    used = used_managed_skills(session, run.id)
    corrected = set(used) if correction else set()
    if correction:
        previous = (
            session.scalar(
                select(Run.id)
                .where(
                    Run.conversation_id == run.conversation_id,
                    Run.parent_id.is_(None),
                    Run.origin == "human",
                    Run.kind == "agent",
                    Run.finished_at.is_not(None),
                    Run.conversation_sequence < run.conversation_sequence,
                )
                .order_by(Run.conversation_sequence.desc())
                .limit(1)
            )
            if run.conversation_sequence is not None
            else None
        )
        if previous:
            corrected.update(used_managed_skills(session, previous))
    for name in sorted(used | corrected):
        values = {
            "load_count": int(name in used),
            "success_run_count": int(name in used and run.status == "completed"),
            "failed_run_count": int(name in used and run.status in {"failed", "budget_exhausted"}),
            "correction_count": int(name in corrected),
        }
        query = insert(SkillUsageTelemetry).values(
            skill_name=name,
            **values,
            last_used_at=(run.finished_at or now) if name in used else None,
            updated_at=now,
        )
        session.execute(
            query.on_conflict_do_update(
                index_elements=[SkillUsageTelemetry.skill_name],
                set_={
                    **{
                        key: getattr(SkillUsageTelemetry, key) + value
                        for key, value in values.items()
                    },
                    "last_used_at": func.greatest(
                        SkillUsageTelemetry.last_used_at, query.excluded.last_used_at
                    ),
                    "updated_at": func.greatest(SkillUsageTelemetry.updated_at, now),
                },
            )
        )
    return used | corrected
