"""Add durable document processing, chunks, and citation snapshots."""

from alembic import op
import sqlalchemy as sa

revision = "20260922_0003"
down_revision = "20260922_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("documents", sa.Column("error_code", sa.String(64), nullable=True))
    op.add_column("documents", sa.Column("size_bytes", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("documents", sa.Column("page_count", sa.Integer(), nullable=True))
    op.add_column("documents", sa.Column("index_generation", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("documents", sa.Column("embedding_digest", sa.String(128), nullable=True))
    op.add_column("documents", sa.Column("index_collection", sa.String(128), nullable=True))
    op.add_column("documents", sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()))
    op.add_column("messages", sa.Column("document_scope", sa.JSON(), nullable=True))

    op.create_table(
        "document_jobs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("document_id", sa.Uuid(), sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_document_jobs_document_id", "document_jobs", ["document_id"])
    op.create_index("ix_document_jobs_claim", "document_jobs", ["status", "next_attempt_at", "lease_until"])

    op.create_table(
        "document_chunks",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("document_id", sa.Uuid(), sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_document_chunks_document_id", "document_chunks", ["document_id"])
    op.create_index("ix_document_chunks_user_id", "document_chunks", ["user_id"])
    op.create_index("ix_document_chunks_scope", "document_chunks", ["user_id", "document_id", "generation"])
    op.create_index(
        "ix_document_chunks_position", "document_chunks",
        ["document_id", "generation", "page_number", "ordinal"], unique=True,
    )

    op.create_table(
        "message_sources",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("message_id", sa.Uuid(), sa.ForeignKey("messages.id", ondelete="CASCADE"), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("chunk_id", sa.Uuid(), nullable=False),
        sa.Column("marker", sa.String(16), nullable=False),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("quote", sa.Text(), nullable=False),
        sa.Column("unavailable", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_message_sources_message_id", "message_sources", ["message_id"])
    op.create_index("ix_message_sources_document", "message_sources", ["document_id"])


def downgrade() -> None:
    op.drop_index("ix_message_sources_document", table_name="message_sources")
    op.drop_index("ix_message_sources_message_id", table_name="message_sources")
    op.drop_table("message_sources")
    op.drop_index("ix_document_chunks_position", table_name="document_chunks")
    op.drop_index("ix_document_chunks_scope", table_name="document_chunks")
    op.drop_index("ix_document_chunks_user_id", table_name="document_chunks")
    op.drop_index("ix_document_chunks_document_id", table_name="document_chunks")
    op.drop_table("document_chunks")
    op.drop_index("ix_document_jobs_claim", table_name="document_jobs")
    op.drop_index("ix_document_jobs_document_id", table_name="document_jobs")
    op.drop_table("document_jobs")
    op.drop_column("messages", "document_scope")
    op.drop_column("documents", "updated_at")
    op.drop_column("documents", "index_collection")
    op.drop_column("documents", "embedding_digest")
    op.drop_column("documents", "index_generation")
    op.drop_column("documents", "page_count")
    op.drop_column("documents", "size_bytes")
    op.drop_column("documents", "error_code")
