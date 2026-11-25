"""分组仅修改目录归属，真实 PG 验证旧游标、幂等及运行身份。"""

from test_api_worker import client as _client
from test_api_worker import services as _services

services, client = _services, _client


def create_project(client, name, key):
    response = client.post("/api/conversation-projects", json={"name": name, "request_key": key})
    assert response.status_code == 201, response.text
    return response.json()


def send(client, chat, key):
    response = client.post(f"/api/conversations/{chat['id']}/messages", json={
        "content": "核对运维试点的回滚规则", "request_key": key,
    })
    assert response.status_code == 202, response.text
    return response.json()


def page(client, project):
    response = client.get("/api/conversations/page", params={"project": project})
    assert response.status_code == 200, response.text
    return response.json()["items"]


def test_project_move_remove_and_retry_preserve_the_conversation(client):
    left = create_project(client, "试点资料", "create-left")
    right = create_project(client, "复盘", "create-right")
    assert create_project(client, "试点资料", "create-left")["id"] == left["id"]
    assert client.post("/api/conversation-projects", json={
        "name": "不同创建请求", "request_key": "create-left",
    }).status_code == 409
    endpoint = "/api/conversations"
    body = {"title": "回滚规则", "request_key": "new-group-chat", "project_id": left["id"]}
    first = client.post(endpoint, json=body)
    assert first.status_code == 201, first.text
    conversation = first.json()
    assert not page(client, left["id"])
    run = send(client, conversation, "message")
    other = client.post(endpoint, json={"title": "旧入口"}).json()
    send(client, other, "message-other")
    before = page(client, left["id"])[0]
    assert before["id"] == conversation["id"] and before["project_revision"] == 0
    cursor = client.get(endpoint + "/page?limit=1").json()["next_cursor"]

    path = f"{endpoint}/{conversation['id']}/project"
    move = {"project_id": right["id"], "expected_revision": 0}
    moved = client.patch(path, json=move)
    assert moved.status_code == 200, moved.text
    assert moved.json()["project_revision"] == 1
    assert client.patch(path, json=move).json() == moved.json()
    assert client.patch(path, json={"project_id": None, "expected_revision": 0}).status_code == 409
    assert not page(client, left["id"])
    assert page(client, right["id"])[0]["activity_at"] == before["activity_at"]
    assert client.get(endpoint + "/page", params={"limit": 1, "cursor": cursor}).status_code == 409
    assert run["conversation_id"] == conversation["id"]

    target = f"/api/conversation-projects/{right['id']}"
    assert client.delete(target).status_code == 204
    assert client.delete(target).status_code == 204
    unfiled = page(client, "unfiled")
    assert {chat["id"] for chat in unfiled} == {other["id"], conversation["id"]}
    assert all(chat["project_id"] is None for chat in unfiled)
    assert client.patch(path, json=move).status_code == 409
    assert client.post(endpoint, json=body).json()["id"] == conversation["id"]
    assert client.get(f"/api/runs/{run['id']}").json()["conversation_id"] == conversation["id"]
    assert [row["content"] for row in client.get(f"{endpoint}/{conversation['id']}/messages").json()] == [
        "核对运维试点的回滚规则",
    ]


def test_project_settings_and_removed_draft_destination(client):
    project = create_project(client, "开始时的名字", "project-key")
    path = f"/api/conversation-projects/{project['id']}"
    updated = client.patch(path, json={"name": "重新命名", "pinned": True})
    assert updated.status_code == 200
    assert updated.json()["name"] == "重新命名" and updated.json()["pinned"]
    assert client.get("/api/conversation-projects").json()["items"] == [updated.json()]
    assert client.delete(path).status_code == 204
    body = {"title": "项目移除前的草稿", "project_id": project["id"], "request_key": "old-draft"}
    response = client.post("/api/conversations", json=body)
    assert response.status_code == 201
    send(client, response.json(), "after-removal")
    assert page(client, "unfiled")[0]["id"] == response.json()["id"]
    assert client.post("/api/conversations", json=body).json()["id"] == response.json()["id"]

