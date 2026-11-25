"""0063 的隔离 PostgreSQL 与技能文件合同。"""

from pathlib import Path

import pytest
from test_evolution_phase1 import _conversation, _human_turn
from test_evolution_phase1 import client as _client
from test_evolution_phase1 import services as _services
from test_skill_management import install
from test_skill_management import manager as _manager

from knowledgeops_agent.persistence.models import SkillPackageState

manager = _manager
client = _client
services = _services


def test_skill_history_and_lock(manager):
    installed = install(manager, "first")
    locked = manager.set_protection(
        "example", expected_revision=installed["revision"], protection_level="locked"
    )
    assert locked["revision"] == 2
    with pytest.raises(ValueError, match="锁定"):
        manager.remove("example", expected_revision=2)
    with pytest.raises(ValueError, match="固定|锁定"):
        manager.archive("example", expected_revision=2)
    with pytest.raises(ValueError, match="锁定"):
        install(manager, "never published", expected_revision=2)
    manager.set_protection("example", expected_revision=2, protection_level="normal")
    assert install(manager, "second", expected_revision=3)["revision"] == 4
    with manager.sessions() as session:
        snapshot = manager.read_revision_snapshot(session, "example", 1)
        assert b"first" in snapshot.files["SKILL.md"]
        assert session.get(SkillPackageState, "example").revision == 4
    assert [item["revision"] for item in manager.revisions("example")] == [4, 3, 2, 1]
    first = manager.revisions("example")[-1]
    assert not Path(first["snapshot_path"]).is_absolute()
    (manager.root / ".management" / first["snapshot_path"]).write_bytes(b"tampered")
    with manager.sessions() as session, pytest.raises(ValueError, match="SHA256"):
        manager.read_revision_snapshot(session, "example", 1)


def test_archive_preserves_package_and_disabled_policy(manager):
    install(manager)
    assert manager.archive("example", expected_revision=1)["archived_at"]
    assert (manager.root / "example/SKILL.md").is_file()
    assert manager.catalog() == []
    (item,) = manager.catalog(include_archived=True)
    assert not item["enabled"] and item["archived_at"]
    assert manager.unarchive("example", expected_revision=2)["archived_at"] is None
    assert not manager.catalog()[0]["enabled"]


def test_0063_migration_preserves_0062_records():
    import os
    from uuid import uuid4

    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, text

    engine = create_engine(os.environ["TEST_DATABASE_URL"])
    assert engine.url.database == "knowledgeops_test"
    schema = "evolution2_" + uuid4().hex
    try:
        with engine.begin() as conn:
            conn.execute(text(f'CREATE SCHEMA "{schema}"'))
            conn.execute(text(f'SET LOCAL search_path TO "{schema}"'))
            config = Config("alembic.ini")
            config.attributes["connection"] = conn
            command.upgrade(config, "0062")
            conn.execute(
                text("""INSERT INTO skill_packages
                (name, revision, content_hash, removed, created_at, updated_at)
                VALUES ('legacy', 9, 'digest', false, now(), now())""")
            )
            conn.execute(
                text("""INSERT INTO preferences
                (id, revision, content, scope, topic, status, source, created_at)
                VALUES ('legacy', 1, '原有记忆', 'global', '', 'active', '{}', now())""")
            )
            conn.execute(text("UPDATE evolution_settings SET daily_budget_requests=7"))
            for attempt in range(2):
                command.upgrade(config, "0063")
                assert (
                    conn.execute(
                        text("SELECT curator_stale_days FROM evolution_settings")
                    ).scalar_one()
                    == 30
                )
                assert conn.execute(text("SELECT count(*) FROM skill_revisions")).scalar_one() == 0
                if attempt == 0:
                    command.downgrade(config, "0062")
                assert conn.execute(text("SELECT revision FROM skill_packages")).scalar_one() == 9
                assert (
                    conn.execute(text("SELECT content FROM preferences")).scalar_one() == "原有记忆"
                )
                assert (
                    conn.execute(
                        text("SELECT daily_budget_requests FROM evolution_settings")
                    ).scalar_one()
                    == 7
                )
    finally:
        with engine.begin() as conn:
            conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        engine.dispose()


def _frozen_skill_run(services, conversation):
    from uuid import uuid4

    from knowledgeops_agent.harness.runtime.agent import RunContext
    from knowledgeops_agent.harness.runtime.resources import prepare_resources

    jobs = services.jobs
    run = jobs.enqueue(conversation, "校验表格后写出结论", request_key=uuid4().hex)
    assert jobs.claim("phase2-foreground", run_id=run["id"], generation=run["generation"])
    context = RunContext(
        run_id=run["id"],
        root_id=run["id"],
        worker_id="phase2-foreground",
        jobs=jobs,
        knowledge=services.knowledge,
        sources=services.sources,
        gateway=services.gateway,
    )
    manifest = prepare_resources(context)
    jobs.finish(run["id"], "phase2-foreground", status="completed")
    return run, manifest


def _run_skill_job(client, services, conversation, proposals, *, during_call=None):
    import json
    from uuid import uuid4

    from langchain_core.messages import AIMessage

    from knowledgeops_agent.app.runs.worker import execute_run

    class Provider:
        def invoke(self, messages, **kwargs):
            assert not kwargs.get("tools")
            packet = json.loads(messages[-1].text)
            if during_call:
                during_call(packet)
            return AIMessage(json.dumps({"proposals": proposals}, ensure_ascii=False))

    services.gateway.enabled = True
    services.gateway.client = Provider()
    queued = client.post(
        "/api/evolution/reviews",
        json={
            "conversation_id": conversation,
            "job_type": "skill_review",
            "request_key": uuid4().hex,
        },
    )
    assert queued.status_code == 202, queued.text
    return execute_run(queued.json()["run_id"], 1, services=services)


def _skill_job(client, services, conversation, proposal, *, during_call=None):
    from knowledgeops_agent.app.runs.worker import execute_run

    run = _run_skill_job(client, services, conversation, [proposal], during_call=during_call)
    assert run["status"] == "completed", run
    assert execute_run(run["id"], 1, services=services) is None
    return client.get("/api/evolution/changes?target_kind=skill").json()[0]


def _patch(installed, source, **overrides):
    return {
        "action": "patch",
        "skill_name": installed["name"],
        "base_revision": installed["revision"],
        "base_content_hash": installed["content_hash"],
        "old_text": "initial",
        "new_text": "先核对表格再整理。",
        "evidence_summary": "用户确认示例表达方式。",
        "source_message_id": source,
        "quote": "回答先给出可运行示例",
        "reason": "固定核对步骤",
        **overrides,
    }


def test_skill_review_preserves_frozen_run_and_rolls_back_exact_package(client, services, manager):
    from knowledgeops_agent.harness.runtime.resources import ResourceService
    from knowledgeops_agent.harness.tools.policy import ToolPolicyService

    installed = install(manager)
    policy = ToolPolicyService(services.sessions)
    policy.update(expected_revision=policy.get()["revision"], skill_enabled={"example": True})
    conversation = _conversation(client)
    first, frozen = _frozen_skill_run(services, conversation)
    assert any("initial" in part["content"] for part in frozen["skills"])
    _, source = _human_turn(services, conversation)
    change = _skill_job(client, services, conversation, _patch(installed, source))
    assert change["review_state"] == "applied" and change["applied_revision"] == 2
    assert change["diff_summary"]["unified_diff"].count("+先核对表格再整理。") == 1
    assert (manager.root / "example/scripts/main.py").read_bytes() == b"print('ready')"
    _, fresh = _frozen_skill_run(services, conversation)
    assert any("先核对表格" in part["content"] for part in fresh["skills"])
    assert (
        ResourceService(services.sessions).read(first["id"])["content"]["skills"]
        == frozen["skills"]
    )
    rolled = client.post(f"/api/evolution/changes/{change['id']}/rollback")
    assert rolled.status_code == 200, rolled.text
    assert rolled.json()["diff_summary"]["rollback_revision"] == 3
    assert "initial" in (manager.root / "example/SKILL.md").read_text()
    assert [row["source_kind"] for row in manager.revisions("example")] == [
        "rollback",
        "evolution_patch",
        "manual_import",
    ]


def test_pending_skill_patches_deduplicate_by_candidate_in_and_across_reviews(
    client, services, manager
):
    installed = install(manager)
    client.patch(
        "/api/evolution/settings", json={"expected_revision": 1, "skill_auto_write": False}
    ).raise_for_status()
    conversation = _conversation(client)
    _, source = _human_turn(services, conversation)
    patch = _patch(installed, source)
    first = _run_skill_job(
        client, services, conversation, [patch, {**patch, "reason": "同一正文的另一种说明"}]
    )
    assert first["status"] == "completed", first
    assert first["outcome"]["result_summary"]["pending_review"] == 1
    assert first["outcome"]["result_summary"]["skipped_duplicate"] == 1
    _, source = _human_turn(services, conversation)
    second = _run_skill_job(
        client,
        services,
        conversation,
        [_patch(installed, source), _patch(installed, source, new_text="先核对来源和日期。")],
    )
    assert second["status"] == "completed", second
    assert second["outcome"]["result_summary"]["pending_review"] == 1
    assert second["outcome"]["result_summary"]["skipped_duplicate"] == 1
    changes = client.get(
        "/api/evolution/changes?target_kind=skill&review_state=pending_review"
    ).json()
    assert len(changes) == 2
    assert len({row["diff_summary"]["candidate_hash"] for row in changes}) == 2


@pytest.mark.parametrize(
    ("mode", "expected", "reason"),
    [
        ("script", "rejected", "invalid_proposal"),
        ("path", "rejected", "invalid_proposal"),
        ("credential", "rejected", "security_scan"),
        ("locked", "rejected", "target_locked"),
        ("unread", "rejected", "target_not_read"),
        ("conflict", "skipped_conflict", "target_revision_changed"),
        ("pinned", "pending_review", "target_pinned"),
        ("disabled", "pending_review", "automatic_write_disabled"),
        ("large", "pending_review", "manual_skill_review"),
    ],
)
def test_skill_review_validates_before_writing(client, services, manager, mode, expected, reason):
    installed = install(manager)
    if mode == "locked" or mode == "pinned":
        installed = manager.set_protection("example", expected_revision=1, protection_level=mode)
    if mode == "disabled":
        client.patch(
            "/api/evolution/settings", json={"expected_revision": 1, "skill_auto_write": False}
        ).raise_for_status()
    conversation = _conversation(client)
    _, message = _human_turn(services, conversation)
    overrides = {
        "script": {"target_file": "scripts/main.py"},
        "path": {"target_file": "a/../SKILL.md"},
        "credential": {"new_text": "Bearer fictionaltestcredential1234"},
        "unread": {"skill_name": "unknown"},
        "large": {"new_text": "文" * 1700},
    }.get(mode, {})
    source = manager.root / "example/SKILL.md"
    before = source.read_bytes()
    change = _skill_job(
        client,
        services,
        conversation,
        _patch(installed, message, **overrides),
        during_call=(lambda packet: install(manager, "人工修订", expected_revision=1))
        if mode == "conflict"
        else None,
    )
    assert change["review_state"] == expected
    assert change["diff_summary"]["reason_code"] == reason
    assert "fictionaltestcredential1234" not in str(change)
    if mode == "conflict":
        assert "人工修订" in source.read_text()
    else:
        assert source.read_bytes() == before
    if mode == "pinned":
        assert client.post(f"/api/evolution/changes/{change['id']}/approve").status_code == 200


@pytest.mark.parametrize("tamper", [False, True])
def test_skill_rollback_conflicts_with_current_edit_or_tampered_history(
    client, services, manager, tamper
):
    installed = install(manager)
    conversation = _conversation(client)
    _, source = _human_turn(services, conversation)
    change = _skill_job(client, services, conversation, _patch(installed, source))
    assert change["review_state"] == "applied"
    if tamper:
        oldest = manager.revisions("example")[-1]
        (manager.root / ".management" / oldest["snapshot_path"]).write_bytes(b"tampered")
    else:
        install(manager, "后续修订", expected_revision=2)
    before = (manager.root / "example/SKILL.md").read_bytes()
    result = client.post(f"/api/evolution/changes/{change['id']}/rollback")
    assert result.status_code == 409, result.text
    assert (manager.root / "example/SKILL.md").read_bytes() == before


def test_telemetry_settles_each_attempt_once_and_triggers_combined(client, services, manager):
    from knowledgeops_agent.evolution.service import EvolutionService
    from knowledgeops_agent.persistence.models import RunSkillUse

    install(manager)
    conversation = _conversation(client)
    run, _ = _human_turn(services, conversation, "继续整理当前表格")
    with services.sessions.begin() as session:
        session.add(
            RunSkillUse(run_id=run["id"], resource="/skills/example/SKILL.md", content_hash="x")
        )
        session.add(
            RunSkillUse(run_id=run["id"], resource="/skills/unmanaged/SKILL.md", content_hash="x")
        )
    service = EvolutionService(services)
    services.gateway.enabled = True
    service.on_foreground_run_finished(run)
    service.on_foreground_run_finished(run)
    assert service.list_jobs() == []
    failed, _ = _human_turn(services, conversation, "不对，以后记住先核对。", status="failed")
    with services.sessions.begin() as session:
        session.add(
            RunSkillUse(run_id=failed["id"], resource="/skills/example/SKILL.md", content_hash="y")
        )
    service.on_foreground_run_finished(failed)
    (job,) = service.list_jobs()
    assert job["job_type"] == "combined_review"
    telemetry = manager.detail("example")["telemetry"]
    assert (
        telemetry["load_count"],
        telemetry["success_run_count"],
        telemetry["failed_run_count"],
        telemetry["correction_count"],
    ) == (2, 1, 1, 1)
    assert telemetry["last_used_at"] is not None and telemetry["last_updated_at"] is not None


def test_curator_zero_cost_approval_and_rollback(client, services, manager):
    from datetime import timedelta
    from uuid import uuid4

    from knowledgeops_agent.app.runs.worker import execute_run
    from knowledgeops_agent.harness.tools.policy import ToolPolicyService
    from knowledgeops_agent.memory.service import MemoryService
    from knowledgeops_agent.persistence.models import Preference, utcnow

    install(manager)
    policy = ToolPolicyService(services.sessions)
    policy.update(expected_revision=policy.get()["revision"], skill_enabled={"example": True})
    memory = MemoryService(services.sessions)
    first = memory.create("示例先检查输入来源再验证输出格式", scope="global", topic="")
    second = memory.create("示例先检查输入来源再验证输出格式。", scope="global", topic="")
    stale = memory.create("我正在学习事务模型", scope="global", topic="", category="goal")
    with services.sessions.begin() as session:
        session.get(SkillPackageState, "example").created_at = utcnow() - timedelta(days=45)
        session.get(Preference, stale["id"]).review_after = utcnow() - timedelta(days=2)
        session.get(Preference, stale["id"]).confirmed_at = utcnow() - timedelta(days=90)
    preview = client.get("/api/evolution/curator/preview").json()
    assert {row["action"] for row in preview["items"]} >= {"merge", "deprecate", "archive"}
    assert client.get("/api/evolution/changes").json() == []
    for attempt in range(2):
        queued = client.post("/api/evolution/curator", json={"request_key": uuid4().hex})
        assert queued.status_code == 202, queued.text
        run_id = queued.json()["run_id"]
        run = execute_run(run_id, 1, services=services)
        assert run["status"] == "completed", run
        assert not services.gateway.ledger.records(root_id=run_id)
        if attempt:
            assert run["outcome"]["result_summary"]["skipped_duplicate"] == 3
    changes = {row["action"]: row for row in client.get("/api/evolution/changes").json()}
    for action in ("merge", "archive"):
        change = changes[action]
        api = f"/api/evolution/changes/{change['id']}"
        approved = client.post(api + "/approve")
        assert approved.status_code == 200, approved.text
        if action == "archive":
            assert manager.catalog() == []
            assert (manager.root / "example/SKILL.md").exists()
            assert _frozen_skill_run(services, _conversation(client))[1]["skills"] == []
        else:
            assert (
                memory.get(first["id"])["status"]
                == memory.get(second["id"])["status"]
                == "inactive"
            )
        rolled = client.post(api + "/rollback")
        assert rolled.status_code == 200, rolled.text
    assert manager.detail("example")["enabled"]
    assert memory.get(first["id"])["status"] == memory.get(second["id"])["status"] == "active"


def test_curator_preserves_protected_or_frozen_assets(client, services, manager):
    from datetime import timedelta

    from knowledgeops_agent.harness.runtime.resources import content_hash
    from knowledgeops_agent.harness.tools.policy import ToolPolicyService
    from knowledgeops_agent.memory.service import MemoryService
    from knowledgeops_agent.persistence.models import (
        LearningTask,
        Preference,
        Run,
        RunResourceSnapshot,
        SkillUsageTelemetry,
        utcnow,
    )

    install(manager)
    policy = ToolPolicyService(services.sessions)
    policy.update(expected_revision=policy.get()["revision"], skill_enabled={"example": True})
    run, manifest = _frozen_skill_run(services, _conversation(client))
    memory = MemoryService(services.sessions).create(
        "保留当前学习任务的事务案例", scope="global", topic="", category="goal"
    )
    with services.sessions.begin() as session:
        task = LearningTask(
            request_key="curator-live", creation_input={}, title="事务模型", goal="核对隔离级别"
        )
        session.add(task)
        session.flush()
        session.get(Run, run["id"]).learning_task_id = task.id
        snapshot = session.get(RunResourceSnapshot, run["id"])
        snapshot.content = {**manifest, "preferences": [{"id": memory["id"]}]}
        snapshot.content_hash = content_hash(snapshot.content)
        stale = utcnow() - timedelta(days=45)
        row = session.get(Preference, memory["id"])
        row.review_after = stale
        row.confirmed_at = stale
        session.get(SkillPackageState, "example").created_at = stale
        usage = session.get(SkillUsageTelemetry, "example")
        usage.load_count = 4
        usage.failed_run_count = 3
        usage.last_used_at = stale

    def candidates():
        result = client.get("/api/evolution/curator/preview")
        assert result.status_code == 200, result.text
        return result.json()["items"]

    active = candidates()
    assert not any(item["target_id"] == memory["id"] for item in active)
    (warning,) = [item for item in active if item["target_id"] == "example"]
    assert (warning["action"], warning["reason_code"], warning["readonly"]) == (
        "inspect",
        "skill_high_failure",
        True,
    )
    with services.sessions.begin() as session:
        session.get(LearningTask, task.id).status = "completed"
    for revision, protection in enumerate(("pinned", "locked", "normal"), start=1):
        manager.set_protection("example", expected_revision=revision, protection_level=protection)
        actions = {item["action"] for item in candidates() if item["target_id"] == "example"}
        assert actions == ({"archive"} if protection == "normal" else set())


def test_memory_snapshot_get_never_freezes_on_read(client, services):
    from knowledgeops_agent.memory.service import MemoryService

    conversation = _conversation(client)
    MemoryService(services.sessions).create("固定字段", scope="global", topic="")
    url = f"/api/conversations/{conversation}/memory-snapshot"
    for _ in range(2):
        assert client.get(url).json() == {
            "frozen": False,
            "entry_count": 0,
            "snapshot_hash": None,
            "frozen_at": None,
        }
    assert client.post(url + "/refresh").status_code == 200
    assert client.get(url).json()["entry_count"] == 1


def test_tool_threshold_triggers_skill_without_advancing_memory(client, services):
    import json
    from uuid import uuid4

    from langchain_core.messages import AIMessage

    from knowledgeops_agent.app.runs.worker import execute_run
    from knowledgeops_agent.evolution.service import EvolutionService
    from knowledgeops_agent.persistence.models import EvolutionCursor, Run

    conversation = _conversation(client)
    run, _ = _human_turn(services, conversation, "请检查章节布局")
    with services.sessions.begin() as session:
        session.get(Run, run["id"]).tool_calls = 8
    services.gateway.enabled = True

    class Provider:
        def invoke(self, messages, **kwargs):
            assert "skill_targets" in json.loads(messages[-1].text)
            return AIMessage('{"proposals":[]}')

    services.gateway.client = Provider()
    evolution = EvolutionService(services)
    evolution.on_foreground_run_finished(run)
    (job,) = evolution.list_jobs()
    assert job["job_type"] == "skill_review" and job["trigger_kind"] == "auto_threshold"
    assert execute_run(job["run_id"], 1, services=services)["status"] == "completed"
    with services.sessions() as session:
        cursor = session.get(EvolutionCursor, conversation)
        assert cursor.pending_tool_calls == 0 and cursor.pending_human_turns == 1
        assert cursor.last_reviewed_message_id is None
    assert (
        client.post(
            "/api/evolution/reviews",
            json={
                "conversation_id": conversation,
                "request_key": uuid4().hex,
            },
        ).json()["job_type"]
        == "memory_review"
    )


def test_skill_replacement_reconciles_immediately_after_commit_and_rollback(
    client, services, manager, monkeypatch
):
    first = install(manager)
    conversation = _conversation(client)
    _, source = _human_turn(services, conversation)
    proposal = _patch(first, source)
    management = manager.root / ".management"
    package = manager.storage.package("example")

    def assert_reconciled(revision, digest):
        for directory in ("journal", "rollback", "staging"):
            assert not list((management / directory).iterdir()), directory
        with manager.sessions() as session:
            state = session.get(SkillPackageState, "example")
            assert (state.revision, state.content_hash) == (revision, digest)
        assert manager.storage.read(package).digest == digest

    original_finish = services.jobs.finish

    def abort_finish(*args, complete_evolution=None, **kwargs):
        if complete_evolution is None:
            return original_finish(*args, **kwargs)

        def abort_after_replacement(session):
            complete_evolution(session)
            assert list((management / "journal").glob("*.json"))
            assert manager.storage.read(package).digest != first["content_hash"]
            raise ValueError("模拟复盘终态事务回滚")

        return original_finish(*args, complete_evolution=abort_after_replacement, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(services.jobs, "finish", abort_finish)
        failed = _run_skill_job(client, services, conversation, [proposal])
    assert failed["status"] == "failed"
    assert failed["stop_reason"] == "模拟复盘终态事务回滚"
    assert_reconciled(1, first["content_hash"])

    change = _skill_job(client, services, conversation, proposal)
    assert change["review_state"] == "applied"
    assert_reconciled(2, change["diff_summary"]["candidate_hash"])

    rolled = client.post(f"/api/evolution/changes/{change['id']}/rollback")
    assert rolled.status_code == 200, rolled.text
    assert_reconciled(3, first["content_hash"])

    _, source = _human_turn(services, conversation)
    client.patch(
        "/api/evolution/settings", json={"expected_revision": 1, "skill_auto_write": False}
    ).raise_for_status()
    pending = _skill_job(client, services, conversation, _patch({**first, "revision": 3}, source))
    assert pending["review_state"] == "pending_review"
    approved = client.post(f"/api/evolution/changes/{pending['id']}/approve")
    assert approved.status_code == 200, approved.text
    assert_reconciled(4, pending["diff_summary"]["candidate_hash"])
