# Smart Learning System backend

FastAPI service for the three-VM lab. Milestone 04 provides liveness/readiness,
Alembic migrations, administrator-created users, Argon2id passwords, hashed
opaque sessions, CSRF/origin protection, private conversations, and ownership
checks for document metadata and conversation attachments. M3 adds durable
normal chat, bounded Ollama streaming, history, and idempotent retries.

M4 adds private PDF storage, page-aware extraction, leased indexing jobs,
Ollama embeddings, Qdrant vectors, attachment-scoped retrieval, cited answers,
and deferred cleanup. The local tests mock model and vector responses; no
model weights are needed on this development machine.

Login attempts are throttled before password verification: by default, at most
10 attempts per normalized email and 60 attempts in total within a rolling
60-second window, with at most two simultaneous verifications. Successful and
unknown-account attempts both count. Rejections return HTTP 429 with
`Retry-After`; the UI displays a sign-in-specific notice. The `LOGIN_*` settings
in `.env.example` control these limits. Account identifiers are hashed in the
limiter, and forwarded IP headers cannot bypass it. Like the chat queue, the
limiter belongs to the supported single API process and resets on restart;
multiple API processes or replicas require a shared limiter before deployment.

Obsolete indexing failures cannot change documents marked for deletion or a
newer index generation. Document row locks serialize worker state changes with
deletion/reindexing requests. A job whose lease expires after
`WORKER_MAX_ATTEMPTS` claims (the worker died while processing it) is marked
failed with `processing_interrupted` instead of being reclaimed indefinitely.

The chat queue admits at most `CHAT_QUEUE_CAPACITY` requests, and at most
`CHAT_QUEUE_USER_CAPACITY` per student. A ticket whose response stream never
starts (the client left before the first byte) expires after 15 seconds, so it
cannot block the head of the queue.

Document answers request Ollama structured output: JSON with `answer`,
`supported`, and `sources`, where `sources` is restricted to the retrieved
passage markers. Markers are validated against the retrieved passages and then
rechecked in PostgreSQL. An unsupported answer returns the no-evidence reply;
an uncited or malformed answer is regenerated once with a correction, and
then falls back to an explicit "couldn't produce an answer with verified
citations" reply with no sources. Set `RAG_STRUCTURED_OUTPUT=false` to restore
free-text `[S1]` markers. A valid citation shows that a retrieved passage was
selected, not that the passage supports the claim; check that with
known-answer questions. While a document answer is validated, the stream sends
SSE keepalive comments so proxies do not time out.
