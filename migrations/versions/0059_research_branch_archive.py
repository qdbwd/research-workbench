"""研发分支归档只添加时间标记，现有提交和资产保留。"""

import sqlalchemy as sa
from alembic import op

revision = "0059"
down_revision = "0058"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("research_branches", sa.Column("archived_at", sa.DateTime(timezone=True)))


def downgrade():
    if op.get_bind().scalar(sa.text("SELECT 1 FROM research_branches WHERE archived_at IS NOT NULL LIMIT 1")):
        raise RuntimeError("已有归档分支，请从升级前备份恢复后再降级。")
    op.drop_column("research_branches", "archived_at")
