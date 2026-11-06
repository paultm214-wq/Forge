#!/usr/bin/env python3
"""Make forge's API index of a .NET library from its DLLs (public types,
their properties with types, methods with parameters, constructors and enum
values), so coders get the real names instead of guessed ones.

    pip install dnfile
    python3 tools/dotnet_api.py --prefix Mutagen.Bethesda -o fo4/mutagen-0.54.4-api.json.gz \\
        Mutagen.Bethesda.Fallout4.dll Mutagen.Bethesda.Core.dll Mutagen.Bethesda.Kernel.dll

On 3 Nov pdx-slice-a-plugin's Program.cs failed 28 attempts on names that do
not exist in Mutagen 0.54.4 (Quest.VMAD, QuestScriptData, StatRecord,
ScriptProperty.Value, Mutagen.Bethesda.Skyrim/Synthesis), and the audits
"corrected" them with other invented names. forge looks compile errors up in
this index (forge.dotnet_hints) and the fo4 coder notes name only members it
lists.
"""
from __future__ import annotations

import argparse
import gzip
import json
import sys

ELEM = {0x01: "void", 0x02: "bool", 0x03: "char", 0x04: "sbyte", 0x05: "byte", 0x06: "short",
        0x07: "ushort", 0x08: "int", 0x09: "uint", 0x0a: "long", 0x0b: "ulong", 0x0c: "float",
        0x0d: "double", 0x0e: "string", 0x18: "nint", 0x19: "nuint", 0x1c: "object"}
ALIAS = {"System.Boolean": "bool", "System.Int32": "int", "System.UInt32": "uint", "System.Int16": "short",
         "System.UInt16": "ushort", "System.Single": "float", "System.Double": "double",
         "System.String": "string", "System.Object": "object", "System.Byte": "byte",
         "System.Int64": "long", "System.UInt64": "ulong", "System.SByte": "sbyte"}


class Sig:
    def __init__(self, data: bytes, resolve):
        self.d, self.i, self.resolve = data, 0, resolve

    def u8(self) -> int:
        b = self.d[self.i]
        self.i += 1
        return b

    def comp(self) -> int:
        b = self.u8()
        if b & 0x80 == 0:
            return b
        if b & 0xC0 == 0x80:
            return ((b & 0x3F) << 8) | self.u8()
        return ((b & 0x1F) << 24) | (self.u8() << 16) | (self.u8() << 8) | self.u8()

    def token(self) -> str:
        c = self.comp()
        table, row = c & 3, c >> 2
        return self.resolve(table, row)

    def type(self) -> str:
        t = self.u8()
        while t in (0x1f, 0x20):           # custom modifiers
            self.token()
            t = self.u8()
        if t in ELEM:
            return ELEM[t]
        if t in (0x11, 0x12):              # VALUETYPE / CLASS
            return self.token()
        if t == 0x1d:                      # SZARRAY
            return self.type() + "[]"
        if t == 0x0f:                      # PTR
            return self.type() + "*"
        if t == 0x10:                      # BYREF
            return "ref " + self.type()
        if t == 0x15:                      # GENERICINST
            self.u8()
            base = self.token()
            n = self.comp()
            args = [self.type() for _ in range(n)]
            base = base.split("`")[0]
            if base == "System.Nullable" and len(args) == 1:
                return args[0] + "?"
            return f"{base}<{', '.join(args)}>"
        if t == 0x13:                      # VAR
            return f"T{self.comp()}"
        if t == 0x1e:                      # MVAR
            return f"TM{self.comp()}"
        if t == 0x14:                      # ARRAY
            el = self.type()
            rank = self.comp()
            for _ in range(self.comp()):
                self.comp()
            for _ in range(self.comp()):
                self.comp()
            return el + "[" + "," * (rank - 1) + "]"
        if t == 0x16:
            return "TypedReference"
        if t == 0x1b:
            return "fnptr"
        raise ValueError(f"signature element 0x{t:02x}")

    def method(self) -> tuple[str, list[str]]:
        cc = self.u8()
        if cc & 0x10:                      # generic method
            self.comp()
        n = self.comp()
        ret = self.type()
        return ret, [self.type() for _ in range(n)]

    def field(self) -> str:
        self.u8()                          # FIELD (0x06)
        try:
            return self.type()
        except (ValueError, IndexError):
            return "?"

    def prop(self) -> str:
        return self.indexer()[0]

    def indexer(self) -> tuple[str, list[str]]:
        """(type, parameter types): parameters only for an indexer (this[...])."""
        self.u8()
        n = self.comp()
        t = self.type()
        return t, [self.type() for _ in range(n)]


def short(name: str) -> str:
    return ALIAS.get(name, name)


def load(paths: list[str], prefix: str) -> dict:
    import dnfile
    types: dict[str, dict] = {}
    for path in paths:
        pe = dnfile.dnPE(path)
        md = pe.net.mdtables
        blob = pe.net.blobs if hasattr(pe.net, "blobs") else None

        def tname(row) -> str:
            ns = str(getattr(row, "TypeNamespace", "") or "")
            nm = str(row.TypeName)
            enc = None
            return (ns + "." if ns else "") + nm

        nested_parent: dict[int, int] = {}
        if md.NestedClass:
            for r in md.NestedClass.rows:
                nested_parent[r.NestedClass.row_index] = r.EnclosingClass.row_index

        def typedef_full(idx: int) -> str:
            row = md.TypeDef.rows[idx - 1]
            if idx in nested_parent:
                return typedef_full(nested_parent[idx]) + "." + str(row.TypeName)
            return tname(row)

        def resolve(table: int, row: int) -> str:
            try:
                if table == 0:
                    return short(typedef_full(row))
                if table == 1:
                    r = md.TypeRef.rows[row - 1]
                    scope = r.ResolutionScope
                    if scope and scope.table and scope.table.name == "TypeRef":
                        outer = md.TypeRef.rows[scope.row_index - 1]
                        return short(tname(outer) + "." + str(r.TypeName))
                    return short(tname(r))
                if table == 2:
                    r = md.TypeSpec.rows[row - 1]
                    return Sig(bytes(r.Signature.value if hasattr(r.Signature, "value") else r.Signature),
                               resolve).type()
            except Exception:
                pass
            return "?"

        def raw(sig) -> bytes:
            return bytes(sig.value if hasattr(sig, "value") else sig)

        props_of: dict[int, list] = {}
        if md.PropertyMap:
            for pm in md.PropertyMap.rows:
                props_of[pm.Parent.row_index] = [p.row for p in pm.PropertyList]
        enum_of: dict[int, list] = {}
        for i, t in enumerate(md.TypeDef.rows, 1):
            f = t.Flags
            if not (f.tdPublic or f.tdNestedPublic):
                continue
            full = typedef_full(i)
            if not full.startswith(prefix) or "<" in full:
                continue
            ext = t.Extends
            base = ""
            if ext is not None and ext.row is not None and ext.table is not None:
                tbl = {"TypeDef": 0, "TypeRef": 1, "TypeSpec": 2}.get(ext.table.name)
                if tbl is not None:
                    base = resolve(tbl, ext.row_index)
            kind = ("interface" if f.tdInterface else "enum" if base == "System.Enum"
                    else "struct" if base == "System.ValueType" else
                    "static class" if f.tdAbstract and f.tdSealed else
                    "abstract class" if f.tdAbstract else "class")
            entry: dict = {"kind": kind}
            if base and base not in ("object", "System.Enum", "System.ValueType"):
                entry["base"] = base
            if kind == "enum":
                entry["values"] = [str(fl.row.Name) for fl in t.FieldList
                                   if fl.row.Flags.fdStatic and fl.row.Flags.fdLiteral]
                types[full] = entry
                continue
            props = []
            for p in props_of.get(i, []):
                name = str(p.Name)
                if "." in name:            # explicit interface implementations
                    continue
                try:
                    ty, params = Sig(raw(p.Type), resolve).indexer()
                except Exception:
                    ty, params = "?", []
                if params:                 # an indexer: this[FormKey]
                    name = f"this[{', '.join(params)}]"
                props.append([name, ty])
            methods, ctors = [], []
            for m in t.MethodList:
                r = m.row
                if not r.Flags.mdPublic:
                    continue
                name = str(r.Name)
                if name.startswith(("get_", "set_", "add_", "remove_", "op_", "<")) or "." in name.lstrip("."):
                    continue
                try:
                    ret, params = Sig(raw(r.Signature), resolve).method()
                except Exception:
                    ret, params = "?", []
                pnames = [str(pp.row.Name) for pp in r.ParamList if pp.row.Sequence > 0]
                sig = [f"{ty} {pnames[k]}" if k < len(pnames) else ty for k, ty in enumerate(params)]
                if name == ".ctor":
                    ctors.append(sig)
                elif name != ".cctor":
                    methods.append([name, ret, sig, bool(r.Flags.mdStatic)])
            fields = [[str(fl.row.Name), Sig(raw(fl.row.Signature), resolve).field()]
                      for fl in t.FieldList
                      if fl.row.Flags.fdPublic and not fl.row.Flags.fdStatic]
            props += [f for f in fields if f[0] not in {p[0] for p in props}]
            if props:
                entry["props"] = props
            if methods:
                entry["methods"] = methods
            if ctors:
                entry["ctors"] = ctors
            types[full] = entry
    return types


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("dlls", nargs="+")
    ap.add_argument("--prefix", default="")
    ap.add_argument("-o", "--out", required=True)
    ap.add_argument("--version", default="")
    ap.add_argument("--name", default="", help="the library's name in hints (Mutagen)")
    a = ap.parse_args(argv)
    types = load(a.dlls, a.prefix)
    doc = {"library": a.prefix, "name": a.name or a.prefix, "version": a.version, "types": types}
    data = json.dumps(doc, separators=(",", ":")).encode()
    (gzip.open if a.out.endswith(".gz") else open)(a.out, "wb").write(data)
    print(f"{len(types)} types, {len(data) // 1024} KiB -> {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
