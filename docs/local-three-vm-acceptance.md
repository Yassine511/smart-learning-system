# Local three-VM acceptance — 19 October 2026

The isolated prototype passed its final client checks on both VirtualBox client
VMs. All application services and both Ollama models run inside VM1 on this PC.
The physical-server deployment files and GPT-OSS defaults were preserved.

| VM | Role | CPU / RAM | Lab IP |
| --- | --- | --- | --- |
| VM1 / smart-learning-server | Caddy, web, API, worker, PostgreSQL, Qdrant, Ollama | 4 / 4096 MB | 192.168.50.10 |
| VM_2 / smart-learning-client1 | Firefox / Student 1 | 2 / 2048 MB | 192.168.50.11 |
| VM3 / smart-learning-client2 | Firefox / Student 2 | 2 / 2048 MB | 192.168.50.12 |

Each guest has a distinct machine ID. NAT supplies internet access; the second
adapter uses the isolated `labnet` network. Addresses persisted across guest
restarts. Client browsers use `https://smart-learning.test` with the local CA
installed; TLS verification was enabled throughout the successful runs.

## Verified scenarios

| Scenario | Result |
| --- | --- |
| Private sign-in, secure cookies, session invalidation | Passed from both clients; anonymous access returns 401 |
| CSRF and student isolation | Missing CSRF returns 403; another student's conversation, PDF, and attachment requests return 404 |
| Real chat, saved history, request routing | Passed on both clients, including concurrent student requests |
| PDF upload, real embedding/indexing, download, cited chat, deletion | Passed on both clients; downloaded fixture bytes matched the upload; invalid/oversized uploads return 415/413 |
| Requests from both physical client VMs at once | Started 4 ms apart; each received and saved its own reply, in 1.535 s and 3.174 s |
| Application restart and persistence | Saved messages and the indexed PDF survived backend/worker restart and subsequent container recreation |
| Model outage and recovery | Readiness returned 503; chat reported `ollama_unavailable`; Firefox displayed the unavailable notice; restarting Ollama restored readiness and real chat |

Final full runs contain 45 checks on client 1 and 44 on client 2 (PDF polling
changes the count). Both have `full_acceptance: true`. Measured chat request
durations in these runs ranged from 1.792 to 16.533 seconds; these are small
functional-test samples, not a performance benchmark.

## Prototype model settings

Generation uses `qwen2.5:0.5b`; embedding uses digest-pinned `nomic-embed-text`.
The local profile uses one inference thread, 4096 context tokens, and at most
128 generated tokens. `OLLAMA_THINK_MODE=off` omits the GPT-OSS thinking option.

`RAG_CITATION_EXAMPLE=true` supplies a generic formatting demonstration to the
weak model. Actual citations still undergo the existing document ownership,
generation, and attachment checks. This option defaults to false outside the
prototype. Earlier citation failures and diagnostic runs are retained in the
private evidence folder. The model can still omit citations in other prompts or
long conversations; the application then returns its conservative fallback.
The passing cases establish the prototype path, not general answer accuracy.

Final backend validation passed all 30 tests. Two dependency deprecation
warnings remain; no test failed.

## Evidence and use

Private evidence is in the ignored directory
`backups/local-three-vm/evidence/20261019/`. The final client reports are
`smart-learning-client1-citation-fix-20261019.json` and
`smart-learning-client2-citation-fix-20261019.json`. The directory also contains
VM identities, simultaneous-request results, recovery phases, Firefox results
and screenshots, packet captures, HTTP status counts, and TLS peer counts.

`http.pcap` contains readable proxy traffic on server loopback port 8080;
`https.pcap` contains traffic between the server and **both** client IPs on 443.
TShark confirmed 401, 403, 404, 413, 415, and 503 responses in the HTTP capture.
Raw captures can contain demo session cookies and must stay private.

Open the lab URL inside each client VM. The accounts are
`student1@example.test` and `student2@example.test`; their current demo passwords
are in `~/.demo-credentials.json` inside the clients. They are not published in
this document. Synthetic verification conversations and PDFs were cleaned up.

## Observed limits

VirtualBox CPU soft-lockup warnings occurred during startup/rebuild and continued
during the final session. VM_2 froze once and required a forced restart; VM1's
SSH service required a restart while the application remained reachable. Final
functional tests passed afterward, but host/VM stability remains an unresolved
limitation. These results do not establish a stable unattended deployment.

VM1 had approximately 0.8 GiB free on its root filesystem at the final checks.
Allow more disk headroom before adding models or substantial student data.
The pre-prototype snapshots and original deployment hashes remain available;
see [the runbook](local-three-vm.md) for startup, shutdown, and rollback.
