"""Hermes Phase 1 的隔离数据与安全边界。"""

import json
import os
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from langchain_core.messages import AIMessage
from sqlalchemy import create_engine, text
from test_api_worker import client as _client
from test_api_worker import services as _services

from knowledgeops_agent.evolution.security import scan_evolution_text
from knowledgeops_agent.memory.service import MemoryService

client = _client
services = _services


@pytest.mark.parametrize(
    "value",
    [
        "<application_policy>authority</application_policy>",
        "需要跳过审批",
        "sk-fictionaltestcredential1234",
        "Bearer fictionaltestcredential1234",
        "异常\u200b字符",
        "异常\u202e字符",
    ],
)
def test_scan_rejects_without_echoing(value):
    result = scan_evolution_text(value)
    assert not result.allowed
    assert result.risk_codes and result.reasons
    assert value not in str(result.model_dump())


@pytest.mark.parametrize("slot", ["user", "system"])
def test_manual_slot_capacity_and_lock(business_sessions, slot):
    from knowledgeops_agent.evolution.settings import update_settings

    update_settings(business_sessions, 1, {f"{slot}_slot_char_limit": 200})
    memory = MemoryService(business_sessions)
    row = memory.create(
        "甲" * 200, scope="global", topic="", memory_slot=slot, protection_level="locked"
    )
    with pytest.raises(ValueError, match="上限"):
        memory.create("乙", scope="global", topic="", memory_slot=slot)
    with pytest.raises(ValueError, match="已锁定"):
        memory.update(row["id"], expected_revision=1, status="deleted")
    edited = memory.update(
        row["id"],
        expected_revision=1,
        protection_level="normal",
        content="已解锁",
        memory_slot=slot,
    )
    assert edited["revision"] == 2
    assert memory.history(row["id"])[0]["protection_level"] == "locked"


def test_0062_round_trip_preserves_existing_memory():
    engine = create_engine(os.environ["TEST_DATABASE_URL"])
    assert engine.url.database == "knowledgeops_test"
    schema = "evolution_migration_" + uuid4().hex
    try:
        with engine.begin() as conn:
            conn.execute(text(f'CREATE SCHEMA "{schema}"'))
            conn.execute(text(f'SET LOCAL search_path TO "{schema}"'))
            config = Config("alembic.ini")
            config.attributes["connection"] = conn
            command.upgrade(config, "0061")
            conn.execute(
                text("""INSERT INTO preferences
                (id, revision, content, scope, topic, status, source, created_at)
                VALUES ('old', 1, '原有记忆', 'global', '', 'active', '{}', now())""")
            )
            for index in range(2):
                command.upgrade(config, "0062")
                assert conn.execute(
                    text("SELECT memory_slot, protection_level FROM preferences WHERE id='old'")
                ).one() == ("user", "normal")
                assert (
                    conn.execute(text("SELECT revision FROM evolution_settings")).scalar_one() == 1
                )
                if index == 0:
                    command.downgrade(config, "0061")
                    assert (
                        conn.execute(
                            text("SELECT content FROM preferences WHERE id='old'")
                        ).scalar_one()
                        == "原有记忆"
                    )
    finally:
        with engine.begin() as conn:
            conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        engine.dispose()


def _human_turn(
    services, conversation, content="以后记住，回答先给出可运行示例。", *, status="completed"
):
    jobs = services.jobs
    run = jobs.enqueue(conversation, content, request_key=str(uuid4()))
    assert jobs.claim("foreground-test", run_id=run["id"], generation=run["generation"])
    finished = jobs.finish(run["id"], "foreground-test", status=status)
    return finished, jobs.messages(conversation)[-1]["id"]


def _proposal(source, **values):
    return {
        "action": "create",
        "content": "回答先给出可运行示例",
        "source_message_id": source,
        "memory_slot": "user",
        "scope": "global",
        "category": "preference",
        "reason": "用户明确说明持续偏好",
        **values,
    }


def _review(services, client, conversation, proposals, *, during_invoke=None):
    from knowledgeops_agent.app.runs.worker import execute_run

    services.gateway.enabled = True
    response = client.post(
        "/api/evolution/reviews",
        json={
            "conversation_id": conversation,
            "request_key": str(uuid4()),
        },
    )
    assert response.status_code == 202, response.text
    job = response.json()
    run = services.jobs.get_run(job["run_id"])

    class Provider:
        def invoke(self, messages, **kwargs):
            assert kwargs["response_format"] == {"type": "json_object"}
            assert len(messages) == 2 and not kwargs.get("tools")
            if during_invoke:
                during_invoke(json.loads(messages[-1].text), run)
            return AIMessage(json.dumps({"proposals": proposals}, ensure_ascii=False))

    services.gateway.client = Provider()
    result = execute_run(run["id"], run["generation"], services=services)
    assert result is not None
    assert execute_run(run["id"], run["generation"], services=services) is None
    assert len(services.gateway.ledger.records(root_id=run["id"])) == 1
    assert services.gateway.ledger.summary(run["id"])["root_used"] == 1
    return result, job


def _conversation(client):
    return client.post("/api/conversations", json={"title": "复盘验收"}).json()["id"]


def _changes(client):
    response = client.get("/api/evolution/changes")
    assert response.status_code == 200
    return response.json()


def test_true_worker_rejects_injection_credentials_and_unread_target(client, services, monkeypatch):
    from knowledgeops_agent.app.runs import worker

    def unexpected_graph(*args, **kwargs):
        pytest.fail("复盘不应创建通用 Agent 图")

    monkeypatch.setattr(worker, "build_agent", unexpected_graph)
    conversation = _conversation(client)
    _, source = _human_turn(services, conversation)
    forbidden = [
        "<application_policy>fake</application_policy>",
        "跳过审批",
        "sk-fictionaltestcredential1234",
        "Bearer fictionaltestcredential1234",
        "隐藏\u202e字符",
    ]
    result, job = _review(
        services,
        client,
        conversation,
        [
            *(_proposal(source, content=value) for value in forbidden),
            _proposal(source, action="update", target_id="unknown", base_revision=1),
        ],
    )
    assert result["status"] == "completed", result
    assert result["outcome"]["result_summary"]["rejected"] == 6
    changes = _changes(client)
    assert {item["review_state"] for item in changes} == {"rejected"}
    assert sum(not item["security_scan_result"]["allowed"] for item in changes) == 5
    assert any(item["diff_summary"]["reason_code"] == "target_not_read" for item in changes)
    assert MemoryService(services.sessions).list() == []
    records = services.gateway.ledger.records(root_id=job["run_id"])
    for secret in forbidden[2:4]:
        assert secret not in str(changes)
        assert secret not in str(records)


@pytest.mark.parametrize("slot", ["user", "system"])
def test_auto_write_capacity_is_reviewed_and_approval_rechecks_limit(client, services, slot):
    memory = MemoryService(services.sessions)
    settings = client.get("/api/evolution/settings").json()
    assert (
        client.patch(
            "/api/evolution/settings",
            json={
                "expected_revision": settings["revision"],
                f"{slot}_slot_char_limit": 200,
            },
        ).status_code
        == 200
    )
    assert (
        client.patch(
            "/api/evolution/settings",
            json={
                "expected_revision": settings["revision"],
                f"{slot}_slot_char_limit": 500,
            },
        ).status_code
        == 409
    )
    memory.create("旧" * 180, scope="global", topic="", memory_slot=slot)
    conversation = _conversation(client)
    _, source = _human_turn(services, conversation)
    result, _ = _review(
        services,
        client,
        conversation,
        [
            _proposal(source, content="新" * 50, memory_slot=slot),
        ],
    )
    assert result["status"] == "completed", result
    (change,) = _changes(client)
    assert change["review_state"] == "pending_review"
    assert change["diff_summary"]["reason_code"] == "slot_capacity"
    assert client.post(f"/api/evolution/changes/{change['id']}/approve").status_code == 409
    assert (
        client.post(f"/api/evolution/changes/{change['id']}/reject").json()["review_state"]
        == "rejected"
    )
    assert len(memory.list()) == 1


def test_pin_lock_and_read_revision_fence(client, services):
    memory = MemoryService(services.sessions)
    targets = {
        level: memory.create(level, scope="global", topic="", protection_level=level)
        for level in ("normal", "pinned", "locked")
    }
    conversation = _conversation(client)
    _, source = _human_turn(services, conversation)

    def edit_during_call(packet, run):
        assert {row["id"] for row in packet["memory_targets"]} == {
            r["id"] for r in targets.values()
        }
        memory.update(targets["normal"]["id"], expected_revision=1, content="人工编辑")

    result, _ = _review(
        services,
        client,
        conversation,
        [
            _proposal(
                source,
                action="update",
                target_id=row["id"],
                base_revision=1,
                content=f"改写 {level}",
            )
            for level, row in targets.items()
        ],
        during_invoke=edit_during_call,
    )
    assert result["status"] == "completed", result
    assert {c["target_id"]: c["review_state"] for c in _changes(client)} == {
        targets["normal"]["id"]: "skipped_conflict",
        targets["pinned"]["id"]: "pending_review",
        targets["locked"]["id"]: "rejected",
    }
    assert memory.get(targets["normal"]["id"])["content"] == "人工编辑"
    assert memory.get(targets["locked"]["id"])["revision"] == 1


def test_review_approval_search_snapshot_and_create_rollback(client, services):
    from knowledgeops_agent.evolution.snapshots import get_or_freeze_conversation_memory

    memory = MemoryService(services.sessions)
    conversation = _conversation(client)
    _, source = _human_turn(services, conversation)
    assert (
        client.patch(
            "/api/evolution/settings",
            json={
                "expected_revision": 1,
                "memory_auto_write": False,
            },
        ).status_code
        == 200
    )
    result, _ = _review(
        services,
        client,
        conversation,
        [
            _proposal(source, content="示例使用 PostgreSQL"),
        ],
    )
    assert result["status"] == "completed", result
    (change,) = _changes(client)
    url = f"/api/evolution/changes/{change['id']}"
    assert change["review_state"] == "pending_review"
    assert not memory.search("PostgreSQL")
    with services.sessions.begin() as session:
        old = get_or_freeze_conversation_memory(
            session,
            conversation_id=conversation,
            query="PostgreSQL",
            topic="",
        )
    assert old["selected"] == []
    response = client.post(url + "/approve")
    assert response.status_code == 200, response.text
    approved = response.json()
    assert client.post(url + "/approve").json() == approved
    assert memory.search("PostgreSQL")[0]["id"] == approved["target_id"]
    with services.sessions.begin() as session:
        reused = get_or_freeze_conversation_memory(
            session,
            conversation_id=conversation,
            query="不同的问题",
            topic="",
        )
    assert reused == old
    different_conversation = _conversation(client)
    with services.sessions.begin() as session:
        new = get_or_freeze_conversation_memory(
            session,
            conversation_id=different_conversation,
            query="PostgreSQL",
            topic="",
        )
    assert new["selected"][0]["id"] == approved["target_id"]
    refresh = client.post(f"/api/conversations/{conversation}/memory-snapshot/refresh").json()
    assert refresh["entry_count"] == 1
    assert refresh["snapshot_hash"] != old["snapshot_hash"]
    rolled_back = client.post(url + "/rollback").json()
    assert rolled_back["review_state"] == "rolled_back"
    assert client.post(url + "/rollback").json() == rolled_back
    history = memory.history(approved["target_id"])
    assert [r["status"] for r in history] == ["active", "inactive"]
    assert history[1]["revocation_epoch"] == history[0]["revocation_epoch"] + 1
    assert not memory.effective(new["selected"])


@pytest.mark.parametrize("edit_again", [False, True])
def test_update_rollback_keeps_immutable_history_and_rejects_conflict(client, services, edit_again):
    memory = MemoryService(services.sessions)
    row = memory.create("旧正文", scope="global", topic="")
    conversation = _conversation(client)
    _, source = _human_turn(services, conversation)
    result, _ = _review(
        services,
        client,
        conversation,
        [
            _proposal(
                source, action="update", target_id=row["id"], base_revision=1, content="新正文"
            ),
        ],
    )
    assert result["status"] == "completed", result
    (change,) = _changes(client)
    assert change["review_state"] == "applied"
    if edit_again:
        memory.update(row["id"], expected_revision=2, content="人工再次修改")
    response = client.post(f"/api/evolution/changes/{change['id']}/rollback")
    assert response.status_code == (409 if edit_again else 200), response.text
    assert [r["content"] for r in memory.history(row["id"])] == [
        "旧正文",
        "新正文",
        "人工再次修改" if edit_again else "旧正文",
    ]


@pytest.mark.parametrize("trigger", ["auto_threshold", "high_signal", "tool_errors"])
def test_foreground_trigger_budget_and_nonrecursive_job(client, services, trigger):
    from knowledgeops_agent.evolution.service import EvolutionService
    from knowledgeops_agent.persistence.models import EvolutionCursor

    service = EvolutionService(services)
    services.gateway.enabled = True
    client.patch(
        "/api/evolution/settings",
        json={
            "expected_revision": 1,
            "review_turn_threshold": 2,
            "daily_budget_requests": 1,
        },
    ).raise_for_status()
    conversation = _conversation(client)
    first, _ = _human_turn(services, conversation, "第一轮普通讨论")
    service.on_foreground_run_finished(first)
    service.on_foreground_run_finished(first)
    assert service.list_jobs() == []
    if trigger == "tool_errors":
        second = services.jobs.enqueue(conversation, "第二轮普通讨论", request_key=str(uuid4()))
        services.jobs.claim("tool-errors", run_id=second["id"], generation=second["generation"])
        for _ in range(2):
            services.jobs.append_event(second["id"], "tool_finished", {"status": "error"})
        second = services.jobs.finish(second["id"], "tool-errors")
    else:
        message = "第二轮普通讨论" if trigger == "auto_threshold" else "纠正一下，以后请给出示例。"
        second, _ = _human_turn(services, conversation, message)
    service.on_foreground_run_finished(second)
    (job,) = service.list_jobs()
    assert job["trigger_kind"] == ("high_signal" if trigger != "auto_threshold" else trigger)
    service.on_foreground_run_finished({**second, "kind": "evolution_review"})
    service.on_foreground_run_finished({**second, "id": "child", "parent_id": second["id"]})
    service.on_foreground_run_finished({**second, "id": "trial", "input": {"trial_case": "demo"}})
    with services.sessions() as session:
        cursor = session.get(EvolutionCursor, conversation)
        assert cursor.pending_human_turns == 2
    other = _conversation(client)
    latest, _ = _human_turn(services, other)
    service.on_foreground_run_finished(latest)
    assert len(service.list_jobs()) == 1
    assert client.get("/api/evolution/settings").json()["daily_remaining"] == 0


def test_review_cancel_recovery_and_manual_idempotency(client, services):
    from knowledgeops_agent.app.runs.worker import execute_run
    from knowledgeops_agent.persistence.models import EvolutionCursor

    conversation = _conversation(client)
    _, source = _human_turn(services, conversation)
    body = {"conversation_id": conversation, "request_key": "same"}
    assert client.post("/api/evolution/reviews", json=body).status_code == 409
    assert client.get("/api/evolution/jobs").json() == []
    services.gateway.enabled = True
    job = client.post("/api/evolution/reviews", json=body).json()
    assert client.post("/api/evolution/reviews", json=body).json() == job
    assert (
        client.post("/api/evolution/reviews", json={**body, "request_key": "other"}).status_code
        == 409
    )
    run = services.jobs.get_run(job["run_id"])
    services.jobs.claim("crashed", run_id=run["id"], generation=run["generation"])
    assert services.jobs.recover_on_startup() == 1
    assert client.get("/api/evolution/jobs").json()[0]["status"] == "interrupted"
    assert execute_run(run["id"], run["generation"], services=services) is None
    assert not services.gateway.ledger.records(root_id=run["id"])
    with services.sessions() as session:
        cursor = session.get(EvolutionCursor, conversation)
        assert cursor.last_reviewed_message_id is None and cursor.active_job_id is None
    assert client.post(f"/api/runs/{run['id']}/cancel").json()["status"] == "cancelled"

    def cancel_during_call(packet, running):
        services.jobs.cancel(running["id"])

    result, new_job = _review(
        services, client, conversation, [_proposal(source)], during_invoke=cancel_during_call
    )
    assert result["status"] == "cancelled"
    assert not _changes(client)
    assert (
        next(j for j in client.get("/api/evolution/jobs").json() if j["id"] == new_job["id"])[
            "status"
        ]
        == "cancelled"
    )


def test_two_resources_use_same_memory_bytes_until_refresh_or_withdrawal(client, services):
    from knowledgeops_agent.harness.runtime.agent import RunContext
    from knowledgeops_agent.harness.runtime.resources import prepare_resources

    memory = MemoryService(services.sessions)
    conversation = _conversation(client)
    original = memory.create("固定显示版本", scope="global", topic="", memory_slot="system")

    def manifest_for_run(chat):
        run = services.jobs.enqueue(chat, "当前任务版本说明", request_key=uuid4().hex)
        services.jobs.claim("snapshot", run_id=run["id"], generation=run["generation"])
        context = RunContext(
            run_id=run["id"],
            root_id=run["id"],
            worker_id="snapshot",
            jobs=services.jobs,
            knowledge=services.knowledge,
            sources=services.sources,
            gateway=services.gateway,
        )
        manifest = prepare_resources(context)
        services.jobs.finish(run["id"], "snapshot")
        return manifest

    first = manifest_for_run(conversation)
    foreground, source = _human_turn(services, conversation)
    from knowledgeops_agent.evolution.service import EvolutionService

    evolution = EvolutionService(services)
    evolution.on_foreground_run_finished(foreground)
    result, _ = _review(services, client, conversation, [_proposal(source, content="新增可用记忆")])
    assert result["status"] == "completed", result
    second = manifest_for_run(conversation)
    assert first["memory"]["snapshot_hash"] == second["memory"]["snapshot_hash"]
    assert json.dumps(first["preferences"]) == json.dumps(second["preferences"])
    assert first["memory"]["slots"]["system"][0]["id"] == original["id"]
    fresh = manifest_for_run(_conversation(client))
    assert fresh["memory"]["slots"]["user"][0]["content"] == "新增可用记忆"
    refreshed = client.post(f"/api/conversations/{conversation}/memory-snapshot/refresh").json()
    assert manifest_for_run(conversation)["memory"]["snapshot_hash"] == refreshed["snapshot_hash"]
    memory.update(original["id"], expected_revision=1, status="inactive")
    assert not memory.effective(second["preferences"])


def test_only_processed_turns_are_subtracted_from_cursor(client, services):
    from knowledgeops_agent.evolution.service import EvolutionService
    from knowledgeops_agent.persistence.models import EvolutionCursor

    service = EvolutionService(services)
    conversation = _conversation(client)
    first, _ = _human_turn(services, conversation, "第一轮普通讨论")
    second, source = _human_turn(services, conversation, "第二轮普通讨论")
    service.on_foreground_run_finished(first)

    def late_finish(packet, running):
        # 复盘已启动，第二轮前台完成通知才到达。
        service.on_foreground_run_finished(second)

    result, _ = _review(
        services, client, conversation, [_proposal(source)], during_invoke=late_finish
    )
    assert result["status"] == "completed", result
    with services.sessions() as session:
        cursor = session.get(EvolutionCursor, conversation)
        assert cursor.pending_human_turns == 0
        assert cursor.last_reviewed_message_id == source
        assert cursor.last_reviewed_run_id == second["id"]
        assert cursor.active_job_id is None


def test_approval_for_deprecation_and_source_conversation_fence(client, services):
    conversation = _conversation(client)
    _, source = _human_turn(services, conversation)
    other = _conversation(client)
    _, outside = _human_turn(services, other)
    memory = MemoryService(services.sessions)
    original = memory.create("仍然有效", scope="global", topic="")
    result, _ = _review(
        services,
        client,
        conversation,
        [
            _proposal(source, action="deprecate", target_id=original["id"], base_revision=1),
            _proposal(outside, content="其他会话的内容"),
        ],
    )
    assert result["status"] == "completed", result
    changes = {r["review_state"]: r for r in _changes(client)}
    assert changes["rejected"]["diff_summary"]["reason_code"] == "invalid_source_evidence"
    change = changes["pending_review"]
    assert change["diff_summary"]["reason_code"] == "deprecation_requires_approval"
    assert memory.get(original["id"])["status"] == "active"
    url = f"/api/evolution/changes/{change['id']}"
    assert client.post(url + "/approve").status_code == 200
    assert memory.get(original["id"])["status"] == "inactive"
    assert client.post(url + "/rollback").status_code == 200
    assert memory.get(original["id"])["status"] == "active"


def test_real_foreground_worker_enqueues_high_signal_review(client, services):
    from langchain_core.messages import AIMessageChunk
    from langgraph.checkpoint.memory import InMemorySaver

    from knowledgeops_agent.app.runs.worker import execute_run

    class Provider:
        def bind_tools(self, tools):
            return self

        def invoke(self, messages, **kwargs):
            current = json.loads(messages[-1].text)
            return AIMessage(
                json.dumps(
                    {
                        "action": "none",
                        "clear": False,
                        "message_id": current["message"]["id"],
                        "root_id": current["root_id"],
                        "quote": "",
                        "changeset_id": None,
                        "expected_revision": None,
                        "confirm_impact": False,
                        "ui_conflict": False,
                        "continue_task": True,
                    }
                )
            )

        def stream(self, messages, **kwargs):
            yield AIMessageChunk(content="收到，示例会优先给出。")

    services.gateway.enabled = True
    services.gateway.client = Provider()
    conversation = _conversation(client)
    run = services.jobs.enqueue(
        conversation, "纠正一下，以后记住先给示例。", request_key=uuid4().hex
    )
    result = execute_run(
        run["id"], run["generation"], services=services, checkpointer=InMemorySaver()
    )
    assert result["status"] == "completed", result
    (job,) = client.get("/api/evolution/jobs").json()
    assert job["status"] == "pending" and job["trigger_kind"] == "high_signal"
    review = services.jobs.get_run(job["run_id"])
    assert review["origin"] == "system"
    assert review["budgets"] == {
        "model_requests": 3,
        "tool_calls": 6,
        "searches": 0,
        "active_seconds": 180,
    }
    assert services.gateway.ledger.records(root_id=review["id"]) == []
    assert MemoryService(services.sessions).list() == []


@pytest.mark.parametrize("activate_pending", [False, True], ids=["new", "pinned-pending"])
def test_review_fix_create_preserves_message_provenance_and_pin(client, services, activate_pending):
    from knowledgeops_agent.persistence.models import ChatMessage

    memory = MemoryService(services.sessions)
    conversation = _conversation(client)
    quote = "以后记住先给示例"
    run, source = _human_turn(services, conversation, quote)
    content = "先给示例"
    prior = None
    if activate_pending:
        prior = memory.create(content, scope="global", topic="", protection_level="pinned")
        memory.update(prior["id"], expected_revision=1, status="pending")
    with services.sessions.begin() as session:
        row = memory.create(
            content,
            scope="global",
            topic="",
            session=session,
            message=session.get(ChatMessage, source),
            quote=quote,
        )
    assert row["status"] == "active"
    if activate_pending:
        assert row["id"] == prior["id"]
        assert row["protection_level"] == "pinned"
        assert row["source"]["last_editor"] == "evolution_review"
    assert row["source_message_id"] == source
    assert row["source"]["conversation_id"] == conversation
    assert row["source"]["run_id"] == run["id"]
    assert row["source"]["quote"] == quote
    assert memory.history(row["id"])[-1]["source"] == row["source"]
    (evidence,) = [e for e in memory.get(row["id"])["evidence"] if e["message_id"] == source]
    assert evidence["kind"] == "evolution_review"
    assert evidence["quote"] == quote
    assert evidence["context_key"] == "conversation:" + conversation


def test_review_fix_deduplicates_pending_creates_without_blocking_approval(client, services):
    conversation = _conversation(client)
    client.patch(
        "/api/evolution/settings",
        json={"expected_revision": 1, "memory_auto_write": False},
    ).raise_for_status()
    _, source = _human_turn(services, conversation)
    first, _ = _review(
        services,
        client,
        conversation,
        [
            _proposal(source, content="首选 Python 示例"),
            _proposal(source, content="首选  ＰＹＴＨＯＮ 示例"),
        ],
    )
    assert first["status"] == "completed", first
    assert first["outcome"]["result_summary"]["pending_review"] == 1
    assert first["outcome"]["result_summary"]["skipped_duplicate"] == 1

    _, source = _human_turn(services, conversation)
    second, _ = _review(
        services,
        client,
        conversation,
        [
            _proposal(source, content="首选 python 示例"),
            _proposal(source, content="首选 python 示例", memory_slot="system"),
        ],
    )
    assert second["status"] == "completed", second
    assert second["outcome"]["result_summary"]["pending_review"] == 1
    assert second["outcome"]["result_summary"]["skipped_duplicate"] == 1
    (user_change,) = [
        row for row in _changes(client) if row["diff_summary"]["after"]["memory_slot"] == "user"
    ]
    approved = client.post(f"/api/evolution/changes/{user_change['id']}/approve")
    assert approved.status_code == 200, approved.text
    assert approved.json()["review_state"] == "approved"


def test_review_fix_freeze_keeps_protected_and_correction_without_lexical_match(business_sessions):
    from knowledgeops_agent.evolution.settings import update_settings

    update_settings(business_sessions, 1, {"user_slot_char_limit": 5000})
    memory = MemoryService(business_sessions)
    protected = {
        memory.create(
            "示例统一采用固定列名",
            scope="global",
            topic="",
            category="goal",
            protection_level="pinned",
        )["id"],
        memory.create(
            "习惯用中文注释",
            scope="global",
            topic="",
            category="background",
            protection_level="locked",
        )["id"],
        memory.create("输出字段应为绝对路径", scope="global", topic="", category="correction")[
            "id"
        ],
    }
    for i in range(33):
        memory.create(f"普通表达习惯 {i}", scope="global", topic="")
    assert memory.search("volcanology") == []
    selected = memory.freeze("volcanology", token_budget=425, correction_budget=175)["selected"]
    assert {e["id"] for e in selected[:3]} == protected


@pytest.mark.parametrize("schema_error", [False, True], ids=["value-error", "schema-error"])
def test_review_fix_worker_explains_invalid_reply_without_agent_outcome(
    client, services, monkeypatch, schema_error
):
    from knowledgeops_agent.app.runs import worker
    from knowledgeops_agent.persistence.models import EvolutionCursor

    def unexpected_outcome(*args, **kwargs):
        pytest.fail("复盘校验失败不应构建通用 Agent 结果")

    conversation = _conversation(client)
    _, source = _human_turn(services, conversation)
    monkeypatch.setattr(worker, "_outcome", unexpected_outcome)
    if not schema_error:
        from knowledgeops_agent.evolution import review

        def invalid_output(reply):
            raise ValueError("复盘模型响应缺少提案")

        monkeypatch.setattr(review, "model_output_error", invalid_output)
    run, job = _review(
        services,
        client,
        conversation,
        [_proposal(source, category="raw-provider-payload" if schema_error else "preference")],
    )
    assert run["status"] == "failed"
    assert run["outcome"] == {"kind": "evolution_review_failed"}
    assert "复盘" in run["stop_reason"]
    assert ("校验" if schema_error else "响应缺少提案") in run["stop_reason"]
    assert "raw-provider-payload" not in run["stop_reason"]
    (stored,) = client.get("/api/evolution/jobs").json()
    assert stored["id"] == job["id"] and stored["status"] == "failed"
    assert stored["error_message"] == run["stop_reason"]
    with services.sessions() as session:
        cursor = session.get(EvolutionCursor, conversation)
        assert cursor.active_job_id is None and cursor.last_reviewed_message_id is None


def test_review_fix_history_rollback_restores_memory_slot(client):
    created = client.post("/api/memories", json={"content": "环境说明", "memory_slot": "system"})
    assert created.status_code == 201, created.text
    url = "/api/memories/" + created.json()["id"]
    moved = client.patch(url, json={"expected_revision": 1, "memory_slot": "user"})
    assert moved.status_code == 200 and moved.json()["memory_slot"] == "user"
    rolled_back = client.post(
        url + "/rollback", json={"expected_revision": 2, "target_revision": 1}
    )
    assert rolled_back.status_code == 200, rolled_back.text
    assert rolled_back.json()["memory_slot"] == "system"
    assert [row["memory_slot"] for row in client.get(url + "/history").json()] == [
        "system",
        "user",
        "system",
    ]
