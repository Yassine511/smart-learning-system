"""Drop single-column indexes already covered by a composite index's leading column."""

from alembic import op

revision = "20261020_0004"
down_revision = "20260922_0003"
branch_labels = None
depends_on = None

# (index, table, column); each column leads a composite index on the same table.
REDUNDANT = (
    ("ix_conversations_user_id", "conversations", "user_id"),
    ("ix_documents_user_id", "documents", "user_id"),
    ("ix_messages_conversation_id", "messages", "conversation_id"),
    ("ix_document_chunks_document_id", "document_chunks", "document_id"),
    ("ix_document_chunks_user_id", "document_chunks", "user_id"),
)


def upgrade() -> None:
    for name, table, _column in REDUNDANT:
        op.drop_index(name, table_name=table)


def downgrade() -> None:
    for name, table, column in REDUNDANT:
        op.create_index(name, table, [column])
