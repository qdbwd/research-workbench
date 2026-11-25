"""主线 B 的 URL、观察引用及证据边界。"""

import hashlib
import os
from contextlib import contextmanager
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient
from test_api_worker import services as _services

from knowledgeops_agent.app.application import create_app
from knowledgeops_agent.core.config import Settings
from knowledgeops_agent.harness.browser.capture import PNG
from knowledgeops_agent.harness.browser.errors import BrowserToolError
from knowledgeops_agent.harness.browser.names import BROWSER_NAMES
from knowledgeops_agent.harness.browser.policy import BrowserPolicy
from knowledgeops_agent.harness.browser.session import controls_from_snapshot
from knowledgeops_agent.harness.browser.tools import invoke
from knowledgeops_agent.harness.tools.policy import baseline_policy
from knowledgeops_agent.persistence.files import FileStore


@pytest.mark.parametrize("target", [
    "file:///tmp/example", "chrome://settings", "javascript:alert(1)",
    "http://127.0.0.1", "http://169.254.169.254", "https://docs.example.org@evil.example.org/",
    "http://postgres", "http://metadata.google.internal", "http://[fd00:ec2::254]",
])
def test_launch_rejects_forbidden_navigation(target):
    with pytest.raises(BrowserToolError, match="origin_blocked"):
        BrowserPolicy(Settings(_env_file=None), "launch").check(target)


def test_launch_rejects_mixed_dns_even_with_a_public_ip(monkeypatch):
    monkeypatch.setattr(
        "knowledgeops_agent.harness.browser.policy.resolve_addresses",
        lambda *_: ["93.184.215.14", "192.168.1.1"],
    )
    with pytest.raises(BrowserToolError, match="origin_blocked"):
        BrowserPolicy(Settings(_env_file=None), "launch").check("https://example.org/")


@pytest.mark.parametrize("target", [
    "https://api.github.com/docs",
    "https://web.dev/articles",
    "https://dev.to/guide",
    "https://redis.io/docs",
])
def test_launch_allows_public_domains_with_names_matching_service_labels(monkeypatch, target):
    monkeypatch.setattr(
        "knowledgeops_agent.harness.browser.policy.resolve_addresses", lambda *_: ["93.184.215.14"],
    )
    assert str(BrowserPolicy(Settings(_env_file=None), "launch").check(target)) == target


@pytest.mark.parametrize("target", [
    "http://postgres.internal/", "http://api.local/", "http://api/",
    "http://web.localhost/", "http://dev.lan/", "http://redis.home.arpa/",
])
def test_internal_service_aliases_are_blocked_even_with_an_explicit_origin(monkeypatch, target):
    monkeypatch.setattr(
        "knowledgeops_agent.harness.browser.policy.resolve_addresses", lambda *_: ["93.184.215.14"],
    )
    settings = Settings(_env_file=None, browser_allowed_origins=target)
    with pytest.raises(BrowserToolError, match="origin_blocked"):
        BrowserPolicy(settings, "launch").check(target)


@pytest.mark.parametrize(("mode", "page"), [
    ("launch", "http://localhost:18080/"),
    ("attach", "http://localhost:3000/"),
])
def test_local_database_endpoint_does_not_block_other_local_services(monkeypatch, mode, page):
    monkeypatch.setattr(
        "knowledgeops_agent.harness.browser.policy.resolve_addresses", lambda *_: ["127.0.0.1"],
    )
    settings = Settings(
        _env_file=None, database_url="postgresql+psycopg://u:p@localhost:5432/db",
        browser_allowed_origins=page + ",http://localhost:5432",
    )
    policy = BrowserPolicy(settings, mode)
    with pytest.raises(BrowserToolError, match="origin_blocked"):
        policy.check("http://localhost:5432/")
    assert str(policy.check(page)) == page


@pytest.mark.parametrize(("setting", "endpoint", "port"), [
    ("database_url", "postgresql+psycopg://u:p@127.0.0.1:15432/db", 15432),
    ("runtime_redis_url", "redis://127.0.0.1:16380/1", 16380),
    ("celery_broker_url", "redis://127.0.0.1:16379/0", 16379),
    ("embedding_base_url", "http://127.0.0.1:18081/v1", 18081),
    ("reranker_base_url", "http://127.0.0.1:18082/v1", 18082),
])
def test_configured_ports_are_blocked_without_blocking_other_ports_on_the_same_ip(setting, endpoint, port):
    settings = Settings(_env_file=None, **{setting: endpoint})
    policy = BrowserPolicy(settings, "attach")
    with pytest.raises(BrowserToolError, match="origin_blocked"):
        policy.check(f"http://127.0.0.1:{port}/")
    assert str(policy.check("http://127.0.0.1:18080/")) == "http://127.0.0.1:18080/"


def test_control_refs_are_parsed_only_from_supported_accessibility_nodes():
    controls = controls_from_snapshot(
        '- button "现行版" [ref=e5]\n'
        '- textbox "脚本文字 [ref=e12]" [ref=e6]\n'
        '- button "Delete" [ref=e7]\n'
        '- text: "- button danger [ref=e8]"\n'
        '- link "条款" [ref=e9]:\n'
        '  - /url: /docs\n'
        '- button "选择文件" [ref=e10]\n'
    )
    assert set(controls) == {"e5", "e7", "e9", "e10"}
    assert not controls["e5"]["blocked"] and not controls["e9"]["blocked"]
    assert controls["e7"]["blocked"] and controls["e10"]["blocked"]


def test_control_labels_only_block_action_verbs_for_button_and_menuitem():
    controls = controls_from_snapshot(
        '- link "发布说明" [ref=e11]\n'
        '- button "发布" [ref=e12]\n'
        '- button "Delete" [ref=e7]\n'
        '- menuitem "提交" [ref=e13]\n'
        '- tab "存档与删除" [ref=e14]\n'
        '- treeitem "删除指南" [ref=e15]\n'
        '- link "指南" [ref=e16] [disabled]\n'
        '- tab "历史版" [ref=e17] [disabled]\n'
        '- treeitem "分组" [ref=e18] [disabled]\n'
        '- link "状态说明 [disabled]" [ref=e19]\n'
    )
    assert {ref for ref, control in controls.items() if control["blocked"]} == {
        "e7", "e12", "e13", "e16", "e17", "e18",
    }


def test_local_origins_use_exact_scheme_host_and_port():
    policy = BrowserPolicy(Settings(_env_file=None, browser_allowed_origins="http://127.0.0.1:9999"), "launch")
    assert str(policy.check("http://127.0.0.1:9999/document")) == "http://127.0.0.1:9999/document"
    with pytest.raises(BrowserToolError, match="origin_blocked"):
        policy.check("http://127.0.0.1:9998/document")

services = _services
CLAUSE = "生产紧急变更须由当班负责人事前批准。"


class FixturePages(SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/blocked-redirect":
            self.send_response(302)
            # 相同测试服务器的另一 origin。代理必须在请求前拒绝它。
            self.send_header("Location", f"http://localhost:{self.server.server_port}/blocked-sink")
            self.end_headers()
            return
        if self.path == "/blocked-sink":
            self.server.blocked_hits += 1
        if self.path.startswith("/state-probe"):
            page = b"""<!doctype html><meta charset="utf-8"><main></main><script>
            if (location.search) {
              document.cookie = 'fixture=seed';
              localStorage.setItem('fixture', 'seed');
            }
            document.querySelector('main').textContent =
              'cookie=' + (document.cookie || 'empty') + ' localStorage=' + (localStorage.getItem('fixture') || 'empty');
            </script>"""
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(page)))
            self.end_headers()
            self.wfile.write(page)
            return
        super().do_GET()


@pytest.fixture
def fixture_server():
    directory = Path(__file__).with_name("fixtures")
    with ThreadingHTTPServer(("127.0.0.1", 0), partial(FixturePages, directory=directory)) as server:
        server.blocked_hits = 0
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield server
        finally:
            server.shutdown()
            thread.join()


@pytest.fixture
def fixture_origin(fixture_server):
    return f"http://127.0.0.1:{fixture_server.server_port}"


def active_runtime(services, settings, tmp_path):
    services.settings = services.jobs.browser_config = settings
    services.sessions.configure(info={**services.sessions.kw["info"], "file_store": FileStore(tmp_path / "workspace")})
    jobs = services.jobs
    chat = jobs.create_conversation("浏览器取证")
    run = jobs.enqueue(chat["id"], "核对规程", request_key="browser-capture")
    jobs.claim("browser-test", run_id=run["id"], generation=run["generation"])
    binding = {"enabled": True, "mode": "launch", "max_actions": 25, "action_timeout": 20}
    context = SimpleNamespace(
        run_id=run["id"], root_id=run["id"], worker_id="browser-test", jobs=jobs,
        sources=services.sources, resources={"browser": binding, "tool_policy": baseline_policy()},
    )
    return context


def runner(context):
    index = 0

    def action(name, **args):
        nonlocal index
        index += 1
        return invoke(SimpleNamespace(context=context, tool_call_id=f"call-{index}"), name, **args)
    return action


def test_browser_settings_default_revision_attach_and_admission(services):
    with TestClient(create_app(services=services, publish_jobs=False)) as client:
        raw = client.get("/api/browser/settings").json()
        assert raw["default_mode"] == "launch" and not raw["local_attach_enabled"]
        assert client.post("/api/browser/probe").json()["code"] == "cdp_disabled"
        chat = client.post("/api/conversations", json={"title": "测试"}).json()
        body = {"content": "浏览", "request_key": "attach", "browser_mode": "attach"}
        assert client.post(f"/api/conversations/{chat['id']}/messages", json=body).status_code == 409
        assert client.patch("/api/browser/settings", json={
            "expected_revision": 1, "local_attach_enabled": True,
        }).status_code == 409

        settings = services.settings.model_copy(update={
            "browser_enabled": True, "browser_local_attach_enabled": True,
            "browser_local_cdp_endpoint": "http://127.0.0.1:9222/private",
        })
        services.settings = services.jobs.browser_config = settings
        saved = client.patch("/api/browser/settings", json={
            "expected_revision": 1, "local_attach_enabled": True, "default_mode": "attach",
        })
        assert saved.status_code == 200 and saved.json()["revision"] == 2
        assert saved.json()["deployment"]["cdp_endpoint_display"] == "http://127.0.0.1:9222"
        assert client.patch("/api/browser/settings", json={
            "expected_revision": 1, "local_attach_enabled": False,
        }).status_code == 409
        sent = client.post(f"/api/conversations/{chat['id']}/messages", json=body)
        assert sent.status_code == 202 and sent.json()["input"]["browser_mode"] == "attach"
        assert client.post(f"/api/conversations/{chat['id']}/messages", json=body).json()["id"] == sent.json()["id"]


@pytest.mark.skipif(os.getenv("KNOWLEDGEOPS_TEST_PLAYWRIGHT_MCP") != "1", reason="requires pinned MCP and Chromium")
def test_real_chromium_capture_saves_evidence_and_verified_png(services, tmp_path, fixture_origin):
    settings = services.settings.model_copy(update={
        "browser_enabled": True, "browser_allowed_origins": fixture_origin,
    })
    url = fixture_origin + "/operations-pilot-dynamic-doc.html"
    assert CLAUSE not in httpx.get(url, timeout=3, trust_env=False).text
    with TestClient(create_app(services=services, publish_jobs=False)) as client:
        context = active_runtime(services, settings, tmp_path)
        call = runner(context)
        assert call("navigate", url=url).get("url") == url
        assert call("wait", text="历史审批条款", time=3)["status"] == "succeeded"
        observation = call("observe")
        assert observation["status"] == "succeeded", observation
        tab = next(control for control in observation["controls"] if control["label"] == "现行版")
        assert call("click", observation_id="invalid", ref=tab["ref"])["error_code"] == "stale_observation"
        assert call("click", observation_id=observation["observation_id"], ref=tab["ref"])["status"] == "succeeded"
        assert call("click", observation_id=observation["observation_id"], ref=tab["ref"])["error_code"] == "stale_observation"
        assert call("wait", text="现行审批条款", time=3)["status"] == "succeeded"
        saved = call("capture", include_screenshot=True, location_hint="现行版")
        assert saved["status"] == "succeeded", saved
        assert CLAUSE in saved["evidence"]["text"] and "旧版规程" not in saved["evidence"]["text"]
        assert saved["version_id"] == saved["evidence"]["source_version_id"] and saved["citation"]
        assert call("close")["status"] == "succeeded"
        assert call("observe")["error_code"] == "browser_session_expired"
        image = client.get(saved["screenshot"]["image_url"])
        assert image.status_code == 200 and image.content.startswith(PNG)
        assert image.headers["x-content-type-options"] == "nosniff"
        assert hashlib.sha256(image.content).hexdigest() == saved["screenshot"]["sha256"]
        assert services.sources.get_version(saved["version_id"])["parser"] == "playwright-mcp-rendered-v1"
        static = services.sources.import_text(
            "静态规程", "静态 HTML 的对照正文", kind="web", origin=url, parser="trafilatura", root_id=context.root_id,
        )
        assert static["id"] == saved["source_id"] and static["version_id"] != saved["version_id"]
        assert CLAUSE in services.sources.read_segment(saved["evidence"]["segment_id"], root_id=context.root_id)["text"]
        assert client.get("/api/runs/missing/artifacts/" + saved["screenshot"]["artifact_id"] + "/image").status_code == 404
        event = next(event for event in services.jobs.events(context.root_id) if event["kind"] == "browser_capture")
        assert event["data"]["screenshot"]["artifact_id"] == saved["screenshot"]["artifact_id"]
        directory = os.getenv("KNOWLEDGEOPS_BROWSER_EVIDENCE_DIR")
        if directory:
            (Path(directory) / "captured-dynamic-document.png").write_bytes(image.content)


def test_browser_tool_surface_has_seven_names_and_no_script_schema():
    from knowledgeops_agent.harness.browser.tools import BROWSER_TOOLS
    from knowledgeops_agent.harness.tools.catalog import tool_options

    assert {tool.name for tool in BROWSER_TOOLS} == set(BROWSER_NAMES)
    for tool in BROWSER_TOOLS:
        props = tool.tool_call_schema.model_json_schema()["properties"]
        assert not ({"function", "script", "code", "selector", "file"} & props.keys())
    catalog = [item for item in tool_options() if item["group"] == "browser"]
    assert len(catalog) == 7
    assert all(item["main_only"] and not item["subagent_selectable"] for item in catalog)


class FakeBrowser:
    def __init__(self, output):
        self.output = output
        self.url = "https://example.org/"
        self.title = "模拟动态页面"
        self.calls = []
        self.text = "这是能作为证据导入的模拟动态正文，用于核对受控数据流。"
        self.clicked = False
        self.close_count = 0

    async def call(self, name, args):
        self.calls.append(name)
        if name == "browser_navigate":
            self.url = args["url"]
        if name == "browser_click":
            self.clicked = True
        if name == "browser_snapshot":
            return SimpleNamespace(content=[SimpleNamespace(type="text", text=(
                "### Snapshot\n" + chr(96) * 3 + "yaml\n- tab \"查看正文\" [ref=e1]\n" + chr(96) * 3
            ))])
        return SimpleNamespace(content=[])

    async def page(self, limit):
        return {"url": self.url, "documentId": 1, "title": self.title, "text": self.text[:limit]}

    async def click_check(self, _ref):
        return {"blocked": False, "href": None}


def fake_manager(services, context, factory=FakeBrowser):
    from contextlib import asynccontextmanager

    from knowledgeops_agent.harness.browser.session import BrowserSessionManager

    opened = []

    @asynccontextmanager
    async def connector(settings, mode, output, policy):
        adapter = factory(output)
        opened.append(adapter)
        try:
            yield adapter
        finally:
            adapter.close_count += 1

    manager = BrowserSessionManager(services.settings, services.sessions, connector=connector)
    context.jobs._browser_sessions = manager
    return manager, opened


def test_close_releases_live_session_after_browser_action_limit(services, tmp_path):
    origin = "http://127.0.0.1:19321"
    config = services.settings.model_copy(update={"browser_enabled": True, "browser_allowed_origins": origin})
    context = active_runtime(services, config, tmp_path)
    context.resources["browser"]["max_actions"] = 1
    manager, opened = fake_manager(services, context)
    call = runner(context)
    try:
        assert call("navigate", url=origin)["status"] == "succeeded"
        assert call("observe")["error_code"] == "browser_action_limit"
        assert opened[0].output.is_dir()
        assert call("close")["status"] == "succeeded"
        assert context.jobs.get_run(context.root_id)["tool_calls"] == 2
        assert opened[0].close_count == 1
        assert not opened[0].output.exists()
    finally:
        manager.close()


def test_fake_browser_evidence_reuse_cleanup_and_restore(services, tmp_path):
    origin = "http://127.0.0.1:19321"
    config = services.settings.model_copy(update={"browser_enabled": True, "browser_allowed_origins": origin})
    context = active_runtime(services, config, tmp_path)
    manager, opened = fake_manager(services, context)
    call = runner(context)
    try:
        assert call("navigate", url=origin)["status"] == "succeeded"
        obs = call("observe")
        assert call("click", observation_id=obs["observation_id"], ref="e1")["status"] == "succeeded"
        assert call("capture", include_screenshot=False, location_hint="")["evidence"]["text"] == opened[0].text
        assert len(opened) == 1 and opened[0].clicked
        context.jobs.close_browser_session(context.root_id)
        assert opened[0].close_count == 1 and not opened[0].output.exists()
        assert call("click", observation_id=obs["observation_id"], ref="e1")["error_code"] == "browser_session_expired"
        assert call("navigate", url=origin)["status"] == "succeeded"
        assert len(opened) == 2 and not opened[1].clicked
    finally:
        manager.close()


@pytest.mark.parametrize("failure", ["origin_blocked", "browser_timeout", "browser_disconnected", "browser_empty_content"])
def test_fake_browser_failure_does_not_publish_source_or_repeat_action(services, tmp_path, failure):
    import asyncio

    from sqlalchemy import func, select

    from knowledgeops_agent.persistence.models import Source

    origin = "http://127.0.0.1:19322"
    settings = services.settings.model_copy(update={"browser_enabled": True, "browser_allowed_origins": origin})
    context = active_runtime(services, settings, tmp_path)
    context.resources["browser"]["action_timeout"] = 0.8

    class FaultyBrowser(FakeBrowser):
        async def call(self, name, args):
            result = await super().call(name, args)
            if name == "browser_click":
                if failure == "origin_blocked":
                    self.url = "http://169.254.169.254/"
                elif failure == "browser_disconnected":
                    raise BrowserToolError(failure)
                elif failure == "browser_timeout":
                    await asyncio.sleep(5)
                else:
                    self.text = "空"
            return result

    manager, opened = fake_manager(services, context, FaultyBrowser)
    call = runner(context)
    try:
        assert call("navigate", url=origin)["status"] == "succeeded"
        obs = call("observe")
        args = {"observation_id": obs["observation_id"], "ref": "e1"}
        action = call("click", **args)
        if failure == "browser_empty_content":
            assert action["status"] == "succeeded"
            action = call("capture", include_screenshot=False, location_hint="")
        assert action["error_code"] == failure
        if failure != "browser_empty_content":
            assert opened[0].close_count == 1 and not opened[0].output.exists()
            assert call("capture", include_screenshot=False, location_hint="")["error_code"] == "browser_session_expired"
        assert opened[0].calls.count("browser_click") == 1
        with services.sessions() as session:
            assert session.scalar(select(func.count()).select_from(Source)) == 0
    finally:
        manager.close()


def test_browser_permissions_match_catalog_and_execution(services, tmp_path):
    from knowledgeops_agent.harness.mcp.calls import mcp_tools
    from knowledgeops_agent.harness.tools.access import allowed_local_ids
    from knowledgeops_agent.harness.tools.policy import ToolPolicyService
    from knowledgeops_agent.harness.tools.preview import effective_catalog
    from knowledgeops_agent.persistence.models import Run

    settings = services.settings.model_copy(update={"browser_enabled": True})
    context = active_runtime(services, settings, tmp_path)
    call = runner(context)
    expected = {f"builtin:{name}" for name in BROWSER_NAMES}
    assert expected <= allowed_local_ids(context)
    off = effective_catalog(services.sessions)
    assert all("browser_disabled" in row["unavailable_reasons"] for row in off["tools"] if row["group"] == "browser")

    for inputs in ({"knowledge_only": True}, {"research": {"review_only": True}}, {"trial_case": "test"}):
        with services.sessions.begin() as session:
            row = session.get(Run, context.root_id)
            original = row.input_data
            row.input_data = {**original, **inputs}
        assert not expected & allowed_local_ids(context)
        # 仅知识库的真实 guard 可以正常运行；其他模式的行需由对应业务服务创建。
        if "knowledge_only" in inputs:
            assert call("observe")["error_code"] == "browser_disabled"
        with services.sessions.begin() as session:
            session.get(Run, context.root_id).input_data = original
    with services.sessions.begin() as session:
        row = session.get(Run, context.root_id)
        row.input_data = {**row.input_data, "research": {"review_only": False}}
    assert expected <= allowed_local_ids(context)
    assert mcp_tools(context) == []
    assert not expected & allowed_local_ids(context, is_root=False)
    with services.sessions.begin() as session:
        row = session.get(Run, context.root_id)
        row.input_data = original
    policy = ToolPolicyService(services.sessions)
    policy.update(expected_revision=0, enabled={"builtin:browser_observe": False})
    assert "builtin:browser_observe" not in allowed_local_ids(context)
    assert call("observe")["error_code"] == "browser_disabled"
    del context.resources["browser"]
    assert not expected & allowed_local_ids(context)


@pytest.mark.skipif(os.getenv("KNOWLEDGEOPS_TEST_PLAYWRIGHT_MCP") != "1", reason="requires pinned MCP and Chromium")
def test_real_cdp_attach_is_exclusive_and_does_not_close_external_chromium(services, tmp_path, fixture_origin):
    import subprocess
    import time

    from knowledgeops_agent.harness.browser.settings import BrowserSettingsService, chromium

    profile = tmp_path / "external-profile"
    with subprocess.Popen([
        str(chromium(services.settings)), "--headless", "--no-sandbox", "--disable-dev-shm-usage",
        "--remote-debugging-port=0", f"--user-data-dir={profile}", "about:blank",
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) as external:
        try:
            for _ in range(100):
                if (profile / "DevToolsActivePort").exists():
                    break
                time.sleep(0.05)
            port = int((profile / "DevToolsActivePort").read_text().splitlines()[0])
            settings = services.settings.model_copy(update={
                "browser_enabled": True, "browser_local_attach_enabled": True,
                "browser_local_cdp_endpoint": f"http://127.0.0.1:{port}",
                "browser_local_cdp_host_header": f"localhost:{port}",
                "browser_attach_allowed_origins": fixture_origin,
            })
            with (
                browser_session_manager(settings, services.sessions) as first,
                browser_session_manager(settings, services.sessions) as second,
            ):
                probe = BrowserSettingsService(services.sessions, settings)
                assert probe.probe()["ok"]
                query = {"url": fixture_origin + "/operations-pilot-dynamic-doc.html"}
                options = {"timeout": 20, "check": lambda: None}
                assert first.execute("root-one", "attach", "navigate", query, **options)["url"] == query["url"]
                assert probe.probe()["attach_busy"]
                with pytest.raises(BrowserToolError, match="local_browser_busy"):
                    second.execute("root-two", "attach", "navigate", query, **options)
                first.close_run("root-one")
                assert external.poll() is None
                assert probe.probe()["ok"] and not probe.probe()["attach_busy"]
                assert second.execute("root-two", "attach", "navigate", query, **options)["url"] == query["url"]
        finally:
            external.terminate()
            external.wait(timeout=10)



@contextmanager
def browser_session_manager(settings, sessions):
    from knowledgeops_agent.harness.browser.session import BrowserSessionManager

    manager = BrowserSessionManager(settings, sessions)
    try:
        yield manager
    finally:
        manager.close()



def test_identical_png_bytes_keep_distinct_provenance_and_migrate_as_binary(services, tmp_path):
    from base64 import b64encode

    from knowledgeops_agent.harness.browser.capture import screenshot_metadata
    from knowledgeops_agent.persistence.models import RunArtifact
    from knowledgeops_agent.persistence.workspace import migrate_content

    url = "http://127.0.0.1:19324"
    config = services.settings.model_copy(update={"browser_enabled": True, "browser_allowed_origins": url})
    context = active_runtime(services, config, tmp_path)
    image = PNG + b"fake image bytes used for path and digest contracts"

    class SameImage(FakeBrowser):
        async def call(self, name, args):
            if name == "browser_take_screenshot":
                return SimpleNamespace(content=[SimpleNamespace(
                    type="image", mime_type="image/png", data=b64encode(image).decode(),
                )])
            return await super().call(name, args)

    manager, opened = fake_manager(services, context, SameImage)
    call = runner(context)
    try:
        assert call("navigate", url=url)["status"] == "succeeded"
        first = call("capture", include_screenshot=True, location_hint="第一段")
        opened[0].text += " 第二版正文在视口以下，截图像素保持相同。"
        second = call("capture", include_screenshot=True, location_hint="第二段")
        assert first["version_id"] != second["version_id"]
        left, right = first["screenshot"], second["screenshot"]
        assert left["artifact_id"] != right["artifact_id"] and left["sha256"] == right["sha256"]
        with services.sessions() as session:
            stored = [screenshot_metadata(session.get(RunArtifact, item["artifact_id"])) for item in (left, right)]
            assert [entry["source_version_id"] for entry in stored] == [first["version_id"], second["version_id"]]
            assert stored[0]["storage_key"] == stored[1]["storage_key"]
        assert migrate_content(services.sessions)["run_artifacts"] == 0
        manager.close_run(context.root_id)
        with TestClient(create_app(services=services, publish_jobs=False)) as client:
            assert client.get(left["image_url"]).content == image
            chat = context.jobs.create_conversation("隔离截图")
            other = context.jobs.enqueue(chat["id"], "另一运行", request_key="other-run")
            route = left["image_url"].replace(context.root_id, other["id"])
            assert client.get(route).status_code == 404
            with services.sessions() as session:
                store = session.info["file_store"]
                store.path(stored[0]["storage_key"]).write_bytes(PNG + b"corrupted")
            assert client.get(left["image_url"]).status_code == 404
    finally:
        manager.close()


def test_browser_stdio_environment_and_disconnect_are_sanitized(monkeypatch):
    import asyncio

    from knowledgeops_agent.harness.browser.transport import BrowserAdapter
    from knowledgeops_agent.harness.mcp.transport import child_environment

    for name in ("OPENAI_API_KEY", "DATABASE_URL", "POSTGRES_PASSWORD", "REDIS_URL"):
        monkeypatch.setenv(name, "fake-sensitive-value")
        assert name not in child_environment({})
    async def disconnected(*_):
        raise OSError("/private/path/fake-sensitive-value")

    adapter = BrowserAdapter(SimpleNamespace(session=SimpleNamespace(call_tool=disconnected)), None)
    with pytest.raises(BrowserToolError, match="^browser_disconnected") as raised:
        asyncio.run(adapter.call("browser_navigate", {}))
    assert "/private/path" not in str(raised.value)



@pytest.mark.skipif(os.getenv("KNOWLEDGEOPS_TEST_PLAYWRIGHT_MCP") != "1", reason="requires pinned MCP and Chromium")
def test_real_launch_has_separate_storage_blocks_redirect_and_reaps_processes(services, fixture_origin, fixture_server):
    settings = services.settings.model_copy(update={"browser_enabled": True, "browser_allowed_origins": fixture_origin})
    session_dirs = set(Path("/tmp").glob("knowledgeops-browser-0064-*"))
    with browser_session_manager(settings, services.sessions) as manager:
        url = fixture_origin + "/state-probe"
        options = {"timeout": 20, "check": lambda: None}
        manager.execute("root-one", "launch", "navigate", {"url": url + "?seed"}, **options)
        assert "cookie=fixture=seed localStorage=seed" in manager.execute(
            "root-one", "launch", "observe", {}, **options,
        )["text"]
        with pytest.raises(BrowserToolError, match="browser_session_expired"):
            manager.execute("root-two", "launch", "observe", {}, **options)
        manager.execute("root-two", "launch", "navigate", {"url": url}, **options)
        assert "cookie=empty localStorage=empty" in manager.execute(
            "root-two", "launch", "observe", {}, **options,
        )["text"]
        owned = set(Path("/tmp").glob("knowledgeops-browser-0064-*")) - session_dirs
        assert len(owned) == 2
        with pytest.raises(BrowserToolError, match="origin_blocked"):
            manager.execute("root-two", "launch", "navigate", {"url": fixture_origin + "/blocked-redirect"}, **options)
        assert fixture_server.blocked_hits == 0
        manager.close_run("root-one")
        assert not any(path.exists() for path in owned)
        for cmdline in Path("/proc").glob("[0-9]*/cmdline"):
            try:
                arguments = cmdline.read_bytes()
            except (FileNotFoundError, ProcessLookupError, PermissionError):
                continue
            assert not any(str(directory).encode() in arguments for directory in owned)


def test_worker_finally_releases_root_browser_when_graph_stops(services, tmp_path, monkeypatch):
    from langgraph.checkpoint.memory import InMemorySaver

    from knowledgeops_agent.app.runs import worker
    from knowledgeops_agent.app.runs.service import RunStopped

    origin = "http://127.0.0.1:19325"
    services.settings = services.jobs.browser_config = services.settings.model_copy(update={
        "browser_enabled": True, "browser_allowed_origins": origin,
    })
    jobs = services.jobs
    conversation = jobs.create_conversation("验证取消")
    run = jobs.enqueue(conversation["id"], "本次验证模型关闭", request_key="worker-finally")
    # 只替换图入口，取消仍走真实 worker 生命周期和 Run 终态持久化。
    manager, opened = fake_manager(services, SimpleNamespace(jobs=jobs))

    def stop_after_browser(context, **_):
        manager.execute(context.root_id, "launch", "navigate", {"url": origin}, timeout=5, check=lambda: None)
        raise RunStopped("cancelled")

    monkeypatch.setattr(worker, "ensure_publication_intent", lambda _: {})
    monkeypatch.setattr(worker, "build_agent", stop_after_browser)
    try:
        result = worker.execute_run(run["id"], run["generation"], services=services, checkpointer=InMemorySaver())
        assert result["status"] == "cancelled"
        assert opened and opened[0].close_count == 1 and not opened[0].output.exists()
    finally:
        manager.close()

