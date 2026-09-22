# M3 normal chat and later server setup

M3 stores each user question and assistant reply in PostgreSQL. The API streams
status and answer events from Ollama. A failed reply keeps its original request
UUID so the student can retry without duplicating the question. The UI shows
private history and retry controls. One backend process runs one generation at a
time; the admission limit is set by CHAT_QUEUE_CAPACITY.

## On this development machine

GPT-OSS is not installed here. Run backend tests with the project virtual
environment and build the frontend without pulling model weights:

    cd backend
    .venv/Scripts/python.exe -m pytest -q
    cd ../frontend
    npm run build

The tests use a fake Ollama HTTP stream. They do not measure real model speed.

## After pulling on the server VM

Copy .env.example to .env. Set a fresh POSTGRES_PASSWORD and put the same
password, URL-encoded if needed, in DATABASE_URL. Set APP_ORIGIN to the exact
Tailscale Serve HTTPS origin and ALLOWED_ORIGINS to that same origin. Keep
SESSION_COOKIE_SECURE=true. The lab override runs its own Ollama container:

    docker compose -f compose.yaml -f compose.lab.yaml up --build -d
    docker compose -f compose.yaml -f compose.lab.yaml exec ollama ollama pull gpt-oss:20b
    docker compose -f compose.yaml -f compose.lab.yaml exec ollama ollama list

Keep OLLAMA_CHAT_MODEL=gpt-oss:20b and OLLAMA_BASE_URL=http://ollama:11434 for
the VM lab. Pull nomic-embed-text for M4 document indexing as described in
[m4-documents.md](m4-documents.md).

## After the complete lab has passed and code is pulled to the physical server

Reuse the existing Ollama container. Do not start compose.lab.yaml there.
Connect the existing Ollama container to the Compose application network and
give it a stable DNS alias (for example, ollama). Set OLLAMA_BASE_URL in .env to
that container's reachable internal URL, such as http://ollama:11434. Never use
localhost:11434 inside the backend container. Record the network attachment in
the final deployment override/runbook so it survives container recreation.

Inside the existing Ollama container, pull and inspect the selected model:

    docker exec -it <existing-ollama-container> ollama pull gpt-oss:20b
    docker exec -it <existing-ollama-container> ollama list

Use compose.yaml for the app, databases, and worker. Before serving students,
check /api/ready, sign in with two distinct accounts, send normal chat requests,
confirm each account sees only its own messages, and retry a failed request
using the same UUID. Measure one-client and two-client latency on the actual
hardware. M4 document indexing also needs nomic-embed-text. See
[m4-documents.md](m4-documents.md).

The M3 stream endpoint is POST /api/conversations/{id}/messages with JSON
{"request_id":"<UUID>","content":"..."}. It requires the session cookie, exact
Origin, and X-CSRF-Token. It emits SSE status, delta, complete, or failed
events. The reply remains failed if the connection is interrupted. Queue
capacity and timeouts are configurable in .env.
