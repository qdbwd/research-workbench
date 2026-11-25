"""后台复盘入队、游标和运行生命周期。模型执行与变更审核由 review 模块负责。"""

import hashlib
import logging
import re

from sqlalchemy import func, select

from knowledgeops_agent.app.lifecycle.access import require_conversation
from knowledgeops_agent.app.runs.control import lock_control
from knowledgeops_agent.app.runs.service import Budgets
from knowledgeops_agent.evolution.security import redact_credentials
from knowledgeops_agent.evolution.settings import EvolutionConflict, daily_used, settings_values
from knowledgeops_agent.persistence.models import (
    ChatMessage,
    Conversation,
    EvolutionChange,
    EvolutionCursor,
    EvolutionJob,
    Run,
    RunDispatch,
    RunEvent,
    new_id,
)

logger = logging.getLogger(__name__)
ACTIVE_JOBS = {"pending", "running"}
COUNTERS = ("pending_human_turns", "pending_tool_calls", "pending_high_signal_events")
_HIGH_SIGNAL = re.compile(
    r"(?:^|[。！？!?\n])\s*(?:不对|搞错了|纠正一下)|以后记住|不要再|总是用|"
    r"\bactually\s*[,:]|\b(?:from now on|remember that)\b",
    re.IGNORECASE,
)


def message_position(message):
    return message.user_sequence, message.created_at, message.id


def _job(session, run_id):
    return session.scalar(
        select(EvolutionJob).where(EvolutionJob.run_id == run_id).with_for_update()
    )


def mark_running(session, run, now):
    job = _job(session, run.id)
    if job is None or job.status != "pending":
        raise ValueError("复盘任务已经领取或结束")
    job.status, job.updated_at = "running", now


def settle_job(session, run, now):
    """与 Run 终态同事务。失败、取消和重启均保留未处理消息及计数。"""
    job = _job(session, run.id)
    if job is None:
        return
    job.status = "failed" if run.status == "budget_exhausted" else run.status
    job.error_message = run.stop_reason
    job.finished_at = job.updated_at = now
    cursor = (
        session.get(EvolutionCursor, job.conversation_id, with_for_update=True)
        if job.conversation_id
        else None
    )
    if cursor is None or cursor.active_job_id != job.id:
        return
    cursor.active_job_id = None
    cursor.updated_at = now
    if job.status != "completed" or not job.cursor_to:
        return
    if job.job_type in {"memory_review", "combined_review"}:
        cursor.last_reviewed_message_id = job.cursor_to["message_id"]
        cursor.last_reviewed_run_id = job.cursor_to["run_id"]
        cursor.last_reviewed_at = now
        for field in ("pending_human_turns", "pending_high_signal_events"):
            setattr(
                cursor, field, max(0, getattr(cursor, field) - job.cursor_to["consumed"][field])
            )
    if job.job_type in {"skill_review", "combined_review"}:
        cursor.pending_tool_calls = max(
            0, cursor.pending_tool_calls - job.cursor_to["skill"]["tool_calls"]
        )


def job_view(row):
    fields = (
        "id",
        "run_id",
        "conversation_id",
        "job_type",
        "trigger_kind",
        "focus_hint",
        "status",
        "result_summary",
        "error_message",
        "created_at",
        "updated_at",
        "finished_at",
    )
    return {field: getattr(row, field) for field in fields}


def change_view(row):
    fields = (
        "id",
        "job_id",
        "conversation_id",
        "target_kind",
        "target_id",
        "action",
        "review_state",
        "base_revision",
        "applied_revision",
        "diff_summary",
        "evidence_refs",
        "security_scan_result",
        "created_at",
        "resolved_at",
    )
    value = {field: getattr(row, field) for field in fields}
    if row.diff_artifact_path:
        from sqlalchemy.orm import object_session

        from knowledgeops_agent.evolution.skill_review import read_diff

        try:
            value["diff_summary"] = {
                **value["diff_summary"],
                "unified_diff": read_diff(row, object_session(row)),
            }
        except (ValueError, OSError):
            value["diff_summary"] = {
                **value["diff_summary"],
                "reason": "技能差异文件不可用或校验失败",
            }
    return value


class EvolutionService:
    def __init__(self, services):
        self.jobs = services.jobs
        self.gateway = services.gateway
        self.sessions = services.jobs.sessions

    def _cursor(self, session, conversation_id):
        cursor = session.get(EvolutionCursor, conversation_id, with_for_update=True)
        if cursor is None:
            cursor = EvolutionCursor(conversation_id=conversation_id, updated_at=self.jobs.now())
            session.add(cursor)
            session.flush()
        return cursor

    def _active(self, session, cursor):
        job = session.get(EvolutionJob, cursor.active_job_id) if cursor.active_job_id else None
        return job is not None and job.status in ACTIVE_JOBS

    def _enqueue(
        self,
        session,
        cursor,
        trigger,
        *,
        request_key=None,
        focus_hint="",
        job_type="memory_review",
        inputs=None,
    ):
        conversation_id = cursor.conversation_id if cursor else None
        cursor_from = (
            {
                "message_id": cursor.last_reviewed_message_id,
                "run_id": cursor.last_reviewed_run_id,
                **{name: getattr(cursor, name) for name in COUNTERS},
            }
            if cursor
            else {}
        )
        if job_type in {"skill_review", "combined_review"}:
            latest = session.scalar(
                select(EvolutionJob)
                .where(
                    EvolutionJob.conversation_id == conversation_id,
                    EvolutionJob.status == "completed",
                    EvolutionJob.job_type.in_(["skill_review", "combined_review"]),
                )
                .order_by(EvolutionJob.finished_at.desc(), EvolutionJob.id.desc())
                .limit(1)
            )
            cursor_from["skill"] = latest.cursor_to.get("skill", {}) if latest else {}

        now = self.jobs.now()
        identity = new_id()
        run = Run(
            id=identity,
            root_id=identity,
            checkpoint_id=identity,
            conversation_id=conversation_id,
            request_key=request_key,
            kind="evolution_review",
            origin="system",
            task={
                "memory_review": "后台复盘长期记忆",
                "skill_review": "后台复盘技能",
                "combined_review": "后台复盘记忆与技能",
                "curator": "整理长期记忆与技能",
            }[job_type],
            input_data={"job_type": job_type, **(inputs or {})},
            budgets=Budgets(
                model_requests=4
                if job_type == "combined_review"
                else 1
                if job_type == "curator"
                else 3,
                tool_calls=6,
                searches=0,
                active_seconds=180,
            ).model_dump(),
            created_at=now,
        )
        session.add(run)
        session.flush()
        session.add(RunDispatch(run_id=run.id, generation=run.generation, next_attempt_at=now))
        self.jobs._event(
            session, run, "queued", {"status": "queued", "operation": "evolution_review"}
        )
        job = EvolutionJob(
            run_id=run.id,
            job_type=job_type,
            trigger_kind=trigger,
            conversation_id=conversation_id,
            focus_hint=focus_hint,
            cursor_from=cursor_from,
            created_at=now,
            updated_at=now,
        )
        session.add(job)
        session.flush()
        if cursor is not None:
            cursor.active_job_id = job.id
        return job

    def trigger_manual_review(
        self, conversation_id, focus_hint="", *, request_key, job_type="memory_review"
    ):
        if (
            not 1 <= len(request_key) <= 200
            or len(focus_hint) > 500
            or job_type not in {"memory_review", "skill_review", "combined_review"}
        ):
            raise ValueError("无效的复盘请求")
        key = "evolution:" + hashlib.sha256(request_key.encode()).hexdigest()
        focus = redact_credentials(self.gateway._redact(focus_hint.strip()))
        with self.sessions.begin() as session:
            lock_control(session)
            conversation = require_conversation(session, conversation_id)
            if conversation.trial_case:
                raise ValueError("试用会话不能发起长期记忆复盘")
            prior = session.scalar(
                select(EvolutionJob)
                .join(Run, Run.id == EvolutionJob.run_id)
                .where(Run.request_key == key)
            )
            if prior is not None:
                if (
                    prior.conversation_id != conversation_id
                    or prior.focus_hint != focus
                    or prior.job_type != job_type
                ):
                    raise EvolutionConflict("请求标识已用于另一个复盘请求")
                return job_view(prior)
            if not self.gateway.enabled:
                raise ValueError("模型调用未开启，无法发起复盘")
            cursor = self._cursor(session, conversation_id)
            if self._active(session, cursor):
                raise EvolutionConflict("该会话已有待运行或正在运行的复盘")
            if (
                daily_used(session, self.jobs.now())
                >= settings_values(session)["daily_budget_requests"]
            ):
                raise EvolutionConflict("今日复盘预算已用完")
            return job_view(
                self._enqueue(
                    session, cursor, "manual", request_key=key, focus_hint=focus, job_type=job_type
                )
            )

    def trigger_curator(self, request_key, *, use_model_merge=False):
        if not 1 <= len(request_key) <= 200:
            raise ValueError("无效的整理请求")
        key = "curator:" + hashlib.sha256(request_key.encode()).hexdigest()
        with self.sessions.begin() as session:
            lock_control(session)
            prior = session.scalar(select(Run).where(Run.request_key == key))
            if prior is not None:
                if (
                    prior.kind != "evolution_review"
                    or prior.input_data.get("use_model_merge") != use_model_merge
                ):
                    raise EvolutionConflict("请求标识已用于其他整理请求")
                return job_view(_job(session, prior.id))
            if use_model_merge and not self.gateway.enabled:
                raise ValueError("模型调用未开启，请使用确定性整理")
            if session.scalar(
                select(EvolutionJob.id)
                .where(
                    EvolutionJob.job_type == "curator",
                    EvolutionJob.status.in_(ACTIVE_JOBS),
                )
                .limit(1)
            ):
                raise EvolutionConflict("已有待运行或正在运行的整理任务")
            if (
                use_model_merge
                and daily_used(session, self.jobs.now())
                >= settings_values(session)["daily_budget_requests"]
            ):
                raise EvolutionConflict("今日复盘预算已用完")
            return job_view(
                self._enqueue(
                    session,
                    None,
                    "manual",
                    request_key=key,
                    job_type="curator",
                    inputs={"use_model_merge": use_model_merge},
                )
            )

    def on_research_delivery_accepted(self, session, conversation_id, delivery_id):
        """接受成果提交后，同事务登记受预算与试用边界约束的技能复盘。"""
        if not conversation_id:
            return
        conversation = session.get(Conversation, conversation_id)
        if (
            conversation is None
            or conversation.deletion_state != "active"
            or conversation.trial_case
        ):
            return
        cursor = self._cursor(session, conversation_id)
        cursor.updated_at = self.jobs.now()
        settings = settings_values(session)
        if self._can_auto_enqueue(session, cursor, settings):
            self._enqueue(
                session,
                cursor,
                "high_signal",
                job_type="skill_review",
                inputs={"research_delivery_id": delivery_id},
            )

    def _can_auto_enqueue(self, session, cursor, settings):
        return (
            settings["auto_review_enabled"]
            and self.gateway.enabled
            and not self._active(session, cursor)
            and daily_used(session, self.jobs.now()) < settings["daily_budget_requests"]
        )

    def on_foreground_run_finished(self, finished):
        """已结束的真人运行只累计一次；复盘入队失败不影响前台交付。"""
        if (
            finished["kind"] != "agent"
            or finished["parent_id"] is not None
            or finished["origin"] != "human"
            or finished["input"].get("trial_case")
            or finished["status"] not in {"completed", "failed", "budget_exhausted"}
            or not finished["conversation_id"]
        ):
            return
        try:
            self._accumulate(finished["id"], finished["conversation_id"])
        except Exception as exc:
            logger.warning(
                "evolution cursor update failed for run %s (%s)", finished["id"], type(exc).__name__
            )

    def _accumulate(self, run_id, conversation_id):
        with self.sessions.begin() as session:
            lock_control(session)
            conversation = require_conversation(session, conversation_id)
            if conversation.trial_case:
                return
            run = session.get(Run, run_id)
            if run is None or run.status not in {"completed", "failed", "budget_exhausted"}:
                return
            marker = (
                select(RunEvent.sequence)
                .where(
                    RunEvent.run_id == run_id,
                    RunEvent.kind == "evolution_cursor_counted",
                )
                .limit(1)
            )
            if session.scalar(marker) is not None:
                return
            message = session.scalar(
                select(ChatMessage)
                .where(
                    ChatMessage.run_id == run_id,
                    ChatMessage.role == "user",
                    ChatMessage.conversation_id == conversation_id,
                )
                .limit(1)
            )
            if message is None:
                return
            errors = session.scalar(
                select(func.count())
                .select_from(RunEvent)
                .where(
                    RunEvent.run_id == run_id,
                    RunEvent.kind == "tool_finished",
                    RunEvent.data["status"].astext == "error",
                )
            )
            from knowledgeops_agent.evolution.telemetry import settle_usage

            correction = bool(_HIGH_SIGNAL.search(message.content))
            high = run.status == "failed" or errors >= 2 or correction
            used_managed_skill = bool(
                settle_usage(session, run, correction=correction, now=self.jobs.now())
            )
            cursor = self._cursor(session, conversation_id)
            reviewed = (
                session.get(ChatMessage, cursor.last_reviewed_message_id)
                if cursor.last_reviewed_message_id
                else None
            )
            if reviewed is not None and message_position(message) <= message_position(reviewed):
                # 复盘可先取得刚提交的真人消息，迟到的完成通知不再重复累计。
                self.jobs._event(
                    session, run, "evolution_cursor_counted", {"already_reviewed": True}
                )
                return
            cursor.pending_human_turns += 1
            cursor.pending_tool_calls += run.tool_calls
            cursor.pending_high_signal_events += int(high)
            cursor.updated_at = self.jobs.now()
            self.jobs._event(
                session,
                run,
                "evolution_cursor_counted",
                {
                    "high_signal": high,
                    "tool_calls": run.tool_calls,
                },
            )
            settings = settings_values(session)
            memory_due = bool(cursor.pending_high_signal_events) or (
                cursor.pending_human_turns >= settings["review_turn_threshold"]
            )
            skill_due = cursor.pending_tool_calls >= settings["skill_tool_threshold"] or (
                used_managed_skill and high
            )
            if (memory_due or skill_due) and self._can_auto_enqueue(session, cursor, settings):
                trigger = "high_signal" if high else "auto_threshold"
                job_type = (
                    "combined_review"
                    if memory_due and skill_due
                    else "skill_review"
                    if skill_due
                    else "memory_review"
                )
                self._enqueue(session, cursor, trigger, job_type=job_type)

    def list_jobs(self, *, conversation_id=None, status=None, limit=50):
        return self._list(EvolutionJob, job_view, conversation_id, limit, status=status)

    def list_changes(self, *, conversation_id=None, target_kind=None, review_state=None, limit=50):
        return self._list(
            EvolutionChange,
            change_view,
            conversation_id,
            limit,
            target_kind=target_kind,
            review_state=review_state,
        )

    def _list(self, model, view, conversation_id, limit, **filters):
        with self.sessions() as session:
            if conversation_id:
                require_conversation(session, conversation_id)
            query = (
                select(model)
                .outerjoin(Conversation, Conversation.id == model.conversation_id)
                .where(model.conversation_id.is_(None) | (Conversation.deletion_state == "active"))
            )
            for name, value in {"conversation_id": conversation_id, **filters}.items():
                if value is not None:
                    query = query.where(getattr(model, name) == value)
            return [
                view(row)
                for row in session.scalars(
                    query.order_by(model.created_at.desc(), model.id.desc()).limit(limit)
                )
            ]
