#!/usr/bin/env bash
# Resume local setup after its free-space preflight, without replacing the disk.
set -euo pipefail
[[ $EUID == 0 ]] || { echo 'Run with sudo.' >&2; exit 1; }
source_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
free_kb=$(df --output=avail -k /opt | tail -1 | tr -d ' ')
if (( free_kb < 8 * 1024 * 1024 )); then
  root_source=$(findmnt -n -o SOURCE /)
  [[ "$root_source" == /dev/mapper/ubuntu--vg-ubuntu--lv ]] || { echo 'Unexpected root LV; preserve it and request a disk repair.' >&2; exit 2; }
  [[ $(findmnt -n -o FSTYPE /) == ext4 ]] || { echo 'Expected ext4 root filesystem.' >&2; exit 2; }
  vg_free=$(vgs --units b --nosuffix --noheadings -o vg_free ubuntu-vg | awk '{printf "%.0f", $1}')
  (( vg_free >= 8 * 1024 * 1024 * 1024 )) || { echo 'The existing volume group lacks 8 GiB spare space; no disk changes performed.' >&2; exit 2; }
  lvextend --size +8G --resizefs /dev/ubuntu-vg/ubuntu-lv
fi
log=/var/log/smart-learning-local-setup.log
nohup bash "$source_dir/deploy/local-prototype/setup.sh" server "$source_dir" > "$log" 2>&1 < /dev/null &
echo "Server setup resumed (PID $!). Progress: tail -f $log"
