# Two-professor demo on the Windows PC

This demo uses the existing VM1 app and Qwen 0.5B model. It does not use the
physical home server. The connection is:

```text
Professor browser -> Tailscale Funnel HTTPS on Windows
                 -> 127.0.0.1:18080 SSH tunnel
                 -> VM1 127.0.0.1:8080 -> Nginx and FastAPI
```

Active URL: <https://desktop-g0tnses.taila6d8df.ts.net/>.

Verified on 20 October 2026 at 17:44 Africa/Lagos: the public route, both
professor logins, secure session cookies, private document/conversation access,
sample PDFs, simultaneous cited replies, saved histories, and logout passed.
The simultaneous requests started together and completed in 37.59 and 56.58
seconds. These are two measured requests, not a general performance guarantee.
The browser also displayed the indexed sample PDF, saved answer, and source cards.

The final `backups/professor-demo/acceptance.json` has `passed: true` and records
the public relay IP `176.58.88.108` with TLS certificate verification enabled.
`public-route.json` separately records public DNS results and both accounts'
public-route checks. The first report is retained as
`acceptance-before-check-correction.json`: its inline-marker assertion was
incorrect for structured answers, which supply citations as separate source
cards. The corrected verifier checks those saved source records.

The dedicated accounts are `professor1@demo.example.test` and
`professor2@demo.example.test`. Their current passwords are stored in the
ignored `backups/professor-demo/.demo-credentials.json` file on Windows and in
`/home/yassine/.professor-demo-credentials.json` inside VM1. Share each
professor's own credentials privately. Do not commit these files.

On 20 October 2026, the professor passwords were changed at the owner's request
using `reset-professor-passwords.py`. Both new logins passed, the previous
passwords returned HTTP 401, and existing professor sessions were invalidated.
The reset is restricted to these two accounts in the local prototype; the
normal account-creation password policy remains unchanged.

`prepare-professor-demo.py`, run with guest sudo, adds the public origin to
VM1's allowed origins, keeps `https://smart-learning.test` and secure cookies,
and creates only those two accounts. It does not reset an existing account's
password. The original environment is backed up in
`/var/lib/smart-learning-professor-demo/env-before-demo` on VM1.

From the project directory in Windows PowerShell, start or inspect the demo:

```powershell
.\scripts\professor_demo.ps1 -Action Start
```

```powershell
.\scripts\professor_demo.ps1 -Action Status
```

VM1 must already be running and this PC must be connected to the prepared
personal Tailscale network. Start enables Windows Tailscale unattended mode so
the connection survives closing its client windows. Keep the PC awake and
connected throughout the demo.
The SSH bridge must be restarted with Start after a reboot or a lost connection.

An initial outage after handoff was traced to Tailscale returning to `NoState`
after its last client window/connection closed; VM1 and the SSH bridge remained
healthy. Enabling unattended mode (`ForceDaemon: true`) restored the network.
This is a Windows Tailscale connection setting; it does not prevent PC sleep.

During recovery, the PC browser loaded again and both accounts passed public
login, download, isolation, and logout through relay `176.58.88.82`. Public
relay tests also passed through `176.58.92.199`, but `176.58.88.108` continued
closing the TLS handshake. Those failures were also seen from a client VM
without Windows Tailscale. This means the initial successful acceptance does
not establish that public access remained reliable after reconnection.
The exact cause of the remaining relay failure is unconfirmed.

Start now checks every current public IPv4 relay using DNS over HTTPS and TLS
certificate verification before reporting public readiness. It saves results
to `backups/professor-demo/public-readiness.json`; `passed: false` means at
least one public route remains unverified or failing. A temporary port-8443
diagnostic was removed; the demo continues to use its original port-443 URL.

After the demo, turn off public access and stop this demo's SSH bridge:

```powershell
.\scripts\professor_demo.ps1 -Action Stop
```

This leaves the VM and its local site running, with accounts and documents
preserved. It does not change the physical deployment or stop the other VMs.

The HTTPS verifier seeds a separate copy of the synthetic two-page networking
PDF and an attached conversation for each professor. It checks login cookies,
cross-account isolation, PDF indexing/downloads, simultaneous real answers,
saved history, page citations, and logout. It retains the sample PDF and demo
conversation for the professors and writes only sanitized acceptance evidence:

```powershell
.\backend\.venv\Scripts\python.exe .\scripts\verify_professor_demo.py `
  --origin https://desktop-g0tnses.taila6d8df.ts.net `
  --credentials-file .\backups\professor-demo\.demo-credentials.json `
  --output .\backups\professor-demo\acceptance.json
```

For an explicit public-route run, query public DNS over HTTPS for the hostname
and pass a current public relay address with `--public-ip`. Do not hard-code a
relay address for normal browser use; public DNS can change these addresses.

Only a report with `passed: true` confirms this verifier passed. A response
through the PC's own Tailscale route does not by itself prove public access;
also test using public DNS/Funnel relay routing or a device outside the tailnet.
The small local model's answer quality remains a demo limitation.
