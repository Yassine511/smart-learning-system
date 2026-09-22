from datetime import timedelta
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.api import messages as messages_api
from app.models import Message, utc_now
from app.services.chat_queue import ChatQueue
from app.services.ollama_chat import ChatFailure
from app.services import ollama_chat
from tests.conftest import TEST_ORIGIN, csrf_headers, login


def create_conversation(client: TestClient, token: str) -> str:
    response = client.post(
        "/api/conversations",
        headers=csrf_headers(token),
        json={"title": "Study"},
    )
    assert response.status_code == 201
    return response.json()["id"]


def test_streamed_chat_history_is_private_and_idempotent(
    client: TestClient, users, monkeypatch
) -> None:
    calls = []

    def fake_stream(prompt):
        calls.append(prompt)
        yield "Hello"
        yield " Alice"

    monkeypatch.setattr(messages_api, "stream_chat", fake_stream)
    token = login(client, "alice@example.test")
    conversation_id = create_conversation(client, token)
    request_id = str(uuid4())
    url = f"/api/conversations/{conversation_id}/messages"
    payload = {"request_id": request_id, "content": "Hi"}
    response = client.post(url, headers=csrf_headers(token), json=payload)
    assert response.status_code == 200
    assert "event: queued" not in response.text
    assert "event: status" in response.text
    assert "event: delta" in response.text
    assert "event: complete" in response.text
    history = client.get(url).json()
    assert [(row["role"], row["status"], row["content"]) for row in history] == [
        ("user", "completed", "Hi"),
        ("assistant", "completed", "Hello Alice"),
    ]
    replay = client.post(url, headers=csrf_headers(token), json=payload)
    assert replay.status_code == 200
    assert "event: complete" in replay.text
    assert len(calls) == 1
    assert client.post(
        url, headers=csrf_headers(token),
        json={"request_id": request_id, "content": "Different"},
    ).status_code == 409

    next_response = client.post(
        url,
        headers=csrf_headers(token),
        json={"request_id": str(uuid4()), "content": "Continue"},
    )
    assert next_response.status_code == 200
    assert [row["content"] for row in calls[1] if row["role"] != "system"] == [
        "Hi", "Hello Alice", "Continue",
    ]

    from app.main import app
    with TestClient(app, base_url=TEST_ORIGIN) as bob_client:
        bob_token = login(bob_client, "bob@example.test")
        assert bob_client.get(url).status_code == 404
        assert bob_client.post(
            url, headers=csrf_headers(bob_token),
            json={"request_id": str(uuid4()), "content": "Guess"},
        ).status_code == 404


def test_failed_generation_retries_without_duplicate_user_message(
    client: TestClient, users, monkeypatch, session_factory: sessionmaker[Session]
) -> None:
    attempts = 0

    def flaky_stream(_prompt):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise ChatFailure("model_missing")
        yield "Recovered"

    monkeypatch.setattr(messages_api, "stream_chat", flaky_stream)
    token = login(client, "alice@example.test")
    conversation_id = create_conversation(client, token)
    url = f"/api/conversations/{conversation_id}/messages"
    payload = {"request_id": str(uuid4()), "content": "Explain TCP"}
    first = client.post(url, headers=csrf_headers(token), json=payload)
    assert "event: failed" in first.text
    assert "model_missing" in first.text
    assert [row["status"] for row in client.get(url).json()] == ["completed", "failed"]

    second = client.post(url, headers=csrf_headers(token), json=payload)
    assert "event: complete" in second.text
    with session_factory() as db:
        rows = db.query(Message).filter_by(conversation_id=UUID(conversation_id)).all()
        assert len(rows) == 2
        assert next(row for row in rows if row.role == "assistant").content == "Recovered"


def test_chat_queue_is_fifo_and_bounded() -> None:
    queue = ChatQueue()
    first = queue.admit(2)
    second = queue.admit(2)
    assert first is not None and second is not None
    assert queue.admit(2) is None
    assert queue.wait_turn(first, 0.01)
    assert not queue.wait_turn(second, 0.01)
    queue.release(first)
    assert queue.admit(2) is not None


def test_ollama_adapter_streams_content_and_maps_missing_model(monkeypatch) -> None:
    real_client = httpx.Client
    requests = []

    def reply(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            text='{"message":{"thinking":"hidden","content":"First"},"done":false}\n'
                 '{"message":{"content":" second"},"done":true}\n',
        )

    transport = httpx.MockTransport(reply)
    monkeypatch.setattr(
        ollama_chat.httpx, "Client",
        lambda **kwargs: real_client(transport=transport, **kwargs),
    )
    result = list(ollama_chat.stream_chat([{"role": "user", "content": "Hi"}]))
    assert result == ["First", " second"]
    assert requests[0].url.path == "/api/chat"
    assert requests[0].content
    assert b'"stream":true' in requests[0].content
    assert b'"think":"low"' in requests[0].content

    monkeypatch.setattr(
        ollama_chat.httpx, "Client",
        lambda **kwargs: real_client(
            transport=httpx.MockTransport(lambda _request: httpx.Response(404)),
            **kwargs,
        ),
    )
    with pytest.raises(ChatFailure, match="model_missing"):
        list(ollama_chat.stream_chat([{"role": "user", "content": "Hi"}]))


def test_interrupted_reply_becomes_retryable(
    client: TestClient, users, monkeypatch, session_factory: sessionmaker[Session]
) -> None:
    monkeypatch.setattr(messages_api, "stream_chat", lambda _prompt: iter(["After restart"]))
    token = login(client, "alice@example.test")
    conversation_id = create_conversation(client, token)
    request_id = uuid4()
    old = utc_now() - timedelta(hours=1)
    with session_factory() as db:
        db.add_all([
            Message(
                conversation_id=UUID(conversation_id),
                request_id=request_id,
                role="user",
                status="completed",
                content="Recover me",
            ),
            Message(
                conversation_id=UUID(conversation_id),
                request_id=request_id,
                role="assistant",
                status="running",
                content="",
                updated_at=old,
            ),
        ])
        db.commit()
    url = f"/api/conversations/{conversation_id}/messages"
    history = client.get(url).json()
    assert history[1]["status"] == "failed"
    assert history[1]["error_code"] == "interrupted"
    response = client.post(
        url,
        headers=csrf_headers(token),
        json={"request_id": str(request_id), "content": "Recover me"},
    )
    assert "event: complete" in response.text
    assert len(client.get(url).json()) == 2
