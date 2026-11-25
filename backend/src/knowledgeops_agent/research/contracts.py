"""第一阶段可写简报字段及并发更新合同。"""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Identity = Annotated[str, Field(min_length=1, max_length=36)]
OperationId = Annotated[str, Field(min_length=1, max_length=200)]
GoalText = Annotated[str, Field(min_length=1, max_length=2000)]
BriefText = Annotated[str, Field(max_length=4000)]
ResearchPhase = Literal["exploration", "investigation", "validation", "synthesis"]
ResearchCapability = Literal["product_design", "technical_research", "experiment_analysis"]


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class BriefPatch(Input):
    goal: GoalText = ""
    scope: BriefText = ""
    constraints: BriefText = ""
    acceptance_criteria: BriefText = ""
    key_decisions: BriefText = ""
    blockers: BriefText = ""
    next_action: BriefText = ""


class BriefChange(Input):
    brief: BriefPatch

    @model_validator(mode="after")
    def requires_change(self):
        if not self.brief.model_fields_set:
            raise ValueError("至少提供一项简报字段。")
        return self


class ReviseResearch(Input):
    operation_id: OperationId
    expected_revision_id: Identity
    expected_control_version: Annotated[int, Field(strict=True, ge=1)]
    changes: BriefChange
    reason: Annotated[str, Field(min_length=1, max_length=1000)]


class StartResearch(Input):
    operation_id: OperationId
    goal: GoalText
    source_ids: list[Identity] = Field(default_factory=list, max_length=30)


class ResearchCommand(Input):
    operation_id: OperationId
    expected_control_version: Annotated[int, Field(strict=True, ge=1)]


class ControlResearch(ResearchCommand):
    action: Literal["pause", "resume", "cancel"]


class SaveResearchCommit(Input):
    operation_id: OperationId
    message: Annotated[str, Field(min_length=1, max_length=1000)]
    preview_hash: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]


class ForkResearchCommit(Input):
    operation_id: OperationId
    source_commit_id: Identity
    name: Annotated[str, Field(min_length=1, max_length=100)]


class SwitchResearchWriteBranch(Input):
    operation_id: OperationId
    branch_id: Identity
    expected_write_version: Annotated[int, Field(strict=True, ge=1)]


class ArchiveResearchBranch(Input):
    operation_id: OperationId
    expected_revision_id: Identity


class ResearchBudget(Input):
    """授权表单必须明确提供每项上限，不从部署设置隐式补足。"""

    model_requests: Annotated[int, Field(strict=True, ge=1, le=600)]
    tool_calls: Annotated[int, Field(strict=True, ge=1, le=2000)]
    searches: Annotated[int, Field(strict=True, ge=0, le=200)]
    active_seconds: Annotated[int, Field(strict=True, ge=1, le=7200)]


class ControlAutonomy(ResearchCommand):
    expected_revision_id: Identity
    action: Literal["grant", "resume"]
    budgets: ResearchBudget | None = None

    @model_validator(mode="after")
    def explicit_new_limits(self):
        if (self.action == "grant") != (self.budgets is not None):
            raise ValueError("只在授权新一段研发时明确填写新的额度。")
        return self


class SetResearchFocus(ResearchCommand):
    expected_revision_id: Identity
    phase: ResearchPhase
    capabilities: list[ResearchCapability] = Field(max_length=3)
    pinned: bool
    reason: Annotated[str, Field(min_length=1, max_length=1000)]


class DeliveryContent(Input):
    title: Annotated[str, Field(min_length=1, max_length=200)]
    diagnosis: Annotated[str, Field(min_length=1, max_length=12000)]
    recommendations: Annotated[str, Field(min_length=1, max_length=12000)]
    limitations: BriefText = ""
    source_version_ids: list[Identity] = Field(default_factory=list, max_length=30)
    evidence_ids: list[Identity] = Field(default_factory=list, max_length=30)


class ReviewDelivery(ResearchCommand):
    source_revision_id: Identity
    action: Literal["accept", "request_changes"]
    reason: Annotated[str, Field(min_length=1, max_length=1000)]


ExperimentKey = Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]+$", min_length=1, max_length=64)]
ExperimentText = Annotated[str, Field(min_length=1, max_length=6000)]


class RetrievalCase(Input):
    id: ExperimentKey
    question: ExperimentText
    gold_id: ExperimentKey
    candidate_ids: Annotated[list[ExperimentKey], Field(min_length=1, max_length=32)]
    observed_context: Annotated[list[ExperimentKey], Field(max_length=32)]
    observed_answer: Annotated[str, Field(max_length=4000)]
    expected_answer: ExperimentText


class RetrievalDataset(Input):
    context_limit: Annotated[int, Field(strict=True, ge=1, le=32)]
    documents: Annotated[dict[ExperimentKey, ExperimentText], Field(min_length=1, max_length=48)]
    cases: Annotated[list[RetrievalCase], Field(min_length=1, max_length=24)]

    @model_validator(mode="after")
    def fixed_references(self):
        if len({case.id for case in self.cases}) != len(self.cases):
            raise ValueError("Each question must have a distinct case ID")
        for case in self.cases:
            candidates = set(case.candidate_ids)
            if (len(candidates) != len(case.candidate_ids) or case.gold_id not in self.documents
                    or not candidates <= self.documents.keys()
                    or not set(case.observed_context) <= candidates
                    or len(case.observed_context) > self.context_limit):
                raise ValueError("候选、最终上下文和正确片段必须引用有效的固定材料。")
        return self


class ExperimentAction(ResearchCommand):
    expected_revision_id: Identity
    action: Literal["approve", "cancel", "reproduce", "acknowledge_stop"]
    reason: BriefText = ""


NodeStatus = Literal["open", "investigating", "blocked", "resolved", "deferred", "needs_review"]


class ResearchRelation(Input):
    target_id: Identity
    description: Annotated[str, Field(min_length=1, max_length=300)]


class ResearchNode(Input):
    id: Identity | None = None
    parent_id: Identity | None = None
    required: bool = True
    question: Annotated[str, Field(min_length=3, max_length=300)]
    status: NodeStatus = "open"
    criteria: Annotated[str, Field(min_length=1, max_length=1200)]
    reason: Annotated[str, Field(max_length=1200)] = ""
    conclusion: Annotated[str, Field(max_length=4000)] = ""
    details: BriefText = ""
    evidence_ids: list[Identity] = Field(default_factory=list, max_length=12)
    related: list[ResearchRelation] = Field(default_factory=list, max_length=12)


class SaveResearchNode(Input):
    action: Literal["save"]
    node: ResearchNode


class RemoveResearchNode(Input):
    action: Literal["remove"]
    node_id: Identity


MapOperation = Annotated[SaveResearchNode | RemoveResearchNode, Field(discriminator="action")]


class EditResearchMap(ResearchCommand):
    expected_revision_id: Identity
    reason: Annotated[str, Field(min_length=1, max_length=1000)]
    operation: MapOperation


class NodeAdoptionTarget(Input):
    node_id: Identity
    expected_revision_id: Identity
    expected_control_version: Annotated[int, Field(strict=True, ge=1)]


class MapAdoptionPreview(NodeAdoptionTarget):
    source_revision_id: Identity


class AdoptResearchConclusion(MapAdoptionPreview):
    operation_id: OperationId
    preview_hash: Annotated[str, Field(pattern="^[0-9a-f]{64}$")]
    reason: Annotated[str, Field(min_length=1, max_length=1000)]


class CommitAdoptionPreview(NodeAdoptionTarget):
    source_commit_id: Identity
    target_branch_id: Identity
    target_parent_id: Identity | None


class AdoptFromResearchCommit(CommitAdoptionPreview):
    operation_id: OperationId
    preview_hash: Annotated[str, Field(pattern="^[a-f0-9]{64}$")]
    reason: Annotated[str, Field(min_length=1, max_length=1000)]
