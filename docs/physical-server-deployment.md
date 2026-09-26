# Physical CasaOS server deployment

Deployed 2026-09-23 to the Ubuntu/CasaOS server at Tailscale address
`100.78.63.52`. The application is available to members of the same tailnet at
<https://home-server.coywolf-harmonic.ts.net/>. Tailscale Serve forwards HTTPS
to Nginx on `127.0.0.1:8080`; the app is not published through Funnel.

The source snapshot is at `/home/zero-day/smart-learning-system`. The app was
built on that server from project source; it was not pulled from a container
registry. Docker Compose owns `smart-learning-system-postgres-1`,
`smart-learning-system-qdrant-1`, `smart-learning-system-backend-1`,
`smart-learning-system-worker-1`, and `smart-learning-system-web-1`.
CasaOS continues to manage the pre-existing `big-bear-ollama-nvidia` container.
Do not create a second Ollama container for this deployment.

The physical override in [compose.physical.yaml](../compose.physical.yaml)
attaches the backend and worker to Ollama's existing Docker network. On the
server, `.env` is readable only by the owner and contains the generated database
password. Its deployment-specific values include:

```dotenv
OLLAMA_DOCKER_NETWORK=big-bear-ollama-nvidia_default
OLLAMA_BASE_URL=http://big-bear-ollama-nvidia:11434
OLLAMA_CHAT_MODEL=gpt-oss:20b
OLLAMA_EMBED_MODEL=nomic-embed-text
OLLAMA_EMBED_DIGEST=0a109f422b47e3a30ba2b10eca18548e944e8a23073ee3f3e947efcf3c45e59f
CHAT_GENERATION_SECONDS=210
APP_ORIGIN=https://home-server.coywolf-harmonic.ts.net
ALLOWED_ORIGINS=https://home-server.coywolf-harmonic.ts.net
SESSION_COOKIE_SECURE=true
```

The embedding digest pins the vectors to the installed model version. If the
embedding model changes, reindex affected PDFs. Keep the real database password,
user credentials, and session material out of Git.

From the server's project directory, inspect and update the stack with:

```bash
sudo docker compose -f compose.yaml -f compose.physical.yaml config --quiet
sudo docker compose -f compose.yaml -f compose.physical.yaml ps
sudo docker compose -f compose.yaml -f compose.physical.yaml up -d --build
curl -f http://127.0.0.1:8080/api/health
curl -f http://127.0.0.1:8080/api/ready
sudo tailscale serve status
```

When replacing source, transfer only reviewed project files, then rebuild with
the two Compose files. Do not overwrite the server's `.env` or delete the named
volumes: they contain accounts, conversations, vectors, and uploaded PDFs.

The first student account was created for `yassoune2006@gmail.com`. Its password
was entered through an interactive prompt and is not recorded here. New student
accounts can be created the same way:

```bash
sudo docker compose -f compose.yaml -f compose.physical.yaml exec backend \
  python -m app.cli create-user --email student@example.test --name "Student"
```

## Checks performed

- Docker Compose configuration validated; backend, PostgreSQL, and Qdrant were
  healthy, while web and worker were running.
- HTTPS certificate validation, `/api/health`, and `/api/ready` passed from a
  tailnet client.
- Student sign-in, current-user lookup, conversation create/list/delete, and
  sign-out passed through the HTTPS endpoint.
- One real `gpt-oss:20b` response completed and was saved in chat history.
- A synthetic two-page PDF uploaded with HTTP 202, reached `indexed`, and was
  deleted; its lookup then returned HTTP 404. The temporary conversation was
  also deleted.
- Recreating the backend while web stayed up still returned HTTP 200 through
  Nginx, verifying Docker DNS re-resolution.

The local repeatable smoke test is [smoke_tailnet.py](../scripts/smoke_tailnet.py).
It prompts for the student password and supports `--chat` and `--pdf`. The PDF
fixture is synthetic and the test deletes its uploaded copy. The three-VM
assessment with two client VMs, five scenarios, and network measurements
remains separate.
