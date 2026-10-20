# HTTP QUERY: private semantic PDF search

`QUERY /api/documents/search` searches the signed-in student's indexed PDFs and
returns verified passages with page references. It works without a conversation
or attachments and does not generate an AI answer, save messages, or change PDFs.
The **Search my PDFs** form uses this method and links each passage to its PDF page.

## Protocol and implementation

The former `httpbis-safe-method-w-body` draft became
[RFC 10008, The HTTP QUERY Method](https://www.rfc-editor.org/rfc/rfc10008.html)
in June 2026. QUERY expresses a safe, idempotent operation with request content.
Repeating a search has no intended resource changes; results may change if PDFs
are renamed, reindexed, or deleted between requests.

| Method | Safe / idempotent semantics | Search input |
| --- | --- | --- |
| GET | Yes / yes | Usually URL parameters; GET content has no generally defined semantics |
| QUERY | Yes / yes | Request content with an explicit Content-Type |
| POST | Neither guaranteed by the method | Request content; generic clients cannot assume safe automatic retries |

QUERY avoids putting private or long search text in the URL, where it is more
likely to appear in access logs, history, or bookmarks and encounter size limits.
Bodies can still be logged or inspected; HTTPS protects them in transit.

The API accepts `Content-Type: application/json` and a body:

```json
{"query":"How does TCP deliver data reliably?","document_ids":null,"limit":5}
```

- `query`: stripped text, 1–2000 characters.
- `document_ids`: omitted/null searches all owned indexed PDFs; an explicit empty
  list searches none. At most 50 UUIDs. Foreign and nonexistent IDs yield no hits.
- `limit`: 1 through `RETRIEVAL_LIMIT` (default 8, configuration maximum 30).
  Omitted limit defaults to the smaller of 5 and the configured maximum.
  The UI requests 5, so its deployment should retain `RETRIEVAL_LIMIT >= 5`.

A successful response is a JSON list of `chunk_id`, `document_id`, `marker`,
`filename`, `page_number`, and `quote`. Empty results return `200 []`. Markers are
numbered S1 onward in vector ranking order after verification.

Embedding uses the configured model's query prefix. Only owned indexed documents
matching its digest and collection qualify. PostgreSQL rechecks chunk ownership,
current generation, document status, digest, and collection after vector lookup;
vector payload ownership/generation/model fields must agree. Chat retrieval uses
the same verification helper and additionally requires a conversation attachment.
Passage text and page numbers come from PostgreSQL, never untrusted vector payload.

Authentication uses the existing Secure, HttpOnly, SameSite=Strict session cookie.
This read-only method needs no CSRF token. Missing authentication returns 401;
invalid fields or JSON return 422; missing Content-Type returns 400; unsupported
media types return 415; embedding/vector failures return
`503 {"detail":"vector_store_unavailable"}`. GET/POST and the other ordinary
methods on the reserved `/search` path return `405` and `Allow: QUERY`.

Search responses carry `Cache-Control: no-store`, `Vary: Cookie`, and
`Accept-Query: application/json`. QUERY is cacheable in principle using content
as part of the cache key, but this private endpoint disables storage. RFC 10008
section 3 scopes Accept-Query to the **same path**, so it is sent on `/search`
(including 405/415 discovery responses). `GET /api/documents` instead advertises
`Link: </api/documents/search>; rel="search"; type="application/json"`.
Adding Accept-Query to the list route would incorrectly advertise QUERY there.

The same-origin browser request needs no CORS preflight. Cross-origin QUERY
requires preflight and an explicit CORS policy; this application does not enable
cross-origin search. Proxy configuration contains no method restrictions, but
forwarding must still be demonstrated on the actual deployed versions.

## Manual requests

Log in to the lab with its existing login workflow and retain a **private** curl
cookie jar as `cookies.txt`. Run these on a client with the trusted local CA:

```bash
curl -i -X QUERY https://smart-learning.test/api/documents/search \
  -H 'Content-Type: application/json' -b cookies.txt \
  -d '{"query":"TCP acknowledgements","limit":5}'
```

```bash
curl -i -X GET https://smart-learning.test/api/documents/search -b cookies.txt
```

The first returns 200 with passages from indexed PDFs; the second returns 405
with `Allow: QUERY` and `Accept-Query: application/json`.
Do not commit cookie jars, passwords, or raw captures containing active sessions.

## Source and live verification

```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest -q
cd ../frontend
npm test
npm run build
```

For the separate VM prototype, deploy the reviewed source with the existing
[manifest-verified update workflow](local-three-vm.md#updating-vm1-to-a-reviewed-revision).
Its `stack.sh` includes all three Compose files and preserves local configuration
and data. Use the deployment's established Compose command elsewhere.

`scripts/verify_http_query.py` performs actual HTTPS login, synthetic PDF upload
and indexing, search, repeat search, cross-student isolation, GET/POST 405, header
checks, and cleanup. Run in the named VM with its existing private demo accounts:

```bash
/opt/smart-learning-local-client/.venv/bin/python scripts/verify_http_query.py --role client1 \
  --pdf backend/tests/fixtures/networking_notes.pdf \
  --output /tmp/query-client1.json --keep-document
```

Use `--role client2` in VM3. The existing client venv supplies httpx; bare system
Python does not. On VM1, use an environment containing httpx, `--role server --http-loopback`, and
`--credentials-file /opt/smart-learning-local/.demo-credentials.json` under sudo
to check both nginx's `127.0.0.1:8080` and Caddy's HTTPS path. Reports contain
checks and synthetic document metadata, with no passwords or cookies.
`--keep-document` retains the synthetic PDF for browser screenshots; delete it
through the UI after evidence collection. Without that flag the verifier deletes it.

## Required packet and browser evidence

Start the existing helper from the repository root in a **separate** PowerShell
window after the deployment is ready:

```powershell
pwsh -File scripts/manual_wireshark.ps1 -OutputDirectory "$PWD/backups/local-three-vm/query-method"
```

It opens readable HTTP on VM1 loopback and encrypted client traffic, and writes
`http-live.pcapng` / `https-live.pcapng` plus the server originals under that
ignored directory. Before starting, it preserves the prior server capture.
Use synthetic accounts/PDFs. These captures include authentication cookies on the
loopback hop; retain raw files privately next to a private copy of the screenshots.
Terminate capture before logging out so sessions can be revoked after collection.

Decode port 8080 as HTTP if necessary. Maximize Wireshark; use 16 pt Consolas,
disable name resolution, hide packet bytes, and retain No., Time, Source,
Destination, Protocol, Info. Expand only relevant layers, keep IPs/ports and frame
numbers visible, and move the pointer away. Save native-resolution PNGs, inspect
at 100%, and avoid rescaling. See [screenshot guidance](mitm-screenshot-guide.md).

The evidence inventory below links the completed, reviewed PNGs. Screenshots
with cookies/passwords collapsed are in `captures/query_method/`; the complete
stream view is private because it displays a subsequently revoked session cookie.
Original-resolution copies of all screenshots sit next to the private captures
in `backups/local-three-vm/query-method/`.

| Screenshot | Filter / view | Caption: claim to demonstrate |
| --- | --- | --- |
| [query_request_wireshark.png](../captures/query_method/query_request_wireshark.png) | Frame 109; `http_query.method == "QUERY"` with compatibility decoder | QUERY request line, method, JSON Content-Type/Length, and expanded body reach VM1. Headers are in frame 108. |
| [query_stream_wireshark.png — private](../backups/local-three-vm/query-method/query_stream_wireshark.png) | `tcp.stream == 3`, Follow TCP Stream, ASCII | Original captured bytes show QUERY/body, 200, no-store, filename, page and quote. The displayed synthetic session cookie was revoked. |
| [query_response_wireshark.png](../captures/query_method/query_response_wireshark.png) | Frame 119 | Original 200/cache headers plus the decoder's link to QUERY frame 108 and its elapsed time. |
| [query_405_wireshark.png](../captures/query_method/query_405_wireshark.png) | Frames 136–137 | GET on the search path receives 405 and Allow: QUERY; the compatibility decoder labels the association. |
| [accept_query_wireshark.png](../captures/query_method/accept_query_wireshark.png) | Frame 119 | The search response advertises Accept-Query: application/json. |
| [query_tls_wireshark.png](../captures/query_method/query_tls_wireshark.png) | `tcp.port == 443 && tls`, search time window | Traffic between client .11 and server .10 is TLS Application Data; HTTP method/body are encrypted. |
| [query_devtools_app.png](../captures/query_method/query_devtools_app.png) | Firefox Network, Method and Request tabs | The real form sends QUERY over HTTPS, receives 200, and carries the shown JSON body. |

[query_results_app.png](../captures/query_method/query_results_app.png) additionally
shows the returned passages and PDF page links in the actual application.

For response filters replace X with the actual request frame. Some Wireshark
versions lack a dedicated Accept-Query field; use the raw-text filter above.
Some versions may not recognize QUERY automatically: inspect Decode As and
Follow TCP Stream before claiming that traffic is absent. The full-stream view
can expose Cookie headers; keep that original private and identify any published
redaction explicitly. TLS evidence alone does not identify a particular QUERY;
correlate timestamps and client/server addresses with the plaintext capture.

The recorded API, browser, and packet evidence below is specific to this QUERY
feature. It does not rerun full course acceptance or establish unattended VM stability.

## Validation record (2026-10-20)

- Final complete backend regression suite: **83 passed**.
- Frontend: all 16 tests passed; production TypeScript/Vite build passed.
- Direct h11 and httptools parser checks accepted a QUERY request with JSON content.
- VM1: the deployed source matched its packaged SHA-256 manifest; dependency
  readiness passed. This deployment contains the feature as uncommitted changes
  on base commit `55749585b702892dd29fdacabd16d2a8b792bad3`.
- Both real client VMs (`192.168.50.11` and `.12`) passed HTTPS search through
  Caddy and nginx, real PDF indexing/embeddings, repeat searches, cross-student
  isolation, unauthenticated 401, GET/POST 405 with Allow/Accept-Query, and logout.
  Each student received two passages, on pages 1 and 2 of the synthetic PDF.
- Direct authenticated nginx loopback QUERY on VM1 returned 200 with two hits,
  `Cache-Control: no-store`, `Vary: Cookie`, and `Accept-Query: application/json`.
- Private client/server JSON reports and packet captures are retained in
  `backups/local-three-vm/query-method/`. Synthetic PDFs were retained for UI proof.
- Visible Firefox 156.0.1: the corrected verifier passed with trusted certificates
  (`accept_insecure_certs: false`), QUERY/200, and four PDF page links. Network tools
  independently show QUERY/200 over HTTP/2 and its JSON body. The earlier browser
  attempts failed because the verifier wrapper lost fetch's Window binding;
  the helper was corrected without changing application code.
- All seven requested evidence views were collected at native resolution and
  visually reviewed. Six are public-safe PNGs in `captures/query_method/`; the
  full-stream PNG and raw captures remain private. An extra app-results PNG is included.
- PNG signatures and native dimensions were checked for all eight images:
  Wireshark views are 1920×1020; DevTools is 1280×800; the app viewport is
  1280×663. SHA-256 hashes and live-report checks are recorded in the private
  `backups/local-three-vm/query-method/evidence-manifest.json`.
- Test browser logout returned 204 and its session was revoked. The synthetic
  PDFs remain available for manual demonstration; no existing PDFs were deleted.

### Wireshark 4.6.8 compatibility

The installed Wireshark 4.6.8 labels QUERY as HTTP Continuation, so its native
`http.request.method == "QUERY"` filter misses these requests. The original TCP
payload nevertheless contains `QUERY /api/documents/search HTTP/1.1` and the
following captured segment contains its JSON body. Native 200 responses are
present, but this version does not associate them with the unrecognized method.

The session-scoped [Lua compatibility decoder](../scripts/wireshark_http_query.lua)
reads those original bytes, exposes `http_query.method` / `http_query.request_in`,
and decodes the captured JSON with Wireshark's JSON dissector. It labels its tree
as a compatibility decoder; it changes no capture bytes or application behavior.
Use `-X lua_script:scripts/wireshark_http_query.lua` when opening the capture and
filter on `http_query.method == "QUERY"`. It targets sequential lab traffic on
port 8080; use Follow TCP Stream for partial or out-of-order captures.

In the first private HTTP snapshot, request headers are in frame 108, the JSON
body is in 109, and the 200 response is in 119. The decoder links the body to its
header frame and the response to request frame 108. Screenshots using this
decoder must retain its visible label and are not evidence of native QUERY
support in Wireshark 4.6.8. The original stream remains the wire-level reference.

The test suite uses real PDF parsing and an isolated database with fake vectors
and embeddings. It proves the application contract and ownership checks; it does
not substitute for the live model/proxy and networking evidence above.
