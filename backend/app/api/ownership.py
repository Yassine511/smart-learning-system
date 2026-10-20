from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Conversation, Document


def owned_conversation(
    db: Session,
    conversation_id: UUID,
    user_id: UUID,
    for_update: bool = False,
) -> Conversation:
    query = select(Conversation).where(
        Conversation.id == conversation_id,
        Conversation.user_id == user_id,
    )
    conversation = db.scalar(query.with_for_update() if for_update else query)
    if conversation is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conversation not found",
        )
    return conversation


def owned_document(
    db: Session,
    document_id: UUID,
    user_id: UUID,
    for_update: bool = False,
) -> Document:
    query = select(Document).where(
        Document.id == document_id,
        Document.user_id == user_id,
    )
    document = db.scalar(query.with_for_update() if for_update else query)
    if document is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found",
        )
    return document
