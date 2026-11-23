"""为已解析的材料建立可重建检索索引，保留原正文和固定引用。"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.orm import Session

from knowledgeops_agent.retrieval.index import add_source_chunks

revision = "0053"
down_revision = "0052"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "source_search_chunks",
        sa.Column(
            "segment_id", sa.String(36),
            sa.ForeignKey("source_segments.id", ondelete="CASCADE"), primary_key=True,
        ),
        sa.Column("chunk_index", sa.Integer(), primary_key=True),
        sa.Column("start", sa.Integer(), nullable=False),
        sa.Column("end", sa.Integer(), nullable=False),
        sa.Column("body_tokens", sa.Text(), nullable=False),
    )
    with Session(bind=op.get_bind()) as session:
        versions = session.scalars(sa.text(
            "SELECT v.id FROM source_versions v JOIN sources s ON s.id = v.source_id "
            "WHERE v.status = 'ready' AND s.deleted_at IS NULL ORDER BY v.id"
        ))
        for version_id in versions:
            add_source_chunks(session, version_id)
    op.create_index(
        "ix_source_chunks_bm25", "source_search_chunks",
        ["segment_id", "chunk_index", sa.text("(body_tokens::pdb.whitespace)")],
        postgresql_using="paradedb",
    )


def downgrade():
    op.drop_table("source_search_chunks")
