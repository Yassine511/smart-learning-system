#!/usr/bin/env bash
set -euo pipefail
[[ $EUID == 0 ]] || { echo 'Run with sudo.' >&2; exit 1; }
role=${1:-}
case "$role" in
  server) name=smart-learning-server; mac=08:00:27:d1:40:34 ;;
  client1) name=smart-learning-client1; mac=08:00:27:2e:ca:ac ;;
  client2) name=smart-learning-client2; mac=08:00:27:6d:d3:97 ;;
  *) echo 'Choose server, client1, or client2.' >&2; exit 2 ;;
esac
grep -qx "$mac" /sys/class/net/*/address || { echo 'Expected VM adapter not found.' >&2; exit 3; }
guest_user=${SUDO_USER:?Run using your normal guest sudo login}
guest_home=$(getent passwd "$guest_user" | cut -d: -f6)
source_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
state=/var/lib/smart-learning-local-setup
install -d -m 0700 "$state"
printf '%s\n' "$role" > "$state/role"
printf '%s\n' "$guest_user" > "$state/guest-user"
install -d -m 0755 /usr/local/lib/smart-learning-local
install -m 0755 "$source_dir/admin.sh" "$source_dir/trust-client.sh" /usr/local/lib/smart-learning-local/
hostnamectl set-hostname "$name"
# These guests were cloned with the same machine ID. Assign one per VM once.
if [[ ! -f "$state/identity-initialized" ]]; then
  cp -p /etc/machine-id "$state/machine-id.original"
  python3 - <<'PY'
from pathlib import Path
from uuid import uuid4
identity = uuid4().hex + '\n'
Path('/etc/machine-id').write_text(identity)
dbus = Path('/var/lib/dbus/machine-id')
if dbus.exists() and not dbus.is_symlink(): dbus.write_text(identity)
PY
  touch "$state/identity-initialized"
fi
# A runtime-only address can disappear when network management reconfigures NICs.
# Retain NAT settings, correcting only a cloned adapter's stale MAC match.
python3 - "$role" "$mac" <<'PY'
from pathlib import Path
import subprocess, sys, yaml
role, mac = sys.argv[1:]
address = {'server': '192.168.50.10', 'client1': '192.168.50.11', 'client2': '192.168.50.12'}[role]
nic = next(p.name for p in Path('/sys/class/net').iterdir() if (p / 'address').read_text().strip() == mac)
current = yaml.safe_load(subprocess.check_output(['netplan', 'get'], text=True)) or {}
devices = current.get('network', {}).get('ethernets', {})
matches = [key for key, data in devices.items() if key == nic or data.get('match', {}).get('macaddress', '').lower() == mac or data.get('match', {}).get('name') == nic]
if len(matches) > 1: raise SystemExit('Ambiguous Netplan lab NIC definition; preserve it and request a repair')
key = matches[0] if matches else nic
content = {'network': {'version': 2, 'ethernets': {key: {'dhcp4': False, 'dhcp6': False, 'accept-ra': False, 'optional': True, 'addresses': [address + '/24']}}}}
expected_nat = {'server': '08:00:27:f0:14:ea', 'client1': '08:00:27:06:72:71', 'client2': '08:00:27:c0:a2:b7'}[role]
nat_nic = next((p.name for p in Path('/sys/class/net').iterdir() if (p / 'address').read_text().strip() == expected_nat), None)
if nat_nic is None: raise SystemExit('Expected NAT adapter not found; preserve its configuration')
nat_matches = [(k, d) for k, d in devices.items() if k == nat_nic or d.get('set-name') == nat_nic]
if len(nat_matches) != 1: raise SystemExit('Ambiguous NAT definition; preserve it for inspection')
nat_key, nat_data = nat_matches[0]
old_mac = nat_data.get('match', {}).get('macaddress')
if old_mac:
    content['network']['ethernets'][nat_key] = {'match': {'macaddress': expected_nat}}
    if old_mac.lower() != expected_nat:
        print(f'Correcting cloned NAT match: {old_mac} -> {expected_nat}')
path = Path('/etc/netplan/99-smart-learning-local.yaml')
path.write_text(yaml.safe_dump(content))
path.chmod(0o600)
PY
netplan generate
netplan apply
# Grant the host key only a forced command, not an unrestricted root shell.
key=$(tr -d '\r' < "$guest_home/.ssh/authorized_keys" | grep '^ssh-ed25519 .* smart-learning-local-automation$' | tail -1)
[[ -n "$key" ]] || { echo 'The bootstrap public key is missing.' >&2; exit 4; }
install -d -m 0700 /root/.ssh
touch /root/.ssh/authorized_keys
entry="restrict,command=\"/usr/local/lib/smart-learning-local/admin.sh\" $key"
grep -qxF "$entry" /root/.ssh/authorized_keys || printf '%s\n' "$entry" >> /root/.ssh/authorized_keys
chmod 0600 /root/.ssh/authorized_keys
echo 'Restricted lab administration key installed; unrestricted root commands are refused.'
echo 'Persistent lab address and unique guest identity configured; reboot after installation finishes.'
if [[ "$role" != server && -f "$guest_home/smart-learning-local-root.crt" ]]; then
  bash /usr/local/lib/smart-learning-local/trust-client.sh "$guest_home/smart-learning-local-root.crt" "$guest_user"
elif [[ "$role" != server ]]; then
  echo 'Browser trust will be completed after the server exports its public root certificate.'
fi
if [[ "$role" != server && ! -x /opt/smart-learning-local-client/.venv/bin/python ]]; then
  install -m 0755 "$source_dir/resume-client.sh" /usr/local/lib/smart-learning-local/
  systemd-run --unit=smart-learning-local-client-resume --collect \
    --setenv="SUDO_USER=$guest_user" /bin/bash /usr/local/lib/smart-learning-local/resume-client.sh "$role"
  echo 'Client installation resume scheduled; it waits for the current package operation.'
fi
