"""Add logical RAG collections and the asset indexing outbox."""

import sqlalchemy as sa
from alembic import op

revision = "0065"
down_revision = "0064"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("rag_settings", sa.Column("auto_index_enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")))
    for table in ("knowledge_search_chunks", "source_search_chunks"):
        op.add_column(table, sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")))
    op.create_table(
        "rag_collections",
        sa.Column("id", sa.String(36), primary_key=True, nullable=False),
        sa.Column("name", sa.String(120), unique=True, nullable=False),
        sa.Column("description", sa.String(500), server_default="", nullable=False),
        sa.Column("revision", sa.Integer, server_default="1", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("revision >= 1", name="ck_rag_collections_revision"),
    )
    op.create_table(
        "rag_collection_items",
        sa.Column("collection_id", sa.String(36), sa.ForeignKey("rag_collections.id", ondelete="CASCADE"), primary_key=True, nullable=False),
        sa.Column("asset_kind", sa.String(20), primary_key=True, nullable=False),
        sa.Column("asset_id", sa.String(36), primary_key=True, nullable=False),
        sa.Column("added_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("asset_kind IN ('knowledge', 'source')", name="ck_rag_collection_items_kind"),
    )
    op.create_index("ix_rag_collection_items_asset", "rag_collection_items", ["asset_kind","asset_id"])
    op.create_table(
        "rag_index_jobs",
        sa.Column("id", sa.String(36), primary_key=True, nullable=False),
        sa.Column("asset_kind", sa.String(20), nullable=False),
        sa.Column("asset_id", sa.String(36), nullable=False),
        sa.Column("version_id", sa.String(36), nullable=False),
        sa.Column("profile_id", sa.String(160), nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("generation", sa.Integer, server_default="1", nullable=False),
        sa.Column("attempts", sa.Integer, server_default="0", nullable=False),
        sa.Column("max_attempts", sa.Integer, server_default="3", nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("chunks_total", sa.Integer, server_default="0", nullable=False),
        sa.Column("chunks_embedded", sa.Integer, server_default="0", nullable=False),
        sa.Column("last_error", sa.Text, nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("asset_kind IN ('knowledge', 'source')", name="ck_rag_index_jobs_kind"),
        sa.CheckConstraint("status IN ('pending', 'indexing', 'ready', 'failed')", name="ck_rag_index_jobs_status"),
        sa.CheckConstraint("generation >= 1", name="ck_rag_index_jobs_generation"),
        sa.CheckConstraint("attempts >= 0", name="ck_rag_index_jobs_attempts"),
        sa.CheckConstraint("max_attempts >= 1", name="ck_rag_index_jobs_max_attempts"),
        sa.UniqueConstraint("asset_kind", "asset_id", name="uq_rag_index_jobs_asset"),
    )
    op.create_index("ix_rag_index_jobs_due", "rag_index_jobs", ["status","next_attempt_at"])


def downgrade():
    op.drop_table("rag_index_jobs")
    op.drop_table("rag_collection_items")
    op.drop_table("rag_collections")
    for table in ("source_search_chunks", "knowledge_search_chunks"):
        op.drop_column(table, "enabled")
    op.drop_column("rag_settings", "auto_index_enabled")
