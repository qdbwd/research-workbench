"""固定用例需明确区分上下文缺证据与回答误用已有证据。"""

import json
from pathlib import Path
from runpy import run_path

ROOT = Path(__file__).parents[1]
FIXTURE = ROOT / "tests/fixtures/research_retrieval_case.json"
SCRIPT = ROOT / "scripts/research-experiments/reranker_check.py"


def test_real_fixture_identifies_which_case_can_benefit_from_reranking():
    load = run_path(str(SCRIPT))
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    result = load["analyze"](data)
    by_id = {item["id"]: item for item in result["cases"]}
    retrieval = by_id["missing-from-context"]
    misuse = by_id["visible-misused"]
    assert retrieval["fault"] == "retrieval_or_context_selection"
    assert retrieval["observed_context_has_gold"] is False
    assert retrieval["reranked_context_has_gold"] is True
    assert misuse["fault"] == "answer_misuse"
    assert misuse["observed_context_has_gold"] is True
    assert misuse["reranked_context_has_gold"] is True
    assert "上游漏选" in result["conclusion"]
    assert "回答误用" in result["conclusion"]
