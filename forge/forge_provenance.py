"""Versioned build records with selected metadata, hashes and bounded comparisons."""

import datetime as dt
import hashlib
import json
import math
import struct
from pathlib import Path

NAME = "BUILD_PROVENANCE.json"
SCHEMA_VERSION = 1
LOCK_NAMES = {
    "package-lock.json",
    "go.sum",
    "go.mod",
    "Cargo.lock",
    "uv.lock",
    "requirements.lock",
    "requirements.txt",
    "gradle.lockfile",
    "forge-dependencies.lock.json",
}


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(2**20), b""):
            h.update(block)
    return h.hexdigest()


def files(root):
    if root.is_symlink():
        raise ValueError("symlink artifact tree: " + root.name)
    result = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError("symlink artifact: " + str(path.relative_to(root)))
        if path.is_file():
            result[path.relative_to(root).as_posix()] = dict(
                sha256=digest(path), bytes=path.stat().st_size
            )
    return result


def image_signature(path):
    if path.suffix.lower() != ".png":
        return None
    try:
        from PIL import Image

        with Image.open(path) as img:
            if img.width * img.height > 16_777_216:
                return None
            return dict(
                size=list(img.size),
                rgba32=list(img.convert("RGBA").resize((32, 32)).tobytes()),
            )
    except (ImportError, OSError, ValueError):
        return None


def mesh_signature(path):
    """Float VEC3 GLB positions in topology order; other formats stay hash-only."""
    if path.suffix.lower() != ".glb" or path.stat().st_size > 64 * 2**20:
        return None
    try:
        raw = path.read_bytes()
        magic, version, length = struct.unpack_from("<4sII", raw)
        if (magic, version, length) != (b"glTF", 2, len(raw)):
            return None
        offset, doc, binary = 12, None, None
        while offset < length:
            n, kind = struct.unpack_from("<II", raw, offset)
            chunk = raw[offset + 8 : offset + 8 + n]
            if len(chunk) != n:
                return None
            if kind == 0x4E4F534A:
                doc = json.loads(chunk)
            elif kind == 0x004E4942:
                binary = chunk
            offset += 8 + n
        if doc is None or binary is None:
            return None
        points, remainder = [], bytearray(binary)
        for mesh in doc.get("meshes", []):
            for primitive in mesh.get("primitives", []):
                a = doc["accessors"][primitive["attributes"]["POSITION"]]
                if (
                    a.get("componentType") != 5126
                    or a.get("type") != "VEC3"
                    or a.get("sparse")
                ):
                    return None
                v = doc["bufferViews"][a["bufferView"]]
                if (
                    v.get("buffer", 0) != 0
                    or a["count"] < 0
                    or len(points) + a["count"] > 100000
                ):
                    return None
                start, stride = (
                    v.get("byteOffset", 0) + a.get("byteOffset", 0),
                    v.get("byteStride", 12),
                )
                if start < 0 or stride < 12:
                    return None
                for i in range(a["count"]):
                    pos = start + i * stride
                    point = struct.unpack_from("<3f", binary, pos)
                    if not all(math.isfinite(x) for x in point):
                        return None
                    points.append(list(point))
                    remainder[pos : pos + 12] = bytes(12)
        if not points:
            return None
        for a in doc.get("accessors", []):
            a.pop("min", None)
            a.pop("max", None)
        doc["non_position_sha256"] = hashlib.sha256(remainder).hexdigest()
        return dict(points=points, structure=doc)
    except (OSError, ValueError, TypeError, KeyError, IndexError, struct.error):
        return None


def artifacts(root):
    result = {}
    for tree in ("assets", "outputs"):
        for name, record in files(root / tree).items():
            path = root / tree / name
            for key, signature in [
                ("image", image_signature(path)),
                ("mesh", mesh_signature(path)),
            ]:
                if signature:
                    record[key] = signature
            result[f"{tree}/{name}"] = record
    return result


def build(root, *, toolchain, image, models, project_files=None):
    state, plan = [
        json.loads((root / name).read_text()) for name in ("state.json", "plan.json")
    ]
    references = {
        f"{tree}/{name}": record
        for tree in ("verified", "chunks", "plan-tests")
        for name, record in files(root / tree).items()
    }
    return dict(
        schema_version=1,
        created_at=dt.datetime.now(dt.timezone.utc).isoformat(),
        toolchain=toolchain,
        image={k: image.get(k) for k in ("reference", "id", "repo_digests")},
        models=[
            {k: m[k] for k in ("role", "model", "kind", "revision", "repo") if k in m}
            for m in models
        ],
        inputs={
            n: digest(root / n)
            for n in ("SPEC.md", "plan.json", "module.py")
            if (root / n).is_file()
        },
        attempt_references=references,
        dependency_locks={
            n: hashlib.sha256(s.encode()).hexdigest()
            for n, s in (project_files or {}).items()
            if Path(n).name in LOCK_NAMES
        },
        assets=[
            dict(
                id=c["id"],
                seed=c.get("seed"),
                rerolls=(state.get("rerolls") or {}).get(c["id"], 0),
            )
            for c in plan.get("chunks", [])
            if c.get("kind") == "image"
        ],
        review_state=[
            {k: r.get(k) for k in ("source", "round", "approved")}
            for r in state.get("reviews", [])
            if isinstance(r, dict)
        ],
        artifacts=artifacts(root),
    )


def compare(expected, actual_root, *, image_tolerance=0, mesh_tolerance=0):
    if expected.get("schema_version") != 1:
        raise ValueError("unsupported provenance version")
    if not 0 <= image_tolerance <= 1 or not 0 <= mesh_tolerance < float("inf"):
        raise ValueError(
            "image tolerance must be 0..1; mesh tolerance must be finite and nonnegative"
        )
    current, previous = artifacts(actual_root), expected["artifacts"]
    differences, tolerated = [], []
    for name in sorted(current.keys() | previous.keys()):
        before, after = previous.get(name), current.get(name)
        if before is None or after is None:
            differences.append(
                dict(path=name, reason="added" if before is None else "missing")
            )
            continue
        if before["sha256"] == after["sha256"]:
            continue
        a, b = before.get("image"), after.get("image")
        if (
            image_tolerance
            and a
            and b
            and a["size"] == b["size"]
            and len(a["rgba32"]) == len(b["rgba32"]) == 4096
        ):
            error = sum(abs(x - y) for x, y in zip(a["rgba32"], b["rgba32"])) / (
                4096 * 255
            )
            if error <= image_tolerance:
                tolerated.append(
                    dict(path=name, comparison="RGBA32 mean error", error=error)
                )
                continue
        a, b = before.get("mesh"), after.get("mesh")
        if (
            mesh_tolerance
            and a
            and b
            and a["structure"] == b["structure"]
            and len(a["points"]) == len(b["points"])
        ):
            error = max(
                abs(x - y)
                for p, q in zip(a["points"], b["points"])
                for x, y in zip(p, q)
            )
            if error <= mesh_tolerance:
                tolerated.append(
                    dict(
                        path=name,
                        comparison="topology-ordered vertex max error",
                        error=error,
                    )
                )
                continue
        differences.append(dict(path=name, reason="content changed"))
    return dict(verified=not differences, differences=differences, tolerated=tolerated)
