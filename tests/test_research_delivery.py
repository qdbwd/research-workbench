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


@pytest.mark.parametrize(("action", "delivery_status", "task_status"), [
    ("accept", "accepted", "completed"),
    ("request_changes", "changes_requested", "open"),
])
def test_review_fixed_delivery_and_stop_autonomy(client, services, action, delivery_status, task_status):
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
    finished = execute_run(root_id, 1, services=services, checkpointer=InMemorySaver())
    assert finished["status"] == "completed", finished
    assert finished["outcome"]["kind"] == "research_delivery"
    assert len(provider.requests) == 1
    messages = client.get(f"/api/conversations/{chat}/messages").json()
    assert sum("待验收" in row["content"] for row in messages if row["role"] == "assistant") == 1
    [delivery] = client.get(base + "/deliveries").json()["items"]
    current = client.get(base).json()
    assert delivery["status"] == "submitted"
    assert delivery["source_revision_id"] == current["head"]["id"]
    assert current["status"] == "awaiting_acceptance"
    permit = client.get(base + "/autonomy").json()
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
    assert services.jobs.claim("no-new-research") is None
