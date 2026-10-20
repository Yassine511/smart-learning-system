"""Narrow root SSH entrypoint. No shell, arbitrary commands, or arbitrary paths."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tarfile
import time

from attacker import ATTACKER, BASE, CLIENT, SERVER, interface_for
from demo_server import valid_run

ROOT = Path(__file__).resolve().parent
SETTINGS = Path("/etc/smart-mitm.json")
MODES = ("observe", "modify", "inject", "drop")


def command(*args, check=True):
    return subprocess.run(args, check=check, text=True, capture_output=True)


def active(unit):
    return command("systemctl", "is-active", "--quiet", unit, check=False).returncode == 0


def stop(unit):
    command("systemctl", "stop", unit, check=False)
    command("systemctl", "reset-failed", unit, check=False)


def settings():
    return json.loads(SETTINGS.read_text())


def evidence(run_id):
    valid_run(run_id)
    path = BASE / "evidence" / run_id
    path.mkdir(parents=True, mode=0o700, exist_ok=True)
    return path


def dispatch(args):
    config = settings()
    role = config["role"]
    address = {"server": SERVER, "client": CLIENT, "comparison": "192.168.50.12", "attacker": ATTACKER}[role]
    action = args[0] if args else ""
    if args == ["status"]:
        nic, mac = interface_for(address)
        print(json.dumps({"role": role, "address": address, "interface": nic, "mac": mac,
            "neighbours": json.loads(command("ip", "-j", "neigh", "show", "dev", nic).stdout),
            "attack_active": active("smart-mitm-attack"),
            "attack_state_exists": (BASE / "attack-state.json").exists(),
            "attack_ready": (BASE / "attack-ready").read_text() if (BASE / "attack-ready").exists() else None,
            "http_service_active": active("smart-mitm-http"),
            "memory": command("free", "-m").stdout,
            "versions": command("dpkg-query", "-W", "python3", "tcpdump", "mitmproxy", "python3-scapy", check=False).stdout}))
    elif action == "capture-start" and len(args) == 2:
        run_id = valid_run(args[1])
        path = evidence(run_id)
        nic, mac = interface_for(address)
        unit = "smart-mitm-cap-" + run_id
        if active(unit) or (path / "network.pcap").exists():
            raise ValueError("Capture run already exists; use a fresh run ID")
        (path / "identity.json").write_text(json.dumps({"role": role, "ip": address, "mac": mac,
            "interface": nic, "time": datetime.now(timezone.utc).isoformat(),
            "neighbours": json.loads(command("ip", "-j", "neigh", "show", "dev", nic).stdout)}, indent=2))
        capture_filter = "arp or (tcp and (port 8008 or port 443) and (host 192.168.50.10) and (host 192.168.50.11 or host 192.168.50.12 or host 192.168.50.13))"
        command("systemd-run", "--unit=" + unit, "--collect", "--property=RuntimeMaxSec=600",
                "--property=KillSignal=SIGINT", "tcpdump", "-i", nic, "-nn", "-s", "0", "-U", "-Z", "root",
                "-w", str(path / "network.pcap"), capture_filter)
        for _ in range(40):
            if (path / "network.pcap").exists() and (path / "network.pcap").stat().st_size >= 24:
                break
            time.sleep(0.1)
        else:
            raise RuntimeError("tcpdump did not create a capture header")
        print("Capture started: " + run_id)
    elif action == "capture-stop" and len(args) == 2:
        run_id = valid_run(args[1])
        stop("smart-mitm-cap-" + run_id)
        path = evidence(run_id)
        nic, _ = interface_for(address)
        (path / "final-neighbours.json").write_text(command("ip", "-j", "neigh", "show", "dev", nic).stdout)
        print("Capture stopped: " + run_id)
    elif action == "attack-start" and len(args) == 3 and role == "attacker":
        mode, run_id = args[1], valid_run(args[2])
        if mode not in MODES or active("smart-mitm-attack"):
            raise ValueError("Invalid mode or attack already running")
        command("systemd-run", "--unit=smart-mitm-attack", "--collect", "--property=RuntimeMaxSec=600",
                "--property=TimeoutStopSec=30", "/usr/bin/python3", str(ROOT / "attacker.py"),
                "run", "--mode", mode, "--run-id", run_id)
        for _ in range(150):
            ready = BASE / "attack-ready"
            if ready.exists() and ready.read_text() == run_id:
                print("Attack ready: " + mode)
                return
            time.sleep(0.2)
        raise RuntimeError("Attack did not become ready; run attack-stop and inspect journal")
    elif args == ["attack-stop"] and role == "attacker":
        stop("smart-mitm-attack")
        command("/usr/bin/python3", str(ROOT / "attacker.py"), "recover")
        (BASE / "attack-ready").unlink(missing_ok=True)
        print("Attack stopped; recovery complete")
    elif action == "probe" and len(args) == 3 and role in ("client", "comparison"):
        mode, run_id = args[1], valid_run(args[2])
        if mode not in (*MODES, "baseline", "recovery", "comparison"):
            raise ValueError("Invalid probe mode")
        home = Path(config["guest_home"])
        # Snap Firefox only permits profiles inside its own common directory.
        output = home / "snap/firefox/common/smart-mitm" / run_id
        output.mkdir(parents=True, mode=0o700, exist_ok=True)
        command("chown", config["guest_user"], str(output.parent), str(output))
        result = command("runuser", "-u", config["guest_user"], "--", "/usr/bin/python3",
                         str(ROOT / "client_probe.py"), mode, run_id, str(output), check=False)
        for source in output.iterdir():
            if source.is_file():
                (evidence(run_id) / source.name).write_bytes(source.read_bytes())
        print(result.stdout)
        if result.returncode:
            raise RuntimeError("Client acceptance failed: " + result.stderr[-1500:])
    elif args == ["repair-neighbour"] and role in ("server", "client", "comparison"):
        nic, _ = interface_for(address)
        target = CLIENT if role == "server" else SERVER
        command("ip", "neigh", "del", target, "dev", nic, check=False)
        print("Removed only the lab peer neighbour entry; next request relearns it")
    elif action == "export" and len(args) == 2:
        run_id = valid_run(args[1])
        path = evidence(run_id)
        if active("smart-mitm-cap-" + run_id):
            raise ValueError("Stop capture before exporting")
        if role == "server":
            source = BASE / "server-events.jsonl"
            lines = [line for line in source.read_text().splitlines() if json.loads(line).get("run_id") == run_id]
            (path / "server-events.jsonl").write_text("\n".join(lines) + "\n")
        if role == "attacker":
            source = BASE / "proxy" / run_id / "flows.jsonl"
            if source.exists():
                (path / "flows.jsonl").write_bytes(source.read_bytes())
            (path / "attack-journal.txt").write_text(command("journalctl", "-u", "smart-mitm-attack", "-n", "150", "--no-pager").stdout)
        with tarfile.open(fileobj=sys.stdout.buffer, mode="w|gz") as archive:
            for source in sorted(path.iterdir()):
                if source.is_file() and not source.is_symlink():
                    archive.add(source, arcname=source.name)
    else:
        raise ValueError("Unsupported restricted lab command")


if __name__ == "__main__":
    try:
        if os.geteuid() != 0:
            raise RuntimeError("Restricted lab administrator requires root")
        dispatch(shlex.split(os.environ.get("SSH_ORIGINAL_COMMAND", "")))
    except Exception as exc:
        print(type(exc).__name__ + ": " + str(exc), file=sys.stderr)
        raise SystemExit(1)
