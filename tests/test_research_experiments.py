"""真实研发运行提出实验，审批后才投递，结果绑定同一尝试和研究版本。"""

import json
from pathlib import Path
from runpy import run_path

import pytest
from langchain_core.messages import AIMessageChunk
from langgraph.checkpoint.memory import InMemorySaver
from test_research_execution import BriefReader, create_task, submit
from test_research_execution import client as research_client
from test_research_execution import services as research_services

from knowledgeops_agent.app.runs.worker import execute_run
from knowledgeops_agent.harness.tools.policy import ToolPolicyService

client = research_client
services = research_services
ROOT = Path(__file__).parents[1]
DATA = json.loads((ROOT / "tests/fixtures/research_retrieval_case.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("intervention", ["none", "cancel", "revise"])
def test_experiment_approval_is_explicit_and_late_results_cannot_be_evidence(client, services, tmp_path, intervention):
    exchange = tmp_path / "exchange"
    services.sessions.configure(info={**services.sessions.kw["info"], "research_experiment_exchange": exchange})
    chat, task = create_task(client)
    base = f"/api/research-tasks/{task['id']}"
    result = client.get(base + "/experiments")
    assert result.status_code == 200, result.text
    assert result.json()["items"] == []
    policy = ToolPolicyService(services.sessions)
    policy.update(expected_revision=policy.get()["revision"], enabled={"builtin:propose_research_experiment": True})

    class Proposer(BriefReader):
        def stream(self, messages, **kwargs):
            self.requests.append(messages)
            if len(self.requests) > 1:
                yield AIMessageChunk("等待人工确认固定输入后执行。")
                return
            yield AIMessageChunk("", tool_call_chunks=[{
                "name": "propose_research_experiment", "id": "request", "index": 0,
                "args": json.dumps({
                    "question": "重排能否分别解决这两种错答？",
                    "hypothesis": "候选漏选可改善，生成误用需要另查。",
                    "dataset": DATA, "evidence_ids": [],
                }, ensure_ascii=False),
            }])

    services.gateway.client, services.gateway.enabled = Proposer(), True
    run = submit(client, chat)
    assert execute_run(run["id"], run["generation"], services=services,
                       checkpointer=InMemorySaver())["status"] == "completed"
    [attempt] = client.get(base + "/experiments").json()["items"]
    assert attempt["status"] == "awaiting_approval"
    current = client.get(base).json()
    path = base + f"/experiments/{attempt['id']}"
    body = {
        "operation_id": "approve", "expected_control_version": current["control_version"],
        "expected_revision_id": current["head"]["id"], "action": "approve",
    }

    from knowledgeops_agent.research.experiments import tick

    tick(services.jobs)
    requests = exchange / "requests"
    assert not list(requests.glob("*.json"))
    invalid = client.post(path, json={**body, "expected_revision_id": "different"})
    assert invalid.status_code == 409
    if intervention == "revise":
        changed = client.post(base + "/focus", json={
            "operation_id": "revise", "expected_control_version": current["control_version"],
            "expected_revision_id": current["head"]["id"], "phase": "investigation",
            "capabilities": ["product_design"], "reason": "先核对验收目标。", "pinned": True,
        })
        assert changed.status_code == 200, changed.text
        revision = changed.json()["head"]["id"]
        old_item = client.get(path).json()
        assert old_item["status"] == "cancelled"
        assert client.get(base + "/experiments").json()["items"][0]["status"] == "cancelled"
        assert client.post(path, json={**body, "expected_revision_id": revision}).status_code == 409
        retried = client.post(path, json={
            **body, "operation_id": "reproduce-after-revision", "action": "reproduce", "expected_revision_id": revision,
        })
        assert retried.status_code == 200, retried.text
        assert retried.json()["status"] == "awaiting_approval"
        assert retried.json()["source_revision_id"] == revision
        assert retried.json()["input_sha256"] == old_item["input_sha256"]
        assert not list(requests.glob("*.json"))
        return
    approved = client.post(path, json=body)
    assert approved.status_code == 200, approved.text
    assert client.post(path, json=body).json() == approved.json()
    tick(services.jobs)
    [request_path] = list(requests.glob("*.json"))
    packet = json.loads(request_path.read_text(encoding="utf-8"))
    assert packet["input"] == DATA

    def execute(packet, _cancelled):
        output = run_path(str(ROOT / "scripts/research-experiments/reranker_check.py"))["analyze"](packet["input"])
        return {"status": "completed", "exit_code": 0, "output": output}

    def late(packet, cancelled):
        if intervention == "cancel":
            denied = client.post(path, json={**body, "operation_id": "cancel", "action": "cancel"})
            assert denied.status_code == 200
        return execute(packet, cancelled)

    runner = run_path(str(ROOT / "scripts/research-experiments/runner.py"))
    receipt = runner["handle"](exchange, packet, execute=late)
    assert receipt["status"] == "completed"
    tick(services.jobs)
    item = client.get(path).json()
    cancel = intervention == "cancel"
    assert item["status"] == ("stale" if cancel else "completed")
    assert bool(item["evidence_id"]) is not cancel
    assert item["result"] == receipt["output"]
    assert client.get(base + "/experiments").json()["items"][0]["result"] == receipt["output"]
    if not cancel:
        evidence = client.get(base + f"/evidence/{item['evidence_id']}").json()
        assert evidence["kind"] == "experiment"
        assert "回答误用" in evidence["text"]
        latest = client.get(base).json()
        again = client.post(path, json={
            **body, "operation_id": "reproduce", "action": "reproduce",
            "expected_revision_id": latest["head"]["id"],
        })
        assert again.status_code == 200, again.text
        assert again.json()["status"] == "awaiting_approval"
        assert again.json()["input_sha256"] == item["input_sha256"]
        assert again.json()["id"] != item["id"]


@pytest.mark.parametrize("pause_at", ["never", "blocked", "running"])
def test_unknown_experiment_prevents_delivery_until_stop_is_confirmed(client, services, tmp_path, pause_at):
    from knowledgeops_agent.research import deliveries, experiments
    from knowledgeops_agent.research.contracts import RetrievalDataset

    exchange = tmp_path / "exchange"
    services.sessions.configure(info={**services.sessions.kw["info"], "research_experiment_exchange": exchange})
    chat, task = create_task(client)
    base = f"/api/research-tasks/{task['id']}"
    run = submit(client, chat, "propose-unknown")
    services.jobs.claim("proposer", run_id=run["id"], generation=run["generation"])
    proposed = experiments.propose(
        services.jobs, run["id"], "proposer", "experiment", "模型误用与截断如何区分？",
        "候选漏选可能可以通过基线对照验证。", RetrievalDataset.model_validate(DATA), [],
    )
    services.jobs.finish(run["id"], "proposer", status="completed")
    current = client.get(base).json()
    path = base + f"/experiments/{proposed['id']}"
    assert client.post(path, json={
        "operation_id": "approve-before-loss", "action": "approve",
        "expected_revision_id": current["head"]["id"], "expected_control_version": current["control_version"],
    }).status_code == 200
    experiments.tick(services.jobs)
    request_path = exchange / "requests" / f"{proposed['id']}.json"
    packet = json.loads(request_path.read_text(encoding="utf-8"))
    claim = exchange / "claims" / request_path.name
    claim.parent.mkdir()
    claim.write_text("{}")

    result = submit(client, chat, "deliver-after-loss")
    services.jobs.claim("deliverer", run_id=result["id"], generation=result["generation"])
    handoff = deliveries.submit(services.jobs, result["id"], "deliverer", "delivery", {
        "title": "已有诊断", "diagnosis": "两类错因需要分别调查。", "recommendations": "先排查证据的读取链。",
        "limitations": "还没有经过核验的实验结果。", "source_version_ids": [], "evidence_ids": [],
    })

    def control(action):
        task = client.get(base).json()
        response = client.post(base + "/control", json={
            "operation_id": action, "action": action, "expected_control_version": task["control_version"],
        })
        assert response.status_code == 200, response.text
        return response.json()

    if pause_at == "running":
        control("pause")
        # 已观察到暂停的 Worker 也必须允许取消优先，不能让旧图落到可恢复暂停。
        assert services.jobs.settle_pause(result["id"], "deliverer", outcome={})["status"] == "cancelled"
        control("resume")
    else:
        services.jobs.finish(result["id"], "deliverer", status="completed")
    assert client.get(base).json()["status"] == "open"
    runner = run_path(str(ROOT / "scripts/research-experiments/runner.py"))

    def unexpected(*_):
        pytest.fail("claimed experiment should not launch again after restart")

    assert runner["handle"](exchange, packet, execute=unexpected)["status"] == "unknown"
    experiments.tick(services.jobs)
    [delivery] = client.get(base + "/deliveries").json()["items"]
    assert delivery["id"] == handoff["delivery_id"]
    assert delivery["status"] == ("superseded" if pause_at == "running" else "blocked")
    assert delivery["blockers"] == [{"id": proposed["id"], "kind": "experiment", "status": "unknown"}]
    current = client.get(base).json()
    if pause_at == "blocked":
        control("pause")
        current = control("resume")
        [withdrawn] = client.get(base + "/deliveries").json()["items"]
        assert withdrawn["status"] == "superseded" and withdrawn["drain_state"] == "blocked"
    stop = client.post(path, json={
        "operation_id": "confirmed-stop", "action": "acknowledge_stop",
        "expected_revision_id": current["head"]["id"], "expected_control_version": current["control_version"],
        "reason": "管理员已检查实例和容器均停止。原结果未知。",
    })
    assert stop.status_code == 200, stop.text
    assert stop.json()["evidence_id"] is None
    pause_before_stop = pause_at != "never"
    assert client.get(base).json()["status"] == ("open" if pause_before_stop else "awaiting_acceptance")
    [saved] = client.get(base + "/deliveries").json()["items"]
    assert saved["status"] == ("superseded" if pause_before_stop else "submitted")
    assert saved["drain_state"] == "settled"
    messages = client.get(f"/api/conversations/{chat}/messages").json()
    assert any(message["id"] == handoff["delivery_id"] for message in messages) is not pause_before_stop
    if pause_before_stop:
        assert submit(client, chat, "continue-after-drain")["status"] == "queued"
