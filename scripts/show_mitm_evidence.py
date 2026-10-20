"""Open recorded MITM evidence in Wireshark with readable presentation settings.

This reads saved pcaps only. It does not boot VMs or start an attack.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
COLLECTION = ROOT / "backups/mitm-lab/evidence/20261020t080704-complete"
# Capture role, display filter, selected frame, and whether to follow TCP.
VIEWS = {
    "baseline-map": ("baseline", "client", "frame.number == 19 || frame.number == 23", 19, False),
    "baseline-stream": ("baseline", "client", "tcp.stream == 1", 19, True),
    "arp": ("observe", "attacker", "arp && frame.number <= 6", 5, False),
    "observe-stream": ("observe", "attacker", "tcp.stream == 2", 34, True),
    "modify-client": ("modify", "client", "tcp.stream == 1", 21, True),
    "modify-server": ("modify", "server", "tcp.stream == 1", 20, True),
    "inject-client": ("inject", "client", "tcp.stream == 2", 38, True),
    "inject-server": ("inject", "server", "tcp.stream == 2", 33, True),
    "drop-client": ("drop", "client", "tcp.stream == 0 || tcp.stream == 1", 22, False),
    "drop-server": ("drop", "server", "http", 9, False),
    "tls": ("observe", "attacker", "tcp.port == 443 && tls", 96, False),
    "recovery-map": ("recovery", "client", "frame.number == 17 || frame.number == 21", 17, False),
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("view", nargs="?", choices=VIEWS)
    parser.add_argument("--font-size", type=int, default=16, choices=range(12, 25))
    parser.add_argument("--describe", action="store_true", help="Print paths and filters without opening a window")
    args = parser.parse_args()
    if not args.view:
        parser.print_help()
        return
    mode, role, display_filter, frame, follow = VIEWS[args.view]
    session = json.loads((COLLECTION / "session.json").read_text(encoding="utf-8"))
    run = next(record for record in session["runs"] if record["mode"] == mode)
    capture = COLLECTION / run["run_id"] / role / "network.pcap"
    if not capture.is_file():
        raise SystemExit("Capture not found: " + str(capture))
    print("Capture:", capture)
    print("Display filter:", display_filter)
    print("Selected frame:", frame)
    if follow:
        print("In Wireshark: Analyze > Follow > TCP Stream (Ctrl+Alt+Shift+T).")
    if args.describe:
        return
    wireshark = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Wireshark/Wireshark.exe"
    if not wireshark.is_file():
        raise SystemExit("Installed Wireshark not found: " + str(wireshark))
    preferences = [
        "gui.qt.font_name:Consolas," + str(args.font_size) + ",-1,5,400,0,0,0,0,0,0,0,0,0,0,1",
        'gui.column.format:"No.","%m","Source","%s","Destination","%d","Protocol","%p","Info","%i"',
        "gui.layout_type:1", "gui.layout_content_3:NONE", "gui.packet_list_show_minimap:FALSE",
        "gui.window_title:MITM " + args.view + " - " + role + " - " + run["run_id"],
    ]
    command = [str(wireshark), "-n", "-r", str(capture), "-d", "tcp.port==8008,http",
               "-Y", display_filter, "-g", str(frame)]
    for preference in preferences:
        command.extend(("-o", preference))
    process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, close_fds=True)
    print("Wireshark PID:", process.pid)


if __name__ == "__main__":
    main()
