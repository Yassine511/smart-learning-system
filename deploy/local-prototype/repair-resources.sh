#!/usr/bin/env bash
# Grow the existing lab root LV if needed and collect stability diagnostics.
set -euo pipefail
[[ $EUID == 0 ]] || { echo 'Run with sudo in VM1.' >&2; exit 1; }
trap 'echo "Resource repair stopped at line $LINENO (exit $?). Review the output before retrying." >&2' ERR
[[ $(hostname) == smart-learning-server ]] || { echo 'Expected the local lab server.' >&2; exit 2; }
grep -qx server /var/lib/smart-learning-local-setup/role
grep -qx 'OLLAMA_CHAT_MODEL=qwen2.5:0.5b' /opt/smart-learning-local/.env
root_source=$(findmnt -n -o SOURCE /)
[[ "$root_source" == /dev/mapper/ubuntu--vg-ubuntu--lv ]] || { echo 'Unexpected root LV.' >&2; exit 2; }
[[ $(findmnt -n -o FSTYPE /) == ext4 ]] || { echo 'Expected ext4 root filesystem.' >&2; exit 2; }
exec 9>/run/lock/smart-learning-resource-repair.lock
flock -n 9 || { echo 'Another resource repair is running.' >&2; exit 2; }

echo '=== Storage before repair ==='
df -h /
vgs --units g -o vg_name,vg_size,vg_free ubuntu-vg 9>&-
lvs --units g -o lv_name,lv_size,lv_attr ubuntu-vg 9>&-
free_bytes=$(df --output=avail -B1 / | tail -1 | tr -d ' ')
if (( free_bytes < 3 * 1024 * 1024 * 1024 )); then
  lv_names=$(lvs --noheadings -o lv_name ubuntu-vg 9>&- | xargs)
  [[ "$lv_names" == ubuntu-lv ]] || { echo 'Unexpected additional LVs; preserve their reserved space.' >&2; exit 2; }
  vg_free=$(vgs --units b --nosuffix --noheadings -o vg_free ubuntu-vg 9>&- | awk '{printf "%.0f", $1}')
  (( vg_free >= 3 * 1024 * 1024 * 1024 )) || { echo 'Less than 3 GiB is unallocated in ubuntu-vg; no storage changes performed.' >&2; exit 2; }
  # Use only already-unallocated VG extents. No partition, virtual disk,
  # snapshot, Docker volume, model, or student file is removed or replaced.
  lvextend --size +3G --resizefs /dev/ubuntu-vg/ubuntu-lv 9>&-
fi
echo '=== Storage after repair ==='
df -h /
free_bytes=$(df --output=avail -B1 / | tail -1 | tr -d ' ')
(( free_bytes >= 3 * 1024 * 1024 * 1024 )) || { echo 'Storage headroom remains below 3 GiB.' >&2; exit 3; }

echo '=== Stability diagnostics (no system settings changed) ==='
date --utc --iso-8601=seconds
uptime
free -m
systemd-detect-virt || true
journalctl -k -b --no-pager --grep='soft lockup|clocksource|tsc|Hypervisor|hypervisor' -n 40
docker system df
echo 'Disk headroom verified. CPU stability still requires review and observation.'
