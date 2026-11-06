"""30 October: rose-city-signal's third run (the VRAM left by an earlier
run, placeholder images over real renders, FILE labels in fences, the
tests' own GDScript slips), and the new roles: Strata on Vulcan as the
principal coder, Apollo rendering, Mercury triaging, run lessons."""
import importlib.util
import json
import os
import sys
import threading
import types
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

import forge
from conftest import make_run

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
import forge_game as game  # noqa: E402


# ------------------------------------------------------------ FILE parsing

def test_file_label_inside_the_fence_with_a_nested_language_fence():
    reply = ("```FILE: ui/a.gd\n```gdscript\nextends Node\n```\n```\n\n"
             "```FILE: ui/a.tscn\n[gd_scene format=3]\n```\n```\n\n"
             "```FILE: ui/b.gd\n```gdscript\nextends Control\n```\n```\n")
    files = forge.parse_files_reply(reply)
    assert files == {"ui/a.gd": "extends Node\n", "ui/a.tscn": "[gd_scene format=3]\n",
                     "ui/b.gd": "extends Control\n"}


def test_a_fence_holding_only_the_label():
    reply = "```\nFILE: ui/a.gd\n```\n\n```gdscript\nextends Node\n```\n"
    assert forge.parse_files_reply(reply) == {"ui/a.gd": "extends Node\n"}


def test_file_lines_with_no_fences_at_all():
    reply = "FILE: ui/a.tscn\n[gd_scene format=3]\n[node name=\"A\"]\n\nFILE: ui/a.gd\nextends Node\n"
    assert forge.parse_files_reply(reply) == {"ui/a.tscn": "[gd_scene format=3]\n[node name=\"A\"]\n",
                                              "ui/a.gd": "extends Node\n"}


def test_the_usual_forms_still_parse():
    assert forge.parse_files_reply("FILE: a.gd\n```gdscript\nx\n```\n") == {"a.gd": "x\n"}
    assert forge.parse_files_reply("```gdscript file=b.gd\ny\n```\n") == {"b.gd": "y\n"}
    assert forge.parse_files_reply("```gdscript\nz\n```\n", "c.gd") == {"c.gd": "z\n"}


# ------------------------------------------------- images never come from code

def test_enforce_files_drops_images_a_coder_wrote():
    plan = {"contract": {"files": [{"path": "data/world.json", "owner": "c2"},
                                   {"path": "assets/rooms/a.png", "owner": "c2"}]}}
    code = forge.files_to_python({"data/world.json": "{}", "assets/rooms/a.png": "placeholder"})
    out, taken, problems = forge.enforce_files(code, "c2", plan)
    assert forge.python_to_files(out) == {"data/world.json": "{}"}
    assert any("assets/rooms/a.png" in t and "binary" in t for t in taken)
    assert problems == []                     # an image is never "missing" from a reply


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setattr(game, "TMP", tmp_path / "tmp")
    (tmp_path / "tmp").mkdir()
    monkeypatch.setattr(game, "_project", None)
    mod = types.ModuleType("module")
    mod.FILES = {"project.godot": "config_version=5\n"}
    monkeypatch.setitem(sys.modules, "module", mod)
    return mod


def test_project_dir_keeps_the_render_over_a_placeholder(project, tmp_path, monkeypatch):
    assets = tmp_path / "assets-src"
    (assets / "rooms").mkdir(parents=True)
    png = b"\x89PNG\r\n\x1a\nrealimage"
    (assets / "rooms" / "a.png").write_bytes(png)
    orig = Path

    class P(type(Path())):
        pass
    monkeypatch.setattr(game, "Path", lambda p="": orig(str(assets)) if p == "/assets" else orig(p))
    project.FILES = {"project.godot": "config_version=5\n", "assets/rooms/a.png": "placeholder"}
    d = game.project_dir()
    assert (d / "assets" / "rooms" / "a.png").read_bytes() == png


def test_files_contains_a_rendered_asset(project, tmp_path, monkeypatch):
    assets = tmp_path / "assets-src"
    assets.mkdir()
    (assets / "map.png").write_bytes(b"\x89PNG")
    orig = Path
    monkeypatch.setattr(game, "Path", lambda p="": orig(str(assets)) if p == "/assets" else orig(p))
    files = game.files()
    assert "res://assets/map.png" in files and "assets/map.png" in files
    assert "assets/nope.png" not in files
    assert "project.godot" in files


# ------------------------------------------------------------- GDScript slips

def test_fix_eval_code_mends_the_tests_slips():
    code = ('var result := 0\nvar skey = func(a, b): return a < b\n'
            'assert skey(1, 2), "ordered"\nfunc helper(x):\n    return x * 2\nresult = helper(3)\n')
    stmts, funcs, notes = game.fix_eval_code(code)
    assert "var result" not in stmts and "result = 0" in stmts
    assert 'assert(skey.call(1, 2), "ordered")' in stmts
    assert "func helper" not in stmts and funcs.startswith("func helper(x):\n\treturn")
    assert len(notes) == 4


def test_script_errors_ignore_files_a_later_chunk_writes(project):
    project.FILES = {"project.godot": '[autoload]\nGame="*res://scripts/game.gd"\n'
                                      '[application]\nrun/main_scene="res://main.tscn"\n',
                     "scripts/adventure.gd": "extends Node\n"}
    game.project_dir()
    out = ("ERROR: Attempt to open script 'res://scripts/game.gd' resulted in error 'File not found'.\n"
           "ERROR: Failed to instantiate an autoload, can't load from path: res://scripts/game.gd.\n"
           "SCRIPT ERROR: Parse Error: Identifier \"x\" not declared in the current scope.\n"
           "   at: GDScript::reload (res://scripts/adventure.gd:3)\n")
    errs = game.script_errors(out)
    assert len(errs) == 1 and "not declared" in errs[0]
    assert len(game.script_errors(out, strict=True)) == 3


def test_image_helpers_take_a_path(tmp_path):
    png = tmp_path / "f.png"
    import struct
    import zlib
    raw = zlib.compress(b"".join(b"\x00" + b"\x10\x20\x30" * 4 for _ in range(4)))
    ch = lambda t, d: struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d))
    png.write_bytes(b"\x89PNG\r\n\x1a\n" + ch(b"IHDR", struct.pack(">IIBBBBB", 4, 4, 8, 2, 0, 0, 0))
                    + ch(b"IDAT", raw) + ch(b"IEND", b""))
    assert game.is_blank(png) and game.is_blank(str(png))
    assert game.diff(png, png) == 0.0
    assert game.count_color(png, (16, 32, 48)) == 16


FAKE = r'''
import json, os, sys
from pathlib import Path
args = sys.argv[1:]
if "--import" in args:
    sys.exit(0)
proj = Path(args[args.index("--path") + 1])
src = (proj / "__forge_eval.gd").read_text()
Path(os.environ["FAKE_LOG"]).open("a").write(src + "\n=====\n")
if "process_frame.connect" in src and os.environ.get("FAKE_NO_FRAME"):
    sys.exit(0)                 # an engine that never ran the first-frame hook
if "process_frame.connect" in src:
    print("FORGE_EVAL_STARTED")
print("FORGE_RESULT:" + json.dumps({"ok": 1}))
'''


@pytest.fixture
def fake_godot(project, tmp_path, monkeypatch):
    b = tmp_path / "bin"
    b.mkdir()
    (b / "godot").write_text(f"#!{sys.executable}\n" + FAKE)
    (b / "godot").chmod(0o755)
    monkeypatch.setenv("PATH", f"{b}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setattr(game, "GODOT", "godot")
    monkeypatch.setenv("FAKE_LOG", str(tmp_path / "evals.txt"))
    return tmp_path / "evals.txt"


def test_godot_eval_runs_at_the_first_frame_with_shims(fake_godot):
    assert game.godot_eval("result = 1") == {"ok": 1}
    src = fake_godot.read_text()
    assert "process_frame.connect(_forge_run, CONNECT_ONE_SHOT)" in src
    assert "func get_tree() -> SceneTree:" in src


def test_godot_eval_falls_back_to_initialize(fake_godot, monkeypatch, capsys):
    monkeypatch.setenv("FAKE_NO_FRAME", "1")
    assert game.godot_eval("result = 1") == {"ok": 1}
    runs = fake_godot.read_text().split("=====")
    assert "process_frame.connect" in runs[0] and "func _initialize():\n\tvar result" in runs[1]
    assert "first-frame run did not start" in capsys.readouterr().err


def test_harness_fault_is_audited_at_once():
    out = ("AssertionError: godot_eval produced no result.\nTHE TEST'S OWN GDSCRIPT ...\n")
    assert forge.harness_fault(out)
    assert forge.harness_fault('SCRIPT ERROR: Parse Error: Function "skey()" not found in base self.\n'
                               "          at: GDScript::reload (res://__forge_eval.gd:29)\n")
    assert not forge.harness_fault("AssertionError: A3: wrong inventory")


def test_godot_rules_reach_coders_and_test_writers(monkeypatch):
    monkeypatch.setattr(forge, "TOOLCHAIN", "godot")
    assert "f.call(2)" in forge.test_addendum() and "root.add_child" in forge.test_addendum()
    assert "Declare every signal" in forge.coder_system()


# ------------------------------------------------------------ VRAM on resume

class Router:
    """A llama-server router with some models loaded."""

    def __init__(self, loaded):
        self.loaded = set(loaded)
        self.unloads = []
        r = self

        class H(BaseHTTPRequestHandler):
            def _send(self, obj):
                body = json.dumps(obj).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                self._send({"data": [{"id": m, "status": {"value": "loaded" if m in r.loaded else "unloaded"}}
                                     for m in ("tiel-coder", "qwen3.8-27b")]})

            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                mid = json.loads(self.rfile.read(n))["model"]
                r.unloads.append(mid)
                r.loaded.discard(mid)
                self._send({"success": True})

            def log_message(self, *a):
                pass
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}"


def test_release_gpu_unloads_a_model_an_earlier_run_left(monkeypatch):
    r = Router(["qwen3.8-27b"])          # the last run switched Mercury to it
    llm = forge.LLM("coder@mercury", {"url": r.url, "model": "tiel-coder"})
    monkeypatch.setattr(forge, "HELPERS", [forge.Helper("mercury", llm, "mercury", False)])
    assert forge.release_gpu("mercury") is True
    assert r.unloads == ["qwen3.8-27b"] and not r.loaded
    assert forge.release_gpu("mercury") is False      # nothing left to free
    r.srv.shutdown()


def test_release_image_machines_frees_every_render_machine(monkeypatch):
    a, m = Router(["tiel-coder"]), Router(["tiel-coder"])
    monkeypatch.setattr(forge, "HELPERS", [
        forge.Helper("apollo", forge.LLM("a", {"url": a.url, "model": "tiel-coder"}), "apollo", True),
        forge.Helper("mercury", forge.LLM("m", {"url": m.url, "model": "tiel-coder"}), "mercury", False)])
    forge.release_image_machines("before this run's renders")
    assert a.unloads == ["tiel-coder"] and m.unloads == ["tiel-coder"]


# ------------------------------------------------------------ the new roles

def test_tokens_are_estimated_without_a_tokenize_endpoint(monkeypatch):
    llm = forge.LLM("coder", {"url": "http://127.0.0.1:9", "model": "strata"})

    def no_tokenize(path, payload, timeout=None, **kw):
        raise forge.ForgeError("coder: HTTP 404 on /tokenize: not found")
    monkeypatch.setattr(llm, "_post_retrying", no_tokenize)
    n = llm.tokens("x" * 300)
    assert n == forge.estimate_tokens("x" * 300) and llm.no_tokenize
    assert forge.LLM("c", {"url": "http://x", "tokenize": False}).no_tokenize


def test_a_pin_to_a_server_that_no_longer_renders_is_dropped():
    chunks = [{"id": "hero", "server": "vulcan"}, {"id": "b", "server": "mercury"}]
    forge.unpin_retired([{"name": "apollo"}, {"name": "mercury"}], chunks)
    assert "server" not in chunks[0] and chunks[1]["server"] == "mercury"


def test_strata_is_not_switched_between_models(monkeypatch):
    cfg = {"coder": {"machine": "vulcan", "switch_models": False},
           "coders": [{"name": "apollo"}], "models": {"enabled": True}}
    monkeypatch.setattr(forge, "machines", lambda cfg: {"vulcan": {"ssh": "vulcan"}, "apollo": {"ssh": "apollo"}})
    coder = forge.LLM("coder", {"url": "http://x", "context": 65536})
    h = forge.Helper("apollo", forge.LLM("a", {"url": "http://y", "context": 65536}), "apollo", True)
    got = forge.register_machines(cfg, coder, [h])
    assert list(got) == ["apollo"]


def test_principal_coder_pauses_while_the_shape_model_holds_its_gpu(monkeypatch):
    pool = types.SimpleNamespace(shape_holding={"vulcan"})
    monkeypatch.setattr(forge, "CURRENT_POOL", pool)
    monkeypatch.setattr(forge, "CURRENT_CFG", {"coder": {"machine": "vulcan"}})
    assert not forge.principal_free()
    pool.shape_holding.clear()
    assert forge.principal_free()


def test_shapes_wait_for_every_shaped_render_on_the_coders_machine():
    pool = forge.ImagePool.__new__(forge.ImagePool)
    pool.cfg = {"coder": {"machine": "vulcan"}}
    pool.queue = [{"id": "chair", "shape": {"filename": "chair.glb"}}]
    pool.busy = {}
    assert pool._batch_shapes("vulcan") and not pool._batch_shapes("apollo")
    pool.queue = [{"id": "wall"}]
    assert not pool._batch_shapes("vulcan")


# ----------------------------------------------------------------- triage

def test_triage_slot_falls_back_while_its_gpu_renders(monkeypatch):
    t = forge.Triage(forge.LLM("triage", {"url": "http://x"}), "mercury", {"diagnose", "audit"})
    monkeypatch.setattr(t, "up", lambda: True)
    monkeypatch.setattr(forge, "TRIAGE", t)
    with forge.triage_slot("diagnose") as llm:
        assert llm is t.llm
    lock = forge.gpu_lock("mercury")
    lock.acquire()
    try:
        with forge.triage_slot("diagnose") as llm:
            assert llm is None                       # rendering: the planner does it
    finally:
        lock.release()
    with forge.triage_slot("review") as llm:
        assert llm is None                           # not one of its uses


def test_setup_triage_inherits_the_coder_settings():
    cfg = {"coder": {"url": "http://vulcan:8081", "max_tokens": 28000, "machine": "vulcan",
                     "switch_models": False},
           "triage": {"enabled": True, "url": "http://mercury:8080", "model": "tiel-coder",
                      "kind": "tiel", "machine": "mercury"}}
    t = forge.setup_triage(cfg)
    assert t.llm.url == "http://mercury:8080" and t.llm.max_tokens == 28000 and t.machine == "mercury"
    assert forge.setup_triage({"coder": {}, "triage": {"enabled": False, "url": "x"}}) is None


# ----------------------------------------------------------------- lessons

def test_lessons_reach_the_rest_of_the_run_and_later_runs(tmp_path, monkeypatch):
    monkeypatch.setattr(forge, "TOOLCHAIN", "godot")
    run = make_run(tmp_path)
    forge.add_lesson(run, "GDScript lambdas are called with f.call(x)", "c3", committed=True)
    forge.add_lesson(run, "gdscript lambdas are called with F.call(x)", "c4")   # the same, once
    assert len(run.state["lessons"]) == 1
    assert "f.call(x)" in forge.run_lessons_text(run)
    # a later run sees a lesson once it has come up twice
    assert forge.run_lessons_text(None) == ""
    run2 = make_run(tmp_path / "2")
    (tmp_path / "2" / "run").rename(tmp_path / "2" / "run-b")      # another run
    run2 = forge.Run(tmp_path / "2" / "run-b")
    forge.add_lesson(run2, "GDScript lambdas are called with f.call(x)", "c9", committed=True)
    assert "f.call(x)" in forge.run_lessons_text(None)


# ------------------------------------------------------------- apply-roles

def load_apply_roles():
    spec = importlib.util.spec_from_file_location("apply_roles", HERE / "apply-roles.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_apply_roles_replaces_only_the_machine_sections():
    import tomllib
    ar = load_apply_roles()
    old = ('[planner]\nurl = "http://127.0.0.1:8080"\n\n# the coder\n[coder]\nurl = "http://apollo:8080"\n\n'
           '[[coders]]\nname = "vulcan"\n\n[images]\nenabled = true\ntimeout = 1200\n\n'
           '[[images.servers]]\nname = "vulcan"\n\n[loop]\nmax_attempts = 6\n')
    new = ar.apply(old, (HERE / "roles.toml").read_text())
    c = tomllib.loads(new)
    assert c["planner"]["url"] == "http://127.0.0.1:8080" and c["loop"]["max_attempts"] == 6
    assert c["images"]["timeout"] == 1200
    assert c["coder"]["url"].endswith(":8081") and c["coder"]["machine"] == "vulcan"
    assert [s["name"] for s in c["images"]["servers"]] == ["apollo", "mercury"]
    assert c["triage"]["machine"] == "mercury"
    assert ar.apply(new, (HERE / "roles.toml").read_text()).count("[coder]") == 1
