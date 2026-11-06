"""coder-models.py against a fake Hugging Face, a fake router and a fake
systemctl. Run: python3 -m pytest -q test_coder_models.py"""
import hashlib
import http.server
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

SCRIPT = Path(__file__).with_name("coder-models.py")
REV = "a" * 40
BLOB = os.urandom(3 * 2**20 + 123)


class Fake(http.server.BaseHTTPRequestHandler):
    blob = BLOB
    calls = []

    def log_message(self, *a):
        pass

    def do_GET(self):
        Fake.calls.append((self.path, self.headers.get("Range")))
        if self.path.startswith("/api/models/org/model/tree/"):
            body = json.dumps([{"type": "file", "path": "m-Q4.gguf", "size": len(self.blob),
                                "lfs": {"oid": hashlib.sha256(BLOB).hexdigest()}},
                               {"type": "file", "path": "huge.gguf", "size": 2**60,
                                "lfs": {"oid": "0" * 64}}]).encode()
            return self._send(200, body)
        if self.path == f"/org/model/resolve/{REV}/m-Q4.gguf":
            start = 0
            rng = self.headers.get("Range")
            if rng:
                start = int(rng.split("=")[1].rstrip("-"))
            data = self.blob[start:]
            if not rng and getattr(Fake, "cut", False):
                Fake.cut = False                      # first try: drop half way
                self.send_response(200)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data[: len(data) // 2])
                self.wfile.flush()
                self.connection.close()
                return
            return self._send(206 if rng else 200, data)
        if self.path == "/health":
            return self._send(200, b"{}")
        if self.path == "/models":
            return self._send(200, json.dumps({"data": [{"id": "tiel-coder", "status": {"value": "loaded"}}]}).encode())
        self._send(404, b"")

    def _send(self, code, body):
        self.send_response(code)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture
def env(tmp_path):
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Fake)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    home = tmp_path / "home"
    (home / "llm-stack").mkdir(parents=True)
    (home / ".config/systemd/user").mkdir(parents=True)
    (home / ".config/systemd/user/coder.service").write_text("[Service]\n")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "systemctl").write_text(f"#!/bin/sh\necho \"$@\" >> {tmp_path}/systemctl.log\n")
    os.chmod(bindir / "systemctl", 0o755)
    model = home / "models/tiel.gguf"
    model.parent.mkdir(parents=True)
    model.write_bytes(b"x")
    (home / "llm-stack/coder-presets.ini").write_text(
        f"version = 1\n\n[tiel-coder]\nmodel = {model}\nc = 65536\ndevice = CUDA0\n"
        "n-cpu-moe = 12\nflash-attn = on\nspec-type = draft-mtp\n")
    (home / "run_coder.sh").write_text("#!/bin/bash\nARGS=(\n  --models-preset x\n)\n")
    e = dict(os.environ, HOME=str(home), HF_ENDPOINT=f"http://127.0.0.1:{srv.server_port}",
             CODER_PORT=str(srv.server_port), PATH=f"{bindir}:{os.environ['PATH']}")
    e.pop("HF_TOKEN", None)
    yield e, home, tmp_path
    srv.shutdown()


def run(env, *args, stdin=None):
    p = subprocess.run([sys.executable, str(SCRIPT), *args], env=env, input=stdin,
                       capture_output=True, text=True, timeout=60)
    return json.loads(p.stdout.strip().splitlines()[-1]) if p.stdout.strip() else p.stderr


def job(**kw):
    return json.dumps({"alias": "qcoder-js", "repo": "org/model", "revision": REV,
                       "files": ["m-Q4.gguf"], "size": len(BLOB),
                       "preset": {"c": 32768, "n-cpu-moe": 4, "temp": 0.3, "evil": "x"}, **kw})


def wait_state(env, alias, want, secs=30):
    t0 = time.time()
    while time.time() - t0 < secs:
        st = run(env, "status")
        s = st["models"].get(alias, {}).get("state")
        if s in want:
            return st
        time.sleep(0.3)
    raise AssertionError(st)


def test_ensure_downloads_resumes_verifies_and_adds_preset(env):
    e, home, tmp = env
    Fake.cut = True
    assert run(e, "ensure", stdin=job())["state"] == "downloading"
    st = wait_state(e, "qcoder-js", ("ready", "failed"))
    assert st["models"]["qcoder-js"]["state"] == "ready", st
    f = home / "models/coders/qcoder-js/m-Q4.gguf"
    assert f.read_bytes() == BLOB
    assert any(r for _, r in Fake.calls if r), "a resumed Range request"
    ini = (home / "llm-stack/coder-presets.ini").read_text()
    sec = ini.split("[qcoder-js]")[1]
    assert f"model = {f}" in sec and "device = CUDA0" in sec and "n-cpu-moe = 4" in sec
    assert "spec-type" not in sec and "evil" not in sec
    assert st["presets_changed"] is True
    assert run(e, "ensure", stdin=job())["state"] == "ready"
    assert run(e, "activate") == {"restarted": True}
    assert "restart coder" in (tmp / "systemctl.log").read_text()
    assert run(e, "activate") == {"restarted": False}


def test_bad_hash_is_refused(env):
    e, home, _ = env
    Fake.blob = BLOB[:-1] + b"\x00"
    try:
        run(e, "ensure", stdin=job())
        st = wait_state(e, "qcoder-js", ("ready", "failed"))
    finally:
        Fake.blob = BLOB
    assert st["models"]["qcoder-js"]["state"] == "failed"
    assert "SHA-256" in st["models"]["qcoder-js"]["error"]
    assert "[qcoder-js]" not in (home / "llm-stack/coder-presets.ini").read_text()


@pytest.mark.parametrize("bad", [{"alias": "tiel-coder"}, {"alias": "Bad Name"},
                                 {"revision": "main"}, {"files": ["../x.gguf"]},
                                 {"files": ["a.bin"]}, {"repo": "x"}])
def test_ensure_refuses_bad_jobs(env, bad):
    assert run(env[0], "ensure", stdin=job(**bad))["state"] == "refused"


def test_prune_keeps_protected_and_recent(env):
    e, home, _ = env
    for name, age in (("old", 100), ("new", 10), ("kept", 1000)):
        d = home / "models/coders" / name
        d.mkdir(parents=True)
        (d / "m.gguf").write_bytes(b"0" * 2**20)
    st = {"models": {"old": {"last_used": 1}, "new": {"last_used": 2}, "kept": {"last_used": 0}}}
    (home / "llm-stack/coder-models-state.json").write_text(json.dumps(st))
    out = run(e, "prune", stdin=json.dumps({"keep": ["kept"], "max_gb": 2.5 / 1024}))
    assert out["removed"] == ["old"]


def test_convert_router(env):
    e, home, _ = env
    (home / ".config/systemd/user/coder.service").unlink()
    (home / ".config/systemd/user/llama-server.service").write_text("[Service]\n")
    (home / "llm-stack/coder-presets.ini").unlink()
    (home / "run_llm.sh").write_text(
        "#!/usr/bin/env bash\nset -euo pipefail\nexport ROCM_PATH=\"/opt/rocm\"\n\nARGS=(\n"
        "  -m /home/p/models/T/tiel-UD-IQ4_XS.gguf\n  --alias tiel-coder\n  -c 65536\n"
        "  --host 0.0.0.0 --port 8080\n  --api-key-file /home/p/.coder-api-key\n  -ngl 999\n"
        "  -fa on\n  --jinja\n  --spec-type draft-mtp\n  --n-cpu-moe 8\n  --metrics\n)\n\n"
        "exec /home/p/llama.cpp/build/bin/llama-server \"${ARGS[@]}\" \"$@\"\n")
    out = run(e, "convert-router")
    assert out.get("alias") == "tiel-coder", out
    rs = (home / "run_llm.sh").read_text()
    assert "--models-preset" in rs and "--models-max 1" in rs and "--port 8080" in rs
    assert "-m " not in rs and "ROCM_PATH" in rs and "exec /home/p/llama.cpp" in rs
    ini = (home / "llm-stack/coder-presets.ini").read_text()
    assert "[tiel-coder]" in ini and "n-cpu-moe = 8" in ini and "jinja = true" in ini
    assert "flash-attn = on" in ini and "n-gpu-layers = 999" in ini and "metrics" not in ini
    assert subprocess.run(["bash", "-n", str(home / "run_llm.sh")]).returncode == 0
    assert run(e, "convert-router") == {"already": "router"}
    assert "restored" in run(e, "convert-router", "--undo")
    assert "-m /home/p" in (home / "run_llm.sh").read_text()


def test_disk_check_uses_published_sizes(env):
    # forge said 1 byte; Hugging Face says an exabyte. The published size wins.
    r = run(env[0], "ensure", stdin=job(files=["huge.gguf"], size=1))
    assert r["state"] == "refused" and "GB free" in r["error"], r


def test_status_records_a_dead_download(env):
    e, home, _ = env
    state = home / "llm-stack/coder-models-state.json"
    state.write_text(json.dumps({"models": {"qcoder-js": {
        "state": "downloading", "pid": 2**22 + 12345, "started": time.time() - 600}}}))
    st = run(e, "status")
    assert st["models"]["qcoder-js"]["state"] == "failed"
    saved = json.loads(state.read_text())["models"]["qcoder-js"]
    assert saved["state"] == "failed" and saved["pid"] is None
    assert "stopped" in saved["error"]
