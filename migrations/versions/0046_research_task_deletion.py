"""研发任务移除状态，保留固定版本身份及操作回执。"""

import sqlalchemy as sa
from alembic import op

revision = "0046"
down_revision = "0045"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("research_tasks", sa.Column("deleted_at", sa.DateTime(timezone=True)))


def downgrade():
    raise RuntimeError("downgrade refused: deleted research tasks must not reappear")
