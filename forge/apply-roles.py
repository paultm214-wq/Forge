#!/usr/bin/env python3
"""
apply-roles.py - put the machine roles of roles.toml into forge's config.

    python3 apply-roles.py                 # ~/forge/config.toml, shows the diff, asks
    python3 apply-roles.py --yes           # no question
    python3 apply-roles.py --dry-run       # the diff only
    python3 apply-roles.py --config PATH --roles PATH

Replaces exactly these sections of your config with roles.toml's: [coder],
every [[coders]], [triage], every [[images.servers]] and every
[[shapes.servers]]. Everything else - [planner], [images] settings, [loop],
[git], your comments - stays. The old file is kept as config.toml.bak-roles.
Run `forge check` afterwards.
"""

from __future__ import annotations

import argparse
import difflib
import re
import shutil
import sys
import tomllib
from pathlib import Path

REPLACED = ("coder", "coders", "triage", "images.servers", "shapes.servers")
HEADER = re.compile(r"^\s*\[\[?\s*([\w.\-]+)\s*\]\]?")


def blocks(text: str) -> list[tuple[str | None, str]]:
    """(table name, text) per table; comment lines just above a header go
    with that header's block."""
    lines = text.splitlines(True)
    out: list[list] = [[None, []]]
    for ln in lines:
        m = HEADER.match(ln)
        if m:
            # move the comment/blank lines that end the previous block here
            prev = out[-1][1]
            k = len(prev)
            while k > 0 and (prev[k - 1].lstrip().startswith("#")):
                k -= 1
            carried = prev[k:]
            del prev[k:]
            out.append([m.group(1), carried + [ln]])
        else:
            out[-1][1].append(ln)
    return [(name, "".join(body)) for name, body in out]


def apply(config: str, roles: str) -> str:
    body = "".join(t for n, t in blocks(roles) if n is not None)
    parts, inserted = [], False
    for name, text in blocks(config):
        if name in REPLACED:
            if not inserted:
                parts.append(body.rstrip("\n") + "\n\n")
                inserted = True
            continue
        parts.append(text)
    if not inserted:
        parts.append("\n" + body)
    return re.sub(r"\n{3,}", "\n\n", "".join(parts))


def main() -> int:
    here = Path(__file__).resolve().parent
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", type=Path, default=Path("~/forge/config.toml").expanduser())
    ap.add_argument("--roles", type=Path, default=here / "roles.toml")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--yes", action="store_true")
    a = ap.parse_args()
    old = a.config.read_text()
    new = apply(old, a.roles.read_text())
    try:
        tomllib.loads(new)
    except tomllib.TOMLDecodeError as exc:
        print(f"the result would not be valid TOML ({exc}) - nothing changed", file=sys.stderr)
        return 1
    diff = list(difflib.unified_diff(old.splitlines(True), new.splitlines(True),
                                     str(a.config), "with roles.toml"))
    if not diff:
        print("already up to date")
        return 0
    sys.stdout.writelines(diff)
    if a.dry_run:
        return 0
    if not a.yes and input("\nWrite this? [y/N] ").strip().lower() != "y":
        print("nothing changed")
        return 1
    shutil.copy2(a.config, a.config.with_name(a.config.name + ".bak-roles"))
    a.config.write_text(new)
    print(f"\nwrote {a.config} (the old one: {a.config.name}.bak-roles). Next: forge check")
    return 0


if __name__ == "__main__":
    sys.exit(main())
