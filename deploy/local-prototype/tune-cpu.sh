#!/usr/bin/env bash
# Select one-thread inference and the weak-model citation example for the
# local prototype. This changes only VM1's .env; application source comes from
# a verified revision deployed by update-source.sh and is never patched here.
set -euo pipefail
[[ $EUID == 0 ]] || { echo 'Run with sudo.' >&2; exit 1; }
target=/opt/smart-learning-local
state=/var/lib/smart-learning-local-setup
grep -qx server "$state/role"
grep -qx 'OLLAMA_CHAT_MODEL=qwen2.5:0.5b' "$target/.env"
if [[ ! -f "$target/deploy-manifest.sha256" ]]; then
  echo 'The deployed source has no manifest, so it cannot be tied to a Git revision.' >&2
  echo 'Deploy a reviewed revision with update-source.sh first.' >&2
  exit 3
fi
(cd "$target" && sha256sum --quiet --strict -c deploy-manifest.sha256) || {
  echo 'Deployed source differs from its recorded revision; redeploy it with update-source.sh.' >&2
  exit 3
}
for setting in ollama_num_threads rag_citation_example; do
  grep -q "^    $setting:" "$target/backend/app/config.py" || {
    echo "The deployed revision does not support $setting; deploy a newer revision." >&2
    exit 4
  }
done
[[ -f "$state/cpu-tuning.env.original" ]] || cp -p "$target/.env" "$state/cpu-tuning.env.original"
python3 - "$target/.env" <<'PY'
from pathlib import Path
import re, sys
path = Path(sys.argv[1])
text = path.read_text()
for key, value in (("OLLAMA_NUM_THREADS", "1"), ("RAG_CITATION_EXAMPLE", "true")):
    line = f"{key}={value}"
    if re.search(rf"^{key}=", text, re.M):
        text = re.sub(rf"^{key}=.*$", line, text, flags=re.M)
    else:
        text = text.rstrip() + "\n" + line + "\n"
path.write_text(text)
path.chmod(0o600)
PY
log=/var/log/smart-learning-local-cpu-tuning.log
touch "$log"
chown "${SUDO_USER:?Run from your normal guest sudo login}:$(id -gn "$SUDO_USER")" "$log"
chmod 0600 "$log"
nohup bash -c '
set -e
stack=/opt/smart-learning-local/deploy/local-prototype/stack.sh
# Recreate only the services that read .env; no image is rebuilt.
bash "$stack" up -d --no-deps --force-recreate --wait --wait-timeout 120 backend worker
curl --retry 10 --retry-connrefused --retry-delay 2 --fail --silent https://smart-learning.test/api/ready
echo
echo "Single-thread prototype inference selected in .env."
' > "$log" 2>&1 < /dev/null &
echo "CPU tuning started (PID $!). Progress: tail -f $log"
echo "Original .env: $state/cpu-tuning.env.original"
