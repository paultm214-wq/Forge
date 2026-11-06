"""Fetch exact locks without evaluating an uploaded build script."""

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import urllib.request
from pathlib import Path


def requirements(text):
    from packaging.requirements import Requirement

    for line in text.replace("\\\n", " ").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        pinned, _, hashes = line.partition("--hash=")
        item = Requirement(pinned.strip())
        specifiers = list(item.specifier)
        if (
            item.url
            or len(specifiers) != 1
            or specifiers[0].operator != "=="
            or "*" in specifiers[0].version
        ):
            raise ValueError(
                "requirements must be exact pinned packages, not URLs or options"
            )
        words = ("--hash=" + hashes).split() if hashes else []
        if not words or not all(
            re.fullmatch(r"--hash=sha256:[a-f0-9]{64}", w) for w in words
        ):
            raise ValueError("each pinned requirement needs SHA256 hashes")
    return text


def maven(lock, dest, cache=Path("/cache"), offline=False):
    dest.mkdir(parents=True, exist_ok=True)
    for entry in lock["artifacts"]:
        coordinate, expected = entry["coordinate"], entry["sha256"]
        if (
            not re.fullmatch(
                r"[A-Za-z0-9_.-]+:[A-Za-z0-9_.-]+:[0-9][A-Za-z0-9_.-]*", coordinate
            )
            or "SNAPSHOT" in coordinate
        ):
            raise ValueError("fixed group:artifact:version required")
        if not re.fullmatch(r"[a-f0-9]{64}", expected):
            raise ValueError("SHA256 required")
        group, artifact, version = coordinate.split(":")
        target = dest / f"{expected[:16]}-{artifact}-{version}.jar"
        cached = cache / target.name
        if (
            cached.is_file()
            and hashlib.sha256(cached.read_bytes()).hexdigest() == expected
        ):
            shutil.copy2(cached, target)
            continue
        if offline:
            raise ValueError(
                "verified Maven artifact unavailable offline: " + coordinate
            )
        url = f"https://repo.maven.apache.org/maven2/{group.replace('.', '/')}/{artifact}/{version}/{artifact}-{version}.jar"
        partial = target.with_suffix(".part")
        digest = hashlib.sha256()
        try:
            with (
                urllib.request.urlopen(url, timeout=60) as source,
                partial.open("wb") as out,
            ):
                for block in iter(lambda: source.read(2**20), b""):
                    digest.update(block)
                    out.write(block)
            if digest.hexdigest() != expected:
                raise ValueError("Maven checksum mismatch")
            partial.replace(target)
            cache.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target, cached)
        finally:
            partial.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("kind", choices=["python", "maven"])
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args()
    if args.kind == "python":
        lock = Path("requirements.lock")
        if not lock.exists() and Path("uv.lock").exists():
            subprocess.run(
                [
                    "uv",
                    "export",
                    "--offline",
                    "--frozen",
                    "--no-dev",
                    "--no-emit-project",
                    "--format",
                    "requirements-txt",
                    "--output-file",
                    str(lock),
                ],
                check=True,
            )
        requirements(lock.read_text())
        command = [
            "python3",
            "-m",
            "pip",
            "download",
            "--only-binary",
            ":all:",
            "--require-hashes",
            "--dest",
            "/deps/wheels",
            "-r",
            str(lock),
        ]
        if args.offline:
            command += ["--no-index", "--find-links", "/cache"]
        subprocess.run(command, check=True)
        Path("/deps/requirements.lock").write_text(lock.read_text())
    else:
        maven(
            json.loads(Path("forge-dependencies.lock.json").read_text()),
            Path("/deps/jars"),
            offline=args.offline,
        )


if __name__ == "__main__":
    main()
