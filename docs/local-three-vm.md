# Local three-VM prototype

The latest measured results are in [the acceptance record](local-three-vm-acceptance.md).

This optional deployment runs entirely in the existing VirtualBox guests on the
Windows PC. The physical-server configuration and GPT-OSS defaults are preserved.
This lab uses a deliberately weak generation model; passing network checks does
not establish production answer quality.

| VM | Role | Memory | CPUs | Internal IP | Host SSH port |
| --- | --- | --- | --- | --- | --- |
| VM1 | Docker application, worker, databases, Ollama, Caddy | 4096 MB | 4 | 192.168.50.10 | 2221 |
| VM_2 | Firefox, Student One | 2048 MB | 2 | 192.168.50.11 | 2222 |
| VM3 | Firefox, Student Two | 2048 MB | 2 | 192.168.50.12 | 2223 |

Adapter 1 remains NAT for downloads. Adapter 2 remains the isolated `labnet`
internal network. Setup initially adds the lab addresses through a dedicated
systemd unit; the finishing helper makes them persistent in a Netplan overlay.
It also corrects stale NAT MAC matches inherited from cloning. The browser
origin is `https://smart-learning.test`. Host SSH forwards bind to loopback only.

## Installation

The initial powered-off snapshots and baseline configurations are recorded in
the ignored `backups/local-three-vm` directory. Model weights, credentials,
private keys, and VM state stay outside tracked source.

The generated `setup.viso` is read-only setup media, attached to each VM's IDE
DVD drive. Start the guests from VirtualBox Manager if an agent-launched start
hits Windows hardening error -5619. Do not disable Hyper-V or Windows security
features to work around that error.

Log in to each guest and run:

```bash
sudo mkdir -p /mnt/smart-learning
sudo mount -o ro /dev/sr0 /mnt/smart-learning
# VM1:
sudo bash /mnt/smart-learning/install.sh server
# VM_2 uses client1; VM3 uses client2 instead of server.
```

Enter the guest sudo password interactively. Setup runs in the background;
progress is in `/var/log/smart-learning-local-setup.log`. The media's public key
is added to that guest user's authorized keys for monitoring. Its private key
stays in the ignored host backup directory. No passwordless sudo rule is added.

Setup verifies the actual guest OS before installing packages. It supports
Ubuntu 22.04, 24.04, and 26.04, preserves the installed OS, and stops with an
explicit error for an unsupported OS, conflicting Docker packages, occupied
application ports, or insufficient server disk space.

The inspected VM1 has a 25 GiB disk with an approximately 11.5 GiB root LV.
`resume-server.sh` handles its initial free-space failure by checking that the
root is the expected ext4 Ubuntu LV and that the existing volume group has at
least 8 GiB unallocated, then expanding only that LV/filesystem by 8 GiB. It
does not repartition, replace, or enlarge the virtual disk. If these checks fail,
it stops for a specific repair.

After copying `deploy/local-prototype/{finish-setup.sh,admin.sh,trust-client.sh,resume-client.sh}`
into `~/smart-learning-finish` on each guest, run the finishing helper with sudo:

```bash
sudo bash ~/smart-learning-finish/finish-setup.sh server
# Use client1 on VM_2 and client2 on VM3.
```

The helper assigns each clone a unique machine ID and hostname, makes the lab
address persistent, and corrects a cloned NAT MAC match while preserving its
other settings. Reboot each guest after package installation finishes. An
interrupted client package download is resumed after any existing package
operation completes; its log is `/var/log/smart-learning-local-client-resume.log`.
If Firefox remains installed but reports a missing `gpu-2404-provider-wrapper`,
copy and run `repair-browser.sh` with sudo. It restores the missing GNOME/Mesa
content providers and connects them to Firefox. This repair is specific to the
inspected Firefox snap; see [Snap content interfaces](https://snapcraft.io/docs/reference/interfaces/content-interface/).

The helper installs the bootstrap public key for root with a forced command and
SSH restrictions. It permits only status, client certificate trust, guest reboot,
application restart, Ollama stop/start, and lab packet capture. Arbitrary commands
are refused. The installed command script is root-owned; no general sudo grant
is added. A Windows CRLF public-key line is normalized before matching it.

Server setup creates `/opt/smart-learning-local` and a mode-0600 `.env` with a
new random database password. It uses a separate Compose project:

```bash
sudo bash /opt/smart-learning-local/deploy/local-prototype/stack.sh ps
sudo bash /opt/smart-learning-local/deploy/local-prototype/stack.sh up -d
```

The command includes the base, lab, and local-prototype Compose files explicitly.
It installs `qwen2.5:0.5b` and `nomic-embed-text` into the VM1 Ollama volume,
records model tags/digests, pins the embedding digest, applies existing Alembic
migrations, builds the UI/API, and checks real readiness. It does not pull GPT-OSS
or use the host's Ollama or the friend's server.

`OLLAMA_THINK_MODE=off` omits the thinking option for Qwen. Application defaults
retain GPT-OSS with `OLLAMA_THINK_MODE=low`. Other prototype settings are 4096
context tokens, 128 generated tokens, a 210-second generation deadline, a
180-second queue wait, embedding batches of two, one active Ollama request, and
one loaded model. The application still limits generation to a single FIFO queue.
`OLLAMA_NUM_THREADS=1` applies to chat and embedding requests in this profile.
The default value of zero leaves Ollama's thread selection unchanged elsewhere.
On the inspected VirtualBox/Hyper-V host, the default threading exceeded the
210-second deadline; a one-thread request with the same chat prompt and context
completed in approximately 11 seconds. This measurement is host-specific.
`RAG_CITATION_EXAMPLE=true` adds a short, explicitly labelled formatting example
for the weak prototype model. It teaches the existing `[S1]` citation format;
the actual answer still receives the student's authorized PDF passages, and
citations retain the same database ownership and generation checks. The default
is false, preserving the normal prompt unless this option is selected.

Two synthetic administrator-created accounts are provisioned:
`student1@example.test` and `student2@example.test`. Their random passwords are
stored only in `/opt/smart-learning-local/.demo-credentials.json`, readable by
the bootstrap guest user. To view them privately in the guest:

```bash
cat /opt/smart-learning-local/.demo-credentials.json
```

To explicitly reset both existing demo accounts, copy
`deploy/local-prototype/reset-demo-passwords.sh` to VM1 and run it with guest
sudo and your chosen demo password:

```bash
sudo bash ~/reset-demo-passwords.sh 'YOUR_LOCAL_DEMO_PASSWORD'
```

This helper checks the local prototype configuration, changes only the two demo
accounts, invalidates their sessions, and refreshes the private credentials file
in the upload volume and server guest. Securely copy the updated file to the
clients if using automated checks. It does not change the normal account
creation password policy.

## Local HTTPS and browser trust

The dedicated `smart-learning-local-caddy` service binds to the server's internal
IP on 443 and proxies to the existing `127.0.0.1:8080` Nginx entry point. Its CA
state is separate from any other Caddy installation. Its public root certificate
is exported as `/opt/smart-learning-local/public/smart-learning-local-root.crt`.
Private CA keys never leave the server.

After client setup, copy that public certificate over authenticated SSH:

```bash
scp yassine@192.168.50.10:/opt/smart-learning-local/public/smart-learning-local-root.crt ~/smart-learning-local-root.crt
```

Launch Firefox once and close it to create its profile, then run:

```bash
sudo bash /opt/smart-learning-local-client/trust-client.sh ~/smart-learning-local-root.crt
```

The script installs OS trust and Firefox NSS trust, prints the public certificate
fingerprint, and verifies readiness without disabling TLS verification. Open
`https://smart-learning.test` from each client and sign in with its own account.
Secure/HttpOnly cookies, CSRF/origin validation, and ownership isolation remain
enabled. Tailscale is not required for this local profile.

After a long powered-off interval, verify certificate renewal before testing.
If Caddy logs show cleanup removed an expired site key while renewal was using
it, restart the dedicated service once, then recheck HTTPS:

```bash
sudo systemctl restart smart-learning-local-caddy
curl --fail --show-error https://smart-learning.test/api/ready
```

This keeps the existing CA and client trust; do not bypass certificate checks.

## Verification and evidence

Run `scripts/verify_three_vm.py` from both client VMs. The client installation
copies it and the sanitized PDF fixture into `/opt/smart-learning-local-client`:

```bash
/opt/smart-learning-local-client/.venv/bin/python \
  /opt/smart-learning-local-client/verify_three_vm.py \
  --role client1 --pdf /opt/smart-learning-local-client/networking_notes.pdf \
  --output ~/smart-learning-client1-results.json
```

Use `--role client2` and a separate output on VM3. Enter the two synthetic student
passwords when prompted, or supply a securely copied private credentials file
with `--credentials-file`. Evidence reports exclude passwords, session tokens,
PDF text, and chat text. The script requires the correct guest IP and validates
real login/logout, secured cookies, CSRF/ownership errors, saved chat, simultaneous
student requests, PDF upload/index/download/delete, and cited document chat.
`--skip-rag` produces diagnostic results only and cannot pass full acceptance.

Verify persistence by creating a conversation and indexed PDF in the UI,
restarting backend/worker, and confirming the same resources are available:

```bash
sudo bash /opt/smart-learning-local/deploy/local-prototype/stack.sh restart backend worker
```

For model-outage recovery, stop only local Ollama, verify readiness returns 503
and the UI reports unavailability, restart Ollama, and repeat a saved request:

```bash
sudo bash /opt/smart-learning-local/deploy/local-prototype/stack.sh stop ollama
sudo bash /opt/smart-learning-local/deploy/local-prototype/stack.sh start ollama
```

Capture during the client scenarios on VM1:

```bash
sudo tcpdump -i enp0s8 -nn -s 0 -w /tmp/smart-learning-https.pcap 'tcp port 443'
# In a second terminal, capture readable proxy HTTP:
sudo tcpdump -i lo -nn -s 0 -w /tmp/smart-learning-http.pcap 'tcp port 8080'
```

Confirm the internal adapter's name with `ip -br addr`; substitute it if needed.
Decode port 8080 as HTTP in Wireshark. Raw captures may contain session cookies;
retain them privately in the ignored backup directory. Share sanitized evidence
and screenshots using only synthetic accounts and the fixture PDF. Record all
three VM identities, CPU/RAM settings, IPs, request results, latency, and resource
usage. A server-only smoke test does not complete the three-VM assessment.

## Start, stop, rollback, and return to the physical server

From Windows, `scripts/local_vm_lab.ps1 -Action Status` displays state and memory.
`-Action Start` checks memory before starting stopped guests. If agent startup is
blocked by VirtualBox hardening, use VirtualBox Manager directly.
`-Action Stop` requests graceful guest shutdown and deletes no data.

`-Action Start` also launches `scripts/keep_lab_awake.ps1` in a hidden process.
It requests that Windows stay awake while any of VM1, VM_2, or VM3 is running,
then releases the request when all three stop. A mutex prevents duplicate
guards. It does not change the Windows power plan, Hyper-V, or security
features. Its logs are in the ignored `backups/local-three-vm/power-guard*.log`
files. When starting guests directly in VirtualBox Manager, run the guard in
a Windows terminal and keep that terminal open:

```powershell
.\scripts\keep_lab_awake.ps1
```

Keep the laptop lid open during a lab session. Manual sleep and lid-close
sleep still suspend the guests; the power request prevents automatic idle
sleep only. On 19 October, the host's lid/Modern Standby events coincided with
approximately nine-minute stall warnings in both client guests. Other
VirtualBox timing warnings remain a separate limitation; a power request
does not establish long-duration VM stability. See Microsoft's
[SetThreadExecutionState documentation](https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-setthreadexecutionstate).

For the inspected server's later low-disk condition, copy
`deploy/local-prototype/repair-resources.sh` into VM1 and run it with sudo.
It verifies the server identity, local Qwen profile, expected Ubuntu ext4 root
LV, and a single-LV volume group. If less than 3 GiB is free on `/` and at least
3 GiB remains unallocated in `ubuntu-vg`, it grows the existing LV/filesystem
by 3 GiB. It stops if these checks fail and makes no storage change when
headroom is already sufficient. It also prints Docker usage and kernel timing
diagnostics. The verified repair increased free space from 962 MiB to 3.8 GiB.

Inside VM1, normal application shutdown preserves volumes and model weights:

```bash
sudo systemctl stop smart-learning-local-caddy
sudo bash /opt/smart-learning-local/deploy/local-prototype/stack.sh stop
```

With all VMs powered off, explicitly running `scripts/local_vm_lab.ps1 -Action
Restore` restores the recorded pre-prototype snapshots and original VM settings.
Any subsequent prototype changes are no longer the active guest state; preserve
needed data before restoring. Keep the baseline backups and snapshot branches.

To return to the physical server, use its existing secrets, GPT-OSS model, and
`compose.physical.yaml` deployment instructions. Do not include either local
prototype or lab Ollama overrides there. No transfer of prototype student data
is needed; changing an embedding digest requires reindexing affected PDFs.

Installation references: [Docker Engine for Ubuntu](https://docs.docker.com/engine/install/ubuntu/),
[Caddy installation](https://caddyserver.com/docs/install),
[Ollama models](https://ollama.com/library/qwen2.5/tags).
