"""1 November: the rose-city run on the new roles - Strata thinking through
every "direct" attempt, triage audits that never finished, a chunk's parse
error blamed on the test, a game that passed every chunk test but did not
start, keyed sprites that were still magenta - and lessons that reach the
coders and the tests not yet run."""
import json
import sys
from pathlib import Path

import pytest

import forge
from conftest import FakeLLM, make_run

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import forge_game as game  # noqa: E402


# ------------------------------------------------------------------ Strata

def test_strata_gets_reasoning_effort_none_at_the_top_level():
    llm = forge.LLM("coder", {"url": "http://x", "kind": "qwen38", "no_think_effort": "none",
                              "reasoning_top_level": True})
    assert llm.effort(False) == "none" and llm.effort(True) == "medium" and llm.effort("high") == "high"
    sent = {}

    def post(path, body, **kw):
        if path == "/tokenize":
            return {"tokens": [0] * 10}
        sent.update(body)
        return {"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]}
    llm._post_retrying = post
    llm.chat("s", "u", thinking=False)
    assert sent["reasoning_effort"] == "none"
    assert sent["chat_template_kwargs"] == {"reasoning_effort": "none"}
    # Gemini's llama-server: as before
    g = forge.LLM("planner", {"url": "http://x", "kind": "qwen38"})
    assert g.effort(False) == "low" and not g.effort_top_level


# ------------------------------------------------- the cause, not the effect

CHUNK_PARSE = """SCRIPT ERROR: Parse Error: Cannot pass a value of type "Orientation" as "HorizontalAlignment".
          at: GDScript::reload (res://scripts/inventory_bar.gd:151)
          GDScript backtrace (most recent call first):
              [0] _forge_run (res://__forge_eval.gd:13)
ERROR: Failed to load script "res://scripts/inventory_bar.gd" with error "Parse error".
SCRIPT ERROR: Invalid call. Nonexistent function 'new' in base 'GDScript'.
          at: _forge_run (res://__forge_eval.gd:13)
"""


def test_a_chunks_parse_error_is_not_blamed_on_the_test():
    assert not game.eval_script_fault(CHUNK_PARSE)
    assert "THE TEST'S OWN" not in game.eval_failure_text(CHUNK_PARSE, game.script_errors(CHUNK_PARSE, True))
    assert not forge.harness_fault(CHUNK_PARSE)
    own = 'SCRIPT ERROR: Parse Error: Expected "(" after "assert".\n   at: GDScript::reload (res://__forge_eval.gd:9)\n'
    assert game.eval_script_fault(own) and forge.harness_fault(own)


def test_fail_signature_names_the_parse_error_not_new():
    assert "Orientation" in forge.fail_signature(CHUNK_PARSE)


def test_more_eval_shims_and_no_inferred_types():
    stmts, funcs, notes = game.fix_eval_code("var box := load('res://a.tscn').instantiate()\nadd_child(box)\n"
                                             "result = has_node('Box')\n")
    assert stmts.startswith("var box = load")
    for f in ("func add_child(", "func get_node(", "func has_node("):
        assert f in game._EVAL_SHIMS


# --------------------------------------------------------- image entries

def test_old_verified_chunks_lose_their_image_entries(tmp_path):
    code = forge.files_to_python({"data/world.json": "{}", "assets/rooms/a.png": "placeholder"})
    run = make_run(tmp_path, verified={"c2": code})
    v = run.verified()
    assert forge.python_to_files(v["c2"]) == {"data/world.json": "{}"}
    assert "a.png" not in (run.root / "verified" / "c2.py").read_text()


# ------------------------------------------------------------------ triage

def test_a_triage_audit_that_fails_goes_to_the_planner(tmp_path, monkeypatch):
    calls = []

    def fake_audit(run, llm, chunk, code, output, allow_upstream=True, efforts=("high", "medium")):
        calls.append((llm, efforts))
        if llm == "TRIAGE":
            raise forge.ForgeError("triage: prompt is 32633 tokens, budget 32512")
        return None
    monkeypatch.setattr(forge, "audit_test", fake_audit)
    t = forge.Triage(forge.LLM("triage", {"url": "http://x"}), None, {"audit", "diagnose"})
    t.llm = "TRIAGE"
    monkeypatch.setattr(t, "up", lambda: True)
    monkeypatch.setattr(forge, "TRIAGE", t)
    # the audit code path itself, without a whole build
    st, chunk = {}, {"id": "c1"}
    new_test, by_triage = None, False
    with forge.triage_slot("audit") as tl:
        assert tl == "TRIAGE"
    # what audit_and_apply does, in short:
    try:
        forge.audit_test(None, "TRIAGE", chunk, "", "", True, efforts=(False,))
    except forge.ForgeError:
        pass
    forge.audit_test(None, "PLANNER", chunk, "", "", True)
    assert calls == [("TRIAGE", (False,)), ("PLANNER", ("high", "medium"))]


# ------------------------------------------------------------------- boot

class BootSandbox:
    def __init__(self, report):
        self.report = report
        self.ran = []

    def run(self, files, entry, *a, **kw):
        self.ran.append(entry)
        return True, "BOOT_REPORT:" + json.dumps(self.report)


def test_boot_errors_go_to_the_chunk_that_wrote_the_file(tmp_path, monkeypatch):
    monkeypatch.setattr(forge, "TOOLCHAIN", "godot")
    plan = {"chunks": [{"id": "c10"}, {"id": "c16"}],
            "contract": {"files": [{"path": "scripts/room_view.gd", "owner": "c10"}]}}
    run = make_run(tmp_path, plan=plan,
                   verified={"c16": forge.files_to_python({"main.tscn": "[gd_scene]"})})
    sb = BootSandbox([{"file": "scripts/room_view.gd", "line": 297,
                       "error": 'Parse Error: Invalid argument for "_is_npc_hotspot()"'},
                      {"file": "main.tscn", "line": None, "error": "Failed loading resource"},
                      {"file": None, "line": None, "error": "something global"}])
    issues = forge.boot_check(run, sb, "module")
    assert [i["chunk"] for i in issues] == ["c10", "c16", None]
    assert "room_view.gd:297" in issues[0]["description"]
    monkeypatch.setattr(forge, "TOOLCHAIN", "python")
    assert forge.boot_check(run, sb, "module") is None


def test_godot_boot_report_reads_errors_and_places(tmp_path, monkeypatch):
    out = ("SCRIPT ERROR: Parse Error: Invalid argument for \"_is_npc_hotspot()\" function.\n"
           "          at: GDScript::reload (res://scripts/room_view.gd:297)\n"
           "ERROR: Not a PNG file\n"
           "SCRIPT ERROR: Parse Error: Invalid argument for \"_is_npc_hotspot()\" function.\n"
           "          at: GDScript::reload (res://scripts/room_view.gd:297)\n"
           "SCRIPT ERROR: Invalid call. Nonexistent function 'show_dialogue' in base 'Control'.\n"
           "          at: _on_talk (res://scripts/room_view.gd:88)\n")
    proj = tmp_path / "p"
    proj.mkdir()
    (proj / "project.godot").write_text('[application]\nrun/main_scene="res://main.tscn"\n')
    monkeypatch.setattr(game, "project_dir", lambda fresh=False: proj)
    monkeypatch.setattr(game, "godot_import", lambda timeout=180: game.Result(0, out))
    monkeypatch.setattr(game, "godot", lambda *a, **k: game.Result(0, ""))
    rep = game.godot_boot_report()
    assert rep == [{"file": "scripts/room_view.gd", "line": 297,
                    "error": 'Parse Error: Invalid argument for "_is_npc_hotspot()" function.'},
                   {"file": "scripts/room_view.gd", "line": 88,
                    "error": "Invalid call. Nonexistent function 'show_dialogue' in base 'Control'."}]


# ------------------------------------------------------------------ images

def sprite(bg, box):
    w = h = 64
    px = bytearray()
    for y in range(h):
        for x in range(w):
            px += bytes(box if 20 <= x < 44 and 20 <= y < 44 else bg) + b"\xff"
    return w, h, px


def test_chroma_key_follows_the_background_the_model_painted():
    for bg in ((200, 40, 190), (255, 0, 255), (235, 20, 215)):
        w, h, px = sprite(bg, (255, 105, 180))           # a PINK doughnut box
        out = forge.img_chroma_key(w, h, px, "#ff00ff", 60)
        opaque = sum(1 for i in range(3, len(out), 4) if out[i])
        assert opaque == 24 * 24, bg
        assert forge.art_problem({"chroma_key": "#ff00ff"}, forge.png_encode(w, h, 4, bytes(out))) is None


def test_unusable_art_is_rendered_again(tmp_path, monkeypatch):
    run = make_run(tmp_path)
    seeds = []

    def fake_render(run, cfg, chunk, srv, name, reroll):
        seeds.append(reroll)
        if reroll < 2 and not chunk.get("_art_accept"):
            raise forge.ArtRejected(f"[{chunk['id']}] nearly black (mean luminance 4 of 255)")
        return 1.0
    monkeypatch.setattr(forge, "_run_image_chunk", fake_render)
    cfg = {"images": {"enabled": True}}
    assert forge.run_image_chunk(run, cfg, {"id": "bg"}, srv={"name": "apollo", "url": "http://x"}) == 1.0
    assert seeds == [0, 1, 2]
    seeds.clear()
    cfg["images"]["art_rerolls"] = 1
    assert forge.run_image_chunk(run, cfg, {"id": "bg"}, srv={"name": "apollo", "url": "http://x"}) == 1.0
    assert seeds == [0, 1, 1]                     # the last one kept, for the review to judge


def test_art_problems():
    dark = forge.png_encode(2, 2, 4, bytes([5, 5, 5, 255] * 4))
    assert "nearly black" in forge.art_problem({}, dark)
    assert forge.art_problem({"min_luminance": 3}, dark) is None
    empty = forge.png_encode(2, 2, 4, bytes([0, 0, 0, 0] * 4))
    assert "almost nothing" in forge.art_problem({"chroma_key": "#ff00ff"}, empty)


# ----------------------------------------------------------------- lessons

def test_code_lessons_reach_the_coders(tmp_path, monkeypatch):
    monkeypatch.setattr(forge, "TOOLCHAIN", "godot")
    run = make_run(tmp_path)
    monkeypatch.setattr(forge, "LESSON_RUN", run)
    forge.add_lesson(run, "Declare every signal a script emits.", "c5", "code", committed=True)
    forge.add_lesson(run, "godot_eval returns the value itself.", "c13", "tests", committed=True)
    assert "Declare every signal" in forge.coder_system()
    assert "returns the value itself" not in forge.coder_system()
    assert "returns the value itself" in forge.chunk_test_system()      # mid-run test writers too


def test_lesson_check_updates_a_test_not_yet_run(tmp_path, monkeypatch):
    monkeypatch.setattr(forge, "TOOLCHAIN", "godot")
    old = "r = game.godot_eval('result = 1')\nassert r.ok\nassert r.out\n"
    new = "r = game.godot_eval('result = 1')\nassert r == 1\nassert r is not None"
    plan = {"chunks": [{"id": "c13", "verify": old}]}
    run = make_run(tmp_path, plan=plan)
    forge.add_lesson(run, "godot_eval returns the value itself; it has no .ok or .out.", "c3", committed=True)
    chunk = run.plan["chunks"][0]
    planner = FakeLLM({"rules learned": [json.dumps({"changed": True, "rules": [1], "test": new})]})
    forge.lesson_check(run, planner, chunk)
    assert chunk["verify"] == new and run.plan["chunks"][0]["verify"] == new
    forge.lesson_check(run, planner, chunk)        # nothing new: no second call
    assert planner.count("rules learned") == 1
    # a "fix" that drops checks is refused
    forge.add_lesson(run, "Another rule.", "c4", committed=True)
    planner2 = FakeLLM({"rules learned": [json.dumps({"changed": True, "test": "assert True\n"})]})
    forge.lesson_check(run, planner2, chunk)
    assert chunk["verify"] == new


def test_forge_lessons_list_and_drop(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(forge, "TOOLCHAIN", "godot")
    for i, name in enumerate(("a", "b")):
        r = make_run(tmp_path / name)
        (tmp_path / name / "run").rename(tmp_path / name / f"run-{name}")
        forge.add_lesson(forge.Run(tmp_path / name / f"run-{name}"), "Use f.call(x).", "c1", committed=True)
    assert forge.cmd_lessons("list", None, None) == 0
    assert "2 run(s), in use" in capsys.readouterr().out
    assert "Use f.call(x)." in forge.run_lessons_text(None)
    assert forge.cmd_lessons("drop", "1", "godot") == 0
    assert forge.run_lessons_text(None) == ""


# ------------------------------------------------- 2 Nov: pdx-slice-a-props

def test_a_lesson_counts_only_once_its_chunk_passes(tmp_path, monkeypatch):
    monkeypatch.setattr(forge, "TOOLCHAIN", "blender")
    run = make_run(tmp_path)
    forge.add_lesson(run, "import_scene.gltf needs the file browser", "c3", "code")
    assert not run.state.get("lessons") and run.state["lessons_pending"]["c3"]
    assert forge.load_lessons() == {}
    run.state["chunks"]["c3"] = {"status": "verified"}
    forge.commit_lessons(run, "c3")
    assert [l["text"] for l in run.state["lessons"]] == ["import_scene.gltf needs the file browser"]
    assert "c3" not in run.state["lessons_pending"]


def test_a_task_naming_the_model_folder_waits_for_every_model():
    plan = {"chunks": [
        {"id": "bubbler", "kind": "image", "filename": "refs/bubbler.png",
         "shape": {"filename": "models/bubbler.glb"}},
        {"id": "stall", "kind": "image", "filename": "refs/stall.png",
         "shape": {"filename": "models/stall.glb"}},
        {"id": "c3", "spec": "open assets/models/<id>.glb with bpy.ops.import_scene.gltf"},
        {"id": "c2", "spec": "game_ready(obj, ...) on any mesh"}]}
    by = {c["id"]: c for c in plan["chunks"]}
    assert forge.chunk_images(plan, by["c3"]) == ["bubbler", "stall"]
    assert forge.chunk_images(plan, by["c2"]) == []


def test_triage_counts_as_a_gpu_tenant(monkeypatch):
    t = forge.Triage(forge.LLM("triage", {"url": "http://mercury:8080"}), "mercury", {"audit"})
    monkeypatch.setattr(forge, "TRIAGE", t)
    assert forge.shares_gpu("mercury") and not forge.shares_gpu("apollo")
    assert [x.image_server for x in forge.gpu_tenants()] == ["mercury"]


def test_a_second_server_waits_for_the_queue():
    pool = forge.ImagePool.__new__(forge.ImagePool)
    pool.queue = [{"id": "a"}]
    pool.busy, pool.tried = {}, {"a": set()}
    pool.servers = [{"name": "apollo"}, {"name": "mercury"}]
    pool.alive = {"apollo", "mercury"}
    # Mercury is the better choice now; Apollo must still be eligible to stay
    assert pool._eligible(pool.queue[0], {"name": "apollo"})


def test_the_shape_input_loses_its_backdrop_and_shadow():
    w = h = 64
    px = bytearray()
    for y in range(h):
        for x in range(w):
            if 24 <= x < 40 and 10 <= y < 50:
                c = (40, 60, 90)                      # the object
            elif 50 <= y < 58 and 16 <= x < 48:
                v = 248 - 6 * min(y - 49, 58 - y)     # a soft floor shadow, darkest in its middle
                c = (v, v, v)
            else:
                c = (255 - y // 8, 255 - y // 8, 255 - y // 8)   # a soft gradient backdrop
            px += bytes(c)
    raw = forge.png_encode(w, h, 3, bytes(px))
    img, keyed = forge.shape_input({"shape": {"filename": "m.glb"}}, raw)
    assert keyed
    w2, h2, ch, out = forge.png_decode(img)
    out = forge.to_rgba(w2, h2, ch, out)
    opaque = sum(1 for i in range(3, len(out), 4) if out[i])
    assert opaque == 16 * 40                          # the object only: no backdrop, no shadow
    assert forge.shape_input({"shape": {"filename": "m.glb", "key_background": False}}, raw) == (raw, False)


def test_a_failed_model_is_not_a_failed_image_server(tmp_path, monkeypatch):
    run = make_run(tmp_path)
    monkeypatch.setattr(forge, "pick_server", lambda icfg, chunk: {"name": "apollo"})
    monkeypatch.setattr(forge, "image_servers", lambda icfg: [{"name": "apollo"}, {"name": "mercury"}])
    monkeypatch.setattr(forge, "server_up", lambda s, timeout=5: True)
    renders = []
    monkeypatch.setattr(forge, "run_image_chunk", lambda run, cfg, c, reroll=0, srv=None: renders.append(srv["name"]))

    def no_vram(run, cfg, c):
        raise forge.ForgeError("vulcan HTTP 500: only 8.4 GB of VRAM free")
    monkeypatch.setattr(forge, "shape_now", no_vram)
    with pytest.raises(forge.ForgeError, match="rendered on apollo, but its 3D model failed"):
        forge.render_failover(run, {"images": {}}, {"id": "bubbler", "shape": {"filename": "m.glb"}}, 1)
    assert renders == ["apollo"]


# ---------------------------------------------- 3 Nov: Blender packages

def test_blender_tests_and_blender_eval_see_the_project_packages(tmp_path, monkeypatch):
    monkeypatch.setattr(forge, "TOOLCHAIN", "blender")
    head = forge.IMPORT_ALL.format("module")
    assert "path.insert(0, str(game.project_dir()))" in head
    monkeypatch.setattr(forge, "TOOLCHAIN", "godot")
    assert "game.project_dir()" not in forge.IMPORT_ALL.format("module")
    seen = []
    monkeypatch.setattr(game, "project_dir", lambda fresh=False: tmp_path / "proj")
    monkeypatch.setattr(game, "blender", lambda script, *a, **k: seen.append(script) or game.Result(0, "FORGE_RESULT:1"))
    assert game.blender_eval("import pdxprops\nresult = 1") == 1
    assert f"_fs.path.insert(0, {str(tmp_path / 'proj')!r})" in seen[0]


def test_the_principal_coder_waits_until_strata_is_back(monkeypatch):
    import types as _t
    monkeypatch.setattr(forge, "CURRENT_CFG", {"coder": {"machine": "vulcan", "url": "http://vulcan:8081"}})
    monkeypatch.setattr(forge, "CURRENT_POOL", _t.SimpleNamespace(shape_holding=set()))
    monkeypatch.setattr(forge, "_PRINCIPAL_DOWN", {"vulcan": __import__("time").monotonic()})
    up = {"ok": False}
    monkeypatch.setattr(forge, "principal_answers", lambda: up["ok"])
    assert not forge.principal_free()           # released, but Strata still starting
    up["ok"] = True
    assert forge.principal_free() and "vulcan" not in forge._PRINCIPAL_DOWN


def test_strata_is_stopped_only_after_its_attempt_finishes(monkeypatch):
    import threading as _th
    import time as _time
    monkeypatch.setattr(forge, "CURRENT_CFG", {"coder": {"machine": "vulcan"}})
    monkeypatch.setattr(forge, "_SHAPE_FREED", set())
    monkeypatch.setattr(forge, "_PRINCIPAL_DOWN", {})
    order = []
    monkeypatch.setattr(forge, "_shape_ssh", lambda srv, cmd, what: order.append("stop") or True)
    assert forge.claim_writer("coder")

    def finish():
        _time.sleep(0.3)
        order.append("attempt done")
        forge.release_writer("coder")
    _th.Thread(target=finish).start()
    forge.shape_free({"name": "vulcan", "machine": "vulcan", "free_cmd": "systemctl --user stop strata",
                      "free_wait": 0})
    assert order == ["attempt done", "stop"] and "vulcan" in forge._PRINCIPAL_DOWN


# ------------------------------------- 3 Nov: props committed without a build

def test_alpha_noise_is_not_transparency():
    w = h = 32
    px = bytearray()
    for y in range(h):
        for x in range(w):
            obj = 10 <= x < 22 and 8 <= y < 26
            px += bytes((60, 50, 40) if obj else (254, 255, 250)) + bytes([254 if (x + y) % 7 == 0 else 255])
    raw = forge.png_encode(w, h, 4, bytes(px))
    img, keyed = forge.shape_input({"shape": {"filename": "m.glb"}}, raw)
    assert keyed
    _, _, ch, out = forge.png_decode(img)
    out = forge.to_rgba(w, h, ch, out)
    assert sum(1 for i in range(3, len(out), 4) if out[i]) == 12 * 18


def test_a_failed_build_is_a_blocking_finding_for_the_build_owner(tmp_path, monkeypatch):
    monkeypatch.setattr(forge, "TOOLCHAIN", "blender")
    plan = {"toolchain": "blender", "chunks": [{"id": "c3"}],
            "contract": {"files": [{"path": "build.py", "owner": "c3"}]}}
    run = make_run(tmp_path, plan=plan)
    monkeypatch.setattr(forge, "OUTPUT_PROBLEMS", ["building the blender package failed: TIMEOUT after 900s"])
    issues = forge.output_issues(run)
    assert issues[0]["chunk"] == "c3" and issues[0]["severity"] == "blocking"
    assert "TIMEOUT after 900s" in issues[0]["description"]
    monkeypatch.setattr(forge, "OUTPUT_PROBLEMS", [])
    assert forge.output_issues(run) == []


def test_papyrus_and_blender_coders_get_their_rules(monkeypatch):
    monkeypatch.setattr(forge, "TOOLCHAIN", "fo4")
    assert 'SendCustomEvent("ControllerChanged", kArgs)' in forge.coder_system()
    assert "as Int" in forge.coder_system()
    monkeypatch.setattr(forge, "TOOLCHAIN", "blender")
    assert "must stop when an iteration changes" in forge.coder_system()
