"""Windows orchestration; VM disks, keys and packet captures stay in ignored backups."""
import argparse
import ctypes
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import time
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
LAB = Path(__file__).resolve().parent
BACKUP = ROOT / "backups/mitm-lab"
STATE = BACKUP / "state.json"
VBOX = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Oracle/VirtualBox/VBoxManage.exe"
SSH = Path(os.environ.get("WINDIR", "C:/Windows")) / "System32/OpenSSH/ssh.exe"
KEYGEN = SSH.with_name("ssh-keygen.exe")
SCP = SSH.with_name("scp.exe")
VMS = {"server": ("VM1", 2221, "192.168.50.10"), "client": ("VM_2", 2222, "192.168.50.11"),
       "comparison": ("VM3", 2223, "192.168.50.12"), "attacker": ("VM4_Attacker", 2224, "192.168.50.13")}
EXPORT_FILES = {"network.pcap", "identity.json", "final-neighbours.json", "client-report.json",
                "browser.png", "browser.txt", "browser.log", "server-events.jsonl", "flows.jsonl", "attack-journal.txt"}


def command(*args, check=True, timeout=60):
    result = subprocess.run([str(a) for a in args], capture_output=True, timeout=timeout)
    if check and result.returncode:
        raise RuntimeError(result.stderr.decode(errors="replace").strip() or result.stdout.decode(errors="replace").strip())
    return result


def vbox(*args, **kwargs):
    return command(VBOX, *args, **kwargs).stdout.decode(errors="replace")


def info(name):
    values = {}
    for line in vbox("showvminfo", name, "--machinereadable").splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            values[key.strip('"')] = value.strip('"').replace("\\\\", "\\")
    return values


def read_state():
    if not STATE.exists():
        raise RuntimeError("Run Prepare first")
    return json.loads(STATE.read_text())


def save(state):
    BACKUP.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, indent=2))


def free_memory_mb():
    class Memory(ctypes.Structure):
        _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong)] + [
            (name, ctypes.c_ulonglong) for name in ("total", "available", "page", "page_available",
                                                   "virtual", "virtual_available", "extended")]
    data = Memory()
    data.length = ctypes.sizeof(data)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(data)):
        raise RuntimeError("Cannot measure host RAM")
    return data.available // (1024 * 1024)


def memory_guard(names):
    required = 1024
    for name in names:
        vm = info(name)
        if vm["VMState"] == "poweroff":
            required += int(vm["memory"])
        elif vm["VMState"] != "running":
            raise RuntimeError(f"{name} is {vm['VMState']}; preserve saved states and resolve manually")
    available = free_memory_mb()
    if available < required:
        raise RuntimeError(f"Need {required} MB free before startup; available {available} MB. "
                           f"Close unused host applications to free at least {required - available} MB; VM3 stays off.")


def disk_guard(names):
    # Existing thin disks and fresh captures need room on every backing volume.
    paths = [BACKUP]
    for name in names:
        vm = info(name)
        if vm.get("CfgFile"):
            paths.append(Path(vm["CfgFile"]).parent)
        paths.extend(Path(value).parent for key, value in vm.items()
                     if key.startswith("SATA-") and value.lower().endswith((".vdi", ".vmdk", ".vhd")))
    for path in paths:
        available = shutil.disk_usage(path).free
        if available < 2 * 1024**3:
            raise RuntimeError(f"Need at least 2 GiB free on the volume containing {path}; "
                               f"available {available // (1024**2)} MB. No VM was started.")


def verify_forward(role):
    name, port, _ = VMS[role]
    vm = info(name)
    forwards = [v for k, v in vm.items() if k.startswith("Forwarding(")]
    if not any(v.split(",")[1:4] == ["tcp", "127.0.0.1", str(port)] and v.endswith(",22") for v in forwards):
        raise RuntimeError(f"{name} must own loopback SSH forward {port}; refused SSH enrollment")
    return vm


def ssh_args(role, remote):
    verify_forward(role)
    _, port, _ = VMS[role]
    return [SSH, "-i", BACKUP / "lab", "-o", "IdentitiesOnly=yes", "-o", "BatchMode=yes",
            "-o", "ConnectTimeout=8", "-o", "StrictHostKeyChecking=accept-new",
            "-o", f'UserKnownHostsFile="{(BACKUP / "known_hosts").as_posix()}"',
            "-p", str(port), "root@127.0.0.1", remote]


def remote(role, text, timeout=90):
    known = BACKUP / "known_hosts"
    before = known.read_bytes() if known.exists() else None
    for attempt in range(2):
        try:
            result = command(*ssh_args(role, text), timeout=timeout, check=False)
            break
        except subprocess.TimeoutExpired:
            if attempt or text.split()[0] not in ("status", "capture-stop", "attack-stop", "repair-neighbour"):
                raise
            print(f"{role} {text.split()[0]} timed out; retrying this idempotent command once.", flush=True)
    if result.returncode:
        error = result.stderr.decode(errors="replace").strip()
        # A pre-install authentication probe must not pin the clone's old host
        # key before the installer regenerates it. Retain every existing pin.
        if result.returncode == 255 and "Permission denied" in error:
            if before is None:
                known.unlink(missing_ok=True)
            elif known.exists() and known.read_bytes() != before:
                known.write_bytes(before)
        raise RuntimeError(error or result.stdout.decode(errors="replace").strip())
    return result.stdout.decode()


def status_remote(role):
    result = json.loads(remote(role, "status"))
    if result["role"] != role or result["address"] != VMS[role][2]:
        raise RuntimeError(f"{role} guest identity mismatch")
    return result


def user_connection(role, scp=False):
    if role not in ("client", "comparison"):
        raise ValueError("Browser probes run only in the two existing client accounts")
    verify_forward(role)
    config = ROOT / "backups/local-three-vm/media/lab-ssh.conf"
    if not config.is_file():
        raise RuntimeError("Existing ordinary-user lab SSH configuration is required for Firefox")
    alias = "sl-client1" if role == "client" else "sl-client2"
    arguments = [SCP if scp else SSH, "-F", config, "-o", "ProxyJump=none", "-o", "HostName=127.0.0.1",
                 "-P" if scp else "-p", str(VMS[role][1])]
    return arguments, alias


def user_probe(role, mode, run_id):
    from demo_server import valid_run
    valid_run(run_id)
    if mode not in ("baseline", "observe", "modify", "inject", "drop", "recovery", "comparison"):
        raise ValueError("Invalid browser probe mode")
    arguments, alias = user_connection(role)
    # An SSH login opens the correct user PAM/cgroup session for Snap Firefox.
    # runuser from a root SSH session leaves Snap in the root session's cgroup.
    probe = f"/usr/bin/python3 /opt/smart-mitm/client_probe.py {mode} {run_id} ~/snap/firefox/common/smart-mitm/{run_id}"
    result = command(*arguments, alias, probe, check=False, timeout=240)
    if result.returncode:
        raise RuntimeError("Guest browser probe failed: " + result.stderr.decode(errors="replace")[-1500:]
                           + "\n" + result.stdout.decode(errors="replace")[-2000:])
    return result.stdout.decode()


def collect_user_probe(role, run_id, destination):
    from demo_server import valid_run
    valid_run(run_id)
    arguments, alias = user_connection(role, scp=True)
    for name in ("client-report.json", "browser.png", "browser.txt", "browser.log"):
        source = f"{alias}:snap/firefox/common/smart-mitm/{run_id}/{name}"
        result = command(*arguments, source, destination / name, check=False, timeout=40)
        if result.returncode and name == "client-report.json":
            raise RuntimeError("Client report collection failed: " + result.stderr.decode(errors="replace"))


def build_media(state):
    media = BACKUP / "media"
    media.mkdir(exist_ok=True)
    if not (BACKUP / "lab").exists():
        command(KEYGEN, "-q", "-t", "ed25519", "-N", "", "-C", "smart-mitm-restricted", "-f", BACKUP / "lab")
    # Restrict the generated private key to its Windows owner, without exposing it.
    command("icacls", BACKUP / "lab", "/inheritance:r", "/grant:r", os.environ["USERNAME"] + ":F")
    shutil.copyfile(BACKUP / "lab.pub", media / "lab.pub")
    mac = info("VM4_Attacker")["macaddress2"].lower()
    (media / "attacker-mac.txt").write_text(":".join(mac[i:i+2] for i in range(0, 12, 2)) + "\n")
    manifest = {}
    with tarfile.open(media / "lab.tar.gz", "w:gz") as archive:
        for path in sorted(LAB.iterdir()):
            if path.suffix != ".py":
                continue
            data = path.read_bytes().replace(b"\r\n", b"\n")
            manifest[path.name] = hashlib.sha256(data).hexdigest()
            item = tarfile.TarInfo(path.name)
            item.size, item.mode = len(data), 0o644
            # mitmproxy 8's watcher loads only files newer than timestamp zero.
            item.mtime = 1
            archive.addfile(item, io.BytesIO(data))
    (media / "install.sh").write_bytes((LAB / "install.sh").read_bytes().replace(b"\r\n", b"\n"))
    manifest["install.sh"] = hashlib.sha256((media / "install.sh").read_bytes()).hexdigest()
    (media / "manifest.json").write_text(json.dumps(manifest, indent=2))
    args = ["--iprt-iso-maker-file-marker-bourne-sh", str(uuid4()), "--volume-id", "MITM_LAB",
            "--file-mode=0444", "--dir-mode=0555"]
    for name in ("lab.tar.gz", "lab.pub", "attacker-mac.txt", "install.sh", "manifest.json"):
        mapping = f"/{name}={(media / name).resolve()}"
        args.append("'" + mapping.replace("'", "'\\''") + "'")
    (media / "setup.viso").write_text(" ".join(args), encoding="utf-8")
    state["source_manifest"] = manifest


def prepare():
    BACKUP.mkdir(parents=True, exist_ok=True)
    state = json.loads(STATE.read_text()) if STATE.exists() else {"created_at": datetime.now(timezone.utc).isoformat(), "original_dvd": {}}
    listed = vbox("list", "vms")
    if '"VM4_Attacker"' not in listed:
        source = info("VM_2")
        if source["VMState"] != "poweroff":
            raise RuntimeError("Power off VM_2 gracefully before the independent full clone")
        if shutil.disk_usage(BACKUP).free < 30 * 1024**3:
            raise RuntimeError("Need at least 30 GiB free for independent clone and evidence")
        state["source_vm"] = source
        save(state)
        print("Creating independent current-state full clone of VM_2...", flush=True)
        vbox("clonevm", "VM_2", "--name", "VM4_Attacker", "--basefolder", BACKUP / "vms",
             "--mode", "machine", "--register", timeout=1800)
        vbox("modifyvm", "VM4_Attacker", "--memory", "1536", "--cpus", "2", "--macaddress1", "auto",
             "--macaddress2", "auto", "--nic1", "nat", "--nic2", "intnet", "--intnet2", "mitm-quarantine",
             "--cableconnected2", "off")
        for key, value in info("VM4_Attacker").items():
            if key.startswith("Forwarding("):
                vbox("modifyvm", "VM4_Attacker", "--natpf1", "delete", value.split(",")[0])
        vbox("modifyvm", "VM4_Attacker", "--natpf1", "MITMSSH,tcp,127.0.0.1,2224,,22")
        state["attacker_uuid"] = info("VM4_Attacker")["UUID"]
        state["quarantined"] = True
        save(state)
    elif state.get("attacker_uuid") != info("VM4_Attacker").get("UUID"):
        raise RuntimeError("Existing VM4_Attacker is not owned by this lab; no changes made")
    for role in ("server", "client", "comparison", "attacker"):
        name = VMS[role][0]
        if info(name)["VMState"] != "poweroff":
            raise RuntimeError(f"Power off {name} before changing read-only setup media")
    state.setdefault("baseline_snapshots", {})
    for name in ("VM1", "VM_2", "VM3"):
        if name not in state["baseline_snapshots"]:
            snapshot = "before-mitm-lab-" + datetime.now(timezone.utc).strftime("%Y%m%dt%H%M%S")
            vbox("snapshot", name, "take", snapshot, timeout=180)
            state["baseline_snapshots"][name] = snapshot
            save(state)
    build_media(state)
    for role in ("server", "client", "comparison", "attacker"):
        name = VMS[role][0]
        state["original_dvd"].setdefault(name, info(name).get("IDE-0-0", "none"))
        save(state)
        vbox("storageattach", name, "--storagectl", "IDE", "--port", "0", "--device", "0",
             "--type", "dvddrive", "--medium", BACKUP / "media/setup.viso")
    save(state)
    print("Prepared independent attacker clone in quarantine and attached read-only setup media.")
    bootstrap_instructions()


def bootstrap_instructions():
    print("One-time guest setup (interactive sudo; no passwords in host scripts):\n"
          "  sudo mkdir -p /mnt/mitm-lab\n  sudo mount -o ro /dev/sr0 /mnt/mitm-lab\n"
          "  sudo bash /mnt/mitm-lab/install.sh ROLE\n"
          "Use attacker in VM4_Attacker, server in VM1, client in VM_2; comparison in VM3 is optional.\n"
          "After attacker setup: sudo poweroff; then run Start again.\n"
          "Compare the printed SSH fingerprint with backups/mitm-lab/known_hosts after enrollment.")


def start():
    state = read_state()
    disk_guard([VMS[role][0] for role in ("server", "client", "attacker")])
    attacker = info("VM4_Attacker")
    if attacker.get("UUID") != state.get("attacker_uuid"):
        raise RuntimeError("Attacker VM identity does not match this lab's manifest")
    if state.get("quarantined", True) and not state.get("identity_verified", False):
        if attacker["VMState"] == "poweroff":
            memory_guard(["VM4_Attacker"])
            vbox("startvm", "VM4_Attacker", "--type", "gui")
            if not state.get("initial_boot_started", False):
                state["initial_boot_started"] = True
                save(state)
                bootstrap_instructions()
                print("Only the quarantined attacker was started. Complete identity setup, then run Start again.")
                return
        # Networkd can withhold the static address while carrier is absent.
        # Enable the cable only on the isolated quarantine switch for verification.
        if attacker.get("nic2") != "intnet" or attacker.get("intnet2") != "mitm-quarantine":
            raise RuntimeError("Unverified attacker must remain on the internal quarantine switch")
        vbox("controlvm", "VM4_Attacker", "setlinkstate2", "on")
        last_error = None
        for _ in range(25):
            try:
                identity = status_remote("attacker")
                expected_mac = ":".join(attacker["macaddress2"][i:i+2] for i in range(0, 12, 2)).lower()
                if identity["mac"].lower() != expected_mac:
                    raise RuntimeError("Attacker guest MAC differs from its prepared adapter")
                break
            except Exception as exc:
                last_error = exc
                time.sleep(1)
        else:
            bootstrap_instructions()
            raise RuntimeError("Attacker bootstrap not yet verified: " + str(last_error))
        state["identity_verified"] = True
        state["verified_identity"] = identity
        save(state)
    if state.get("quarantined", True):
        if info("VM4_Attacker")["VMState"] == "running":
            vbox("controlvm", "VM4_Attacker", "acpipowerbutton")
        for _ in range(60):
            if info("VM4_Attacker")["VMState"] == "poweroff":
                break
            time.sleep(1)
        else:
            raise RuntimeError("Wait for graceful attacker shutdown; no forced poweroff performed")
        # Poweroff can precede release of the VirtualBox GUI session lock.
        for _ in range(30):
            try:
                vbox("modifyvm", "VM4_Attacker", "--intnet2", "labnet", "--cableconnected2", "on")
                break
            except RuntimeError as exc:
                if "already locked" not in str(exc):
                    raise
                time.sleep(1)
        else:
            raise RuntimeError("Attacker powered off but its VirtualBox session lock is still pending; rerun Start")
        state["quarantined"] = False
        save(state)
    names = [VMS[role][0] for role in ("server", "client", "attacker")]
    memory_guard(names)
    for name in names:
        if info(name)["VMState"] == "poweroff":
            vbox("startvm", name, "--type", "headless" if name == "VM4_Attacker" else "gui")
    # Match the existing lab's idle-sleep guard, with a hidden helper window.
    guard = ROOT / "scripts/keep_lab_awake.ps1"
    if guard.exists():
        subprocess.Popen(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "RemoteSigned", "-File", str(guard)],
                         creationflags=subprocess.CREATE_NO_WINDOW, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    bootstrap_instructions()
    print("Started the staged lab. Run Status after guest setup; attack is not enabled by Start.")


def export(role, run_id, destination, include_probe=True):
    destination.mkdir(parents=True, exist_ok=True)
    archive = destination / "evidence.tar.gz"
    with archive.open("wb") as stream:
        result = subprocess.run([str(a) for a in ssh_args(role, "export " + run_id)], stdout=stream, stderr=subprocess.PIPE, timeout=90)
    if result.returncode:
        raise RuntimeError(result.stderr.decode(errors="replace"))
    with tarfile.open(archive, "r:gz") as bundle:
        for item in bundle.getmembers():
            if not item.isfile() or item.name not in EXPORT_FILES or item.size > 1024**3:
                raise RuntimeError("Unexpected evidence archive member")
        bundle.extractall(destination, filter="data")
    if include_probe and role in ("client", "comparison"):
        collect_user_probe(role, run_id, destination)


def run_session(mode):
    state = read_state()
    disk_guard([VMS[role][0] for role in ("server", "comparison" if mode == "comparison" else "client", "attacker")])
    if state.get("quarantined", True):
        raise RuntimeError("Complete attacker bootstrap and Start before running attacks")
    if mode == "comparison":
        stop_attack(poweroff=True)
        for _ in range(60):
            if info("VM4_Attacker")["VMState"] == "poweroff":
                break
            time.sleep(1)
        else:
            raise RuntimeError("Wait for attacker shutdown before starting VM3")
        memory_guard(["VM3"])
        if info("VM3")["VMState"] == "poweroff":
            vbox("startvm", "VM3", "--type", "gui")
        roles = ("server", "comparison")
    elif mode in ("baseline", "recovery") and info("VM4_Attacker")["VMState"] == "poweroff":
        # A genuine direct-path capture requires only the two communicating VMs.
        # Keep the complete six-phase run on its normal three-VM path.
        roles = ("server", "client")
    else:
        roles = ("server", "client", "attacker")
    for role in roles:
        vm = info(VMS[role][0])
        if vm.get("nic2") != "intnet" or vm.get("intnet2") != "labnet":
            raise RuntimeError(f"{role} NIC2 must use isolated internal labnet")
    identities = {role: status_remote(role) for role in roles}
    if len({identity["mac"] for identity in identities.values()}) != len(roles):
        raise RuntimeError("Lab MACs must be distinct")
    session_id = datetime.now(timezone.utc).strftime("%Y%m%dt%H%M%S") + "-" + uuid4().hex[:6]
    directory = BACKUP / "evidence" / session_id
    directory.mkdir(parents=True)
    state["last_session"] = session_id
    save(state)
    session = {"id": session_id, "identities": identities, "runs": [], "passed": False}
    modes = ("baseline", "observe", "modify", "inject", "drop", "recovery") if mode == "all" else (mode,)
    try:
        if "attacker" in roles:
            remote("attacker", "attack-stop")
        for current in modes:
            run_id = current + "-" + session_id
            record = {"mode": current, "run_id": run_id, "passed": False}
            session["runs"].append(record)
            capturing = []
            failures = []
            probe_started = False
            print("Running " + current + " (" + run_id + ")", flush=True)
            try:
                if current in ("baseline", "recovery"):
                    remote("client", "repair-neighbour")
                    remote("server", "repair-neighbour")
                for role in roles:
                    remote(role, "capture-start " + run_id)
                    capturing.append(role)
                if current in ("observe", "modify", "inject", "drop"):
                    remote("attacker", "attack-start " + current + " " + run_id)
                probe_role = "comparison" if current == "comparison" else "client"
                probe_started = True
                record["probe_stdout"] = user_probe(probe_role, current, run_id)
                record["identities_during_run"] = {role: status_remote(role) for role in roles}
                record["passed"] = True
            except Exception as exc:
                failures.append(str(exc))
            finally:
                # Always stop poisoning even when probes or export fail.
                try:
                    if "attacker" in roles:
                        remote("attacker", "attack-stop")
                except Exception as exc:
                    failures.append("ATTACK RECOVERY: " + str(exc))
                for role in capturing:
                    try:
                        remote(role, "capture-stop " + run_id)
                        export(role, run_id, directory / run_id / role, include_probe=probe_started)
                    except Exception as exc:
                        failures.append(role + " evidence: " + str(exc))
                record["errors"] = failures
                record["passed"] = record["passed"] and not failures
                (directory / "session.json").write_text(json.dumps(session, indent=2))
            if failures:
                raise RuntimeError("; ".join(failures))
        session["passed"] = all(record["passed"] for record in session["runs"])
    finally:
        (directory / "session.json").write_text(json.dumps(session, indent=2))
    collect(session_id)


def stop_attack(poweroff=True):
    vm = info("VM4_Attacker")
    if vm["VMState"] == "running":
        remote("attacker", "attack-stop")
        if poweroff:
            vbox("controlvm", "VM4_Attacker", "acpipowerbutton")
            for _ in range(60):
                if info("VM4_Attacker")["VMState"] == "poweroff":
                    break
                time.sleep(1)
            else:
                raise RuntimeError("Graceful attacker shutdown still pending; no forced poweroff performed")
    for role in ("client", "server"):
        if info(VMS[role][0])["VMState"] == "running":
            remote(role, "repair-neighbour")
    print("Recovery requested; attacker retained. Status confirms final power state.")


def collect(session_id):
    from demo_server import valid_run
    from analyze import analyze_session
    session_id = valid_run(session_id or read_state().get("last_session", ""))
    directory = BACKUP / "evidence" / session_id
    if not (directory / "session.json").exists():
        raise RuntimeError("Session evidence does not exist")
    result = analyze_session(directory)
    print(json.dumps(result, indent=2))
    if not result["passed"]:
        raise RuntimeError("Packet acceptance is incomplete; inspect analysis.json")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("status", "prepare", "start", "run", "stop", "collect"))
    parser.add_argument("--mode", default="all", choices=("all", "baseline", "observe", "modify", "inject", "drop", "recovery", "comparison"))
    parser.add_argument("--session", default="")
    args = parser.parse_args()
    if args.action == "status":
        print("Host free RAM:", free_memory_mb(), "MB")
        listed = vbox("list", "vms")
        for role, (name, _, _) in VMS.items():
            if f'"{name}"' in listed:
                vm = info(name)
                print(name, vm["VMState"], vm["memory"] + " MB", vm.get("intnet2", ""))
                if vm["VMState"] == "running" and STATE.exists():
                    try:
                        print(json.dumps(status_remote(role)))
                    except Exception as exc:
                        print("Guest lab setup not verified:", str(exc))
    elif args.action == "prepare":
        prepare()
    elif args.action == "start":
        start()
    elif args.action == "run":
        run_session(args.mode)
    elif args.action == "stop":
        stop_attack()
    else:
        collect(args.session)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(type(exc).__name__ + ": " + str(exc), file=sys.stderr)
        raise SystemExit(1)
