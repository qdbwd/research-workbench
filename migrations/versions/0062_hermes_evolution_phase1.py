"""Hermes Phase 1：双槽记忆与隔离复盘。"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0062"
down_revision = "0061"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "skill_packages",
        sa.Column("protection_level", sa.String(20), nullable=False, server_default="normal"),
    )
    op.add_column(
        "skill_packages", sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_check_constraint(
        "ck_skill_packages_protection_level",
        "skill_packages",
        "protection_level IN ('normal', 'pinned', 'locked')",
    )
    op.add_column(
        "preferences",
        sa.Column("memory_slot", sa.String(20), nullable=False, server_default="user"),
    )
    op.add_column(
        "preferences",
        sa.Column("protection_level", sa.String(20), nullable=False, server_default="normal"),
    )
    op.add_column(
        "preferences", sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_check_constraint(
        "ck_preferences_memory_slot", "preferences", "memory_slot IN ('user', 'system')"
    )
    op.create_check_constraint(
        "ck_preferences_protection_level",
        "preferences",
        "protection_level IN ('normal', 'pinned', 'locked')",
    )
    op.create_table(
        "evolution_settings",
        sa.Column("id", sa.String(32), primary_key=True, nullable=False, server_default="default"),
        sa.Column("auto_review_enabled", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("memory_auto_write", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("skill_auto_write", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("user_slot_char_limit", sa.Integer, nullable=False, server_default="2400"),
        sa.Column("system_slot_char_limit", sa.Integer, nullable=False, server_default="3600"),
        sa.Column("review_turn_threshold", sa.Integer, nullable=False, server_default="6"),
        sa.Column("skill_tool_threshold", sa.Integer, nullable=False, server_default="8"),
        sa.Column("daily_budget_requests", sa.Integer, nullable=False, server_default="20"),
        sa.Column("revision", sa.Integer, nullable=False, server_default="1"),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint("id = 'default'", name="ck_evolution_settings_singleton"),
        sa.CheckConstraint(
            "user_slot_char_limit BETWEEN 200 AND 20000",
            name="ck_evolution_settings_user_slot_char_limit",
        ),
        sa.CheckConstraint(
            "system_slot_char_limit BETWEEN 200 AND 30000",
            name="ck_evolution_settings_system_slot_char_limit",
        ),
        sa.CheckConstraint(
            "review_turn_threshold BETWEEN 1 AND 100",
            name="ck_evolution_settings_review_turn_threshold",
        ),
        sa.CheckConstraint(
            "skill_tool_threshold BETWEEN 1 AND 100",
            name="ck_evolution_settings_skill_tool_threshold",
        ),
        sa.CheckConstraint(
            "daily_budget_requests BETWEEN 0 AND 500",
            name="ck_evolution_settings_daily_budget_requests",
        ),
    )
    op.create_table(
        "evolution_jobs",
        sa.Column("id", sa.String(36), primary_key=True, nullable=False),
        sa.Column(
            "run_id",
            sa.String(36),
            sa.ForeignKey("runs.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("job_type", sa.String(32), nullable=False),
        sa.Column("trigger_kind", sa.String(32), nullable=False),
        sa.Column(
            "conversation_id",
            sa.String(36),
            sa.ForeignKey("conversations.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("focus_hint", sa.Text, nullable=False, server_default=""),
        sa.Column("status", sa.String(30), nullable=False, server_default="pending"),
        sa.Column("cursor_from", JSONB, nullable=False, server_default="{}"),
        sa.Column("cursor_to", JSONB, nullable=False, server_default="{}"),
        sa.Column("result_summary", JSONB, nullable=False, server_default="{}"),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "job_type IN ('memory_review', 'skill_review', 'combined_review', 'curator')",
            name="ck_evolution_jobs_job_type",
        ),
        sa.CheckConstraint(
            "trigger_kind IN ('auto_threshold', 'high_signal', 'manual', 'curator_schedule')",
            name="ck_evolution_jobs_trigger_kind",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'completed', 'failed', 'cancelled', 'interrupted')",
            name="ck_evolution_jobs_status",
        ),
    )
    op.create_index("ix_evolution_jobs_conversation_id", "evolution_jobs", ["conversation_id"])
    op.create_table(
        "evolution_cursors",
        sa.Column(
            "conversation_id",
            sa.String(36),
            sa.ForeignKey("conversations.id", ondelete="CASCADE"),
            primary_key=True,
            nullable=False,
        ),
        sa.Column("last_reviewed_message_id", sa.String(36), nullable=True),
        sa.Column("last_reviewed_run_id", sa.String(36), nullable=True),
        sa.Column("last_reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("pending_human_turns", sa.Integer, nullable=False, server_default="0"),
        sa.Column("pending_tool_calls", sa.Integer, nullable=False, server_default="0"),
        sa.Column("pending_high_signal_events", sa.Integer, nullable=False, server_default="0"),
        sa.Column(
            "active_job_id",
            sa.String(36),
            sa.ForeignKey("evolution_jobs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "pending_human_turns >= 0", name="ck_evolution_cursors_pending_human_turns"
        ),
        sa.CheckConstraint(
            "pending_tool_calls >= 0", name="ck_evolution_cursors_pending_tool_calls"
        ),
        sa.CheckConstraint(
            "pending_high_signal_events >= 0",
            name="ck_evolution_cursors_pending_high_signal_events",
        ),
    )
    op.create_table(
        "evolution_changes",
        sa.Column("id", sa.String(36), primary_key=True, nullable=False),
        sa.Column(
            "job_id",
            sa.String(36),
            sa.ForeignKey("evolution_jobs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "conversation_id",
            sa.String(36),
            sa.ForeignKey("conversations.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("target_kind", sa.String(20), nullable=False),
        sa.Column("target_id", sa.String(128), nullable=True),
        sa.Column("action", sa.String(24), nullable=False),
        sa.Column("review_state", sa.String(24), nullable=False),
        sa.Column("base_revision", sa.Integer, nullable=True),
        sa.Column("applied_revision", sa.Integer, nullable=True),
        sa.Column("proposal_payload", JSONB, nullable=False, server_default="{}"),
        sa.Column("diff_summary", JSONB, nullable=False, server_default="{}"),
        sa.Column("diff_artifact_path", sa.Text, nullable=True),
        sa.Column("diff_artifact_sha256", sa.String(64), nullable=True),
        sa.Column("evidence_refs", JSONB, nullable=False, server_default="{}"),
        sa.Column("security_scan_result", JSONB, nullable=False, server_default="{}"),
        sa.Column("idempotency_key", sa.String(160), nullable=False, unique=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "target_kind IN ('memory', 'skill')", name="ck_evolution_changes_target_kind"
        ),
        sa.CheckConstraint(
            "action IN ('create', 'update', 'patch', 'deprecate', 'merge', 'archive', 'rollback')",
            name="ck_evolution_changes_action",
        ),
        sa.CheckConstraint(
            "review_state IN ('applied', 'pending_review', 'approved', 'rejected', 'superseded', 'rolled_back', 'skipped_conflict')",
            name="ck_evolution_changes_review_state",
        ),
    )
    op.create_index("ix_evolution_changes_job_id", "evolution_changes", ["job_id"])
    op.create_index(
        "ix_evolution_changes_conversation_id", "evolution_changes", ["conversation_id"]
    )
    op.create_table(
        "conversation_memory_snapshots",
        sa.Column(
            "conversation_id",
            sa.String(36),
            sa.ForeignKey("conversations.id", ondelete="CASCADE"),
            primary_key=True,
            nullable=False,
        ),
        sa.Column("snapshot_hash", sa.String(64), nullable=False),
        sa.Column("frozen_entries", JSONB, nullable=False, server_default="[]"),
        sa.Column(
            "frozen_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
        ),
    )
    op.execute("INSERT INTO evolution_settings (id) VALUES ('default')")


def downgrade() -> None:
    op.drop_table("conversation_memory_snapshots")
    op.drop_table("evolution_changes")
    op.drop_table("evolution_cursors")
    op.drop_table("evolution_jobs")
    op.drop_table("evolution_settings")
    op.drop_constraint("ck_preferences_memory_slot", "preferences", type_="check")
    op.drop_constraint("ck_preferences_protection_level", "preferences", type_="check")
    op.drop_column("preferences", "last_used_at")
    op.drop_column("preferences", "protection_level")
    op.drop_column("preferences", "memory_slot")
    op.drop_constraint("ck_skill_packages_protection_level", "skill_packages", type_="check")
    op.drop_column("skill_packages", "archived_at")
    op.drop_column("skill_packages", "protection_level")
