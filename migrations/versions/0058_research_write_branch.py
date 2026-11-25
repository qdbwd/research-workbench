"""为研发任务保存后续会话的写入分支。旧任务继续使用默认分支。"""

import sqlalchemy as sa
from alembic import op

revision = "0058"
down_revision = "0057"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("research_tasks", sa.Column("write_branch_id", sa.String(36)))
    op.add_column("research_tasks", sa.Column("write_version", sa.Integer(), server_default="1", nullable=False))
    op.create_foreign_key("fk_research_task_write_branch", "research_tasks", "research_branches",
                          ["write_branch_id", "id"], ["id", "task_id"])
    op.create_check_constraint("ck_research_task_write_version", "research_tasks", "write_version >= 1")


def downgrade():
    if op.get_bind().scalar(sa.text("SELECT 1 FROM research_tasks WHERE write_version > 1 LIMIT 1")):
        raise RuntimeError("已有写入分支变更，请从升级前备份恢复后再降级。")
    op.drop_constraint("ck_research_task_write_version", "research_tasks", type_="check")
    op.drop_constraint("fk_research_task_write_branch", "research_tasks", type_="foreignkey")
    op.drop_column("research_tasks", "write_version")
    op.drop_column("research_tasks", "write_branch_id")
