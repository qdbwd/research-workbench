"""校验执行器观察记录与固定输入的一致性，再允许成为证据。"""


def check_output(dataset, output, exit_code):
    if (
        type(exit_code) is not int or exit_code != 0 or not isinstance(output, dict)
        or output.get("schema_version") != 1 or output.get("algorithm") != "query_token_overlap"
        or output.get("context_limit") != dataset["context_limit"]
    ):
        raise ValueError("实验结果的退出状态或规格不匹配。")
    if any(not isinstance(output.get(key), str) or not 1 <= len(output[key]) <= 1500
           for key in ("conclusion", "limitation")):
        raise ValueError("实验结果没有有效的通用结论和限制。")
    rows = output.get("cases")
    if not isinstance(rows, list) or len(rows) != len(dataset["cases"]):
        raise ValueError("实验结果没有覆盖固定问题集。")
    for case, row in zip(dataset["cases"], rows, strict=True):
        gold = case["gold_id"]
        observed = case["observed_context"]
        correct = case["observed_answer"].strip().casefold() == case["expected_answer"].strip().casefold()
        fault = "retrieval_or_context_selection" if gold not in observed else "none_observed" if correct else "answer_misuse"
        expected = {
            "id": case["id"], "gold_id": gold, "observed_context_ids": observed, "fault": fault,
        }
        if not isinstance(row, dict) or any(row.get(key) != value for key, value in expected.items()):
            raise ValueError("实验结果与已固定的输入、上下文和故障类别不一致。")
        reranked = row.get("reranked_context_ids")
        if (not isinstance(reranked, list) or len(reranked) > dataset["context_limit"]
                or any(identifier not in case["candidate_ids"] for identifier in reranked)
                or len(reranked) != len(set(reranked))):
            raise ValueError("重排后的上下文超出原候选范围。")
        checks = {
            "observed_context_has_gold": gold in observed, "observed_answer_correct": correct,
            "gold_in_candidates": gold in case["candidate_ids"], "reranked_context_has_gold": gold in reranked,
        }
        if any(row.get(key) is not value for key, value in checks.items()):
            raise ValueError("实验结果指标与实际记录不一致。")
