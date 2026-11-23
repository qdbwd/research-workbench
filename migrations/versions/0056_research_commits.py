"""正式提交独立于自动修订，旧修订和分支身份原样保留。"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0056"
down_revision = "0055"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "research_commits",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("branch_id", sa.String(36), sa.ForeignKey("research_branches.id"), nullable=False),
        sa.Column("revision_id", sa.String(36), nullable=False),
        sa.Column("parent_commit_id", sa.String(36), sa.ForeignKey("research_commits.id")),
        sa.Column("number", sa.Integer, nullable=False),
        sa.Column("message", sa.Text, nullable=False),
        sa.Column("snapshot", JSONB, nullable=False),
        sa.Column("snapshot_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("branch_id", "number", name="uq_research_commit_number"),
        sa.ForeignKeyConstraint(
            ["revision_id", "branch_id"], ["research_revisions.id", "research_revisions.branch_id"],
            name="fk_research_commit_revision",
        ),
    )
    op.add_column("research_branches", sa.Column("fork_commit_id", sa.String(36)))
    op.create_foreign_key("fk_research_branch_fork_commit", "research_branches", "research_commits",
                          ["fork_commit_id"], ["id"])


def downgrade():
    if op.get_bind().scalar(sa.text("SELECT 1 FROM research_commits LIMIT 1")):
        raise RuntimeError("已有正式研发提交，请从升级前备份恢复后再降级。")
    op.drop_constraint("fk_research_branch_fork_commit", "research_branches", type_="foreignkey")
    op.drop_column("research_branches", "fork_commit_id")
    op.drop_table("research_commits")
