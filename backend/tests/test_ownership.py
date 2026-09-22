from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.main import app
from app.models import Conversation, Document
from tests.conftest import TEST_ORIGIN, csrf_headers, login


def new_client() -> TestClient:
    return TestClient(app, base_url=TEST_ORIGIN)


def test_conversations_are_private_even_when_ids_are_guessed(
    client: TestClient,
    users,
) -> None:
    alice_csrf = login(client, "alice@example.test")
    created = client.post(
        "/api/conversations",
        headers=csrf_headers(alice_csrf),
        json={"title": "Alice private chat"},
    )
    assert created.status_code == 201
    alice_conversation_id = created.json()["id"]

    with new_client() as bob_client:
        bob_csrf = login(bob_client, "bob@example.test")
        assert bob_client.get(f"/api/conversations/{alice_conversation_id}").status_code == 404
        assert (
            bob_client.patch(
                f"/api/conversations/{alice_conversation_id}",
                headers=csrf_headers(bob_csrf),
                json={"title": "Hijacked"},
            ).status_code
            == 404
        )
        assert (
            bob_client.delete(
                f"/api/conversations/{alice_conversation_id}",
                headers=csrf_headers(bob_csrf),
            ).status_code
            == 404
        )
        assert bob_client.get("/api/conversations").json() == []

    assert client.get(f"/api/conversations/{alice_conversation_id}").status_code == 200


def test_documents_and_attachments_enforce_both_owners(
    client: TestClient,
    users,
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as db:
        alice_conversation = Conversation(
            user_id=users["alice"].id,
            title="Alice chat",
        )
        bob_conversation = Conversation(
            user_id=users["bob"].id,
            title="Bob chat",
        )
        alice_document = Document(
            user_id=users["alice"].id,
            original_filename="alice.pdf",
            display_name="Alice PDF",
            status="indexed",
            storage_key=f"alice/{uuid4()}.pdf",
        )
        bob_document = Document(
            user_id=users["bob"].id,
            original_filename="bob.pdf",
            display_name="Bob PDF",
            status="indexed",
            storage_key=f"bob/{uuid4()}.pdf",
        )
        db.add_all(
            [alice_conversation, bob_conversation, alice_document, bob_document]
        )
        db.commit()
        ids = {
            "alice_conversation": alice_conversation.id,
            "bob_conversation": bob_conversation.id,
            "alice_document": alice_document.id,
            "bob_document": bob_document.id,
        }

    alice_csrf = login(client, "alice@example.test")
    assert [item["id"] for item in client.get("/api/documents").json()] == [
        str(ids["alice_document"])
    ]
    attach = client.put(
        f"/api/conversations/{ids['alice_conversation']}/documents/{ids['alice_document']}",
        headers=csrf_headers(alice_csrf),
    )
    assert attach.status_code == 204
    attached = client.get(
        f"/api/conversations/{ids['alice_conversation']}/documents"
    )
    assert [document["id"] for document in attached.json()] == [
        str(ids["alice_document"])
    ]

    cross_attach = client.put(
        f"/api/conversations/{ids['alice_conversation']}/documents/{ids['bob_document']}",
        headers=csrf_headers(alice_csrf),
    )
    assert cross_attach.status_code == 404

    with new_client() as bob_client:
        bob_csrf = login(bob_client, "bob@example.test")
        assert [item["id"] for item in bob_client.get("/api/documents").json()] == [
            str(ids["bob_document"])
        ]
        guessed_document = f"/api/documents/{ids['alice_document']}"
        assert bob_client.get(guessed_document).status_code == 404
        assert (
            bob_client.patch(
                guessed_document,
                headers=csrf_headers(bob_csrf),
                json={"display_name": "Stolen"},
            ).status_code
            == 404
        )
        assert (
            bob_client.delete(
                guessed_document,
                headers=csrf_headers(bob_csrf),
            ).status_code
            == 404
        )
        assert (
            bob_client.get(
                f"/api/conversations/{ids['alice_conversation']}/documents"
            ).status_code
            == 404
        )
        assert (
            bob_client.delete(
                f"/api/conversations/{ids['alice_conversation']}/documents/{ids['alice_document']}",
                headers=csrf_headers(bob_csrf),
            ).status_code
            == 404
        )
        assert (
            bob_client.put(
                f"/api/conversations/{ids['bob_conversation']}/documents/{ids['alice_document']}",
                headers=csrf_headers(bob_csrf),
            ).status_code
            == 404
        )

    assert client.get(f"/api/documents/{ids['alice_document']}").status_code == 200
