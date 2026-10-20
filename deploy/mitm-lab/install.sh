#!/usr/bin/env bash
# Run once interactively with sudo from this lab's read-only setup media.
set -euo pipefail
[[ $EUID == 0 ]] || { echo 'Run with sudo from the ordinary guest account.' >&2; exit 1; }
role=${1:-}
case "$role" in server|client|comparison|attacker) ;; *) echo 'Choose server|client|comparison|attacker' >&2; exit 2 ;; esac
guest_user=${SUDO_USER:?Run through sudo from the ordinary guest user}
guest_home=$(getent passwd "$guest_user" | cut -d: -f6)
media=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
[[ -f "$media/lab.tar.gz" && -f "$media/lab.pub" ]] || { echo 'Mount the MITM_LAB setup media first.' >&2; exit 3; }
python3 - "$media" <<'PY'
import hashlib, json, sys, tarfile
from pathlib import Path
media = Path(sys.argv[1])
manifest = json.loads((media / 'manifest.json').read_text())
assert hashlib.sha256((media / 'install.sh').read_bytes()).hexdigest() == manifest['install.sh'], 'Installer hash mismatch'
with tarfile.open(media / 'lab.tar.gz', 'r:gz') as archive:
    assert {m.name for m in archive.getmembers()} == set(manifest) - {'install.sh'}, 'Unexpected package contents'
    for member in archive.getmembers():
        assert member.isfile() and '/' not in member.name and member.name.endswith('.py'), 'Unsafe package member'
        assert hashlib.sha256(archive.extractfile(member).read()).hexdigest() == manifest[member.name], 'Source hash mismatch'
PY
. /etc/os-release
[[ ${ID:-} == ubuntu ]] || { echo 'Expected existing Ubuntu guest; no OS replacement.' >&2; exit 4; }
case "${VERSION_ID:-}" in 22.04|24.04|26.04) ;; *) echo 'Unsupported Ubuntu version; inspect before installing.' >&2; exit 4 ;; esac
if [[ "$role" != attacker ]]; then
  case "$role" in server) expected_ip=192.168.50.10 ;; client) expected_ip=192.168.50.11 ;; comparison) expected_ip=192.168.50.12 ;; esac
  ip -j address show | python3 -c 'import json,sys; expected=sys.argv[1]; matches=[i for i in json.load(sys.stdin) if any(a.get("local")==expected and a.get("prefixlen")==24 for a in i.get("addr_info",[]))]; sys.exit(0 if len(matches)==1 else 1)' "$expected_ip" || {
    echo "Guest does not have expected role address $expected_ip; no installation performed." >&2; exit 5;
  }
fi
if [[ "$role" == attacker && ! -f /etc/smart-mitm-identity-ready ]]; then
  # Host Prepare connects NIC2 only to a disconnected quarantine network.
  # Refuse to sanitize a source client accidentally selected by the operator.
  current_mac=$(cat /sys/class/net/enp0s8/address)
  expected_mac=$(tr -d '\r\n' < "$media/attacker-mac.txt" | tr '[:upper:]' '[:lower:]')
  [[ "$current_mac" == "$expected_mac" && "$current_mac" != 08:00:27:2e:ca:ac ]] || {
    echo 'Not the prepared attacker clone; no identity changes performed.' >&2; exit 5;
  }
  systemctl stop lightdm.service display-manager.service 2>/dev/null || true
  systemctl set-default multi-user.target
  hostnamectl set-hostname smart-learning-attacker
  sed -i 's/smart-learning-client1/smart-learning-attacker/g' /etc/hosts
  # These paths are copied clone state only, never the original client disk.
  rm -f /etc/machine-id /var/lib/dbus/machine-id
  systemd-machine-id-setup
  ln -sf /etc/machine-id /var/lib/dbus/machine-id
  rm -f /etc/ssh/ssh_host_*
  ssh-keygen -A
  install -d -m 0700 /var/lib/smart-mitm/pre-clone-network
  cp -a /etc/netplan/. /var/lib/smart-mitm/pre-clone-network/
  find /etc/netplan -maxdepth 1 -type f \( -name '*.yaml' -o -name '*.yml' \) -delete
  systemctl disable --now smart-learning-local-network.service 2>/dev/null || true
  nat_mac=$(cat /sys/class/net/enp0s3/address)
  cat > /etc/netplan/90-smart-mitm.yaml <<EOF
network:
  version: 2
  ethernets:
    attacker_nat:
      match: {macaddress: "$nat_mac"}
      dhcp4: true
      dhcp6: false
    attacker_lab:
      match: {macaddress: "$current_mac"}
      addresses: [192.168.50.13/24]
      dhcp4: false
      dhcp6: false
      link-local: []
EOF
  chmod 0600 /etc/netplan/90-smart-mitm.yaml
  # Copied student sessions, PDFs and private SSH credentials must not survive.
  [[ "$guest_home" == /home/* && "$guest_home" != /home/ && -d "$guest_home" ]] || exit 5
  # Empty only the verified independent clone's ordinary-user home, including
  # downloaded PDFs and old verification reports that may contain demo state.
  find "$guest_home" -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +
  rm -rf -- /opt/smart-learning-local-client /var/lib/smart-learning-local-evidence
  rm -f /root/.ssh/authorized_keys
  netplan generate
  netplan apply
  touch /etc/smart-mitm-identity-ready
fi
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y python3 tcpdump openssh-server
if [[ "$role" == attacker ]]; then
  apt-get install -y mitmproxy python3-scapy iptables
  id smart-mitm >/dev/null 2>&1 || useradd --system --home-dir /var/lib/smart-mitm/proxy --shell /usr/sbin/nologin smart-mitm
  install -d -o smart-mitm -g smart-mitm -m 0700 /var/lib/smart-mitm/proxy
fi
install -d -m 0755 /opt/smart-mitm
tar -xzf "$media/lab.tar.gz" -C /opt/smart-mitm
chown -R root:root /opt/smart-mitm
find /opt/smart-mitm -type d -exec chmod 0755 {} +
find /opt/smart-mitm -type f -exec chmod 0644 {} +
install -d -m 0711 /var/lib/smart-mitm
python3 - "$role" "$guest_user" "$guest_home" <<'PY'
import json, os, sys
from pathlib import Path
path = Path('/etc/smart-mitm.json')
path.write_text(json.dumps(dict(zip(('role','guest_user','guest_home'), sys.argv[1:]))))
os.chmod(path, 0o600)
PY
key=$(tr -d '\r\n' < "$media/lab.pub")
[[ "$key" == ssh-ed25519\ * ]] || { echo 'Invalid lab public key.' >&2; exit 6; }
install -d -m 0700 /root/.ssh
touch /root/.ssh/authorized_keys
entry="restrict,command=\"/usr/bin/python3 /opt/smart-mitm/admin.py\" $key"
grep -qxF "$entry" /root/.ssh/authorized_keys || printf '%s\n' "$entry" >> /root/.ssh/authorized_keys
chmod 0600 /root/.ssh/authorized_keys
if [[ "$role" == server ]]; then
  if ss -ltnH 'sport = :8008' | grep -q . && ! systemctl is-active --quiet smart-mitm-http; then
    echo 'Port 8008 occupied; existing service was not replaced.' >&2; exit 7
  fi
  id smart-mitm >/dev/null 2>&1 || useradd --system --home-dir /var/lib/smart-mitm --shell /usr/sbin/nologin smart-mitm
  touch /var/lib/smart-mitm/server-events.jsonl
  chown smart-mitm:smart-mitm /var/lib/smart-mitm/server-events.jsonl
  cat > /etc/systemd/system/smart-mitm-http.service <<'EOF'
[Unit]
Description=Synthetic MITM HTTP teaching page (separate from application)
After=network-online.target
Wants=network-online.target
[Service]
User=smart-mitm
ExecStart=/usr/bin/python3 /opt/smart-mitm/demo_server.py --log /var/lib/smart-mitm/server-events.jsonl
Restart=on-failure
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/var/lib/smart-mitm/server-events.jsonl
[Install]
WantedBy=multi-user.target
EOF
  systemctl daemon-reload
  systemctl enable --now smart-mitm-http
fi
systemctl enable --now ssh
systemctl restart ssh
echo "MITM lab installed for $role. Public host fingerprint:"
ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub
if [[ "$role" == attacker ]]; then
  echo 'Identity prepared in quarantine. Power off; host Start will attach NIC2 to labnet.'
fi
