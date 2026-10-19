"""Owned conversation history and streamed normal chat."""

from collections.abc import Iterator
from datetime import timedelta, timezone
import json
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker, selectinload

from app.api.ownership import owned_conversation
from app.config import get_settings
from app.db import get_db, get_session_factory
from app.models import Conversation, Message, utc_now
from app.schemas import MessageCreate, MessageRead
from app.security import AuthContext, get_current_auth, require_csrf
from app.services.chat_queue import chat_queue
from app.services.ollama_chat import ChatFailure, stream_chat
from app.services.rag import (NO_EVIDENCE, attachment_scope, cited_sources, complete_with_sources, document_prompt, retrieve)
from app.services.vector_index import IndexProblem

router = APIRouter(prefix="/api/conversations", tags=["messages"])


def get_chat_session_factory() -> sessionmaker[Session]:
    return get_session_factory()


def event(name: str, payload: dict) -> str:
    return f"event: {name}\ndata: {json.dumps(payload)}\n\n"


def _update_message(
    factory: sessionmaker[Session],
    message_id: UUID,
    state: str,
    content: str | None = None,
    error_code: str | None = None,
) -> None:
    with factory() as db:
        message = db.get(Message, message_id)
        if message is None:
            return
        message.status = state
        message.error_code = error_code
        if content is not None:
            message.content = content
        message.updated_at = utc_now()
        conversation = db.get(Conversation, message.conversation_id)
        if conversation is not None:
            conversation.updated_at = utc_now()
        db.commit()


def _prompt(
    factory: sessionmaker[Session],
    conversation_id: UUID,
    request_id: UUID,
    question: str,
    reserved_chars: int = 0,
) -> list[dict[str, str]]:
    settings = get_settings()
    with factory() as db:
        completed_requests = select(Message.request_id).where(
            Message.conversation_id == conversation_id,
            Message.role == "assistant",
            Message.status == "completed",
        )
        rows = list(
            db.scalars(
                select(Message)
                .where(
                    Message.conversation_id == conversation_id,
                    Message.request_id != request_id,
                    Message.request_id.in_(completed_requests),
                    Message.status == "completed",
                )
                .order_by(Message.created_at.desc())
                .limit(settings.chat_history_messages)
            )
        )
    rows.sort(key=lambda row: (row.created_at, row.role == "assistant"))
    budget = max(0, settings.chat_context_tokens * 3 - reserved_chars)
    selected: list[dict[str, str]] = []
    for row in reversed(rows):
        if len(row.content) > budget:
            break
        selected.append({"role": row.role, "content": row.content})
        budget -= len(row.content)
    selected.reverse()
    return [
        {"role": "system", "content": "You are a helpful study assistant. Answer clearly. Do not claim to have read documents when none were provided."},
        *selected,
        {"role": "user", "content": question},
    ]


def _stream(
    factory: sessionmaker[Session],
    ticket: UUID,
    assistant_id: UUID,
    conversation_id: UUID,
    request_id: UUID,
    question: str,
    user_id: UUID,
    scope: list[dict],
) -> Iterator[str]:
    settings = get_settings()
    answer = ""
    finished = False
    candidates = []
    try:
        yield event("status", {"request_id": str(request_id), "message_id": str(assistant_id), "status": "queued"})
        if not chat_queue.wait_turn(ticket, settings.chat_queue_wait_seconds):
            raise ChatFailure("queue_timeout")
        _update_message(factory, assistant_id, "running")
        yield event("status", {"request_id": str(request_id), "message_id": str(assistant_id), "status": "running"})
        if scope:
            candidates = retrieve(factory, conversation_id, user_id, scope, question)
            if not candidates:
                answer = NO_EVIDENCE
                _update_message(factory, assistant_id, "completed", answer)
                finished = True
                yield event("delta", {"request_id": str(request_id), "content": answer})
                yield event("complete", {"request_id": str(request_id), "message_id": str(assistant_id), "content": answer})
                return
            rag_messages = document_prompt(question, candidates)
            reserved = sum(len(item["content"]) for item in rag_messages)
            history = _prompt(
                factory, conversation_id, request_id, question, reserved_chars=reserved
            )[1:-1]
            prompt = [rag_messages[0], *rag_messages[1:-1], *history, rag_messages[-1]]
        else:
            prompt = _prompt(factory, conversation_id, request_id, question)
        for delta in stream_chat(prompt):
            answer += delta
            if not candidates:
                yield event("delta", {"request_id": str(request_id), "content": delta})
        if not answer.strip():
            raise ChatFailure("empty_model_response")
        if candidates:
            cited = cited_sources(answer, candidates)
            if cited:
                complete_with_sources(factory, assistant_id, conversation_id, user_id, answer, cited)
            else:
                answer = NO_EVIDENCE
                _update_message(factory, assistant_id, "completed", answer)
        else:
            cited = []
            _update_message(factory, assistant_id, "completed", answer)
        finished = True
        if candidates:
            for source in cited:
                yield event("source", {"request_id": str(request_id), **source.public()})
            yield event("delta", {"request_id": str(request_id), "content": answer})
        yield event("complete", {
            "request_id": str(request_id),
            "message_id": str(assistant_id),
            "content": answer,
            "sources": [source.public() for source in cited],
        })
    except (ChatFailure, IndexProblem) as exc:
        _update_message(factory, assistant_id, "failed", answer, exc.code)
        finished = True
        yield event("failed", {"request_id": str(request_id), "message_id": str(assistant_id), "error_code": exc.code})
    except Exception:
        _update_message(factory, assistant_id, "failed", answer, "internal_error")
        finished = True
        yield event("failed", {"request_id": str(request_id), "message_id": str(assistant_id), "error_code": "internal_error"})
    finally:
        if not finished:
            _update_message(factory, assistant_id, "failed", answer, "client_disconnected")
        chat_queue.release(ticket)


def _expire_stale(db: Session, conversation_id: UUID) -> None:
    settings = get_settings()
    cutoff = utc_now() - timedelta(
        seconds=settings.chat_queue_wait_seconds + settings.chat_generation_seconds + 10
    )
    pending = db.scalars(
        select(Message).where(
            Message.conversation_id == conversation_id,
            Message.role == "assistant",
            Message.status.in_(("queued", "running")),
        )
    )
    changed = False
    for message in pending:
        updated = message.updated_at
        if updated.tzinfo is None:
            updated = updated.replace(tzinfo=timezone.utc)
        if updated < cutoff:
            message.status = "failed"
            message.error_code = "interrupted"
            message.updated_at = utc_now()
            changed = True
    if changed:
        db.commit()


@router.get("/{conversation_id}/messages", response_model=list[MessageRead])
def list_messages(
    conversation_id: UUID,
    auth: AuthContext = Depends(get_current_auth),
    db: Session = Depends(get_db),
) -> list[Message]:
    owned_conversation(db, conversation_id, auth.user.id)
    _expire_stale(db, conversation_id)
    return list(
        db.scalars(
            select(Message)
            .options(selectinload(Message.sources))
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.created_at, Message.role.desc())
        )
    )


@router.post("/{conversation_id}/messages")
def send_message(
    conversation_id: UUID,
    payload: MessageCreate,
    auth: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
    factory: sessionmaker[Session] = Depends(get_chat_session_factory),
) -> StreamingResponse:
    question = payload.content.strip()
    if not question:
        raise HTTPException(status_code=422, detail="Message cannot be blank")
    owned_conversation(db, conversation_id, auth.user.id)
    conversation = db.scalar(
        select(Conversation).where(
            Conversation.id == conversation_id, Conversation.user_id == auth.user.id
        ).with_for_update()
    )
    _expire_stale(db, conversation_id)
    previous_user = db.scalar(
        select(Message).where(
            Message.conversation_id == conversation_id,
            Message.request_id == payload.request_id,
            Message.role == "user",
        )
    )
    previous_assistant = db.scalar(
        select(Message).where(
            Message.conversation_id == conversation_id,
            Message.request_id == payload.request_id,
            Message.role == "assistant",
        )
    )
    if previous_user is not None:
        if previous_user.content != question or previous_assistant is None:
            raise HTTPException(status_code=409, detail="Request ID already used")
        if previous_assistant.status == "completed":
            saved_sources = [
                {
                    "document_id": str(source.document_id),
                    "chunk_id": str(source.chunk_id),
                    "marker": source.marker,
                    "filename": source.filename,
                    "page_number": source.page_number,
                    "quote": source.quote,
                    "unavailable": source.unavailable,
                }
                for source in previous_assistant.sources
            ]
            return StreamingResponse(
                iter([
                    *(event("source", {"request_id": str(payload.request_id), **source}) for source in saved_sources),
                    event("complete", {
                        "request_id": str(payload.request_id),
                        "message_id": str(previous_assistant.id),
                        "content": previous_assistant.content,
                        "sources": saved_sources,
                    }),
                ]),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
            )
        if previous_assistant.status in {"queued", "running"}:
            raise HTTPException(status_code=409, detail="Request already in progress")

    active = db.scalar(
        select(Message.id).where(
            Message.conversation_id == conversation_id,
            Message.role == "assistant",
            Message.status.in_(("queued", "running")),
        ).limit(1)
    )
    if active is not None:
        raise HTTPException(status_code=409, detail="Conversation already has a pending reply")

    scope = previous_user.document_scope if previous_user is not None else attachment_scope(
        db, conversation_id, auth.user.id
    )
    scope = scope or []
    ticket = chat_queue.admit(get_settings().chat_queue_capacity)
    if ticket is None:
        raise HTTPException(status_code=429, detail="Chat queue is full", headers={"Retry-After": "5"})
    try:
        if previous_user is None:
            db.add(Message(
                conversation_id=conversation_id,
                request_id=payload.request_id,
                role="user",
                status="completed",
                content=question,
                document_scope=scope,
            ))
            assistant = Message(
                conversation_id=conversation_id,
                request_id=payload.request_id,
                role="assistant",
                status="queued",
                content="",
            )
            db.add(assistant)
        else:
            assistant = previous_assistant
            assistant.status = "queued"
            assistant.content = ""
            assistant.error_code = None
            assistant.updated_at = utc_now()
        conversation.updated_at = utc_now()
        db.commit()
        db.refresh(assistant)
    except Exception:
        db.rollback()
        chat_queue.release(ticket)
        raise
    return StreamingResponse(
        _stream(factory, ticket, assistant.id, conversation_id, payload.request_id, question, auth.user.id, scope),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )
