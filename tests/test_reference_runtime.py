"""用真实 Agent 工具和资料服务验证联合问答，唯有供应商响应为离线替身。"""

import json

import pytest
from langchain_core.messages import AIMessageChunk, HumanMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy import select
from test_runtime import runtime_services as _runtime_services
from test_runtime import setup_run

from knowledgeops_agent.harness.tools.policy import ToolPolicyService
from knowledgeops_agent.persistence.models import ModelStep, Source, utcnow

runtime_services = _runtime_services


class JointAnswerProvider:
    def __init__(self, *, withdrawn: bool):
        self.withdrawn = withdrawn

    def bind_tools(self, tools):
        return self

    def stream(self, messages, **kwargs):
        results = [message for message in messages if isinstance(message, ToolMessage)]
        if not results:
            yield AIMessageChunk(content="", tool_call_chunks=[{
                "name": "search_references",
                "args": '{"query":"生产紧急变更"}',
                "id": "joint-search",
                "index": 0,
            }])
            return
        assert results[-1].status != "error"
        result = json.loads(results[-1].content)
        rows = result["evidence"]
        assert [row["kind"] for row in rows] == (
            ["knowledge"] if self.withdrawn else ["knowledge", "material"]
        )
        assert "当班负责人事前批准" in rows[0]["text"]
        if not self.withdrawn:
            assert "记录回滚点" in rows[1]["text"]
        else:
            assert "记录回滚点" not in results[-1].content
        yield AIMessageChunk(content="批准与准备条件分别见 " + " 和 ".join(row["citation"] for row in rows))


@pytest.mark.parametrize("withdrawn", [False, True])
def test_answer_reads_current_evidence_from_both_lanes_in_real_graph(runtime_services, monkeypatch, withdrawn):
    jobs, knowledge, sources, _ = runtime_services
    draft = knowledge.create_draft("fixture", [{
        "action": "create", "title": "现行规程", "body": "生产紧急变更由当班负责人事前批准。",
    }])
    knowledge.approve(draft["id"])
    knowledge.publish(draft["id"], idempotency_key="current-regulation")
    material = sources.import_text("运维操作手册", "生产紧急变更前须记录回滚点。")
    ToolPolicyService(jobs.sessions).update(
        expected_revision=0, enabled={"builtin:search_references": True},
    )
    module, context, run = setup_run(runtime_services, task="生产紧急变更需要谁批准？动手前还要检查什么？")
    # 清空测试帮助函数的默认附件，执行普通资料问答的默认联合范围。
    with jobs.sessions.begin() as session:
        row = jobs._locked(session, run["id"])
        row.input_data = {**row.input_data, "source_ids": []}
    if withdrawn:
        original_read = sources.read_search_hit

        def read_then_withdraw(*args, **kwargs):
            evidence = original_read(*args, **kwargs)
            with jobs.sessions.begin() as session:
                session.get(Source, material["id"]).deleted_at = utcnow()
            return evidence

        monkeypatch.setattr(sources, "read_search_hit", read_then_withdraw)
    context.gateway.client = JointAnswerProvider(withdrawn=withdrawn)
    graph = module.build_agent(context, checkpointer=InMemorySaver())
    state = graph.invoke(
        {"messages": [HumanMessage(run["task"])]},
        config={"configurable": {"thread_id": run["checkpoint_id"]}},
        context=context,
    )
    evidence = [sources.get_evidence(row["id"]) for row in sources.evidence_catalog(run["id"])]
    assert any(row.get("source_version_id") == material["version_id"] for row in evidence)
    answer = state["messages"][-1].content
    assert answer.count("/api/evidence/") == (1 if withdrawn else 2)
    delivered = jobs.finish(run["id"], "worker-test", outcome={"kind": "answered", "answer": answer})
    assert delivered["status"] == "completed"
    with jobs.sessions() as session:
        bound = list(session.scalars(select(ModelStep).where(ModelStep.run_id == run["id"])))
    sent = {identity for step in bound for identity in step.request_snapshot.get("reference_evidence_ids", [])}
    assert sent == {row["id"] for row in evidence if not (withdrawn and row.get("source_id"))}
    assert set(delivered["outcome"]["reference_citations"]) == sent
    [search] = [event["data"] for event in jobs.events(run["id"]) if event["kind"] == "reference_search"]
    assert [row["kind"] for row in search["candidates"]] == ["knowledge", "material"]
    assert not any("text" in row or "snippet" in row for row in search["candidates"])
