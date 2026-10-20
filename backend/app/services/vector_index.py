"""Ollama embeddings and Qdrant vector operations."""

import math
import re
from uuid import UUID

import httpx

from app.config import get_settings


class IndexProblem(Exception):
    def __init__(self, code: str, retryable: bool = True) -> None:
        self.code = code
        self.retryable = retryable
        super().__init__(code)


def _ollama_client() -> httpx.Client:
    return httpx.Client(
        base_url=get_settings().ollama_base_url.rstrip("/"),
        timeout=httpx.Timeout(connect=5, read=120, write=20, pool=5),
    )


def _qdrant_client() -> httpx.Client:
    return httpx.Client(
        base_url=get_settings().qdrant_url.rstrip("/"),
        timeout=httpx.Timeout(connect=5, read=30, write=20, pool=5),
    )


def embed_model_entries(models: list) -> list[dict]:
    """Entries of an Ollama ``/api/tags`` list naming the configured embedding model."""
    requested = get_settings().ollama_embed_model
    aliases = {requested} if ":" in requested else {requested, f"{requested}:latest"}
    return [
        model for model in models
        if isinstance(model, dict) and (model.get("name") in aliases or model.get("model") in aliases)
    ]


def embedding_digest() -> str:
    settings = get_settings()
    try:
        with _ollama_client() as client:
            response = client.get("/api/tags")
            response.raise_for_status()
            models = response.json().get("models", [])
    except (httpx.HTTPError, ValueError) as exc:
        raise IndexProblem("embedding_service_unavailable") from exc
    for model in embed_model_entries(models):
        digest = model.get("digest")
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", digest):
            raise IndexProblem("embedding_digest_unavailable")
        digest = digest.lower()
        if settings.ollama_embed_digest and digest != settings.ollama_embed_digest.lower():
            raise IndexProblem("embedding_digest_mismatch", retryable=False)
        return digest
    raise IndexProblem("embedding_model_missing")


def collection_name(digest: str) -> str:
    prefix = get_settings().qdrant_collection_prefix
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", prefix):
        raise IndexProblem("invalid_collection_prefix", retryable=False)
    return f"{prefix}_{digest[:16]}"


def embed(texts: list[str], *, query: bool) -> list[list[float]]:
    if not texts:
        return []
    settings = get_settings()
    prefix = "search_query: " if query else "search_document: "
    payload = {
        "model": settings.ollama_embed_model,
        "input": [prefix + value for value in texts],
        "truncate": False,
    }
    if settings.ollama_num_threads:
        payload["options"] = {"num_thread": settings.ollama_num_threads}
    try:
        with _ollama_client() as client:
            response = client.post(
                "/api/embed",
                json=payload,
            )
            if response.status_code == 404:
                raise IndexProblem("embedding_model_missing")
            response.raise_for_status()
            vectors = response.json().get("embeddings")
    except IndexProblem:
        raise
    except (httpx.HTTPError, ValueError) as exc:
        raise IndexProblem("embedding_service_unavailable") from exc
    if not isinstance(vectors, list) or len(vectors) != len(texts):
        raise IndexProblem("invalid_embedding_response")
    dimension = None
    for vector in vectors:
        if not isinstance(vector, list) or not vector:
            raise IndexProblem("invalid_embedding_response")
        if dimension is None:
            dimension = len(vector)
        if len(vector) != dimension or any(
            not isinstance(value, (int, float)) or not math.isfinite(value)
            for value in vector
        ):
            raise IndexProblem("invalid_embedding_response")
    return vectors


def ensure_collection(name: str, dimension: int) -> None:
    try:
        with _qdrant_client() as client:
            response = client.get(f"/collections/{name}")
            if response.status_code == 404:
                created = client.put(
                    f"/collections/{name}",
                    json={"vectors": {"size": dimension, "distance": "Cosine"}},
                )
                created.raise_for_status()
                response = client.get(f"/collections/{name}")
            response.raise_for_status()
            vectors = response.json()["result"]["config"]["params"]["vectors"]
            if vectors.get("size") != dimension:
                raise IndexProblem("embedding_dimension_mismatch", retryable=False)
    except IndexProblem:
        raise
    except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError) as exc:
        raise IndexProblem("vector_store_unavailable") from exc


def upsert_chunks(
    name: str,
    user_id: UUID,
    document_id: UUID,
    generation: int,
    digest: str,
    chunks: list,
    vectors: list[list[float]],
) -> None:
    points = [
        {
            "id": str(chunk.id),
            "vector": vector,
            "payload": {
                "user_id": str(user_id),
                "document_id": str(document_id),
                "generation": generation,
                "model_digest": digest,
            },
        }
        for chunk, vector in zip(chunks, vectors, strict=True)
    ]
    try:
        with _qdrant_client() as client:
            response = client.put(
                f"/collections/{name}/points",
                params={"wait": "true"},
                json={"points": points},
            )
            response.raise_for_status()
            if response.json().get("result", {}).get("status") not in {"completed", "acknowledged"}:
                raise IndexProblem("vector_upsert_failed")
    except IndexProblem:
        raise
    except (httpx.HTTPError, ValueError) as exc:
        raise IndexProblem("vector_store_unavailable") from exc


def delete_document_points(name: str | None, user_id: UUID, document_id: UUID) -> None:
    if not name:
        return
    try:
        with _qdrant_client() as client:
            response = client.post(
                f"/collections/{name}/points/delete",
                params={"wait": "true"},
                json={"filter": {"must": [
                    {"key": "user_id", "match": {"value": str(user_id)}},
                    {"key": "document_id", "match": {"value": str(document_id)}},
                ]}},
            )
            if response.status_code == 404:
                return
            response.raise_for_status()
    except httpx.HTTPError as exc:
        raise IndexProblem("vector_store_unavailable") from exc


def query_points(
    name: str,
    vector: list[float],
    user_id: UUID,
    document_ids: list[UUID],
) -> list[dict]:
    settings = get_settings()
    try:
        with _qdrant_client() as client:
            response = client.post(
                f"/collections/{name}/points/query",
                json={
                    "query": vector,
                    "filter": {"must": [
                        {"key": "user_id", "match": {"value": str(user_id)}},
                        {"key": "document_id", "match": {"any": [str(value) for value in document_ids]}},
                    ]},
                    "score_threshold": settings.retrieval_score_threshold,
                    "limit": settings.retrieval_limit,
                    "with_payload": True,
                    "with_vector": False,
                },
            )
            response.raise_for_status()
            return response.json()["result"]["points"]
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
        raise IndexProblem("vector_store_unavailable") from exc


def delete_all_document_points(user_id: UUID, document_id: UUID) -> None:
    prefix = get_settings().qdrant_collection_prefix + "_"
    try:
        with _qdrant_client() as client:
            response = client.get("/collections")
            response.raise_for_status()
            names = [
                item["name"]
                for item in response.json()["result"]["collections"]
                if isinstance(item.get("name"), str) and item["name"].startswith(prefix)
            ]
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
        raise IndexProblem("vector_store_unavailable") from exc
    for name in names:
        delete_document_points(name, user_id, document_id)
