"""暂停真实流/图/worker，不用延时模拟供应商，也不产生付费请求。"""

import json
from threading import Event, Thread

from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from test_api_worker import client as _client
from test_api_worker import enqueue
from test_api_worker import services as _services
from test_runtime import DraftProvider
from test_runtime import runtime_services as _runtime_services
from test_workspace_runtime import enable

from knowledgeops_agent.app.runs.worker import execute_run

client = _client
services = _services
runtime_services = _runtime_services


def test_queued_pause_resume_cancel_is_persistent_and_idempotent(client, services):
    _, run, _ = enqueue(client)
    url = f"/api/runs/{run['id']}"
    paused = client.post(url + "/pause")
    assert paused.status_code == 200
    assert paused.json()["status"] == "paused" and paused.json()["pause_requested"]
    assert client.post(url + "/pause").json()["last_event"] == paused.json()["last_event"]
    assert services.jobs.claim("must-not-start", run_id=run["id"], generation=run["generation"]) is None
    resumed = client.post(url + "/resume", json={"budgets": {}})
    assert resumed.status_code == 202 and resumed.json()["status"] == "queued"
    assert not resumed.json()["pause_requested"]
    client.post(url + "/pause")
    assert client.post(url + "/cancel").json()["status"] == "cancelled"


class PauseProvider:
    invoke = DraftProvider.invoke

    def __init__(self, jobs, run_id):
        self.jobs, self.run_id = jobs, run_id
        self.requests = 0
        self.finished_stream = False

    def bind_tools(self, tools):
        return self

    def stream(self, messages, **kwargs):
        self.requests += 1
        if any(isinstance(message, ToolMessage) for message in messages):
            yield AIMessageChunk(content="已恢复并完成文件写入。")
            return
        yield AIMessageChunk(content="准备写入，")
        self.jobs.pause(self.run_id)
        yield AIMessageChunk(content="响应已完整接收。", tool_call_chunks=[{
            "name": "write_file", "id": "pause-write", "index": 0,
            "args": json.dumps({"file_path": "/work/result.txt", "content": "once"}),
        }])
        self.finished_stream = True


def test_worker_saves_entire_response_before_pause_and_resumes_original_tools(client, services, tmp_path):
    _, run, _ = enqueue(client)
    services.settings.workspace_root = tmp_path / "workspace"
    services.settings.workspace_root.mkdir()
    enable(services.sessions)
    provider = PauseProvider(services.jobs, run["id"])
    services.gateway.enabled, services.gateway.client = True, provider
    saver = InMemorySaver()
    paused = execute_run(run["id"], run["generation"], services=services, checkpointer=saver, worker_id="pause-worker")
    path = services.settings.workspace_root / "work/result.txt"
    assert paused["status"] == "paused"
    assert provider.finished_stream and provider.requests == 1 and not path.exists()
    config = {"configurable": {"thread_id": run["checkpoint_id"]}}
    values = saver.get(config)["channel_values"]
    answer = next(m for m in reversed(values["messages"]) if isinstance(m, AIMessage))
    assert answer.content == "准备写入，响应已完整接收。" and answer.tool_calls[0]["id"] == "pause-write"
    original_users = [m.content for m in values["messages"] if isinstance(m, HumanMessage)]
    resumed = client.post(f"/api/runs/{run['id']}/resume", json={"budgets": {}}).json()
    waiting = execute_run(run["id"], resumed["generation"], services=services, checkpointer=saver, worker_id="pause-worker")
    # 恢复暂停只继续原图，默认ask仍需确认该次文件写入，不能顺便授予许可。
    assert waiting["outcome"]["kind"] == "waiting_approval" and not path.exists()
    assert provider.requests == 1
    action = client.get(f"/api/runs/{run['id']}/approvals").json()["actions"][0]
    approved = client.post(f"/api/runs/{run['id']}/approvals/{action['id']}/decision", json={
        "expected_fingerprint": action["fingerprint"], "approve": True,
    })
    assert approved.status_code == 200
    resumed = services.jobs.get_run(run["id"])
    finished = execute_run(run["id"], resumed["generation"], services=services, checkpointer=saver, worker_id="pause-worker")
    assert finished["status"] == "completed" and path.read_text() == "once"
    assert provider.requests == 2 and finished["tool_calls"] == 1
    values = saver.get(config)["channel_values"]
    assert [m.content for m in values["messages"] if isinstance(m, HumanMessage)] == original_users


def test_approving_a_paused_run_does_not_resume_it(runtime_services, tmp_path):
    from test_execution_harness import graph_setup, resume_graph, save_wait

    module, context, run, graph, config = graph_setup(runtime_services, tmp_path, ["touch work/approved"])
    graph.invoke({"messages": module.initial_messages(context)}, config, context=context)
    approvals, _ = save_wait(context, run, graph, config)
    assert context.jobs.pause(run["id"])["status"] == "paused"
    action = approvals.list()[0]
    approvals.decide(action["id"], expected_fingerprint=action["fingerprint"], approve=True)
    assert context.jobs.get_run(run["id"])["status"] == "paused"
    assert not (context.workspace_root / "work/approved").exists()
    context.jobs.resume(run["id"])
    resume_graph(context, graph, config)
    assert (context.workspace_root / "work/approved").exists()


def test_paused_guidance_is_saved_and_old_tool_proposal_never_runs(client, services, tmp_path):
    _, run, _ = enqueue(client)
    services.settings.workspace_root = tmp_path / "workspace"
    services.settings.workspace_root.mkdir()
    enable(services.sessions)
    provider = PauseProvider(services.jobs, run["id"])
    services.gateway.enabled, services.gateway.client = True, provider
    saver = InMemorySaver()
    paused = execute_run(run["id"], run["generation"], services=services, checkpointer=saver, worker_id="pause-worker")
    queued = services.jobs.enqueue(run["conversation_id"], "不要写文件了，只解释原方案", request_key="paused-guidance")
    assert services.jobs.claim("queued-worker", run_id=queued["id"], generation=1) is None
    delivery = services.jobs.guide(queued["id"], target_id=run["id"], target_generation=paused["generation"])
    assert delivery["status"] == "pending" and services.jobs.get_run(run["id"])["status"] == "paused"
    resumed = client.post(f"/api/runs/{run['id']}/resume", json={"budgets": {}}).json()
    finished = execute_run(run["id"], resumed["generation"], services=services, checkpointer=saver, worker_id="pause-worker")
    assert finished["status"] == "completed"
    assert not (services.settings.workspace_root / "work/result.txt").exists()
    values = saver.get({"configurable": {"thread_id": run["checkpoint_id"]}})["channel_values"]
    assert any(isinstance(m, HumanMessage) and m.content == "不要写文件了，只解释原方案" for m in values["messages"])
    assert any(isinstance(m, ToolMessage) and m.tool_call_id == "pause-write" and m.status == "error" for m in values["messages"])


def test_concurrent_pause_during_resource_freeze_never_deadlocks(client, services, monkeypatch):
    """复现冻结资源期间并发点击暂停的真实窗口，确保锁序统一不会触发 PG AB-BA 死锁。"""
    from knowledgeops_agent.harness.runtime import resources as resources_mod
    from knowledgeops_agent.harness.runtime.state import RunContext

    _, run, _ = enqueue(client)
    claimed = services.jobs.claim("freeze-worker", run_id=run["id"], generation=run["generation"])
    assert claimed is not None

    entered_freeze = Event()
    release_freeze = Event()
    original_loader = resources_mod.load_knowledge_policy

    def gated_loader():
        entered_freeze.set()
        release_freeze.wait(timeout=5)
        return original_loader()

    monkeypatch.setattr(resources_mod, "load_knowledge_policy", gated_loader)
    context = RunContext(
        run_id=run["id"],
        root_id=run["id"],
        worker_id="freeze-worker",
        jobs=services.jobs,
        knowledge=services.knowledge,
        sources=services.sources,
        gateway=services.gateway,
    )
    errors: list[BaseException] = []

    def freeze_worker():
        try:
            resources_mod.prepare_resources(context)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    worker_thread = Thread(target=freeze_worker)
    worker_thread.start()
    assert entered_freeze.wait(timeout=5)

    pause_result: dict = {}

    def pause_caller():
        try:
            resp = client.post(f"/api/runs/{run['id']}/pause")
            pause_result["status_code"] = resp.status_code
            pause_result["body"] = resp.json()
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    pause_thread = Thread(target=pause_caller)
    pause_thread.start()
    # 给 pause 线程留足时间推进到锁等待，再放行冻结事务完成 flush 与提交。
    pause_thread.join(timeout=0.15)
    release_freeze.set()
    worker_thread.join(timeout=10)
    pause_thread.join(timeout=10)

    assert not errors, f"unexpected concurrent error: {errors!r}"
    assert pause_result["status_code"] == 200
    assert pause_result["body"]["pause_requested"] is True


def test_concurrent_pause_during_learning_resource_freeze_never_deadlocks(
    client, services, monkeypatch, tmp_path
):
    """带学习任务的 Run 冻结资源必须复用外层控制锁连接，既不单线程自锁也不与暂停死锁。"""
    from knowledgeops_agent.harness.runtime import resources as resources_mod
    from knowledgeops_agent.harness.runtime.state import RunContext
    from knowledgeops_agent.learning.tasks import LearningTaskService
    from knowledgeops_agent.persistence.files import FileStore

    services.sessions.configure(info={"file_store": FileStore(tmp_path)})
    conversation = services.jobs.create_conversation("学习并发冻结")
    tasks = LearningTaskService(services.sessions)
    task = tasks.create(
        conversation_id=conversation["id"],
        request_key="learn-freeze-pause",
        title="事务隔离学习",
        goal="理解锁序与连接复用",
        topic="数据库",
        units=[{"key": "locks", "title": "锁序", "goal": "解释死锁与咨询锁"}],
    )
    tasks.select(task["id"])
    created = client.post(
        f"/api/conversations/{conversation['id']}/messages",
        json={"content": "开始讲解锁序", "request_key": "learn-freeze-pause-msg"},
    )
    assert created.status_code == 202
    run = created.json()
    assert run["learning_task_id"] == task["id"]
    claimed = services.jobs.claim("freeze-learning-worker", run_id=run["id"], generation=run["generation"])
    assert claimed is not None

    entered_freeze = Event()
    release_freeze = Event()
    original_loader = resources_mod.load_knowledge_policy

    def gated_loader():
        entered_freeze.set()
        release_freeze.wait(timeout=5)
        return original_loader()

    monkeypatch.setattr(resources_mod, "load_knowledge_policy", gated_loader)
    context = RunContext(
        run_id=run["id"],
        root_id=run["id"],
        worker_id="freeze-learning-worker",
        jobs=services.jobs,
        knowledge=services.knowledge,
        sources=services.sources,
        gateway=services.gateway,
    )
    errors: list[BaseException] = []
    frozen_manifest: dict = {}

    def freeze_worker():
        try:
            frozen_manifest["value"] = resources_mod.prepare_resources(context)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    worker_thread = Thread(target=freeze_worker, daemon=True)
    worker_thread.start()
    assert entered_freeze.wait(timeout=5)

    pause_result: dict = {}

    def pause_caller():
        try:
            resp = client.post(f"/api/runs/{run['id']}/pause")
            pause_result["status_code"] = resp.status_code
            pause_result["body"] = resp.json()
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    pause_thread = Thread(target=pause_caller, daemon=True)
    pause_thread.start()
    pause_thread.join(timeout=0.15)
    release_freeze.set()
    worker_thread.join(timeout=10)
    pause_thread.join(timeout=10)

    assert not worker_thread.is_alive(), "learning resource freeze hung (possible nested lock_control self-lock)"
    assert not pause_thread.is_alive(), "pause request hung waiting for control lock"
    assert not errors, f"unexpected concurrent error: {errors!r}"
    assert pause_result["status_code"] == 200
    assert pause_result["body"]["pause_requested"] is True
    assert len(frozen_manifest["value"]["learning"]["profiles"]["profiles"]) == 2
    snapshot = resources_mod.ResourceService(services.sessions).read(run["id"])
    assert snapshot is not None
    assert len(snapshot["content"]["learning"]["profiles"]["profiles"]) == 2
