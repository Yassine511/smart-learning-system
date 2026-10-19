"""M4 integration tests use real PDF parsing and fake model/vector responses."""

from datetime import timedelta
from io import BytesIO
from pathlib import Path
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from pypdf import PdfReader, PdfWriter
import pytest
from sqlalchemy.orm import Session, sessionmaker

from app import worker
from app.api import messages as messages_api
from app.config import Settings, get_settings
from app.models import Document, DocumentChunk, DocumentJob, utc_now
from app.services import rag
from app.services.pdf_files import stored_path
from app.services.vector_index import IndexProblem
from tests.conftest import TEST_ORIGIN, csrf_headers, login

FIXTURE = Path(__file__).parent / "fixtures" / "networking_notes.pdf"
DIGEST = "a" * 64


def test_citation_format_example_is_opt_in_and_keeps_actual_passages(monkeypatch):
    source = rag.SourceCandidate(uuid4(), uuid4(), 1, "S1", "owned.pdf", 3, "The real owned passage.")
    for enabled in (False, True):
        monkeypatch.setattr(rag, "get_settings", lambda: Settings(_env_file=None, rag_citation_example=enabled))
        prompt = rag.document_prompt("The actual question?", [source])
        assert prompt[0]["role"] == "system"
        assert prompt[-1]["role"] == "user"
        assert "owned.pdf, page 3" in prompt[-1]["content"]
        assert "The real owned passage." in prompt[-1]["content"]
        assert "The actual question?" in prompt[-1]["content"]
        assert [message["role"] for message in prompt] == (["system", "user", "assistant", "user"] if enabled else ["system", "user"])
        if enabled:
            assert "not evidence for the real question" in prompt[1]["content"]
            assert prompt[2]["content"].endswith("[S1]")


def fake_index(monkeypatch):
    points = {}

    def remove(_user, document):
        for key in list(points):
            if points[key]["payload"]["document_id"] == str(document):
                del points[key]

    def upsert(_name, user, document, generation, digest, chunks, vectors):
        for chunk, vector in zip(chunks, vectors, strict=True):
            points[str(chunk.id)] = {
                "id": str(chunk.id),
                "payload": {
                    "user_id": str(user),
                    "document_id": str(document),
                    "generation": generation,
                    "model_digest": digest,
                },
                "score": 0.92,
                "vector": vector,
            }

    monkeypatch.setattr(worker, "embedding_digest", lambda: DIGEST)
    monkeypatch.setattr(worker, "embed", lambda texts, *, query: [[0.4, 0.6] for _ in texts])
    monkeypatch.setattr(worker, "ensure_collection", lambda _name, _size: None)
    monkeypatch.setattr(worker, "delete_all_document_points", remove)
    monkeypatch.setattr(worker, "upsert_chunks", upsert)
    monkeypatch.setattr(rag, "embedding_digest", lambda: DIGEST)
    monkeypatch.setattr(rag, "embed", lambda texts, *, query: [[0.4, 0.6] for _ in texts])
    monkeypatch.setattr(
        rag,
        "query_points",
        lambda _name, _vector, _user, _documents: list(points.values()),
    )
    return points


def upload(client: TestClient, token: str, content: bytes | None = None):
    return client.post(
        "/api/documents",
        headers=csrf_headers(token),
        files={"file": ("networking_notes.pdf", content if content is not None else FIXTURE.read_bytes(), "application/pdf")},
    )


def conversation(client: TestClient, token: str) -> str:
    response = client.post(
        "/api/conversations", headers=csrf_headers(token), json={"title": "Networking"}
    )
    assert response.status_code == 201
    return response.json()["id"]


@pytest.mark.parametrize("citation_example", [False, True])
def test_upload_index_cited_chat_and_delete(
    client: TestClient, users, session_factory: sessionmaker[Session], tmp_path, monkeypatch, citation_example
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "rag_citation_example", citation_example)
    old_root = settings.upload_root
    settings.upload_root = str(tmp_path)
    try:
        points = fake_index(monkeypatch)
        prompts = []
        def answer(prompt):
            prompts.append(prompt)
            return iter(["TCP uses acknowledgements [S1]."])
        monkeypatch.setattr(messages_api, "stream_chat", answer)
        token = login(client, "alice@example.test")
        posted = upload(client, token)
        assert posted.status_code == 202
        document_id = posted.json()["id"]
        assert posted.json()["status"] == "pending"
        assert client.get(f"/api/documents/{document_id}/file").content.startswith(b"%PDF-")
        assert worker.run_once(session_factory)
        indexed = client.get(f"/api/documents/{document_id}").json()
        assert indexed["status"] == "indexed"
        assert indexed["page_count"] == 2
        assert points
        with session_factory() as db:
            chunks = db.query(DocumentChunk).filter_by(document_id=UUID(document_id)).all()
            assert any(chunk.page_number == 1 and "acknowledgements" in chunk.content for chunk in chunks)
            assert any(chunk.page_number == 2 and "UDP" in chunk.content for chunk in chunks)

        conversation_id = conversation(client, token)
        attach_url = f"/api/conversations/{conversation_id}/documents/{document_id}"
        assert client.put(attach_url, headers=csrf_headers(token)).status_code == 204
        chat_url = f"/api/conversations/{conversation_id}/messages"
        request_id = str(uuid4())
        question = "How does TCP deliver data reliably?"
        reply = client.post(
            chat_url,
            headers=csrf_headers(token),
            json={"request_id": request_id, "content": question},
        )
        assert reply.status_code == 200
        assert "event: source" in reply.text
        assert "event: complete" in reply.text
        assert any(item["content"] == "The sample is blue. [S1]" for item in prompts[0]) == citation_example
        assert "PDF passages:" in prompts[0][-1]["content"]
        replay = client.post(
            chat_url,
            headers=csrf_headers(token),
            json={"request_id": request_id, "content": question},
        )
        assert replay.status_code == 200
        assert "event: source" in replay.text
        assert '"unavailable": false' in replay.text
        assert '"sources": [' in replay.text
        assistant = client.get(chat_url).json()[-1]
        assert assistant["status"] == "completed"
        assert assistant["sources"][0]["page_number"] == 1
        assert assistant["sources"][0]["filename"] == "networking_notes.pdf"
        assert assistant["sources"][0]["unavailable"] is False
        followup = client.post(
            chat_url,
            headers=csrf_headers(token),
            json={"request_id": str(uuid4()), "content": "And why are acknowledgements useful?"},
        )
        assert "event: complete" in followup.text
        assert any(item["content"] == "How does TCP deliver data reliably?" for item in prompts[1])
        assert any(item["content"] == "TCP uses acknowledgements [S1]." for item in prompts[1])

        path = stored_path(f"{users['alice'].id}/{document_id}.pdf")
        assert path.exists()
        assert client.delete(f"/api/documents/{document_id}", headers=csrf_headers(token)).status_code == 204
        assert client.get(f"/api/documents/{document_id}/file").status_code == 404
        deleted_source = client.get(chat_url).json()[-1]["sources"][0]
        assert deleted_source["unavailable"] is True
        assert deleted_source["quote"] == ""
        deleted_replay = client.post(
            chat_url,
            headers=csrf_headers(token),
            json={"request_id": request_id, "content": question},
        )
        assert deleted_replay.status_code == 200
        assert '"unavailable": true' in deleted_replay.text
        assert worker.run_once(session_factory)
        assert not path.exists()
        assert not points
        assert client.get(f"/api/documents/{document_id}").status_code == 404
        assert client.get(chat_url).json()[-1]["sources"][0]["unavailable"] is True
    finally:
        settings.upload_root = old_root


def test_upload_rejects_wrong_type_and_size(
    client: TestClient, users, tmp_path
) -> None:
    settings = get_settings()
    old_root, old_limit = settings.upload_root, settings.max_upload_bytes
    settings.upload_root = str(tmp_path)
    try:
        token = login(client, "alice@example.test")
        assert upload(client, token, b"not a PDF").status_code == 415
        settings.max_upload_bytes = 1024
        assert upload(client, token).status_code == 413
        assert client.get("/api/documents").json() == []
    finally:
        settings.upload_root, settings.max_upload_bytes = old_root, old_limit


def test_image_only_encrypted_and_corrupt_pdfs_fail_clearly(
    client: TestClient, users, session_factory: sessionmaker[Session], tmp_path
) -> None:
    settings = get_settings()
    old_root = settings.upload_root
    settings.upload_root = str(tmp_path)
    try:
        token = login(client, "alice@example.test")
        image_only = PdfWriter()
        image_only.add_blank_page(612, 792)
        blank = BytesIO()
        image_only.write(blank)
        encrypted_writer = PdfWriter()
        encrypted_writer.append(PdfReader(str(FIXTURE)))
        encrypted_writer.encrypt("secret")
        encrypted = BytesIO()
        encrypted_writer.write(encrypted)
        expected = [
            (blank.getvalue(), "image_only_pdf"),
            (encrypted.getvalue(), "encrypted_pdf"),
            (b"%PDF-1.7" + bytes([10]) + b"not a real PDF", "corrupt_pdf"),
        ]
        for content, code in expected:
            response = upload(client, token, content)
            assert response.status_code == 202
            assert worker.run_once(session_factory)
            status = client.get(f"/api/documents/{response.json()['id']}").json()
            assert status["status"] == "failed"
            assert status["error_code"] == code
    finally:
        settings.upload_root = old_root


def test_failed_embedding_job_retries_after_model_becomes_available(
    client: TestClient, users, session_factory: sessionmaker[Session], tmp_path, monkeypatch
) -> None:
    settings = get_settings()
    old_root, old_attempts = settings.upload_root, settings.worker_max_attempts
    settings.upload_root = str(tmp_path)
    settings.worker_max_attempts = 1
    try:
        token = login(client, "alice@example.test")
        posted = upload(client, token)
        document_id = posted.json()["id"]
        monkeypatch.setattr(
            worker, "embedding_digest",
            lambda: (_ for _ in ()).throw(IndexProblem("embedding_model_missing")),
        )
        assert worker.run_once(session_factory)
        assert client.get(f"/api/documents/{document_id}").json()["status"] == "failed"
        fake_index(monkeypatch)
        retry = client.post(f"/api/documents/{document_id}/retry", headers=csrf_headers(token))
        assert retry.status_code == 202
        assert worker.run_once(session_factory)
        assert client.get(f"/api/documents/{document_id}").json()["status"] == "indexed"
        with session_factory() as db:
            document = db.get(Document, UUID(document_id))
            assert document.index_generation == 2
    finally:
        settings.upload_root, settings.worker_max_attempts = old_root, old_attempts


def test_cross_student_guessed_ids_never_expose_pdf_or_sources(
    client: TestClient, users, session_factory: sessionmaker[Session], tmp_path, monkeypatch
) -> None:
    settings = get_settings()
    old_root = settings.upload_root
    settings.upload_root = str(tmp_path)
    try:
        fake_index(monkeypatch)
        alice_token = login(client, "alice@example.test")
        posted = upload(client, alice_token)
        document_id = posted.json()["id"]
        assert worker.run_once(session_factory)
        from app.main import app
        with TestClient(app, base_url=TEST_ORIGIN) as bob:
            bob_token = login(bob, "bob@example.test")
            for url in (f"/api/documents/{document_id}", f"/api/documents/{document_id}/file"):
                assert bob.get(url).status_code == 404
            assert bob.post(f"/api/documents/{document_id}/retry", headers=csrf_headers(bob_token)).status_code == 404
            assert bob.delete(f"/api/documents/{document_id}", headers=csrf_headers(bob_token)).status_code == 404
            bob_conversation = conversation(bob, bob_token)
            assert bob.put(
                f"/api/conversations/{bob_conversation}/documents/{document_id}",
                headers=csrf_headers(bob_token),
            ).status_code == 404
            with session_factory() as db:
                assert not rag.retrieve(
                    session_factory, UUID(bob_conversation), users["bob"].id,
                    [{"document_id": document_id, "generation": 1}],
                    "What is TCP?",
                )
    finally:
        settings.upload_root = old_root


def test_unrelated_question_and_invented_citation_do_not_return_sources(
    client: TestClient, users, session_factory: sessionmaker[Session], tmp_path, monkeypatch
) -> None:
    settings = get_settings()
    old_root = settings.upload_root
    settings.upload_root = str(tmp_path)
    try:
        points = fake_index(monkeypatch)
        token = login(client, "alice@example.test")
        document_id = upload(client, token).json()["id"]
        assert worker.run_once(session_factory)
        conversation_id = conversation(client, token)
        client.put(
            f"/api/conversations/{conversation_id}/documents/{document_id}",
            headers=csrf_headers(token),
        )
        chat_url = f"/api/conversations/{conversation_id}/messages"
        monkeypatch.setattr(rag, "query_points", lambda *_args: [])
        monkeypatch.setattr(
            messages_api, "stream_chat",
            lambda _prompt: (_ for _ in ()).throw(AssertionError("No evidence must skip chat")),
        )
        unrelated = client.post(
            chat_url, headers=csrf_headers(token),
            json={"request_id": str(uuid4()), "content": "What is the price of gold today?"},
        )
        assert "event: complete" in unrelated.text
        assert rag.NO_EVIDENCE in unrelated.text
        assert client.get(chat_url).json()[-1]["sources"] == []

        monkeypatch.setattr(rag, "query_points", lambda *_args: list(points.values()))
        monkeypatch.setattr(messages_api, "stream_chat", lambda _prompt: iter(["The passages do not answer this question."]))
        unsupported = client.post(
            chat_url, headers=csrf_headers(token),
            json={"request_id": str(uuid4()), "content": "What is the price of gold today?"},
        )
        assert "event: complete" in unsupported.text
        assert rag.NO_EVIDENCE in unsupported.text
        assert client.get(chat_url).json()[-1]["sources"] == []

        monkeypatch.setattr(messages_api, "stream_chat", lambda _prompt: iter(["Unsupported claim [S99]."]))
        invented = client.post(
            chat_url, headers=csrf_headers(token),
            json={"request_id": str(uuid4()), "content": "What does the PDF say about TCP?"},
        )
        assert "event: failed" in invented.text
        assert "invalid_citation" in invented.text
        assert client.get(chat_url).json()[-1]["sources"] == []
    finally:
        settings.upload_root = old_root


def test_stale_other_student_vector_is_discarded_after_database_check(
    client: TestClient, users, session_factory: sessionmaker[Session], tmp_path, monkeypatch
) -> None:
    settings = get_settings()
    old_root = settings.upload_root
    settings.upload_root = str(tmp_path)
    try:
        points = fake_index(monkeypatch)
        alice_token = login(client, "alice@example.test")
        alice_document = upload(client, alice_token).json()["id"]
        assert worker.run_once(session_factory)
        from app.main import app
        with TestClient(app, base_url=TEST_ORIGIN) as bob:
            bob_token = login(bob, "bob@example.test")
            bob_document = upload(bob, bob_token).json()["id"]
            assert worker.run_once(session_factory)
        conversation_id = conversation(client, alice_token)
        client.put(
            f"/api/conversations/{conversation_id}/documents/{alice_document}",
            headers=csrf_headers(alice_token),
        )
        foreign_hits = [
            point for point in points.values()
            if point["payload"]["document_id"] == bob_document
        ]
        monkeypatch.setattr(rag, "query_points", lambda *_args: foreign_hits)
        monkeypatch.setattr(
            messages_api, "stream_chat",
            lambda _prompt: (_ for _ in ()).throw(AssertionError("Foreign hit must be ignored")),
        )
        response = client.post(
            f"/api/conversations/{conversation_id}/messages",
            headers=csrf_headers(alice_token),
            json={"request_id": str(uuid4()), "content": "Explain TCP"},
        )
        assert "event: complete" in response.text
        assert rag.NO_EVIDENCE in response.text
    finally:
        settings.upload_root = old_root


def test_expired_lease_recovers_and_reindex_replaces_old_generation(
    client: TestClient, users, session_factory: sessionmaker[Session], tmp_path, monkeypatch
) -> None:
    settings = get_settings()
    old_root = settings.upload_root
    settings.upload_root = str(tmp_path)
    try:
        points = fake_index(monkeypatch)
        token = login(client, "alice@example.test")
        document_id = upload(client, token).json()["id"]
        with session_factory() as db:
            job = db.query(DocumentJob).filter_by(document_id=UUID(document_id)).one()
            job.status = "running"
            job.attempts = 1
            job.lease_until = utc_now() - timedelta(minutes=1)
            db.commit()
        assert worker.run_once(session_factory)
        first_ids = set(points)
        assert first_ids
        retry = client.post(f"/api/documents/{document_id}/retry", headers=csrf_headers(token))
        assert retry.status_code == 202
        assert worker.run_once(session_factory)
        assert points
        assert first_ids.isdisjoint(points)
        with session_factory() as db:
            document = db.get(Document, UUID(document_id))
            assert document.index_generation == 2
            assert {chunk.generation for chunk in document.chunks} == {2}
    finally:
        settings.upload_root = old_root


def test_failed_deletion_can_be_requeued_without_reexposing_document(
    client: TestClient, users, session_factory: sessionmaker[Session], tmp_path, monkeypatch
) -> None:
    settings = get_settings()
    old_root, old_attempts = settings.upload_root, settings.worker_max_attempts
    settings.upload_root = str(tmp_path)
    settings.worker_max_attempts = 1
    try:
        fake_index(monkeypatch)
        token = login(client, "alice@example.test")
        document_id = upload(client, token).json()["id"]
        assert worker.run_once(session_factory)
        original_delete = worker.delete_all_document_points
        monkeypatch.setattr(
            worker, "delete_all_document_points",
            lambda *_args: (_ for _ in ()).throw(IndexProblem("vector_store_unavailable")),
        )
        url = f"/api/documents/{document_id}"
        assert client.delete(url, headers=csrf_headers(token)).status_code == 204
        assert worker.run_once(session_factory)
        status = client.get(url).json()
        assert status["status"] == "deleting"
        assert status["error_code"] == "vector_store_unavailable"
        assert client.get(url + "/file").status_code == 404
        monkeypatch.setattr(worker, "delete_all_document_points", original_delete)
        assert client.delete(url, headers=csrf_headers(token)).status_code == 204
        assert worker.run_once(session_factory)
        assert client.get(url).status_code == 404
    finally:
        settings.upload_root, settings.worker_max_attempts = old_root, old_attempts
