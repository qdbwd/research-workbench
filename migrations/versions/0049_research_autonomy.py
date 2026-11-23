"""保存研究授权与跨运行额度，保留旧运行和工具策略修订。"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

from knowledgeops_agent.persistence.models import utcnow

revision = "0049"
down_revision = "0048"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "research_permits",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("task_id", sa.String(36), nullable=False),
        sa.Column("branch_id", sa.String(36), nullable=False),
        sa.Column("scope_revision_id", sa.String(36), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("budgets", JSONB, nullable=False),
        sa.Column("usage", JSONB, nullable=False),
        sa.Column("latest_run_id", sa.String(36)),
        sa.Column("reason", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["branch_id", "task_id"], ["research_branches.id", "research_branches.task_id"],
            name="fk_research_permit_branch",
        ),
        sa.ForeignKeyConstraint(
            ["scope_revision_id", "branch_id"], ["research_revisions.id", "research_revisions.branch_id"],
            name="fk_research_permit_scope",
        ),
        sa.CheckConstraint(
            "status IN ('active','paused','recovery_required','exhausted','revoked')",
            name="ck_research_permit_status",
        ),
    )
    op.create_index("ix_research_permits_task_id", "research_permits", ["task_id"])
    op.add_column("runs", sa.Column("research_accounted_ms", sa.Integer(), nullable=False, server_default="0"))

    connection = op.get_bind()
    policies = sa.table("tool_policies", sa.column("id"), sa.column("revision"), sa.column("content", JSONB))
    revisions = sa.table("tool_policy_revisions", sa.column("policy_id"), sa.column("revision"),
                         sa.column("content", JSONB), sa.column("created_at"))
    row = connection.execute(sa.select(policies).where(policies.c.id == "default")).mappings().one()
    value = {**row["content"], "revision": row["revision"] + 1}
    value["enabled"] = {"builtin:continue_research": True, **value["enabled"]}
    connection.execute(policies.update().where(policies.c.id == "default").values(
        revision=value["revision"], content=value,
    ))
    connection.execute(revisions.insert().values(
        policy_id="default", revision=value["revision"], content=value, created_at=utcnow(),
    ))


def downgrade():
    raise RuntimeError("downgrade refused: preserve research authorization and usage records")
