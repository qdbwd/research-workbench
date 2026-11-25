"""研究证据和知识版本由真实读取生成，并可经固定主线历史比较和采纳。"""

import json

from langchain_core.messages import AIMessageChunk, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from test_research_execution import BriefReader, create_task, submit
from test_research_execution import client as research_client
from test_research_execution import services as research_services

from knowledgeops_agent.app.runs.worker import execute_run
from knowledgeops_agent.harness.tools.policy import ToolPolicyService
from knowledgeops_agent.persistence.models import Evidence, KnowledgeDocument, KnowledgeVersion

client = research_client
services = research_services


def update_node(client, task, nonce, node):
    base = f"/api/research-tasks/{task['id']}/map"
    return client.post(base, json={
        "operation_id": nonce, "expected_control_version": task["control_version"],
        "expected_revision_id": task["head"]["id"], "reason": "核对当前研究证据",
        "operation": {"action": "save", "node": node},
    })


def test_adopt_new_node_from_fixed_commit_into_explicit_branch(client, services):
    from knowledgeops_agent.persistence.models import AssetReference, utcnow

    conversation = client.post("/api/conversations", json={"trial_case": "operations-pilot-v1"}).json()
    chat = conversation["id"]
    task = client.post(f"/api/conversations/{chat}/research-task", json={
        "operation_id": "pilot", "goal": "评估运维问答助手的内部试点",
    }).json()
    root = f"/api/research-tasks/{task['id']}"
    main = f"{root}/branches/{task['main_branch_id']}"

    def commit(key):
        preview = client.get(main + "/commit-preview").json()
        response = client.post(main + "/commits", json={
            "operation_id": key, "message": key, "preview_hash": preview["preview_hash"],
        })
        assert response.status_code == 201, response.text
        return response.json()

    run = submit(client, chat)
    with services.sessions.begin() as session:
        session.add(KnowledgeDocument(id="pilot-evidence-doc", trial_conversation_id=chat))
        session.flush()
        session.add(KnowledgeVersion(
            id="pilot-evidence-version", document_id="pilot-evidence-doc", number=1, title="最终上下文摘录",
            category="工程", body="窗口截断移除了现行手册。", action="create", root_id=run["id"],
        ))
    read = services.knowledge.read_document("pilot-evidence-doc", version_id="pilot-evidence-version", root_id=run["id"])
    latest = client.get(root).json()
    parent_save = update_node(client, latest, "parent", {
        "question": "材料是否支持进入试点？", "criteria": "核对可用材料",
        "status": "resolved", "conclusion": "已确认材料范围。", "reason": "核对试点材料", "evidence_ids": [read["id"]],
    })
    assert parent_save.status_code == 200, parent_save.text
    parent = parent_save.json()["map"]["nodes"][0]
    baseline = commit("试点基线")
    fork = client.post(root + "/branches", json={
        "operation_id": "fork", "name": "候选排序实验", "source_commit_id": baseline["id"],
    }).json()
    latest = parent_save.json()["task"]
    result = update_node(client, latest, "new-point", {
        "question": "现行手册在哪一步丢失？", "criteria": "核对最终上下文",
        "parent_id": parent["id"], "status": "resolved", "conclusion": "在上下文窗口被截断。",
        "reason": "最终上下文摘录确认", "evidence_ids": [read["id"]],
        "related": [{"target_id": parent["id"], "description": "辅助解释手册覆盖范围"}],
    })
    assert result.status_code == 200, result.text
    source = commit("固定截断诊断")
    node = next(row for row in source["snapshot"]["map"]["nodes"] if row["question"] == "现行手册在哪一步丢失？")
    params = {
        "source_commit_id": source["id"], "node_id": node["id"], "target_branch_id": fork["id"],
        "expected_revision_id": fork["head"]["id"], "expected_control_version": task["control_version"],
        "target_parent_id": parent["id"],
    }
    preview_route = root + "/map/commit-adoption-preview"
    adopt_route = root + "/map/commit-adopt"
    assert client.post(preview_route, json={**params, "target_parent_id": "unrelated-parent"}).status_code == 409
    preview = client.post(preview_route, json=params)
    assert preview.status_code == 200, preview.text
    plan = preview.json()
    assert plan["before"] is None
    assert plan["after"]["status"] == "needs_review"
    assert plan["after"]["parent_id"] == parent["id"]
    assert plan["after"]["related"] == []
    assert plan["evidence"][0]["id"] == read["id"]
    assert plan["reopened_ancestors"] == [{"id": parent["id"], "question": parent["question"]}]

    # 预览后目标简报变化也会阻止写入。
    fork_path = f"{root}/branches/{fork['id']}"
    changed = client.post(fork_path + "/revisions", json={
        "operation_id": "advance-target", "expected_revision_id": fork["head"]["id"],
        "expected_control_version": task["control_version"], "changes": {"brief": {"next_action": "补齐范围"}},
        "reason": "先校验另一个条件",
    })
    assert changed.status_code == 201, changed.text
    command = {**params, "operation_id": "adopt", "preview_hash": plan["preview_hash"], "reason": "补上确认过的诊断"}
    assert client.post(adopt_route, json=command).status_code == 409
    target = next(row for row in client.get(root + "/branches").json()["items"] if row["id"] == fork["id"])
    command["expected_revision_id"] = target["head"]["id"]
    preview = client.post(preview_route, json={**params, "expected_revision_id": target["head"]["id"]})
    assert preview.status_code == 200, preview.text
    command["preview_hash"] = preview.json()["preview_hash"]
    with services.sessions.begin() as session:
        session.get(KnowledgeDocument, "pilot-evidence-doc").deleted_at = utcnow()
    revoked = client.post(adopt_route, json=command)
    assert revoked.status_code == 409 and revoked.json()["code"] == "adoption_evidence_unavailable"
    with services.sessions.begin() as session:
        session.get(KnowledgeDocument, "pilot-evidence-doc").deleted_at = None
    applied = client.post(adopt_route, json=command)
    assert applied.status_code == 200, applied.text
    assert client.post(adopt_route, json=command).json() == applied.json()
    [selected] = [row for row in applied.json()["map"]["nodes"] if row["id"] == node["id"]]
    assert selected["evidence"][0]["id"] == read["id"]
    assert selected["status"] == "needs_review"
    revision = applied.json()["map"]["revision_id"]
    fixed = client.get(fork_path + "/revisions/" + revision).json()
    assert fixed["state"]["adopted_from"] == {
        "research_revision_id": source["revision_id"], "research_commit_id": source["id"], "node_id": node["id"],
    }
    with services.sessions() as session:
        assert session.get(AssetReference, ("research_revision", revision, f"research_commit:{source['id']}"))
    assert client.get(main + "/commits/" + source["id"]).json() == source
    assert client.get(root).json()["write_branch_id"] == task["main_branch_id"]


def test_history_preserves_evidence_and_adopts_only_confirmed_conclusion(client, services):
    chat, task = create_task(client)
    run = submit(client, chat)
    with services.sessions.begin() as session:
        article = KnowledgeDocument(id="test-doc")
        session.add(article)
        session.flush()
        version = KnowledgeVersion(id="test-version", document_id=article.id, number=1, title="上下文样本",
                                   category="工程", body="正确候选确实进入了最终上下文。",
                                   action="create", root_id=run["id"])
        session.add(version)
    read = services.knowledge.read_document("test-doc", version_id="test-version", root_id=run["id"])
    current = client.get(f"/api/research-tasks/{task['id']}").json()
    node = {
        "question": "正确候选是否进入最终上下文？", "status": "resolved",
        "criteria": "核对候选 ID 和最终上下文采样", "reason": "对照固定样本",
        "conclusion": "已进入", "evidence_ids": [read["id"]],
    }
    added = update_node(client, current, "new", node)
    assert added.status_code == 200, added.text
    old = added.json()
    [original] = old["map"]["nodes"]
    assert original["evidence"][0]["url"] == "/evidence/" + read["id"]

    changed_node = {**node, "id": original["id"], "conclusion": "已进入，需要修正指令理解"}
    changed = update_node(client, old["task"], "change", changed_node)
    assert changed.status_code == 200, changed.text
    latest = changed.json()["task"]
    root = f"/api/research-tasks/{task['id']}/map"
    old_id, new_id = old["task"]["head"]["id"], latest["head"]["id"]
    diff = client.get(root + f"/compare?before={old_id}&after={new_id}").json()
    assert diff["changes"][0]["types"] == ["conclusion"]

    params = {
        "source_revision_id": old_id, "node_id": original["id"],
        "expected_revision_id": new_id, "expected_control_version": latest["control_version"],
    }
    preview = client.post(root + "/adoption-preview", json=params)
    assert preview.status_code == 200, preview.text
    assert preview.json()["after"]["conclusion"] == "已进入"
    adopted = client.post(root + "/adopt", json={
        **params, "preview_hash": preview.json()["preview_hash"],
        "operation_id": "adopt", "reason": "保留更审慎的结论",
    })
    assert adopted.status_code == 200, adopted.text
    state = adopted.json()
    assert state["map"]["nodes"][0]["conclusion"] == "已进入"
    assert state["task"]["head"]["parent_revision_id"] == new_id
    assert state["map"]["nodes"][0]["status"] == "needs_review"
    assert client.get(root + f"?revision_id={new_id}").json()["nodes"][0]["conclusion"] == changed_node["conclusion"]


def test_other_research_cannot_attach_an_unrelated_evidence_id(client, services):
    _, task = create_task(client)
    with services.sessions.begin() as session:
        session.add(KnowledgeDocument(id="unrelated-doc"))
        session.flush()
        session.add(KnowledgeVersion(id="unrelated-version", document_id="unrelated-doc", number=1,
                                     title="另一项研究", category="工程", body="属于另一会话。",
                                     action="create", root_id="different-root"))
        session.flush()
        session.add(Evidence(id="unrelated", root_id="different-root", knowledge_version_id="unrelated-version",
                             start_offset=0, end_offset=7, quote="属于另一会话。"))
    response = update_node(client, task, "unrelated", {
        "question": "需要判断什么？", "status": "investigating",
        "criteria": "核对原文", "evidence_ids": ["unrelated"],
    })
    assert response.status_code == 409, response.text


def test_map_diff_reports_node_addition_removal_and_move_without_changing_history(client):
    _, task = create_task(client)
    endpoint = f"/api/research-tasks/{task['id']}/map"
    initial = task
    identifiers = {}
    for title, parent in (
        ("原始主题路径", None), ("另一研究主题", None), ("需要移动的子主题", "原始主题路径"), ("等待移除的旧主题", None),
    ):
        response = update_node(client, initial, title, {
            "question": title, "parent_id": identifiers.get(parent), "criteria": "核对与总目标的依赖",
        })
        assert response.status_code == 200, response.text
        saved = response.json()
        identifiers[title] = next(node["id"] for node in saved["map"]["nodes"] if node["question"] == title)
        initial = saved["task"]
    before = initial["head"]["id"]
    moved = update_node(client, initial, "move", {
        "id": identifiers["需要移动的子主题"], "question": "需要移动的子主题",
        "parent_id": identifiers["另一研究主题"], "criteria": "核对与总目标的依赖",
    }).json()["task"]
    removed = client.post(endpoint, json={
        "operation_id": "remove", "expected_control_version": moved["control_version"],
        "expected_revision_id": moved["head"]["id"], "reason": "移除当前不适用的路线",
        "operation": {"action": "remove", "node_id": identifiers["等待移除的旧主题"]},
    })
    assert removed.status_code == 200, removed.text
    newer = update_node(client, removed.json()["task"], "add", {
        "question": "新的替代主题", "criteria": "解释新路线与目标的关系",
    }).json()
    after = newer["task"]["head"]["id"]
    added_id = next(row["id"] for row in newer["map"]["nodes"] if row["question"] == "新的替代主题")
    diff = client.get(endpoint + f"/compare?before={before}&after={after}").json()
    assert {row["id"]: row["types"] for row in diff["changes"]} == {
        added_id: ["added"], identifiers["需要移动的子主题"]: ["moved"], identifiers["等待移除的旧主题"]: ["removed"],
    }
    historical = {row["id"]: row for row in diff["before"]["nodes"]}
    assert historical[identifiers["需要移动的子主题"]]["parent_id"] == identifiers["原始主题路径"]
    assert identifiers["等待移除的旧主题"] in historical


def test_research_run_pins_read_history_and_updates_its_map(client, services):
    chat, task = create_task(client)
    policy = ToolPolicyService(services.sessions)
    policy.update(expected_revision=policy.get()["revision"], enabled={
        "builtin:history_search": True, "builtin:history_read": True, "builtin:update_research_map": True,
    })

    class MapProvider(BriefReader):
        def stream(self, messages, **kwargs):
            self.requests.append(messages)
            observed = {msg.name: json.loads(msg.content) for msg in messages
                        if isinstance(msg, ToolMessage) and msg.name in {"history_search", "history_read"}}
            if len(self.requests) == 1:
                tool, arguments = "history_search", {"query": "简报开始调查", "role": "user"}
            elif len(self.requests) == 2:
                quote = observed["history_search"]["results"][0]
                tool, arguments = "history_read", {"source_id": quote["id"], "offset": 0}
            elif len(self.requests) == 3:
                point = observed["history_read"]["research_evidence_id"]
                tool, arguments = "update_research_map", {
                    "operation": {"action": "save", "node": {
                        "question": "当前任务从哪个问题开始？", "criteria": "找到用户原话和范围",
                        "status": "resolved", "conclusion": "当前应先调查简报确定的问题。",
                        "reason": "用户在此会话发出明确要求", "evidence_ids": [point],
                    }},
                    "reason": "固定开始调查的真人原话",
                }
            else:
                yield AIMessageChunk("已固定现有目标并更新地图。")
                return
            yield AIMessageChunk("", tool_call_chunks=[{
                "name": tool, "args": json.dumps(arguments, ensure_ascii=False),
                "id": f"map-evidence-{len(self.requests)}", "index": 0,
            }])

    provider = MapProvider()
    services.gateway.enabled, services.gateway.client = True, provider
    run = submit(client, chat)
    finished = execute_run(run["id"], run["generation"], services=services, checkpointer=InMemorySaver())
    assert finished["status"] == "completed", finished
    result = client.get(f"/api/research-tasks/{task['id']}/map").json()
    [node] = result["nodes"]
    [handle] = node["evidence"]
    assert handle["kind"] == "history"
    assert handle["quote"] == "请根据简报开始调查"
    details = client.get(handle["url"].replace("/research/", "/api/research-tasks/"))
    assert details.status_code == 200, details.text
    assert details.json()["role"] == "human"
    assert details.json()["conversation_url"].startswith(f"/chat/{chat}?message=")
