"""加载部署方法，然后通过内容摘要固定本任务实际使用的版本。"""

from typing import get_args

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.dialects.postgresql import insert

from knowledgeops_agent.app.conversations import fingerprint
from knowledgeops_agent.core.resources import bundled_resource
from knowledgeops_agent.persistence.models import ResearchMethodBundle
from knowledgeops_agent.research.contracts import ResearchCapability

CAPABILITIES = get_args(ResearchCapability)
REQUIRED_METHODS = {"common", "exploration", *CAPABILITIES}

RESEARCH_DUTIES = (
    "本次消息属于已登记的研发任务。承接研发简报与当前目标，根据证据推进研究。"
    "区分用户给定的线索、读取的事实、未经验证的假设及结论。按当前阶段使用已提供的专业方法，"
    "涉及应用能力未支持的操作时说明缺口。用户授权、可用工具、固定版本和成果提交均由程序校验。"
    "阶段或专业方法需要变化时，单独调用 set_research_focus，再根据新装配内容决定操作。"
    "用户固定的专业方法不能被工具覆盖。"
    "只有已确认的自主授权允许用 continue_research 记录下一轮的具体工作。"
    "记录下一步后结束本轮，待回复、待审批或范围不清时先停下来。"
    "达到约定交付要求时，单独调用 submit_research_delivery 冻结成果并停止，等待用户验收；"
    "直接传平铺字段（title、diagnosis、recommendations、limitations、source_version_ids、evidence_ids），"
    "不要外包 delivery 嵌套对象，结论与建议保持精炼结构化，详细分析沉淀在研究地图。"
    "按需读取研究地图。把重要问题、解决标准、判断理由和已读依据用 update_research_map 更新；"
    "只有必要子问题和自身证据都满足标准才标记为已解决。"
    "仅当观察结果会改变判断并且已准备好固定数据时，尝试提出独立实验。"
    "等待人工审批，之后用 read_research_experiment 核对真实回执及证据 ID，再修订建议。"
    "普通答复结束不代表整体研发任务已经验收。"
)


class MethodBundle(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = Field(ge=1)
    methods: dict[str, str]


def canonical_capabilities(capabilities):
    return [name for name in CAPABILITIES if name in capabilities]


def freeze(session) -> str:
    source = bundled_resource("config/research-methods.json")
    packet = MethodBundle.model_validate_json(source.read_text(encoding="utf-8")).model_dump()
    if packet["methods"].keys() != REQUIRED_METHODS or any(
        not text.strip() or len(text) > 10000 for text in packet["methods"].values()
    ):
        raise ValueError("研发方法包缺少必需正文或超过单项容量。")
    identity = fingerprint(packet)
    session.execute(insert(ResearchMethodBundle).values(id=identity, content=packet).on_conflict_do_nothing())
    return identity


def for_state(session, state):
    identity = state["method_bundle_ref"]
    bundle = session.get(ResearchMethodBundle, identity)
    if bundle is None or fingerprint(bundle.content) != identity:
        raise ValueError("固定研发方法包缺失或校验失败。")
    active = state["active_capabilities"]
    if any(capability not in CAPABILITIES for capability in active):
        raise ValueError("研究修订包含未知能力。")
    selected = ["common"]
    if state["phase"] == "exploration" or not active:
        selected.append("exploration")
    selected.extend(capability for capability in CAPABILITIES if capability in active)
    return {key: bundle.content["methods"][key] for key in selected}
