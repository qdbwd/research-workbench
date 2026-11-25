"""在一个运行预算和成功事务内协调复盘类型。"""

from knowledgeops_agent.evolution import curator, review, skill_review


def prepare_evolution(context, run):
    kind = run["input"].get("job_type", "memory_review")
    if kind == "curator":
        return curator.prepare_curator(context, run)
    if kind == "skill_review":
        return skill_review.prepare_skill_review(context, run)
    memory = review.execute(context, run)
    if kind != "combined_review":
        return memory
    skill = skill_review.prepare_skill_review(context, run)

    def finish(session):
        first = memory(session)
        second = skill(session)
        from knowledgeops_agent.persistence.models import EvolutionJob

        job = session.get(EvolutionJob, first["job_id"])
        summary = {"memory": first["result_summary"], "skill": second["result_summary"]}
        job.result_summary = summary
        return {**first, "result_summary": summary}

    return finish
