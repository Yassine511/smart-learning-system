"""Synthetic HTTP teaching service, independent of the Smart Learning API."""
import argparse
import json
import re
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

SERVER = "192.168.50.10"
PORT = 8008
RUN = re.compile(r"^[a-z0-9-]{1,64}$")
PAGE = b'''<!doctype html><html lang="en"><meta charset="utf-8">
<title>Smart Learning - MITM teaching lab</title>
<style>body{font:20px system-ui;max-width:850px;margin:60px auto;padding:24px;
background:#f4f1e9;color:#17252b}h1{font-size:42px}label,input,button{display:block;
margin:16px 0}input{font:inherit;width:90%;padding:12px}button{font:inherit;padding:12px}
pre{white-space:pre-wrap;background:white;padding:24px}aside{padding:24px;background:#ffdb70}</style>
<h1>Smart Learning: network teaching lab</h1>
<p>This separate HTTP page exchanges synthetic notes. The main application uses HTTPS.</p>
<p id="lesson">Original server lesson: ARP maps an IPv4 address to a local MAC address.</p>
<label>Synthetic note<input id="note" value="ORIGINAL synthetic study note"></label>
<button onclick="submitNote()">Send note</button><pre id="result">Ready to submit a synthetic note.</pre>
<script>const run=new URLSearchParams(location.search).get('run')||'manual';
async function submitNote(){try{const r=await fetch('/api/submit',{method:'POST',
headers:{'Content-Type':'application/json','X-MITM-Run':run},
body:JSON.stringify({run_id:run,note:document.querySelector('#note').value})});
document.querySelector('#result').textContent=JSON.stringify(await r.json(),null,2)}
catch(e){document.querySelector('#result').textContent='Submission interrupted: '+e.message}}</script>
</html>'''


def valid_run(value):
    if not RUN.fullmatch(value):
        raise ValueError("Invalid run ID")
    return value


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_):
        pass

    def respond(self, status, body, kind="application/json", run="manual", note=None):
        entry = {"time": datetime.now(timezone.utc).isoformat(), "run_id": run,
                 "peer": self.client_address[0], "method": self.command,
                 "path": urlsplit(self.path).path, "status": status}
        if note is not None:
            entry["note"] = note
        with self.server.events.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(entry) + "\n")
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(data)
        self.close_connection = True

    def run_id(self):
        query = parse_qs(urlsplit(self.path).query)
        return valid_run(self.headers.get("X-MITM-Run", query.get("run", ["manual"])[0]))

    def do_GET(self):
        try:
            run = self.run_id()
        except ValueError:
            self.respond(400, {"error": "invalid run ID"})
            return
        path = urlsplit(self.path).path
        if path == "/":
            self.respond(200, PAGE, "text/html; charset=utf-8", run)
        elif path == "/api/lesson":
            self.respond(200, {"run_id": run, "lesson": "ORIGINAL server lesson"}, run=run)
        else:
            self.respond(404, {"error": "unknown lab route"}, run=run)

    def do_POST(self):
        try:
            run = self.run_id()
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= 4096:
                raise ValueError("body size")
            data = json.loads(self.rfile.read(size))
            if data.get("run_id") != run or not isinstance(data.get("note"), str):
                raise ValueError("body")
            if len(data["note"]) > 1024:
                raise ValueError("note length")
        except (ValueError, TypeError, AttributeError):
            self.respond(400, {"error": "invalid synthetic submission"})
            return
        if urlsplit(self.path).path != "/api/submit":
            self.respond(404, {"error": "unknown lab route"}, run=run)
            return
        self.respond(200, {"run_id": run, "received_note": data["note"]}, run=run, note=data["note"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log", type=Path, required=True)
    args = parser.parse_args()
    args.log.parent.mkdir(parents=True, exist_ok=True)
    with ThreadingHTTPServer((SERVER, PORT), Handler) as server:
        server.events = args.log
        server.serve_forever()


if __name__ == "__main__":
    main()
