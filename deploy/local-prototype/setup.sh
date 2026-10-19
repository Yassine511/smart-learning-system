#!/usr/bin/env bash
# Intended for the snapshotted local VirtualBox guests only.
set -euo pipefail
[[ $EUID == 0 ]] || { echo 'Run this script with sudo.' >&2; exit 1; }
role=${1:-}
source_dir=${2:-$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)}
case "$role" in
  server) address=192.168.50.10; nic_mac=08:00:27:d1:40:34 ;;
  client1) address=192.168.50.11; nic_mac=08:00:27:2e:ca:ac ;;
  client2) address=192.168.50.12; nic_mac=08:00:27:6d:d3:97 ;;
  *) echo 'Usage: sudo bash setup.sh server|client1|client2 [source_directory]' >&2; exit 2 ;;
esac
. /etc/os-release
printf 'Guest: %s; role: %s\n' "$PRETTY_NAME" "$role"
case "${ID:-}:${VERSION_ID:-}" in
  ubuntu:22.04|ubuntu:24.04|ubuntu:26.04) ;;
  *) echo 'Unsupported guest OS. Preserve it and request a specific repair; no OS replacement was performed.' >&2; exit 3 ;;
esac
nic=''
for device in /sys/class/net/*; do
  if [[ $(cat "$device/address") == "$nic_mac" ]]; then nic=${device##*/}; break; fi
done
[[ -n "$nic" ]] || { echo 'Expected lab adapter MAC not found; no network settings changed.' >&2; exit 4; }
state_dir=/var/lib/smart-learning-local-setup
install -d -m 0700 "$state_dir"
[[ -f "$state_dir/hosts.original" ]] || cp -p /etc/hosts "$state_dir/hosts.original"
ip_bin=$(command -v ip)
cat > /etc/systemd/system/smart-learning-local-network.service <<EOF
[Unit]
Description=Additional static address for Smart Learning local lab
Wants=network-online.target
After=network-online.target
[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=$ip_bin link set dev $nic up
ExecStart=$ip_bin address replace $address/24 dev $nic
ExecStop=$ip_bin address del $address/24 dev $nic
[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable --now smart-learning-local-network.service
python3 - <<'PY'
from pathlib import Path
p = Path('/etc/hosts')
lines = []
for line in p.read_text().splitlines():
    body, separator, comment = line.partition('#')
    words = body.split()
    if len(words) > 1 and 'smart-learning.test' in words[1:]:
        words = [words[0]] + [w for w in words[1:] if w != 'smart-learning.test']
        if len(words) == 1:
            if separator: lines.append('#' + comment)
            continue
        line = ' '.join(words) + ((' #' + comment) if separator else '')
    lines.append(line)
lines.append('192.168.50.10 smart-learning.test')
p.write_text('\n'.join(lines) + '\n')
PY
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y ca-certificates curl gnupg openssh-server python3 tcpdump
systemctl enable --now ssh

if [[ "$role" != server ]]; then
  apt-get install -y xfce4 lightdm firefox libnss3-tools python3-venv
  systemctl set-default graphical.target
  systemctl start lightdm
  install -d /opt/smart-learning-local-client
  cp -a "$source_dir/scripts/verify_three_vm.py" /opt/smart-learning-local-client/
  cp -a "$source_dir/deploy/local-prototype/trust-client.sh" /opt/smart-learning-local-client/
  cp -a "$source_dir/backend/tests/fixtures/networking_notes.pdf" /opt/smart-learning-local-client/
  python3 -m venv /opt/smart-learning-local-client/.venv
  /opt/smart-learning-local-client/.venv/bin/pip install 'httpx>=0.28,<1'
  echo 'Client network/browser setup complete. Next run trust-client.sh with the exported server root certificate.'
  exit 0
fi

free_kb=$(df --output=avail -k /opt | tail -1 | tr -d ' ')
(( free_kb >= 8 * 1024 * 1024 )) || { echo 'Server needs at least 8 GiB free for images, models, and data.' >&2; exit 5; }
if ! command -v docker >/dev/null; then
  for package in docker.io docker-compose docker-compose-v2 podman-docker containerd runc; do
    if dpkg-query -W -f='${Status}' "$package" 2>/dev/null | grep -q 'install ok installed'; then
      echo "Existing package $package conflicts with Docker CE; stop for a specific repair." >&2; exit 6
    fi
  done
  install -d -m 0755 /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker-smart-learning.asc
  chmod 0644 /etc/apt/keyrings/docker-smart-learning.asc
  cat > /etc/apt/sources.list.d/docker-smart-learning.sources <<EOF
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: ${UBUNTU_CODENAME:-$VERSION_CODENAME}
Components: stable
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/docker-smart-learning.asc
EOF
  apt-get update
  apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
fi
docker compose version >/dev/null || { echo 'Existing Docker lacks Compose v2; stop for a repair.' >&2; exit 6; }
systemctl enable --now docker
if ! command -v caddy >/dev/null; then
  curl -fsSL https://dl.cloudsmith.io/public/caddy/stable/gpg.key | gpg --dearmor --yes -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -fsSL https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt -o /etc/apt/sources.list.d/caddy-stable.list
  chmod 0644 /usr/share/keyrings/caddy-stable-archive-keyring.gpg /etc/apt/sources.list.d/caddy-stable.list
  apt-get update
  apt-get install -y caddy
  systemctl disable --now caddy
fi
for port in 443 8080; do
  if ss -ltnH "sport = :$port" | grep -q . && [[ ! -f "$state_dir/deployed" ]]; then
    echo "Port $port is already occupied; stop rather than replace its service." >&2; exit 7
  fi
done
target=/opt/smart-learning-local
install -d -m 0755 "$target"
if [[ "$source_dir" != "$target" ]]; then
  tar -C "$source_dir" -cf - compose.yaml compose.lab.yaml compose.local-prototype.yaml backend frontend deploy scripts docs |
    tar -C "$target" -xf -
fi
chmod +x "$target/deploy/local-prototype/stack.sh"
if [[ ! -f "$target/.env" ]]; then
  python3 - "$target" <<'PY'
from pathlib import Path
import os, secrets, sys
p = Path(sys.argv[1])
content = (p / 'deploy/local-prototype/env.example').read_text().replace('GENERATED_BY_SETUP', secrets.token_hex(24))
fd = os.open(p / '.env', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, 'w') as file: file.write(content)
PY
fi
stack="$target/deploy/local-prototype/stack.sh"
# Validate without printing the rendered database password.
bash "$stack" config --quiet
bash "$stack" up -d ollama
bash "$stack" exec -T ollama ollama pull qwen2.5:0.5b
bash "$stack" exec -T ollama ollama pull nomic-embed-text
bash "$stack" exec -T ollama ollama list
# The Ollama image has no curl. Query its private bridge address from this guest.
ollama_container=$(bash "$stack" ps -q ollama)
ollama_ip=$(docker inspect --format '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' "$ollama_container")
curl --fail --silent "http://$ollama_ip:11434/api/tags" > "$state_dir/model-tags.json"
python3 - "$target/.env" "$state_dir/model-tags.json" <<'PY'
from pathlib import Path
import json, re, sys
env, tags = Path(sys.argv[1]), json.loads(Path(sys.argv[2]).read_text())
embed = next(m for m in tags['models'] if m['name'] in ('nomic-embed-text', 'nomic-embed-text:latest'))
digest = embed['digest']
if not re.fullmatch('[a-fA-F0-9]{64}', digest): raise SystemExit('Invalid embedding digest')
text = env.read_text()
previous = re.search(r'^OLLAMA_EMBED_DIGEST=(.*)$', text, re.M)
if previous and previous.group(1) and previous.group(1).lower() != digest.lower():
    raise SystemExit('Embedding digest changed; explicitly reindex PDFs before accepting this change')
env.write_text(re.sub(r'^OLLAMA_EMBED_DIGEST=.*$', 'OLLAMA_EMBED_DIGEST=' + digest, text, flags=re.M))
env.chmod(0o600)
PY
bash "$stack" up --build -d
install -d -o caddy -g caddy -m 0700 /var/lib/caddy-smart-learning-local
cat > /etc/systemd/system/smart-learning-local-caddy.service <<EOF
[Unit]
Description=Local HTTPS for Smart Learning VM prototype
Requires=smart-learning-local-network.service
After=network-online.target smart-learning-local-network.service docker.service
[Service]
User=caddy
Group=caddy
Environment=XDG_DATA_HOME=/var/lib/caddy-smart-learning-local
ExecStart=$(command -v caddy) run --config $target/deploy/local-prototype/Caddyfile --adapter caddyfile
Restart=on-failure
AmbientCapabilities=CAP_NET_BIND_SERVICE
NoNewPrivileges=true
PrivateTmp=true
[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable --now smart-learning-local-caddy.service
root_cert=/var/lib/caddy-smart-learning-local/caddy/pki/authorities/local/root.crt
for attempt in {1..30}; do [[ -f "$root_cert" ]] && break; sleep 1; done
[[ -f "$root_cert" ]] || { echo 'Caddy did not generate its CA; inspect its service journal.' >&2; exit 8; }
install -d -m 0755 "$target/public"
install -m 0644 "$root_cert" "$target/public/smart-learning-local-root.crt"
install -m 0644 "$root_cert" /usr/local/share/ca-certificates/smart-learning-local.crt
update-ca-certificates
for attempt in {1..60}; do
  if curl --fail --silent https://smart-learning.test/api/ready > "$state_dir/readiness.json"; then break; fi
  sleep 2
done
curl --fail --silent https://smart-learning.test/api/ready >/dev/null
touch "$state_dir/deployed"
bash "$stack" exec -T backend python - < "$target/deploy/local-prototype/provision-demo.py"
# Provisioning writes only synthetic account credentials into the private upload
# volume. Export them to a private guest file for local acceptance checks.
bash "$stack" exec -T backend cat /data/uploads/.demo-credentials.json > "$target/.demo-credentials.json"
chmod 0600 "$target/.demo-credentials.json"
if [[ -n ${SUDO_USER:-} ]]; then chown "$SUDO_USER:$(id -gn "$SUDO_USER")" "$target/.demo-credentials.json"; fi
echo 'Server ready at https://smart-learning.test using local Qwen 0.5B.'
echo 'Synthetic demo accounts: student1@example.test and student2@example.test.'
echo "Passwords are in $target/.demo-credentials.json (private guest file, not logged)."
echo "Public CA certificate: $target/public/smart-learning-local-root.crt"
