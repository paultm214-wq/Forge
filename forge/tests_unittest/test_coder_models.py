"""coder-models.py: fixes 1-3 of the review."""

import http.server
import os
import shlex
import threading
import unittest
from unittest import mock

from _load import HOME, coder_models

cm = coder_models()
JOB = {"alias": "qwen-x", "repo": "a/b", "revision": "0" * 40, "files": ["x.gguf"]}


class FakePopen:
    started = 0

    def __init__(self, *a, **kw):
        FakePopen.started += 1
        self.pid = os.getpid()          # alive for as long as the test runs


class Ensure(unittest.TestCase):
    """Fix 1: a download in progress is recognised, so no second worker
    writes into the same .part file."""

    def setUp(self):
        cm.STACK.mkdir(parents=True, exist_ok=True)
        cm.PRESETS.write_text("version = 1\n")
        (HOME / "run_llm.sh").write_text("ARGS=(\n  --models-preset x\n)\n")
        if cm.STATE.exists():
            cm.STATE.unlink()
        FakePopen.started = 0
        # subprocess is the shared stdlib module: patch it for this test only.
        for p in (mock.patch.object(cm, "published", lambda repo, rev: {"x.gguf": (10, "")}),
                  mock.patch.object(cm.subprocess, "Popen", FakePopen)):
            p.start()
            self.addCleanup(p.stop)

    def test_second_ensure_joins_the_running_download(self):
        self.assertEqual(cm.cmd_ensure(dict(JOB))["state"], "downloading")
        again = cm.cmd_ensure(dict(JOB))
        self.assertEqual(again["state"], "downloading")
        self.assertEqual(FakePopen.started, 1)

    def test_other_revision_waits_for_the_running_one(self):
        cm.cmd_ensure(dict(JOB))
        other = cm.cmd_ensure({**JOB, "revision": "1" * 40})
        self.assertEqual(other["state"], "refused")
        self.assertEqual(FakePopen.started, 1)

    def test_same_job_compares_the_job_while_in_flight(self):
        self.assertTrue(cm.same_job({"state": "downloading", "job": JOB, "repo": None}, JOB))
        self.assertTrue(cm.same_job({"state": "ready", "repo": "a/b", "revision": "0" * 40,
                                     "files": ["x.gguf"]}, JOB))
        self.assertFalse(cm.same_job({"state": "queued", "job": {**JOB, "files": ["y.gguf"]}}, JOB))


class Fetch(unittest.TestCase):
    """Fix 2: a .part larger than the published file starts over."""

    def test_oversized_part_is_downloaded_again(self):
        body = b"x" * 100

        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            cm.HF = f"http://127.0.0.1:{srv.server_port}"
            dest = HOME / "models" / "coders" / "qwen-x" / "x.gguf"
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.with_name("x.gguf.part").write_bytes(b"y" * 150)
            cm.STACK.mkdir(parents=True, exist_ok=True)
            cm.fetch("a/b", "0" * 40, "x.gguf", dest, 100, "qwen-x", 0, 100)
            self.assertEqual(dest.read_bytes(), body)
        finally:
            srv.shutdown()


class ConvertRouter(unittest.TestCase):
    """Fix 3: negative values and --flag=value."""

    def test_arguments(self):
        alias, preset, router = cm.parse_server_args(shlex.split(
            "-m /m/t.gguf --alias tiel-coder -c 65536 -ngl 99 --seed -1 -n -1 "
            "--ctx-size=65536 --port 8080 --metrics --sleep-idle-seconds -1"))
        self.assertEqual(alias, "tiel-coder")
        self.assertEqual(preset, {"model": "/m/t.gguf", "c": "65536", "n-gpu-layers": "99",
                                  "seed": "-1", "n": "-1", "ctx-size": "65536"})
        self.assertEqual(router, [("--port", "8080"), ("--metrics", None),
                                  ("--sleep-idle-seconds", "-1")])

    def test_converted_script(self):
        rs = HOME / "run_llm.sh"
        rs.write_text("#!/bin/bash\nexport X=1\nARGS=(\n  -m /m/t.gguf --alias tiel-coder\n"
                      "  -c 65536 --seed -1 --port=8080 --host 0.0.0.0\n)\n"
                      'exec llama-server "${ARGS[@]}"\n')
        cm.STACK.mkdir(parents=True, exist_ok=True)       # the installer makes it
        if cm.PRESETS.exists():
            cm.PRESETS.unlink()
        real_run = cm.subprocess.run

        def fake_run(cmd, **kw):          # no systemctl here; `bash -n` runs for real
            if cmd[0] == "bash":
                return real_run(cmd, **kw)
            return type("P", (), {"returncode": 0, "stderr": "", "stdout": ""})()

        with mock.patch.object(cm.subprocess, "run", fake_run):
            out = cm.cmd_convert_router()
        self.assertEqual(out.get("alias"), "tiel-coder", out)
        text = rs.read_text()
        self.assertIn("--port 8080", text)
        self.assertIn("--host 0.0.0.0", text)
        _, secs = cm.read_presets()
        self.assertEqual(secs["tiel-coder"]["seed"], "-1")


if __name__ == "__main__":
    unittest.main()
