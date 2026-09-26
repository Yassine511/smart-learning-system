# Session 5: HTTP failures and Wireshark

The existing client and server already support sign-in, private conversations,
streamed chat, PDF upload, indexing, retrieval, and readiness. This session adds
consistent failure panels to the React client. Panels give the HTTP code when
there is one, a plain-language explanation, and a useful next step. They cover
sign-in/session failures, CSRF blocks, unavailable private items, upload limits
and formats, request conflicts, invalid input, busy chat, service outages, and
network loss. Failed chat messages keep their existing Retry action. Failed PDF
jobs display an explanation next to the document and retain Reindex.

## Six HTTP status simulations

Run from the repository root:

```powershell
& backend/.venv/Scripts/python.exe scripts/simulate_http_failures.py --capture evidence/http-six-cases.pcapng
```

The script starts the real FastAPI app on a random local loopback port with a
disposable in-memory SQLite database and synthetic student credentials. It
signs in before capture, sends the following six requests, and asserts each
response code. It does not use the deployed PostgreSQL, Qdrant, or Ollama.
Only this temporary TCP port is captured by Wireshark dumpcap. The synthetic
session cookie in the capture is valid only until the test server stops.

| Case | Real application request | Expected and observed |
| --- | --- | --- |
| No session | `GET /api/me` | 401 Unauthorized |
| Missing CSRF/origin | `POST /api/conversations` | 403 Forbidden |
| Unknown private conversation | `GET /api/conversations/{id}` | 404 Not Found |
| PDF over test limit | `POST /api/documents` | 413 Content Too Large |
| Non-PDF upload | `POST /api/documents` | 415 Unsupported Media Type |
| Dependency not ready | `GET /api/ready` | 503 Service Unavailable |

The test temporarily sets the PDF limit to 1 KiB to exercise the same 413 code
path without storing a large file. Normal deployment still uses the 20 MiB
limit. The 503 test reports an unavailable Ollama dependency from the isolated
test app; it does not change production readiness behavior.

## Wireshark capture analysis, 2026-09-23

Artifacts: [`../evidence/http-six-cases.pcapng`](../evidence/http-six-cases.pcapng)
and [`../evidence/http-six-cases.json`](../evidence/http-six-cases.json).
Wireshark captured 69 packets on the Npcap loopback adapter. The capture
contains six HTTP request/response pairs across two TCP streams. The server
responded with the expected codes:

| HTTP response | TCP stream | Wireshark response time |
| --- | ---: | ---: |
| 401 | 0 | 6.0 ms |
| 403 | 1 | 23.0 ms |
| 404 | 1 | 15.4 ms |
| 413 | 1 | 21.9 ms |
| 415 | 1 | 8.9 ms |
| 503 | 1 | 4.1 ms |

The first request used a separate connection without a session cookie. The
authenticated client reused a second TCP stream for the remaining five
requests. HTTP 413 and 415 were application responses to upload checks, while
HTTP 503 came from the readiness endpoint. These timings measure a local test
server; they are not throughput or latency measurements for the three-VM lab.

To inspect in Wireshark, open the pcapng and apply display filter
`http.request || http.response`. The JSON result records the random port.
If Wireshark does not decode the random port as HTTP automatically, choose
**Analyze → Decode As → HTTP** for that TCP port. Repeat the capture on the
server VM or a client VM during three-VM acceptance to measure the deployed
network path and tailnet HTTPS behavior.

## Error screen visual check

The login screen was checked in a real browser at desktop and 390 px mobile
widths while the API was unavailable. The HTTP 502 panel remained readable,
showed a next step, and exposed an accessible alert with a Dismiss button.
Screenshots: [desktop](../frontend/output/playwright/error-login-desktop.png)
and [mobile](../frontend/output/playwright/error-login-mobile.png). The same
error component renders the other HTTP codes with their own wording.
