#!/usr/bin/env python3
"""Create the complete tracked-source archive, excluding older download archives."""
import gzip
import hashlib
import io
import subprocess
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main():
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    paths = subprocess.check_output(["git", "ls-tree", "-r", "--name-only", "-z", commit], cwd=ROOT).split(b"\0")
    paths = [p.decode() for p in paths if p and not p.startswith(b"downloads/")]
    raw = subprocess.check_output(["git", "archive", "--format=tar", "--prefix=Forge/", commit, *paths], cwd=ROOT)
    buffer = io.BytesIO(raw)
    with tarfile.open(fileobj=buffer, mode="a") as archive:
        data = (commit + "\n").encode()
        entry = tarfile.TarInfo("Forge/SOURCE_COMMIT.txt")
        entry.size = len(data)
        entry.mode = 0o644
        archive.addfile(entry, io.BytesIO(data))
    output = ROOT / "downloads/forge-reviewed.tar.gz"
    output.parent.mkdir(exist_ok=True)
    with output.open("wb") as target:
        with gzip.GzipFile(filename="", fileobj=target, mode="wb", mtime=0) as compressed:
            compressed.write(buffer.getvalue())
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    output.with_suffix(output.suffix + ".sha256").write_text(digest + "  " + output.name + "\n")
    print(f"{output.name}: {output.stat().st_size} bytes; source {commit}; SHA256 {digest}")


if __name__ == "__main__":
    main()
