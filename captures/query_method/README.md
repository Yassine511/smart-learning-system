# QUERY method evidence — 2026-10-20

These original, native-resolution PNGs show the deployed local VM lab using
synthetic student accounts and networking PDFs. See
[the implementation and validation record](../../docs/http-query.md).

- `query_request_wireshark.png`: request method, JSON type/length, captured search text.
- `query_response_wireshark.png`: 200, private cache headers, request-frame association.
- `query_405_wireshark.png`: GET receives 405 with `Allow: QUERY`.
- `accept_query_wireshark.png`: search response advertises JSON query support.
- `query_tls_wireshark.png`: client/server TLS Application Data hides HTTP content.
- `query_devtools_app.png`: real Firefox form sends QUERY, receives 200, and carries JSON.
- `query_results_app.png`: successful passage results and PDF page links.

Wireshark 4.6.8 needs the explicitly labeled
[compatibility decoder](../../scripts/wireshark_http_query.lua) for QUERY fields
and associations. The full-stream screenshot is retained privately at
`backups/local-three-vm/query-method/query_stream_wireshark.png`, next to the
original packet captures and private copies of these PNGs. It includes cookies
from a synthetic session that was subsequently logged out. Raw captures also
contain login traffic and must remain private; they are excluded by `.gitignore`.
