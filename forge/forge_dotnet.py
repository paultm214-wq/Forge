"""Real API facts for C# compile errors, from an index made from the DLLs
(tools/dotnet_api.py).

On 3 Nov pdx-slice-a-plugin's Plugin/Program.cs failed 28 attempts on Mutagen
names that do not exist (Quest.VMAD, QuestScriptData, StatRecord,
ScriptProperty.Value, FormKey.Value, Mutagen.Bethesda.Skyrim and .Synthesis)
or on a missing `using` (IModGetter, eleven times). The test audits
"corrected" them with other invented names. Every hint here comes from the
index of the Mutagen version the fo4 image builds with, so it is never a
guess: the members a type really has, the namespace a type really lives in,
the constructors it really takes.
"""
from __future__ import annotations

import difflib
import gzip
import json
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
_INDEX: dict[str, dict] = {}

NOISE = {"BinaryWriteTranslator", "StaticRegistration", "Registration", "EqualityContract",
         "Print", "Equals", "GetHashCode", "ToString", "CreateFromBinary", "TryCreateFromBinary",
         "EnumerateFormLinks", "RemapLinks", "EnumerateAssetLinks", "RemapAssetLinks",
         "EnumerateListedAssetLinks", "RemapListedAssetLinks", "GetEqualsMask", "Deconstruct",
         "<Clone>$", "LinkType", "DeepCopy", "DeepCopyIn", "GetMask", "GetHasBeenSetIMask",
         "Clear", "CopyInFromBinary", "GetErrorMask", "Diff", "Translate",
         "FormKeyEqualityComparer", "MajorRecordFlagsRaw", "Fallout4MajorRecordFlags",
         "FormVersionAbstract", "TitleString", "IsCompressed", "VersionControl", "Version2",
         "Unused", "Unknown", "Disable", "WriteToBinary"}

# What every record has; a compact digest leaves the rest of the base out.
RECORD_BASES = {"Fallout4MajorRecord", "MajorRecord", "AMajorRecord", "SkyrimMajorRecord"}
RECORD_KEEP = {"EditorID", "FormKey"}

# Record and subrecord signatures (what the format docs and the CK say) to
# the names Mutagen gives them - coders reach for quest.VMAD, StatRecord.
SIGNATURES = {"FACT": "Faction", "WRLD": "Worldspace", "STAT": "Static", "GLOB": "Global",
              "QUST": "Quest", "MISC": "MiscItem", "KYWD": "Keyword", "FLST": "FormList",
              "ACTI": "Activator", "CONT": "Container", "MESG": "Message", "CELL": "Cell",
              "REFR": "PlacedObject", "WEAP": "Weapon", "ARMO": "Armor", "BOOK": "Book",
              "NPC_": "Npc", "LVLI": "LeveledItem", "COBJ": "ConstructibleObject",
              "TERM": "Terminal", "NOTE": "Holotape", "LCTN": "Location", "SCOL": "StaticCollection"}
SUB_ALIASES = {"VMAD": "VirtualMachineAdapter", "MODL": "Model", "EDID": "EditorID",
               "FULL": "Name", "OBND": "ObjectBounds", "XNAM": "Relations", "DATA": "Data",
               "WNAM": "Parent", "DESC": "Description"}


def index_path(toolchain: str) -> Path | None:
    """The newest index for a toolchain (fo4: fo4/mutagen-<version>-api.json.gz)."""
    if toolchain != "fo4":
        return None
    found = sorted((HERE / "fo4").glob("mutagen-*-api.json.gz"),
                   key=lambda p: [int(x) if x.isdigit() else x
                                  for x in re.split(r"[.-]", p.name)])
    return found[-1] if found else None


def load(toolchain: str) -> dict | None:
    if toolchain in _INDEX:
        return _INDEX[toolchain] or None
    p = index_path(toolchain)
    doc = None
    if p is not None:
        try:
            doc = json.loads(gzip.open(p).read())
        except (OSError, ValueError):
            doc = None
    if doc:
        types = doc["types"]
        by_short: dict[str, list[str]] = {}
        for full in types:
            s = short_name(full)
            by_short.setdefault(s, []).append(full)
        doc["by_short"] = by_short
        counts: dict[str, int] = {}
        for full in types:
            ns = full.rsplit(".", 1)[0] if "." in full else ""
            if ns and ns not in types and not re.search(r"Translation\b|\.Internals?\b|\.DI\b", ns):
                counts[ns] = counts.get(ns, 0) + 1
        doc["namespaces"] = sorted(n for n, c in counts.items() if c >= 3)
    _INDEX[toolchain] = doc or {}
    return doc


def short_name(full: str) -> str:
    """Mutagen.Bethesda.Fallout4.Fallout4Group`1 -> Fallout4Group;
    Mutagen.Bethesda.Fallout4.Quest.Flag -> Quest.Flag (nested keeps its parent)."""
    base = full.split("`")[0]
    parts = base.split(".")
    # namespaces are lower-dot-Capital; nested types follow a type name
    i = len(parts) - 1
    return ".".join(parts[i:]) if i >= 0 else base


def tidy(t: str) -> str:
    """Drop namespaces from a type string: Noggog.ExtendedList<Mutagen.Bethesda.Fallout4.ScriptEntry>
    -> ExtendedList<ScriptEntry>."""
    return re.sub(r"\b(?:[A-Za-z_]\w*\.)+(?=[A-Z_]\w*)", "", t).replace("`1", "")


def find_types(doc: dict, name: str) -> list[str]:
    name = re.sub(r"<.*>$", "", name.strip())
    if name in doc["types"]:
        return [name]
    gen = [k for k in doc["types"] if k.split("`")[0] == name]
    if gen:
        return gen
    last = name.split(".")[-1]
    hits = doc["by_short"].get(last, [])
    if "." in name:
        nested = [k for k in hits if k.split("`")[0].endswith(name)]
        if nested:
            return nested
    return hits


def chain(doc: dict, full: str, depth: int = 6) -> list[str]:
    out, seen = [], set()
    while full and full not in seen and depth > 0:
        seen.add(full)
        out.append(full)
        base = (doc["types"].get(full) or {}).get("base", "")
        base = re.sub(r"<.*>$", "", base)
        full = next(iter(find_types(doc, base)), None) if base else None
        depth -= 1
    return out


def members(doc: dict, full: str, compact: bool = False) -> tuple[list[str], list[str]]:
    """(properties as 'Name: Type', method names) of a type and its bases;
    compact leaves out what every record has (but EditorID and FormKey)."""
    props, meths, seenp, seenm = [], [], set(), set()
    for t in chain(doc, full):
        base_rec = short_name(t) in RECORD_BASES
        e = doc["types"][t]
        for name, ty in e.get("props", []):
            if name in NOISE or name in seenp or (compact and base_rec and name not in RECORD_KEEP):
                continue
            seenp.add(name)
            props.append(f"{tidy(name)}: {tidy(ty)}")
        for name, ret, params, static in e.get("methods", []):
            if name in NOISE or name in seenm or (compact and base_rec):
                continue
            seenm.add(name)
            meths.append(f"{'static ' if static else ''}{name}({', '.join(tidy(p) for p in params)})")
    return props, meths


def extensions(doc: dict, full: str) -> list[str]:
    """Extension methods whose first parameter is this type or an interface it
    is known by (IQuest, IQuestGetter...)."""
    names = {short_name(t) for t in chain(doc, full)}
    names |= {f"I{n}" for n in names} | {f"I{n}Getter" for n in names}
    out = []
    for t, e in doc["types"].items():
        if e.get("kind") != "static class":
            continue
        for name, ret, params, static in e.get("methods", []):
            if not params or name in NOISE:
                continue
            first = re.sub(r"<.*", "", params[0].split(" ")[0]).split(".")[-1]
            if first in names:
                s = f"{name}() (extension in {t.rsplit('.', 1)[0]})"
                if s not in out:
                    out.append(s)
    return out[:12]


def display_name(doc: dict, full: str) -> str:
    """Quest.Flag for a nested type, Quest for a top-level one."""
    base = full.split("`")[0]
    parent = full.rsplit(".", 1)[0]
    if parent in doc["types"]:
        return display_name(doc, parent) + "." + base.rsplit(".", 1)[1]
    return base.rsplit(".", 1)[-1]


def member_type(doc: dict, full: str, member: str) -> str | None:
    """The type a member gives (a property's type, a nested type), or "" when
    it exists but leads nowhere checkable, None when it does not exist."""
    for t in chain(doc, full):
        e = doc["types"][t]
        for name, ty in e.get("props", []):
            if name == member:
                ty = re.sub(r"\?$", "", ty)
                hit = find_types(doc, ty)
                return hit[0] if hit else ""
        if member in e.get("values", []):
            return ""
        for name, *_ in e.get("methods", []):
            if name == member:
                return ""
        nested = f"{t}.{member}"
        if nested in doc["types"]:
            return nested
    return None


def lib_name(doc: dict) -> str:
    return f"{doc.get('name') or doc.get('library', 'the library')} {doc.get('version', '')}".strip()


def describe_compact(doc: dict, full: str, limit: int = 24) -> str:
    """One or two lines: `Quest : Fallout4MajorRecord - Name: TranslatedString; ... | new Quest(mod, editorID)`."""
    e = doc["types"][full]
    name = display_name(doc, full)
    if e.get("kind") == "enum":
        return f"{name} (enum): {', '.join(e.get('values', []))}"
    props, meths = members(doc, full, compact=True)
    head = name + (f" : {tidy(e['base'])}" if e.get("base") else "")
    groups = [p.split(":")[0] for p in props if re.search(r":\s*\w*Group<", p)]
    if len(groups) > 5:
        # a mod: every record group by name (Factions, Quests ...), then the rest
        rest = [p for p in props if p.split(":")[0] not in groups]
        line = (head + " - record groups (Group<record>; .AddNew(), .Add(rec)): "
                + ", ".join(groups) + "; " + "; ".join(rest[:limit]))
    else:
        line = head + " - " + "; ".join(props[:limit]) + (" ..." if len(props) > limit else "")
    ctors = sorted(e.get("ctors", []), key=len)
    ctor_txt = " | ".join(f"new {short_name(full).split('`')[0]}({', '.join(p.split(' ')[-1] for p in c)})"
                          for c in ctors[:3])
    if meths and not any(short_name(t) in RECORD_BASES for t in chain(doc, full)[1:2]):
        line += " | methods: " + "; ".join(m.split("(")[0] for m in meths[:12])
    return line + (" | " + ctor_txt if ctor_txt else "")


def describe(doc: dict, full: str, limit: int = 40) -> str:
    e = doc["types"][full]
    kind = e.get("kind", "class")
    head = f"{tidy(full.replace('`1', ''))} ({kind} in {full.rsplit('.', 1)[0]}"
    head += f", base {tidy(e['base'])})" if e.get("base") else ")"
    if kind == "enum":
        return f"{head}: values {', '.join(e.get('values', []))}"
    props, meths = members(doc, full)
    ctors = ["(" + ", ".join(tidy(p) for p in c) + ")" for c in e.get("ctors", [])]
    parts = [head + ":"]
    if props:
        parts.append("  properties: " + "; ".join(props[:limit])
                     + (" ..." if len(props) > limit else ""))
    if meths:
        parts.append("  methods: " + "; ".join(meths[:limit // 2])
                     + (" ..." if len(meths) > limit // 2 else ""))
    if ctors:
        parts.append("  constructors: new " + tidy(short_name(full)) + " " + " | ".join(ctors[:6]))
    ext = extensions(doc, full)
    if ext:
        parts.append("  extension methods: " + "; ".join(ext))
    return "\n".join(parts)


def close_types(doc: dict, name: str, n: int = 5) -> list[str]:
    shorts = list(doc["by_short"])
    picks = difflib.get_close_matches(name, shorts, n=n, cutoff=0.6)
    low = name.lower()
    for s in shorts:                       # QuestScriptData -> ScriptEntry has no
        if len(picks) >= n:                # ratio, but Quest* / *Script* do
            break
        if s not in picks and (s.lower() == low or (len(low) > 4 and low in s.lower())):
            picks.append(s)
    return picks


def where(doc: dict, short: str) -> list[str]:
    return sorted({f.rsplit(".", 1)[0] for f in doc["by_short"].get(short, [])
                   if "." in f})


# C# compiler messages (roslyn, English)
_CS = re.compile(r"(?P<file>[^\s:()]+\.cs)\((?P<line>\d+),(?P<col>\d+)\): error (?P<code>CS\d{4}): "
                 r"(?P<msg>.*?)(?: \[[^\]]*\.csproj\])?$", re.M)


def compile_errors(output: str) -> list[dict]:
    seen, out = set(), []
    for m in _CS.finditer(output or ""):
        key = (m["code"], m["msg"])
        if key in seen:
            continue
        seen.add(key)
        out.append(m.groupdict())
    return out


def hint_for(doc: dict, code: str, msg: str) -> tuple[str | None, bool]:
    src = f"(from the {lib_name(doc)} DLLs)"
    m = re.match(r"'([^']+)' does not contain a definition for '(\w+)'", msg)
    if code in ("CS1061", "CS0117") and m:
        tname, member = m.groups()
        ts = find_types(doc, tname)
        if not ts:
            return None, False
        props, meths = members(doc, ts[0])
        names = [p.split(":")[0] for p in props] + [x.split("(")[0].replace("static ", "") for x in meths]
        close = difflib.get_close_matches(member, names, n=3, cutoff=0.5)
        alias = SUB_ALIASES.get(member.upper())
        if alias in names and alias not in close:
            close.insert(0, alias)
        txt = (f"`{tidy(tname)}` has no `{member}` {src}."
               + (f" Closest real members: {', '.join(f'`{c}`' for c in close)}." if close else "")
               + "\n" + describe(doc, ts[0]))
        return txt, bool(close)
    m = re.match(r"The type or namespace name '(\w+)' does not exist in the namespace '([\w.]+)'", msg)
    if code == "CS0234" and m:
        name, ns = m.groups()
        full = f"{ns}.{name}"
        if any(n == full or n.startswith(full + ".") for n in doc["namespaces"]):
            return None, False
        homes = where(doc, name)
        if homes:
            return (f"`{name}` is not in `{ns}` {src}; it is in "
                    + ", ".join(f"`{h}`" for h in homes) + f" - use `using {homes[0]};`."), True
        if ns.startswith(doc.get("library", "~")):
            near = [n for n in doc["namespaces"] if n.startswith(ns)][:12] or doc["namespaces"][:12]
            return (f"There is no `{full}` {src}. Namespaces under `{ns}`: "
                    + ", ".join(f"`{n}`" for n in near) + "."), True
        return None, False
    m = (re.match(r"The type or namespace name '(\w+)' could not be found", msg) if code == "CS0246"
         else re.match(r"The name '(\w+)' does not exist in the current context", msg)
         if code == "CS0103" else None)
    if m:
        name = m.group(1)
        homes = where(doc, name)
        if homes:
            return (f"`{name}` is `{homes[0]}.{name}` {src}: add `using {homes[0]};`"
                    + (f" (also in {', '.join(homes[1:])})" if len(homes) > 1 else "") + "."), True
        near = close_types(doc, name)
        sig = SIGNATURES.get(name[:4].upper())
        if sig and doc["by_short"].get(sig):
            near = [sig] + [n for n in near if n != sig]
        if code == "CS0103" and not near:
            return None, False
        if near:
            return (f"There is no type `{name}` {src}. Real types with similar names: "
                    + "; ".join(f"`{n}` ({', '.join(where(doc, n))})" for n in near)
                    + ". Read the members of the one you mean before using it."), False
        return f"There is no type `{name}` {src}.", False
    m = re.match(r"'(\w+)' does not contain a constructor that takes (\d+) arguments", msg)
    if code == "CS1729" and m:
        ts = find_types(doc, m.group(1))
        if not ts:
            return None, False
        ctors = doc["types"][ts[0]].get("ctors", [])
        props, _ = members(doc, ts[0])
        shown = " | ".join("(" + ", ".join(tidy(p) for p in c) + ")" for c in ctors) or "none public"
        return (f"`{m.group(1)}` constructors {src}: new {m.group(1)}{shown}. Set the rest "
                f"with an object initializer: properties {'; '.join(props[:20])}."), True
    m = re.match(r"'([^']+)' does not contain a definition for '(\w+)' and the best extension "
                 r"method overload '([^']+)' requires a receiver of type '([^']+)'", msg)
    if code == "CS1929" and m:
        have, member, _, want = m.groups()
        getter = re.search(r"Getter\b", have) and not re.search(r"Getter\b", want)
        txt = (f"`{member}` needs a `{tidy(want)}`; this is a `{tidy(have)}` {src}.")
        if getter:
            txt += (" A *Getter is the read-only view: a mod read with "
                    "Fallout4Mod.CreateFromBinaryOverlay(...) is an IFallout4ModDisposableGetter and "
                    "can never take new records. Make the plugin as "
                    "new Fallout4Mod(ModKey.FromFileName(\"X.esp\"), Fallout4Release.Fallout4) and add "
                    "records to that.")
        return txt, bool(getter)
    m = re.match(r"'(\w+)' is an ambiguous reference between '([\w.]+)' and '([\w.]+)'", msg)
    if code == "CS0104" and m:
        name, a, b = m.groups()
        return (f"`{name}` is both `{a}` and `{b}`: write the full name you mean "
                f"(`{b if b.startswith('System') else a}` for the .NET one), or add "
                f"`using {name} = {a};` at the top."), True
    m = re.match(r"Argument (\d+): cannot convert from '([^']+)' to '([^']+)'", msg)
    if code == "CS1503" and m:
        n, have, want = m.groups()
        ts = find_types(doc, want)
        if not ts:
            return None, False
        lines = [f"Argument {n} must be a `{tidy(want)}`, not `{tidy(have)}` {src}."]
        keyed = sorted({display_name(doc, t) for t, e in doc["types"].items()
                        for name, _ in e.get("props", [])
                        if name.startswith("this[") and tidy(want) in tidy(name)
                        and re.search(r"Group|Cache", t)})
        if keyed:
            lines.append(f"Indexers that take a `{tidy(want)}`: "
                         + ", ".join(f"{k}[{tidy(want)}]" for k in keyed[:4])
                         + f" - a record group is keyed by {tidy(want)}, not a list: keep the "
                           f"records you add in your own List<T> (AddNew returns each one) to "
                           f"reach them by number.")
        e = doc["types"][ts[0]]
        made = ["new " + short_name(ts[0]).split("`")[0] + "(" + ", ".join(tidy(p) for p in c) + ")"
                for c in e.get("ctors", [])]
        made += [f"{short_name(ts[0])}.{name}({', '.join(tidy(p) for p in params)})"
                 for name, ret, params, static in e.get("methods", [])
                 if static and tidy(ret) == tidy(want).split("<")[0]]
        if tidy(want) == "FormKey":
            made.insert(0, "record.FormKey (every record has one)")
        if made:
            lines.append(f"Ways to get a `{tidy(want)}`: " + "; ".join(made[:6]) + ".")
        return " ".join(lines), bool(keyed)
    m = re.match(r"Property or indexer '([\w.]+)\.(\w+)' cannot be assigned to -- it is read only", msg)
    if code == "CS0200" and m:
        ts = find_types(doc, m.group(1))
        if not ts:
            return None, False
        return (f"`{m.group(1)}.{m.group(2)}` is get-only {src}: change the object it returns "
                f"(add to the list, set its properties) instead of assigning it.\n"
                + describe(doc, ts[0])), True
    return None, False


def compile_hints(output: str, toolchain: str, limit: int = 6,
                  sources: dict[str, str] | None = None) -> tuple[list[str], bool]:
    """(hints, sure): one hint per distinct C# error the index can answer, and
    whether every error got a definite one (then no model diagnosis is
    needed). With the project's sources, each hint quotes its line."""
    errs = compile_errors(output)
    if not errs:
        return [], False
    doc = load(toolchain)
    if not doc:
        return [], False
    hints, sure = [], True
    for e in errs[:limit * 2]:
        h, ok = hint_for(doc, e["code"], e["msg"])
        if h is None:
            sure = False
            continue
        line = source_line(sources, e["file"], int(e["line"]))
        h = (f"{Path(e['file']).name} line {e['line']}"
             + (f" (`{line}`)" if line else "") + f": {h}")
        if h not in hints:
            hints.append(h)
        sure = sure and ok
        if len(hints) >= limit:
            break
    return hints, bool(hints) and sure


def source_line(sources: dict[str, str] | None, file: str, n: int) -> str:
    """Line n (from 1) of the source whose path ends like the compiler's."""
    if not sources or n < 1:
        return ""
    want = file.replace("\\", "/")
    for path, text in sources.items():
        if want.endswith("/" + path.lstrip("./")) or want == path:
            lines = text.splitlines()
            return lines[n - 1].strip()[:160] if n <= len(lines) else ""
    return ""


def digest(toolchain: str, names: list[str], limit: int = 24) -> str:
    """Compact real members for the named types (for a coder prompt)."""
    doc = load(toolchain)
    if not doc:
        return ""
    out, seen = [], set()
    for n in names:
        ts = find_types(doc, n)
        if ts and ts[0] not in seen:
            seen.add(ts[0])
            out.append("- " + describe_compact(doc, ts[0], limit))
    return "\n".join(out)


def signature_types(text: str) -> list[str]:
    """Mutagen type names for the record signatures a spec mentions (FACT, QUST...)."""
    return [SIGNATURES[s] for s in dict.fromkeys(re.findall(r"\b([A-Z][A-Z_]{3})\b", text or ""))
            if s in SIGNATURES]


_CS_LESSON = re.compile(r"Mutagen|C#|Program\.cs|\.cs\b|Fallout4Mod|csproj")
_TYPEISH = re.compile(r"^[A-Z][a-z0-9]+(?:[A-Z][A-Za-z0-9]*)+$|^I[A-Z][a-z]+[A-Z]\w*$")


def unknown_names(text: str, toolchain: str) -> list[str]:
    """Library names a (C#) lesson asserts that the index does not have:
    `Type.Member` with a capitalised receiver, and back-quoted CamelCase
    type names. Empty when the text is not about the library or there is
    no index - an audit's lesson naming Fallout4Mod.CreateEmpty or
    QuestDataFlags is a guess, and a guess must not become a rule."""
    if not _CS_LESSON.search(text or ""):
        return []
    doc = load(toolchain)
    if not doc:
        return []
    bad = []
    for dotted in re.findall(r"(?<![\w.:])([A-Z]\w*(?:\.[A-Za-z_]\w*)+)", text):
        parts = dotted.split(".")
        if parts[0] in ("Mutagen", "System", "Path", "Console", "Main", "Plugin", "Scripts",
                        "PDX", "Noggog", "Data", "Fallout4"):
            continue
        ts = find_types(doc, parts[0])
        if not ts:
            if _TYPEISH.match(parts[0]):
                bad.append(parts[0])
            continue
        cur = ts[0]
        for i, member in enumerate(parts[1:], 1):
            if member in NOISE:
                break
            nxt = member_type(doc, cur, member)
            if nxt is None:
                bad.append(".".join(parts[:i + 1]))
                break
            if not nxt:
                break
            cur = nxt
    for name in re.findall(r"`([A-Z]\w+)(?:<[^`]*>)?`", text):
        if _TYPEISH.match(name) and not find_types(doc, name):
            bad.append(name)
    # "ScriptObjectProperty must have its Type (and Access) field set": members
    # claimed for the library types the lesson names, none of which has them.
    named = [t for w in re.findall(r"\b([A-Z]\w{3,})\b", text) for t in find_types(doc, w)[:1]]
    if named:
        have = set()
        for t in named:
            props, meths = members(doc, t)
            have |= {p.split(":")[0] for p in props} | {m.split("(")[0].replace("static ", "")
                                                        for m in meths}
        for grp in re.findall(r"\bits ((?:[A-Z]\w*)(?:\s*(?:\(and|,|and|/)\s*[A-Z]\w*\)?)*)\s+"
                              r"(?:field|property|member|value)s?\b", text):
            for w in re.findall(r"[A-Z]\w*", grp):
                if w not in have:
                    bad.append(f"{short_name(named[0])}.{w}")
    return list(dict.fromkeys(bad))
