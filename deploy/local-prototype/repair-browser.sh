#!/usr/bin/env bash
# Repair content providers left missing by an interrupted Firefox snap download.
set -euo pipefail
[[ $EUID == 0 ]] || { echo 'Run with sudo.' >&2; exit 1; }
guest_user=${SUDO_USER:?Run using your normal guest sudo login}
snap list firefox >/dev/null
for provider in gnome-46-2404 mesa-2404; do
  if ! snap list "$provider" >/dev/null 2>&1; then
    snap install "$provider"
  fi
done
snap connect firefox:gnome-46-2404 gnome-46-2404:gnome-46-2404
snap connect firefox:gpu-2404 mesa-2404:gpu-2404
echo "Firefox dependencies repaired for $guest_user."
