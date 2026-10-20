from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.ownership import owned_conversation, owned_document
from app.db import get_db
from app.models import Conversation, ConversationDocument, Document, utc_now
from app.schemas import (
    ConversationCreate,
    ConversationRead,
    ConversationUpdate,
    DocumentRead,
)
from app.security import AuthContext, get_current_auth, require_csrf

router = APIRouter(prefix="/api/conversations", tags=["conversations"])


@router.get("", response_model=list[ConversationRead])
def list_conversations(
    auth: AuthContext = Depends(get_current_auth),
    db: Session = Depends(get_db),
) -> list[Conversation]:
    return list(
        db.scalars(
            select(Conversation)
            .where(Conversation.user_id == auth.user.id)
            .order_by(Conversation.updated_at.desc())
        )
    )


@router.post(
    "",
    response_model=ConversationRead,
    status_code=status.HTTP_201_CREATED,
)
def create_conversation(
    payload: ConversationCreate,
    auth: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> Conversation:
    conversation = Conversation(
        user_id=auth.user.id,
        title=payload.title,
    )
    db.add(conversation)
    db.commit()
    db.refresh(conversation)
    return conversation


@router.get("/{conversation_id}", response_model=ConversationRead)
def get_conversation(
    conversation_id: UUID,
    auth: AuthContext = Depends(get_current_auth),
    db: Session = Depends(get_db),
) -> Conversation:
    return owned_conversation(db, conversation_id, auth.user.id)


@router.patch("/{conversation_id}", response_model=ConversationRead)
def update_conversation(
    conversation_id: UUID,
    payload: ConversationUpdate,
    auth: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> Conversation:
    conversation = owned_conversation(db, conversation_id, auth.user.id)
    conversation.title = payload.title
    conversation.updated_at = utc_now()
    db.commit()
    db.refresh(conversation)
    return conversation


@router.delete(
    "/{conversation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_conversation(
    conversation_id: UUID,
    auth: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> None:
    conversation = owned_conversation(db, conversation_id, auth.user.id)
    db.delete(conversation)
    db.commit()


@router.get(
    "/{conversation_id}/documents",
    response_model=list[DocumentRead],
)
def list_conversation_documents(
    conversation_id: UUID,
    auth: AuthContext = Depends(get_current_auth),
    db: Session = Depends(get_db),
) -> list[Document]:
    conversation = owned_conversation(db, conversation_id, auth.user.id)
    return list(
        db.scalars(
            select(Document)
            .join(
                ConversationDocument,
                ConversationDocument.document_id == Document.id,
            )
            .where(
                ConversationDocument.conversation_id == conversation.id,
                Document.user_id == auth.user.id,
            )
            .order_by(Document.created_at.desc())
        )
    )


@router.put(
    "/{conversation_id}/documents/{document_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def attach_document(
    conversation_id: UUID,
    document_id: UUID,
    auth: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> None:
    conversation = owned_conversation(db, conversation_id, auth.user.id)
    document = owned_document(db, document_id, auth.user.id)
    if document.status != "indexed":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Only indexed documents can be attached",
        )
    existing = db.get(
        ConversationDocument,
        {
            "conversation_id": conversation.id,
            "document_id": document.id,
        },
    )
    if existing is None:
        db.add(
            ConversationDocument(
                conversation_id=conversation.id,
                document_id=document.id,
            )
        )
        conversation.updated_at = utc_now()
        db.commit()


@router.delete(
    "/{conversation_id}/documents/{document_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def detach_document(
    conversation_id: UUID,
    document_id: UUID,
    auth: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> None:
    conversation = owned_conversation(db, conversation_id, auth.user.id)
    document = owned_document(db, document_id, auth.user.id)
    link = db.get(
        ConversationDocument,
        {
            "conversation_id": conversation.id,
            "document_id": document.id,
        },
    )
    if link is not None:
        db.delete(link)
        conversation.updated_at = utc_now()
        db.commit()
