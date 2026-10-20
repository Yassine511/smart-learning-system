# Smart Learning System

A Computer Networks project: a centralized, privately hosted AI learning platform serving multiple students over Tailscale. Students have separate accounts, conversations, PDFs, and document retrieval contexts.

**Status (2026-10-19):** the separate local three-VM prototype passed functional
checks from both client VMs, including real chat, PDF retrieval, student
isolation, simultaneous requests, and outage recovery. See the
[acceptance record](docs/local-three-vm-acceptance.md) for measurements and the
unresolved VirtualBox stability limits. Manual browser/Wireshark testing is in
progress. The earlier physical CasaOS deployment and GPT-OSS configuration are
preserved.

## Deployment decisions

For the optional prototype using the three existing VirtualBox VMs on this PC,
see [local three-VM setup](docs/local-three-vm.md). It uses local HTTPS and Qwen
0.5B while preserving the physical-server configuration and GPT-OSS defaults.

- Stage 1: validate the complete system in three VMs: one Ubuntu server VM and two client VMs.
- The server VM runs the application, databases, private storage, and its own Ollama container. The lab does not depend on the physical server for inference.
- Each client VM has a browser and Tailscale and uses a separate student account.
- Stage 2: deploy the same application stack to the dedicated physical server. This deployment was completed ahead of the planned three-VM lab acceptance at the user's request.
- Physical target: Ubuntu 26.04 LTS with CasaOS, Intel Core i9-9900K, 32 GB RAM, and NVIDIA RTX 2080 Ti, as reported by the server owner.
- On the physical target, reuse the existing Ollama Docker container on port 11434 rather than creating a second inference service.
- HTTPS access within the tailnet. No public website or Tailscale Funnel in the initial deployment.

The physical server and its installed models were inspected during deployment. See [physical server deployment](docs/physical-server-deployment.md) for the current endpoint, Compose commands, model pin, and verified checks.

For the temporary public professor demo hosted on this Windows PC and VM1, see
[professor demo](docs/professor-demo.md) for the Funnel URL, measured checks,
private credential location, and start/stop commands.

## Architecture

```mermaid
flowchart TD
    A[Client VM A: browser + Tailscale] -->|HTTPS over Tailscale| TS
    B[Client VM B: browser + Tailscale] -->|HTTPS over Tailscale| TS
    subgraph SERVER[Server VM for course lab or physical CasaOS server]
        TS[Tailscale Serve: HTTPS entry point] --> WEB[Nginx: React static files and /api proxy]
        WEB --> API[FastAPI: authentication, chat, retrieval]
        API --> DB[(PostgreSQL: accounts, sessions, chats, chunks, jobs)]
        API --> Q[(Qdrant: vectors and filtering metadata)]
        API --> FILES[(Private PDF storage)]
        API --> O[Ollama: lab container or existing physical container]
        WORKER[PDF indexing worker] --> DB
        WORKER --> Q
        WORKER --> FILES
        WORKER --> O
    end
```

See [the architecture plan](docs/architecture.md) for boundaries, flows, API outline, implementation phases, and acceptance checks.

## Run the phase-one foundation

Prerequisites on the Ubuntu server VM are Docker Engine, Docker Compose, and
enough disk/RAM for the selected Ollama model. M3 does not pull model weights
automatically. Pull GPT-OSS 20B and nomic-embed-text on the chosen server VM before testing
chat and document indexing.

```bash
cp .env.example .env
# Replace POSTGRES_PASSWORD in .env, including inside DATABASE_URL.
# Set APP_ORIGIN and ALLOWED_ORIGINS to the exact Tailscale Serve HTTPS origin.
docker compose -f compose.yaml -f compose.lab.yaml config
docker compose -f compose.yaml -f compose.lab.yaml up --build -d
```

The web entry point binds only to `127.0.0.1:8080`, ready for Tailscale Serve.
Open `http://127.0.0.1:8080` on the server VM to see dependency readiness. API
liveness is available at `/api/health`; `/api/ready` returns HTTP 503 until
PostgreSQL and Qdrant are reachable and the selected chat model is installed in Ollama.

Useful checks:

```bash
docker compose -f compose.yaml -f compose.lab.yaml ps
curl http://127.0.0.1:8080/api/health
curl -i http://127.0.0.1:8080/api/ready
```

The backend applies Alembic migrations before starting. Create the two initial
student accounts interactively so passwords do not appear in shell history:

```bash
docker compose exec backend python -m app.cli create-user \
  --email student1@example.test --name "Student One"
docker compose exec backend python -m app.cli create-user \
  --email student2@example.test --name "Student Two"
```

Passwords use Argon2id. Login sessions use random opaque cookie values while
only SHA-256 token hashes are stored in PostgreSQL. Authenticated writes require
both an exact allowed browser origin and a matching CSRF header/cookie. The
session cookie is Secure, HttpOnly, and SameSite=Strict.

Milestones 02 through 04 routes:

- `POST /api/login`, `POST /api/logout`, and `GET /api/me`.
- List, create, read, rename, and delete private conversations.
- Upload, list, read, rename, view/download, reindex, and delete owned PDFs.
  Upload returns 202 while the worker indexes pages and chunks.
- Search owned indexed PDFs with `QUERY /api/documents/search` and the
  **Search my PDFs** form; see [HTTP QUERY implementation and evidence](docs/http-query.md).
- Attach/detach indexed owned documents to owned conversations.
- List private message history and submit a normal chat request with a UUID.
  The POST response streams queued, running, delta, and terminal events.

Unknown and cross-student private resource IDs return 404. Request UUIDs
deduplicate completed submissions and allow failed answers to be retried without
a second user message. One generation runs at a time in this single-process
backend; a full queue returns 429. Document chat searches only the current
student's indexed PDFs attached to that conversation. Retrieved chunks are
rechecked in PostgreSQL before the answer and citations are saved. Documents
with no relevant evidence receive an explicit no-evidence answer. Deletion
immediately removes retrieval eligibility and queues vector/file cleanup.

Local verification:

```bash
cd backend
python -m pytest -q
cd ../frontend
npm test
npm run build
```

## Course requirements

The supplied screenshot requires one server, at least two clients, at least three VMs, and at least five client-server scenarios. Physical computers are listed as a bonus in addition to the VM setup.

The required lab topology is one server VM and two client VMs. The physical server deployment was completed first at the user's request; it does not replace the three-VM demonstration. Record at least five successful client-server scenarios in that VM environment and retain the evidence for assessment. The physical deployment can supplement the VM demonstration for the bonus.

## First-version features

- Student login/logout and separate private workspaces.
- General AI chat and persistent conversation history.
- PDF upload, asynchronous indexing, and processing status.
- Document list, rename, authorized view/download, deletion, and indexing retry.
- Attach/detach indexed documents to individual conversations.
- Document-grounded answers with filename and page references.
- Concurrent client access with isolated student contexts and bounded inference capacity.

Professor roles, shared course documents, OCR, internet search, and autonomous agents are outside the first version.

## Repository safety

Do not commit credentials, Tailscale keys, real student PDFs, conversation data, database dumps, model weights, or server state. Deployment configuration will use environment variables and persistent Docker volumes.

See [M3 chat and server setup](docs/m3-chat.md) for the server-side model pull, Ollama network setup, configuration, and checks after pulling the code.

See [M4 document setup](docs/m4-documents.md) for embedding-model setup, job recovery, reindexing, and acceptance checks.

See [M5 HTTP failure simulation and Wireshark evidence](docs/m5-http-failures.md) for the six response cases, error screens, local capture, and limits of that capture.

See [the isolated MITM lab](docs/mitm-lab.md) for the fourth attacker VM, synthetic
HTTP demonstrations, HTTPS protection check, Wireshark evidence, and recovery.
The runbook distinguishes implemented tooling from completed live acceptance.

See [physical server deployment](docs/physical-server-deployment.md) for the CasaOS stack, existing Ollama connection, access URL, and operational checks.
