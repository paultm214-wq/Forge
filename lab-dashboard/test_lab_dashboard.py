"""Controls on the lab dashboard: the agent's fixed action list, the token,
the hub's checks, and the forge buttons.   python3 -m pytest -q test_lab_dashboard.py"""
import importlib.util
import json
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent


def load(name, file):
    spec = importlib.util.spec_from_file_location(name, HERE / file)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


agent = load("lab_agent", "lab-agent.py")
hubmod = load("lab_dashboard", "lab-dashboard.py")


class Router:
    def __init__(self):
        self.loaded, self.unloads = {"tiel-coder"}, []
        r = self

        class H(BaseHTTPRequestHandler):
            def _send(self, obj):
                b = json.dumps(obj).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(b)))
                self.end_headers()
                self.wfile.write(b)

            def do_GET(self):
                self._send({"data": [{"id": "tiel-coder", "status": {"value": "loaded" if r.loaded else "unloaded"}}]})

            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                r.unloads.append(json.loads(self.rfile.read(n))["model"])
                r.loaded.clear()
                self._send({"success": True})

            def log_message(self, *a):
                pass
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}"


def cfg_with(**kw):
    c = dict(agent.DEFAULTS)
    c.update(kw)
    return c


def test_no_token_no_controls():
    c = cfg_with(llm=[{"name": "coder", "url": "http://x", "unit": "coder"}])
    ctl = agent.Controls(c, [agent.LlamaWatch(s) for s in c["llm"]])
    assert ctl.available() == []
    assert ctl.start("restart:coder")[0] == 400


def test_the_action_list_is_fixed_and_unload_works(tmp_path, monkeypatch):
    monkeypatch.setattr(agent, "ACTIONS_LOG", tmp_path / "actions.log")
    r = Router()
    c = cfg_with(control_token="s3cret", llm=[{"name": "coder", "url": r.url, "unit": "coder"}],
                 sd=[{"name": "sd-server", "url": "http://127.0.0.1:9", "unit": "sd-server"}],
                 control_units=["strata", "bad unit; rm -rf /"])
    w = agent.LlamaWatch(c["llm"][0])
    w.router = True
    ctl = agent.Controls(c, [w])
    ids = {a["id"] for a in ctl.available()}
    assert {"clear_vram", "unload:coder", "restart:coder", "restart:sd-server",
            "restart:strata", "stop:strata", "start:strata"} <= ids
    assert not any("rm" in i for i in ids)              # a unit name that is not a name is ignored
    assert ctl.start("restart:sshd")[0] == 400
    code, out = ctl.start("unload:coder")
    assert code == 202
    for _ in range(50):
        if ctl.snapshot()["recent"]:
            break
        time.sleep(0.05)
    rec = ctl.snapshot()["recent"][-1]
    assert rec["ok"] and "unloaded tiel-coder" in rec["message"] and r.unloads == ["tiel-coder"]
    assert json.loads((tmp_path / "actions.log").read_text().splitlines()[-1])["action"] == "unload:coder"


def test_lan_only():
    c = {"lan": "192.168.50.0/24"}
    assert hubmod.lan_client(c, "127.0.0.1") and hubmod.lan_client(c, "192.168.50.20")
    assert not hubmod.lan_client(c, "10.0.0.5") and not hubmod.lan_client(c, "nonsense")


class FakeAgent:
    def __init__(self, token):
        self.got = []
        me = self

        class H(BaseHTTPRequestHandler):
            def do_GET(self):
                b = json.dumps({"host": {}, "gpus": [], "llm": [], "sd": [], "services": {},
                                "controls": {"available": [{"id": "clear_vram", "label": "Clear VRAM"}]}}).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(b)))
                self.end_headers()
                self.wfile.write(b)

            def do_POST(self):
                if self.headers.get("X-Lab-Control") != token:
                    return self.send_error(403)
                n = int(self.headers.get("Content-Length") or 0)
                me.got.append(json.loads(self.rfile.read(n))["action"])
                b = b'{"ok": true, "message": "started"}'
                self.send_response(202)
                self.send_header("Content-Length", str(len(b)))
                self.end_headers()
                self.wfile.write(b)

            def log_message(self, *a):
                pass
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.port = self.srv.server_address[1]


@pytest.fixture
def hub(tmp_path):
    fa = FakeAgent("tok")
    cfg = dict(hubmod.DEFAULTS)
    cfg.update(control_token="tok", forge_status=str(tmp_path / "status.json"),
               forge=str(tmp_path / "forge.py"), forge_config=str(tmp_path / "config.toml"),
               nodes=[{"name": "Apollo", "host": "127.0.0.1", "port": fa.port}])
    h = hubmod.Hub(cfg)
    for _ in range(40):
        if (h.nodes.get("Apollo") or {}).get("data"):
            break
        time.sleep(0.1)
    return h, fa, tmp_path


def test_hub_forwards_only_offered_actions(hub):
    h, fa, _ = hub
    assert h.node_action("Apollo", "clear_vram")[0] == 202 and fa.got == ["clear_vram"]
    assert h.node_action("Apollo", "restart:sshd")[0] == 400
    assert h.node_action("Nope", "clear_vram")[0] == 404


def test_forge_buttons(hub, monkeypatch):
    h, _, tmp = hub
    run = tmp / "runs" / "r1"
    run.mkdir(parents=True)
    (run / "plan.json").write_text(json.dumps({"chunks": [{"id": "c1"}, {"id": "c2"}]}))
    (run / "state.json").write_text(json.dumps({"chunks": {"c1": {"status": "verified"},
                                                           "c2": {"status": "failed"}}}))
    (tmp / "forge.py").write_text("import json, sys\nopen(sys.argv[0] + '.log', 'a').write("
                                  "json.dumps(sys.argv[1:]) + '\\n')\n")
    (tmp / "status.json").write_text(json.dumps({"pid": 999999, "run": "r1", "run_dir": str(run),
                                                  "active": False}))
    monkeypatch.setattr(hubmod.shutil, "which", lambda n: None)        # no systemd-run here
    assert h.forge_command("retest_failed")[0] == 202
    assert h.forge_command("resume")[0] == 202
    for _ in range(50):
        log = tmp / "forge.py.log"
        if log.exists() and len(log.read_text().splitlines()) == 2:
            break
        time.sleep(0.1)
    calls = [json.loads(x) for x in (tmp / "forge.py.log").read_text().splitlines()]
    assert ["retest", str(run), "c2"] == calls[0][2:] or ["retest", str(run), "c2"] == calls[1][2:]
    assert any(c[2:] == ["resume", str(run)] for c in calls)
    import os
    (tmp / "status.json").write_text(json.dumps({"pid": os.getpid(), "run": "r1", "run_dir": str(run),
                                                  "active": True}))
    assert h.forge_command("resume")[0] == 409                        # still running


def test_page_and_icon():
    assert hubmod.png_icon()[:8] == b"\x89PNG\r\n\x1a\n"
    assert "apple-mobile-web-app-capable" in hubmod.PAGE and "slider" in hubmod.PAGE


class FakeStrata:
    """Strata's endpoints as docs/DETAILS.md describes them: /metrics is JSON
    unless asked for text/plain."""

    def __init__(self, state="answering", tok=37.5):
        me = self
        self.state, self.tok = state, tok

        class H(BaseHTTPRequestHandler):
            def _send(self, body, ctype):
                b = body.encode()
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(b)))
                self.end_headers()
                self.wfile.write(b)

            def do_GET(self):
                path = self.path.split("?")[0]
                if path == "/health":
                    return self._send('{"status": "ok", "model": "strata", "loaded": true}', "application/json")
                if path == "/props":
                    return self._send('{"model_alias": "qwen3.8-flash-next", "total_slots": 1}', "application/json")
                if path == "/slots":
                    return self._send('[{"id": 0, "is_processing": true, "n_prompt_tokens": 9000}]', "application/json")
                if path == "/metrics":
                    if "text/plain" not in (self.headers.get("Accept") or ""):
                        return self._send('{"totals": {}}', "application/json")
                    states = "".join(f'strata:live_state{{state="{s}"}} {1 if s == me.state else 0}\n'
                                     for s in ("idle", "reading the prompt", "answering"))
                    return self._send(states + f"strata:live_tok_s {me.tok}\n"
                                      "strata:last_decode_tok_s 41.2\nstrata:live_prefill_tok_s_mean 612.0\n"
                                      "vllm:num_requests_waiting 2\n", "text/plain")
                self.send_error(404)

            def log_message(self, *a):
                pass
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}"


def test_strata_tokens_per_second():
    s = FakeStrata()
    w = agent.LlamaWatch({"name": "strata", "url": s.url})
    out = w.poll()
    assert out["state"] == "generating" and out["tps"] == 37.5
    assert out["last_gen_tps"] == 41.2 and out["last_prompt_tps"] == 612.0 and out["queued"] == 2
    assert out["model"] == "qwen3.8-flash-next"
    s.state, s.tok = "reading the prompt", 0
    out = w.poll()
    assert out["state"] == "prompt" and out["tps"] == 0.0
    s.state = "idle"
    assert w.poll()["state"] == "idle"


def test_toolchain_health_is_visible_without_a_run(hub):
    h, _, tmp = hub
    health = tmp / "health.json"
    h.cfg["toolchain_health"] = str(health)
    health.write_text(json.dumps({"observed_at": "2000-01-01T00:00:00+00:00",
        "host": {"hostname": "another host", "architecture": "other"},
        "toolchains": [{"name": "python-app", "status": "installed", "smoke": {"status": "passed", "validated_at": "2000-01-01T00:00:00+00:00"}}]}))
    state = h.read_forge()
    assert state["present"] is False
    assert state["toolchain_health"]["stale"]
    assert state["toolchain_health"]["toolchains"][0]["smoke"]["stale"]


def test_cooperative_control_requires_active_run_and_uses_offline_cli(hub, monkeypatch):
    import subprocess
    h, _, tmp = hub
    monkeypatch.setattr(h, "read_forge", lambda: {"present": True, "run_dir": str(tmp), "alive": True})
    calls = []
    monkeypatch.setattr(hubmod.subprocess, "run", lambda args, **kwargs: calls.append(args) or subprocess.CompletedProcess(args, 0, "{}", ""))
    assert h.forge_command("pause")[0] == 200
    assert calls[0][-3:] == ["control", str(tmp), "pause"]
    monkeypatch.setattr(h, "read_forge", lambda: {"present": True, "run_dir": str(tmp), "alive": False})
    assert h.forge_command("cancel")[0] == 409
