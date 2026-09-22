from dataclasses import asdict, dataclass
import json
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

from sqlalchemy import text

from app.config import get_settings
from app.db import get_engine


@dataclass(frozen=True)
class ComponentStatus:
    ok: bool
    detail: str

    def as_dict(self) -> dict[str, bool | str]:
        return asdict(self)


def _http_probe(url: str, expected_json_key: str | None = None) -> ComponentStatus:
    timeout = get_settings().readiness_timeout_seconds
    try:
        with urlopen(url, timeout=timeout) as response:
            if not 200 <= response.status < 300:
                return ComponentStatus(False, f"HTTP {response.status}")
            if expected_json_key is not None:
                body = json.load(response)
                if expected_json_key not in body:
                    return ComponentStatus(False, "unexpected response")
        return ComponentStatus(True, "reachable")
    except (HTTPError, URLError, OSError, TimeoutError, json.JSONDecodeError):
        return ComponentStatus(False, "unreachable")


def check_postgres() -> ComponentStatus:
    try:
        with get_engine().connect() as connection:
            connection.execute(text("SELECT 1"))
        return ComponentStatus(True, "reachable")
    except Exception:  # The public endpoint must not leak credentials or driver details.
        return ComponentStatus(False, "unreachable")


def check_qdrant() -> ComponentStatus:
    return _http_probe(f"{get_settings().qdrant_url.rstrip('/')}/readyz")


def check_ollama() -> ComponentStatus:
    settings = get_settings()
    try:
        with urlopen(
            f"{settings.ollama_base_url.rstrip('/')}/api/tags",
            timeout=settings.readiness_timeout_seconds,
        ) as response:
            if not 200 <= response.status < 300:
                return ComponentStatus(False, f"HTTP {response.status}")
            models = json.load(response).get("models", [])
            if not isinstance(models, list):
                return ComponentStatus(False, "unexpected response")
            available = {
                value
                for item in models if isinstance(item, dict)
                for value in (item.get("name"), item.get("model"))
                if isinstance(value, str)
            }
            if settings.ollama_chat_model not in available:
                return ComponentStatus(False, "chat model missing")
            embed_names = {settings.ollama_embed_model}
            if ":" not in settings.ollama_embed_model:
                embed_names.add(settings.ollama_embed_model + ":latest")
            if not embed_names.intersection(available):
                return ComponentStatus(False, "embedding model missing")
            if settings.ollama_embed_digest:
                matching = [
                    item for item in models if isinstance(item, dict)
                    and (item.get("name") in embed_names or item.get("model") in embed_names)
                ]
                if not any(
                    item.get("digest", "").lower() == settings.ollama_embed_digest.lower()
                    for item in matching if isinstance(item.get("digest"), str)
                ):
                    return ComponentStatus(False, "embedding digest mismatch")
            return ComponentStatus(True, "chat and embedding models installed")
    except (HTTPError, URLError, OSError, TimeoutError, ValueError, AttributeError):
        return ComponentStatus(False, "unreachable")


def probe_dependencies() -> dict[str, ComponentStatus]:
    return {
        "postgres": check_postgres(),
        "qdrant": check_qdrant(),
        "ollama": check_ollama(),
    }
