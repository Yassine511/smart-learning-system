"""Real guest HTTP/TLS probes and installed-Firefox screenshots; synthetic data only."""
import argparse
import base64
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import ssl
import subprocess
import time
import urllib.error
import urllib.request

from demo_server import valid_run


def request(url, run_id, data=None, timeout=8):
    headers = {"X-MITM-Run": run_id, "Content-Type": "application/json", "Connection": "close"}
    body = None if data is None else json.dumps(data).encode()
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}),
        urllib.request.HTTPSHandler(context=ssl.create_default_context()))
    with opener.open(urllib.request.Request(url, body, headers), timeout=timeout) as response:
        return response.read().decode()


def browser(run_id, output):
    driver = "/snap/bin/firefox.geckodriver"
    if not Path(driver).exists():
        raise RuntimeError("Installed Firefox geckodriver is unavailable")
    profile = output / "firefox-profile"
    profile.mkdir(exist_ok=True)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    session = None

    def call(method, route, value=None):
        body = None if value is None else json.dumps(value).encode()
        req = urllib.request.Request("http://127.0.0.1:4445" + route, body,
                                     {"Content-Type": "application/json"}, method=method)
        with opener.open(req, timeout=35) as response:
            result = json.load(response)["value"]
        if isinstance(result, dict) and result.get("error"):
            raise RuntimeError("WebDriver: " + result["error"])
        return result

    with (output / "browser.log").open("w") as log:
        process = subprocess.Popen([driver, "--host", "127.0.0.1", "--port", "4445"], stdout=log, stderr=log)
        try:
            for _ in range(40):
                try:
                    if call("GET", "/status")["ready"]:
                        break
                except (OSError, ValueError):
                    time.sleep(0.5)
            else:
                raise RuntimeError("Guest Firefox driver did not start")
            created = call("POST", "/session", {"capabilities": {"alwaysMatch": {
                "browserName": "firefox", "acceptInsecureCerts": False,
                "moz:firefoxOptions": {"args": ["-headless", "-profile", str(profile)]}}}})
            session = "/session/" + created["sessionId"]
            call("POST", session + "/window/rect", {"width": 1200, "height": 1000})
            call("POST", session + "/url", {"url": "http://192.168.50.10:8008/?run=" + run_id})
            call("POST", session + "/execute/sync", {"script": "submitNote(); return true", "args": []})
            text = ""
            for _ in range(30):
                text = call("POST", session + "/execute/sync", {"script": "return document.body.innerText", "args": []})
                if "received_note" in text or "Submission interrupted:" in text:
                    break
                time.sleep(0.3)
            (output / "browser.png").write_bytes(base64.b64decode(call("GET", session + "/screenshot")))
            (output / "browser.txt").write_text(text)
            return {"installed_firefox": True, "accept_insecure_certs": False,
                    "injected_banner_visible": "INJECTED by the lab attacker" in text,
                    "submission_modified_visible": "MODIFIED by the lab attacker" in text,
                    "submission_interrupted_visible": "Submission interrupted:" in text,
                    "submission_received_visible": "received_note" in text}
        finally:
            if session:
                try:
                    call("DELETE", session)
                except Exception:
                    pass
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            # Only this disposable, newly created profile; no student profiles.
            shutil.rmtree(profile)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("baseline", "observe", "modify", "inject", "drop", "recovery", "comparison"))
    parser.add_argument("run_id", type=valid_run)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    report = {"time": datetime.now(timezone.utc).isoformat(), "mode": args.mode,
              "run_id": args.run_id, "passed": False, "checks": {}}
    check = report["checks"]
    base = "http://192.168.50.10:8008"
    try:
        lesson = json.loads(request(base + "/api/lesson", args.run_id))
        check["unrelated_request_succeeds"] = lesson["lesson"] == "ORIGINAL server lesson"
        original = "ORIGINAL synthetic study note " + args.run_id
        report["submitted_note"] = original
        try:
            reply = json.loads(request(base + "/api/submit", args.run_id,
                                      {"run_id": args.run_id, "note": original}))
            report["received_note"] = reply["received_note"]
            expected = "MODIFIED by the lab attacker" if args.mode == "modify" else original
            check["submission"] = args.mode != "drop" and reply["received_note"] == expected
        except (OSError, urllib.error.URLError) as exc:
            report["submission_error"] = type(exc).__name__ + ": " + str(exc)
            check["submission"] = args.mode == "drop"
        html = request(base + "/?run=" + args.run_id, args.run_id)
        check["response_injection"] = ("id=\"mitm-banner\"" in html) == (args.mode == "inject")
        # Uses system CA trust, exact hostname, and normal TLS verification.
        health = json.loads(request("https://smart-learning.test/api/health", args.run_id))
        report["https_health"] = health
        check["https_verified"] = bool(health)  # HTTP errors already raise.
        report["tls_verification_enabled"] = True
        report["browser"] = browser(args.run_id, args.output)
        observed = report["browser"]
        check["browser_injection"] = observed["injected_banner_visible"] == (args.mode == "inject")
        check["browser_submission"] = (observed["submission_interrupted_visible"] if args.mode == "drop"
            else observed["submission_received_visible"] and
            (observed["submission_modified_visible"] == (args.mode == "modify")))
        report["addresses"] = json.loads(subprocess.check_output(["ip", "-j", "address"], text=True))
        report["neighbours"] = json.loads(subprocess.check_output(["ip", "-j", "neigh"], text=True))
        report["passed"] = all(check.values())
    except Exception as exc:
        report["error"] = type(exc).__name__ + ": " + str(exc)
    finally:
        (args.output / "client-report.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report))
    raise SystemExit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()
