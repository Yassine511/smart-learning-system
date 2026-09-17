# Architecture and implementation plan

## 1. Deployment and networking

Deployment has two stages. First, validate a self-contained lab with one Ubuntu server VM and two separate client VMs. The server VM is the sole application, model, and data host for this stage. Run its own Ollama container alongside the application stack; do not route lab inference to the physical server. Use CPU inference unless GPU access inside the VM is explicitly verified, and benchmark a smaller quantized model if needed.

After lab acceptance, deploy the same application stack to the dedicated physical server. CasaOS remains that server's container management interface, and its existing Ollama container is reused. Keep deployment in a base Docker Compose file with a lab override adding Ollama and a physical-server override connecting to the existing model service. Configure model names, endpoint, hostname, secrets, and storage paths per environment without changing application code.

Install or reuse Tailscale inside all three VMs for the lab, then on the physical server for the later deployment. Each VM needs its own machine identity and outbound network connectivity; do not duplicate enrolled Tailscale state when cloning clients. Use separate tailnet hostnames for lab and physical servers. Use Tailscale Serve for a trusted HTTPS entry point on the server's assigned tailnet hostname. Forward it to an Nginx container published only on server loopback, initially `127.0.0.1:8080`. Verify that port is free before deployment. Nginx serves the built React application and proxies `/api/` to FastAPI without stripping the API prefix. Disable proxy buffering for streamed chat responses.

Client browsers use one HTTPS origin for the frontend and API. Tailnet access grants permit the student client identities to reach only the application HTTPS entry point. The server still authenticates individual application users; a Tailscale identity is not a student account.

PostgreSQL, Qdrant, the worker, and FastAPI use internal Docker networking without published client ports. In the lab, the backend and worker reach the Compose-managed Ollama service at `http://ollama:11434`. On the physical target, connect them to the existing Ollama container through a named Docker network and a stable internal alias. Inspect the existing CasaOS network and container configuration before choosing that alias. Use `OLLAMA_BASE_URL` for the internal endpoint; `localhost:11434` inside the backend container is not the Ollama container. Record any network attachment in the deployment runbook so it survives container recreation. Do not expose Ollama directly to students.

Review the existing 11434 host binding before deployment and restrict unwanted access without disrupting other authorized server uses. Changes to the existing server are implementation work, not already completed actions.

## 2. Components and initial defaults

| Component | Planned choice | Responsibility |
| --- | --- | --- |
| Web UI | React, TypeScript, Vite | Student workspace, chat, documents, source display |
| Web entry point | Tailscale Serve + Nginx | HTTPS and same-origin frontend/API routing |
| API | FastAPI, SQLAlchemy, Alembic | Authentication, ownership, chat orchestration, retrieval |
| Application store | PostgreSQL | Authoritative user data, chunk text, source metadata, jobs |
| Vector index | Qdrant | Derived embeddings with owner/document/chunk filters |
| Model service | Lab Ollama container; existing Ollama on physical target | Local text generation and embeddings |
| Document worker | Separate Python container from backend image | PDF extraction, chunking, embedding, indexing |
| Files | Private persistent volume | Uploaded PDF bytes under server-generated names |

Physical-server model candidate: `qwen3:8b` Q4_K_M, with thinking disabled and a 4,096-token context budget for the first benchmark. Select the lab's quantized model after verifying its host and VM resource allocation; do not assume the physical server's GPU is available to the VM. Both environments use the same Ollama integration with a configurable model name. These are benchmark candidates, not measured performance claims. Compare answer quality, available memory, CPU/GPU residency, time to first token, total duration, and two-client queue behavior before locking each environment's model. Repeat the known-answer tests when changing models.

Use `nomic-embed-text` as the initial embedding candidate through Ollama. Pin resolved model versions/digests at implementation time. Apply the model's document/query input conventions consistently, derive the Qdrant vector dimension from a verified embedding, and reindex when the embedding model changes. Keep embedding batches small so uploads do not overwhelm chat inference.

Start with one backend process and one active chat generation at a time, allowing other clients to authenticate, upload, read history, and queue requests concurrently. A bounded FIFO queue makes simultaneous access demonstrable without promising parallel GPU generation. Use request IDs, explicit queued/running/failed status, timeouts, and a busy response when capacity is exhausted. Benchmark worker embedding contention with active chat before increasing concurrency.

## 3. Data ownership and user flows

PostgreSQL entities: users, sessions, conversations, messages, documents, conversation-document attachments, chunks, indexing jobs, and message-source references. Every private resource has an ownership path to a user. Qdrant payloads carry user ID, document ID, chunk ID, and index generation. PostgreSQL remains authoritative for chunk text and access checks.

Use administrator-created student accounts for the first demo, password hashes with Argon2id, and random opaque sessions stored as token hashes. Deliver the session token in a Secure, HttpOnly, SameSite cookie. Check CSRF tokens and origins on authenticated state-changing requests. Return 404 for resources owned by another student. Avoid recording passwords, session tokens, or PDF content in request logs.

**Normal chat:** authenticate, authorize conversation, snapshot its attachment scope, save the user message, load bounded history, queue inference, stream the answer, then persist the completed assistant message. With no attachments, skip vector retrieval. Retain failed message status and allow an explicit retry without duplicating the user message. A client-provided request UUID deduplicates retried submissions within its conversation.

**PDF upload:** accept PDF files up to an initial 20 MiB limit, validate actual content, store under a generated private path, and create a durable indexing job. Return 202 with document ID and processing state. The worker extracts page-level text, splits it into overlapping chunks, stores authoritative text/metadata in PostgreSQL, and writes vectors to Qdrant. Mark the document indexed only after the whole generation succeeds. Image-only, encrypted, corrupt, or unusable PDFs receive a clear failed status; OCR is deferred.

Use PostgreSQL-backed job claiming with leases and bounded retries rather than adding Redis for this project. Deterministic chunk IDs and index generations make worker retries idempotent. Failed or incomplete generations remain excluded from retrieval. Restarting the worker must recover abandoned jobs without duplicating active work.

**Document chat:** resolve the current user's indexed attachments for that conversation, embed the question, search Qdrant with both user and allowed document filters, then reload and authorize the returned chunks through PostgreSQL. Assemble only bounded passages and history within the model context budget. Treat PDF text as evidence, not executable instructions. If attached documents yield no usable evidence, return that result rather than silently switching to general chat. Tune chunk length and retrieval thresholds against a small known-answer PDF set.

Responses identify sources using server-issued chunk IDs mapped to real filenames and one-based PDF pages. Reject invented source IDs. Keep source validation distinct from the harder question of whether an answer is factually supported; inspect that with known-answer tests. Snapshot source metadata with the message so rename/delete actions do not silently misattribute past answers.

**Document management:** authorize list, rename, download/view, attach/detach, and delete operations. Attach only owned, indexed files. Deletion first makes the document ineligible for retrieval, then idempotently removes vectors, chunks, attachments, and stored bytes. Recheck eligibility before returning retrieved source content so a stale vector result cannot bypass deletion or ownership checks. Keep historical source labels with a deleted/unavailable marker after deletion.

## 4. API outline

All application routes use the `/api` prefix and cookie authentication except login and a minimal health endpoint.

| Routes | Purpose |
| --- | --- |
| `POST /api/login`, `POST /api/logout`, `GET /api/me` | Sessions and current student |
| `GET/POST /api/conversations` | List/create private chats |
| `PATCH/DELETE /api/conversations/{id}` | Rename/delete an owned chat |
| `GET /api/conversations/{id}/messages` | Read history and request states |
| `POST /api/conversations/{id}/messages` | Submit question with request UUID; stream status, answer deltas, sources, and completion using fetch |
| `GET/POST /api/documents` | List documents or upload multipart PDF |
| `GET/PATCH/DELETE /api/documents/{id}` | Status, rename, delete |
| `GET /api/documents/{id}/file` | Authorized PDF view/download |
| `POST /api/documents/{id}/retry` | Retry failed indexing |
| `PUT/DELETE /api/conversations/{id}/documents/{document_id}` | Idempotent attach/detach |

Clients poll document status during indexing. Streaming is optional at the model transport level but planned in the user interface; the server must send a terminal success/error event and persist a non-complete state if generation is interrupted. Set Nginx and API timeouts coherently with the bounded generation deadline.

## 5. Implementation phases and acceptance

1. **Three-VM lab and network skeleton:** verify the VM host's available resources, allocate one Ubuntu server VM and two browser-capable client VMs, and record their distinct identities. Inspect guest OS, Docker, disk, CPU/GPU access, model inventory, and Tailscale access rules. Run all server services, including Ollama, inside the server VM. Deploy a minimal HTTPS page/API and reach it from both client VMs. Verify tailnet authorization blocks an unauthorized client.
2. **Accounts and persistence:** build sessions, authorization, conversations, and migrations. Verify two students cannot read or mutate each other's chats, PDFs, or attachments, including guessed IDs.
3. **Normal chat:** integrate Ollama and the bounded queue, stream answers, retain history, and handle timeouts/retries. Measure one-client and two-client latency and routing correctness; do not set a performance claim before measurement.
4. **Documents and RAG:** implement durable indexing, sources, attachments, and cleanup. Test known-answer PDFs, unrelated questions, image-only PDFs, corrupt/oversized input, reindex retries, document deletion, and cross-student retrieval attempts.
5. **Three-VM networking demonstration:** with all three VMs running, demonstrate login, general chat, PDF upload, document-grounded chat, history retrieval, and simultaneous two-student requests. Show VM identities/IPs, HTTPS requests/status codes, server request IDs, file transfer, and response routing. Capture traffic on the Tailscale interface to show inner connections; HTTPS payloads remain encrypted. Compare with the guest network adapter's tunnel traffic and explain direct versus relayed connectivity if observed. Save evidence of at least five scenarios before proceeding to physical deployment.
6. **Recovery and delivery:** restart application/worker containers and confirm persistent accounts, documents, and conversations; simulate unavailable Ollama/Qdrant and worker interruption. Provide setup instructions, pinned dependencies/images, `.env.example`, sanitized demo data, packet-capture instructions, and measured results. Back up PostgreSQL and private files; Qdrant is rebuildable from chunks and the pinned embedding model.

7. **Physical-server deployment:** only after lab acceptance, inspect the dedicated server and existing Ollama/CasaOS setup, deploy the same tested application revision with separate secrets and volumes, and change the configured model endpoint and HTTPS hostname. Start with fresh demo accounts and upload the same sanitized PDFs; transferring personal lab data is not required. Use the same embedding model or rebuild the index if it changes. Repeat the scenario, isolation, persistence, and failure checks, then benchmark GPU-backed inference. Retain the working lab as the assessment environment. Add physical browser clients for the bonus demonstration.

No application code or server deployment is included in the planning documentation.

## 6. Assessment and deployment gate

The agreed lab topology is one server VM and two client VMs, which matches the supplied server/client and VM-count requirements. The earlier proposed substitution of a physical server for the server VM is superseded. Full project acceptance still requires evidence of at least five working scenarios and the isolation/recovery checks above. The dedicated physical server is a subsequent deployment, not a replacement for the required VM demonstration.

## References

- User-provided architecture PDF and course-requirements screenshot are design inputs; the agreed sequence is three-VM validation first, then dedicated-server deployment, with Tailscale access in both stages.
- [Tailscale Serve](https://tailscale.com/docs/features/tailscale-serve): HTTPS service access within a tailnet.
- [Ollama Docker documentation](https://docs.ollama.com/docker): NVIDIA GPU access requires the appropriate container runtime configuration.
- [Qwen3 8B model entry](https://ollama.com/library/qwen3:8b): initial quantized generation candidate.
- [Nomic embedding model entry](https://ollama.com/library/nomic-embed-text): initial local embedding candidate.
