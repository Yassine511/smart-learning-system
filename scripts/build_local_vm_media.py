"""Build read-only VirtualBox setup media from source, excluding secrets/state."""
import argparse
from datetime import datetime, timezone
import hashlib
import io
from pathlib import Path
import subprocess
import tarfile
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
FILES = ("compose.yaml", "compose.lab.yaml", "compose.local-prototype.yaml")
FOLDERS = ("backend", "frontend", "deploy", "scripts", "docs")
EXCLUDED = {".git", ".venv", "node_modules", "dist", "build", "output", "__pycache__", ".pytest_cache", "data", "uploads", "backups", "secrets", "models", ".mypy_cache", ".ruff_cache", ".idea", ".vscode"}


def permitted(path: Path) -> bool:
    relative = path.relative_to(ROOT)
    return not (path.is_symlink() or any(p in EXCLUDED or p.endswith(".egg-info") for p in relative.parts)
                or path.name.startswith(".env") or path.name == ".demo-credentials.json"
                or path.suffix.lower() in {".pem", ".key", ".gguf", ".pcap", ".pcapng", ".log", ".pyc", ".tsbuildinfo", ".sqlite", ".sqlite3", ".dump"})


def git(*arguments: str) -> str:
    try:
        return subprocess.run(["git", "-c", f"safe.directory={ROOT.as_posix()}", *arguments], cwd=ROOT,
                              capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return ""


def add_bytes(archive: tarfile.TarFile, name: str, data: bytes) -> None:
    info = tarfile.TarInfo(name)
    info.size, info.mode = len(data), 0o644
    archive.addfile(info, io.BytesIO(data))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--public-key", type=Path)
    args = parser.parse_args()
    output = args.output.resolve()
    if not output.is_relative_to(ROOT / "backups" / "local-three-vm"):
        raise SystemExit("Output must stay inside this checkout's backups/local-three-vm directory")
    output.mkdir(parents=True, exist_ok=True)
    paths = [ROOT / name for name in FILES]
    for folder in FOLDERS:
        paths.extend(p for p in (ROOT / folder).rglob("*") if p.is_file() and permitted(p))
    # The manifest lets VM1 prove its deployed source is exactly this revision;
    # deploy/local-prototype/update-source.sh and tune-cpu.sh check it.
    commit = git("rev-parse", "HEAD") or "unknown"
    dirty = bool(git("status", "--porcelain", "--untracked-files=no", "--", *FILES, *FOLDERS))
    manifest = []
    with tarfile.open(output / "source.tar.gz", "w:gz") as archive:
        for path in sorted(paths):
            data = path.read_bytes()
            if path.suffix in {".sh", ".py"}: data = data.replace(b"\r\n", b"\n")
            name = path.relative_to(ROOT).as_posix()
            add_bytes(archive, name, data)
            manifest.append(f"{hashlib.sha256(data).hexdigest()}  {name}\n")
        add_bytes(archive, "deploy-manifest.sha256", "".join(manifest).encode())
        built = datetime.now(timezone.utc).isoformat(timespec="seconds")
        revision = f"commit {commit}\nuncommitted_changes {str(dirty).lower()}\nbuilt_at {built}\n"
        add_bytes(archive, "deploy-revision.txt", revision.encode())
    # VirtualBox synthesizes an ISO from this descriptor; no guest additions needed.
    arguments = ["--iprt-iso-maker-file-marker-bourne-sh", str(uuid4()),
                 "--volume-id", "SMART_LEARNING_LOCAL", "--file-mode=0444", "--dir-mode=0555"]
    mappings = [("source.tar.gz", output / "source.tar.gz"),
                ("install.sh", ROOT / "deploy/local-prototype/media-bootstrap.sh")]
    if args.public_key:
        key = args.public_key.read_text().strip()
        if not key.startswith("ssh-ed25519 "): raise SystemExit("Expected an Ed25519 public key")
        (output / "agent.pub").write_text(key + "\n")
        mappings.append(("agent.pub", output / "agent.pub"))
    for destination, source in mappings:
        value = f"/{destination}={source.resolve()}"
        arguments.append("'" + value.replace("'", "'\\''") + "'")
    media = output / "setup.viso"
    media.write_text(" ".join(arguments), encoding="utf-8")
    note = " plus uncommitted changes" if dirty else ""
    print(f"Built {media} with {len(paths)} source files from {commit}{note}; no secrets or model weights.")


if __name__ == "__main__": main()
