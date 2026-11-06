#!/usr/bin/env python3
"""kiwix-forge-library.py - download the offline-library books forge uses.

Run it on any machine with Python 3 and the internet, writing to the folder
your kiwix-serve reads (the share behind /data on Ziggurat), e.g.:

    python3 kiwix-forge-library.py /Volumes/Media/Books/Zim            # macOS, share mounted
    python3 kiwix-forge-library.py /mnt/ziggurat/Media/Books/Zim --dry-run
    python3 kiwix-forge-library.py DEST --groups devdocs qa            # just these groups
    python3 kiwix-forge-library.py DEST --with-stackoverflow           # + Stack Overflow (115 GB)

For each book it looks up the newest English ZIM in the Kiwix catalog
(library.kiwix.org), downloads it (resuming a partial download), and checks
its size and SHA-256 against the published metalink before giving it its
real name. Books already present and complete are skipped; an older dated
version of a book is left alone (delete it yourself once the new one is
served). Restart the kiwix-serve container afterwards so it loads the new
books, then `forge check` on Gemini lists what forge can use.

Standard library only. Nothing is executed from what is downloaded.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

CATALOG = "https://library.kiwix.org/catalog/v2/entries"
UA = {"User-Agent": "kiwix-forge-library (forge offline docs)"}

GROUPS = {
    # DevDocs: reference docs for the languages and engines forge builds with (~0.2 GB together)
    "devdocs": ["devdocs_en_" + n for n in (
        "python godot love lua rust c cpp gcc cmake gnu-make bash git docker html css dom "
        "javascript http svg typescript playwright pygame sqlite flask werkzeug jinja numpy "
        "pytest requests opengl vulkan openjdk qt gtk tcl-tk threejs phaser man markdown "
        "node npm go postgresql mariadb duckdb vitest docker jq").split()],
    # Q&A for design, testing and engineering questions (~2.8 GB)
    "qa": [n + ".stackexchange.com_en_all" for n in (
        "gamedev softwareengineering ux codereview computergraphics sqa cs retrocomputing "
        "reverseengineering").split()],
    # Q&A for particular toolchains and the lab's install scripts (~9.7 GB)
    "toolchain-qa": ["blender.stackexchange.com_en_all", "askubuntu.com_en_all", "superuser.com_en_all"],
    # The Lua manual site (~0.4 GB)
    "docs": ["lua.org_en_all"],
    # Hardware, Linux and data Q&A for the device, Pi, microcontroller, SQL and
    # shell toolchains (5 Nov; sizes vary, several GB together)
    "hardware-qa": [n + ".stackexchange.com_en_all" for n in (
        "unix raspberrypi electronics arduino iot android dba networkengineering robotics "
        "security").split()] + ["serverfault.com_en_all"],
    # Reference wikis: Arch wiki, man pages, Minecraft wiki, computing articles
    "wikis": ["archlinux_en_all", "www.mankier.com_en_all", "minecraft.wiki_en_all",
              "wikipedia_en_computer"],
}
STACKOVERFLOW = ["stackoverflow.com_en_all"]


def get(url: str, timeout: float = 60) -> bytes:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def catalog() -> dict[str, dict]:
    """book name -> newest English entry {"url" (metalink), "updated", "title"}."""
    ns = {"a": "http://www.w3.org/2005/Atom"}
    root = ET.fromstring(get(CATALOG + "?" + urllib.parse.urlencode({"count": "-1", "lang": "eng"}), 300))
    out: dict[str, dict] = {}
    for e in root.findall("a:entry", ns):
        name = (e.findtext("a:name", "", ns) or "").strip()
        flavour = (e.findtext("a:flavour", "", ns) or "").strip()
        updated = (e.findtext("a:updated", "", ns) or "")[:10]
        meta4 = ""
        for link in e.findall("a:link", ns):
            if (link.get("rel") or "").endswith("acquisition/open-access") and \
                    (link.get("href") or "").endswith(".meta4"):
                meta4 = link.get("href")
        if not name or not meta4:
            continue
        # One flavour per book: prefer maxi, then none, then nopic.
        rank = {"maxi": 0, "": 1, "nopic": 2}.get(flavour, 3)
        cur = out.get(name)
        if cur is None or rank < cur["rank"] or (rank == cur["rank"] and updated > cur["updated"]):
            out[name] = {"url": meta4, "updated": updated, "rank": rank,
                         "title": (e.findtext("a:title", "", ns) or name).strip()}
    return out


def metalink(url: str) -> dict:
    """{"name", "size", "sha256", "urls"} from a .meta4 file."""
    root = ET.fromstring(get(url))
    ns = {"m": "urn:ietf:params:xml:ns:metalink"}
    f = root.find("m:file", ns)
    if f is None:
        raise ValueError(f"no file in {url}")
    sha = next((h.text.strip() for h in f.findall("m:hash", ns) if h.get("type") == "sha-256"), "")
    urls = [u.text.strip() for u in sorted(f.findall("m:url", ns),
                                           key=lambda u: int(u.get("priority") or 99)) if u.text]
    urls.append(url[:-len(".meta4")])            # the load balancer itself, last
    name = f.get("name") or ""
    if not re.fullmatch(r"[\w.+-]+\.zim", name):
        raise ValueError(f"unexpected file name {name!r} in {url}")
    return {"name": name, "size": int(f.findtext("m:size", "0", ns)), "sha256": sha, "urls": urls}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def download(meta: dict, dest: Path) -> str:
    target = dest / meta["name"]
    part = dest / (meta["name"] + ".part")
    if target.exists() and target.stat().st_size == meta["size"]:
        return "present"
    last = None
    for url in meta["urls"]:
        for attempt in range(3):
            have = part.stat().st_size if part.exists() else 0
            if have > meta["size"]:
                part.unlink()
                have = 0
            if have == meta["size"]:
                break
            req = urllib.request.Request(url, headers={**UA, **({"Range": f"bytes={have}-"} if have else {})})
            try:
                with urllib.request.urlopen(req, timeout=120) as r:
                    mode = "ab" if have and r.status == 206 else "wb"
                    done = have if mode == "ab" else 0
                    t0, shown = time.monotonic(), 0.0
                    with open(part, mode) as fh:
                        while True:
                            block = r.read(1 << 20)
                            if not block:
                                break
                            fh.write(block)
                            done += len(block)
                            if time.monotonic() - shown > 2:
                                shown = time.monotonic()
                                rate = (done - (have if mode == "ab" else 0)) / max(0.1, shown - t0)
                                print(f"\r    {done / 1e6:9.1f} / {meta['size'] / 1e6:.1f} MB "
                                      f"({rate / 1e6:.1f} MB/s)", end="", flush=True)
                print()
            except (OSError, urllib.error.URLError) as exc:
                last = exc
                print(f"\n    {url}: {exc} - retrying", file=sys.stderr)
                time.sleep(5 * (attempt + 1))
                continue
            break
        if part.exists() and part.stat().st_size == meta["size"]:
            break
    if not part.exists() or part.stat().st_size != meta["size"]:
        raise RuntimeError(f"incomplete download ({last})")
    if meta["sha256"]:
        got = sha256(part)
        if got != meta["sha256"]:
            part.unlink()
            raise RuntimeError(f"SHA-256 mismatch ({got}) - deleted, run again")
    os.replace(part, target)
    return "downloaded"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dest", type=Path, help="the folder kiwix-serve reads (*.zim)")
    ap.add_argument("--groups", nargs="+", choices=sorted(GROUPS), default=sorted(GROUPS))
    ap.add_argument("--with-stackoverflow", action="store_true", help="also Stack Overflow (~115 GB)")
    ap.add_argument("--dry-run", action="store_true", help="list what would be downloaded")
    args = ap.parse_args(argv)
    if not args.dry_run:
        if not args.dest.is_dir():
            sys.exit(f"{args.dest} is not a folder - is the share mounted?")
        probe = args.dest / ".kiwix-forge-write-test"
        try:
            probe.write_text("x")
            probe.unlink()
        except OSError as exc:
            sys.exit(f"cannot write to {args.dest}: {exc}")
    names = [n for g in args.groups for n in GROUPS[g]] + (STACKOVERFLOW if args.with_stackoverflow else [])
    print("reading the Kiwix catalog ...")
    cat = catalog()
    plan, missing = [], []
    for n in names:
        e = cat.get(n)
        if e is None:
            missing.append(n)
            continue
        try:
            plan.append((n, e, metalink(e["url"])))
        except (OSError, ValueError, ET.ParseError) as exc:
            missing.append(f"{n} ({exc})")
    total = sum(m["size"] for _, _, m in plan)
    print(f"{len(plan)} books, {total / 1e9:.2f} GB in total"
          + (f"; not in the catalog: {', '.join(missing)}" if missing else ""))
    failed = []
    for i, (n, e, m) in enumerate(plan, 1):
        print(f"[{i}/{len(plan)}] {m['name']}  {m['size'] / 1e6:.1f} MB  ({e['title']})")
        if args.dry_run:
            continue
        try:
            print(f"    {download(m, args.dest)}")
        except RuntimeError as exc:
            print(f"    FAILED: {exc}", file=sys.stderr)
            failed.append(m["name"])
    if failed:
        print(f"\n{len(failed)} failed: {', '.join(failed)} - run the same command again to resume.")
        return 1
    if not args.dry_run:
        print("\nDone. Restart the kiwix-serve container so it loads the new books, then run "
              "`forge check` on Gemini.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
