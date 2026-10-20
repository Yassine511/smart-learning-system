"""Verify real QUERY search in guest Firefox and keep the browser open for evidence."""
import argparse
import base64
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import time

import httpx


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--student", type=int, choices=(1, 2), default=1)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--hold-seconds", type=int, default=900)
    parser.add_argument("--port", type=int, default=4445)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    common = Path.home() / "snap/firefox/common"
    profiles = list((common / ".mozilla/firefox").glob("*/cert9.db"))
    if not profiles:
        raise RuntimeError("The client's trusted Firefox profile is missing")
    account = json.loads((Path.home() / ".demo-credentials.json").read_text())[args.student - 1]
    report = {"passed": False, "browser": "visible guest Firefox", "accept_insecure_certs": False}
    driver = None
    prefix = None
    with tempfile.TemporaryDirectory(prefix="query-proof-", dir=common) as temp, args.output.with_suffix(".driver.log").open("w") as log:
        profile = Path(temp)
        for name in ("cert9.db", "key4.db", "pkcs11.txt"):
            source = profiles[0].parent / name
            if source.exists():
                shutil.copy2(source, profile / name)
        try:
            driver = subprocess.Popen(["/snap/bin/firefox.geckodriver", "--host", "127.0.0.1", "--port", str(args.port)], stdout=log, stderr=log)
            with httpx.Client(base_url=f"http://127.0.0.1:{args.port}", timeout=180, trust_env=False) as client:
                def call(method, path, data=None):
                    response = client.request(method, path, json=data)
                    response.raise_for_status()
                    value = response.json()["value"]
                    if isinstance(value, dict) and value.get("error"):
                        raise RuntimeError("WebDriver: " + value["error"])
                    return value

                for _ in range(60):
                    try:
                        if call("GET", "/status")["ready"]:
                            break
                    except httpx.HTTPError:
                        pass
                    time.sleep(1)
                created = call("POST", "/session", {"capabilities": {"alwaysMatch": {
                    "browserName": "firefox", "acceptInsecureCerts": False,
                    "moz:firefoxOptions": {"args": ["-profile", str(profile)], "prefs": {
                        "devtools.toolbox.host": "bottom", "devtools.toolbox.footer.height": 460,
                        "devtools.netmonitor.persistlog": True,
                    }},
                }}})
                prefix = "/session/" + created["sessionId"]
                control_file = args.output.with_suffix(".webdriver.json")
                control_file.write_text(json.dumps({"port": args.port, "prefix": prefix}))
                control_file.chmod(0o600)
                call("POST", prefix + "/window/maximize", {})
                call("POST", prefix + "/url", {"url": "https://smart-learning.test"})

                def find(selector):
                    elements = call("POST", prefix + "/elements", {"using": "css selector", "value": selector})
                    return elements[0]["element-6066-11e4-a52e-4f735466cecf"] if elements else None

                def wait_for(selector):
                    for _ in range(60):
                        element = find(selector)
                        if element:
                            return element
                        time.sleep(1)
                    raise RuntimeError("Element did not appear: " + selector)

                for name in ("email", "password"):
                    call("POST", prefix + "/element/" + wait_for(f'input[name="{name}"]') + "/value", {"text": account[name]})
                call("POST", prefix + "/element/" + find('form button[type="submit"]') + "/click", {})
                search = wait_for("#pdf-search")
                call("POST", prefix + "/execute/sync", {"script": "window.queryProof=[]; const original=window.fetch.bind(window); window.fetch=async function(input, options){ let record; if(input==='/api/documents/search'){record={url:input,method:options.method,body:JSON.parse(options.body)};window.queryProof.push(record);} try{const response=await original(...arguments);if(record)record.status=response.status;return response;}catch(error){if(record)record.error=String(error);throw error;} }; return true", "args": []})
                call("POST", prefix + "/element/" + search + "/value", {"text": "How does TCP deliver data reliably?"})
                call("POST", prefix + "/element/" + find('.document-search-form button[type="submit"]') + "/click", {})
                wait_for(".document-search-results .source-list a")
                call("POST", prefix + "/execute/sync", {"script": "document.querySelector('.document-search-form').scrollIntoView({block:'start'}); return true", "args": []})
                args.output.with_suffix(".png").write_bytes(base64.b64decode(call("GET", prefix + "/screenshot")))
                report["requests"] = call("POST", prefix + "/execute/sync", {"script": "return window.queryProof", "args": []})
                report["page_links"] = call("POST", prefix + "/execute/sync", {"script": "return Array.from(document.querySelectorAll('.document-search-results .source-list a'), a=>a.getAttribute('href'))", "args": []})
                if not report["requests"] or report["requests"][0]["method"] != "QUERY":
                    raise RuntimeError("The search form did not send QUERY")
                report["passed"] = True
                args.output.write_text(json.dumps(report, indent=2) + "\n")
                print(json.dumps(report), flush=True)
                deadline = time.monotonic() + args.hold_seconds
                while time.monotonic() < deadline and not args.output.with_suffix(".stop").exists():
                    time.sleep(1)
                call("POST", prefix + "/execute/async", {"script": "const done=arguments[0]; const csrf=document.cookie.split('; ').find(x=>x.startsWith('smart_learning_csrf='))?.split('=')[1]; fetch('/api/logout',{method:'POST',headers:{'X-CSRF-Token':csrf || ''}}).then(r=>done(r.status));", "args": []})
                call("DELETE", prefix)
        except Exception as exc:
            report.update(passed=False, error=type(exc).__name__ + ": " + str(exc))
            if prefix:
                try:
                    with httpx.Client(base_url=f"http://127.0.0.1:{args.port}", timeout=20, trust_env=False) as debug:
                        observed = debug.post(prefix + "/execute/sync", json={"script": "return {requests:window.queryProof, alerts:Array.from(document.querySelectorAll('[role=alert]'),e=>e.innerText), result:document.querySelector('.document-search-results')?.innerText, query:document.querySelector('#pdf-search')?.value}", "args": []})
                        report["diagnostic"] = observed.json().get("value")
                        screenshot = debug.get(prefix + "/screenshot").json().get("value")
                        if isinstance(screenshot, str):
                            args.output.with_suffix(".failure.png").write_bytes(base64.b64decode(screenshot))
                except Exception:
                    pass
            args.output.write_text(json.dumps(report, indent=2) + "\n")
            print(json.dumps(report), flush=True)
            deadline = time.monotonic() + args.hold_seconds
            while prefix and time.monotonic() < deadline and not args.output.with_suffix(".stop").exists():
                time.sleep(1)
        finally:
            args.output.with_suffix(".webdriver.json").unlink(missing_ok=True)
            if driver:
                driver.terminate()
                try:
                    driver.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    driver.kill()
                    driver.wait(timeout=10)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
