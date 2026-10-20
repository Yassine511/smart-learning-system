"""TShark packet assertions, frame references, and synthetic-only submission extracts."""
import csv
import io
import json
import os
from pathlib import Path
import subprocess

FIELDS = ("frame.number", "eth.src", "eth.dst", "ip.src", "ip.dst", "tcp.srcport", "tcp.dstport",
          "tcp.stream", "arp.opcode", "arp.src.proto_ipv4", "arp.src.hw_mac", "arp.dst.proto_ipv4",
          "http.request.method", "http.request.uri", "http.response.code", "http.file_data", "tls.record.content_type",
          "tls.record.opaque_type")


def packets(capture):
    tshark = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Wireshark/tshark.exe"
    if not tshark.exists():
        raise RuntimeError("Wireshark TShark is required")
    args = [str(tshark), "-n", "-r", str(capture), "-d", "tcp.port==8008,http",
            "-o", "tls.keylog_file:", "-Y", "arp || http || tls", "-T", "fields",
            "-E", "header=y", "-E", "separator=,", "-E", "quote=d", "-E", "occurrence=f"]
    for field in FIELDS:
        args.extend(("-e", field))
    result = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60, check=True)
    rows = list(csv.DictReader(io.StringIO(result.stdout)))
    for row in rows:
        try:
            row["body"] = bytes.fromhex(row["http.file_data"].replace(":", "")).decode("utf-8", errors="replace")
        except ValueError:
            row["body"] = ""
    return rows


def load_json(path):
    return json.loads(path.read_text())


def events(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()] if path.exists() else []


def check_run(directory, run, identities, rows):
    mode, run_id = run["mode"], run["run_id"]
    role = "comparison" if mode == "comparison" else "client"
    report = load_json(directory / role / "client-report.json")
    server_events = events(directory / "server/server-events.jsonl")
    submits = [event for event in server_events if event["method"] == "POST" and event["path"] == "/api/submit"]
    original = report.get("submitted_note", "")
    check = {"guest_probe_passed": report.get("passed") is True,
             "guest_firefox_screenshot": (directory / role / "browser.png").exists(),
             "verified_https": report.get("tls_verification_enabled") is True and report.get("checks", {}).get("https_verified") is True}
    client_mac, server_mac = identities[role]["mac"].lower(), identities["server"]["mac"].lower()
    client_rows, server_rows = rows[role], rows["server"]
    requests = [row for row in client_rows if row["http.request.method"]]
    check["http_requests_captured"] = bool(requests)
    check["tls_captured_on_client"] = any(row["tls.record.content_type"] and
        "443" in (row["tcp.srcport"], row["tcp.dstport"]) for row in client_rows)
    if mode in ("baseline", "recovery", "comparison"):
        check["direct_server_mac"] = any(row["eth.src"] == client_mac and row["eth.dst"] == server_mac for row in requests)
        check["server_received_original"] = any(event.get("note") == original for event in submits) and bool(original)
    else:
        attacker_mac = identities["attacker"]["mac"].lower()
        attacker_rows = rows["attacker"]
        check["client_sent_to_attacker_mac"] = any(row["eth.dst"] == attacker_mac for row in requests)
        for label, ip, target in (("server", "192.168.50.10", "192.168.50.11"),
                                  ("client", "192.168.50.11", "192.168.50.10")):
            check["forged_arp_" + label] = any(row["arp.opcode"] == "2" and row["arp.src.proto_ipv4"] == ip
                and row["arp.src.hw_mac"] == attacker_mac and row["arp.dst.proto_ipv4"] == target for row in attacker_rows)
        check["tls_forwarded_on_attacker"] = any(row["tls.record.content_type"] and
            "443" in (row["tcp.srcport"], row["tcp.dstport"]) for row in attacker_rows)
        check["encrypted_tls_records_on_attacker"] = any(
            "23" in (row["tls.record.content_type"], row["tls.record.opaque_type"]) and
            "443" in (row["tcp.srcport"], row["tcp.dstport"]) for row in attacker_rows)
        check["no_http_decoded_on_https"] = not any(row["http.request.method"] and
            "443" in (row["tcp.srcport"], row["tcp.dstport"]) for row in attacker_rows)
        check["synthetic_note_visible_to_attacker"] = bool(original) and any(original in row["body"] for row in attacker_rows)
        check["separate_proxy_connection"] = any(row["ip.src"] == "192.168.50.13" and row["http.request.method"] for row in server_rows)
        if mode == "modify":
            check["server_received_modified"] = any(event.get("note") == "MODIFIED by the lab attacker" for event in submits)
            check["original_absent_at_server"] = not any(event.get("note") == original for event in submits)
            check["modified_bytes_captured_at_server"] = any("MODIFIED by the lab attacker" in row["body"] for row in server_rows)
        elif mode == "drop":
            check["submission_absent_at_server"] = not submits and not any(row["http.request.uri"] == "/api/submit" for row in server_rows)
            check["unrelated_request_reached_server"] = any(event["path"] == "/api/lesson" for event in server_events)
            check["drop_recorded"] = any(event.get("action") == "dropped_before_forwarding" for event in events(directory / "attacker/flows.jsonl"))
        else:
            check["server_received_original"] = any(event.get("note") == original for event in submits)
        if mode == "inject":
            check["injected_bytes_on_client"] = any("id=\"mitm-banner\"" in row["body"] for row in client_rows)
            check["injected_bytes_absent_at_server"] = not any("id=\"mitm-banner\"" in row["body"] for row in server_rows)
    return check


def analyze_session(directory):
    session = load_json(directory / "session.json")
    result = {"session": session["id"], "passed": False, "runs": []}
    for run in session["runs"]:
        target = directory / run["run_id"]
        summary = {"run_id": run["run_id"], "mode": run["mode"], "checks": {}, "passed": False}
        try:
            rows = {role: packets(target / role / "network.pcap") for role in session["identities"]}
            summary["checks"] = check_run(target, run, session["identities"], rows)
            summary["passed"] = run["passed"] and all(summary["checks"].values())
            # No packet payloads or real app contents are published. Frame and stream IDs
            # refer to private pcaps; synthetic client screenshots are safe to submit.
            for role, packets_in_role in rows.items():
                with (target / role / "frames.csv").open("w", newline="", encoding="utf-8") as stream:
                    fields = [field for field in FIELDS if field != "http.file_data"]
                    writer = csv.DictWriter(stream, fields, extrasaction="ignore")
                    writer.writeheader()
                    writer.writerows(packets_in_role)
                streams = sorted({row["tcp.stream"] for row in packets_in_role if row["http.request.method"]}, key=int)
                summary.setdefault("http_streams", {})[role] = streams
                summary.setdefault("packet_frames", {})[role] = {
                    "arp": [row["frame.number"] for row in packets_in_role if row["arp.opcode"]],
                    "http": [row["frame.number"] for row in packets_in_role if row["http.request.method"] or row["http.response.code"]],
                    "tls": [row["frame.number"] for row in packets_in_role if row["tls.record.content_type"] or row["tls.record.opaque_type"]]}
                # Follow TCP Stream extracts use only already identified lab HTTP streams.
                tshark = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Wireshark/tshark.exe"
                for number in streams[:8]:
                    followed = subprocess.run([str(tshark), "-n", "-r", str(target / role / "network.pcap"),
                        "-q", "-z", "follow,tcp,ascii," + number], capture_output=True, check=True, timeout=30)
                    (target / role / ("http-stream-" + number + ".txt")).write_bytes(followed.stdout)
        except Exception as exc:
            summary["error"] = str(exc)
        summary["wireshark_screenshot_present"] = (target / "wireshark.png").exists()
        result["runs"].append(summary)
    result["passed"] = bool(result["runs"]) and all(run["passed"] for run in result["runs"])
    result["full_scenario_set"] = {run["mode"] for run in result["runs"]} >= {
        "baseline", "observe", "modify", "inject", "drop", "recovery"}
    result["deliverables_complete"] = result["passed"] and result["full_scenario_set"] and all(
        run["wireshark_screenshot_present"] for run in result["runs"])
    (directory / "analysis.json").write_text(json.dumps(result, indent=2))
    # Collect explicitly creates a sanitized submission bundle next to the private evidence.
    public = directory / "submission"
    public.mkdir(exist_ok=True)
    (public / "analysis.json").write_text(json.dumps(result, indent=2))
    for run in result["runs"]:
        target = directory / run["run_id"]
        for role in session["identities"]:
            for name in ("frames.csv", "browser.png"):
                source = target / role / name
                if source.exists():
                    (public / (run["mode"] + "-" + role + "-" + name)).write_bytes(source.read_bytes())
        for screenshot in target.glob("wireshark*.png"):
            if screenshot.is_file():
                (public / (run["mode"] + "-" + screenshot.name)).write_bytes(screenshot.read_bytes())
    return result
