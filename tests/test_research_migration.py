"""从 0045 继续升级到 0052，保留旧聊天、研发身份和版本归属。"""

import os
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import MetaData, Table, create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from knowledgeops_agent.app.runs.service import RunService
from knowledgeops_agent.research.service import ResearchService


def test_migration_preserves_history_and_guards_version_ownership():
    engine = create_engine(os.environ["TEST_DATABASE_URL"])
    assert engine.url.database == "knowledgeops_test"
    schema = "research_migration_" + uuid4().hex
    try:
        with engine.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
            connection.execute(text(f'SET LOCAL search_path TO "{schema}"'))
            config = Config("alembic.ini")
            config.attributes["connection"] = connection
            command.upgrade(config, "0044")
            meta = MetaData()
            conversations = Table("conversations", meta, autoload_with=connection)
            runs = Table("runs", meta, autoload_with=connection)
            messages = Table("chat_messages", meta, autoload_with=connection)
            now = datetime.now(UTC)
            for identity in ["legacy-chat", "another-chat"]:
                connection.execute(
                    conversations.insert().values(id=identity, title=identity, created_at=now)
                )
            connection.execute(
                runs.insert().values(
                    id="legacy-run",
                    root_id="legacy-run",
                    checkpoint_id="legacy-run",
                    conversation_id="legacy-chat",
                    task="原任务",
                    status="completed",
                    format_version=2,
                    kind="agent",
                    input_data={},
                    outcome={"answer": "原答复"},
                    cancel_requested=False,
                    searches=0,
                    event_sequence=0,
                    created_at=now,
                    budgets={},
                    tool_calls=0,
                    active_ms=0,
                )
            )
            connection.execute(
                messages.insert().values(
                    id="legacy-message",
                    conversation_id="legacy-chat",
                    run_id="legacy-run",
                    role="assistant",
                    content="仍可读取的旧聊天",
                    created_at=now,
                )
            )
            before = [
                connection.execute(table.select()).all()
                for table in (conversations, runs, messages)
            ]
            command.upgrade(config, "0045")
            connection.execute(text("""
                INSERT INTO research_tasks (id, title, main_branch_id, status, control_version, created_at)
                VALUES ('old-task', '旧研发任务', 'old-branch', 'paused', 2, now())
            """))
            connection.execute(text("""
                INSERT INTO research_branches (id, task_id, name, head_revision_id)
                VALUES ('old-branch', 'old-task', '主线', 'old-revision')
            """))
            connection.execute(text("""
                INSERT INTO research_revisions
                    (id, branch_id, number, schema_version, state, content_hash, actor, origin, reason, created_at)
                VALUES ('old-revision', 'old-branch', 1, 1,
                    '{"brief":{"goal":"已有目标"},"phase":"exploration"}',
                    'legacy-hash', 'user', 'start', '既有固定版本', now())
            """))
            connection.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
            command.upgrade(config, "0046")
            command.upgrade(config, "0046")
            assert [
                connection.execute(table.select()).all()
                for table in (conversations, runs, messages)
            ] == before
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0046"
            assert connection.execute(text(
                "SELECT status, control_version, deleted_at FROM research_tasks WHERE id = 'old-task'",
            )).one() == ("paused", 2, None)
            revision_before = connection.scalar(text("SELECT revision FROM tool_policies WHERE id = 'default'"))
            command.upgrade(config, "0048")
            assert connection.scalar(text("SELECT count(*) FROM research_method_bundles")) == 0
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0048"
            assert connection.scalar(text("""
                SELECT content->'enabled'->>'builtin:set_research_focus'
                FROM tool_policy_revisions WHERE policy_id = 'default' AND revision = :revision
            """), {"revision": revision_before}) is None
            assert connection.scalar(text("""
                SELECT content->'enabled'->>'builtin:set_research_focus'
                FROM tool_policies WHERE id = 'default'
            """)) == "true"
            command.upgrade(config, "0049")
            assert connection.scalar(text("SELECT count(*) FROM research_permits")) == 0
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0049"
            assert connection.scalar(text(
                "SELECT research_accounted_ms FROM runs WHERE id = 'legacy-run'",
            )) == 0
            assert [
                connection.execute(table.select()).all()
                for table in (conversations, runs, messages)
            ] == before
            assert connection.scalar(text("""
                SELECT content->'enabled'->>'builtin:continue_research'
                FROM tool_policies WHERE id = 'default'
            """)) == "true"
            command.upgrade(config, "0050")
            assert connection.scalar(text("SELECT count(*) FROM research_deliveries")) == 0
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0050"
            assert connection.execute(text("""
                SELECT head_revision_id, pending_delivery_id
                FROM research_branches WHERE id = 'old-branch'
            """)).one() == ("old-revision", None)
            command.upgrade(config, "0051")
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0051"
            assert connection.scalar(text("SELECT count(*) FROM research_maps")) == 0
            command.upgrade(config, "0052")
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0052"
            assert connection.scalar(text("SELECT count(*) FROM research_experiments")) == 0
            assert [connection.execute(table.select()).all() for table in (conversations, runs, messages)] == before

        scoped = create_engine(engine.url, connect_args={"options": f"-csearch_path={schema}"})
        try:
            sessions = sessionmaker(scoped)
            jobs = RunService(sessions)
            service = ResearchService(sessions)
            assert jobs.messages("legacy-chat")[0]["content"] == "仍可读取的旧聊天"
            assert jobs.get_run("legacy-run")["outcome"]["answer"] == "原答复"
            ordinary = jobs.enqueue("another-chat", "正常提问", request_key="ordinary-after-upgrade")
            assert ordinary["status"] == "queued" and "research" not in ordinary["input"]
            assert jobs.claim("ordinary-worker", run_id=ordinary["id"], generation=ordinary["generation"])
            assert jobs.finish(ordinary["id"], "ordinary-worker", outcome={"answer": "普通答复"})["status"] == "completed"
            assert service.get("old-task")["head"]["state"]["brief"]["goal"] == "已有目标"
            first = service.create("legacy-chat", "activate", "旧会话启用研发", "第一份")
            second = service.create("another-chat", "activate", "另一会话启用研发", "第二份")
            assert first["head"]["state"]["brief"]["scope"] == ""
            assert service.for_conversation("legacy-chat") == first
            for statement, params in [
                (
                    "UPDATE research_tasks SET main_branch_id=:branch WHERE id=:id",
                    {"branch": second["main_branch_id"], "id": first["id"]},
                ),
                (
                    "UPDATE research_branches SET head_revision_id=:head WHERE id=:id",
                    {"head": second["head"]["id"], "id": first["main_branch_id"]},
                ),
                (
                    "UPDATE research_revisions SET parent_revision_id=:parent, number=2 WHERE id=:id",
                    {"parent": second["head"]["id"], "id": first["head"]["id"]},
                ),
            ]:
                with pytest.raises(IntegrityError), scoped.begin() as connection:
                    connection.execute(text(statement), params)
            assert service.get(first["id"]) == first
            assert service.get(second["id"]) == second
        finally:
            scoped.dispose()
    finally:
        with engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        engine.dispose()
