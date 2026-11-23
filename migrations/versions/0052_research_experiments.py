"""增加独立实验尝试，保留所有旧快照和历次审批。"""

from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0052"
down_revision = "0051"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "research_experiments",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("branch_id", sa.String(36), sa.ForeignKey("research_branches.id"), nullable=False),
        sa.Column("source_revision_id", sa.String(36), nullable=False),
        sa.Column("source_control_version", sa.Integer(), nullable=False),
        sa.Column("propose_run_id", sa.String(36), nullable=False),
        sa.Column("reproduced_from", sa.String(36), sa.ForeignKey("research_experiments.id")),
        sa.Column("specification", JSONB, nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("reason", sa.Text()),
        sa.Column("evidence_id", sa.String(36)),
        sa.Column("receipt", JSONB),
        sa.Column("dispatched_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["source_revision_id", "branch_id"], ["research_revisions.id", "research_revisions.branch_id"],
            name="fk_research_experiment_source",
        ),
        sa.CheckConstraint(
            "status IN ('awaiting_approval','approved','dispatched','running','cancel_requested',"
            "'completed','cancelled','failed','timed_out','unknown','stale')",
            name="ck_research_experiment_status",
        ),
    )
    op.create_index("ix_research_experiments_branch_id", "research_experiments", ["branch_id"])
    connection = op.get_bind()
    policies = sa.table("tool_policies", sa.column("id"), sa.column("revision"), sa.column("content", JSONB))
    revisions = sa.table("tool_policy_revisions", sa.column("policy_id"), sa.column("revision"),
                         sa.column("content", JSONB), sa.column("created_at"))
    row = connection.execute(sa.select(policies).where(policies.c.id == "default")).mappings().one()
    value = {**row["content"], "revision": row["revision"] + 1}
    value["enabled"] = {
        "builtin:propose_research_experiment": True, "builtin:read_research_experiment": True, **value["enabled"],
    }
    connection.execute(policies.update().where(policies.c.id == "default").values(
        revision=value["revision"], content=value,
    ))
    connection.execute(revisions.insert().values(
        policy_id="default", revision=value["revision"], content=value, created_at=datetime.now(UTC),
    ))


def downgrade():
    raise RuntimeError("downgrade refused: preserve experiment receipts and evidence")
