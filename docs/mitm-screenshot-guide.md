# Reproduce readable MITM screenshots

These instructions reopen the real packet recordings from the completed lab. No VM startup or attack rerun is necessary. Original captures and original screenshots remain unchanged.

## Quick start on the Windows host

Open PowerShell in the project root. Python and the existing Wireshark installation are required.

```powershell
python .\scripts\show_mitm_evidence.py arp
```

The helper opens the correct pcap, applies its display filter, selects the evidence frame, uses 16-point Consolas, keeps five useful columns, disables address-name resolution, and hides the hex pane. It also prints the full capture path. To print the instructions without opening Wireshark:

```powershell
python .\scripts\show_mitm_evidence.py arp --describe
```

1. Maximize Wireshark. For packet views, drag the horizontal divider until the packet list occupies about one-third of the window.
2. Expand only the relevant Ethernet, ARP, HTTP/JSON, or TLS details. Keep the frame number and IP/MAC addresses visible.
3. For a stream view, choose **Analyze > Follow > TCP Stream** (`Ctrl+Alt+Shift+T` on this installation). Maximize the separate stream window too. Use **Entire conversation** and **ASCII**. Red and blue identify conversation directions; they do not identify malicious versus safe traffic.
4. Move the pointer away from the evidence. Press `Alt+Print Screen`, paste into Paint, and save as **PNG**. Alternatively, use the Windows Snipping Tool. Avoid photographing the display or saving JPEG.
5. Insert the original PNG into the slide. Use one claim per slide; use separate client/server slides if a side-by-side pair makes the text too small.

The new captures were taken at the available native desktop size, 1536 x 816. The automation capture API supplies JPEG, so the new files retain that original format and bytes as `.jpg`; they were not upscaled or redrawn. For a lossless PNG, use the manual capture step above. For a higher-resolution capture, maximize on a 1920 x 1080 or larger display and repeat these steps. Increasing an existing image's dimensions does not add evidence detail. You can request a different font, for example `--font-size 18`; if long JSON lines wrap too much, return to 16.

## Exact views and what to capture

Run `python .\scripts\show_mitm_evidence.py NAME` for each name below. Frame numbers apply to the indicated capture role, not to every pcap in that scenario.

| NAME | Capture role | Display filter | Selected frame | Screenshot focus / claim |
|---|---|---|---|---|
| `baseline-map` | baseline / client | `frame.number == 19 || frame.number == 23` | 19 | Ethernet destination is the real server MAC; POST receives HTTP 200. |
| `baseline-stream` | baseline / client | `tcp.stream == 1` | 19 | Follow TCP Stream: submitted `ORIGINAL` note equals `received_note`. |
| `arp` | observe / attacker | `arp && frame.number <= 6` | 5 | Expand ARP. Frames 5 and 6 falsely advertise the server/client IPs at the attacker's MAC. Frame 5 sender IP is .10 and sender MAC is the attacker. |
| `observe-stream` | observe / attacker | `tcp.stream == 2` | 34 | Follow TCP Stream: the attacker capture contains the readable note and unchanged echo. |
| `modify-client` | modify / client | `tcp.stream == 1` | 21 | Follow TCP Stream: client sends `ORIGINAL`, response echoes `MODIFIED by the lab attacker`. Ethernet destination on frame 21 is the attacker. |
| `modify-server` | modify / server | `tcp.stream == 1` | 20 | Follow TCP Stream: the server receives the modified note. Match the run ID with the client view. |
| `inject-server` | inject / server | `tcp.stream == 2` | 33 | Follow TCP Stream: original server HTML has no injected banner. |
| `inject-client` | inject / client | `tcp.stream == 2` | 38 | Follow TCP Stream: response HTML includes the added banner. Also use the saved browser PNG to show the visible result. |
| `drop-client` | drop / client | `tcp.stream == 0 || tcp.stream == 1` | 22 | GET frame 8 succeeds at 12; selected POST at 22 is followed by FIN at 24 without an HTTP response. |
| `drop-server` | drop / server | `http` | 9 | GET frame 9 and response 13 are present, while the selected POST is absent from this recorded server capture. Combine with client capture and endpoint log; absence alone is not sufficient proof. |
| `tls` | observe / attacker | `tcp.port == 443 && tls` | 96 | Handshake packets and encrypted Application Data. Expand TLS on frame 96. The HTTP note is not readable in this connection. |
| `recovery-map` | recovery / client | `frame.number == 17 || frame.number == 21` | 17 | Ethernet destination returns to the real server MAC; direct POST receives HTTP 200. |

Useful commands, one at a time:

```powershell
python .\scripts\show_mitm_evidence.py baseline-stream
python .\scripts\show_mitm_evidence.py observe-stream
python .\scripts\show_mitm_evidence.py modify-client
python .\scripts\show_mitm_evidence.py modify-server
python .\scripts\show_mitm_evidence.py inject-server
python .\scripts\show_mitm_evidence.py inject-client
python .\scripts\show_mitm_evidence.py drop-client
python .\scripts\show_mitm_evidence.py drop-server
python .\scripts\show_mitm_evidence.py tls
python .\scripts\show_mitm_evidence.py recovery-map
```

## Identify the machines correctly

| Machine | Lab IP | Lab MAC |
|---|---|---|
| VM1 server | 192.168.50.10 | 08:00:27:d1:40:34 |
| VM_2 client | 192.168.50.11 | 08:00:27:2e:ca:ac |
| VM4 attacker | 192.168.50.13 | 08:00:27:22:b7:fa |

In the poisoned client capture, the IP destination can still be `.10` while the Ethernet destination is the attacker's MAC. That is the useful comparison with baseline. On the server-facing proxied HTTP connection, the source IP is `.13`.

## Saved files and slide captions

The collection root is `backups/mitm-lab/evidence/20261020t080704-complete/`. New native Wireshark screenshots are in `presentation/`; the earlier screenshots remain in `submission/`. The helper reads `session.json` to resolve each scenario's original run ID. These were independently recorded scenario runs; do not describe them as one continuous recording.

Suggested captions:

- Normal: "The client sends directly to the server MAC; the note is returned unchanged."
- ARP: "Forged replies associate the legitimate peer IPs with the attacker MAC."
- Observe: "Plain HTTP content is readable at the attacker."
- Modify: "The client sent ORIGINAL; the server received MODIFIED, with the same run ID."
- Inject: "The banner is absent from server HTML and present in the client response."
- Drop: "A selected submission never reaches the server; an unrelated GET still succeeds."
- TLS: "The attacker sees TLS records and connection metadata. The separate verified HTTPS probe establishes certificate validation; this packet screenshot alone does not."
- Recovery: "Direct server MAC addressing and successful HTTP return after cleanup."

For injection, use `submission/inject-client-browser.png` as the visible banner evidence. It is an original browser capture, not a reconstruction. Original HTTP/HTTPS acceptance results, endpoint logs, and packet assertions are described in `docs/mitm-lab.md` and the collection's `analysis.json`.

## Manual alternative without the helper

Use `--describe` to obtain the exact capture path, then **File > Open** in Wireshark. Paste the table's display filter, press Enter, and use **Go > Go to Packet** for its selected frame. Under **Edit > Preferences > Appearance > Font and Colors**, select Consolas 16. Under **Appearance > Layout**, use stacked packet list/details and hide packet bytes. Keep No., Source, Destination, Protocol, and Info columns. Disable address-name resolution to show literal MAC addresses. Then follow the capture steps above.

Do not place raw pcaps, SSH keys, or runtime configuration in a public slide bundle. The screenshots show synthetic lab content. Review any additional image before sharing.
