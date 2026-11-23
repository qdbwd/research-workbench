"""把研发焦点工具加入新的工具策略修订。"""

from copy import deepcopy
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0048"
down_revision = "0047"
branch_labels = None
depends_on = None


def upgrade():
    connection = op.get_bind()
    policies = sa.table("tool_policies", sa.column("id"), sa.column("revision"), sa.column("content", JSONB))
    revisions = sa.table("tool_policy_revisions", sa.column("policy_id"), sa.column("revision"),
                         sa.column("content", JSONB), sa.column("created_at"))
    row = connection.execute(sa.select(policies).where(policies.c.id == "default")).mappings().one()
    value = deepcopy(row["content"])
    value["enabled"].setdefault("builtin:set_research_focus", True)
    value["revision"] += 1
    connection.execute(policies.update().where(policies.c.id == "default").values(
        revision=value["revision"], content=value,
    ))
    connection.execute(revisions.insert().values(
        policy_id="default", revision=value["revision"], content=value, created_at=datetime.now(UTC),
    ))


def downgrade():
    raise RuntimeError("downgrade refused: preserve the historical tool policy revisions")
