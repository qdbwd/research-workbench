"""验证流式思考片段（reasoning_delta）的脱敏推送与业务正文隔离。"""

from langchain_core.messages import AIMessageChunk
from langgraph.checkpoint.memory import InMemorySaver
from test_api_worker import services as _services

services = _services


def test_streaming_emits_redacted_reasoning_delta_isolated_from_chat_answer(services, monkeypatch):
    from knowledgeops_agent.app.runs import worker

    secret = "sk-secret-reasoning-token-999999"
    services.gateway.sensitive_values = [secret]

    class ReasoningProvider:
        def bind_tools(self, tools):
            return self

        def stream(self, messages, **kwargs):
            yield AIMessageChunk(
                content="",
                additional_kwargs={"reasoning_content": f"先核对关键线索，密钥 {secret} 不应明文暴露。"},
            )
            yield AIMessageChunk(
                content="这是面向用户的最终答复。",
                response_metadata={"finish_reason": "stop"},
            )

    services.gateway.enabled = True
    services.gateway.client = ReasoningProvider()
    monkeypatch.setattr(worker, "ensure_publication_intent", lambda context: {})

    conversation = services.jobs.create_conversation("思考流可观测性测试")
    run = services.jobs.enqueue(conversation["id"], "请分析原因", request_key="reasoning-delta-1")
    result = worker.execute_run(
        run["id"],
        run["generation"],
        services=services,
        worker_id="reasoning-stream-worker",
        checkpointer=InMemorySaver(),
    )

    assert result["status"] == "completed"
    assert result["outcome"]["answer"] == "这是面向用户的最终答复。"
    assert "先核对关键线索" not in result["outcome"]["answer"]

    events = services.jobs.events(run["id"])
    reasoning_events = [event for event in events if event["kind"] == "reasoning_delta"]
    assert reasoning_events, "应推送 reasoning_delta 事件用于实时进度展示"
    combined_reasoning = "".join(event["data"]["text"] for event in reasoning_events)
    assert "先核对关键线索" in combined_reasoning
    assert secret not in combined_reasoning
    assert "[REDACTED]" in combined_reasoning

    messages = services.jobs.messages(conversation["id"])
    assistant_texts = [row["content"] for row in messages if row["role"] == "assistant"]
    assert assistant_texts == ["这是面向用户的最终答复。"]
