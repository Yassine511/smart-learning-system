from uuid import uuid4
import json

import httpx
import pytest

from app.config import Settings
from app.services import vector_index
from app.services.pdf_files import ExtractedChunk

DIGEST = "b" * 64


@pytest.mark.parametrize("threads", [0, 1])
def test_embedding_api_uses_nomic_prefixes_and_resolves_digest(monkeypatch, threads) -> None:
    monkeypatch.setattr(vector_index, "get_settings", lambda: Settings(_env_file=None, ollama_num_threads=threads))
    requests = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "nomic-embed-text:latest", "digest": DIGEST}]})
        assert request.url.path == "/api/embed"
        return httpx.Response(200, json={"embeddings": [[0.4, 0.6]]})

    transport = httpx.MockTransport(handle)
    monkeypatch.setattr(
        vector_index, "_ollama_client",
        lambda: httpx.Client(base_url="http://ollama:11434", transport=transport),
    )
    assert vector_index.embedding_digest() == DIGEST
    assert vector_index.embed(["TCP"], query=False) == [[0.4, 0.6]]
    assert vector_index.embed(["TCP?"], query=True) == [[0.4, 0.6]]
    document_payload = json.loads(requests[1].content)
    query_payload = json.loads(requests[2].content)
    assert document_payload["input"] == ["search_document: TCP"]
    assert query_payload["input"] == ["search_query: TCP?"]
    assert document_payload["truncate"] is False
    if threads:
        assert document_payload["options"] == query_payload["options"] == {"num_thread": threads}
    else:
        assert "options" not in document_payload
        assert "options" not in query_payload


def test_qdrant_api_creates_collection_and_filters_queries(monkeypatch) -> None:
    requests = []
    collection_exists = False

    def handle(request: httpx.Request) -> httpx.Response:
        nonlocal collection_exists
        requests.append(request)
        path = request.url.path
        if path == "/collections/smart_learning_chunks_" + DIGEST[:16]:
            if request.method == "GET" and not collection_exists:
                return httpx.Response(404)
            if request.method == "PUT":
                collection_exists = True
                assert json.loads(request.content)["vectors"] == {"size": 2, "distance": "Cosine"}
                return httpx.Response(200, json={"result": True})
            return httpx.Response(200, json={"result": {"config": {"params": {"vectors": {"size": 2}}}}})
        if path.endswith("/points") and request.method == "PUT":
            return httpx.Response(200, json={"result": {"status": "completed"}})
        if path.endswith("/points/query"):
            return httpx.Response(200, json={"result": {"points": []}})
        if path == "/collections":
            return httpx.Response(200, json={"result": {"collections": [
                {"name": "smart_learning_chunks_" + DIGEST[:16]}, {"name": "other_collection"},
            ]}})
        if path.endswith("/points/delete"):
            return httpx.Response(200, json={"result": {"status": "completed"}})
        raise AssertionError(str(request.url))

    monkeypatch.setattr(
        vector_index, "_qdrant_client",
        lambda: httpx.Client(base_url="http://qdrant:6333", transport=httpx.MockTransport(handle)),
    )
    user_id, document_id, chunk_id = uuid4(), uuid4(), uuid4()
    name = vector_index.collection_name(DIGEST)
    vector_index.ensure_collection(name, 2)
    chunk = ExtractedChunk(id=chunk_id, page_number=1, ordinal=0, content="TCP")
    vector_index.upsert_chunks(name, user_id, document_id, 2, DIGEST, [chunk], [[0.4, 0.6]])
    vector_index.query_points(name, [0.4, 0.6], user_id, [document_id])
    vector_index.delete_all_document_points(user_id, document_id)

    upsert = next(request for request in requests if request.url.path.endswith("/points") and request.method == "PUT")
    point = json.loads(upsert.content)["points"][0]
    assert point["id"] == str(chunk_id)
    assert point["payload"] == {
        "user_id": str(user_id), "document_id": str(document_id),
        "chunk_id": str(chunk_id), "generation": 2,
        "model_digest": DIGEST, "page_number": 1,
    }
    query = next(request for request in requests if request.url.path.endswith("/points/query"))
    conditions = json.loads(query.content)["filter"]["must"]
    assert {"key": "user_id", "match": {"value": str(user_id)}} in conditions
    assert {"key": "document_id", "match": {"any": [str(document_id)]}} in conditions
    deletion = next(request for request in requests if request.url.path.endswith("/points/delete"))
    assert json.loads(deletion.content)["filter"]["must"][1]["match"]["value"] == str(document_id)
