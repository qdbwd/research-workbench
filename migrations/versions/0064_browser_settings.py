"""单行浏览器配置。运行历史继续使用既有冻结快照。"""

import sqlalchemy as sa
from alembic import op

revision = "0064"
down_revision = "0063"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "browser_settings",
        sa.Column("id", sa.String(32), primary_key=True, server_default="default"),
        sa.Column("local_attach_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("default_mode", sa.String(16), nullable=False, server_default="launch"),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("id = 'default'", name="ck_browser_settings_singleton"),
        sa.CheckConstraint("default_mode IN ('launch', 'attach')", name="ck_browser_settings_mode"),
        sa.CheckConstraint("revision >= 1", name="ck_browser_settings_revision"),
        sa.CheckConstraint(
            "default_mode <> 'attach' OR local_attach_enabled = true", name="ck_browser_settings_attach"
        ),
    )
    op.execute("INSERT INTO browser_settings (id) VALUES ('default') ON CONFLICT (id) DO NOTHING")


def downgrade():
    op.drop_table("browser_settings")
