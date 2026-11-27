"""贯穿浏览器验收的确定性模型供应商。实验由外部真实执行器运行。"""

import json

from fixtures.execution_provider import ExecutionProvider
from langchain_core.messages import AIMessageChunk, ToolMessage

QUESTION = "问答错在候选漏选，还是使用已有证据时出错？"
RECOMMENDATION = "只对候选中已有正确片段但被截断的案例试点重排；已有证据被回答误用时，优先核对生成约束。"
PILOT_QUESTION = "三条试点问答分别在哪一步丢失或误用了现行审批证据？"
PILOT_DIAGNOSIS = "旧版案例的候选中没有现行规程；截断案例可通过排序改善上下文覆盖；误用案例已提供正确证据，但回答仍错误。"
PILOT_RECOMMENDATION = "暂缓开放试点。先阻止旧规程进入候选，再验证上下文保留和审批表述，留出题回归通过后由值班团队验收。"
PILOT_LIMITATION = "实验以试点失败记录的英文对应摘录重放固定候选；仅改变排序，没有重新生成答案，也没有评估线上准确率。"


class ResearchExperimentProvider(ExecutionProvider):
    def __init__(self, source, *, attempt=None, pilot=False):
        self.source = source
        self.attempt = attempt
        self.pilot = pilot
        self.step = 0

    def stream(self, messages, **kwargs):
        research = next(
            json.loads(message.content.split("\n", 1)[1])["research"]
            for message in messages if isinstance(message.content, str) and message.content.startswith("应用组织的任务背景")
        )
        self.step += 1
        if self.attempt is None:
            tool = self._propose(messages, research)
        else:
            tool = self._interpret(messages, research)
        if isinstance(tool, str):
            yield AIMessageChunk(tool)
            return
        name, arguments = tool
        yield AIMessageChunk("", tool_call_chunks=[{
            "name": name, "args": json.dumps(arguments, ensure_ascii=False),
            "id": f"research-{self.attempt or 'request'}-{self.step}", "index": 0,
        }])

    @staticmethod
    def _result(messages, name):
        item = next(message for message in reversed(messages) if isinstance(message, ToolMessage) and message.name == name)
        assert item.status == "success", item.content
        return json.loads(item.content)

    def _propose(self, messages, research):
        if self.step == 1:
            assert research["phase"] == "exploration" and "exploration" in research["methods"]
            return "set_research_focus", {
                "phase": "validation", "capabilities": ["experiment_analysis"],
                "reason": "分别固定候选、最终上下文和回答，再比较只改变排序的结果。",
            }
        if self.step == 2:
            assert research["phase"] == "validation" and "experiment_analysis" in research["methods"]
            assert self.source["version_id"] in {item["source_version_id"] for item in research["material_refs"]}
            return "read_source", {"segment_id": self.source["segments"][0]["id"]}
        if self.step == 3:
            observed = self._result(messages, "read_source")
            return "propose_research_experiment", {
                "question": PILOT_QUESTION if self.pilot else QUESTION,
                "hypothesis": "如果正确片段已在候选中，重排或可补足上下文。若片段已进入上下文而回答仍误用，则另查生成。",
                "dataset": json.loads(observed["text"]),
                "evidence_ids": [observed["evidence_id"]],
            }
        result = self._result(messages, "propose_research_experiment")
        assert result["status"] == "awaiting_approval"
        return "我会用同一批候选核对最终上下文和原回答。请先在独立实验页审批固定输入。"

    def _interpret(self, messages, research):
        assert research["phase"] == "validation" and "experiment_analysis" in research["methods"]
        if self.step == 1:
            return "read_research_experiment", {"attempt_id": self.attempt}
        result = self._result(messages, "read_research_experiment")
        assert result["status"] == "completed" and result["evidence_id"]
        cases = {case["id"]: case for case in result["result"]["cases"]}
        missing, misuse = cases["missing-from-context"], cases["visible-misused"]
        assert missing["fault"] == "retrieval_or_context_selection" and missing["reranked_context_has_gold"]
        assert misuse["fault"] == "answer_misuse" and misuse["observed_context_has_gold"]
        if self.pilot:
            assert not cases["old-version"]["gold_in_candidates"] and not cases["old-version"]["reranked_context_has_gold"]
        question = PILOT_QUESTION if self.pilot else QUESTION
        recommendation = PILOT_RECOMMENDATION if self.pilot else RECOMMENDATION
        diagnosis = PILOT_DIAGNOSIS if self.pilot else "第一组正确片段未进入上下文；第二组正确片段已在上下文中，回答仍误用。"
        ids = [*result["evidence_ids"], result["evidence_id"]]
        if self.step == 2:
            return "update_research_map", {
                "action": "save",
                "question": question,
                "criteria": "分别核对正确片段是否进入最终上下文及原回答是否正确。",
                "status": "resolved",
                "node_reason": diagnosis,
                "conclusion": recommendation,
                "evidence_ids": ids,
                "reason": "依据固定原文与真实实验修订建议",
            }
        if self.step == 3:
            return "submit_research_delivery", {
                "title": "运维问答助手内部试点建议" if self.pilot else "问答错因诊断与重排建议",
                "diagnosis": diagnosis, "recommendations": recommendation,
                "limitations": PILOT_LIMITATION if self.pilot else
                    "夹具只改变排序，没有重新生成回答。实验回执固定输入、脚本和镜像，可在实验页复现。",
                "source_version_ids": [self.source["version_id"]], "evidence_ids": ids,
            }
        raise AssertionError("提交成果后应停在固定验收状态，不再调用模型")
