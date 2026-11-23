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
