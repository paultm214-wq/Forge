#!/usr/bin/env python3
"""
coder-models.py - which coding models this machine holds, for forge.

Lives on each coder machine (Apollo, Vulcan, Mercury) at
~/llm-stack/coder-models.py. forge on Gemini drives it over ssh; it never
listens on the network. Standard library only.

    coder-models.py status                 what is here, downloading, loaded
    coder-models.py ensure  < model.json   start fetching a model (returns at once)
    coder-models.py activate               restart the router if its presets changed
    coder-models.py revert ALIAS           drop a model that failed to load
    coder-models.py prune   < keep.json    free disk: least recently used first
    coder-models.py convert-router         Apollo only: single-model -> router mode
    coder-models.py convert-router --undo  put Apollo's old run script back

Every model is a section of the llama-server router's presets file
(~/llm-stack/coder-presets.ini). A request naming the section's alias loads
it; `--models-max 1` swaps one model out for another. Models fetched here
live in ~/models/coders/<alias>/; the installer's own model (tiel-coder) is
never deleted.

Downloads: plain HTTPS from Hugging Face at a pinned commit, resumable, each
file checked against the SHA-256 Hugging Face publishes before it is used.
"""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import http.client
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HOME = Path.home()
STACK = Path(os.environ.get("STACK_DIR", HOME / "llm-stack"))
MODELS = Path(os.environ.get("CODER_MODELS_DIR", HOME / "models" / "coders"))
PRESETS = STACK / "coder-presets.ini"
STATE = STACK / "coder-models-state.json"
LOCK = STACK / ".coder-models.lock"
LOGS = STACK / "coder-models-logs"
API_KEY = HOME / ".coder-api-key"
PORT = int(os.environ.get("CODER_PORT", "8080"))
HF = os.environ.get("HF_ENDPOINT", "https://huggingface.co").rstrip("/")
# \Z, not $: $ also matches before a final newline. An alias "x\n" wrote its
# section header over two lines, and its settings replaced the previous model's.
ALIAS_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,62}\Z")
FILE_RE = re.compile(r"^(?!/)(?!.*\.\.)[\w./+-]+\.gguf\Z", re.I)
REPO_RE = re.compile(r"^[\w.-]+/[\w.-]+\Z")
SHA_RE = re.compile(r"^[0-9a-f]{40}\Z")
OID_RE = re.compile(r"^[0-9a-f]{64}\Z")
# Router-level flags: on the router's command line, never in a preset.
ROUTER_FLAGS = {"host", "port", "api-key-file", "api-key", "metrics", "alias", "a",
                "models-preset", "models-max", "sleep-idle-seconds", "models-dir"}
# Keys a preset from forge may set. Anything else is ignored.
PRESET_KEYS = {"c", "ctx-size", "n-cpu-moe", "n-gpu-layers", "parallel", "flash-attn",
               "cache-type-k", "cache-type-v", "batch-size", "ubatch-size", "jinja",
               "spec-type", "spec-draft-n-max", "temp", "top-p", "top-k", "min-p",
               "repeat-penalty", "reasoning-format", "chat-template-kwargs"}


# ------------------------------------------------------------------ state

@contextlib.contextmanager
def locked():
    STACK.mkdir(parents=True, exist_ok=True)
    with open(LOCK, "w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        yield


def load_state() -> dict:
    try:
        return json.loads(STATE.read_text())
    except (OSError, ValueError):
        return {"models": {}}


def save_state(st: dict) -> None:
    tmp = STATE.with_name(f".{STATE.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(st, indent=2))
    tmp.replace(STATE)


def update(alias: str, **kw) -> None:
    with locked():
        st = load_state()
        st.setdefault("models", {}).setdefault(alias, {}).update(kw)
        save_state(st)


# ---------------------------------------------------------------- presets

def read_presets() -> tuple[list[str], dict[str, dict[str, str]]]:
    """(lines before the first section, {section: {key: value}}), in order."""
    head, secs, cur = [], {}, None
    try:
        text = PRESETS.read_text()
    except OSError:
        text = "version = 1\n"
    for ln in text.splitlines():
        s = ln.strip()
        m = re.match(r"^\[([^\]]+)\]$", s)
        if m:
            cur = m.group(1)
            secs[cur] = {}
        elif cur is None:
            head.append(ln)
        elif s and not s.startswith(("#", ";")) and "=" in s:
            k, v = s.split("=", 1)
            secs[cur][k.strip()] = v.strip()
    return head, secs


def write_presets(head: list[str], secs: dict[str, dict[str, str]]) -> None:
    out = [ln for ln in head if ln.strip()] or ["version = 1"]
    for name, kv in secs.items():
        out += ["", f"[{name}]"] + [f"{k} = {v}" for k, v in kv.items()]
    tmp = PRESETS.with_suffix(".tmp")
    tmp.write_text("\n".join(out) + "\n")
    tmp.replace(PRESETS)


def presets_hash() -> str:
    try:
        return hashlib.sha256(PRESETS.read_bytes()).hexdigest()[:16]
    except OSError:
        return ""


def base_preset(secs: dict[str, dict[str, str]]) -> dict[str, str]:
    """This machine's tuned settings (device, cache types, batch sizes) from
    the installer's section, without what belongs to one model."""
    src = secs.get("tiel-coder") or (next(iter(secs.values())) if secs else {})
    drop = {"model", "n-cpu-moe", "spec-type", "spec-draft-n-max", "temp", "top-p", "top-k",
            "min-p", "mmproj", "c", "ctx-size", "chat-template-kwargs", "reasoning-format"}
    return {k: v for k, v in src.items() if k not in drop}


# ---------------------------------------------------------------- service

def service() -> str:
    d = HOME / ".config" / "systemd" / "user"
    if (d / "coder.service").exists():
        return "coder"
    return "llama-server"


def api(path: str, body: dict | None = None, timeout: float = 10):
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}",
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"})
    try:
        req.add_header("Authorization", "Bearer " + API_KEY.read_text().strip())
    except OSError:
        pass
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
    return json.loads(raw) if raw.strip() else {}


def loaded_models() -> list[str] | None:
    try:
        data = api("/models")
    except (urllib.error.URLError, OSError, ValueError):
        return None
    out = []
    for m in data.get("data", []):
        status = m.get("status")
        val = status.get("value") if isinstance(status, dict) else status
        if val in ("loaded", "loading"):
            out.append(m.get("id"))
    return out


def is_router() -> bool:
    return PRESETS.exists() and "--models-preset" in run_script_text()


def run_script() -> Path:
    return HOME / ("run_coder.sh" if service() == "coder" else "run_llm.sh")


def run_script_text() -> str:
    try:
        return run_script().read_text()
    except OSError:
        return ""


# ------------------------------------------------------------- downloads

def hf_token() -> str | None:
    t = os.environ.get("HF_TOKEN", "").strip()
    if t:
        return t
    try:
        return (Path(os.environ.get("HF_HOME", HOME / ".cache" / "huggingface")) / "token") \
            .read_text().strip() or None
    except OSError:
        return None


def published(repo: str, revision: str) -> dict[str, tuple[int, str]]:
    """{path: (size, sha256)} straight from Hugging Face - the check does not
    rely on what forge was told."""
    url = f"{HF}/api/models/{repo}/tree/{revision}?recursive=true"
    req = urllib.request.Request(url, headers={"User-Agent": "forge-coder-models"})
    tok = hf_token()
    if tok:
        req.add_header("Authorization", f"Bearer {tok}")
    with urllib.request.urlopen(req, timeout=60) as r:
        entries = json.load(r)
    return {e["path"]: (int(e.get("size") or 0), (e.get("lfs") or {}).get("oid", ""))
            for e in entries if e.get("type") == "file"}


def fetch(repo: str, revision: str, path: str, dest: Path, size: int, alias: str,
          done_before: int, total: int) -> None:
    part = dest.with_name(dest.name + ".part")
    url = f"{HF}/{repo}/resolve/{revision}/{urllib.parse.quote(path)}"
    for attempt in range(1, 7):
        have = part.stat().st_size if part.exists() else 0
        if size and have > size:
            # Longer than the published file: two writers appended to it, or
            # a server sent extra bytes. It can never verify - start over
            # (it used to stop here and fail every later ensure the same way).
            update(alias, note=f"{path}: partial file larger than published ({have} > {size}); "
                               f"starting over")
            part.unlink()
            have = 0
        if have >= size > 0:
            break
        req = urllib.request.Request(url, headers={"User-Agent": "forge-coder-models"})
        tok = hf_token()
        if tok:
            req.add_header("Authorization", f"Bearer {tok}")
        if have:
            req.add_header("Range", f"bytes={have}-")
        try:
            with urllib.request.urlopen(req, timeout=60) as r, open(part, "ab" if have else "wb") as fh:
                if have and r.status != 206:      # the server ignored Range: start over
                    fh.truncate(0)
                    have = 0
                last = time.monotonic()
                while True:
                    buf = r.read(8 << 20)
                    if not buf:
                        break
                    fh.write(buf)
                    have += len(buf)
                    if time.monotonic() - last > 5:
                        update(alias, bytes=done_before + have, total=total)
                        last = time.monotonic()
        except (urllib.error.URLError, OSError, TimeoutError, http.client.HTTPException) as exc:
            update(alias, note=f"{path}: attempt {attempt} failed ({exc}); resuming")
            time.sleep(min(60, 10 * attempt))
            continue
        if size and have >= size:
            break
    if not part.exists() or (size and part.stat().st_size != size):
        raise RuntimeError(f"{path}: download incomplete after 6 attempts")
    part.replace(dest)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for buf in iter(lambda: fh.read(8 << 20), b""):
            h.update(buf)
    return h.hexdigest()


def download_worker(alias: str) -> int:
    """Runs detached: fetch, verify, add the preset section."""
    st = load_state()
    job = st["models"][alias]["job"]
    repo, rev, files = job["repo"], job["revision"], job["files"]
    target = MODELS / alias
    target.mkdir(parents=True, exist_ok=True)
    try:
        pub = published(repo, rev)
        # GGUF weights must have a published digest. A missing LFS oid is
        # not permission to activate an unchecked file (including cached ones).
        for f in files:
            if f not in pub or not OID_RE.fullmatch(pub[f][1] or ""):
                raise RuntimeError(f"{f}: missing or invalid published SHA-256; refusing unverified model")
        total = sum(pub.get(f, (0, ""))[0] for f in files)
        done = 0
        for f in files:
            if f not in pub:
                raise RuntimeError(f"{f} is not in {repo} at {rev[:10]}")
            size, oid = pub[f]
            dest = target / Path(f).name
            if not (dest.exists() and dest.stat().st_size == size):
                update(alias, state="downloading", bytes=done, total=total, file=f)
                fetch(repo, rev, f, dest, size, alias, done, total)
            update(alias, state="verifying", bytes=done + size, total=total)
            if sha256(dest) != oid:
                dest.unlink()
                raise RuntimeError(f"{f}: SHA-256 does not match what Hugging Face publishes "
                                   f"(deleted; ensure again to re-download)")
            done += size
        # An update to a new commit may rename files: drop the old ones.
        names = {Path(f).name for f in files}
        for old in target.iterdir():
            if old.is_file() and old.name not in names and old.suffix in (".gguf", ".part"):
                old.unlink()
        with locked():
            head, secs = read_presets()
            kv = base_preset(secs)
            kv.update({k: str(v).lower() if isinstance(v, bool) else str(v)
                       for k, v in (job.get("preset") or {}).items() if k in PRESET_KEYS})
            kv = {"model": str(target / Path(files[0]).name), **kv}
            secs[alias] = kv
            write_presets(head, secs)
            st = load_state()
            m = st["models"].setdefault(alias, {})
            m.update(state="ready", bytes=total, total=total, error=None, pid=None,
                     repo=repo, revision=rev, files=files, size=total,
                     last_used=time.time())
            save_state(st)
        return 0
    except Exception as exc:
        update(alias, state="failed", error=str(exc)[:400], pid=None)
        return 1


def pid_alive(pid) -> bool:
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, TypeError, ValueError):
        return False


# --------------------------------------------------------------- commands

def cmd_status() -> dict:
    # A download whose process died is recorded as failed (it used to be
    # reported as failed but stay "downloading" in the state file, so the
    # next ensure could take it for a live download).
    with locked():
        st = load_state()
        dead = False
        for m in st.get("models", {}).values():
            if m.get("state") in ("downloading", "verifying", "queued") and not pid_alive(m.get("pid")) \
                    and time.time() - (m.get("started") or 0) > 10:
                m.update(state="failed", pid=None,
                         error=m.get("error") or "the download process stopped")
                dead = True
        if dead:
            save_state(st)
    _, secs = read_presets()
    models = {}
    for alias, kv in secs.items():
        p = Path(kv.get("model", ""))
        models[alias] = {"state": "ready" if p.exists() else "missing", "model": str(p),
                         "managed": str(p).startswith(str(MODELS))}
    for alias, m in st.get("models", {}).items():
        if alias not in models or m.get("state") != "ready":
            models[alias] = {**models.get(alias, {}),
                             **{k: m.get(k) for k in ("state", "bytes", "total", "error", "file")}}
    du = shutil.disk_usage(MODELS if MODELS.exists() else HOME)
    return {"host": os.uname().nodename, "router": is_router(), "service": service(),
            "models": models, "loaded": loaded_models(),
            "presets_changed": presets_hash() != st.get("active_hash"),
            "disk_free_gb": round(du.free / 2**30, 1),
            "managed_gb": round(sum(f.stat().st_size for f in MODELS.rglob("*") if f.is_file())
                                / 2**30, 1) if MODELS.exists() else 0.0}


def valid_job(job: dict) -> str | None:
    if not ALIAS_RE.match(str(job.get("alias", ""))):
        return "alias must be lowercase letters, digits, . _ -"
    if job["alias"] == "tiel-coder":
        return "tiel-coder is the installer's model"
    if not REPO_RE.match(str(job.get("repo", ""))):
        return "bad repo"
    if not SHA_RE.match(str(job.get("revision", ""))):
        return "revision must be a full commit SHA"
    files = job.get("files") or []
    if not files or not all(isinstance(f, str) and FILE_RE.match(f) for f in files):
        return "files must be .gguf paths in the repo"
    return None


IN_FLIGHT = ("queued", "downloading", "verifying")


def same_job(m: dict, job: dict) -> bool:
    """Whether the state entry `m` is (or is fetching) exactly this job. A
    download in progress has its repo, revision and files only in m["job"]:
    the top-level ones are filled in when it finishes, so comparing those
    took every running download for a different one and started a second
    worker on the same .part file."""
    cur = (m.get("job") or {}) if m.get("state") in IN_FLIGHT else m
    return ((cur.get("repo"), cur.get("revision"), cur.get("files"))
            == (job["repo"], job["revision"], job["files"]))


def cmd_ensure(job: dict) -> dict:
    err = valid_job(job)
    if err:
        return {"state": "refused", "error": err}
    alias = job["alias"]
    if not is_router():
        return {"state": "refused", "error": "this machine's llama-server is not in router mode "
                                              "(run coder-models.py convert-router)"}
    # Disk check against what Hugging Face publishes for these files, not the
    # size forge sent (which can be missing or stale). Bytes already here
    # (finished files, partial downloads) are not needed again.
    need = int(job.get("size") or 0)
    try:
        pub = published(job["repo"], job["revision"])
        if all(f in pub for f in job["files"]):
            need = 0
            for f in job["files"]:
                dest = MODELS / alias / Path(f).name
                part = dest.with_name(dest.name + ".part")
                have = dest.stat().st_size if dest.exists() else \
                    part.stat().st_size if part.exists() else 0
                need += max(0, pub[f][0] - have)
    except (urllib.error.URLError, OSError, ValueError, KeyError, http.client.HTTPException):
        pass                                # the download worker checks again
    with locked():
        st = load_state()
        m = st.setdefault("models", {}).get(alias, {})
        _, secs = read_presets()
        same = same_job(m, job)
        if m.get("state") == "ready" and same and alias in secs \
                and Path(secs[alias].get("model", "")).exists():
            m["last_used"] = time.time()
            save_state(st)
            return {"state": "ready"}
        if m.get("state") in IN_FLIGHT and same and pid_alive(m.get("pid")):
            return {"state": m["state"], "bytes": m.get("bytes"), "total": m.get("total")}
        if m.get("state") in IN_FLIGHT and pid_alive(m.get("pid")):
            # A different job for this alias is still writing its files.
            return {"state": "refused", "error": f"{alias} is still downloading another "
                                                  f"revision (pid {m.get('pid')})"}
        free = shutil.disk_usage(HOME).free
        if need and free < need + 5 * 2**30:
            return {"state": "refused", "error": f"needs {need / 2**30:.1f} GB, "
                                                  f"{free / 2**30:.1f} GB free (prune first)"}
        LOGS.mkdir(parents=True, exist_ok=True)
        st["models"][alias] = {"state": "queued", "job": job, "repo": None, "error": None,
                               "started": time.time()}
        save_state(st)
        # Started and recorded under the lock: another ensure in between saw
        # "queued" with no pid, took it for dead and started a second worker;
        # and the worker's own updates wait for the lock, so this one cannot
        # overwrite a state it has already moved on from.
        with open(LOGS / f"{alias}.log", "ab") as log:
            p = subprocess.Popen([sys.executable, os.path.abspath(__file__), "_download", alias],
                                 stdout=log, stderr=log, stdin=subprocess.DEVNULL,
                                 start_new_session=True)
        st["models"][alias].update(pid=p.pid, state="downloading")
        save_state(st)
    return {"state": "downloading"}


def cmd_activate(timeout: float = 180) -> dict:
    """Restart the router so it reads new presets. forge calls this only
    while it sends this machine no work."""
    h = presets_hash()
    st = load_state()
    if h == st.get("active_hash"):
        return {"restarted": False}
    r = subprocess.run(["systemctl", "--user", "restart", service()], capture_output=True,
                       text=True, timeout=120)
    if r.returncode != 0:
        return {"restarted": False, "error": (r.stderr or r.stdout).strip()[-400:]}
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        try:
            api("/health", timeout=3)
            break
        except (urllib.error.URLError, OSError, ValueError):
            time.sleep(2)
    else:
        return {"restarted": True, "error": "router did not come back on /health"}
    with locked():
        st = load_state()
        st["active_hash"] = h
        save_state(st)
    return {"restarted": True}


def cmd_revert(alias: str) -> dict:
    if alias == "tiel-coder":
        return {"error": "tiel-coder is the installer's model"}
    with locked():
        head, secs = read_presets()
        secs.pop(alias, None)
        write_presets(head, secs)
        st = load_state()
        st.setdefault("models", {}).setdefault(alias, {}).update(state="failed-load")
        save_state(st)
    return {"reverted": alias}


def cmd_prune(req: dict) -> dict:
    """Delete fetched models, least recently used first, until they take at
    most max_gb. Never one in `keep`, the installer's, or a loaded one."""
    keep = set(req.get("keep") or []) | set(loaded_models() or []) | {"tiel-coder"}
    max_b = float(req.get("max_gb", 120)) * 2**30
    st = load_state()
    dirs = sorted((d for d in MODELS.iterdir() if d.is_dir()) if MODELS.exists() else [],
                  key=lambda d: st.get("models", {}).get(d.name, {}).get("last_used", 0))
    size = lambda d: sum(f.stat().st_size for f in d.rglob("*") if f.is_file())
    total = sum(size(d) for d in dirs)
    removed = []
    for d in dirs:
        if total <= max_b:
            break
        if d.name in keep:
            continue
        m = st.get("models", {}).get(d.name, {})
        if m.get("state") in ("downloading", "verifying") and pid_alive(m.get("pid")):
            continue
        total -= size(d)
        shutil.rmtree(d, ignore_errors=True)
        removed.append(d.name)
    if removed:
        with locked():
            head, secs = read_presets()
            for a in removed:
                secs.pop(a, None)
            write_presets(head, secs)
            st = load_state()
            for a in removed:
                st.get("models", {}).pop(a, None)
            save_state(st)
    return {"removed": removed, "managed_gb": round(total / 2**30, 1)}


SHORT_FLAGS = {"m": "model", "ngl": "n-gpu-layers", "fa": "flash-attn", "b": "batch-size",
               "ub": "ubatch-size", "np": "parallel"}


def is_flag(word: str) -> bool:
    """`-c`, `--port`, `--ctx-size=8192` - but not a negative number (`-1`)."""
    return word.startswith("-") and len(word) > 1 and not re.match(r"^-\d", word)


def parse_server_args(words: list[str]) -> tuple[str, dict[str, str], list[tuple[str, str | None]]]:
    """(alias, preset keys, router flags) from llama-server's arguments.
    `--seed -1` and `-n -1` used to read the -1 as a flag named "1", and
    `--ctx-size=65536` became a key called "ctx-size=65536"."""
    alias, preset, router = "tiel-coder", {}, []
    i = 0
    while i < len(words):
        w = words[i]
        if not is_flag(w):
            i += 1
            continue
        flag, val = w, None
        if w.startswith("--") and "=" in w:
            flag, val = w.split("=", 1)
        elif i + 1 < len(words) and not is_flag(words[i + 1]):
            val = words[i + 1]
            i += 1
        i += 1
        key = flag.lstrip("-")
        key = SHORT_FLAGS.get(key, key)
        if key in ("alias", "a"):
            alias = val or alias
        elif key in ROUTER_FLAGS:
            router.append((flag, val))
        else:
            preset[key] = "true" if val is None else val
    return alias, preset, router


def cmd_convert_router(undo: bool = False) -> dict:
    """Apollo's llama-server runs one model with --alias. The router serves
    the same model under the same name - forge sees no difference - and can
    hold others. The old run script is kept and --undo restores it."""
    rs = run_script()
    bak = rs.with_name(rs.name + ".single-model")
    if undo:
        if not bak.exists():
            return {"error": f"no {bak.name} to restore"}
        shutil.copy(bak, rs)
        subprocess.run(["systemctl", "--user", "restart", service()], timeout=120)
        return {"restored": str(rs)}
    if is_router():
        return {"already": "router"}
    text = rs.read_text()
    m = re.search(r"^ARGS=\($(.*?)^\)$", text, re.S | re.M)
    if not m:
        return {"error": f"no ARGS=( ... ) array in {rs}"}
    words = shlex.split(m.group(1), comments=True)
    alias, preset, router = parse_server_args(words)
    if "model" not in preset:
        return {"error": "the run script names no model (-m)"}
    shutil.copy(rs, bak)
    head, secs = read_presets()
    secs = {alias: preset, **{k: v for k, v in secs.items() if k != alias}}
    write_presets(head, secs)
    env_lines = text[:m.start()].rstrip("\n")
    args = ["  --models-preset " + shlex.quote(str(PRESETS)), "  --models-max 1"]
    args += [f"  {flag} {shlex.quote(val)}" if val is not None else f"  {flag}"
             for flag, val in router]
    tail = text[m.end():].strip("\n")
    rs.write_text(env_lines + "\n# Router mode (coder-models.py convert-router): one model at a "
                  "time, by name.\nARGS=(\n" + "\n".join(args) + "\n)\n" + tail + "\n")
    os.chmod(rs, 0o755)
    r = subprocess.run(["bash", "-n", str(rs)], capture_output=True, text=True)
    if r.returncode != 0:
        shutil.copy(bak, rs)
        return {"error": f"generated script failed bash -n, restored: {r.stderr.strip()}"}
    subprocess.run(["systemctl", "--user", "restart", service()], timeout=120)
    with locked():
        st = load_state()
        st["active_hash"] = presets_hash()
        save_state(st)
    return {"converted": str(rs), "backup": str(bak), "alias": alias,
            "preset_keys": sorted(preset)}


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    cmd, rest = argv[0], argv[1:]
    if cmd == "_download":
        return download_worker(rest[0])
    if cmd == "status":
        out = cmd_status()
    elif cmd == "ensure":
        out = cmd_ensure(json.load(sys.stdin))
    elif cmd == "activate":
        out = cmd_activate()
    elif cmd == "revert":
        out = cmd_revert(rest[0])
    elif cmd == "prune":
        out = cmd_prune(json.load(sys.stdin))
    elif cmd == "convert-router":
        out = cmd_convert_router(undo="--undo" in rest)
    else:
        print(f"unknown command {cmd!r}\n{__doc__}", file=sys.stderr)
        return 2
    print(json.dumps(out))
    return 1 if isinstance(out, dict) and out.get("error") else 0


if __name__ == "__main__":
    signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    sys.exit(main(sys.argv[1:]))
