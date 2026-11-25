"""前台 v6 解释仅保留授权与学习，旧版恢复由历史冻结测试覆盖。"""

from test_preferences import proposal
from test_publication_intent import context as _context
from test_publication_intent import interpret, semantic_response

from knowledgeops_agent.memory.service import MemoryService

context = _context


def test_v6_delegates_memory_without_changing_publication_intent(context):
    task = context.jobs.get_run(context.run_id)["task"]
    result = interpret(
        context,
        semantic_response(
            context,
            preferences=[proposal("新增长期偏好", task)],
        ),
    )
    assert result["mode"] == "direct"
    assert result["preferences"] == {"delegated": "evolution_review"}
    assert not MemoryService(context.jobs.sessions).list()
    (record,) = context.gateway.ledger.records(root_id=context.root_id)
    assert (
        context.gateway.ledger.linked_record(
            context.run_id,
            "intent-v6:" + context.jobs.publication_context(context.run_id)["message"]["id"],
        )["id"]
        == record["id"]
    )
    prompt, payload = record["request"]["messages"]
    assert "preferences数组" not in prompt["content"]
    assert "known_preferences" not in payload["content"]
    assert "memory_categories" not in payload["content"]
