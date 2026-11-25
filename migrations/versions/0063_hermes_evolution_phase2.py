"""0063：保存技能不可变修订、使用遥测及 Curator 配置。"""

import sqlalchemy as sa
from alembic import op

revision = "0063"
down_revision = "0062"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "evolution_settings",
        sa.Column("curator_stale_days", sa.Integer(), server_default="30", nullable=False),
    )
    op.add_column(
        "evolution_settings", sa.Column("curator_last_run_at", sa.DateTime(timezone=True))
    )
    op.create_check_constraint(
        "ck_evolution_settings_curator_stale_days",
        "evolution_settings",
        "curator_stale_days BETWEEN 7 AND 365",
    )
    op.create_table(
        "skill_revisions",
        sa.Column(
            "skill_name",
            sa.String(64),
            sa.ForeignKey("skill_packages.name", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("revision", sa.Integer(), primary_key=True),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("snapshot_path", sa.Text(), nullable=False),
        sa.Column("snapshot_sha256", sa.String(64), nullable=False),
        sa.Column("source_kind", sa.String(32), nullable=False),
        sa.Column(
            "change_id", sa.String(36), sa.ForeignKey("evolution_changes.id", ondelete="SET NULL")
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "source_kind IN ('manual_import','method_proposal','evolution_patch','rollback','bootstrap','curator')",
            name="ck_skill_revisions_source_kind",
        ),
    )
    op.create_table(
        "skill_usage_telemetry",
        sa.Column(
            "skill_name",
            sa.String(64),
            sa.ForeignKey("skill_packages.name", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("load_count", sa.Integer(), nullable=False, server_default="0"),
        sa.CheckConstraint("load_count >= 0", name="ck_skill_telemetry_load_count"),
        sa.Column("success_run_count", sa.Integer(), nullable=False, server_default="0"),
        sa.CheckConstraint("success_run_count >= 0", name="ck_skill_telemetry_success_run_count"),
        sa.Column("failed_run_count", sa.Integer(), nullable=False, server_default="0"),
        sa.CheckConstraint("failed_run_count >= 0", name="ck_skill_telemetry_failed_run_count"),
        sa.Column("correction_count", sa.Integer(), nullable=False, server_default="0"),
        sa.CheckConstraint("correction_count >= 0", name="ck_skill_telemetry_correction_count"),
        sa.Column("last_used_at", sa.DateTime(timezone=True)),
        sa.Column("last_updated_at", sa.DateTime(timezone=True)),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )


def downgrade():
    op.drop_table("skill_usage_telemetry")
    op.drop_table("skill_revisions")
    op.drop_constraint(
        "ck_evolution_settings_curator_stale_days", "evolution_settings", type_="check"
    )
    op.drop_column("evolution_settings", "curator_last_run_at")
    op.drop_column("evolution_settings", "curator_stale_days")
