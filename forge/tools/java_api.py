#!/usr/bin/env python3
"""Make forge's API index of a Java library from its jars: public classes,
interfaces, enums and records, their supertypes, public fields, methods
(with parameter and return types), constructors and enum constants.

    python3 tools/java_api.py --name "Paper API" --version 26.3.build.242-stable \\
        -o paper-api.json.gz --prefix org.bukkit --prefix io.papermc lib/*.jar

Standard library only: it reads the class files itself. The index has the
same shape as tools/dotnet_api.py's, so forge's compile-error hints work the
same way for both: coders get the names the library really has instead of
guessed ones (on 3 Nov the guessed Mutagen names cost 28 attempts).
"""
from __future__ import annotations

import argparse
import gzip
import json
import struct
import sys
import zipfile

ACC_PUBLIC, ACC_PROTECTED, ACC_STATIC, ACC_FINAL = 0x0001, 0x0004, 0x0008, 0x0010
ACC_INTERFACE, ACC_ABSTRACT, ACC_SYNTHETIC = 0x0200, 0x0400, 0x1000
ACC_ANNOTATION, ACC_ENUM, ACC_BRIDGE = 0x2000, 0x4000, 0x0040

PRIM = {"B": "byte", "C": "char", "D": "double", "F": "float", "I": "int", "J": "long",
        "S": "short", "Z": "boolean", "V": "void"}


def java_name(internal: str) -> str:
    """org/bukkit/entity/Player -> org.bukkit.entity.Player;
    org/bukkit/Material$Foo -> org.bukkit.Material.Foo."""
    return internal.replace("/", ".").replace("$", ".")


def parse_desc(desc: str, i: int = 0) -> tuple[str, int]:
    dims = 0
    while desc[i] == "[":
        dims += 1
        i += 1
    c = desc[i]
    if c == "L":
        j = desc.index(";", i)
        t = java_name(desc[i + 1:j])
        i = j + 1
    else:
        t = PRIM[c]
        i += 1
    return t + "[]" * dims, i


def method_desc(desc: str) -> tuple[str, list[str]]:
    assert desc[0] == "("
    i, params = 1, []
    while desc[i] != ")":
        t, i = parse_desc(desc, i)
        params.append(t)
    ret, _ = parse_desc(desc, i + 1)
    return ret, params


class ClassFile:
    def __init__(self, data: bytes):
        self.d = data
        self.p = 0
        magic, _minor, _major = self.u("I"), self.u("H"), self.u("H")
        if magic != 0xCAFEBABE:
            raise ValueError("not a class file")
        n = self.u("H")
        self.cp: list = [None] * n
        i = 1
        while i < n:
            tag = self.u("B")
            if tag == 1:
                ln = self.u("H")
                self.cp[i] = ("utf8", self.d[self.p:self.p + ln].decode("utf-8", "replace"))
                self.p += ln
            elif tag in (3, 4):
                self.p += 4
            elif tag in (5, 6):
                self.p += 8
                i += 1                         # takes two slots
            elif tag == 7:
                self.cp[i] = ("class", self.u("H"))
            elif tag in (8, 16, 19, 20):
                self.cp[i] = ("ref", self.u("H"))
            elif tag in (9, 10, 11, 12, 17, 18):
                self.p += 4
            elif tag == 15:
                self.p += 3
            else:
                raise ValueError(f"constant pool tag {tag}")
            i += 1
        self.access = self.u("H")
        self.name = self.class_name(self.u("H"))
        sup = self.u("H")
        self.super = self.class_name(sup) if sup else None
        self.interfaces = [self.class_name(self.u("H")) for _ in range(self.u("H"))]
        self.fields = [self.member() for _ in range(self.u("H"))]
        self.methods = [self.member() for _ in range(self.u("H"))]
        self.attrs = self.attributes()

    def u(self, fmt: str):
        v = struct.unpack_from(">" + fmt, self.d, self.p)[0]
        self.p += struct.calcsize(">" + fmt)
        return v

    def utf8(self, i: int) -> str:
        return self.cp[i][1]

    def class_name(self, i: int) -> str:
        return self.utf8(self.cp[i][1])

    def attributes(self) -> dict:
        out = {}
        for _ in range(self.u("H")):
            name = self.utf8(self.u("H"))
            ln = self.u("I")
            out[name] = self.d[self.p:self.p + ln]
            self.p += ln
        return out

    def member(self) -> tuple[int, str, str, dict]:
        acc = self.u("H")
        name = self.utf8(self.u("H"))
        desc = self.utf8(self.u("H"))
        return acc, name, desc, self.attributes()

    def inner_flags(self) -> dict[str, int]:
        """Access flags of nested classes as declared (InnerClasses)."""
        raw = self.attrs.get("InnerClasses")
        out: dict[str, int] = {}
        if not raw:
            return out
        n = struct.unpack_from(">H", raw, 0)[0]
        for k in range(n):
            inner, _outer, _name, acc = struct.unpack_from(">HHHH", raw, 2 + 8 * k)
            out[self.class_name(inner)] = acc
        return out


def load(jars: list[str], prefixes: list[str]) -> dict:
    types: dict[str, dict] = {}
    nested_access: dict[str, int] = {}
    for jar in jars:
        with zipfile.ZipFile(jar) as z:
            for info in z.infolist():
                n = info.filename
                if not n.endswith(".class") or n.startswith("META-INF/") or n.endswith(
                        ("module-info.class", "package-info.class")):
                    continue
                try:
                    cf = ClassFile(z.read(n))
                except (ValueError, struct.error, IndexError, KeyError, TypeError):
                    continue
                nested_access.update(cf.inner_flags())
                full = java_name(cf.name)
                if prefixes and not any(full.startswith(p) for p in prefixes):
                    continue
                if not cf.access & ACC_PUBLIC or cf.access & ACC_SYNTHETIC:
                    continue
                if any(part[:1].isdigit() for part in cf.name.split("$")[1:]):
                    continue                    # anonymous classes
                kind = ("annotation" if cf.access & ACC_ANNOTATION else
                        "interface" if cf.access & ACC_INTERFACE else
                        "enum" if cf.access & ACC_ENUM else
                        "record" if cf.super == "java/lang/Record" else
                        "abstract class" if cf.access & ACC_ABSTRACT else "class")
                e: dict = {"kind": kind, "_internal": cf.name}
                if cf.super and cf.super not in ("java/lang/Object", "java/lang/Enum",
                                                 "java/lang/Record"):
                    e["base"] = java_name(cf.super)
                if cf.interfaces:
                    e["interfaces"] = [java_name(i) for i in cf.interfaces]
                fields, values = [], []
                for acc, name, desc, _a in cf.fields:
                    if not acc & ACC_PUBLIC or acc & ACC_SYNTHETIC:
                        continue
                    if acc & ACC_ENUM:
                        values.append(name)
                        continue
                    t, _ = parse_desc(desc)
                    fields.append([name, t] + (["static"] if acc & ACC_STATIC else []))
                methods, ctors, deprecated = [], [], []
                for acc, name, desc, attrs in cf.methods:
                    # interface methods are public; protected members of
                    # classes a plugin extends (JavaPlugin) count too
                    if not acc & (ACC_PUBLIC | ACC_PROTECTED) or acc & (ACC_SYNTHETIC | ACC_BRIDGE):
                        continue
                    if name == "<clinit>":
                        continue
                    ret, params = method_desc(desc)
                    if "Deprecated" in attrs and name not in deprecated and name != "<init>":
                        deprecated.append(name)
                    if name == "<init>":
                        ctors.append(params)
                    else:
                        methods.append([name, ret, params, bool(acc & ACC_STATIC)])
                if values:
                    e["values"] = values
                if fields:
                    e["props"] = fields
                if methods:
                    e["methods"] = methods
                if ctors:
                    e["ctors"] = ctors
                if deprecated:
                    e["deprecated"] = deprecated
                types[full] = e
    # Nested classes that are not public where they are declared.
    for full in list(types):
        acc = nested_access.get(types[full]["_internal"])
        if acc is not None and not acc & ACC_PUBLIC:
            del types[full]
    for e in types.values():
        e.pop("_internal", None)
    return types


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("jars", nargs="+")
    ap.add_argument("--prefix", action="append", default=[])
    ap.add_argument("--name", default="")
    ap.add_argument("--version", default="")
    ap.add_argument("-o", "--out", required=True)
    a = ap.parse_args(argv)
    types = load(a.jars, a.prefix)
    doc = {"library": (a.prefix or [""])[0], "name": a.name or "the library",
           "version": a.version, "language": "java", "types": types}
    data = json.dumps(doc, separators=(",", ":")).encode()
    (gzip.open if a.out.endswith(".gz") else open)(a.out, "wb").write(data)
    print(f"{len(types)} types, {len(data) // 1024} KiB -> {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
