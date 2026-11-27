"""工具事件在真实 Run 中保存有界预览与耗时。"""

import json
from types import SimpleNamespace

import pytest
from langchain_core.messages import ToolMessage
from test_runtime import runtime_services as _runtime_services
from test_runtime import setup_run

from knowledgeops_agent.harness.runtime import control

runtime_services = _runtime_services


@pytest.mark.parametrize(("name", "args", "expected"), [
    ("search_references", {"query": "  回滚\n规则  "}, "回滚 规则"),
    ("read_file", {"file_path": "notes/result.md", "body": "未获准投影"}, "notes/result.md"),
    ("read_web", {"url": "https://alice:secret@example.org:8443/guide?token=ignored#fragment"},
     "https://example.org:8443/guide"),
    ("execute", {"command": "echo Bearer secret-token-0123456789 " + "x" * 200},
     "echo [REDACTED] " + "x" * 83 + "…"),
    ("browser_navigate", {"url": "https://example.org"}, None),
    ("external_tool", {"query": "禁止任意外部参数进入摘要"}, None),
    ("read_file", {"file_path": {"value": "禁止转换任意对象"}}, None),
])
def test_summary_whitelists_and_bounds_tool_values(name, args, expected):
    assert control._summarize_tool_args(name, args, lambda value: value) == expected


@pytest.mark.parametrize(("name", "args", "expected"), [
    ("search_references", {"query": "test-known-key " + "回" * 130, "ignore": "脏值" * 4000},
     "[REDACTED] " + "回" * 108 + "…"),
    ("read_file", {"file_path": "notes/result.md"}, "notes/result.md"),
    ("read_web", {"url": "https://alice:password@example.org/guide?token=hidden"}, "https://example.org/guide"),
    ("execute", {"command": "pwd"}, "pwd"),
])
def test_tool_events_keep_summary_and_duration(runtime_services, name, args, expected, monkeypatch, tmp_path):
    module, context, run = setup_run(runtime_services)
    context.gateway.sensitive_values = ["test-known-key"]
    if name == "execute":
        # 此处只隔离命令审批，命令回执和事件继续走真实数据库。
        from knowledgeops_agent.harness.permissions.approvals import ExecutionApprovals

        context.workspace_root = tmp_path
        monkeypatch.setattr(ExecutionApprovals, "before_tool", lambda *_: None)
        monkeypatch.setattr(ExecutionApprovals, "validate_command_execution", lambda *_, **__: True)
    request = SimpleNamespace(runtime=SimpleNamespace(context=context), state={},
                              tool_call={"id": "step", "name": name, "args": args})
    result = module.RuntimeControlMiddleware().wrap_tool_call(
        request, lambda _: ToolMessage(content=json.dumps("输出" * 4000), tool_call_id="step"),
    )
    assert result.status == "success", result.content
    events = {event["kind"]: event["data"] for event in context.jobs.events(run["id"])}
    assert events["tool_started"]["summary"] == expected
    assert events["tool_finished"]["summary"] == expected
    assert events["tool_finished"]["duration_ms"] >= 0
