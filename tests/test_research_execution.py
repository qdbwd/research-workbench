"""研发会话进入真实 Run/Agent 循环，模型供应商使用本地确定性替身。"""

import json

import pytest
from fixtures.execution_provider import ExecutionProvider
from langchain_core.messages import AIMessageChunk
from langgraph.checkpoint.memory import InMemorySaver
from test_api_worker import client as api_client
from test_api_worker import services as api_services

from knowledgeops_agent.app.runs.worker import execute_run
from knowledgeops_agent.harness.tools.policy import ToolPolicyService

client = api_client
services = api_services


class BriefReader(ExecutionProvider):
    def __init__(self):
        self.requests = []

    def stream(self, messages, **kwargs):
        self.requests.append(messages)
        yield AIMessageChunk("先核对候选片段，再比较最终上下文和回答。")


def create_task(client):
    chat = client.post("/api/conversations", json={"title": "诊断验收"}).json()["id"]
    response = client.post(f"/api/conversations/{chat}/research-task", json={
        "operation_id": "start", "goal": "检查内部问答的证据在哪一步丢失",
    })
    assert response.status_code == 201, response.text
    return chat, response.json()


def submit(client, chat, key="inspect"):
    response = client.post(f"/api/conversations/{chat}/messages", json={
        "content": "请根据简报开始调查", "request_key": key,
    })
    assert response.status_code == 202, response.text
    return response.json()


@pytest.mark.parametrize("invalidate", [None, "before", "during"])
def test_research_message_binds_real_model_request_and_rejects_stale_goal(client, services, invalidate):
    chat, task = create_task(client)
    run = submit(client, chat)
    provider = BriefReader()
    services.gateway.enabled, services.gateway.client = True, provider

    def change_goal():
        active = client.get(f"/api/research-tasks/{task['id']}").json()
        changed = client.post(
            f"/api/research-tasks/{task['id']}/branches/{task['main_branch_id']}/revisions",
            json={
                "operation_id": "change-goal",
                "expected_revision_id": active["head"]["id"],
                "expected_control_version": active["control_version"],
                "changes": {"brief": {"goal": "改用另一批文档"}},
                "reason": "用户修改研究目标",
            },
        )
        assert changed.status_code == 201, changed.text

    if invalidate == "before":
        change_goal()
    if invalidate == "during":
        policy = ToolPolicyService(services.sessions)
        policy.update(expected_revision=policy.get()["revision"], enabled={"builtin:set_research_focus": True})

        def late(messages, **kwargs):
            provider.requests.append(messages)
            change_goal()
            yield AIMessageChunk("", tool_call_chunks=[{
                "name": "set_research_focus", "id": "proposal-from-obsolete-goal", "index": 0,
                "args": json.dumps({
                    "phase": "investigation", "capabilities": ["technical_research"],
                    "reason": "根据旧目标转入技术调研",
                }),
            }])

        provider.stream = late

    result = execute_run(run["id"], run["generation"], services=services, checkpointer=InMemorySaver())
    if invalidate:
        assert result["status"] == "interrupted", result
        assert len(provider.requests) == int(invalidate == "during")
        history = client.get(f"/api/conversations/{chat}/messages").json()
        assert all(row["role"] == "user" for row in history)
        latest = client.get(f"/api/research-tasks/{task['id']}").json()["head"]["state"]
        assert latest["phase"] == "exploration" and latest["brief"]["goal"] == "改用另一批文档"
        assert not any(event["kind"] == "tool_started" for event in services.jobs.events(run["id"]))
    else:
        assert result["status"] == "completed", result
        [messages] = provider.requests
        [background] = [
            json.loads(text.split("\n", 1)[1])["research"]
            for message in messages
            if (text := str(message.content)).startswith("应用组织的任务背景")
        ]
        assert background["task_id"] == task["id"]
        assert background["brief"]["goal"] == "检查内部问答的证据在哪一步丢失"
        assert background["phase"] == "exploration"
        assert list(background["methods"]) == ["common", "exploration"]
        assert all(background["methods"].values())
        assert submit(client, chat)["id"] == run["id"]


def test_user_can_pin_and_clear_research_focus(client, services):
    chat, task = create_task(client)
    active = f"/api/research-tasks/{task['id']}"
    body = {
        "operation_id": "pin", "expected_control_version": 1,
        "expected_revision_id": task["head"]["id"], "phase": "investigation",
        "capabilities": ["product_design", "technical_research"], "pinned": True,
        "reason": "同时需要产品设计与技术核对",
    }
    response = client.post(active + "/focus", json=body)
    assert response.status_code == 200, response.text
    pinned = response.json()
    assert pinned["head"]["state"]["capability_override"] == body["capabilities"]
    assert client.post(active + "/focus", json=body).json() == pinned
    assert client.post(active + "/focus", json={**body, "operation_id": "stale"}).status_code == 409

    provider = BriefReader()
    services.gateway.enabled, services.gateway.client = True, provider
    run = submit(client, chat, "research-product-design")
    completed = execute_run(run["id"], run["generation"], services=services, checkpointer=InMemorySaver())
    assert completed["status"] == "completed", completed
    [messages] = provider.requests
    [background] = [
        json.loads(text.split("\n", 1)[1])["research"]
        for message in messages
        if (text := str(message.content)).startswith("应用组织的任务背景")
    ]
    assert list(background["methods"]) == ["common", "product_design", "technical_research"]
    frozen = client.get(active).json()
    cleared = client.post(active + "/focus", json={
        **body, "operation_id": "clear", "pinned": False,
        "expected_revision_id": frozen["head"]["id"],
        "expected_control_version": frozen["control_version"],
    }).json()
    assert cleared["head"]["state"]["capability_override"] is None
    assert cleared["head"]["state"]["method_bundle_ref"] == background["method_bundle_ref"]


def test_assistant_changes_focus_in_run_only_as_a_separate_decision(client, services):
    from langchain_core.messages import ToolMessage

    chat, task = create_task(client)
    policy = ToolPolicyService(services.sessions)
    policy.update(expected_revision=policy.get()["revision"], enabled={"builtin:set_research_focus": True})

    class FocusProvider(BriefReader):
        def stream(self, messages, **kwargs):
            self.requests.append(messages)
            calls = [
                message for message in messages
                if isinstance(message, ToolMessage) and message.name == "set_research_focus"
            ]
            args = json.dumps({
                "phase": "investigation", "capabilities": ["technical_research"], "reason": "目标已明确",
            })
            if not calls:
                yield AIMessageChunk("", tool_call_chunks=[
                    {"name": "set_research_focus", "args": args, "id": "mixed-focus", "index": 0},
                    {"name": "list_knowledge", "args": "{}", "id": "mixed-read", "index": 1},
                ])
            elif len(calls) == 1:
                yield AIMessageChunk("", tool_call_chunks=[
                    {"name": "set_research_focus", "args": args, "id": "focus-alone", "index": 0},
                ])
            else:
                yield AIMessageChunk("研究焦点已调整，接下来检查检索流水线。")

    provider = FocusProvider()
    services.gateway.enabled, services.gateway.client = True, provider
    run = submit(client, chat, "research-method-selection")
    finished = execute_run(run["id"], run["generation"], services=services, checkpointer=InMemorySaver())
    assert finished["status"] == "completed", finished
    assert len(provider.requests) == 3
    assert not any(
        event["kind"] == "tool_started" and event["data"]["name"] == "list_knowledge"
        for event in services.jobs.events(run["id"])
    )
    current = client.get(f"/api/research-tasks/{task['id']}").json()
    assert current["head"]["state"]["active_capabilities"] == ["technical_research"]
    assert current["head"]["state"]["phase"] == "investigation"
    text = next(str(message.content) for message in provider.requests[-1]
                if str(message.content).startswith("应用组织的任务背景"))
    assert list(json.loads(text.split("\n", 1)[1])["research"]["methods"]) == ["common", "technical_research"]


def test_research_controls_pause_resume_and_cancel_the_same_run(client, services):
    chat, task = create_task(client)
    run = submit(client, chat)
    endpoint = f"/api/research-tasks/{task['id']}/control"
    paused = {"action": "pause", "operation_id": "pause", "expected_control_version": 1}
    assert client.post(endpoint, json=paused).json()["status"] == "paused"
    scheduled = services.jobs.get_run(run["id"])
    assert scheduled["status"] == "paused"
    assert scheduled["checkpoint_id"] == run["checkpoint_id"]
    assert execute_run(run["id"], run["generation"], services=services) is None
    assert client.post(endpoint, json=paused).json()["status"] == "paused"

    resumed = {"action": "resume", "operation_id": "resume", "expected_control_version": 2}
    assert client.post(endpoint, json=resumed).json()["status"] == "open"
    same = services.jobs.get_run(run["id"])
    assert same["generation"] == scheduled["generation"] + 1
    assert same["checkpoint_id"] == scheduled["checkpoint_id"]
    assert same["budgets"] == scheduled["budgets"]
    assert same["input"]["research"]["control_version"] == 3

    cancelled = {"action": "cancel", "operation_id": "cancel", "expected_control_version": 3}
    assert client.post(endpoint, json=cancelled).json()["status"] == "closed"
    assert services.jobs.get_run(run["id"])["status"] == "cancelled"
    assert execute_run(run["id"], same["generation"], services=services) is None


def test_product_design_method_is_preserved_after_compaction_and_resume(client, services):
    from test_manual_compaction import SummaryProvider

    chat = client.post("/api/conversations", json={"title": "产品设计探索"}).json()["id"]
    started = client.post(f"/api/conversations/{chat}/research-task", json={
        "operation_id": "product-start", "goal": "明确企业知识助手的文档交接问题与首版验收目标",
    }).json()
    base = f"/api/research-tasks/{started['id']}"
    checkpointer = InMemorySaver()
    explorer = BriefReader()
    services.gateway.enabled, services.gateway.client = True, explorer
    for number in range(4):
        response = client.post(f"/api/conversations/{chat}/messages", json={
            "content": f"第 {number} 次探索。有产品经理、维护者和使用答复的员工，需要明确决策与原始材料。",
            "request_key": f"product-explore-{number}",
        })
        assert response.status_code == 202, response.text
        run = response.json()
        assert execute_run(run["id"], run["generation"], services=services, checkpointer=checkpointer)["status"] == "completed"
    latest = client.get(base).json()
    focus = client.post(base + "/focus", json={
        "operation_id": "product-focus", "expected_control_version": latest["control_version"],
        "expected_revision_id": latest["head"]["id"], "phase": "investigation",
        "capabilities": ["product_design"], "pinned": True,
        "reason": "用户要求先收敛验收流程及不同使用者的责任",
    })
    assert focus.status_code == 200, focus.text
    services.gateway.client = SummaryProvider()
    compact = client.post(f"/api/conversations/{chat}/context-compaction", json={"request_key": "product-compact"})
    assert compact.status_code == 202, compact.text
    ran = compact.json()
    compacted = execute_run(ran["id"], ran["generation"], services=services)
    assert compacted["outcome"]["kind"] == "context_compacted", compacted

    following = submit(client, chat, "product-after-compaction")
    for version, action in enumerate(("pause", "resume"), start=focus.json()["control_version"]):
        response = client.post(base + "/control", json={
            "operation_id": action, "action": action, "expected_control_version": version,
        })
        assert response.status_code == 200, response.text
    resumed = services.jobs.get_run(following["id"])
    assert resumed["checkpoint_id"] == following["checkpoint_id"]
    recorder = BriefReader()
    services.gateway.client = recorder
    finished = execute_run(resumed["id"], resumed["generation"], services=services, checkpointer=checkpointer)
    assert finished["status"] == "completed", finished
    [messages] = recorder.requests
    observed = next(json.loads(item.content.split("\n", 1)[1])["research"] for item in messages
                    if isinstance(item.content, str) and item.content.startswith("应用组织的任务背景"))
    assert observed["brief"]["goal"] == "明确企业知识助手的文档交接问题与首版验收目标"
    assert observed["research_revision_id"] == focus.json()["head"]["id"]
    assert observed["method_bundle_ref"] == focus.json()["head"]["state"]["method_bundle_ref"]
    assert list(observed["methods"]) == ["common", "product_design"]
    assert any("早期讨论要求保留约束" in str(item.content) for item in messages)
