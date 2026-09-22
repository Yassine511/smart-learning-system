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
