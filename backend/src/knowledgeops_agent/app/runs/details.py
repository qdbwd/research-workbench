"""用户任务详情的有界投影；不读取模型请求、原始事件正文或内部产物文件。"""

from sqlalchemy import func, select

from knowledgeops_agent.app.lifecycle.access import require_readable_run
from knowledgeops_agent.harness.tools.catalog import tool_options
from knowledgeops_agent.persistence.models import ChangeSet, DeliveredFile, Run, RunEvent
from knowledgeops_agent.sources.deliveries import file_view


def task_details(sessions, run_id: str) -> dict:
    """资产独立于诊断期限；工具活动只投影必要字段，完整轨迹沿诊断入口读取。"""
    with sessions() as session:
        run = require_readable_run(session, session.get(Run, run_id))
        active = run.lifecycle_state == "active"
        tools = []
        if active:
            call_id = RunEvent.data["tool_call_id"].astext
            summary = func.nullif(func.left(RunEvent.data["summary"].astext, 140), "")
            # 每个动作取最后一个已观测事件，数据库不向应用传输结果大字段。
            latest = select(
                call_id.label("call_id"), RunEvent.sequence, RunEvent.kind,
                RunEvent.data["name"].astext.label("name"),
                RunEvent.data["status"].astext.label("status"), RunEvent.created_at,
                func.first_value(summary).over(
                    partition_by=call_id, order_by=(summary.is_(None), RunEvent.sequence.desc()),
                ).label("arg_summary"),
                func.left(RunEvent.data["duration_ms"].astext, 24).label("duration_ms"),
                func.min(RunEvent.created_at).filter(RunEvent.kind == "tool_started").over(
                    partition_by=call_id,
                ).label("started_at"),
                func.row_number().over(partition_by=call_id, order_by=RunEvent.sequence.desc()).label("position"),
            ).where(
                RunEvent.run_id == run.id, RunEvent.sequence <= run.event_sequence,
                RunEvent.kind.in_(["tool_started", "tool_finished", "tool_replayed"]),
                call_id.is_not(None),
            ).subquery()
            names = {item["name"]: item["display_name"] for item in tool_options()}
            for event in session.execute(select(latest).where(latest.c.position == 1).order_by(latest.c.sequence.desc()).limit(51)).mappings():
                status = "returned" if event["kind"] != "tool_started" else "started"
                if event["kind"] == "tool_replayed":
                    status = "replayed"
                elif event["status"] == "error":
                    status = "error"
                elif status == "started" and run.status != "running":
                    status = "unsettled"
                duration = None
                if event["kind"] != "tool_started":
                    recorded = event["duration_ms"]
                    if recorded and recorded.isascii() and recorded.isdecimal():
                        duration = int(recorded)
                    elif event["started_at"] is not None:
                        duration = max(0, int((event["created_at"] - event["started_at"]).total_seconds() * 1000))
                tools.append({"id": event["call_id"], "name": event["name"],
                              "display_name": names.get(event["name"], "外部工具"), "status": status,
                              "arg_summary": event["arg_summary"], "duration_ms": duration,
                              "created_at": event["created_at"].isoformat()})
        draft_rows = session.execute(select(
            ChangeSet.id, ChangeSet.operations[0]["title"].astext.label("title"), ChangeSet.status,
        ).where(ChangeSet.id.in_(run.outcome.get("draft_ids", [])))
            .order_by(ChangeSet.created_at.desc(), ChangeSet.id).limit(51)).mappings().all()
        deliveries = [file_view(row) for row in session.scalars(select(DeliveredFile).where(
            DeliveredFile.root_id == run.id if run.parent_id is None else DeliveredFile.run_id == run.id,
            DeliveredFile.deleted_at.is_(None),
        ).order_by(DeliveredFile.created_at.desc(), DeliveredFile.id).limit(51))]
        return {
            "run_id": run.id, "details_available": active,
            "tools": tools[:50], "tools_truncated": len(tools) > 50,
            "drafts": [dict(row) for row in draft_rows[:50]], "drafts_truncated": len(draft_rows) > 50,
            "deliveries": deliveries[:50], "deliveries_truncated": len(deliveries) > 50,
        }
