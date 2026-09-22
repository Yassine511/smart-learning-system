# M4 documents and cited answers

M4 accepts PDFs up to 20 MiB into a private volume. The API records a durable
job; the worker extracts page text, creates overlapping chunks, embeds them
with Ollama, and stores vectors in Qdrant. PostgreSQL holds authoritative
chunk text and ownership. Upload status moves through pending, indexing,
indexed, or failed. Encrypted, corrupt, image-only, oversized, and unusable
PDFs receive explicit error codes. OCR is not included.

The worker claims jobs with database leases. Expired running jobs can be
reclaimed after a restart. Transient errors retry up to WORKER_MAX_ATTEMPTS.
A failed document can be reindexed from the UI or POST /api/documents/{id}/retry.
Reindexing increments its generation, makes old vectors ineligible, and
replaces them with deterministic chunk IDs. Deletion immediately makes the
document ineligible for search, then a durable job removes its vectors, file,
chunks, and record. A failed deletion can be retried with DELETE again.

A conversation searches only its currently attached, indexed PDFs. Qdrant
receives user and document filters; PostgreSQL rechecks every returned chunk
and verifies the current index generation. Cited answers save filename and
one-based page snapshots. An invented citation fails the reply. If search
finds no relevant evidence, the answer says so without switching to general
chat. Source excerpts are removed when the PDF is deleted.

## Server VM setup after pulling

1. Copy .env.example to .env and set database secrets and the exact HTTPS
   origin. Keep UPLOAD_ROOT=/data/uploads so the private_uploads volume is used.
2. Start the lab stack with compose.yaml and compose.lab.yaml.
3. Pull both models in the lab Ollama container:

       docker compose -f compose.yaml -f compose.lab.yaml exec ollama ollama pull gpt-oss:20b
       docker compose -f compose.yaml -f compose.lab.yaml exec ollama ollama pull nomic-embed-text

4. Read the embedding model's 64-character digest from Ollama GET /api/tags
   and set OLLAMA_EMBED_DIGEST in .env. Recreate backend and worker after
   changing .env. The collection name includes the digest; vector dimension
   is derived from a verified embedding. If the model digest later changes,
   update the pin and reindex affected PDFs from the UI.
5. Confirm /api/ready, upload a sanitized known-answer PDF, wait for indexed,
   attach it to one conversation, ask a question, and check filename/page
   citations. Test an unrelated question, two separate students, retries,
   deletion, and worker restart. Save measured results on the three-VM lab.

On the later physical server, reuse the existing Ollama container and connect
it to the application Docker network. Set OLLAMA_BASE_URL to its internal
address, not localhost inside backend or worker. Pull the same two models and
set the embedding digest there. If it differs from the lab digest, reindex
PDFs rather than reusing old Qdrant vectors.

## Local verification without model weights

    cd backend
    .venv/Scripts/python.exe -m pytest -q
    cd ../frontend
    npm run build

The local suite parses a synthetic two-page PDF and mocks Ollama/Qdrant HTTP
responses. Docker's daemon and model weights are not available on this
development machine, so real-model quality and server throughput remain for
the VM acceptance run.
