"""保存固定研发成果及独立于验收状态的收尾屏障。"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

from knowledgeops_agent.persistence.models import utcnow

revision = "0050"
down_revision = "0049"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "research_deliveries",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("branch_id", sa.String(36), sa.ForeignKey("research_branches.id"), nullable=False),
        sa.Column("source_revision_id", sa.String(36), nullable=False),
        sa.Column("submit_run_id", sa.String(36), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("drain_state", sa.String(16), nullable=False),
        sa.Column("content", JSONB, nullable=False),
        sa.Column("blockers", JSONB, nullable=False),
        sa.Column("review_reason", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(
            ["source_revision_id", "branch_id"], ["research_revisions.id", "research_revisions.branch_id"],
            name="fk_research_delivery_source",
        ),
        sa.CheckConstraint(
            "status IN ('finalizing','blocked','submitted','accepted','changes_requested','superseded')",
            name="ck_research_delivery_status",
        ),
        sa.CheckConstraint("drain_state IN ('pending','blocked','settled')", name="ck_research_delivery_drain"),
    )
    op.create_index("ix_research_deliveries_branch_id", "research_deliveries", ["branch_id"])
    op.add_column("research_branches", sa.Column("pending_delivery_id", sa.String(36)))
    op.create_foreign_key(
        "fk_research_branch_pending_delivery", "research_branches", "research_deliveries",
        ["pending_delivery_id"], ["id"],
    )

    connection = op.get_bind()
    policies = sa.table("tool_policies", sa.column("id"), sa.column("revision"), sa.column("content", JSONB))
    revisions = sa.table("tool_policy_revisions", sa.column("policy_id"), sa.column("revision"),
                         sa.column("content", JSONB), sa.column("created_at"))
    row = connection.execute(sa.select(policies).where(policies.c.id == "default")).mappings().one()
    value = {**row["content"], "revision": row["revision"] + 1}
    value["enabled"] = {"builtin:submit_research_delivery": True, **value["enabled"]}
    connection.execute(policies.update().where(policies.c.id == "default").values(
        revision=value["revision"], content=value,
    ))
    connection.execute(revisions.insert().values(
        policy_id="default", revision=value["revision"], content=value, created_at=utcnow(),
    ))


def downgrade():
    raise RuntimeError("downgrade refused: preserve fixed research deliveries and reviews")
