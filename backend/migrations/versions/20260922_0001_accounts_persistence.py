"""Create account and private-resource tables.

Revision ID: 20260922_0001
Revises:
Create Date: 2026-09-22
"""

from alembic import op
import sqlalchemy as sa

revision = "20260922_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("display_name", sa.String(length=120), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)

    op.create_table(
        "login_sessions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("csrf_token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_login_sessions_expires_at",
        "login_sessions",
        ["expires_at"],
        unique=False,
    )
    op.create_index(
        "ix_login_sessions_token_hash",
        "login_sessions",
        ["token_hash"],
        unique=True,
    )
    op.create_index(
        "ix_login_sessions_user_id",
        "login_sessions",
        ["user_id"],
        unique=False,
    )

    op.create_table(
        "conversations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(length=160), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_conversations_owner_updated",
        "conversations",
        ["user_id", "updated_at"],
        unique=False,
    )
    op.create_index(
        "ix_conversations_user_id",
        "conversations",
        ["user_id"],
        unique=False,
    )

    op.create_table(
        "documents",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("storage_key", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("storage_key"),
    )
    op.create_index(
        "ix_documents_owner_created",
        "documents",
        ["user_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_documents_user_id",
        "documents",
        ["user_id"],
        unique=False,
    )

    op.create_table(
        "conversation_documents",
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("attached_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["conversation_id"], ["conversations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["document_id"], ["documents.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("conversation_id", "document_id"),
    )


def downgrade() -> None:
    op.drop_table("conversation_documents")
    op.drop_index("ix_documents_user_id", table_name="documents")
    op.drop_index("ix_documents_owner_created", table_name="documents")
    op.drop_table("documents")
    op.drop_index("ix_conversations_user_id", table_name="conversations")
    op.drop_index("ix_conversations_owner_updated", table_name="conversations")
    op.drop_table("conversations")
    op.drop_index("ix_login_sessions_user_id", table_name="login_sessions")
    op.drop_index("ix_login_sessions_token_hash", table_name="login_sessions")
    op.drop_index("ix_login_sessions_expires_at", table_name="login_sessions")
    op.drop_table("login_sessions")
    op.drop_index("ix_users_email", table_name="users")
    op.drop_table("users")
