"""Real API facts for Java compile errors, from indexes made from the jars
(tools/java_api.py): the Paper API and MockBukkit for the minecraft
toolchain.

Like forge_dotnet for Mutagen: when javac says "cannot find symbol", the
next attempt, the diagnosis and the test audit get the members the type
really has (its supertypes and interfaces included), the package a class
really lives in, and the overloads a method really takes - never a guess.
"""
from __future__ import annotations

import difflib
import gzip
import json
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
INDEX_DIRS = {"minecraft": [Path("~/forge/minecraft").expanduser(), HERE / "minecraft"]}
_CACHE: dict[str, dict] = {}
NOISE = {"equals", "hashCode", "toString", "getClass", "notify", "notifyAll", "wait", "spigot"}


def _vkey(p: Path):
    return [int(x) if x.isdigit() else x for x in re.split(r"[.\-]", p.name)]


def load(toolchain: str) -> dict | None:
    """All indexes for the toolchain merged: {"types", "by_short",
    "packages", "names": [library names with versions]}. The installer's
    (~/forge/minecraft, made from the image's own jars) win over the ones
    shipped with forge; within a folder the newest of each library."""
    if toolchain in _CACHE:
        return _CACHE[toolchain] or None
    types: dict[str, dict] = {}
    names: list[str] = []
    seen_libs: set[str] = set()
    for d in INDEX_DIRS.get(toolchain, []):
        newest: dict[str, Path] = {}
        for p in sorted(d.glob("*-api.json.gz"), key=_vkey):
            lib = re.sub(r"-[\d.].*$", "", p.name)
            newest[lib] = p
        for lib, p in newest.items():
            if lib in seen_libs:
                continue
            try:
                doc = json.loads(gzip.open(p).read())
            except (OSError, ValueError):
                continue
            seen_libs.add(lib)
            names.append(f"{doc.get('name', lib)} {doc.get('version', '')}".strip())
            for k, v in doc.get("types", {}).items():
                types.setdefault(k, v)
    if not types:
        _CACHE[toolchain] = {}
        return None
    by_short: dict[str, list[str]] = {}
    for full in types:
        by_short.setdefault(full.rsplit(".", 1)[-1], []).append(full)
    packages = {pkg_of(full) for full in types}
    doc = {"types": types, "by_short": by_short, "packages": sorted(packages), "names": names}
    _CACHE[toolchain] = doc
    return doc


def pkg_of(full: str) -> str:
    """org.bukkit.Material.Foo -> org.bukkit (packages are lower case)."""
    parts = full.split(".")
    i = 0
    while i < len(parts) and parts[i][:1].islower():
        i += 1
    return ".".join(parts[:i])


def tidy(t: str) -> str:
    return re.sub(r"\b(?:[a-z_][\w]*\.)+(?=[A-Z])", "", t or "")


def find_types(doc: dict, name: str) -> list[str]:
    name = re.sub(r"<.*>$", "", (name or "").strip()).rstrip("[]")
    if name in doc["types"]:
        return [name]
    if "." in name:
        hits = [k for k in doc["types"] if k.endswith("." + name)]
        if hits:
            return hits
    return doc["by_short"].get(name.split(".")[-1], [])


def supertypes(doc: dict, full: str, limit: int = 40) -> list[str]:
    out, todo = [], [full]
    while todo and len(out) < limit:
        t = todo.pop(0)
        if t in out or t not in doc["types"]:
            continue
        out.append(t)
        e = doc["types"][t]
        todo += ([e["base"]] if e.get("base") else []) + list(e.get("interfaces") or [])
    return out


def members(doc: dict, full: str) -> tuple[list[str], list[str]]:
    """(fields, methods as 'name(params) -> ret') of a type and everything
    it extends or implements, each overload once."""
    fields, meths, seenf, seenm = [], [], set(), set()
    for t in supertypes(doc, full):
        e = doc["types"][t]
        for f in e.get("props", []):
            if f[0] not in seenf:
                seenf.add(f[0])
                fields.append(f"{f[0]}: {tidy(f[1])}")
        dep = set(e.get("deprecated") or [])
        for name, ret, params, static in e.get("methods", []):
            sig = f"{'static ' if static else ''}{name}({', '.join(tidy(p) for p in params)})"
            if name in NOISE or sig in seenm:
                continue
            seenm.add(sig)
            meths.append(f"{sig} -> {tidy(ret)}" + (" (deprecated)" if name in dep else ""))
    return fields, meths


def method_names(doc: dict, full: str) -> list[str]:
    names = []
    for t in supertypes(doc, full):
        for m in doc["types"][t].get("methods", []):
            if m[0] not in names:
                names.append(m[0])
    return names


def lib_names(doc: dict) -> str:
    return " and ".join(doc.get("names") or ["the library"])


def describe(doc: dict, full: str, limit: int = 45, only: str | None = None) -> str:
    e = doc["types"][full]
    head = f"{tidy(full)} ({e.get('kind', 'class')} in {pkg_of(full)}"
    ext = ([tidy(e["base"])] if e.get("base") else []) + [tidy(i) for i in e.get("interfaces") or []]
    head += f"; extends/implements {', '.join(ext)})" if ext else ")"
    if e.get("kind") == "enum":
        vals = e.get("values", [])
        head += f": {len(vals)} constants, e.g. {', '.join(vals[:25])}" + (" ..." if len(vals) > 25 else "")
    fields, meths = members(doc, full)
    if only:
        meths = [m for m in meths if re.match(rf"(static )?{re.escape(only)}\(", m)]
    parts = [head]
    if meths:
        parts.append("  methods: " + "; ".join(meths[:limit]) + (" ..." if len(meths) > limit else ""))
    if fields and not only:
        parts.append("  fields: " + "; ".join(fields[:20]))
    ctors = e.get("ctors") or []
    if ctors and not only:
        parts.append("  constructors: " + " | ".join(
            f"new {tidy(full).split('.')[-1]}({', '.join(tidy(p) for p in c)})" for c in ctors[:6]))
    return "\n".join(parts)


_ERR = re.compile(r"^(?P<file>[^\s:]+\.java):(?P<line>\d+): error: (?P<msg>.*)$")


def compile_errors(output: str) -> list[dict]:
    """javac errors with their symbol/location/required/found notes."""
    out, cur = [], None
    for ln in (output or "").splitlines():
        m = _ERR.match(ln.strip())
        if m:
            cur = dict(m.groupdict(), notes={})
            out.append(cur)
            continue
        if cur is not None:
            n = re.match(r"^\s*(symbol|location|required|found|reason)\s*:\s*(.*)$", ln)
            if n:
                cur["notes"][n.group(1)] = n.group(2).strip()
    seen, res = set(), []
    for e in out:
        key = (e["msg"], tuple(sorted(e["notes"].items())))
        if key not in seen:
            seen.add(key)
            res.append(e)
    return res


def _location_type(loc: str) -> str | None:
    """"class Foo" / "interface Player" / "variable p of type Player" -> the type."""
    m = re.search(r"of type ([\w.$<>,? \[\]]+)$", loc or "")
    if m:
        return m.group(1).strip()
    m = re.match(r"^(?:class|interface|enum|record)\s+([\w.$<>]+)", loc or "")
    return m.group(1) if m else None


def hint_for(doc: dict, err: dict) -> tuple[str | None, bool]:
    msg, notes = err["msg"], err["notes"]
    src = f"(from the {lib_names(doc)} jars)"
    if msg.startswith("cannot find symbol"):
        sym = notes.get("symbol", "")
        m = re.match(r"^(method|variable|class|interface|enum)\s+([\w$]+)", sym)
        if not m:
            return None, False
        what, name = m.groups()
        if what in ("class", "interface", "enum"):
            homes = find_types(doc, name)
            if homes:
                return (f"`{name}` is `{homes[0]}` {src}: add `import {homes[0]};`"
                        + (f" (also: {', '.join(homes[1:4])})" if len(homes) > 1 else "") + "."), True
            near = difflib.get_close_matches(name, list(doc["by_short"]), n=5, cutoff=0.7)
            if near:
                return (f"There is no class `{name}` {src}. Similar real ones: "
                        + "; ".join(f"`{find_types(doc, n)[0]}`" for n in near) + "."), False
            return None, False
        loc = _location_type(notes.get("location", ""))
        ts = find_types(doc, loc) if loc else []
        if not ts:
            return None, False
        names = method_names(doc, ts[0]) + [f[0] for t in supertypes(doc, ts[0])
                                            for f in doc["types"][t].get("props", [])]
        close = difflib.get_close_matches(name, names, n=4, cutoff=0.55)
        text = (f"`{tidy(loc)}` has no {what} `{name}` {src}."
                + (f" Closest real ones: {', '.join(f'`{c}`' for c in close)}." if close else "")
                + "\n" + describe(doc, ts[0]))
        return text, bool(close)
    m = re.match(r"^package ([\w.]+) does not exist", msg)
    if m:
        pkg = m.group(1)
        if pkg.split(".")[0] not in {p.split(".")[0] for p in doc["packages"]}:
            return None, False
        near = difflib.get_close_matches(pkg, doc["packages"], n=6, cutoff=0.6)
        return (f"There is no package `{pkg}` {src}."
                + (f" Real packages with similar names: {', '.join(f'`{p}`' for p in near)}." if near
                   else "")), bool(near)
    m = re.match(r"^(?:method|constructor) ([\w$]+) in (?:class|interface|enum|record) ([\w.$<>]+) "
                 r"cannot be applied to given types", msg)
    if m:
        name, owner = m.groups()
        ts = find_types(doc, owner)
        if not ts:
            return None, False
        return (f"The real overloads of `{tidy(owner)}.{name}` {src}:\n"
                + describe(doc, ts[0], only=name if name != tidy(owner).split(".")[-1] else None)), True
    m = re.match(r"^no suitable (?:method|constructor) found for ([\w$]+)\(", msg)
    if m:
        loc = _location_type(notes.get("location", ""))
        ts = find_types(doc, loc) if loc else []
        if ts:
            return f"The real overloads of `{m.group(1)}` {src}:\n" + describe(doc, ts[0], only=m.group(1)), True
        return None, False
    m = re.match(r"^([\w$]+) is not abstract and does not override abstract method ([\w$]+)\((.*?)\) in ([\w.$]+)", msg)
    if m:
        ts = find_types(doc, m.group(4))
        if ts:
            return (f"`{m.group(1)}` must implement `{m.group(2)}({m.group(3)})` from "
                    f"`{tidy(ts[0])}` {src}:\n" + describe(doc, ts[0], only=m.group(2))), True
    return None, False


def source_line(sources: dict[str, str] | None, file: str, n: int) -> str:
    if not sources or n < 1:
        return ""
    want = file.replace("\\", "/")
    for path, text in sources.items():
        if want.endswith("/" + path.lstrip("./")) or want == path:
            lines = text.splitlines()
            return lines[n - 1].strip()[:160] if n <= len(lines) else ""
    return ""


def compile_hints(output: str, toolchain: str, limit: int = 6,
                  sources: dict[str, str] | None = None) -> tuple[list[str], bool]:
    errs = compile_errors(output)
    if not errs:
        return [], False
    doc = load(toolchain)
    if not doc:
        return [], False
    hints, sure = [], True
    for e in errs[:limit * 2]:
        h, ok = hint_for(doc, e)
        if h is None:
            sure = False
            continue
        line = source_line(sources, e["file"], int(e["line"]))
        h = f"{Path(e['file']).name} line {e['line']}" + (f" (`{line}`)" if line else "") + f": {h}"
        if h not in hints:
            hints.append(h)
        sure = sure and ok
        if len(hints) >= limit:
            break
    return hints, bool(hints) and sure


def digest(toolchain: str, names: list[str], limit: int = 20) -> str:
    doc = load(toolchain)
    if not doc:
        return ""
    out = []
    for n in names:
        ts = find_types(doc, n)
        if ts:
            out.append("- " + describe(doc, ts[0], limit=limit).replace("\n", "\n  "))
    return "\n".join(out)


_TYPEISH = re.compile(r"^[A-Z][a-z0-9]+(?:[A-Z][A-Za-z0-9]*)+$")


def unknown_names(text: str, toolchain: str) -> list[str]:
    """`Type.member` and back-quoted CamelCase names a (Java) lesson asserts
    that the index does not have."""
    if not re.search(r"Bukkit|Paper|MockBukkit|\.java\b|plugin\.yml|JavaPlugin|Java\b", text or ""):
        return []
    doc = load(toolchain)
    if not doc:
        return []
    bad = []
    for recv, member in re.findall(r"(?<![\w.])([A-Z]\w*)\.([a-zA-Z_]\w*)\(?", text):
        ts = find_types(doc, recv)
        if not ts:
            continue
        e = doc["types"][ts[0]]
        names = set(method_names(doc, ts[0])) | set(e.get("values", [])) \
            | {f[0] for t in supertypes(doc, ts[0]) for f in doc["types"][t].get("props", [])} \
            | {k.rsplit(".", 1)[-1] for k in doc["types"] if k.startswith(ts[0] + ".")}
        if member not in names and member not in NOISE and member != "class":
            bad.append(f"{recv}.{member}")
    for name in re.findall(r"`([A-Z]\w+)(?:<[^`]*>)?`", text):
        if _TYPEISH.match(name) and not find_types(doc, name):
            bad.append(name)
    return list(dict.fromkeys(bad))
