"""复现真实 I02 的 DSML 包装；拒绝泄漏，不执行正文中的调用。"""

from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk
from langgraph.checkpoint.memory import InMemorySaver
from test_api_worker import services as _services
from test_runtime import runtime_services as _runtime_services
from test_runtime import setup_run

from knowledgeops_agent.harness.models.validation import FinalAnswerValidation

services = _services
runtime_services = _runtime_services

# b4280fc8-77f9-425c-8d07-811f792aae4d/I02-interactive-1.json：
# turns[2].models ordinal=1368，tool_calls=[]、finish_reason=stop。
# 保留真实协议字符、空格和工具名；仅把材料 UUID 换成无关联的合成值。
ACTUAL_DSML = """<｜｜DSML｜｜ calls>
<｜｜DSML｜｜ invoke name="inspect_material">
<｜｜DSML｜｜ parameter name="source_id" string="true">00000000-0000-4000-8000-000000000001</｜｜DSML｜｜ parameter>
</｜｜DSML｜｜ invoke>
</｜｜DSML｜｜ calls>"""


def test_actual_dsml_retries_once_in_place_then_fails_without_tool_execution(runtime_services):
    _, context, _ = setup_run(runtime_services)
    first = FinalAnswerValidation().after_model(
        {"messages": [AIMessage(ACTUAL_DSML)]}, SimpleNamespace(context=context)
    )
    assert first["jump_to"] == "model"
    assert first["protocol_repair_count"] == 1
    assert first["final_response_feedback"]

    with pytest.raises(RuntimeError, match="结构化工具调用"):
        FinalAnswerValidation().after_model(
            {"messages": [AIMessage(ACTUAL_DSML)], "protocol_repair_count": 1},
            SimpleNamespace(context=context),
        )
    events = [e for e in context.jobs.events(context.run_id) if e["kind"] == "model_protocol_error"]
    assert len(events) == 2
    assert events[0]["data"]["retrying"] is True
    assert events[1]["data"]["retrying"] is False
    assert not any(event["kind"] == "tool_started" for event in context.jobs.events(context.run_id))
    assert context.gateway.ledger.records() == []
    assert context.knowledge.list_drafts(root_id=context.root_id) == []


@pytest.mark.parametrize(
    "content",
    [f"```xml\n{ACTUAL_DSML}\n```", f"`{ACTUAL_DSML}`", f"协议示例如下：\n{ACTUAL_DSML}"],
)
def test_actual_dsml_in_documentation_remains_a_valid_answer(runtime_services, content):
    _, context, _ = setup_run(runtime_services)
    assert FinalAnswerValidation().after_model(
        {"messages": [AIMessage(content)]}, SimpleNamespace(context=context)
    ) == {"final_response_feedback": []}


def test_actual_dsml_in_a_legacy_checkpoint_is_not_delivered_as_an_answer(runtime_services):
    from knowledgeops_agent.app.runs.worker import _outcome

    _, context, _ = setup_run(runtime_services)
    outcome = _outcome(context, {"messages": [AIMessage(ACTUAL_DSML)]})
    assert outcome["kind"] == "partial"
    assert outcome["answer"] == ""


def test_actual_dsml_worker_self_heals_within_single_bounded_retry(services, monkeypatch):
    from knowledgeops_agent.app.runs import worker as worker

    class SelfHealingProvider:
        def __init__(self):
            self.calls = 0

        def bind_tools(self, tools):
            return self

        def stream(self, messages, **kwargs):
            self.calls += 1
            if self.calls == 1:
                yield AIMessageChunk(content=ACTUAL_DSML, response_metadata={"finish_reason": "stop"})
            else:
                yield AIMessageChunk(content="已改用规范回复，未读取到额外材料。", response_metadata={"finish_reason": "stop"})

    services.gateway.enabled = True
    services.gateway.client = SelfHealingProvider()
    monkeypatch.setattr(worker, "ensure_publication_intent", lambda context: {})
    conversation = services.jobs.create_conversation("协议偶发自愈回归")
    run = services.jobs.enqueue(conversation["id"], "读取材料", request_key="actual-dsml-self-heal")
    result = worker.execute_run(
        run["id"],
        run["generation"],
        services=services,
        worker_id="actual-dsml-heal-worker",
        checkpointer=InMemorySaver(),
    )
    assert result["status"] == "completed"
    assert "规范回复" in result["outcome"]["answer"]
    records = services.gateway.ledger.records(root_id=run["root_id"])
    assert len(records) == 2
    assert not any(event["kind"] == "tool_started" for event in services.jobs.events(run["id"]))


def test_actual_dsml_worker_fails_after_bounded_retry_and_keeps_raw_response(
    services, monkeypatch
):
    from knowledgeops_agent.app.runs import worker as worker

    class Provider:
        def bind_tools(self, tools):
            return self

        def stream(self, messages, **kwargs):
            yield AIMessageChunk(content=ACTUAL_DSML, response_metadata={"finish_reason": "stop"})

    services.gateway.enabled = True
    services.gateway.client = Provider()
    monkeypatch.setattr(worker, "ensure_publication_intent", lambda context: {})
    conversation = services.jobs.create_conversation("真实协议形态回归")
    run = services.jobs.enqueue(conversation["id"], "读取材料", request_key="actual-dsml-leak")
    result = worker.execute_run(
        run["id"],
        run["generation"],
        services=services,
        worker_id="actual-dsml-worker",
        checkpointer=InMemorySaver(),
    )
    assert result["status"] == "failed"
    assert result["outcome"]["kind"] == "partial"
    assert result["outcome"]["answer"] == ""
    assert "结构化工具调用" in result["stop_reason"]
    records = services.gateway.ledger.records(root_id=run["root_id"])
    assert len(records) == 2
    assert records[0]["response"]["content"] == ACTUAL_DSML
    assert records[0]["response"]["tool_calls"] == []
    assert not any(event["kind"] == "tool_started" for event in services.jobs.events(run["id"]))
    assert not services.knowledge.list_drafts(root_id=run["root_id"])
