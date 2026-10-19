#!/usr/bin/env bash
set -euo pipefail
[[ $EUID == 0 ]] || { echo 'Run with sudo.' >&2; exit 1; }
certificate=${1:?Provide the server root.crt copied over authenticated SSH}
guest_user=${2:-${SUDO_USER:-}}
[[ -n "$guest_user" && "$guest_user" != root ]] || { echo 'Provide your browser login username.' >&2; exit 2; }
openssl x509 -in "$certificate" -noout -subject -fingerprint -sha256
openssl x509 -in "$certificate" -noout -text | grep -q 'CA:TRUE' || { echo 'Expected a root CA certificate.' >&2; exit 3; }
install -m 0644 "$certificate" /usr/local/share/ca-certificates/smart-learning-local.crt
update-ca-certificates
guest_home=$(getent passwd "$guest_user" | cut -d: -f6)
found=0
for browser_root in "$guest_home/.mozilla/firefox" "$guest_home/snap/firefox/common/.mozilla/firefox"; do
  [[ -d "$browser_root" ]] || continue
  while IFS= read -r -d '' database; do
    profile=${database%/*}
    runuser -u "$guest_user" -- certutil -A -d "sql:$profile" -n smart-learning-local -t 'C,,' -i /usr/local/share/ca-certificates/smart-learning-local.crt
    found=1
  done < <(find "$browser_root" -type f -name cert9.db -print0)
done
if (( found == 0 )); then
  echo 'Launch Firefox once, close it, and rerun this command to install browser trust.' >&2
  exit 4
fi
curl --fail --silent https://smart-learning.test/api/ready
echo
echo 'System and Firefox trust verified. Open https://smart-learning.test.'
