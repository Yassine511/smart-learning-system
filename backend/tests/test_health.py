from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def test_health_is_independent_of_external_services() -> None:
    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "Smart Learning System",
        "version": "0.1.0",
        "environment": "development",
    }


def test_readiness_reports_all_components(monkeypatch) -> None:
    from app import main
    from app.services.readiness import ComponentStatus

    monkeypatch.setattr(
        main,
        "probe_dependencies",
        lambda: {
            "postgres": ComponentStatus(True, "reachable"),
            "qdrant": ComponentStatus(True, "reachable"),
            "ollama": ComponentStatus(False, "unreachable"),
        },
    )

    response = client.get("/api/ready")

    assert response.status_code == 503
    assert response.json()["status"] == "not_ready"
    assert response.json()["components"]["ollama"]["ok"] is False


def test_ollama_readiness_requires_configured_model(monkeypatch) -> None:
    import io
    from app.services import readiness

    class Response(io.BytesIO):
        status = 200

    monkeypatch.setattr(
        readiness,
        "urlopen",
        lambda *_args, **_kwargs: Response(b'{"models":[{"name":"another:latest"}]}'),
    )
    missing = readiness.check_ollama()
    assert missing.ok is False
    assert missing.detail == "chat model missing"

    monkeypatch.setattr(
        readiness,
        "urlopen",
        lambda *_args, **_kwargs: Response(b'{"models":[{"name":"gpt-oss:20b"},{"name":"nomic-embed-text:latest"}]}'),
    )
    assert readiness.check_ollama().ok is True


def test_ollama_readiness_detects_wrong_embedding_digest(monkeypatch) -> None:
    import io
    from app.config import get_settings
    from app.services import readiness

    class Response(io.BytesIO):
        status = 200

    monkeypatch.setattr(
        readiness, "urlopen",
        lambda *_args, **_kwargs: Response(
            b'{"models":[{"name":"gpt-oss:20b"},{"name":"nomic-embed-text:latest","digest":"'
            + b"a" * 64 + b'"}]}'
        ),
    )
    settings = get_settings()
    old = settings.ollama_embed_digest
    settings.ollama_embed_digest = "b" * 64
    try:
        status = readiness.check_ollama()
        assert status.ok is False
        assert status.detail == "embedding digest mismatch"
    finally:
        settings.ollama_embed_digest = old
