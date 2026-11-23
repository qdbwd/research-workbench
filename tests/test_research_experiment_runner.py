"""独立执行器使用固定程序并保留已领取身份，重复请求不会重跑。"""

import hashlib
import json
from pathlib import Path
from runpy import run_path
from uuid import uuid4

ROOT = Path(__file__).parents[1]
RUNNER = ROOT / "scripts/research-experiments/runner.py"
SCRIPT = ROOT / "scripts/research-experiments/reranker_check.py"


def request():
    payload = {"documents": {}, "cases": [], "context_limit": 1}
    return {
        "attempt_id": str(uuid4()),
        "kind": "reranker-check-v1",
        "image": "ghcr.io/astral-sh/uv@sha256:e5b65587bce7de595f299855d7385fe7fca39b8a74baa261ba1b7147afa78e58",
        "script_sha256": hashlib.sha256(SCRIPT.read_bytes()).hexdigest(),
        "input_sha256": hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False,
                                                   separators=(",", ":")).encode()).hexdigest(),
        "input": payload,
        "timeout_seconds": 5,
    }


def test_confirmed_attempt_replays_fixed_receipt_without_a_second_launch(tmp_path):
    runner = run_path(str(RUNNER))
    launches = []
    job = request()

    def execute(packet, cancelled):
        launches.append((packet["attempt_id"], cancelled()))
        return {"status": "completed", "output": {"schema_version": 1}, "exit_code": 0}

    first = runner["handle"](tmp_path, job, execute=execute)
    again = runner["handle"](tmp_path, job, execute=execute)
    assert first == again
    assert first["status"] == "completed"
    assert first["input_sha256"] == job["input_sha256"]
    assert launches == [(job["attempt_id"], False)]


def test_claim_without_result_is_unknown_and_cancellation_before_claim_does_not_launch(tmp_path):
    runner = run_path(str(RUNNER))
    lost = request()
    cancelled = request()
    for directory, job in (("claims", lost), ("cancel", cancelled)):
        target = tmp_path / directory / f"{job['attempt_id']}.json"
        target.parent.mkdir(parents=True)
        target.write_text("{}", encoding="utf-8")

    def unexpected(*_):
        raise AssertionError("an unknown or withdrawn attempt must not launch")

    assert runner["handle"](tmp_path, lost, execute=unexpected)["status"] == "unknown"
    assert runner["handle"](tmp_path, cancelled, execute=unexpected)["status"] == "cancelled"
