# MITM lab extension

This lab adds an independent Ubuntu attacker VM and a synthetic HTTP teaching
service to the existing Smart Learning network. It demonstrates ARP interception,
eavesdropping, request modification, response injection, and selective dropping.
The existing application's HTTPS path remains separate and verifies certificates.

## Implementation and measured status — 20 October 2026

The six scenarios passed real endpoint, Firefox, and packet acceptance checks.
The evidence collection is `20261020t080704-complete`, under
`backups/mitm-lab/evidence/`. It combines independent measured runs; it is not a
claim that one uninterrupted automatic sequence completed. Original captures,
run IDs, timestamps, and source-session provenance are retained.

- VM1, VM_2, and the independent VM4 attacker were verified at `.10`, `.11`,
  and `.13`, with distinct MAC addresses on the internal `labnet`.
- The user authorized a reduced profile: VM1 **2048 MB**, VM_2 **1536 MB**,
  and attacker **1536 MB**. Startup requires **6144 MB available**, including
  the 1024 MB host reserve. Original RAM allocations are saved privately for
  rollback. VM3 stays off. This profile passed the teaching lab; model inference
  and multi-user application load were not tested.
- **29 focused tests pass**, alongside Python, Bash, and PowerShell syntax
  checks. Tests cover target restrictions, transformations, selective dropping,
  recovery after interrupted setup, retained state after recovery failure,
  archive timestamps, and bounded retries of idempotent SSH commands.
- Four real attack cycles stopped through SIGTERM and recovered. The recovery
  probe used direct peer MAC addressing, unchanged HTTP, and verified HTTPS.
  VM4 was shut down through the guest console with `sudo poweroff` after
  VirtualBox's graceful shutdown button did not complete. Its powered-off state
  was verified. Repeated Stop then succeeded. At **08:13:23 UTC**, a fresh check
  with VM4 off confirmed HTTP, the real VM1 neighbour MAC, and certificate-verified
  application HTTPS. The measured result is retained as `post-cleanup.json`.
- Original application APIs, student accounts, physical deployment files, and
  VM snapshots are preserved. VM4 is an independent full clone with regenerated
  identity and SSH keys, separate MACs, and no copied student/browser sessions.
- This Windows/VirtualBox run experienced intermittent SSH stalls. One server
  capture stop/export was retried successfully; its original timeout remains
  recorded in the private session. A later attacker SSH stall interrupted the
  sequence between phases. The remaining phases ran separately after recovery.
  The hypervisor log also recorded guest heartbeat/timing stalls. Successful
  independent scenarios do not establish unattended-run reliability.

Earlier failures remain available under ignored backups. The initial controller
REDIRECT rule needed its own TCP selector. The setup archive also gave scripts
timestamp zero, which the installed mitmproxy 8 watcher skips; packaging now uses
a positive timestamp. The installed attacker repair and addon execution were
verified by controller checksum, proxy logs, and changed traffic in the passing
runs. Snap Firefox probes use the ordinary-user SSH login and a disposable
profile. No TLS verification or Snap protection was disabled.

### Measured evidence

Each frame number below belongs to the named role's own `network.pcap` in that
phase. Stream numbers are local to each capture. Evidence timestamps are UTC;
Africa/Lagos local time is UTC+1. `analysis.json` contains the full assertions,
frame references, and stream lists. `provenance.json` identifies the source runs.

| Scenario | Measured evidence | Wireshark display filter |
| --- | --- | --- |
| Normal HTTP | Client frames **19/23**, stream **1**: original POST and unchanged echo; destination MAC `08:00:27:d1:40:34` | `tcp.stream == 1` |
| ARP interception | Observe attacker frames **5/6** advertise `.10` and `.11` at attacker MAC `08:00:27:22:b7:fa`; client neighbour table points `.10` to VM4 | `arp.opcode == 2` |
| Eavesdropping | Observe attacker frame **34**, stream **2**, contains the original unique note; server frame **20**, stream **1**, receives the same note | `tcp.stream == 2` on attacker |
| Modification | Modify client frame **21**, stream **1**, sends the original; server frame **20**, stream **1**, contains `MODIFIED by the lab attacker`; VM1 log agrees | `http.request.method == "POST"` |
| Injection | Inject server response frame **33** lacks the banner; client response frame **38** contains it, both in stream **2** of their respective captures; Firefox displays the yellow banner | `tcp.stream == 2` |
| Selective drop | Drop client POST frame **22**, stream **1**, has no HTTP response and is followed by connection closure. Server has **no POST** for the run. Unrelated GET succeeds at client frames **8/12**, server **9/13**, stream **0** | `tcp.stream == 0 || tcp.stream == 1` |
| HTTPS protection | Observe attacker frames **85/90** show the TLS handshake; **96/100/101** are encrypted application records. All four attack probes passed normal certificate verification | `tcp.port == 443 && tls` |
| Recovery | Client frames **17/21**, stream **1**, show original note and echo with the legitimate peer MAC; HTTPS verifies again | `tcp.stream == 1` |

Source runs:

| Phase | Unique run ID |
| --- | --- |
| Baseline | `baseline-20261020t074701-03f79c` |
| Observe | `observe-20261020t074701-03f79c` |
| Modify | `modify-20261020t075204-454af2` |
| Inject | `inject-20261020t075344-448a45` |
| Drop | `drop-20261020t080215-9d0d7a` |
| Recovery | `recovery-20261020t080329-b71ea5` |

## Architecture

Editable diagram: [mitm-lab.mmd](mitm-lab.mmd).

| VM | Role | Lab address | RAM | Host SSH |
| --- | --- | --- | --- | --- |
| VM1 | Existing application plus separate teaching service | 192.168.50.10 | 2048 MB | 127.0.0.1:2221 |
| VM_2 | Target client, installed Firefox | 192.168.50.11 | 1536 MB | 127.0.0.1:2222 |
| VM3 | Staged comparison client | 192.168.50.12 | 2048 MB | 127.0.0.1:2223 |
| VM4_Attacker | ARP controller and transparent HTTP proxy | 192.168.50.13 | 1536 MB | 127.0.0.1:2224 |

Adapter 2 must be VirtualBox **Internal Network**, named `labnet`. Do not use
bridged networking. NAT on adapter 1 is for package installation and loopback
SSH management. The attack controller resolves only `.10` and `.11`, verifies
distinct MAC identities, and never scans a subnet. VM3 is not poisoned.

The main demonstration uses VM1, VM_2, and VM4. Leave VM3 powered off to conserve
memory. Startup requires newly allocated VM RAM plus 1024 MB host headroom
(6144 MB available when all three demonstration VMs are off), and at least
2 GiB free on each volume containing VM disks or evidence. Creating the full
clone requires 30 GiB free on its destination volume.
If the guard fails, close unused host applications and rerun; it does not kill
applications or reduce VM resources automatically. Keep the laptop lid open.

This reduced profile is for the MITM demonstration; application model inference
and multi-user load require separate resource validation. To restore the original
RAM after shutting down the affected VMs, run `VBoxManage modifyvm VM1 --memory
4096` and `VBoxManage modifyvm VM_2 --memory 2048`. The attacker remains 1536 MB.

## One-time setup

Run host commands from the repository root in PowerShell. `Prepare` is repeatable
while the VMs are powered off; it preserves the first recorded DVD attachments
and existing snapshots. It refuses an unrelated VM with the same attacker name.

```powershell
.\scripts\mitm_lab.ps1 -Action Prepare
.\scripts\mitm_lab.ps1 -Action Start
```

The first Start opens only the attacker in quarantine for interactive setup.
Use its text console (`Ctrl+Alt+F3`), then log in with the cloned guest's existing
OS account. The installer stops the desktop, so a desktop terminal session can
be interrupted. Inside **VM4_Attacker**:

```bash
sudo mkdir -p /mnt/mitm-lab
sudo mount -o ro /dev/sr0 /mnt/mitm-lab
sudo bash /mnt/mitm-lab/install.sh attacker
```

Enter sudo credentials in the guest, not in chat, scripts, or captured command
arguments. The installer verifies the new attacker MAC against the setup media
before changing clone identity. It regenerates machine ID, hostname, and SSH
host keys; clears the clone's ordinary-user home and copied demo state; replaces
cloned network configuration; and disables the desktop. These operations affect
only the independent clone. Its original OS login password remains unchanged.
Package installation uses Ubuntu's repository versions of mitmproxy and Scapy.

Run host Start again after setup completes. It verifies the restricted guest
administrator, gracefully shuts the clone down, attaches its prepared adapter to
`labnet`, and starts the staged VMs if memory permits. If you powered the clone
off after installation, Start boots it in quarantine to verify setup first.

In **VM1** and **VM_2**, mount the same media and run respectively:

```bash
# VM1
sudo mkdir -p /mnt/mitm-lab
sudo mount -o ro /dev/sr0 /mnt/mitm-lab
sudo bash /mnt/mitm-lab/install.sh server

# VM_2
sudo mkdir -p /mnt/mitm-lab
sudo mount -o ro /dev/sr0 /mnt/mitm-lab
sudo bash /mnt/mitm-lab/install.sh client
```

The existing application CA must already be trusted on the client. No attacker
CA is installed. Check `https://smart-learning.test` normally; fix an expired
server certificate through the existing local-lab runbook if necessary.

Setup installs a separate restricted root SSH key. It permits status, bounded
packet capture, fixed-target attack start/stop, synthetic probes, peer-neighbour
repair, and evidence export. It permits no arbitrary shell commands, forwarding,
PTYs, general SCP, or unrestricted sudo. The private key stays in ignored backups
with owner-only Windows permissions. First SSH use enrolls a host key only after
verifying the registered VM owns the expected loopback forward. Compare the
guest's printed host fingerprint with the enrolled key; later changes are refused.

```powershell
.\scripts\mitm_lab.ps1 -Action Status
```

All three guests must report the expected role, IP, distinct MAC, and internal
network before Run. Guest-script changes after media creation require rerunning
Prepare while powered off and rerunning the guest installers; do not mutate
active media. Host-only orchestration fixes take effect immediately and do not
require reinstalling unchanged guest scripts.

## Run the demonstration

```powershell
.\scripts\mitm_lab.ps1 -Action Run
# Or a single phase:
.\scripts\mitm_lab.ps1 -Action Run -Mode modify
.\scripts\mitm_lab.ps1 -Action Collect
.\scripts\mitm_lab.ps1 -Action Stop
```

The complete sequence is baseline, observe, modify, inject, drop, and recovery.
Each phase receives a fresh run ID. Captures start before poisoning and stop
after restoration. The client makes real HTTP requests, checks existing HTTPS
with certificate verification, and uses its installed headless Firefox for a
page screenshot. Firefox runs through the existing ordinary-user lab SSH
configuration in `backups/local-three-vm/media/lab-ssh.conf`; the root capture key
does not launch the browser. Firefox uses a disposable profile, leaving student
profiles alone. The run stops on a failed phase while retaining its evidence.
A standalone baseline/recovery can run with only VM1 and VM_2 while the attacker
is off; the complete sequence still uses all three demonstration VMs.

The demonstration page is `http://192.168.50.10:8008/`. A manual request can use
`?run=manual`, but proxy changes apply only to the current active run ID. Use the
reported run ID in the page URL when repeating a browser interaction during a
single active phase. The synthetic service exposes only `/`, `/api/lesson`,
and `/api/submit`; it has no account or database access.

| Phase | Demonstrated change | Required comparison |
| --- | --- | --- |
| baseline | Direct HTTP, original note | Client sends to real VM1 MAC; server receives original |
| observe | Same content through attacker | Forged ARP replies; client sends to attacker MAC; note visible in attacker capture |
| modify | Submitted note becomes `MODIFIED by the lab attacker` | Original at client; changed bytes and log at server |
| inject | Added yellow attacker banner | Banner absent in server response, present in client response and Firefox |
| drop | `/api/submit` connection terminated before forwarding | Client failure, no matching server submission; `/api/lesson` still succeeds |
| recovery | Correct neighbours and direct traffic return | Real server MAC, unchanged note, verified HTTPS |

Dropping uses mitmproxy's flow termination with lazy upstream connection setup;
the client can see a reset/closed connection rather than a timeout. This is
selective denial of delivery, not a fake successful server response.

For the optional VM3 comparison, install the `comparison` role in VM3 using the
same attached setup media once. `Run -Mode comparison` first stops and powers off
the attacker, checks RAM, then boots VM3. Wait for the guest to boot and rerun
if its restricted SSH endpoint is not yet ready. This staged comparison proves
normal access from VM3; it does not claim both clients were active during an attack.

## Wireshark and evidence

Each session is private at `backups/mitm-lab/evidence/<session>/`. Each phase
contains client, server, and attacker captures, VM identities, neighbour tables,
synthetic server/proxy logs, and a client report plus Firefox screenshot. The
analysis creates `frames.csv`, HTTP Follow TCP Stream text extracts, and explicit
frame/stream references in `analysis.json`. No existing capture is overwritten.

Open the phase's `network.pcap` in Wireshark and decode TCP port 8008 as HTTP
(Analyze → Decode As). Useful display filters:

```text
arp
arp.opcode == 2 && (arp.src.proto_ipv4 == 192.168.50.10 || arp.src.proto_ipv4 == 192.168.50.11)
tcp.port == 8008 && http
http.request.method == "POST" && http.request.uri == "/api/submit"
tcp.port == 443 && tls
```

Use the reported stream ID with `tcp.stream == N`, then Follow → TCP Stream to
show the synthetic note and HTTP response. For modification, compare the client
stream with the server stream: a transparent proxy creates separate TCP
connections, so their stream IDs and source IPs can differ. For injection,
compare response bodies and the client Firefox screenshot. For dropping,
combine the client failure with the absence of the run's POST in both the server
capture and endpoint log; an empty screenshot alone does not prove dropping.

Save one Wireshark screenshot per phase as `<phase-run-id>/wireshark.png`, with
the relevant filter, packet details, and frame numbers visible. Rerun Collect
to include them. `submission/` contains synthetic Firefox screenshots, selected
Wireshark screenshots, header-only frame tables, and analysis results. Review
Wireshark screenshots before sharing: show only the filtered lab packets.
Raw pcaps, journals, keys, runtime reports, and stream text remain private.

## Explanation for the assessment

**How the attacker became involved:** the attacker sends false ARP replies to
VM_2 saying that VM1's IP belongs to the attacker's MAC, and to VM1 saying the
same about VM_2. Their IP destinations and browser URL remain unchanged, but
their local Ethernet frames reach VM4. Temporary forwarding carries traffic;
the scoped redirect passes demonstration HTTP through the proxy. Source IP `.13`
on the proxy's server-side HTTP connection is expected and must be explained.

**What is observable:** the attacker can read HTTP request paths, headers,
synthetic notes, and responses on port 8008. On the existing HTTPS path, captures
show network endpoints, TCP/TLS handshakes, packet lengths and timing, and
encrypted records. This lab does not decrypt the application's HTTPS payloads.

**What changed:** baseline traffic uses the legitimate peer MAC directly.
During interception, ARP mappings and Ethernet destinations point to VM4.
Observe preserves the content; modify changes the note delivered to VM1;
inject adds content returned to the browser; drop prevents selected submissions
from reaching VM1. Verified HTTPS still protects contents while traversing the
attacker, although an on-path attacker can still disrupt availability.

## Recovery

Stop works repeatedly. Normal shutdown and the ten-minute attack watchdog
terminate poisoning and the proxy, advertise legitimate ARP mappings, remove
only the lab's dedicated firewall rules/chains, and restore recorded sysctls.
No global firewall flush occurs. Incomplete restoration retains its state for
retry. Never force-power-off the attacker while poisoning is active.

On this host, VirtualBox's ACPI shutdown button sometimes did not finish. If
Stop reports that graceful shutdown is pending, verify the attack was stopped,
run `sudo poweroff` interactively in VM4, then rerun Stop after Status reports
`poweroff`. This was the final shutdown path used in the measured demonstration.

```powershell
.\scripts\mitm_lab.ps1 -Action Stop
.\scripts\mitm_lab.ps1 -Action Status
```

After an abrupt kill or host sleep, use the attacker console:

```bash
sudo systemctl stop smart-mitm-attack
sudo python3 /opt/smart-mitm/attacker.py recover
```

If the attacker cannot boot, remove only the affected neighbour entries in the
two guest consoles so normal ARP discovery can resume:

```bash
# VM_2: discover the interface containing 192.168.50.11, then:
sudo ip neigh del 192.168.50.10 dev enp0s8
# VM1:
sudo ip neigh del 192.168.50.11 dev enp0s8
```

Verify the interface name before those commands. Start the recovered lab and
run `Run -Mode recovery` to prove direct HTTP and verified HTTPS again. Stop
leaves VM1 and VM_2 running and retains the attacker VM powered off. The synthetic
page can be disabled separately with `sudo systemctl disable --now smart-mitm-http`
on VM1. Original DVD attachments are recorded in `backups/mitm-lab/state.json`;
restore them through VirtualBox only while the guests are powered off. Existing
pre-prototype snapshots and physical-server configuration are retained.

## Local checks

```powershell
python -m unittest discover -s deploy/mitm-lab -p test_lab.py -v
python -m compileall -q deploy/mitm-lab
```

On a restricted Windows sandbox, localhost tests and Git Bash initialization
may need to run outside that sandbox. If antivirus injects an inaccessible
`SSLKEYLOGFILE`, clear that environment variable only for the test process.
Use a writable temporary directory under ignored backups when system temp is
not writable. These are host test conditions, not changes to guest TLS trust.

References: [mitmproxy transparent mode](https://docs.mitmproxy.org/stable/howto/transparent/),
[mitmproxy addon examples](https://docs.mitmproxy.org/stable/addons/examples/).
