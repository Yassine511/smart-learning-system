#!/usr/bin/env bash
set -euo pipefail
[[ $EUID == 0 ]] || { echo 'Run with sudo.' >&2; exit 1; }
target=/opt/smart-learning-local
state=/var/lib/smart-learning-local-setup
grep -qx server "$state/role"
grep -qx 'OLLAMA_CHAT_MODEL=qwen2.5:0.5b' "$target/.env"
source_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
[[ -f "$state/cpu-tuning.env.original" ]] || cp -p "$target/.env" "$state/cpu-tuning.env.original"
install -m 0644 "$source_dir/config.py" "$target/backend/app/config.py"
install -m 0644 "$source_dir/ollama_chat.py" "$target/backend/app/services/ollama_chat.py"
install -m 0644 "$source_dir/vector_index.py" "$target/backend/app/services/vector_index.py"
if [[ -f "$source_dir/rag.py" && -f "$source_dir/messages.py" ]]; then
  install -d -m 0700 "$state/prompt-original"
  [[ -f "$state/prompt-original/rag.py" ]] || cp -p "$target/backend/app/services/rag.py" "$state/prompt-original/rag.py"
  [[ -f "$state/prompt-original/messages.py" ]] || cp -p "$target/backend/app/api/messages.py" "$state/prompt-original/messages.py"
  install -m 0644 "$source_dir/rag.py" "$target/backend/app/services/rag.py"
  install -m 0644 "$source_dir/messages.py" "$target/backend/app/api/messages.py"
fi
python3 - "$target/.env" <<'PY'
from pathlib import Path
import re, sys
p=Path(sys.argv[1])
text=p.read_text()
if re.search(r'^OLLAMA_NUM_THREADS=',text,re.M):
    text=re.sub(r'^OLLAMA_NUM_THREADS=.*$', 'OLLAMA_NUM_THREADS=1',text,flags=re.M)
else:
    text=text.rstrip()+'\nOLLAMA_NUM_THREADS=1\n'
p.write_text(text)
p.chmod(0o600)
PY
if [[ -f "$source_dir/rag.py" && -f "$source_dir/messages.py" ]]; then
  python3 - "$target/.env" <<'PY'
from pathlib import Path
import re, sys
p=Path(sys.argv[1]); text=p.read_text()
if re.search(r'^RAG_CITATION_EXAMPLE=',text,re.M):
    text=re.sub(r'^RAG_CITATION_EXAMPLE=.*$', 'RAG_CITATION_EXAMPLE=true',text,flags=re.M)
else:
    text=text.rstrip()+'\nRAG_CITATION_EXAMPLE=true\n'
p.write_text(text); p.chmod(0o600)
PY
fi
log=/var/log/smart-learning-local-cpu-tuning.log
touch "$log"
chown "${SUDO_USER:?Run from your normal guest sudo login}:$(id -gn "$SUDO_USER")" "$log"
chmod 0600 "$log"
nohup bash -c '
set -e
stack=/opt/smart-learning-local/deploy/local-prototype/stack.sh
bash "$stack" up -d --build --no-deps --wait --wait-timeout 120 backend worker
bash "$stack" restart web
curl --retry 10 --retry-connrefused --retry-delay 2 --fail --silent https://smart-learning.test/api/ready
echo
echo "Single-thread prototype inference deployed."
' > "$log" 2>&1 < /dev/null &
echo "CPU tuning started (PID $!). Progress: tail -f $log"
