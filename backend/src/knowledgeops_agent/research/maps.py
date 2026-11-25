"""固定研究地图、版本对比及经用户确认的节点采纳。"""

from copy import deepcopy

from sqlalchemy import select, union_all
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import aliased

from knowledgeops_agent.app.conversations import fingerprint
from knowledgeops_agent.app.runs.control import lock_control
from knowledgeops_agent.persistence.models import (
    Evidence,
    KnowledgeDocument,
    ResearchBranch,
    ResearchMap,
    ResearchRevision,
    Run,
    RunArtifact,
    Source,
    new_id,
)
from knowledgeops_agent.research import evidence
from knowledgeops_agent.research.deliveries import for_task
from knowledgeops_agent.research.service import (
    ResearchConflict,
    _append,
    _read_receipt,
    _save_receipt,
    require_writable_branch,
    task_view,
)

MAX_NODES = 80


def load(session, revision):
    identity = revision.state.get("map_ref")
    if identity is None:
        return {}
    manifest = session.get(ResearchMap, identity)
    if manifest is None or fingerprint(manifest.content) != identity:
        raise ValueError("固定研究地图已丢失或校验失败。")
    return manifest.content["nodes"]


def _node_view(node, known):
    return {**node, "evidence": [known[identifier] for identifier in node["evidence_ids"]]}


def _map_view(session, revision, nodes=None):
    nodes = load(session, revision) if nodes is None else nodes
    identifiers = list(dict.fromkeys(identifier for node in nodes.values() for identifier in node["evidence_ids"]))
    branch = session.get(ResearchBranch, revision.branch_id)
    by_id = {item["id"]: item for item in evidence.views(session, identifiers, task_id=branch.task_id)}
    return {
        "revision_id": revision.id, "number": revision.number,
        "node_adoptions": revision.state.get("node_adoptions", {}),
        "nodes": [_node_view(node, by_id) for node in
                  sorted(nodes.values(), key=lambda node: (node["question"], node["id"]))],
    }


def _fixed(session, branch, revision_id):
    row = session.get(ResearchRevision, revision_id)
    if row is None or row.branch_id != branch.id:
        raise KeyError("没有找到该主线的固定研究版本。")
    return row


def get(sessions, task_id, revision_id=None):
    with sessions() as session:
        revision = session.get(ResearchRevision, revision_id) if revision_id else None
        if revision_id and revision is None:
            raise KeyError("没有找到该研发的固定研究版本。")
        _, branch = for_task(session, task_id, revision.branch_id if revision else None)
        return _map_view(session, _fixed(session, branch, revision_id or branch.head_revision_id))


def evidence_catalog(sessions, task_id, offset, limit):
    with sessions() as session:
        task, branch = for_task(session, task_id)
        bound = Run.input_data["research"]
        scope = (bound["task_id"].as_string() == task.id, bound["branch_id"].as_string() == branch.id)
        observed = select(Evidence.id, Evidence.created_at).join(
            Run, Run.id == Evidence.root_id,
        ).where(*scope)
        owner = aliased(Run)
        excerpts = select(RunArtifact.id, RunArtifact.created_at).join(
            owner, owner.id == RunArtifact.run_id,
        ).join(Run, Run.id == owner.root_id).where(
            *scope, RunArtifact.kind == "research_excerpt", RunArtifact.expired_at.is_(None),
        )
        combined = union_all(observed, excerpts).subquery()
        rows = list(session.execute(select(combined).order_by(
            combined.c.created_at.desc(), combined.c.id.desc(),
        ).offset(offset).limit(limit + 1)))
        return {
            "items": evidence.views(session, [row.id for row in rows[:limit]], task_id=task.id),
            "next_offset": offset + limit if len(rows) > limit else None,
        }


def read_excerpt(sessions, task_id, identifier):
    with sessions() as session:
        _, branch = for_task(session, task_id)
        pinned = session.scalar(select(ResearchRevision.id).join(
            ResearchBranch, ResearchBranch.id == ResearchRevision.branch_id,
        ).where(
            ResearchBranch.task_id == task_id,
            ResearchRevision.state["provenance_refs"].contains([{"artifact_id": identifier}]),
        ).limit(1))
        return evidence.excerpt_content(session, identifier, branch, [identifier] if pinned else [])


def _check_head(task, branch, body):
    require_writable_branch(branch)
    if (task.control_version, branch.head_revision_id) != (body.expected_control_version, body.expected_revision_id):
        raise ResearchConflict("map_conflict", "目标分支或任务状态已更新，请核对最新版。", task=task, branch=branch)
    if task.status != "open" or branch.pending_delivery_id:
        raise ResearchConflict("map_unavailable", "当前研发状态暂不能修改研究地图。")


def _validate(nodes, edited_id):
    if len(nodes) > MAX_NODES:
        raise ValueError(f"当前地图最多保存 {MAX_NODES} 个节点。")
    for identifier, node in nodes.items():
        parent = node["parent_id"]
        path = {identifier}
        while parent:
            if parent in path or parent not in nodes:
                raise ValueError("主题的父节点不存在，或会形成环。")
            path.add(parent)
            parent = nodes[parent]["parent_id"]
        targets = [edge["target_id"] for edge in node["related"]]
        if len(set(targets)) != len(targets) or any(target == identifier or target not in nodes for target in targets):
            raise ValueError("请调整关联主题。关联目标必须存在、互不重复且不能指向自己。")
        if node["status"] == "resolved" and not (node["reason"] and node["conclusion"] and node["evidence_ids"]):
            raise ValueError("解决节点前请提供结论、判断理由和已核对证据。")

    # 必要子主题被重开时，把受影响的上层判断置为待复核，保留旧结论和历史。
    missing = [node for node in nodes.values() if node["status"] != "resolved" and node["required"]]
    while missing:
        node = missing.pop()
        parent = nodes.get(node["parent_id"])
        if parent and parent["status"] == "resolved":
            if parent["id"] == edited_id:
                raise ValueError("必要子主题仍未解决，请先核对依赖。")
            parent["status"] = "needs_review"
            if parent["required"]:
                missing.append(parent)


def _write(session, task, branch, head, nodes, reason, *, actor, origin, adoption=None):
    content = {"schema_version": 1, "nodes": nodes}
    identity = fingerprint(content)
    session.execute(insert(ResearchMap).values(id=identity, content=content).on_conflict_do_nothing())
    references = sorted({identifier for node in nodes.values() for identifier in node["evidence_ids"]})
    preserved = [ref for ref in head.state.get("provenance_refs", []) if ref.get("kind") != "map_evidence"]
    state = {
        **head.state, "map_ref": identity, "provenance_refs": [
            *preserved, *({"kind": "map_evidence", **evidence.projection(session, identifier)} for identifier in references),
        ],
    }
    records = {key: entry for key, entry in state.get("node_adoptions", {}).items() if key in nodes}
    if adoption:
        state["adopted_from"] = adoption
        if adoption.get("node_id"):
            records[adoption["node_id"]] = adoption
    if records or "node_adoptions" in state:
        state["node_adoptions"] = records
    new = _append(session, branch, state, parent=head, reason=reason, origin=origin, actor=actor)
    writing = branch if task.writing_branch_id == branch.id else session.get(ResearchBranch, task.writing_branch_id)
    current = new if writing.id == branch.id else session.get(ResearchRevision, writing.head_revision_id)
    return {"task": task_view(task, current, writing), "map": _map_view(session, new, nodes)}


def apply(session, task, branch, operation, reason, *, actor):
    head = _fixed(session, branch, branch.head_revision_id)
    nodes = deepcopy(load(session, head))
    if operation.action == "remove":
        identifier = operation.node_id
        if identifier not in nodes:
            raise KeyError("没有找到该研究主题。")
        del nodes[identifier]
    else:
        node = operation.node.model_dump()
        identifier = node["id"] or new_id()
        if node["id"] and identifier not in nodes:
            raise KeyError("没有找到该研究主题。")
        prior = nodes.get(identifier)
        node["id"] = identifier
        node["evidence_ids"] = list(dict.fromkeys(node["evidence_ids"]))
        for evidence_id in node["evidence_ids"]:
            evidence.scope_checked(session, evidence_id, branch, (prior or {}).get("evidence_ids", []))
        nodes[identifier] = node
    _validate(nodes, identifier)
    return _write(session, task, branch, head, nodes, reason, actor=actor, origin="map_edit")


def edit(sessions, task_id, body):
    key = "research:map:" + fingerprint([task_id, body.operation_id])
    request_hash = fingerprint(body.model_dump(exclude={"operation_id"}))
    with sessions.begin() as session:
        lock_control(session)
        prior = _read_receipt(session, key, request_hash)
        if prior is not None:
            return prior
        task, branch = for_task(session, task_id)
        _check_head(task, branch, body)
        result = apply(session, task, branch, body.operation, body.reason, actor="user")
        return _save_receipt(session, key, request_hash, result)


def _differences(before, after):
    if before is None:
        return ["added"]
    if after is None:
        return ["removed"]
    categories = (
        ("moved", ("parent_id",)), ("conclusion", ("conclusion", "reason")),
        ("status", ("status",)), ("evidence", ("evidence_ids",)),
        ("description", ("question", "criteria", "details", "required", "related")),
    )
    return [label for label, fields in categories if any(before[field] != after[field] for field in fields)]


def compare(sessions, task_id, before, after):
    with sessions() as session:
        revision = session.get(ResearchRevision, before)
        if revision is None:
            raise KeyError("没有找到该研发的固定研究版本。")
        _, branch = for_task(session, task_id, revision.branch_id)
        older = _map_view(session, _fixed(session, branch, before))
        newer = _map_view(session, _fixed(session, branch, after))
        return diff_maps(older, newer)


def diff_maps(older, newer):
    a, b = ({node["id"]: node for node in side["nodes"]} for side in (older, newer))
    changes = [{"id": identifier, "types": kinds}
               for identifier in sorted(a.keys() | b.keys())
               if (kinds := _differences(a.get(identifier), b.get(identifier)))]
    return {"before": older, "after": newer, "changes": changes}


def _adoption(session, task, branch, body, *, from_commit):
    _check_head(task, branch, body)
    commit = None
    if from_commit:
        from knowledgeops_agent.research.commits import _commit

        commit = _commit(session, task, body.source_commit_id)
        source = session.get(ResearchRevision, commit.revision_id)
    else:
        source = _fixed(session, branch, body.source_revision_id)
    target = _fixed(session, branch, branch.head_revision_id)
    if target.id == source.id:
        raise ValueError("请选择目标分支当前进展以外的固定版本。")
    target_nodes = load(session, target)
    nodes = deepcopy(target_nodes)
    source_nodes = load(session, source)
    old = source_nodes.get(body.node_id)
    current = nodes.get(body.node_id)
    if not old or (current is None and commit is None):
        raise ValueError("所选版本未包含可采纳的主题。")
    if current is not None and not old["conclusion"]:
        raise ValueError("历史节点没有可以采纳的结论。")
    if current is None:
        if body.target_parent_id is not None and body.target_parent_id not in nodes:
            raise ValueError("请在目标分支选择有效的上级主题。")
        restored = {**old, "parent_id": body.target_parent_id, "related": [], "status": "needs_review"}
    else:
        restored = {**current, **{key: old[key] for key in ("conclusion", "reason", "evidence_ids")},
                    "status": "needs_review"}
    reviewed = []
    for identifier in old["evidence_ids"]:
        detail = evidence.scope_checked(session, identifier, branch, old["evidence_ids"])
        if commit:
            owner = None
            if detail.get("source_id"):
                owner = session.get(Source, detail["source_id"])
            elif detail.get("document_id"):
                owner = session.get(KnowledgeDocument, detail["document_id"])
            if owner is not None and owner.deleted_at is not None:
                raise ResearchConflict("adoption_evidence_unavailable", "固定证据已撤回，无法采纳到新进展。")
        reviewed.append(detail)
    nodes[body.node_id] = restored
    _validate(nodes, body.node_id)
    digest = [source.id, target.id, task.control_version, restored]
    proposal = {
        "source_revision_id": source.id, "target_revision_id": target.id, "node_id": body.node_id,
        "before": current, "after": restored, "evidence": reviewed,
    }
    origin = {"research_revision_id": source.id}
    if commit:
        reopened = [{"id": key, "question": child["question"]} for key, child in nodes.items()
                    if key != body.node_id and child["status"] != target_nodes[key]["status"]]
        digest.extend((commit.id, branch.id, reopened))
        origin.update(research_commit_id=commit.id, node_id=body.node_id)
        proposal.update(
            source_commit_id=commit.id, source_commit_message=commit.message,
            target_branch_id=branch.id, target_branch_name=branch.name, target_revision_number=target.number,
            target_parent_title=nodes.get(restored["parent_id"], {}).get("question"),
            reopened_ancestors=reopened,
            omitted_relations=[{**relation, "question": source_nodes[relation["target_id"]]["question"]}
                               for relation in old["related"]] if current is None else [],
        )
    return nodes, {**proposal, "preview_hash": fingerprint(digest)}, origin


def preview(sessions, task_id, body, *, from_commit=False):
    with sessions() as session:
        task, branch = for_task(session, task_id, body.target_branch_id if from_commit else None)
        _, proposal, _ = _adoption(session, task, branch, body, from_commit=from_commit)
        return proposal


def adopt(sessions, task_id, body, *, from_commit=False):
    key = ("research:commit-adopt:" if from_commit else "research:adopt:") + fingerprint([task_id, body.operation_id])
    request_hash = fingerprint(body.model_dump(exclude={"operation_id"}))
    with sessions.begin() as session:
        lock_control(session)
        prior = _read_receipt(session, key, request_hash)
        if prior is not None:
            return prior
        task, branch = for_task(session, task_id, body.target_branch_id if from_commit else None)
        nodes, proposal, origin = _adoption(session, task, branch, body, from_commit=from_commit)
        if proposal["preview_hash"] != body.preview_hash:
            raise ResearchConflict("adoption_conflict", "预览已变化，请复核后再次确认。")
        head = _fixed(session, branch, branch.head_revision_id)
        result = _write(session, task, branch, head, nodes, body.reason, actor="user", origin="map_adopt",
                        adoption=origin)
        return _save_receipt(session, key, request_hash, result)
