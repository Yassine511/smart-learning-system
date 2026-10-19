#!/usr/bin/env bash
# Replace VM1's deployed application source with the revision on the setup
# media, verify every file against the media manifest, and rebuild.
# .env, credentials, the public CA file, Docker volumes, and models are kept.
set -euo pipefail
[[ $EUID == 0 ]] || { echo 'Run with sudo.' >&2; exit 1; }
state=/var/lib/smart-learning-local-setup
target=/opt/smart-learning-local
[[ $(cat "$state/role" 2>/dev/null) == server ]] || { echo 'Run this on the lab server (VM1).' >&2; exit 2; }
grep -qx 'OLLAMA_CHAT_MODEL=qwen2.5:0.5b' "$target/.env" || { echo 'Expected the local prototype .env.' >&2; exit 2; }
media=${1:-/mnt/smart-learning}
[[ -r "$media/source.tar.gz" ]] || { echo "Mount the rebuilt setup media at $media first." >&2; exit 3; }
work=$(mktemp -d /tmp/smart-learning-update.XXXXXX)
trap 'rm -rf "$work"' EXIT
tar -xzf "$media/source.tar.gz" -C "$work"
[[ -f "$work/deploy-manifest.sha256" && -f "$work/deploy-revision.txt" ]] ||
  { echo 'This media has no deploy manifest; rebuild it with scripts/build_local_vm_media.py.' >&2; exit 4; }
(cd "$work" && sha256sum --quiet --strict -c deploy-manifest.sha256) ||
  { echo 'Media contents do not match their manifest; nothing was changed.' >&2; exit 5; }
echo 'Deploying:'
cat "$work/deploy-revision.txt"

items=(compose.yaml compose.lab.yaml compose.local-prototype.yaml backend frontend deploy scripts docs)
backup="$state/source-before-update-$(date -u +%Y%m%dT%H%M%SZ).tar.gz"
existing=()
for item in "${items[@]}" deploy-manifest.sha256 deploy-revision.txt; do
  [[ -e "$target/$item" ]] && existing+=("$item")
done
tar -C "$target" -czf "$backup" "${existing[@]}"
chmod 0600 "$backup"
echo "Previous source saved to $backup"

# Remove whole source folders so files deleted in Git do not linger on VM1,
# including any files patched by earlier out-of-band helpers.
for item in backend frontend deploy scripts docs; do rm -rf "${target:?}/$item"; done
tar -C "$work" -cf - "${items[@]}" deploy-manifest.sha256 deploy-revision.txt | tar -C "$target" -xf -
chmod +x "$target/deploy/local-prototype/stack.sh"
(cd "$target" && sha256sum --quiet --strict -c deploy-manifest.sha256)
# Earlier tune-cpu.sh runs kept copies of the files they overwrote; those
# copies no longer describe the deployment.
if [[ -d "$state/prompt-original" ]]; then
  mv "$state/prompt-original" "$state/prompt-original.superseded-$(date -u +%Y%m%dT%H%M%SZ)"
fi

stack="$target/deploy/local-prototype/stack.sh"
bash "$stack" config --quiet
bash "$stack" up -d --build --wait --wait-timeout 300
curl --retry 15 --retry-connrefused --retry-delay 2 --fail --silent https://smart-learning.test/api/ready
echo
echo 'Deployed source matches its manifest:'
cat "$target/deploy-revision.txt"
