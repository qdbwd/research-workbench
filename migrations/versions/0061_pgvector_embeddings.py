"""新增 pgvector 派生向量索引表与 RAG 运行时开关表。"""

import sqlalchemy as sa
from alembic import op

from knowledgeops_agent.persistence.models import PgVector

revision = "0061"
down_revision = "0060"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(sa.text("CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public"))

    op.create_table(
        "embedding_profiles",
        sa.Column("profile_id", sa.String(160), primary_key=True),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("model", sa.String(160), nullable=False),
        sa.Column("dimensions", sa.Integer(), nullable=False),
        sa.Column("query_prefix", sa.Text(), nullable=False, server_default=""),
        sa.Column("document_prefix", sa.Text(), nullable=False, server_default=""),
        sa.Column("normalize", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("contextual", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("status", sa.String(24), nullable=False, server_default="building"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("status IN ('building','ready','stale')", name="ck_embedding_profile_status"),
        sa.CheckConstraint("dimensions > 0", name="ck_embedding_profile_dimensions"),
    )

    op.create_table(
        "rag_settings",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("hybrid_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("rerank_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("contextual_embedding_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("rrf_k", sa.Integer(), nullable=False, server_default="60"),
        sa.Column("rerank_top_k", sa.Integer(), nullable=False, server_default="20"),
        sa.Column(
            "active_profile_id",
            sa.String(160),
            sa.ForeignKey("embedding_profiles.profile_id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("rrf_k >= 1 AND rrf_k <= 1000", name="ck_rag_settings_rrf_k"),
        sa.CheckConstraint("rerank_top_k >= 1 AND rerank_top_k <= 100", name="ck_rag_settings_rerank_top_k"),
    )

    op.create_table(
        "knowledge_chunk_embeddings",
        sa.Column("document_id", sa.String(36), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column(
            "profile_id",
            sa.String(160),
            sa.ForeignKey("embedding_profiles.profile_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "version_id",
            sa.String(36),
            sa.ForeignKey("knowledge_versions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("embedding", PgVector(), nullable=False),
        sa.Column("dimensions", sa.Integer(), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("document_id", "chunk_index", "profile_id"),
        sa.ForeignKeyConstraint(
            ["document_id", "chunk_index"],
            ["knowledge_search_chunks.document_id", "knowledge_search_chunks.chunk_index"],
            ondelete="CASCADE",
            name="fk_knowledge_chunk_embeddings_chunk",
        ),
    )
    op.create_index(
        "ix_knowledge_chunk_embeddings_version_id",
        "knowledge_chunk_embeddings",
        ["version_id"],
    )
    op.create_index(
        "ix_knowledge_chunk_embeddings_profile_version",
        "knowledge_chunk_embeddings",
        ["profile_id", "version_id"],
    )

    op.create_table(
        "source_chunk_embeddings",
        sa.Column("segment_id", sa.String(36), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column(
            "profile_id",
            sa.String(160),
            sa.ForeignKey("embedding_profiles.profile_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("embedding", PgVector(), nullable=False),
        sa.Column("dimensions", sa.Integer(), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("segment_id", "chunk_index", "profile_id"),
        sa.ForeignKeyConstraint(
            ["segment_id", "chunk_index"],
            ["source_search_chunks.segment_id", "source_search_chunks.chunk_index"],
            ondelete="CASCADE",
            name="fk_source_chunk_embeddings_chunk",
        ),
    )
    op.create_index(
        "ix_source_chunk_embeddings_profile_segment",
        "source_chunk_embeddings",
        ["profile_id", "segment_id"],
    )


def downgrade():
    op.drop_index("ix_source_chunk_embeddings_profile_segment", table_name="source_chunk_embeddings")
    op.drop_table("source_chunk_embeddings")
    op.drop_index("ix_knowledge_chunk_embeddings_profile_version", table_name="knowledge_chunk_embeddings")
    op.drop_index("ix_knowledge_chunk_embeddings_version_id", table_name="knowledge_chunk_embeddings")
    op.drop_table("knowledge_chunk_embeddings")
    op.drop_table("rag_settings")
    op.drop_table("embedding_profiles")
