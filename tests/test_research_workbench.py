"""第一阶段 HTTP 合同，使用真实隔离 PostgreSQL，关闭模型。"""

from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

from sqlalchemy import func, select
from test_api_worker import client as _client
from test_api_worker import services as _services

from knowledgeops_agent.app.lifecycle.asset_references import reachable_assets
from knowledgeops_agent.app.lifecycle.catalog import ReferenceCatalog
from knowledgeops_agent.persistence.models import ChatMessage, OperationReceipt, Run, RunDispatch

client = _client
services = _services


def create_conversation(client, title="已有会话"):
    return client.post("/api/conversations", json={"title": title}).json()["id"]


def activate(client, conversation_id, operation_id="activate-one", goal="核对检索失败"):
    return client.post(
        "/api/research-tasks",
        json={
            "operation_id": operation_id,
            "conversation_id": conversation_id,
            "goal": goal,
        },
    )


def test_start_in_an_empty_chat_records_goal_once_and_controls_lifecycle(client, services):
    conversation_id = create_conversation(client, "新对话")
    start = f"/api/conversations/{conversation_id}/research-task"
    receipt = start + "/start-receipt?operation_id=start-chat-research"
    source = client.post("/api/sources/text", json={
        "title": "现有检索说明", "content": "当前排序使用关键词匹配。",
        "conversation_id": conversation_id,
    }).json()
    body = {
        "operation_id": "start-chat-research",
        "goal": "对比关键词检索和重排",
        "source_ids": [source["id"]],
    }
    assert client.get(receipt).json() == {"state": "not_accepted"}
    assert not any(row["id"] == conversation_id for row in client.get("/api/conversations").json())
    response = client.post(start, json=body)
    assert response.status_code == 201, response.text
    task = response.json()
    assert task["head"]["state"]["material_refs"] == [{
        "source_id": source["id"], "source_version_id": source["version_id"], "title": source["title"],
    }]
    assert client.post(start, json=body).json() == task
    assert client.get(receipt).json() == {"state": "accepted", "task": task}
    assert client.post(start, json={**body, "goal": "不同目标"}).status_code == 409
    assert conversation_id in {row["id"] for row in client.get("/api/conversations").json()}
    messages = client.get(f"/api/conversations/{conversation_id}/messages").json()
    assert [row["content"] for row in messages] == [body["goal"]]
    with services.sessions() as session:
        run = session.get(Run, messages[0]["run_id"])
        assert run.kind == "research_start"
        assert run.status == "completed"
        assert not session.scalar(select(RunDispatch.run_id).where(RunDispatch.run_id == run.id))

    control = f"/api/research-tasks/{task['id']}/control"
    pause = {"operation_id": "pause-one", "expected_control_version": 1, "action": "pause"}
    paused = client.post(control, json=pause).json()
    assert paused["status"] == "paused"
    assert paused["control_version"] == 2
    assert paused["head"] == task["head"]
    assert client.post(control, json=pause).json() == paused
    stale_stop = {**pause, "operation_id": "cancel-stale", "action": "cancel"}
    assert client.post(control, json=stale_stop).json()["code"] == "control_conflict"
    resumed = client.post(control, json={"operation_id": "resume-one", "expected_control_version": 2, "action": "resume"}).json()
    assert resumed["status"] == "open"
    closed = client.post(control, json={"operation_id": "cancel-one", "expected_control_version": 3, "action": "cancel"}).json()
    assert closed["status"] == "closed"
    assert closed["head"] == task["head"]
    assert client.get(start).json() == closed
    assert client.get(f"/api/conversations/{conversation_id}/messages").json() == messages
    with services.sessions() as session:
        kept = reachable_assets(session, excluding_conversation=conversation_id, durable_only=True)
        assert session.scalar(select(kept.c.node).where(kept.c.node == f"source_version:{source['version_id']}"))
    next_task = client.post(start, json={"operation_id": "start-again", "goal": "另一次研究"}).json()
    assert next_task["id"] != task["id"]
    old = client.get(f"/api/research-tasks/{task['id']}").json()
    assert old["status"] == "closed"
    assert old["head"] == task["head"]


def path(task):
    return f"/api/research-tasks/{task['id']}/branches/{task['main_branch_id']}/revisions"


def test_delete_research_replays_and_preserves_chat_and_other_binding(client):
    chat = create_conversation(client)
    endpoint = f"/api/conversations/{chat}/research-task"
    task = client.post(endpoint, json={"operation_id": "start", "goal": "需要保留的原话"}).json()
    task_endpoint = f"/api/research-tasks/{task['id']}"
    before = client.get(f"/api/conversations/{chat}/messages").json()
    command = {"operation_id": "delete", "expected_control_version": 1}
    stale = client.post(task_endpoint + "/delete", json={**command, "expected_control_version": 2})
    assert stale.status_code == 409

    def remove(operation):
        return client.post(task_endpoint + "/delete", json={**command, "operation_id": operation})

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(remove, ["delete", "other-tab"]))
    assert all(response.status_code == 200 for response in responses)
    assert responses[0].json() == responses[1].json() == remove("delete").json()
    assert client.get(endpoint).json() is None
    assert client.get(task_endpoint).status_code == 404
    assert client.get(path(task)).status_code == 404
    assert not client.get("/api/research-tasks").json()["items"]
    assert client.get(f"/api/conversations/{chat}/messages").json() == before
    next_task = client.post(endpoint, json={"operation_id": "new", "goal": "新任务"}).json()
    assert next_task["id"] != task["id"]
    assert remove("retry-after-rebind").status_code == 200
    assert client.get(endpoint).json()["id"] == next_task["id"]


def test_deleted_research_keeps_only_reachable_fixed_assets(client, services):
    chat = create_conversation(client)
    endpoint = f"/api/conversations/{chat}/research-task"
    source = client.post("/api/sources/text", json={
        "title": "固定证据", "content": "原始数据", "conversation_id": chat,
    }).json()
    task = client.post(endpoint, json={
        "operation_id": "start", "goal": "有独立版本引用的研究", "source_ids": [source["id"]],
    }).json()
    historical = path(task) + "/" + task["head"]["id"]
    other = activate(client, create_conversation(client), goal=f"引用 {historical}").json()
    catalog = ReferenceCatalog(services.sessions)
    for _ in range(100):
        if catalog.step()["complete"]:
            break
    else:
        raise AssertionError("引用目录未完成")
    for current in (task, other):
        assert client.post(f"/api/research-tasks/{current['id']}/delete", json={
            "operation_id": "remove", "expected_control_version": 1,
        }).status_code == 200
        available = current == task
        assert client.get(historical).status_code == (200 if available else 404)
        with services.sessions() as session:
            nodes = reachable_assets(session, durable_only=True)
            assert bool(session.scalar(select(nodes.c.node).where(
                nodes.c.node == f"source_version:{source['version_id']}",
            ))) is available


def draft(task, operation_id, **brief):
    return {
        "operation_id": operation_id,
        "expected_revision_id": task["head"]["id"],
        "expected_control_version": task["control_version"],
        "changes": {"brief": brief},
        "reason": "补充验收条件",
    }


def test_activation_preserves_conversation_and_replays_exact_response(client, services):
    identity = create_conversation(client)
    other = create_conversation(client, "普通对话")
    accepted = client.post(
        f"/api/conversations/{identity}/messages",
        json={
            "content": "这条历史消息还在",
            "request_key": str(uuid4()),
        },
    )
    assert accepted.status_code == 202
    history = client.get(f"/api/conversations/{identity}/messages").json()
    assert client.get(f"/api/conversations/{identity}/research-task").json() is None
    response = activate(client, identity)
    assert response.status_code == 201, response.text
    task = response.json()
    assert task["status"] == "open"
    assert task["head"]["state"]["phase"] == "exploration"
    assert task["head"]["state"]["context_initialized"] is False
    assert task["head"]["state"]["brief"]["goal"] == "核对检索失败"
    assert activate(client, identity).json() == task
    assert activate(client, identity, goal="另一个目标").json()["code"] == "operation_conflict"
    assert (
        activate(client, identity, operation_id="different").json()["code"] == "conversation_bound"
    )
    assert client.get(f"/api/conversations/{identity}/research-task").json()["id"] == task["id"]
    assert client.get(f"/api/conversations/{other}/research-task").json() is None
    assert client.get(f"/api/conversations/{identity}/messages").json() == history
    with services.sessions() as session:
        assert session.scalar(select(func.count()).select_from(Run)) == 1
        assert session.scalar(select(func.count()).select_from(ChatMessage)) == 1
    assert services.gateway.ledger.records() == []


def test_concurrent_revisions_keep_old_versions_and_replay_the_winner(client):
    task = activate(client, create_conversation(client)).json()
    base = task["head"]
    body_a = draft(task, "same-operation", scope="用户 A 有限范围")
    body_b = draft(task, "other-operation", scope="用户 B 有限范围")
    with ThreadPoolExecutor(3) as pool:
        responses = list(
            pool.map(
                lambda payload: client.post(path(task), json=payload), [body_a, body_a, body_b]
            )
        )
    assert sorted(response.status_code for response in responses) in (
        [201, 201, 409],
        [201, 409, 409],
    )
    if responses[0].status_code == 201:
        assert responses[1].json() == responses[0].json()
    loser = next(response for response in responses if response.status_code == 409)
    winner = next(response for response in responses if response.status_code == 201).json()
    assert loser.json()["code"] == "revision_conflict"
    assert loser.json()["current_revision_id"] == winner["head"]["id"]
    assert winner["head"]["parent_revision_id"] == base["id"]
    assert winner["head"]["number"] == 2
    assert client.get(f"{path(task)}/{base['id']}").json() == base
    assert client.get(path(task)).json()["items"][0]["id"] == winner["head"]["id"]
    assert len(client.get(path(task)).json()["items"]) == 2
    later = client.post(path(task), json=draft(winner, "later", next_action="核对材料"))
    assert later.status_code == 201, later.text
    winner_body = body_a if responses[0].status_code == 201 else body_b
    assert client.post(path(task), json=winner_body).json() == winner
    reused = client.post(path(task), json={**winner_body, "reason": "改变请求"})
    assert reused.status_code == 409 and reused.json()["code"] == "operation_conflict"
    with_wrong_control = {
        **draft(later.json(), "wrong-control", scope="过期"),
        "expected_control_version": 999,
    }
    rejected = client.post(path(task), json=with_wrong_control)
    assert rejected.status_code == 409 and rejected.json()["code"] == "control_conflict"
    invalid = client.post(path(task), json={**with_wrong_control, "expected_control_version": True})
    assert invalid.status_code == 422

    other = activate(client, create_conversation(client), operation_id="activate-two").json()
    assert client.get(f"{path(task)}/{other['head']['id']}").status_code == 404
    foreign = path(task).replace(task["main_branch_id"], other["main_branch_id"])
    assert client.get(foreign).status_code == 404
    assert client.post(foreign, json=draft(task, "wrong-branch", scope="失败")).status_code == 404


def test_concurrent_activation_keeps_one_binding(client, services):
    identity = create_conversation(client)
    with ThreadPoolExecutor(3) as pool:
        responses = list(
            pool.map(lambda key: activate(client, identity, key), ["same", "same", "other"])
        )
    success = [response.json() for response in responses if response.status_code == 201]
    assert success and len({row["id"] for row in success}) == 1
    assert any(response.status_code == 409 for response in responses)
    with services.sessions() as session:
        count = session.scalar(
            select(func.count())
            .select_from(OperationReceipt)
            .where(OperationReceipt.operation_key.like("research:create:%"))
        )
        assert count == 1


def test_deleted_conversation_unbinds_but_preserves_research(client):
    identity = create_conversation(client)
    deletion_url = f"/api/conversations/{identity}"
    stale_preview = client.get(f"{deletion_url}/deletion-preview").json()
    task = activate(client, identity).json()
    assert (
        client.request(
            "DELETE",
            deletion_url,
            json={
                "expected_version": stale_preview["version"],
            },
        ).status_code
        == 409
    )
    preview = client.get(f"{deletion_url}/deletion-preview").json()
    assert preview["counts"]["research_tasks"] == 1
    assert "研发简报与版本" in preview["preserved"]
    assert (
        client.request(
            "DELETE",
            deletion_url,
            json={
                "expected_version": preview["version"],
            },
        ).status_code
        == 202
    )
    assert activate(client, identity, operation_id="after-delete").status_code == 409
    kept = client.get(f"/api/research-tasks/{task['id']}").json()
    assert kept["home_conversation_id"] is None
    assert kept["head"] == task["head"]
    assert client.get("/api/research-tasks").json()["items"][0]["id"] == task["id"]
