"""供资料问答使用的联合检索工具，沿用材料和知识的读取授权。"""

from langchain.tools import ToolRuntime, tool

from knowledgeops_agent.harness.runtime.state import RunContext
from knowledgeops_agent.harness.tools.access import allowed_local_ids
from knowledgeops_agent.retrieval.collections import mounted_reference_scope
from knowledgeops_agent.retrieval.references import ReferenceScope
from knowledgeops_agent.retrieval.references import search_references as find_references

_CANDIDATES = 10
_READ_LIMIT = 4


@tool
def search_references(
    query: str, runtime: ToolRuntime[RunContext], scope: ReferenceScope = "all",
) -> dict:
    """资料问答的首选入口。默认联合检索已发布知识和已导入材料，返回已读证据。

    后台支持关键词（BM25）与语义向量（pgvector，启用并构建索引时自动生效）的 RRF 混合检索
    及可选重排，可直接用自然语言问句、同义改写或跨语言表述查询。定位相关内容时应优先调用
    本工具，再按需补读上下文，避免先逐篇通读全部材料。用户限定只查知识或只查原始材料时
    分别用 scope=knowledge 或 material。当前会话选定的材料范围和本次授权由程序核验。
    回答时紧邻具体论断使用返回的完整 citation；evidence 为空时可换表述/关键词重试或说明资料不足。
    """
    context = runtime.context
    root = context.jobs.guard(context.run_id, worker_id=context.worker_id)
    allowed = allowed_local_ids(context)
    include_knowledge = scope != "material" and {
        "builtin:search_knowledge", "builtin:read_knowledge",
    } <= allowed
    auto_research = root.get("origin") == "automatic" and bool(root["input"].get("research"))
    include_material = (
        scope != "knowledge"
        and (not root["input"].get("knowledge_only") or auto_research)
        and "builtin:read_source" in allowed
    )
    if not include_knowledge and not include_material:
        raise PermissionError("本次运行没有所选资料范围的读取许可")
    if not include_knowledge:
        scope = "material"
    elif not include_material:
        scope = "knowledge"
    with context.jobs.sessions() as session:
        document_ids, source_ids = mounted_reference_scope(session, root["input"])
    candidates = find_references(
        context.knowledge, context.sources, query, root_id=context.root_id, scope=scope,
        source_ids=source_ids, document_ids=document_ids, limit=_CANDIDATES, vector=context.vector,
    )
    context.jobs.append_event(context.run_id, "reference_search", {
        "tool_call_id": runtime.tool_call_id,
        "scope": scope,
        "candidates": [
            {key: row[key] for key in ("kind", "asset_id", "version_id", "start", "end")}
            for row in candidates
        ],
    })
    evidence = []
    for hit in candidates:
        try:
            if hit["kind"] == "knowledge":
                row = context.knowledge.read_document(
                    hit["asset_id"], root_id=context.root_id, version_id=hit["version_id"],
                    start=hit["start"], length=hit["end"] - hit["start"], current_only=True,
                    mask_disabled=True,
                )
            else:
                row = context.sources.read_search_hit(
                    hit["segment_id"], root_id=context.root_id, version_id=hit["version_id"],
                    start=hit["start"], end=hit["end"], source_ids=source_ids,
                    mask_disabled=True,
                )
        except (KeyError, ValueError, PermissionError):
            # 候选可能在查询与读取之间被撤回或排除检索；其余来源仍可提供证据。
            continue
        evidence.append({
            "kind": hit["kind"], "asset_id": hit["asset_id"], "version_id": hit["version_id"],
            **{key: row[key] for key in ("evidence_id", "title", "start", "end", "text", "citation")},
        })
        if len(evidence) == _READ_LIMIT:
            break
    return {"scope": scope, "candidate_count": len(candidates), "evidence": evidence}
