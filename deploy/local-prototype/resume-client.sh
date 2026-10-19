#!/usr/bin/env bash
set -euo pipefail
[[ $EUID == 0 ]] || exit 1
case "${1:-}" in client1|client2) ;; *) exit 2 ;; esac
# Let the original installer finish before repairing an interrupted download.
while pgrep -x apt-get >/dev/null || pgrep -x dpkg >/dev/null; do sleep 5; done
[[ ! -x /opt/smart-learning-local-client/.venv/bin/python ]] || exit 0
log=/var/log/smart-learning-local-client-resume.log
touch "$log"
chown "$SUDO_USER:$(id -gn "$SUDO_USER")" "$log"
chmod 0600 "$log"
exec > "$log" 2>&1
export DEBIAN_FRONTEND=noninteractive
dpkg --configure -a
apt-get -o Acquire::Retries=3 -f install -y
[[ -r /mnt/smart-learning/source.tar.gz ]] || { echo 'Setup media must remain mounted.'; exit 3; }
work_dir=$(mktemp -d /tmp/smart-learning-client-resume.XXXXXX)
tar -xzf /mnt/smart-learning/source.tar.gz -C "$work_dir"
bash "$work_dir/deploy/local-prototype/setup.sh" "$1" "$work_dir"
