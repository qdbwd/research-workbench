"""固定主线地图内容，给新运行提供读取和编辑专用工具。"""

from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0051"
down_revision = "0050"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "research_maps",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("content", JSONB, nullable=False),
    )
    connection = op.get_bind()
    policies = sa.table("tool_policies", sa.column("id"), sa.column("revision"), sa.column("content", JSONB))
    revisions = sa.table("tool_policy_revisions", sa.column("policy_id"), sa.column("revision"),
                         sa.column("content", JSONB), sa.column("created_at"))
    row = connection.execute(sa.select(policies).where(policies.c.id == "default")).mappings().one()
    value = {**row["content"], "revision": row["revision"] + 1}
    value["enabled"] = {"builtin:read_research_map": True, "builtin:update_research_map": True, **value["enabled"]}
    connection.execute(policies.update().where(policies.c.id == "default").values(
        revision=value["revision"], content=value,
    ))
    connection.execute(revisions.insert().values(
        policy_id="default", revision=value["revision"], content=value, created_at=datetime.now(UTC),
    ))


def downgrade():
    raise RuntimeError("downgrade refused: preserve immutable research map manifests")
