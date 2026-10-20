"""Focused unit and real loopback HTTP tests. These do not claim VM acceptance."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import threading
from types import SimpleNamespace, ModuleType
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import attacker
import demo_server
import analyze
import host


def load_addon():
    module = ModuleType("mitmproxy")
    module.ctx = SimpleNamespace(options=None)
    spec = importlib.util.spec_from_file_location("lab_addon_test", HERE / "addon.py")
    addon = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {"mitmproxy": module}):
        spec.loader.exec_module(addon)
    return addon


addon_module = load_addon()


class Flow:
    def __init__(self, host="192.168.50.10", port=8008, run="test-1", method="POST", path="/api/submit"):
        self.request = SimpleNamespace(host=host, port=port, scheme="http", method=method,
            pretty_url=f"http://{host}:{port}{path}", path=path, headers={"X-MITM-Run": run},
            text=json.dumps({"run_id": run, "note": "ORIGINAL unique note"}))
        self.request.get_text = lambda: self.request.text
        self.response = SimpleNamespace(content=demo_server.PAGE)
        self.killed = False

    def kill(self):
        self.killed = True


class ProxyTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.log = Path(self.temporary.name) / "events.jsonl"
        addon_module.ctx.options = SimpleNamespace(lab_mode="observe", lab_run="test-1", lab_log=str(self.log))
        self.addon = addon_module.LabAddon()

    def test_observe_preserves_body_and_records_synthetic_note(self):
        flow = Flow()
        before = flow.request.text
        self.addon.request(flow)
        self.assertEqual(before, flow.request.text)
        self.assertFalse(flow.killed)
        self.assertEqual(json.loads(self.log.read_text())["submitted_note"], "ORIGINAL unique note")

    def test_modification_preserves_run_and_changes_forwarded_note(self):
        addon_module.ctx.options.lab_mode = "modify"
        flow = Flow()
        self.addon.request(flow)
        self.assertEqual(json.loads(flow.request.text), {"run_id": "test-1", "note": "MODIFIED by the lab attacker"})

    def test_injection_adds_content_without_replacing_original_page(self):
        addon_module.ctx.options.lab_mode = "inject"
        flow = Flow(method="GET", path="/")
        self.addon.response(flow)
        self.assertIn(addon_module.BANNER, flow.response.content)
        self.assertIn(b"Original server lesson", flow.response.content)

    def test_drop_kills_only_selected_submission(self):
        addon_module.ctx.options.lab_mode = "drop"
        submission = Flow()
        lesson = Flow(method="GET", path="/api/lesson")
        self.addon.request(submission)
        self.addon.request(lesson)
        self.assertTrue(submission.killed)
        self.assertFalse(lesson.killed)
        self.assertIn("dropped_before_forwarding", self.log.read_text())

    def test_proxy_refuses_other_origins_ports_tls_and_runs(self):
        flows = [Flow(host="example.com"), Flow(port=443), Flow(run="different-run")]
        encrypted = Flow()
        encrypted.request.scheme = "https"
        flows.append(encrypted)
        for flow in flows:
            with self.subTest(flow=flow.request.pretty_url):
                self.addon.request(flow)
                self.assertTrue(flow.killed)
        self.assertFalse(self.log.exists())


class HttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.server = demo_server.ThreadingHTTPServer(("127.0.0.1", 0), demo_server.Handler)
        cls.server.events = Path(cls.temporary.name) / "events.jsonl"
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = "http://127.0.0.1:" + str(cls.server.server_port)
        cls.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()
        cls.temporary.cleanup()

    def fetch(self, path, data=None, run="http-test"):
        request = urllib.request.Request(self.base + path, None if data is None else json.dumps(data).encode(),
            {"X-MITM-Run": run, "Content-Type": "application/json"})
        return self.opener.open(request, timeout=3)

    def test_real_http_round_trip_and_server_log(self):
        with self.fetch("/api/submit", {"run_id": "http-test", "note": "ORIGINAL HTTP"}) as response:
            self.assertEqual(json.load(response)["received_note"], "ORIGINAL HTTP")
        self.assertIn('"note": "ORIGINAL HTTP"', self.server.events.read_text())

    def test_original_page_has_no_injected_banner(self):
        with self.fetch("/") as response:
            body = response.read()
            self.assertNotIn(addon_module.BANNER, body)
            self.assertIn(b"This separate HTTP page", body)

    def test_mismatched_run_is_rejected(self):
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.fetch("/api/submit", {"run_id": "other", "note": "not accepted"})
        self.assertEqual(error.exception.code, 400)

    def test_real_api_is_not_exposed_by_teaching_service(self):
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.fetch("/api/login")
        self.assertEqual(error.exception.code, 404)


class ControllerTests(unittest.TestCase):
    def test_proxy_start_failure_restores_saved_sysctls_without_poisoning(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            def fake_command(*args, **kwargs):
                return subprocess.CompletedProcess(args, 1 if "-S" in args else 0, "", "")
            with patch.object(attacker, "BASE", base), patch.object(attacker, "STATE", base / "state.json"), \
                 patch.object(attacker, "interface_for", return_value=("lab0", "08:00:27:00:00:13")), \
                 patch.object(attacker, "resolve_peers", return_value={attacker.SERVER: "08:00:27:00:00:10", attacker.CLIENT: "08:00:27:00:00:11"}), \
                 patch.object(attacker, "command", side_effect=fake_command), \
                 patch.object(attacker, "sysctl_read", return_value="0"), \
                 patch.object(attacker, "sysctl_write") as sysctl, \
                 patch.object(attacker, "remove_rules", return_value=[]) as remove, \
                 patch.object(attacker, "arp_pair") as arp, patch.object(attacker.subprocess, "Popen") as process:
                process.return_value.poll.return_value = 1
                with self.assertRaisesRegex(RuntimeError, "failed before ARP"):
                    attacker.run("observe", "startup-failure")
                self.assertEqual(sysctl.call_count, 6)
                remove.assert_called_once()
                arp.assert_not_called()
                self.assertFalse(attacker.STATE.exists())

    def test_arbitrary_targets_and_path_traversal_are_rejected(self):
        with self.assertRaises(ValueError):
            attacker.validate_peers("192.168.1.1", attacker.CLIENT, attacker.ATTACKER)
        for value in ("../secrets", "/tmp", "test;reboot", "UPPERCASE", "x" * 65):
            with self.subTest(value=value), self.assertRaises(ValueError):
                demo_server.valid_run(value)

    def test_firewall_redirect_is_limited_to_fixed_http_tuple(self):
        rules = attacker.rules("enp0s8")
        redirect = next(rule for rule in rules if "PREROUTING" in rule)
        for value in ("enp0s8", attacker.CLIENT, attacker.SERVER, "8008"):
            self.assertIn(value, redirect)
        self.assertNotIn("443", redirect)
        self.assertFalse(any("-F" in rule for rule in rules))

    def test_redirect_port_rule_declares_tcp_inside_its_own_chain(self):
        # Protocol matches on a calling chain do not supply the extension
        # context needed to parse REDIRECT --to-ports in this separate rule.
        redirect = next(rule for rule in attacker.rules("lab0") if "--to-ports" in rule)
        self.assertEqual(redirect[redirect.index("-p") + 1], "tcp")

    def test_recovery_repairs_peers_and_restores_only_saved_state(self):
        with tempfile.TemporaryDirectory() as temporary:
            state_file = Path(temporary) / "state.json"
            state_file.write_text(json.dumps({"poisoning": True, "rules": attacker.rules("lab0"),
                "chains": [["nat", "SLMITM_NAT"], ["filter", "SLMITM_FWD"]],
                "sysctl": {"net.ipv4.ip_forward": "0", "net.ipv4.conf.all.send_redirects": "1"}}))
            with patch.object(attacker, "STATE", state_file), patch.object(attacker, "arp_pair") as arp, \
                 patch.object(attacker, "sysctl_write") as sysctl, patch.object(attacker, "command") as command:
                command.return_value = subprocess.CompletedProcess([], 0, "", "")
                attacker.recover()
                arp.assert_called_once()
                self.assertTrue(arp.call_args.kwargs["restore"])
                self.assertEqual(sysctl.call_count, 2)
                self.assertFalse(state_file.exists())
                before = command.call_count
                attacker.recover()
                self.assertEqual(command.call_count, before)
                commands = [call.args for call in command.call_args_list]
                self.assertFalse(any("-F" in args for args in commands))
                self.assertTrue(any("-X" in args and "SLMITM_NAT" in args for args in commands))

    def test_failed_recovery_keeps_state_for_retry(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "state.json"
            path.write_text(json.dumps({"poisoning": True}))
            with patch.object(attacker, "STATE", path), patch.object(attacker, "arp_pair", side_effect=OSError("link down")):
                with self.assertRaisesRegex(RuntimeError, "retained state"):
                    attacker.recover()
            self.assertTrue(path.exists())

    def test_interface_discovery_rejects_missing_or_ambiguous_addresses(self):
        result = subprocess.CompletedProcess([], 0, "[]", "")
        with patch.object(attacker, "command", return_value=result):
            with self.assertRaises(RuntimeError):
                attacker.interface_for(attacker.ATTACKER)


class HostTests(unittest.TestCase):
    def test_only_idempotent_remote_commands_retry_a_timeout(self):
        with patch.object(host, 'ssh_args', return_value=['ssh']), \
             patch.object(host, 'command', side_effect=[subprocess.TimeoutExpired('ssh', 1),
                 subprocess.CompletedProcess([], 0, b'{}', b'')]) as command:
            self.assertEqual(host.remote('server', 'status'), '{}')
            self.assertEqual(command.call_count, 2)
        with patch.object(host, 'ssh_args', return_value=['ssh']), \
             patch.object(host, 'command', side_effect=subprocess.TimeoutExpired('ssh', 1)) as command:
            with self.assertRaises(subprocess.TimeoutExpired):
                host.remote('attacker', 'attack-start modify test-run')
            self.assertEqual(command.call_count, 1)

    def test_packaged_addon_timestamp_triggers_real_script_watcher(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            lab = root / 'source'
            lab.mkdir()
            (lab / 'addon.py').write_text('addons = []\n')
            (lab / 'install.sh').write_text('#!/bin/bash\n')
            (root / 'lab').write_text('test key placeholder')
            (root / 'lab.pub').write_text('ssh-ed25519 test-placeholder')
            with patch.object(host, 'LAB', lab), patch.object(host, 'BACKUP', root), \
                 patch.object(host, 'command'), patch.object(host, 'info', return_value={'macaddress2': '08002722B7FA'}):
                host.build_media({})
            with tarfile.open(root / 'media/lab.tar.gz') as archive:
                self.assertGreater(archive.getmember('addon.py').mtime, 0)

    def test_insufficient_disk_refuses_before_startup(self):
        with patch.object(host, "info", return_value={}), \
             patch.object(host.shutil, "disk_usage", return_value=SimpleNamespace(free=1024**3)), \
             patch.object(host, "vbox") as vbox:
            with self.assertRaisesRegex(RuntimeError, "Need at least 2 GiB"):
                host.disk_guard(["VM4_Attacker"])
            vbox.assert_not_called()

    def test_browser_probe_uses_user_login_without_root_delegation(self):
        with patch.object(host, "user_connection", return_value=(["ssh", "-F", "lab.conf"], "sl-client1")), \
             patch.object(host, "command", return_value=subprocess.CompletedProcess([], 0, b'{"passed":true}', b"")) as command:
            self.assertIn('"passed":true', host.user_probe("client", "baseline", "valid-run"))
            arguments = command.call_args.args
            self.assertIn("sl-client1", arguments)
            self.assertIn("/opt/smart-mitm/client_probe.py baseline valid-run", arguments[-1])
            self.assertNotIn("root@", " ".join(map(str, arguments)))
            self.assertNotIn("sudo", arguments[-1])

    def test_user_probe_rejects_shell_input_in_run_id(self):
        with patch.object(host, "user_connection") as connection:
            with self.assertRaises(ValueError):
                host.user_probe("client", "baseline", "run;reboot")
            connection.assert_not_called()

    def test_known_hosts_option_quotes_paths_containing_spaces(self):
        with patch.object(host, "BACKUP", Path("C:/test folder/mitm lab")), patch.object(host, "verify_forward"):
            arguments = host.ssh_args("attacker", "status")
        option = next(str(argument) for argument in arguments if str(argument).startswith("UserKnownHostsFile="))
        self.assertEqual(option, 'UserKnownHostsFile="C:/test folder/mitm lab/known_hosts"')

    def test_failed_auth_does_not_pin_pre_install_host_key(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(host, "BACKUP", Path(temporary)), \
             patch.object(host, "ssh_args", return_value=["ssh"]):
            known = Path(temporary) / "known_hosts"
            known.write_bytes(b"previous valid key\n")
            def refused(*args, **kwargs):
                known.write_bytes(b"previous valid key\nnew pre-install key\n")
                return subprocess.CompletedProcess(args, 255, b"", b"Permission denied (publickey,password)")
            with patch.object(host, "command", side_effect=refused):
                with self.assertRaisesRegex(RuntimeError, "Permission denied"):
                    host.remote("attacker", "status")
            self.assertEqual(known.read_bytes(), b"previous valid key\n")

    def test_insufficient_memory_refuses_startup_without_vm_mutation(self):
        with patch.object(host, "info", return_value={"VMState": "poweroff", "memory": "1536"}), \
             patch.object(host, "free_memory_mb", return_value=2000), patch.object(host, "vbox") as vbox:
            with self.assertRaisesRegex(RuntimeError, "Need 2560 MB"):
                host.memory_guard(["VM4_Attacker"])
            vbox.assert_not_called()


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        for role in ("client", "server", "attacker"):
            (self.directory / role).mkdir()
        self.identities = {role: {"mac": mac} for role, mac in (
            ("client", "cc:cc:cc:cc:cc:cc"), ("server", "aa:aa:aa:aa:aa:aa"), ("attacker", "bb:bb:bb:bb:bb:bb"))}
        self.report = {"passed": True, "submitted_note": "ORIGINAL unique evidence", "tls_verification_enabled": True,
                       "checks": {"https_verified": True}}
        self.write_report()
        (self.directory / "client/browser.png").write_bytes(b"test screenshot placeholder, never submission evidence")
        (self.directory / "server/server-events.jsonl").write_text(json.dumps(
            {"method": "POST", "path": "/api/submit", "note": self.report["submitted_note"]}) + "\n")
        def packet(**values):
            return {**{field: "" for field in analyze.FIELDS}, "body": "", **values}
        self.rows = {
            "client": [packet(**{"http.request.method": "POST", "eth.dst": self.identities["attacker"]["mac"]}),
                       packet(**{"tls.record.content_type": "23", "tcp.dstport": "443"})],
            "server": [packet(**{"ip.src": attacker.ATTACKER, "http.request.method": "GET"})],
            "attacker": [packet(**{"arp.opcode": "2", "arp.src.proto_ipv4": attacker.SERVER,
                "arp.src.hw_mac": self.identities["attacker"]["mac"], "arp.dst.proto_ipv4": attacker.CLIENT}),
                packet(**{"arp.opcode": "2", "arp.src.proto_ipv4": attacker.CLIENT,
                "arp.src.hw_mac": self.identities["attacker"]["mac"], "arp.dst.proto_ipv4": attacker.SERVER}),
                packet(**{"tls.record.content_type": "23", "tcp.dstport": "443"}),
                packet(body=self.report["submitted_note"])]}

    def write_report(self):
        (self.directory / "client/client-report.json").write_text(json.dumps(self.report))

    def check(self, mode="observe"):
        return analyze.check_run(self.directory, {"mode": mode, "run_id": "unit-evidence"}, self.identities, self.rows)

    def test_claimed_guest_success_does_not_replace_both_arp_directions(self):
        self.assertTrue(all(self.check().values()))
        self.rows["attacker"].pop(1)
        self.assertFalse(self.check()["forged_arp_client"])

    def test_claimed_tls_success_does_not_replace_certificate_verification(self):
        self.report["tls_verification_enabled"] = False
        self.write_report()
        self.assertFalse(self.check()["verified_https"])

    def test_client_hello_alone_does_not_prove_encrypted_application_records(self):
        self.rows['attacker'][2]['tls.record.content_type'] = '22'
        self.assertFalse(self.check()['encrypted_tls_records_on_attacker'])

    def test_drop_cannot_pass_when_server_received_a_submission(self):
        checks = self.check("drop")
        self.assertFalse(checks["submission_absent_at_server"])

    def test_decoded_http_on_443_fails_encryption_claim(self):
        self.rows["attacker"][2]["http.request.method"] = "GET"
        self.assertFalse(self.check()["no_http_decoded_on_https"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
