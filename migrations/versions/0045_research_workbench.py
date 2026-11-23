"""独立研发任务与固定简报修订；不改写旧会话或运行记录。"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0045"
down_revision = "0044"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "research_tasks",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column(
            "home_conversation_id",
            sa.String(36),
            sa.ForeignKey("conversations.id", ondelete="SET NULL"),
            unique=True,
        ),
        sa.Column("main_branch_id", sa.String(36), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("control_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("control_version >= 1", name="ck_research_task_control_version"),
        sa.CheckConstraint(
            "status IN ('open','paused','awaiting_acceptance','completed','closed')",
            name="ck_research_task_status",
        ),
    )
    op.create_table(
        "research_branches",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("task_id", sa.String(36), sa.ForeignKey("research_tasks.id"), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("head_revision_id", sa.String(36), nullable=False),
        sa.UniqueConstraint("id", "task_id", name="uq_research_branch_task"),
    )
    op.create_index("ix_research_branches_task_id", "research_branches", ["task_id"])
    op.create_table(
        "research_revisions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "branch_id", sa.String(36), sa.ForeignKey("research_branches.id"), nullable=False
        ),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("parent_revision_id", sa.String(36)),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("state", JSONB(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("actor", sa.String(20), nullable=False),
        sa.Column("origin", sa.String(40), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("id", "branch_id", name="uq_research_revision_branch"),
        sa.UniqueConstraint("branch_id", "number", name="uq_research_revision_number"),
        sa.ForeignKeyConstraint(
            ["parent_revision_id", "branch_id"],
            ["research_revisions.id", "research_revisions.branch_id"],
            name="fk_research_revision_parent",
        ),
        sa.CheckConstraint(
            "(number = 1 AND parent_revision_id IS NULL) OR (number > 1 AND parent_revision_id IS NOT NULL)",
            name="ck_research_revision_parent",
        ),
        sa.CheckConstraint("schema_version >= 1", name="ck_research_revision_schema_version"),
    )
    # 首次创建一次写入任务、分支与快照，提交时验证所有指针归属。
    op.create_foreign_key(
        "fk_research_task_main_branch",
        "research_tasks",
        "research_branches",
        ["main_branch_id", "id"],
        ["id", "task_id"],
        deferrable=True,
        initially="DEFERRED",
    )
    op.create_foreign_key(
        "fk_research_branch_head",
        "research_branches",
        "research_revisions",
        ["head_revision_id", "id"],
        ["id", "branch_id"],
        deferrable=True,
        initially="DEFERRED",
    )


def downgrade():
    raise RuntimeError(
        "downgrade refused: research tasks and immutable revisions must be preserved"
    )
