"""Small Ollama streaming adapter. No local model is required to import it."""

from collections.abc import Iterator
import json
from time import monotonic

import httpx

from app.config import get_settings


class ChatFailure(Exception):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def stream_chat(messages: list[dict[str, str]]) -> Iterator[str]:
    settings = get_settings()
    deadline = monotonic() + settings.chat_generation_seconds
    payload = {
        "model": settings.ollama_chat_model,
        "messages": messages,
        "stream": True,
        "think": "low",
        "options": {
            "num_ctx": settings.chat_context_tokens,
            "num_predict": settings.chat_predict_tokens,
        },
    }
    timeout = httpx.Timeout(
        connect=5.0,
        read=settings.chat_generation_seconds,
        write=10.0,
        pool=5.0,
    )
    try:
        with httpx.Client(timeout=timeout) as client:
            with client.stream(
                "POST",
                f"{settings.ollama_base_url.rstrip('/')}/api/chat",
                json=payload,
            ) as response:
                if response.status_code == 404:
                    raise ChatFailure("model_missing")
                if response.status_code != 200:
                    raise ChatFailure("ollama_unavailable")
                finished = False
                for line in response.iter_lines():
                    if monotonic() >= deadline:
                        raise ChatFailure("generation_timeout")
                    if not line:
                        continue
                    try:
                        part = json.loads(line)
                    except json.JSONDecodeError as exc:
                        raise ChatFailure("invalid_model_response") from exc
                    if part.get("error"):
                        raise ChatFailure("ollama_error")
                    chunk = part.get("message", {}).get("content", "")
                    if not isinstance(chunk, str):
                        raise ChatFailure("invalid_model_response")
                    if chunk:
                        yield chunk
                    if part.get("done") is True:
                        finished = True
                        break
                if not finished:
                    raise ChatFailure("incomplete_model_response")
    except httpx.TimeoutException as exc:
        raise ChatFailure("generation_timeout") from exc
    except httpx.HTTPError as exc:
        raise ChatFailure("ollama_unavailable") from exc
