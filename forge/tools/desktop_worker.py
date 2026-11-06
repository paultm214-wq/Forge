#!/usr/bin/env python3
"""Native Qt workers. Build without keys; sign an approved artifact separately.

Run project builds on disposable, isolated platform workers. Environment filtering
is credential hygiene, not an operating-system sandbox.
"""

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for data in iter(lambda: source.read(2**20), b""):
            digest.update(data)
    return digest.hexdigest()


def native_platform():
    return {"Linux": "linux", "Darwin": "macos", "Windows": "windows"}[
        platform.system()
    ]


def run(command, cwd=None):
    allow = {
        "PATH",
        "HOME",
        "USERPROFILE",
        "SYSTEMROOT",
        "WINDIR",
        "COMSPEC",
        "TEMP",
        "TMP",
        "TMPDIR",
        "LANG",
        "LC_ALL",
        "PATHEXT",
        "PYTHONUTF8",
    }
    env = {k: v for k, v in os.environ.items() if k in allow}
    env["QT_QPA_PLATFORM"] = "offscreen"
    subprocess.run(command, cwd=cwd, env=env, check=True, timeout=1200)


def build(source, output, target="auto"):
    actual = native_platform()
    if target not in ("auto", actual):
        raise ValueError(
            target
            + " builds require a "
            + target
            + " native worker; this worker is "
            + actual
        )
    source, output = source.resolve(), output.resolve()
    if not (source / "app.py").is_file():
        raise ValueError("desktop project needs app.py")
    if any(p.is_symlink() for p in source.rglob("*")):
        raise ValueError("source symlinks are not accepted")
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="forge-desktop-") as temporary:
        project = Path(temporary) / "project"
        shutil.copytree(
            source,
            project,
            ignore=shutil.ignore_patterns(
                ".git",
                ".venv",
                "__pycache__",
                ".env",
                "*.key",
                "*.p12",
                "*.keystore",
                "dist",
                "build",
            ),
        )
        reports = project / "reports"
        reports.mkdir()
        run(
            [
                sys.executable,
                "-m",
                "pytest",
                "-q",
                "--junitxml",
                str(reports / "tests.xml"),
                "tests",
            ],
            cwd=project,
        )
        tests = [
            t
            for t in ET.parse(reports / "tests.xml").getroot().iter("testcase")
            if t.find("skipped") is None
        ]
        if not tests or any(
            t.find("failure") is not None or t.find("error") is not None for t in tests
        ):
            raise ValueError("actual passing GUI tests required")
        cmd = [
            sys.executable,
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--clean",
            "--onedir",
            "--name",
            "app",
        ]
        if actual == "macos":
            cmd += ["--windowed"]
        run([*cmd, "app.py"], cwd=project)
        binary = project / (
            "dist/app.app/Contents/MacOS/app"
            if actual == "macos"
            else "dist/app/" + ("app.exe" if actual == "windows" else "app")
        )
        run([str(binary), "--selftest"], cwd=project)
        artifact = output / f"desktop-{actual}-{platform.machine()}.tar.gz"
        with tarfile.open(artifact, "w:gz") as archive:
            for child in sorted((project / "dist").iterdir()):
                archive.add(child, arcname=child.name)
        record = dict(
            platform=actual,
            architecture=platform.machine(),
            tests_passed=len(tests),
            artifact=artifact.name,
            sha256=sha256(artifact),
            signed=False,
        )
        (output / "BUILD_PROVENANCE.json").write_text(json.dumps(record, indent=2))
        return record


def sign(artifact, output, expected, identity):
    if sha256(artifact) != expected:
        raise ValueError("artifact SHA256 differs from approved build")
    actual = native_platform()
    if actual not in ("macos", "windows"):
        raise ValueError("signing requires a macOS or Windows native worker")
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="forge-sign-") as temporary:
        root = Path(temporary)
        with tarfile.open(artifact) as archive:
            members = archive.getmembers()
            if len(members) > 100000 or sum(m.size for m in members) > 4 * 2**30:
                raise ValueError("artifact exceeds extraction limits")
            archive.extractall(root, filter="data")
        if actual == "macos":
            apps = list(root.glob("*.app"))
            if not apps:
                raise ValueError("no macOS app in artifact")
            for app in apps:
                subprocess.run(
                    [
                        "codesign",
                        "--force",
                        "--deep",
                        "--options",
                        "runtime",
                        "--sign",
                        identity,
                        str(app),
                    ],
                    check=True,
                    timeout=300,
                )
                subprocess.run(
                    ["codesign", "--verify", "--deep", "--strict", str(app)],
                    check=True,
                    timeout=300,
                )
        else:
            binaries = list(root.rglob("*.exe"))
            if not binaries:
                raise ValueError("no Windows executable in artifact")
            for binary in binaries:
                subprocess.run(
                    [
                        "signtool",
                        "sign",
                        "/sha1",
                        identity,
                        "/fd",
                        "SHA256",
                        str(binary),
                    ],
                    check=True,
                    timeout=300,
                )
                subprocess.run(
                    ["signtool", "verify", "/pa", str(binary)], check=True, timeout=300
                )
        signed = output / ("signed-" + artifact.name)
        with tarfile.open(signed, "w:gz") as archive:
            for child in sorted(root.iterdir()):
                archive.add(child, arcname=child.name)
        record = dict(
            platform=actual,
            source_sha256=expected,
            artifact=signed.name,
            sha256=sha256(signed),
            signed=True,
            notarized=False,
        )
        (output / "SIGNING_PROVENANCE.json").write_text(json.dumps(record, indent=2))
        return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    cmd = sub.add_parser("build")
    cmd.add_argument("project", type=Path)
    cmd.add_argument(
        "--platform", choices=["auto", "linux", "macos", "windows"], default="auto"
    )
    cmd.add_argument("--output", type=Path, required=True)
    cmd = sub.add_parser("sign")
    cmd.add_argument("artifact", type=Path)
    cmd.add_argument("--expected-sha256", required=True)
    cmd.add_argument(
        "--identity",
        required=True,
        help="certificate-store selector, never a private-key value",
    )
    cmd.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = (
            build(args.project, args.output, args.platform)
            if args.action == "build"
            else sign(args.artifact, args.output, args.expected_sha256, args.identity)
        )
        print(json.dumps(result))
        return 0
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        print(json.dumps(dict(error=str(error))))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
