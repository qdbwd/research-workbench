"""在隔离业务库验证自主授权及真正的研发 Run。"""

import json
import os
import time
from uuid import uuid4

import pytest
from celery.contrib.testing.worker import start_worker
from langchain_core.messages import AIMessageChunk, HumanMessage, ToolMessage
from test_research_execution import BriefReader, create_task, submit
from test_research_execution import client as research_client
from test_research_execution import services as research_services

from knowledgeops_agent.app.runs.queue import TASK_NAME, OutboxPublisher, create_queue
from knowledgeops_agent.app.runs.worker import execute_run, open_checkpointer
from knowledgeops_agent.harness.models.gateway import ModelBudgetExceeded
from knowledgeops_agent.harness.tools.policy import ToolPolicyService
from knowledgeops_agent.learning.budget import reserve_model

client = research_client
services = research_services


def test_pending_human_message_is_claimed_before_autonomous_root(client, services):
    chat, task = create_task(client)
    automatic = authorize(client, task).json()["permit"]["run_id"]
    human = submit(client, chat, "human-priority")
    assert services.jobs.claim("automatic-too-early", run_id=automatic, generation=1) is None
    selected = services.jobs.claim("human-first", run_id=human["id"], generation=human["generation"])
    assert selected["id"] == human["id"]


def authorize(client, task, *, model_requests=3, operation_id="allow"):
    return client.post(f"/api/research-tasks/{task['id']}/autonomy", json={
        "operation_id": operation_id,
        "expected_control_version": task["control_version"],
        "expected_revision_id": task["head"]["id"],
        "action": "grant",
        "budgets": {
            "model_requests": model_requests, "tool_calls": 5, "searches": 0, "active_seconds": 120,
        },
    })


def test_autonomous_runs_share_permission_and_stop_at_model_limit(client, services):
    chat, task = create_task(client)
    base = f"/api/research-tasks/{task['id']}"
    policy = ToolPolicyService(services.sessions)
    policy.update(expected_revision=policy.get()["revision"], enabled={"builtin:continue_research": True})

    class ContinuationProvider(BriefReader):
        def stream(self, messages, **kwargs):
            self.requests.append(messages)
            boundary = max(
                index for index, message in enumerate(messages)
                if isinstance(message, HumanMessage) and message.additional_kwargs.get("knowledgeops_origin") == "automatic"
            )
            if any(isinstance(message, ToolMessage) for message in messages[boundary + 1:]):
                yield AIMessageChunk("已核对本次资料，已记录下一步。")
                return
            yield AIMessageChunk("", tool_call_chunks=[{
                "name": "continue_research", "id": "next", "index": 0,
                "args": json.dumps({"next_action": "比对候选片段与最终上下文"}),
            }])

    provider = ContinuationProvider()
    services.gateway.enabled, services.gateway.client = True, provider
    permitted = authorize(client, task)
    assert permitted.status_code == 200, permitted.text
    assert authorize(client, task).json() == permitted.json()
    permit_id = permitted.json()["permit"]["id"]
    first_id = permitted.json()["permit"]["run_id"]
    broker = os.environ["TEST_REDIS_URL"]
    assert broker.endswith("/15") and services.engine.url.database == "knowledgeops_test"

    def consume(run_id, generation):
        return execute_run(run_id, generation, services=services)

    queue = create_queue(broker, handler=consume, queue_name="research-" + uuid4().hex)
    assert TASK_NAME in queue.tasks
    publisher = OutboxPublisher(services.sessions, queue)

    def wait_for(run_id, status):
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            run = services.jobs.get_run(run_id)
            if run["status"] == status:
                return run
            assert run["status"] in {"queued", "running"}, run
            time.sleep(0.05)
        raise AssertionError(f"Expected {status} after Redis delivery: {run}")

    try:
        with start_worker(queue, pool="solo", concurrency=1, perform_ping_check=False, shutdown_timeout=10):
            assert publisher.publish_due() == 1
            first = wait_for(first_id, "completed")
            continued = client.get(base + "/autonomy").json()
            assert continued["id"] == permit_id
            assert continued["usage"]["model_requests"] == 2
            second_id = continued["run_id"]
            assert second_id != first_id
            assert publisher.publish_due() == 1
            wait_for(second_id, "budget_exhausted")
            with open_checkpointer(services) as saver:
                assert saver.get_tuple({"configurable": {"thread_id": first["checkpoint_id"]}}) is not None
    finally:
        queue.close()
    exhausted = client.get(base + "/autonomy").json()
    assert exhausted["status"] == "exhausted"
    assert exhausted["usage"]["model_requests"] == 3
    assert exhausted["run_id"] == second_id
    assert len(provider.requests) == 3
    assert sum(row["role"] == "user" for row in client.get(f"/api/conversations/{chat}/messages").json()) == 1


@pytest.mark.parametrize("exhausted", [False, True])
def test_recovery_requires_explicit_resume_and_preserves_permission(client, services, exhausted):
    _, task = create_task(client)
    base = f"/api/research-tasks/{task['id']}"
    granted = authorize(client, task, model_requests=1 if exhausted else 3)
    assert granted.status_code == 200, granted.text
    permit = granted.json()["permit"]
    if exhausted:
        assert services.jobs.claim("before-restart", run_id=permit["run_id"], generation=1)
        reserve_model(services.jobs, "spent-before-restart", permit["run_id"], permit["run_id"])
    assert services.jobs.recover_on_startup() == 1
    assert services.jobs.claim("unapproved", run_id=permit["run_id"], generation=1) is None
    recovering = client.get(base + "/autonomy").json()
    current = client.get(base).json()
    resumed = client.post(base + "/autonomy", json={
        "operation_id": "resume", "expected_control_version": current["control_version"],
        "expected_revision_id": current["head"]["id"], "action": "resume",
    })
    if exhausted:
        assert recovering["status"] == "exhausted" and recovering["usage"]["model_requests"] == 1
        assert resumed.status_code == 409 and resumed.json()["code"] == "permit_unavailable"
        assert services.jobs.get_run(permit["run_id"])["status"] == "interrupted"
        assert authorize(client, current, operation_id="new-permission").status_code == 200
        return
    assert recovering["status"] == "recovery_required"
    assert resumed.status_code == 200, resumed.text
    assert resumed.json()["permit"]["id"] == permit["id"]
    assert resumed.json()["permit"]["usage"] == permit["usage"]
    recovered = services.jobs.claim("approved", run_id=permit["run_id"], generation=2)
    assert recovered["status"] == "running"


def test_child_and_retried_model_receipt_share_research_permit(client, services):
    _, task = create_task(client)
    permit = authorize(client, task, model_requests=2).json()["permit"]
    root_id = permit["run_id"]
    jobs = services.jobs
    assert jobs.claim("research-parent", run_id=root_id, generation=1)
    reserve_model(jobs, "parent-receipt", root_id, root_id)
    child = jobs.start_child(root_id, "独立核对原文", operation_key="source-check")
    assert jobs.claim("research-child", run_id=child["id"], generation=child["generation"])
    reserve_model(jobs, "child-receipt", root_id, child["id"])
    reserve_model(jobs, "child-receipt", root_id, child["id"])
    with pytest.raises(ModelBudgetExceeded, match="research model request budget exhausted"):
        reserve_model(jobs, "new-child-receipt", root_id, child["id"])
    current = client.get(f"/api/research-tasks/{task['id']}/autonomy").json()
    assert current["id"] == permit["id"] and current["usage"]["model_requests"] == 2


def test_paused_exhausted_autonomy_can_continue_in_manual_research(client, services):
    chat, task = create_task(client)
    base = f"/api/research-tasks/{task['id']}"
    granted = authorize(client, task, model_requests=1).json()
    old_id = granted["permit"]["run_id"]
    assert services.jobs.claim("last-model", run_id=old_id, generation=1)
    reserve_model(services.jobs, "last-model", old_id, old_id)
    stopped = client.post(base + "/control", json={
        "action": "pause", "operation_id": "pause-at-limit",
        "expected_control_version": granted["task"]["control_version"],
    })
    assert stopped.status_code == 200, stopped.text
    assert services.jobs.settle_pause(old_id, "last-model", outcome={})["status"] == "paused"

    resumed = client.post(base + "/control", json={
        "action": "resume", "operation_id": "continue-manually",
        "expected_control_version": stopped.json()["control_version"],
    })
    assert resumed.status_code == 200, resumed.text
    assert resumed.json()["status"] == "open"
    exhausted = client.get(base + "/autonomy").json()
    assert exhausted["status"] == "exhausted" and exhausted["usage"]["model_requests"] == 1
    assert services.jobs.get_run(old_id)["status"] == "cancelled"
    assert submit(client, chat, "manual-without-permit")["input"]["research"].get("permit_id") is None
