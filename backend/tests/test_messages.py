from datetime import timedelta
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.api import messages as messages_api
from app.models import Message, utc_now
from app.services.chat_queue import ChatQueue, QueueFull
from app.services.ollama_chat import ChatFailure
from app.services import ollama_chat
from app.config import Settings, get_settings
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
    with pytest.raises(QueueFull) as full:
        queue.admit(2)
    assert full.value.scope == "global"
    assert queue.wait_turn(first, 0.01)
    assert not queue.wait_turn(second, 0.01)
    queue.release(first)
    assert queue.admit(2) is not None


def test_chat_queue_limits_each_student_share() -> None:
    queue = ChatQueue()
    queue.admit(4, owner="alice", owner_capacity=2)
    queue.admit(4, owner="alice", owner_capacity=2)
    with pytest.raises(QueueFull) as full:
        queue.admit(4, owner="alice", owner_capacity=2)
    assert full.value.scope == "owner"
    assert queue.admit(4, owner="bob", owner_capacity=2) is not None


def test_abandoned_unstarted_stream_does_not_block_the_queue(monkeypatch) -> None:
    queue = ChatQueue(attach_seconds=0.05)
    monkeypatch.setattr(messages_api, "chat_queue", queue)
    abandoned = queue.admit(4)
    # The client disconnected before the response body was iterated, so the
    # generator never starts and its cleanup never runs.
    stream = messages_api._stream(None, abandoned, uuid4(), uuid4(), uuid4(), "q", uuid4(), [])
    del stream
    later = queue.admit(4)
    queue.attach(later)
    assert queue.wait_turn(later, 2)
    queue.release(later)
    assert queue.admit(1) is not None


def test_started_stream_is_not_expired_while_waiting() -> None:
    queue = ChatQueue(attach_seconds=0.01)
    first = queue.admit(4)
    second = queue.admit(4)
    queue.attach(second)
    assert queue.wait_turn(first, 1)
    assert not queue.wait_turn(second, 0.1)
    queue.release(first)


def test_one_student_cannot_take_every_queue_slot(
    client: TestClient, users, monkeypatch
) -> None:
    queue = ChatQueue()
    monkeypatch.setattr(messages_api, "chat_queue", queue)
    monkeypatch.setattr(messages_api, "stream_chat", lambda _prompt: iter(["Answer"]))
    for _ in range(2):
        queue.attach(queue.admit(4, owner=str(users["alice"].id), owner_capacity=2))
    token = login(client, "alice@example.test")
    conversation_id = create_conversation(client, token)
    busy = client.post(
        f"/api/conversations/{conversation_id}/messages",
        headers=csrf_headers(token),
        json={"request_id": str(uuid4()), "content": "Another question"},
    )
    assert busy.status_code == 429
    assert busy.json()["detail"] == "You already have the maximum number of pending replies"

    # Alice's held tickets stay ahead of Bob, so shorten his wait; the point is
    # that he is admitted (HTTP 200 stream) rather than refused with 429.
    monkeypatch.setattr(get_settings(), "chat_queue_wait_seconds", 0.2)
    from app.main import app
    with TestClient(app, base_url=TEST_ORIGIN) as bob_client:
        bob_token = login(bob_client, "bob@example.test")
        bob_conversation = create_conversation(bob_client, bob_token)
        admitted = bob_client.post(
            f"/api/conversations/{bob_conversation}/messages",
            headers=csrf_headers(bob_token),
            json={"request_id": str(uuid4()), "content": "My question"},
        )
        assert admitted.status_code == 200
        assert '"status": "queued"' in admitted.text


def test_ollama_adapter_sends_structured_output_schema(monkeypatch) -> None:
    monkeypatch.setattr(ollama_chat, "get_settings", lambda: Settings(_env_file=None))
    real_client = httpx.Client
    requests = []

    def reply(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, text='{"message":{"content":"{}"},"done":true}\n')

    monkeypatch.setattr(
        ollama_chat.httpx, "Client",
        lambda **kwargs: real_client(transport=httpx.MockTransport(reply), **kwargs),
    )
    schema = {"type": "object", "properties": {"answer": {"type": "string"}}}
    assert list(ollama_chat.stream_chat([{"role": "user", "content": "Hi"}], response_format=schema)) == ["{}"]
    assert b'"format":{"type":"object"' in requests[0].content
    list(ollama_chat.stream_chat([{"role": "user", "content": "Hi"}]))
    assert b'"format"' not in requests[1].content


@pytest.mark.parametrize("prototype", [False, True])
def test_ollama_adapter_streams_content_and_maps_missing_model(monkeypatch, prototype) -> None:
    settings = Settings(_env_file=None)
    if prototype:
        settings.ollama_chat_model = "qwen2.5:0.5b"
        settings.ollama_think_mode = "off"
        settings.ollama_num_threads = 1
    monkeypatch.setattr(ollama_chat, "get_settings", lambda: settings)
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
    if prototype:
        assert b'"think"' not in requests[0].content
        assert b'"model":"qwen2.5:0.5b"' in requests[0].content
        assert b'"num_thread":1' in requests[0].content
    else:
        assert b'"think":"low"' in requests[0].content
        assert b'"model":"gpt-oss:20b"' in requests[0].content
        assert b'"num_thread"' not in requests[0].content

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
