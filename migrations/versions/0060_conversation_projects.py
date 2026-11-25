"""会话项目归类。旧会话默认未分组，独立于运行、研发分支和资料。"""

import sqlalchemy as sa
from alembic import op

revision = "0060"
down_revision = "0059"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "conversation_projects",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("pinned", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.add_column("conversations", sa.Column("project_id", sa.String(36),
                                             sa.ForeignKey("conversation_projects.id")))
    op.add_column("conversations", sa.Column("project_revision", sa.Integer(), server_default="0", nullable=False))
    op.create_index("ix_conversations_project_id", "conversations", ["project_id"])


def downgrade():
    if op.get_bind().scalar(sa.text("SELECT 1 FROM conversation_projects LIMIT 1")):
        raise RuntimeError("已有会话项目，请从升级前备份恢复后再降级。")
    op.drop_index("ix_conversations_project_id", table_name="conversations")
    op.drop_column("conversations", "project_revision")
    op.drop_column("conversations", "project_id")
    op.drop_table("conversation_projects")
