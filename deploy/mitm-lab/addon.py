"""mitmproxy addon: alter only the fixed synthetic lab origin and active run."""
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from mitmproxy import ctx

MODES = ("observe", "modify", "inject", "drop")
BANNER = b'<aside id="mitm-banner">INJECTED by the lab attacker: this banner was absent from VM1.</aside>'


class LabAddon:
    def load(self, loader):
        loader.add_option("lab_mode", str, "observe", "Synthetic demonstration mode")
        loader.add_option("lab_run", str, "manual", "Active synthetic run ID")
        loader.add_option("lab_log", str, "/var/lib/smart-mitm/proxy/flows.jsonl", "Private event log")

    def matches(self, flow):
        request = flow.request
        query = parse_qs(urlsplit(request.pretty_url).query)
        run = request.headers.get("X-MITM-Run", query.get("run", [""])[0])
        return (request.host == "192.168.50.10" and request.port == 8008
                and request.scheme == "http" and run == ctx.options.lab_run)

    def record(self, flow, action, **values):
        record = {"time": datetime.now(timezone.utc).isoformat(), "run_id": ctx.options.lab_run,
                  "mode": ctx.options.lab_mode, "action": action,
                  "method": flow.request.method, "path": urlsplit(flow.request.path).path, **values}
        with Path(ctx.options.lab_log).open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record) + "\n")

    def request(self, flow):
        if not self.matches(flow):
            # This proxy is not a general-purpose relay.
            flow.kill()
            return
        path = urlsplit(flow.request.path).path
        if flow.request.method == "POST" and path == "/api/submit":
            original = json.loads(flow.request.get_text())
            self.record(flow, "observed", submitted_note=original.get("note"))
            if ctx.options.lab_mode == "drop":
                self.record(flow, "dropped_before_forwarding")
                flow.kill()
            elif ctx.options.lab_mode == "modify":
                altered = dict(original, note="MODIFIED by the lab attacker")
                flow.request.text = json.dumps(altered)
                self.record(flow, "modified", original_note=original.get("note"), forwarded_note=altered["note"])

    def response(self, flow):
        if not self.matches(flow) or flow.response is None:
            return
        if ctx.options.lab_mode == "inject" and urlsplit(flow.request.path).path == "/":
            flow.response.content = flow.response.content.replace(b"</html>", BANNER + b"</html>")
            # Setting content/text lets mitmproxy update Content-Length.
            self.record(flow, "injected_response_banner")


addons = [LabAddon()]
