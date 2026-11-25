"""单行配置、UTC 入队预算与记忆容量。写入者统一持有运行控制事务锁。"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from knowledgeops_agent.app.runs.control import lock_control
from knowledgeops_agent.persistence.models import (
    EvolutionJob,
    EvolutionSettings,
    Preference,
    Run,
    utcnow,
)

DEFAULTS = {
    "auto_review_enabled": True,
    "memory_auto_write": True,
    "skill_auto_write": True,
    "user_slot_char_limit": 2400,
    "system_slot_char_limit": 3600,
    "review_turn_threshold": 6,
    "skill_tool_threshold": 8,
    "daily_budget_requests": 20,
    "curator_stale_days": 30,
}
BOUNDS = {
    "user_slot_char_limit": (200, 20000),
    "system_slot_char_limit": (200, 30000),
    "review_turn_threshold": (1, 100),
    "skill_tool_threshold": (1, 100),
    "daily_budget_requests": (0, 500),
    "curator_stale_days": (7, 365),
}
SLOTS = ("user", "system")


class EvolutionConflict(ValueError):
    """需要刷新对象或重新确认的冲突。"""


def settings_values(session) -> dict:
    row = session.get(EvolutionSettings, "default")
    if row is None:
        return {
            **DEFAULTS,
            "id": "default",
            "revision": 1,
            "updated_at": None,
            "curator_last_run_at": None,
        }
    return {
        **{key: getattr(row, key) for key in DEFAULTS},
        "id": row.id,
        "revision": row.revision,
        "updated_at": row.updated_at.isoformat(),
        "curator_last_run_at": row.curator_last_run_at.isoformat()
        if row.curator_last_run_at
        else None,
    }


def daily_used(session, now: datetime) -> int:
    start = now.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    return (
        session.scalar(
            select(func.count())
            .select_from(EvolutionJob)
            .join(Run, Run.id == EvolutionJob.run_id)
            .where(
                (EvolutionJob.job_type != "curator")
                | (Run.input_data["use_model_merge"].astext == "true"),
                EvolutionJob.created_at >= start,
                EvolutionJob.created_at < start + timedelta(days=1),
            )
        )
        or 0
    )


def slot_usage(session) -> dict:
    limits = settings_values(session)
    totals = {
        slot: (characters, count)
        for slot, characters, count in session.execute(
            select(Preference.memory_slot, func.sum(func.length(Preference.content)), func.count())
            .where(Preference.status == "active")
            .group_by(Preference.memory_slot)
        )
    }
    return {
        slot: {
            "character_count": totals.get(slot, (0, 0))[0],
            "entry_count": totals.get(slot, (0, 0))[1],
            "char_limit": limits[f"{slot}_slot_char_limit"],
        }
        for slot in SLOTS
    }


def exceeds_capacity(session, slot, content, *, replacing=()) -> bool:
    usage = slot_usage(session)[slot]
    removed = sum(
        len(row.content) for row in replacing if row.status == "active" and row.memory_slot == slot
    )
    # 已有数据或调低上限可处于超额状态，允许无增长的维护和精简。
    return len(content) > removed and (
        usage["character_count"] + len(content) - removed > usage["char_limit"]
    )


def require_capacity(session, slot, content, *, replacing=()) -> None:
    if exceeds_capacity(session, slot, content, replacing=replacing):
        raise ValueError("记忆槽位字符数超过上限，请先合并、精简或调高槽位上限")


def settings_view(session, now: datetime) -> dict:
    values = settings_values(session)
    used = daily_used(session, now)
    return {
        **values,
        "slots": slot_usage(session),
        "daily_used": used,
        "daily_remaining": max(0, values["daily_budget_requests"] - used),
    }


def update_settings(sessions, expected_revision: int, updates: dict, *, now=utcnow) -> dict:
    with sessions.begin() as session:
        lock_control(session)
        current = settings_values(session)
        if current["revision"] != expected_revision:
            raise EvolutionConflict("自进化设置已变化，请刷新后再保存")
        for key, value in updates.items():
            if key not in DEFAULTS or type(value) is not type(DEFAULTS[key]):
                raise ValueError("无效自进化设置")
            if key in BOUNDS and not BOUNDS[key][0] <= value <= BOUNDS[key][1]:
                raise ValueError("自进化设置超出允许范围")
        if updates:
            row = session.get(EvolutionSettings, "default", with_for_update=True)
            if row is None:
                row = EvolutionSettings(id="default", **DEFAULTS)
                session.add(row)
            for key, value in updates.items():
                setattr(row, key, value)
            row.revision = current["revision"] + 1
            row.updated_at = now()
            session.flush()
        return settings_view(session, now())
