"""存储研发使用的不可变方法正文。"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0047"
down_revision = "0046"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "research_method_bundles",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("content", JSONB, nullable=False),
    )


def downgrade():
    raise RuntimeError("downgrade refused: frozen research methods must be preserved")
