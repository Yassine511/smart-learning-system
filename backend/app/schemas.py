from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


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
    title: str = Field(default="New conversation", min_length=1, max_length=160)


class ConversationUpdate(BaseModel):
    title: str = Field(min_length=1, max_length=160)


class ConversationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    title: str
    created_at: datetime
    updated_at: datetime


class DocumentUpdate(BaseModel):
    display_name: str = Field(min_length=1, max_length=255)


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
    content: str = Field(min_length=1, max_length=4000)


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
