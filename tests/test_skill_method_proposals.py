"""用运维试点的固定验收和实际技能管理检验审阅边界。"""

import pytest
from test_api_worker import client as api_client
from test_api_worker import services as api_services
from test_runtime import setup_run

from knowledgeops_agent.harness.runtime.resources import ResourceService
from knowledgeops_agent.harness.skills.management import SkillManagement
from knowledgeops_agent.research.trial import completed_trial

client = api_client
services = api_services
ROUTE = "/api/skills/proposals"
LESSON = "定位检索问答的缺证与误引"
INITIAL = b"---\nname: evidence-check\ndescription: Check sources\n---\n# Audit\n"


@pytest.fixture
def skill_directory(client, monkeypatch, tmp_path):
    root = tmp_path / "skills"
    monkeypatch.setenv("SKILLS_DIRECTORY", str(root))
    stage = client.post("/api/skills/imports", files=[
        ("files", ("SKILL.md", INITIAL)), ("files", ("references/example.md", b"Fixed reference")),
    ])
    assert stage.status_code == 201, stage.text
    assert client.post(f"/api/skills/imports/{stage.json()['id']}/commit").status_code == 200
    policy = client.get("/api/tools/policy").json()
    assert client.patch("/api/tools/policy", json={
        "expected_revision": policy["revision"], "skill_enabled": {"evidence-check": True},
    }).status_code == 200
    return root


def prepare(client):
    case = completed_trial()
    response = client.post(ROUTE, json={"report_id": case["report"]["id"]})
    assert response.status_code == 201, response.text
    return response.json()


def review(client, proposal, action="publish", **overrides):
    return client.post(f"{ROUTE}/{proposal['id']}/review", json={
        "action": action, "review_hash": proposal["review_hash"], **overrides,
    })


def test_publication_freezes_versions_and_replays_the_review(client, services, skill_directory):
    case = completed_trial()
    assert client.post(ROUTE, json={"report_id": "unverified"}).status_code == 404
    assert client.get(ROUTE).json() == {"items": []}

    runtime = (services.jobs, services.knowledge, services.sources, services.gateway.ledger)
    _, before, _ = setup_run(runtime)
    snapshots = ResourceService(services.sessions)
    resource = "/skills/evidence-check/SKILL.md"

    def frozen(context):
        return next(row for row in snapshots.ensure(context)["skills"] if row["resource"] == resource)

    assert frozen(before)["content"].encode() == INITIAL
    proposal = prepare(client)
    assert prepare(client)["id"] == proposal["id"]
    assert proposal["state"] == "pending_review"
    assert proposal["evidence"]["report_id"] == case["report"]["id"]
    assert proposal["evidence"]["experiment_id"] == case["experiment"]["id"]
    assert proposal["evidence"]["report_status"] == "accepted"
    assert LESSON in proposal["diff"]
    assert proposal["base_revision"] == 1
    assert (skill_directory / "evidence-check/SKILL.md").read_bytes() == INITIAL
    assert client.post(f"/api/skills/imports/{proposal['id']}/commit").status_code == 409
    assert review(client, proposal, review_hash="0" * 64).status_code == 409

    published = review(client, proposal)
    assert published.status_code == 200, published.text
    assert published.json()["state"] == "published"
    assert published.json()["published_revision"] == 2
    assert review(client, proposal).json() == published.json()
    assert review(client, proposal, "reject").status_code == 409
    assert prepare(client)["id"] == proposal["id"]
    assert frozen(before)["content"].encode() == INITIAL
    services.jobs.cancel(before.run_id)
    _, after, _ = setup_run(runtime)
    assert LESSON in frozen(after)["content"]
    assert case["report"]["id"] not in frozen(after)["content"]
    assert (skill_directory / "evidence-check/references/example.md").read_bytes() == b"Fixed reference"
    assert services.gateway.ledger.records() == []


def test_rejection_and_stale_base_keep_the_managed_package(client, skill_directory):
    proposal = prepare(client)
    rejected = review(client, proposal, "reject")
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["state"] == "rejected"
    assert review(client, proposal, "reject").json() == rejected.json()
    assert review(client, proposal).status_code == 409
    assert (skill_directory / "evidence-check/SKILL.md").read_bytes() == INITIAL

    def update(line, revision):
        staged = client.post("/api/skills/imports", files={"files": ("SKILL.md", INITIAL + line)},
                             data={"expected_revision": str(revision)})
        assert staged.status_code == 201
        assert client.post(f"/api/skills/imports/{staged.json()['id']}/commit").status_code == 200

    update(b"First edit\n", 1)
    old = prepare(client)
    update(b"Concurrent edit\n", 2)
    assert review(client, old).status_code == 409
    rebuilt = prepare(client)
    assert rebuilt["id"] != old["id"] and rebuilt["base_revision"] == 3
    assert [item["id"] for item in client.get(ROUTE).json()["items"]][:2] == [rebuilt["id"], old["id"]]


def test_review_retry_recovers_after_file_swap(client, services, monkeypatch, skill_directory):
    proposal = prepare(client)

    def interrupted(_manager, event):
        if event == "new_moved":
            raise OSError("injected directory swap interruption")

    with monkeypatch.context() as inject:
        inject.setattr(SkillManagement, "_checkpoint", interrupted)
        assert review(client, proposal).status_code == 409
    assert (skill_directory / "evidence-check/SKILL.md").read_bytes() == INITIAL
    assert review(client, proposal).json()["published_revision"] == 2
