#!/usr/bin/env bash
set -euo pipefail
if [[ $EUID != 0 ]]; then
  echo 'Run with sudo bash /mnt/smart-learning/install.sh server|client1|client2' >&2
  exit 1
fi
case "${1:-}" in server|client1|client2) ;; *) echo 'Choose server, client1, or client2.' >&2; exit 2 ;; esac
media_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
work_dir=$(mktemp -d /tmp/smart-learning-local.XXXXXX)
tar -xzf "$media_dir/source.tar.gz" -C "$work_dir"
guest_user=${SUDO_USER:?Run from your ordinary guest account with sudo}
guest_home=$(getent passwd "$guest_user" | cut -d: -f6)
if [[ -f "$media_dir/agent.pub" ]]; then
  install -d -o "$guest_user" -g "$(id -gn "$guest_user")" -m 0700 "$guest_home/.ssh"
  touch "$guest_home/.ssh/authorized_keys"
  key=$(tr -d '\r' < "$media_dir/agent.pub")
  grep -qxF "$key" "$guest_home/.ssh/authorized_keys" || printf '%s\n' "$key" >> "$guest_home/.ssh/authorized_keys"
  chown "$guest_user:$(id -gn "$guest_user")" "$guest_home/.ssh/authorized_keys"
  chmod 0600 "$guest_home/.ssh/authorized_keys"
fi
log=/var/log/smart-learning-local-setup.log
touch "$log"
chown "$guest_user:$(id -gn "$guest_user")" "$log"
chmod 0600 "$log"
nohup bash "$work_dir/deploy/local-prototype/setup.sh" "$1" "$work_dir" > "$log" 2>&1 < /dev/null &
echo "Setup started (PID $!). Progress: tail -f $log"
