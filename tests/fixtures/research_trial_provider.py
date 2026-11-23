"""浏览器试用验收只替代模型，检索、工具授权和引用由实际应用处理。"""

import json

from langchain_core.messages import AIMessageChunk, ToolMessage


class ResearchTrialProvider:
    def bind_tools(self, tools):
        return self

    def stream(self, messages, **kwargs):
        results = [message for message in messages if isinstance(message, ToolMessage)
                   and message.name == "search_references"]
        if not results:
            yield AIMessageChunk(content="", tool_call_chunks=[{
                "name": "search_references", "args": '{"query":"生产紧急变更 审批 当班负责人 错误率"}',
                "id": "trial-reference-search", "index": 0,
            }])
            return
        rows = json.loads(results[-1].content)["evidence"]
        regulation = next(row for row in rows if row["title"] == "生产紧急变更规程")
        manual = next(row for row in rows if row["title"] == "值班操作手册")
        assert "当班负责人事前批准" in regulation["text"]
        assert "错误率" in manual["text"]
        yield AIMessageChunk(content=(
            f"生产紧急变更须由当班负责人事前批准。{regulation['citation']}\n\n"
            f"变更后核对错误率、请求延迟和核心业务探针。{manual['citation']}"
        ))
