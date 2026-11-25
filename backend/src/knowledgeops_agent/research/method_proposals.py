"""把已验收运维试点的固定证据转为一条可人工审阅的方法更新。"""

from difflib import unified_diff

from sqlalchemy import select

from knowledgeops_agent.app.conversations import fingerprint
from knowledgeops_agent.harness.skills.imports import inspect_package
from knowledgeops_agent.harness.tools.configuration import lock_configuration
from knowledgeops_agent.persistence.models import ExtensionOperation, SkillPackageState, utcnow
from knowledgeops_agent.research.trial import completed_trial

SKILL = "evidence-check"
TITLE = "定位检索问答的缺证与误引"
METHOD = (
    f"## {TITLE}\n\n"
    "当已有正确材料但问答遗漏、误用或误引时，固定同一问题、允许使用的资料版本和证据预算，依次核对：\n\n"
    "1. 正确片段是否进入候选。未进入时检查数据版本、访问范围和召回，重排无法补回缺失候选。\n"
    "2. 正确片段是否真正进入模型请求。候选命中但未送达时，对照排序、去重和截断，用相同预算测试修正。\n"
    "3. 已送达的原文是否支持最终回答及每条引用。把错误回答样例与正式依据区分开，"
    "检查漏引、误引和过度概括。正确片段已送达时，单独排查生成与引用对齐。\n\n"
    "用留出题分别记录候选覆盖、上下文覆盖和回答引用，保留无答案反例。固定候选上的排序改善"
    "只支持对应的上下文结论，生成质量和实际环境中的效果需另测。\n"
)
STATES = {
    "uploading": "preparing",
    "ready": "pending_review",
    "failed": "publish_failed",
    "succeeded": "published",
    "cancelled": "rejected",
    "expired": "expired",
}


def _evidence(report_id):
    case = completed_trial()
    if case is None or case["report"]["id"] != report_id or case["report"]["status"] != "accepted":
        raise KeyError("没有可用于此提案的已验收案例。")
    report, experiment = case["report"], case["experiment"]
    receipt = experiment["receipt"]
    rows = receipt["output"]["cases"]
    diagnostics = {
        (row["gold_in_candidates"], row["observed_context_has_gold"], row["reranked_context_has_gold"],
         row["observed_answer_correct"]) for row in rows
    }
    if (experiment["status"] != "completed" or receipt["status"] != "completed" or receipt["exit_code"] != 0
        or receipt["attempt_id"] != experiment["id"]
        or experiment["evidence_id"] not in report["content"]["evidence_ids"]
        or not {(False, False, False, False), (True, False, True, False), (True, True, True, False)} <= diagnostics):
        raise ValueError("固定实验缺少生成此方法提案所需的证据。")
    return {
        "case_key": case["key"], "case_title": case["title"], "report_title": report["content"]["title"],
        "report_id": report["id"], "report_status": report["status"], "experiment_id": experiment["id"],
        "evidence_id": experiment["evidence_id"], "commit_id": case["comparison"]["after"]["id"],
        "record_hash": fingerprint({"report": report, "receipt": receipt}),
        "decision": report["content"]["recommendations"], "finding": receipt["output"]["conclusion"],
        "limitation": receipt["output"]["limitation"],
    }


def _query():
    return select(ExtensionOperation).where(
        ExtensionOperation.kind == "skill_import",
        ExtensionOperation.parameters["method_review"]["review_hash"].astext.is_not(None),
    ).order_by(ExtensionOperation.created_at.desc(), ExtensionOperation.id.desc())


def _view(row):
    details = row.parameters["method_review"]
    state = STATES[row.status]
    if row.status in {"ready", "failed", "uploading"} and row.expires_at <= utcnow():
        state = "expired"
    return {
        **details, "id": row.id, "state": state, "target": row.target, "base_revision": row.expected_revision,
        "published_revision": row.result.get("package", {}).get("revision"),
    }


def list_proposals(manager):
    with manager.sessions() as session:
        return {"items": [_view(row) for row in session.scalars(_query().limit(30))]}


def _read(manager, identity):
    with manager.sessions() as session:
        row = session.get(ExtensionOperation, identity)
        if row is None or row.kind != "skill_import" or "method_review" not in row.parameters:
            raise KeyError("没有找到该方法提案。")
        return _view(row)


def prepare(manager, report_id):
    evidence = _evidence(report_id)
    with manager.sessions.begin() as session:
        lock_configuration(session)
        manager._recover_in(session)
        state = session.get(SkillPackageState, SKILL)
        if state is None or state.removed:
            raise ValueError("请先在技能管理中接管或导入「证据核对」。")
        revision, base_hash = state.revision, state.content_hash
        _, path = manager._current(session, SKILL, revision)
        current = manager.storage.read_files(path)
        original = current["SKILL.md"].decode("utf-8")
        if f"## {TITLE}" in original:
            published = session.scalar(_query().where(
                ExtensionOperation.target == SKILL, ExtensionOperation.status == "succeeded",
                ExtensionOperation.parameters["method_review"]["evidence"]["record_hash"].astext == evidence["record_hash"],
            ).limit(1))
            if published is not None and METHOD.strip() in original:
                return _view(published)
            raise ValueError("当前技能已经包含同名方法，请在技能管理中核对。")
    separator = "" if original.endswith("\n\n") else "\n" if original.endswith("\n") else "\n\n"
    revised = original + separator + METHOD
    current["SKILL.md"] = revised.encode("utf-8")
    candidate = inspect_package(list(current.items()))
    proposal = {
        "title": TITLE, "base_hash": base_hash, "candidate_hash": candidate.digest, "evidence": evidence,
        "scope": "本工作区按现有技能配置允许读取「证据核对」的新运行。已冻结的运行继续使用原版本。",
        "diff": "\n".join(unified_diff(original.splitlines(), revised.splitlines(),
                                       fromfile="当前 SKILL.md", tofile="提议 SKILL.md", n=1, lineterm="")),
    }
    proposal["review_hash"] = fingerprint({"target": SKILL, "revision": revision, "proposal": proposal})
    operation = manager.stage(list(current.items()), expected_revision=revision, _method_review=proposal)
    return _read(manager, operation["id"])


def review(manager, identity, action, review_hash):
    proposal = _read(manager, identity)
    if action == "reject":
        manager.discard(identity, review_hash=review_hash)
    else:
        if proposal["state"] != "published":
            evidence = proposal["evidence"]
            if _evidence(evidence["report_id"])["record_hash"] != evidence["record_hash"]:
                raise ValueError("案例证据已更新，请根据新的已验收结果重新准备。")
        manager.commit(identity, review_hash=review_hash)
    return _read(manager, identity)
