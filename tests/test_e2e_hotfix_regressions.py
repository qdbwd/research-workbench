"""冲刺包 2 端到端联调热修回归：平铺地图工具、畸形工具调用有界纠偏、知识集与分块全工具收敛及研发检索权限。"""

from __future__ import annotations

import json

import pytest
from langchain_core.messages import AIMessageChunk, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from test_api_worker import client as _client
from test_api_worker import services as _services
from test_pgvector_retrieval import _publish_doc
from test_research_map import BriefReader, create_task, submit

from knowledgeops_agent.app.runs.worker import execute_run
from knowledgeops_agent.harness.models.validation import (
    INVALID_TOOL_CALL_FAILURE,
    INVALID_TOOL_CALL_REPAIR_FEEDBACK,
)
from knowledgeops_agent.harness.tools.policy import ToolPolicyService

client = _client
services = _services


def test_update_research_map_flat_params_save_and_remove(client, services):
    """平铺参数支持新增/更新/删除地图节点并保持幂等。"""
    chat, task = create_task(client)
    policy = ToolPolicyService(services.sessions)
    policy.update(expected_revision=policy.get()["revision"], enabled={
        "builtin:update_research_map": True,
        "builtin:read_research_map": True,
    })

    class FlatMapProvider(BriefReader):
        def stream(self, messages, **kwargs):
            self.requests.append(messages)
            if len(self.requests) == 1:
                tool, arguments = "update_research_map", {
                    "action": "save",
                    "question": "入口限流与渠道并发池是否存在容量落差？",
                    "criteria": "基于压测数据完成定量核算",
                    "status": "investigating",
                    "conclusion": "存在约 2 倍容量落差。",
                    "reason": "建立容量核算子议题",
                }
            elif len(self.requests) == 2:
                tool, arguments = "read_research_map", {}
            elif len(self.requests) == 3:
                listed = next(
                    json.loads(msg.content)
                    for msg in reversed(messages)
                    if isinstance(msg, ToolMessage) and msg.name == "read_research_map"
                )
                node_id = listed["nodes"][0]["id"]
                tool, arguments = "update_research_map", {
                    "action": "remove",
                    "node_id": node_id,
                    "reason": "移除临时探索议题",
                }
            else:
                yield AIMessageChunk("已完成平铺参数地图增删。")
                return
            yield AIMessageChunk("", tool_call_chunks=[{
                "name": tool,
                "args": json.dumps(arguments, ensure_ascii=False),
                "id": f"flat-map-{len(self.requests)}",
                "index": 0,
            }])

    services.gateway.enabled, services.gateway.client = True, FlatMapProvider()
    run = submit(client, chat)
    finished = execute_run(run["id"], run["generation"], services=services, checkpointer=InMemorySaver())
    assert finished["status"] == "completed", finished
    assert client.get(f"/api/research-tasks/{task['id']}/map").json()["nodes"] == []


def test_invalid_tool_calls_repaired_once_and_fails_safely_on_repeat(client, services):
    """单次畸形 JSON 工具调用经 1 次有界纠偏恢复且不抛 ConversionError；连续两次安全失败。"""
    chat, task = create_task(client)
    policy = ToolPolicyService(services.sessions)
    policy.update(expected_revision=policy.get()["revision"], enabled={"builtin:update_research_map": True})

    class RepairOnceProvider(BriefReader):
        def __init__(self):
            super().__init__()
            self.bound_tool_counts: list[int] = []

        def bind_tools(self, tools):
            self.bound_tool_counts.append(len(tools))
            return self

        def stream(self, messages, **kwargs):
            self.requests.append(messages)
            if len(self.requests) == 1:
                # 模拟 DeepSeek 结尾多一个 '}' 落入 invalid_tool_calls。
                yield AIMessageChunk("", tool_call_chunks=[{
                    "name": "update_research_map",
                    "args": '{"action": "save", "question": "容量核算", "criteria": "完成定量核对", "reason": "建节点"}}',
                    "id": "bad-json-1",
                    "index": 0,
                }])
            elif len(self.requests) == 2:
                system_text = messages[0].content
                assert INVALID_TOOL_CALL_REPAIR_FEEDBACK in system_text
                # 纠偏轮仍保留工具定义，模型可重新发起合法调用。
                assert self.bound_tool_counts[-1] > 0
                yield AIMessageChunk("", tool_call_chunks=[{
                    "name": "update_research_map",
                    "args": json.dumps({
                        "action": "save",
                        "question": "容量核算是否存在落差？",
                        "criteria": "完成定量核对",
                        "reason": "纠偏后成功建节点",
                    }, ensure_ascii=False),
                    "id": "good-json-2",
                    "index": 0,
                }])
            else:
                yield AIMessageChunk("纠偏后已成功更新地图。")

    provider = RepairOnceProvider()
    services.gateway.enabled, services.gateway.client = True, provider
    run = submit(client, chat)
    finished = execute_run(run["id"], run["generation"], services=services, checkpointer=InMemorySaver())
    assert finished["status"] == "completed", finished
    nodes = client.get(f"/api/research-tasks/{task['id']}/map").json()["nodes"]
    assert len(nodes) == 1 and nodes[0]["question"] == "容量核算是否存在落差？"

    # 逆向：连续两次返回畸形 JSON，安全中止为 InvalidModelOutput，绝不报 ConversionError。
    class AlwaysMalformedProvider(BriefReader):
        def stream(self, messages, **kwargs):
            self.requests.append(messages)
            yield AIMessageChunk("", tool_call_chunks=[{
                "name": "update_research_map",
                "args": '{"action": "save", "reason": "bad"}}',
                "id": f"always-bad-{len(self.requests)}",
                "index": 0,
            }])

    bad_provider = AlwaysMalformedProvider()
    services.gateway.client = bad_provider
    run2 = client.post(
        f"/api/conversations/{chat}/messages",
        json={"content": "再次尝试更新地图", "request_key": "repeat-bad-json"},
    ).json()
    failed = execute_run(run2["id"], run2["generation"], services=services, checkpointer=InMemorySaver())
    assert failed["status"] == "failed"
    assert len(bad_provider.requests) == 2
    assert INVALID_TOOL_CALL_FAILURE in (failed.get("stop_reason") or "")


def test_mounted_collection_and_disabled_chunks_enforced_across_all_tools(client, services):
    """挂载知识集与已排除分块必须同时收敛 list/inspect/read/search 全部工具，防止绕过泄露。"""
    in_src = services.sources.import_text(
        "集内故障复盘",
        "核心技术正文：渠道 B 缺少 read_timeout 导致连接池打满。\n\n"
        + ("背景补充段落内容。" * 280)
        + "\n\n秘密噪声附录：行政预订美式咖啡 12 杯与生椰拿铁 8 杯。",
    )
    out_src = services.sources.import_text(
        "集外前端灰度规范",
        "前端 Nginx 灰度切流比例为 5% -> 20% -> 100%。",
    )
    assert client.post(f"/api/sources/{in_src['id']}/save").status_code == 200
    assert client.post(f"/api/sources/{out_src['id']}/save").status_code == 200
    # 建立知识文档（集内 + 集外）
    in_doc = _publish_doc(services, title="集内规范 V2", body="现行 V2 入口限流 3500 QPS。")
    out_doc = _publish_doc(services, title="集外文档", body="仅限集外查看的秘密配置 9999。")

    col = client.post("/api/retrieval/collections", json={"name": "支付专题", "description": "仅支付资料"}).json()
    put_resp = client.put(
        f"/api/retrieval/collections/{col['id']}/items",
        json={
            "expected_revision": col["revision"],
            "items": [
                {"asset_kind": "source", "asset_id": in_src["id"]},
                {"asset_kind": "knowledge", "asset_id": in_doc["id"]},
            ],
        },
    )
    assert put_resp.status_code == 200, put_resp.text
    # 排除包含“美式咖啡 12 杯”的分块
    chunks_path = f"/api/retrieval/assets/source/{in_src['id']}/chunks"
    chunks = client.get(chunks_path).json()
    noise = next(item for item in chunks["items"] if "美式咖啡" in item["text"])
    patch_resp = client.patch(
        chunks_path + "/status",
        json={
            "version_id": chunks["version_id"],
            "chunk_index": noise["chunk_index"],
            "segment_id": noise["segment_id"],
            "enabled": False,
        },
    )
    assert patch_resp.status_code == 200, patch_resp.text

    conv = services.jobs.create_conversation("挂载隔离测试")
    created = client.post(
        f"/api/conversations/{conv['id']}/messages",
        json={
            "content": "尝试绕过挂载读取集外资料和禁用噪声块",
            "request_key": "mount-enforce-1",
            "rag_scope": "collections",
            "collection_ids": [col["id"]],
        },
    )
    assert created.status_code == 202
    run = created.json()
    services.jobs.claim("enforce-worker", run_id=run["id"], generation=run["generation"])

    from types import SimpleNamespace

    from knowledgeops_agent.harness.runtime.state import RunContext
    from knowledgeops_agent.knowledge.tools import (
        knowledge_history,
        list_knowledge,
        read_knowledge,
        search_knowledge,
    )
    from knowledgeops_agent.sources.tools import inspect_material, list_materials, read_source

    context = RunContext(
        run_id=run["id"],
        root_id=run["id"],
        worker_id="enforce-worker",
        jobs=services.jobs,
        knowledge=services.knowledge,
        sources=services.sources,
        gateway=services.gateway,
    )
    rt = SimpleNamespace(context=context, tool_call_id="call-1", state={})

    # 1. list_materials / list_knowledge / search_knowledge 仅返回集内资产
    mats = list_materials.func(runtime=rt)
    assert {m["id"] for m in mats} == {in_src["id"]}
    docs = list_knowledge.func(runtime=rt)
    assert {d["id"] for d in docs} == {in_doc["id"]}
    hits = search_knowledge.func(query="秘密配置", runtime=rt)
    assert all(h["document_id"] == in_doc["id"] for h in hits)

    # 2. inspect_material / read_source / read_knowledge / knowledge_history 拒绝集外资产
    with pytest.raises(PermissionError, match="知识集范围"):
        inspect_material.func(source_id=out_src["id"], runtime=rt)
    out_detail = services.sources.get_source(out_src["id"], root_id=run["id"])
    with pytest.raises(PermissionError, match="知识集范围"):
        read_source.func(segment_id=out_detail["segments"][0]["id"], runtime=rt)
    with pytest.raises(PermissionError, match="知识集范围"):
        read_knowledge.func(document_id=out_doc["id"], runtime=rt)
    with pytest.raises(PermissionError, match="知识集范围"):
        knowledge_history.func(document_id=out_doc["id"], runtime=rt)

    # 3. 集内启用正文可读，且被禁用的噪声块绝不泄露
    in_detail = inspect_material.func(source_id=in_src["id"], runtime=rt)
    visible_texts = []
    excluded_raised = False
    for seg in in_detail["segments"]:
        try:
            read = read_source.func(segment_id=seg["id"], runtime=rt)
            visible_texts.append(read["text"])
        except PermissionError as exc:
            if "排除检索" in str(exc):
                excluded_raised = True
            else:
                raise
    combined = "\n".join(visible_texts)
    assert "read_timeout" in combined
    assert "美式咖啡" not in combined
    assert excluded_raised or "已排除检索的分块内容" in combined


def test_research_search_references_allows_materials_when_web_disabled(client, services):
    """研发自主运行未开联网（knowledge_only=True）时，search_references 仍允许检索本地已导入材料。"""
    src = services.sources.import_text(
        "研发材料",
        "支付网关熔断阈值为连续 30 秒错误率大于 15%。",
    )
    assert client.post(f"/api/sources/{src['id']}/save").status_code == 200
    chat, task = create_task(client)
    policy = ToolPolicyService(services.sessions)
    policy.update(expected_revision=policy.get()["revision"], enabled={
        "builtin:search_references": True,
        "builtin:read_source": True,
    })
    # 以 searches=0 授予自主许可（research/autonomy.py 会写入 knowledge_only=True）
    granted = client.post(
        f"/api/research-tasks/{task['id']}/autonomy",
        json={
            "operation_id": "grant-no-web",
            "expected_control_version": task["control_version"],
            "expected_revision_id": task["head"]["id"],
            "action": "grant",
            "budgets": {"model_requests": 4, "tool_calls": 8, "searches": 0, "active_seconds": 300},
        },
    )
    assert granted.status_code == 200, granted.text
    runs = client.get(f"/api/runs/page?conversation_id={chat}&roots_only=true").json()["items"]
    auto_run = next(r for r in runs if r["kind"] == "agent")
    assert auto_run["input"]["knowledge_only"] is True
    services.jobs.claim("research-ref-worker", run_id=auto_run["id"], generation=auto_run["generation"])

    from types import SimpleNamespace

    from knowledgeops_agent.harness.runtime.resources import prepare_resources
    from knowledgeops_agent.harness.runtime.state import RunContext
    from knowledgeops_agent.retrieval.tools import search_references
    from knowledgeops_agent.sources.tools import search_web

    context = RunContext(
        run_id=auto_run["id"],
        root_id=auto_run["id"],
        worker_id="research-ref-worker",
        jobs=services.jobs,
        knowledge=services.knowledge,
        sources=services.sources,
        gateway=services.gateway,
    )
    prepare_resources(context)
    rt = SimpleNamespace(context=context, tool_call_id="ref-1", state={})
    res = search_references.func(query="熔断阈值", scope="material", runtime=rt)
    assert res["scope"] == "material"
    assert any(ev["asset_id"] == src["id"] for ev in res["evidence"])
    # 联网搜索仍严格被 knowledge_only 拒绝
    with pytest.raises(PermissionError, match="knowledge-only"):
        search_web.func(query="熔断", runtime=rt)


def test_invalid_tool_call_cleaned_message_strips_wire_tool_calls_for_deepseek(client, services):
    """P0-1：净化后的 AIMessage 必须剥离 additional_kwargs['tool_calls']，真实 DeepSeek 序列化不带孤立 tool_calls。"""
    from types import SimpleNamespace

    from langchain_core.messages import AIMessage, HumanMessage

    from knowledgeops_agent.harness.models.deepseek import DeepSeekClient
    from knowledgeops_agent.harness.models.validation import FinalAnswerValidation
    from knowledgeops_agent.harness.runtime.state import RunContext

    conv = services.jobs.create_conversation("DeepSeek 净化序列化测试")
    run = client.post(
        f"/api/conversations/{conv['id']}/messages",
        json={"content": "测试净化消息", "request_key": "clean-ds-wire"},
    ).json()
    services.jobs.claim("ds-wire-worker", run_id=run["id"], generation=run["generation"])
    context = RunContext(
        run_id=run["id"],
        root_id=run["id"],
        worker_id="ds-wire-worker",
        jobs=services.jobs,
        knowledge=services.knowledge,
        sources=services.sources,
        gateway=services.gateway,
    )
    malformed = AIMessage(
        content="",
        tool_calls=[],
        invalid_tool_calls=[
            {
                "name": "update_research_map",
                "args": '{"action":"save"}}',
                "id": "call-bad-1",
                "error": "JSONDecodeError",
                "type": "invalid_tool_call",
            }
        ],
        additional_kwargs={
            "tool_calls": [
                {
                    "id": "call-bad-1",
                    "type": "function",
                    "function": {"name": "update_research_map", "arguments": '{"action":"save"}}'},
                }
            ],
            "function_call": {"name": "update_research_map", "arguments": "{}"},
            "reasoning_content": "先检查平铺参数格式",
        },
    )
    rt = SimpleNamespace(context=context)
    patch = FinalAnswerValidation().after_model(
        {"messages": [HumanMessage(content="请更新地图"), malformed], "protocol_repair_count": 0},
        rt,
    )
    assert patch is not None and patch["jump_to"] == "model"
    cleaned = patch["messages"][0]
    assert "tool_calls" not in cleaned.additional_kwargs
    assert "function_call" not in cleaned.additional_kwargs
    assert cleaned.additional_kwargs["reasoning_content"] == "先检查平铺参数格式"

    ds_client = DeepSeekClient(api_key="test-key", model="deepseek-chat")
    payload = ds_client._get_request_payload(
        [HumanMessage(content="请更新地图"), cleaned],
        extra_body={"thinking": {"type": "enabled"}},
    )
    assistant_msgs = [m for m in payload["messages"] if m["role"] == "assistant"]
    assert len(assistant_msgs) == 1
    assert "tool_calls" not in assistant_msgs[0]
    assert "function_call" not in assistant_msgs[0]
    assert assistant_msgs[0]["reasoning_content"] == "先检查平铺参数格式"


def test_protocol_repair_mixed_batch_not_executed_and_finalize_uses_wrapup(client, services):
    """P1-2 & 同批不执行：合法+畸形混合批次全批跳过；进入 finalize 收尾时清空 tools 且不要求重调工具。"""
    chat, task = create_task(client)
    policy = ToolPolicyService(services.sessions)
    policy.update(expected_revision=policy.get()["revision"], enabled={"builtin:update_research_map": True})

    # Part A: 同批 1 个合法 + 1 个畸形，整批都不执行，纠偏轮收到全批未执行提示后再提交
    class MixedBatchProvider(BriefReader):
        def stream(self, messages, **kwargs):
            self.requests.append(messages)
            if len(self.requests) == 1:
                yield AIMessageChunk("", tool_call_chunks=[
                    {
                        "name": "update_research_map",
                        "args": json.dumps({
                            "action": "save",
                            "question": "同批合法调用不应先落地",
                            "criteria": "核对",
                            "reason": "混合批次测试",
                        }, ensure_ascii=False),
                        "id": "valid-in-mixed",
                        "index": 0,
                    },
                    {
                        "name": "update_research_map",
                        "args": '{"action": "save", "reason": "bad"}}',
                        "id": "invalid-in-mixed",
                        "index": 1,
                    },
                ])
            elif len(self.requests) == 2:
                # 验证同批那个合法调用绝没有被提前执行
                assert client.get(f"/api/research-tasks/{task['id']}/map").json()["nodes"] == []
                assert "同批所有工具调用均未执行" in messages[0].content
                yield AIMessageChunk("确认同批均未执行，直接总结。")

    services.gateway.enabled, services.gateway.client = True, MixedBatchProvider()
    run1 = submit(client, chat)
    done1 = execute_run(run1["id"], run1["generation"], services=services, checkpointer=InMemorySaver())
    assert done1["status"] == "completed", done1
    assert client.get(f"/api/research-tasks/{task['id']}/map").json()["nodes"] == []

    # Part B: remaining <= 2（finalize=True）叠加 protocol_repair=True 时，tools=[] 且不要求重调工具
    class FinalizeRepairProvider(BriefReader):
        def __init__(self):
            super().__init__()
            self.bound_tool_counts: list[int] = []

        def bind_tools(self, tools):
            self.bound_tool_counts.append(len(tools))
            return self

        def stream(self, messages, **kwargs):
            self.requests.append(messages)
            if len(self.requests) == 1:
                yield AIMessageChunk("", tool_call_chunks=[{
                    "name": "update_research_map",
                    "args": '{"action": "save", "reason": "bad"}}',
                    "id": "bad-near-budget",
                    "index": 0,
                }])
            else:
                system_text = messages[0].content
                # tools=[] 时 gateway 直接调用裸 client 跳过 bind_tools，故调用次数仍为 1。
                assert len(self.bound_tool_counts) == 1
                assert "当前任务额度已进入收尾阶段，不再执行新工具" in system_text
                assert INVALID_TOOL_CALL_REPAIR_FEEDBACK not in system_text
                yield AIMessageChunk("额度收尾，基于已有信息给出结论。")

    fin_provider = FinalizeRepairProvider()
    services.gateway.client = fin_provider
    run2 = client.post(
        f"/api/conversations/{chat}/messages",
        json={"content": "收尾边界测试", "request_key": "finalize-protocol-repair"},
    ).json()
    # 将根运行 model_requests 预算设为 3：第 1 轮耗 1 次后 remaining=2 即触发 finalize=True
    from knowledgeops_agent.persistence.models import Run

    with services.sessions.begin() as session:
        row = session.get(Run, run2["id"])
        budgets = dict(row.budgets)
        budgets["model_requests"] = 3
        row.budgets = budgets
    done2 = execute_run(run2["id"], run2["generation"], services=services, checkpointer=InMemorySaver())
    assert done2["status"] == "completed", done2
    assert len(fin_provider.requests) == 2


def test_disabled_chunk_no_evidence_leak_and_overlap_masking(client, services):
    """P0-2：禁用分块不得写入 Evidence.quote、全排除知识文档不落脏证据、160 字重叠区同样脱敏。"""
    from types import SimpleNamespace

    from sqlalchemy import func, select

    from knowledgeops_agent.harness.runtime.state import RunContext
    from knowledgeops_agent.knowledge.tools import read_knowledge
    from knowledgeops_agent.persistence.models import Evidence
    from knowledgeops_agent.retrieval.tools import search_references
    from knowledgeops_agent.sources.tools import inspect_material, list_evidence, read_source

    src = services.sources.import_text(
        "证据脱敏测试材料",
        "有效正文：支付通道超时需配置连接池隔离。\n\n"
        + ("技术规格填充段落内容。" * 280)
        + "\n\n禁止外泄噪声：行政预订美式咖啡 12 杯与生椰拿铁 8 杯。",
    )
    assert client.post(f"/api/sources/{src['id']}/save").status_code == 200
    src_chunks_url = f"/api/retrieval/assets/source/{src['id']}/chunks"
    src_chunks = client.get(src_chunks_url).json()
    noise_chunk = next(item for item in src_chunks["items"] if "美式咖啡" in item["text"])
    assert client.patch(
        src_chunks_url + "/status",
        json={
            "version_id": src_chunks["version_id"],
            "chunk_index": noise_chunk["chunk_index"],
            "segment_id": noise_chunk["segment_id"],
            "enabled": False,
        },
    ).status_code == 200

    # 1. 单块知识文档全排除 -> 必须抛 PermissionError 且零写入 Evidence
    full_disabled_doc = _publish_doc(services, title="全禁用文档", body="绝密单块内容_ONLY_DISABLED。")
    fd_chunks_url = f"/api/retrieval/assets/knowledge/{full_disabled_doc['id']}/chunks"
    fd_chunks = client.get(fd_chunks_url).json()
    for item in fd_chunks["items"]:
        assert client.patch(
            fd_chunks_url + "/status",
            json={
                "version_id": fd_chunks["version_id"],
                "chunk_index": item["chunk_index"],
                "segment_id": item["segment_id"],
                "enabled": False,
            },
        ).status_code == 200

    # 2. 多块知识文档：把机密口令放在第 0/1 块的 160 字重叠区（约 1120 字处），禁用第 1 块，
    #    验证经由第 0 块召回时重叠区同样被脱敏，绝不写入 Evidence.quote。
    overlap_prefix = "核心检索定位词_OVERLAP_TEST。" + ("正常技术正文段落。" * 123)
    overlap_body = overlap_prefix + "重叠机密口令_XYZ_999。" + ("后续第二块正文内容。" * 60)
    overlap_doc = _publish_doc(services, title="重叠脱敏文档", body=overlap_body)
    ov_chunks_url = f"/api/retrieval/assets/knowledge/{overlap_doc['id']}/chunks"
    ov_chunks = client.get(ov_chunks_url).json()
    assert len(ov_chunks["items"]) >= 2
    secret_chunks = [item for item in ov_chunks["items"] if "重叠机密口令_XYZ_999" in item["text"]]
    assert len(secret_chunks) >= 2, f"期望机密口令落在重叠区，实际仅命中 {len(secret_chunks)} 块"
    target_disable = secret_chunks[-1]
    assert client.patch(
        ov_chunks_url + "/status",
        json={
            "version_id": ov_chunks["version_id"],
            "chunk_index": target_disable["chunk_index"],
            "segment_id": target_disable["segment_id"],
            "enabled": False,
        },
    ).status_code == 200

    conv = services.jobs.create_conversation("证据二次泄露回归")
    run = client.post(
        f"/api/conversations/{conv['id']}/messages",
        json={"content": "核对脱敏与证据落库", "request_key": "evidence-mask-1"},
    ).json()
    services.jobs.claim("evidence-mask-worker", run_id=run["id"], generation=run["generation"])
    context = RunContext(
        run_id=run["id"],
        root_id=run["id"],
        worker_id="evidence-mask-worker",
        jobs=services.jobs,
        knowledge=services.knowledge,
        sources=services.sources,
        gateway=services.gateway,
    )
    rt = SimpleNamespace(context=context, tool_call_id="ev-call-1", state={})

    # 读取材料 -> 验证 API /api/evidence/{id} 与 list_evidence 均不泄露“美式咖啡”
    detail = inspect_material.func(source_id=src["id"], runtime=rt)
    evidence_ids: list[str] = []
    for seg in detail["segments"]:
        try:
            read = read_source.func(segment_id=seg["id"], runtime=rt)
            evidence_ids.append(read["evidence_id"])
            assert "美式咖啡" not in read["text"]
        except PermissionError:
            pass
    assert evidence_ids
    for ev_id in evidence_ids:
        ev_resp = client.get(f"/api/evidence/{ev_id}")
        assert ev_resp.status_code == 200
        assert "美式咖啡" not in ev_resp.json()["text"]
    with services.sessions() as session:
        quotes = list(session.scalars(select(Evidence.quote).where(Evidence.id.in_(evidence_ids))))
    assert quotes and all("美式咖啡" not in q for q in quotes)
    matched = list_evidence.func(query="美式咖啡", runtime=rt)
    assert "美式咖啡" not in json.dumps(matched, ensure_ascii=False)

    # 全禁用知识文档：先计 Evidence 总数，调用 read_knowledge 报错后总数必须不变
    with services.sessions() as session:
        before_count = session.scalar(select(func.count()).select_from(Evidence).where(Evidence.root_id == run["id"]))
    with pytest.raises(PermissionError, match="排除检索"):
        read_knowledge.func(document_id=full_disabled_doc["id"], runtime=rt)
    with services.sessions() as session:
        after_count = session.scalar(select(func.count()).select_from(Evidence).where(Evidence.root_id == run["id"]))
    assert after_count == before_count

    # 重叠区间：search_references 与 search_knowledge 命中相邻启用块时，返回片段与落库 quote 均不可包含已禁用块的“重叠机密口令_XYZ_999”
    from knowledgeops_agent.knowledge.tools import search_knowledge

    ref_res = search_references.func(query="核心检索定位词_OVERLAP_TEST", scope="knowledge", runtime=rt)
    assert ref_res["evidence"]
    for ev in ref_res["evidence"]:
        assert "重叠机密口令_XYZ_999" not in ev.get("text", "")
        assert "重叠机密口令_XYZ_999" not in ev.get("quote", "")
        api_ev = client.get(f"/api/evidence/{ev['evidence_id']}").json()
        assert "重叠机密口令_XYZ_999" not in api_ev["text"]
    kw_hits = search_knowledge.func(query="重叠机密口令_XYZ_999", runtime=rt)
    assert all("重叠机密口令_XYZ_999" not in hit["snippet"] for hit in kw_hits)
    kw_hits2 = search_knowledge.func(query="核心检索定位词_OVERLAP_TEST", runtime=rt)
    assert kw_hits2 and all("重叠机密口令_XYZ_999" not in hit["snippet"] for hit in kw_hits2)


def test_search_knowledge_mounted_collection_no_recall_starvation(client, services):
    """P1-3：挂载知识集时 search_knowledge 下推 SQL 过滤，不被 12 篇高分集外文档挤出 Top-10。"""
    from types import SimpleNamespace

    from knowledgeops_agent.harness.runtime.state import RunContext
    from knowledgeops_agent.knowledge.tools import search_knowledge

    for idx in range(12):
        _publish_doc(
            services,
            title=f"集外高分文档-{idx}",
            body=("雪崩熔断饥饿测试词 " * 8) + f" 编号 {idx}",
        )
    in_doc = _publish_doc(
        services,
        title="集内唯一规范",
        body="本规范包含 雪崩熔断饥饿测试词 供集合内检索。",
    )
    col = client.post("/api/retrieval/collections", json={"name": "饥饿测试集", "description": "仅 1 篇"}).json()
    assert client.put(
        f"/api/retrieval/collections/{col['id']}/items",
        json={
            "expected_revision": col["revision"],
            "items": [{"asset_kind": "knowledge", "asset_id": in_doc["id"]}],
        },
    ).status_code == 200

    conv = services.jobs.create_conversation("召回饥饿回归")
    run = client.post(
        f"/api/conversations/{conv['id']}/messages",
        json={
            "content": "检索集内文档",
            "request_key": "starvation-1",
            "rag_scope": "collections",
            "collection_ids": [col["id"]],
        },
    ).json()
    services.jobs.claim("starvation-worker", run_id=run["id"], generation=run["generation"])
    context = RunContext(
        run_id=run["id"],
        root_id=run["id"],
        worker_id="starvation-worker",
        jobs=services.jobs,
        knowledge=services.knowledge,
        sources=services.sources,
        gateway=services.gateway,
    )
    rt = SimpleNamespace(context=context, tool_call_id="starve-1", state={})
    hits = search_knowledge.func(query="雪崩熔断饥饿测试词", runtime=rt)
    assert [h["document_id"] for h in hits] == [in_doc["id"]]


def test_tei_reranker_batches_large_candidate_lists(monkeypatch):
    """P0-3：TEIRerankerProvider 对超过 max_batch_size 的候选自动分批并合并全局排序。"""
    import httpx

    from knowledgeops_agent.retrieval.embeddings import TEIRerankerProvider

    batch_sizes: list[int] = []

    class FakeResponse:
        def __init__(self, payload):
            self._payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    def fake_post(self, url, *, json=None, headers=None):
        assert url == "http://tei-reranker:80/rerank"
        assert json["truncate"] is True
        texts = json["texts"]
        batch_sizes.append(len(texts))
        # 按文档编号构造可预测分数：doc-24 最高（0.99），doc-10 次高（0.95），其余按索引递增
        payload = []
        for local_idx, text in enumerate(texts):
            doc_num = int(text.split("-")[1])
            score = 0.99 if doc_num == 24 else (0.95 if doc_num == 10 else doc_num * 0.01)
            payload.append({"index": local_idx, "score": score})
        return FakeResponse(payload)

    monkeypatch.setattr(httpx.Client, "post", fake_post)
    provider = TEIRerankerProvider(base_url="http://tei-reranker:80", max_batch_size=8)
    ranked = provider.rerank("测试查询", [f"doc-{i}" for i in range(25)], top_n=5)
    assert batch_sizes == [8, 8, 8, 1]
    assert ranked[0] == (24, 0.99)
    assert ranked[1] == (10, 0.95)
    assert len(ranked) == 5


def test_research_manual_knowledge_only_blocks_materials_and_runs_page_excludes_kind(client, services):
    """P1-4 & P1-6：研发会话手动开启 knowledge_only 仍拒绝材料；/api/runs/page 支持 SQL 级 exclude_kind。"""
    from datetime import UTC, datetime, timedelta
    from types import SimpleNamespace

    from knowledgeops_agent.harness.runtime.resources import prepare_resources
    from knowledgeops_agent.harness.runtime.state import RunContext
    from knowledgeops_agent.persistence.models import Run
    from knowledgeops_agent.retrieval.tools import search_references

    src = services.sources.import_text("研发本地材料", "熔断阈值 15% 仅供自主研发阶段查阅。")
    assert client.post(f"/api/sources/{src['id']}/save").status_code == 200
    chat, _task = create_task(client)
    policy = ToolPolicyService(services.sessions)
    policy.update(expected_revision=policy.get()["revision"], enabled={"builtin:search_references": True})

    # 手动发起消息且勾选仅用知识库（origin="human"）
    manual_run = client.post(
        f"/api/conversations/{chat}/messages",
        json={"content": "仅查正式知识库", "request_key": "manual-ko-1", "knowledge_only": True},
    ).json()
    assert manual_run["origin"] == "human"
    services.jobs.claim("manual-ko-worker", run_id=manual_run["id"], generation=manual_run["generation"])
    context = RunContext(
        run_id=manual_run["id"],
        root_id=manual_run["id"],
        worker_id="manual-ko-worker",
        jobs=services.jobs,
        knowledge=services.knowledge,
        sources=services.sources,
        gateway=services.gateway,
    )
    prepare_resources(context)
    rt = SimpleNamespace(context=context, tool_call_id="manual-ko-ref", state={})
    with pytest.raises(PermissionError, match="读取许可"):
        search_references.func(query="熔断阈值", scope="material", runtime=rt)

    # P1-6：写入一条更新时间戳的 evolution_review 运行，验证 exclude_kind 在 SQL 分页层过滤
    with services.sessions.begin() as session:
        agent_row = session.get(Run, manual_run["id"])
        evo_id = "evo-review-page-test-1"
        session.add(
            Run(
                id=evo_id,
                root_id=evo_id,
                parent_id=None,
                conversation_id=chat,
                checkpoint_id=evo_id,
                kind="evolution_review",
                origin="automatic",
                status="completed",
                lifecycle_state="active",
                generation=0,
                task="后台复盘",
                input_data={},
                budgets=agent_row.budgets,
                outcome={"kind": "evolution_review"},
                created_at=datetime.now(UTC) + timedelta(seconds=10),
            )
        )
    page_all = client.get(f"/api/runs/page?conversation_id={chat}&roots_only=true&limit=1").json()
    assert page_all["items"][0]["kind"] == "evolution_review"
    page_filtered = client.get(
        f"/api/runs/page?conversation_id={chat}&roots_only=true&exclude_kind=evolution_review&limit=1"
    ).json()
    assert len(page_filtered["items"]) == 1
    assert page_filtered["items"][0]["id"] == manual_run["id"]
    assert page_filtered["items"][0]["kind"] != "evolution_review"
