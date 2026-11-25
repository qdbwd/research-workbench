"""成果从实际模型工具递交，程序完成收尾，最终验收由请求方处理。"""

import json

import pytest
from langchain_core.messages import AIMessageChunk
from langgraph.checkpoint.memory import InMemorySaver
from test_research_autonomy import authorize
from test_research_execution import BriefReader, create_task
from test_research_execution import client as research_client
from test_research_execution import services as research_services

from knowledgeops_agent.app.runs.worker import execute_run
from knowledgeops_agent.harness.tools.policy import ToolPolicyService

client = research_client
services = research_services


@pytest.mark.parametrize(("action", "delivery_status", "task_status", "switch_branch"), [
    ("accept", "accepted", "completed", False),
    ("request_changes", "changes_requested", "open", False),
    ("accept", "accepted", "open", True),
])
def test_review_fixed_delivery_and_stop_autonomy(client, services, action, delivery_status, task_status, switch_branch):
    chat, task = create_task(client)
    base = f"/api/research-tasks/{task['id']}"
    policy = ToolPolicyService(services.sessions)
    policy.update(expected_revision=policy.get()["revision"], enabled={"builtin:submit_research_delivery": True})

    class DeliveryProvider(BriefReader):
        def stream(self, messages, **kwargs):
            self.requests.append(messages)
            assert len(self.requests) == 1, "交付完成后的提示由程序生成，无需新的模型响应"
            yield AIMessageChunk("", tool_call_chunks=[{
                "name": "submit_research_delivery", "id": "deliver", "index": 0,
                "args": json.dumps({
                    "title": "内部问答诊断",
                    "diagnosis": "先核实正确候选进入最终上下文的比例。",
                    "recommendations": "固定问题集，逐步追踪检索、截断和生成环节。",
                    "limitations": "当前没有标注样本，先验证假设。",
                    "source_version_ids": [],
                }, ensure_ascii=False),
            }])

    provider = DeliveryProvider()
    services.gateway.enabled, services.gateway.client = True, provider
    grant = authorize(client, task)
    assert grant.status_code == 200, grant.text
    root_id = grant.json()["permit"]["run_id"]
    if switch_branch:
        main = f"{base}/branches/{task['main_branch_id']}"
        preview = client.get(main + "/commit-preview").json()
        commit = client.post(main + "/commits", json={
            "operation_id": "commit", "message": "固定对照起点", "preview_hash": preview["preview_hash"],
        })
        assert commit.status_code == 201, commit.text
        fork = client.post(base + "/branches", json={
            "operation_id": "fork", "name": "新方向", "source_commit_id": commit.json()["id"],
        })
        assert fork.status_code == 201, fork.text
        selected = client.post(base + "/write-branch", json={
            "operation_id": "select", "branch_id": fork.json()["id"], "expected_write_version": 1,
        })
        assert selected.status_code == 200, selected.text
    finished = execute_run(root_id, 1, services=services, checkpointer=InMemorySaver())
    assert finished["status"] == "completed", finished
    assert finished["outcome"]["kind"] == "research_delivery"
    assert len(provider.requests) == 1
    messages = client.get(f"/api/conversations/{chat}/messages").json()
    assert sum("待验收" in row["content"] for row in messages if row["role"] == "assistant") == 1
    [delivery] = client.get(base + "/deliveries").json()["items"]
    current = client.get(base).json()
    assert delivery["status"] == "submitted"
    if switch_branch:
        assert current["status"] == "open"
        assert current["write_branch_id"] == fork.json()["id"]
        assert current["head"]["id"] == fork.json()["head"]["id"]
        assert delivery["branch_id"] == task["main_branch_id"]
    else:
        assert delivery["source_revision_id"] == current["head"]["id"]
        assert current["status"] == "awaiting_acceptance"
    permit = client.get(base + "/autonomy").json()
    if switch_branch:
        assert permit is None
    else:
        assert permit["status"] == "revoked"
        assert permit["run_id"] == root_id

    body = {
        "operation_id": "review",
        "expected_control_version": current["control_version"],
        "source_revision_id": delivery["source_revision_id"],
        "action": action,
        "reason": "复核固定成果和适用范围",
    }
    route = base + f"/deliveries/{delivery['id']}/review"
    invalid = client.post(route, json={**body, "source_revision_id": "other"})
    assert invalid.status_code == 409, invalid.text
    reviewed = client.post(route, json=body)
    assert reviewed.status_code == 200, reviewed.text
    assert client.post(route, json=body).json() == reviewed.json()
    assert reviewed.json()["task"]["status"] == task_status
    assert reviewed.json()["delivery"]["status"] == delivery_status
    assert reviewed.json()["delivery"]["content"] == delivery["content"]
    assert client.get(base + "/autonomy").json() == permit
    jobs = client.get(f"/api/evolution/jobs?conversation_id={chat}").json()
    if action == "accept":
        (review_job,) = jobs
        assert review_job["job_type"] == "skill_review"
        assert review_job["trigger_kind"] == "high_signal"
        assert services.jobs.get_run(review_job["run_id"])["input"]["research_delivery_id"] == delivery["id"]
        services.jobs.cancel(review_job["run_id"])
    else:
        assert jobs == []
    assert services.jobs.claim("no-new-research") is None

    if task_status == "completed":
        main = f"{base}/branches/{task['main_branch_id']}"
        preview = client.get(main + "/commit-preview").json()
        fixed = client.post(main + "/commits", json={
            "operation_id": "fixed-accepted", "message": "固定已验收成果", "preview_hash": preview["preview_hash"],
        })
        assert fixed.status_code == 201, fixed.text
        branch = client.post(base + "/branches", json={
            "operation_id": "fork-accepted", "source_commit_id": fixed.json()["id"], "name": "试点开放条件复核",
        })
        assert branch.status_code == 201, branch.text
        branch_id = branch.json()["id"]
        restart = f"{base}/branches/{branch_id}/restart"
        expected = reviewed.json()["task"]["control_version"]
        stale = client.post(restart, json={"operation_id": "stale", "expected_control_version": expected - 1})
        assert stale.status_code == 409 and stale.json()["code"] == "control_conflict"
        operation = {"operation_id": "restart", "expected_control_version": expected}
        started = client.post(restart, json=operation)
        assert started.status_code == 200, started.text
        assert started.json()["id"] == task["id"]
        assert started.json()["status"] == "open"
        assert started.json()["write_branch_id"] == branch_id
        assert started.json()["head"]["state"] == fixed.json()["snapshot"]["revision"]["state"]
        assert client.post(restart, json=operation).json() == started.json()
        assert client.get(main + "/commits/" + fixed.json()["id"]).json() == fixed.json()
        assert client.get(base + "/autonomy").json() is None
        with services.sessions() as session:
            from knowledgeops_agent.persistence.models import ResearchPermit

            assert session.get(ResearchPermit, permit["id"]).status == "revoked"
        assert services.jobs.claim("no-implicit-restart") is None
        sent = client.post(f"/api/conversations/{chat}/messages", json={
            "request_key": "fresh", "content": "从固定证据继续研究。",
        })
        assert sent.status_code == 202, sent.text
        assert sent.json()["input"]["research"]["branch_id"] == branch_id
        assert not sent.json()["input"]["research"].get("permit_id")
