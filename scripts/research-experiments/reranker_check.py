"""对固定问题重放候选排序，独立检查最终上下文与保存的回答。"""

import json
import re
import sys


def terms(value):
    return set(re.findall(r"[a-z0-9_]+", value.casefold()))


def analyze(packet):
    documents = packet["documents"]
    limit = packet["context_limit"]
    cases = []
    for case in packet["cases"]:
        candidates = case["candidate_ids"]
        observed = case["observed_context"]
        gold = case["gold_id"]
        query_terms = terms(case["question"])
        # 只改变排序。候选集合、截断长度和原回答保持固定，避免把排序归因给生成。
        alternate = sorted(candidates, key=lambda identity: -len(query_terms & terms(documents[identity])))[:limit]
        answered_correctly = case["observed_answer"].strip().casefold() == case["expected_answer"].strip().casefold()
        if gold not in observed:
            fault = "retrieval_or_context_selection"
        elif not answered_correctly:
            fault = "answer_misuse"
        else:
            fault = "none_observed"
        cases.append({
            "id": case["id"],
            "fault": fault,
            "gold_id": gold,
            "gold_in_candidates": gold in candidates,
            "observed_context_ids": observed,
            "observed_context_has_gold": gold in observed,
            "reranked_context_ids": alternate,
            "reranked_context_has_gold": gold in alternate,
            "observed_answer_correct": answered_correctly,
        })
    return {
        "schema_version": 1,
        "algorithm": "query_token_overlap",
        "context_limit": limit,
        "cases": cases,
        "conclusion": "只有上游漏选且正确片段已在候选集中时，该重排验证才支持改进。"
        "回答误用已提供证据的情形，需单独检查生成约束、证据对齐与输出评估。",
        "limitation": "此处比较固定夹具和确定性排序，未重新生成回答，也未测量线上问答准确率。",
    }


if __name__ == "__main__":
    json.dump(analyze(json.load(sys.stdin)), sys.stdout, sort_keys=True)
    sys.stdout.write("\n")
