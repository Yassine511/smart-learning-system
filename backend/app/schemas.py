from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

from app.config import get_settings


# Stripped before validation, so whitespace-only input is rejected as blank.
ConversationTitle = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=160)]
DocumentName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)]
MessageText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)]


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: str
    display_name: str


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=1024)


class LoginResponse(BaseModel):
    user: UserRead
    csrf_token: str


class ConversationCreate(BaseModel):
    title: ConversationTitle = "New conversation"


class ConversationUpdate(BaseModel):
    title: ConversationTitle


class ConversationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    title: str
    created_at: datetime
    updated_at: datetime


class DocumentUpdate(BaseModel):
    display_name: DocumentName


class DocumentSearch(BaseModel):
    query: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]
    document_ids: list[UUID] | None = Field(default=None, max_length=50)
    limit: int = Field(default_factory=lambda: min(5, get_settings().retrieval_limit), ge=1, le=30)

    @field_validator("limit")
    @classmethod
    def within_retrieval_limit(cls, value: int) -> int:
        if value > get_settings().retrieval_limit:
            raise ValueError("limit exceeds the configured retrieval limit")
        return value


class SearchHit(BaseModel):
    chunk_id: UUID
    document_id: UUID
    marker: str
    filename: str
    page_number: int
    quote: str


class DocumentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    original_filename: str
    display_name: str
    status: str
    error_code: str | None
    size_bytes: int
    page_count: int | None
    created_at: datetime
    updated_at: datetime


class MessageCreate(BaseModel):
    request_id: UUID
    content: MessageText


class MessageSourceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    document_id: UUID
    chunk_id: UUID
    marker: str
    filename: str
    page_number: int
    quote: str
    unavailable: bool


class MessageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    request_id: UUID
    role: str
    status: str
    content: str
    error_code: str | None
    created_at: datetime
    updated_at: datetime
    sources: list[MessageSourceRead] = Field(default_factory=list)
