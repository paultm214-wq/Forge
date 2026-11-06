"""Download only fixed HTTPS URLs with committed SHA256 checksums."""

import hashlib
import json
import sys
import urllib.request
from pathlib import Path

manifest = json.loads(Path(sys.argv[1]).read_text())
dest = Path(sys.argv[2])
dest.mkdir(parents=True, exist_ok=True)
for entry in manifest["files"]:
    name = entry["name"]
    if Path(name).name != name or not entry["url"].startswith("https://"):
        raise ValueError("unsafe artifact reference")
    with urllib.request.urlopen(entry["url"], timeout=60) as response:
        data = response.read(16 * 2**20)
    if hashlib.sha256(data).hexdigest() != entry["sha256"]:
        raise ValueError("artifact checksum mismatch: " + name)
    (dest / name).write_bytes(data)
