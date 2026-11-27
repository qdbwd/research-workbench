"""仅给已绑定研发的主图提供经过验证的研究焦点转换。"""

from typing import Annotated, Any, Literal

from langchain.tools import ToolRuntime, tool
from pydantic import Field

from knowledgeops_agent.app.conversations import fingerprint
from knowledgeops_agent.app.runs.children import unsettled_children
from knowledgeops_agent.persistence.models import ResearchRevision
from knowledgeops_agent.research.autonomy import active_permit
from knowledgeops_agent.research.contracts import (
    DeliveryContent,
    Identity,
    NodeStatus,
    RemoveResearchNode,
    ResearchCapability,
    ResearchNode,
    ResearchPhase,
    ResearchRelation,
    RetrievalDataset,
    SaveResearchNode,
)
from knowledgeops_agent.research.execution import active_binding
from knowledgeops_agent.research.methods import canonical_capabilities
from knowledgeops_agent.research.service import _append, _read_receipt, _save_receipt


@tool
def propose_research_experiment(
    question: Annotated[str, Field(min_length=8, max_length=500)],
    hypothesis: Annotated[str, Field(min_length=8, max_length=1500)],
    dataset: RetrievalDataset,
    evidence_ids: Annotated[list[Identity], Field(max_length=20)],
    runtime: ToolRuntime[Any],
) -> dict:
    """提出固定文档问答重排实验。使用有依据的候选、最终上下文、原回答与预期答案。

    工具只固定输入和假设。等待用户核对并批准。切勿把提出或排队视为已执行的实验结果。
    """
    from knowledgeops_agent.research.experiments import propose

    context = runtime.context
    if not runtime.tool_call_id:
        raise ValueError("实验提案需要显式工具调用身份。")
    return propose(context.jobs, context.run_id, context.worker_id, runtime.tool_call_id,
                   question, hypothesis, dataset, evidence_ids)


@tool
def read_research_experiment(attempt_id: Identity, runtime: ToolRuntime[Any]) -> dict:
    """读取当前主线一项实验及真实结果。只有状态 completed 和对应证据允许纳入成果。"""
    from knowledgeops_agent.research.experiments import _visible, view

    context = runtime.context
    with context.jobs.sessions.begin() as session:
        root, _ = context.jobs.execution_guard(session, context.run_id, context.worker_id)
        binding = root.input_data.get("research")
        if not binding:
            raise PermissionError("本运行未绑定研发任务。")
        branch = active_binding(session, binding)
        return view(_visible(session, branch, attempt_id))


@tool
def read_research_map(
    runtime: ToolRuntime[Any],
    node_id: Identity | None = None,
    offset: Annotated[int, Field(ge=0)] = 0,
) -> dict:
    """查看当前固定研究地图的目录，或按稳定节点 ID 补读结论、证据和判断理由。"""
    from knowledgeops_agent.research.evidence import views
    from knowledgeops_agent.research.maps import load

    context = runtime.context
    with context.jobs.sessions.begin() as session:
        root, _ = context.jobs.execution_guard(session, context.run_id, context.worker_id)
        binding = root.input_data.get("research")
        if not binding:
            raise PermissionError("本运行未绑定研发任务。")
        revision = session.get(ResearchRevision, binding["research_revision_id"])
        nodes = load(session, revision)
        if node_id is not None:
            if node_id not in nodes:
                raise KeyError("没有找到该研究主题。")
            node = nodes[node_id]
            return {"revision_id": revision.id, "node": {
                **node, "evidence": views(session, node["evidence_ids"], task_id=binding["task_id"]),
            }}
        keys = ("id", "parent_id", "question", "status", "required")
        entries = [{key: node[key] for key in keys} for node in
                   sorted(nodes.values(), key=lambda node: (node["question"], node["id"]))]
        return {"revision_id": revision.id, "nodes": entries[offset:offset + 20],
                "next_offset": offset + 20 if len(entries) > offset + 20 else None}


@tool
def update_research_map(
    action: Literal["save", "remove"],
    reason: Annotated[str, Field(min_length=1, max_length=1000)],
    runtime: ToolRuntime[Any],
    node_id: Identity | None = None,
    parent_id: Identity | None = None,
    question: Annotated[str | None, Field(min_length=3, max_length=300)] = None,
    criteria: Annotated[str | None, Field(min_length=1, max_length=1200)] = None,
    status: NodeStatus = "open",
    conclusion: Annotated[str, Field(max_length=4000)] = "",
    details: Annotated[str, Field(max_length=4000)] = "",
    node_reason: Annotated[str, Field(max_length=1200)] = "",
    evidence_ids: Annotated[list[Identity] | None, Field(max_length=12)] = None,
    required: bool = True,
    related: Annotated[list[ResearchRelation] | None, Field(max_length=12)] = None,
) -> dict:
    """更新主线的一个主题。必须单独调用，直接传平铺参数（切勿外包 operation 或 node 嵌套对象）。

    新增或修改主题传 action="save"，并填写 question、criteria、status 及本次更新 reason；
    修改既有主题需传 node_id；挂载子节点传 parent_id；删除主题传 action="remove" 与 node_id。
    已解决节点（status="resolved"）必须给出 node_reason、conclusion 及本任务的已读 evidence_ids。
    与父主题无关的可选探索将 required 设为 false。保存后返回新版本，其他动作按新版重新判断。
    """
    from knowledgeops_agent.app.lifecycle.asset_references import replace_asset_references
    from knowledgeops_agent.app.lifecycle.catalog import reference_value
    from knowledgeops_agent.persistence.models import ResearchTask
    from knowledgeops_agent.research.maps import apply

    context = runtime.context
    reason = reason.strip()
    if context.run_id != context.root_id or not runtime.tool_call_id or not reason:
        raise ValueError("仅主运行可更新研究地图，并须说明理由。")
    if action == "remove":
        if not node_id:
            raise ValueError("删除研究主题必须提供 node_id。")
        operation = RemoveResearchNode(action="remove", node_id=node_id)
    else:
        if not question or not criteria:
            raise ValueError("保存研究主题必须提供 question 与 criteria。")
        operation = SaveResearchNode(
            action="save",
            node=ResearchNode(
                id=node_id,
                parent_id=parent_id,
                required=required,
                question=question,
                status=status,
                criteria=criteria,
                reason=node_reason,
                conclusion=conclusion,
                details=details,
                evidence_ids=evidence_ids or [],
                related=related or [],
            ),
        )
    body = operation.model_dump()
    key = "research:tool-map:" + fingerprint([context.run_id, runtime.tool_call_id])
    request_hash = fingerprint([body, reason])
    with context.jobs.sessions.begin() as session:
        root, _ = context.jobs.execution_guard(session, context.run_id, context.worker_id)
        binding = root.input_data.get("research")
        if not binding or binding.get("review_only"):
            raise PermissionError("当前运行不能更新研究地图。")
        prior = _read_receipt(session, key, request_hash)
        if prior is not None:
            return prior
        branch = active_binding(session, binding)
        task = session.get(ResearchTask, branch.task_id)
        if unsettled_children(session, root.id):
            raise ValueError("请先接收子任务结果，再更新它们所依据的研究版本。")
        result = apply(session, task, branch, operation, reason, actor="assistant")
        head_id = result["map"]["revision_id"]
        root.input_data = {**root.input_data, "research": {**binding, "research_revision_id": head_id}}
        replace_asset_references(session, "run_input", root.id, reference_value(session, root))
        return _save_receipt(session, key, request_hash, {"revision_id": head_id, "updated": True})


@tool
def submit_research_delivery(
    title: Annotated[str, Field(min_length=1, max_length=200)],
    diagnosis: Annotated[str, Field(min_length=1, max_length=12000)],
    recommendations: Annotated[str, Field(min_length=1, max_length=12000)],
    limitations: Annotated[str, Field(max_length=4000)],
    source_version_ids: Annotated[list[str], Field(max_length=30)],
    runtime: ToolRuntime[Any],
    evidence_ids: Annotated[list[Identity] | None, Field(max_length=30)] = None,
) -> dict:
    """固定提交本轮研发成果，请用户验收。必须单独调用。

    直接传平铺参数（title、diagnosis、recommendations、limitations、source_version_ids、evidence_ids），
    切勿外包一层 delivery 嵌套对象。diagnosis 与 recommendations 保持精炼结构化（建议各在千字内），
    详细推导留在研究地图中，并列出已核对过的证据 ID 与关联材料版本。交付触发收尾并停止自主推进；
    只在约定成果准备好时使用，不适用于阶段性进展或普通答复。
    """
    from knowledgeops_agent.research.deliveries import submit

    context = runtime.context
    if context.run_id != context.root_id or not runtime.tool_call_id:
        raise PermissionError("只有研发主运行可以提交固定成果。")
    content = DeliveryContent(
        title=title, diagnosis=diagnosis, recommendations=recommendations,
        limitations=limitations, source_version_ids=source_version_ids, evidence_ids=evidence_ids or [],
    ).model_dump()
    return submit(context.jobs, context.run_id, context.worker_id, runtime.tool_call_id, content)


@tool
def continue_research(
    next_action: Annotated[str, Field(min_length=1, max_length=2000)],
    runtime: ToolRuntime[Any],
) -> dict:
    """记录确有必要在下一轮独立推进的具体工作。仅在存在用户自主授权时使用。

    记录后给出本轮结果并结束。需要用户回答、审批或修订范围时不要调用。
    这不是立即执行、增加额度或批准实验的操作。
    """
    context = runtime.context
    action = next_action.strip()
    if context.run_id != context.root_id or not runtime.tool_call_id or not action:
        raise ValueError("仅研发主运行可记录明确的续跑步骤。")
    key = "research:next:" + fingerprint([context.run_id, runtime.tool_call_id])
    request_hash = fingerprint(action)
    with context.jobs.sessions.begin() as session:
        root, _ = context.jobs.execution_guard(session, context.run_id, context.worker_id)
        if active_permit(session, root) is None:
            raise ValueError("当前没有用户确认的自主授权。")
        prior = _read_receipt(session, key, request_hash)
        if prior is not None:
            return prior
        binding = root.input_data["research"]
        existing = root.input_data.get("research_next")
        if existing and existing["revision_id"] == binding["research_revision_id"]:
            raise ValueError("当前运行已记录过下一步，请先给出本轮结果。")
        intent = {
            "action": action, "revision_id": binding["research_revision_id"],
            "control_version": binding["control_version"],
        }
        root.input_data = {**root.input_data, "research_next": intent}
        return _save_receipt(session, key, request_hash, {"recorded": True, **intent})


@tool
def set_research_focus(
    phase: ResearchPhase,
    capabilities: Annotated[list[ResearchCapability], Field(max_length=3)],
    reason: Annotated[str, Field(min_length=1, max_length=1000)],
    runtime: ToolRuntime[Any],
) -> dict:
    """在探索、调研、验证或综合阶段之间调整焦点，加载下一步所需的专业方法。

    只在阶段或方法实质改变时单独调用。不要与其他工具同批。不能覆盖用户固定的方法，
    不启动后台任务、不改变授权或预算。需要真人决定时先独立补问。
    """
    context = runtime.context
    if context.run_id != context.root_id or not runtime.tool_call_id:
        raise PermissionError("只有研发主运行可调整研究焦点。")
    active = canonical_capabilities(capabilities)
    reason = reason.strip()
    if not reason:
        raise ValueError("请说明调整研究焦点的理由。")
    key = "research:run-focus:" + fingerprint([context.run_id, runtime.tool_call_id])
    request_hash = fingerprint([phase, active, reason])
    with context.jobs.sessions.begin() as session:
        root, _ = context.jobs.execution_guard(session, context.run_id, context.worker_id)
        binding = root.input_data.get("research")
        if not binding:
            raise PermissionError("本运行未绑定研发任务。")
        prior = _read_receipt(session, key, request_hash)
        if prior is not None:
            return prior
        branch = active_binding(session, binding)
        head = session.get(ResearchRevision, branch.head_revision_id)
        override = head.state.get("capability_override")
        if override is not None and override != active:
            raise PermissionError("用户已固定专业方法，请保持当前选择。")
        if head.state["phase"] != phase or head.state["active_capabilities"] != active:
            if unsettled_children(session, root.id):
                raise ValueError("先收齐当前子任务或明确取消，再调整研究焦点。")
            head = _append(
                session, branch, {
                    **head.state, "phase": phase, "active_capabilities": active,
                    "capability_source": "user" if override is not None else "assistant",
                    "capability_reason": reason,
                },
                parent=head, reason=reason, origin="model_focus", actor="assistant",
            )
            root.input_data = {
                **root.input_data, "research": {**binding, "research_revision_id": head.id},
            }
            from knowledgeops_agent.app.lifecycle.asset_references import (
                replace_asset_references,
            )
            from knowledgeops_agent.app.lifecycle.catalog import reference_value

            replace_asset_references(session, "run_input", root.id, reference_value(session, root))
        return _save_receipt(session, key, request_hash, {
            "phase": phase, "capabilities": active, "revision_id": head.id,
        })
