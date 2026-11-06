#!/usr/bin/env python3
"""Run every shipped CPU-only test suite in its required isolated process."""
import argparse
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lint", action="store_true", help="also check Python errors and shell syntax")
    args = parser.parse_args()
    checks = [
        (ROOT / "forge", [sys.executable, "-m", "pytest", "-q", "tests"]),
        (ROOT / "forge", [sys.executable, "-m", "unittest", "discover", "-s", "tests_unittest"]),
        (ROOT, [sys.executable, "-m", "pytest", "-q", "coder-models", "lab-dashboard"]),
    ]
    if args.lint:
        checks.append((ROOT, [sys.executable, "-m", "ruff", "check", "--select", "E9,F63,F7,F82", "."]))
        checks.extend((ROOT, ["bash", "-n", str(p)]) for p in sorted(ROOT.rglob("*.sh")))
    failed = False
    for directory, command in checks:
        print(f"\n[{directory.relative_to(ROOT)}] {' '.join(command)}", flush=True)
        result = subprocess.run(command, cwd=directory)
        failed |= result.returncode != 0
    return int(failed)


if __name__ == "__main__":
    sys.exit(main())
