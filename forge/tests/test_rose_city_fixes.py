"""Fixes from the two rose-city-signal runs (Godot, 2026-10-28 and -29):
GDScript indented with spaces inside godot_eval, Godot left running after a
script failed to load, a reply naming res://data/world.json stopping the
whole build, a single unlabelled code block lost, and failure headlines that
named a C++ source line instead of the error."""
import json
import os
import sys
import types
from pathlib import Path

import pytest

import forge

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import forge_game as game  # noqa: E402


# --------------------------------------------------------------- godot_eval

def test_gd_indent_never_mixes_tabs_and_spaces():
    code = '''
        var a = Adventure.new()
        a.new_game()
        if a.inventory().size() > 0:
            result = {"inv": a.inventory()}
        else:
            result = []
    '''
    out = game.gd_indent(code)
    lines = [ln for ln in out.splitlines() if ln.strip()]
    assert lines[0] == "\tvar a = Adventure.new()"
    assert lines[3] == "\t\tresult = {\"inv\": a.inventory()}"
    assert all(ln[:len(ln) - len(ln.lstrip())].strip("\t") == "" for ln in lines)


def test_gd_indent_keeps_tab_indented_code():
    assert game.gd_indent("var x = 1\nif x:\n\tresult = x") == "\tvar x = 1\n\tif x:\n\t\tresult = x"


FAKE_GODOT = r'''
import json, os, sys
from pathlib import Path
args = sys.argv[1:]
Path(os.environ["FAKE_LOG"]).write_text(json.dumps(args))
if "--import" in args:
    sys.exit(0)
proj = Path(args[args.index("--path") + 1])
over = proj / "override.cfg"
Path(os.environ["FAKE_LOG"] + ".override").write_text(over.read_text() if over.exists() else "")
src = (proj / "__forge_eval.gd").read_text()
for ln in src.splitlines():
    lead = ln[:len(ln) - len(ln.lstrip())]
    if "\t" in lead and " " in lead:
        print("SCRIPT ERROR: Parse Error: Mixed use of tabs and spaces for indentation.", file=sys.stderr)
        print("   at: load (modules/gdscript/gdscript_resource_format.cpp:46)", file=sys.stderr)
        sys.exit(0)
print("SCRIPT ERROR: Trying to assign an array of type \"Array\" to a variable of type \"Array[String]\".",
      file=sys.stderr)
print("FORGE_RESULT:" + json.dumps({"inv": []}))
'''


@pytest.fixture
def godot_project(tmp_path, monkeypatch):
    monkeypatch.setattr(game, "TMP", tmp_path / "tmp")
    (tmp_path / "tmp").mkdir()
    monkeypatch.setattr(game, "_project", None)
    mod = types.ModuleType("module")
    mod.FILES = {"project.godot": "config_version=5\n"}
    monkeypatch.setitem(sys.modules, "module", mod)
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "godot").write_text(f"#!{sys.executable}\n" + FAKE_GODOT)
    (bindir / "godot").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setattr(game, "GODOT", "godot")
    monkeypatch.setenv("FAKE_LOG", str(tmp_path / "godot.json"))
    return tmp_path


def test_godot_eval_with_space_indented_code_reports_script_errors(godot_project, capsys):
    v = game.godot_eval("""
        var a = Adventure.new()
        result = {"inv": a.inventory()}
    """)
    assert v == {"inv": []}
    err = capsys.readouterr().err
    assert "Godot reported script errors" in err and "Array[String]" in err
    args = json.loads((godot_project / "godot.json").read_text())
    assert args[args.index("--quit-after") + 1] == str(game.GODOT_EVAL_ITERATIONS)


def test_a_timeout_keeps_what_the_program_printed_to_stderr(tmp_path):
    script = tmp_path / "hang.py"
    script.write_text("import sys, time\nprint('SCRIPT ERROR: boom', file=sys.stderr, flush=True)\n"
                      "time.sleep(30)\n")
    r = game.run([sys.executable, str(script)], timeout=2)
    assert r.returncode == 124 and "SCRIPT ERROR: boom" in r.out and "TIMEOUT after 2s" in r.out


def test_files_accept_godot_paths(monkeypatch):
    mod = types.ModuleType("module")
    mod.FILES = {"data/world.json": "{}"}
    monkeypatch.setitem(sys.modules, "module", mod)
    f = game.files()
    assert "res://data/world.json" in f and f["res://data/world.json"] == "{}"
    assert f.get("./data/world.json") == "{}" and "data/world.json" in f
    assert list(f) == ["data/world.json"]


# ----------------------------------------------------------- coder replies

def test_res_paths_in_replies_become_project_paths():
    files = forge.parse_files_reply("FILE: res://data/world.json\n```json\n{}\n```\n")
    assert files == {"data/world.json": "{}\n"}


def test_a_single_unlabelled_block_is_the_chunks_only_file(monkeypatch):
    monkeypatch.setattr(forge, "TOOLCHAIN", "godot")
    plan = {"contract": {"files": [{"path": "scripts/adventure.gd", "owner": "c3"},
                                   {"path": "scripts/game.gd", "owner": "c4"}]}}
    reply = "```gdscript\nclass_name Adventure extends RefCounted\n```\n"
    code, _ = forge.reply_to_code(reply, {}, {"id": "c3"}, plan)
    assert forge.python_to_files(code) == {"scripts/adventure.gd":
                                           "class_name Adventure extends RefCounted\n"}
    # two blocks, or a chunk owning two files: no guessing
    code, _ = forge.reply_to_code(reply + reply, {}, {"id": "c3"}, plan)
    assert "no FILE blocks" in code and "FILE: scripts/adventure.gd" in code


def test_an_unsafe_path_is_a_failed_attempt_not_a_stopped_run(monkeypatch):
    monkeypatch.setattr(forge, "TOOLCHAIN", "godot")
    code, _ = forge.reply_to_code("FILE: ../../etc/passwd\n```\nx\n```\n", {}, {"id": "c2"}, None)
    assert "raise SyntaxError" in code and "relative to the project root" in code
    ns = {}
    with pytest.raises(SyntaxError):
        exec(code, ns)


# ------------------------------------------------------------- headlines

GODOT_FAIL = """Traceback (most recent call last):
  File "/work/verify.py", line 23, in <module>
AssertionError: godot_eval produced no result:
Godot Engine v4.7.2.stable.official.ed1daf0bf - https://godotengine.org
SCRIPT ERROR: Parse Error: Mixed use of tabs and spaces for indentation.
          at: GDScript::reload (res://__forge_eval.gd:17)
ERROR: Failed to load script "res://__forge_eval.gd" with error "Parse error".
   at: load (modules/gdscript/gdscript_resource_format.cpp:46)
"""


def test_fail_signature_names_the_engine_error():
    assert forge.fail_signature(GODOT_FAIL) == ("SCRIPT ERROR: Parse Error: Mixed use of tabs and "
                                                "spaces for indentation.")


def test_fail_signature_keeps_a_plain_assertion():
    out = ("godot_eval: Godot reported script errors:\nSCRIPT ERROR: Trying to assign ...\n"
           "Traceback (most recent call last):\nAssertionError: A3: Wrong inventory: []\n")
    assert forge.fail_signature(out) == "AssertionError: A3: Wrong inventory: []"
    assert forge.fail_signature("x\nTIMEOUT after 120s\n") == "TIMEOUT after 120s"
    assert forge.fail_signature("") == ""


def test_test_writers_learn_paths_partial_projects_and_no_fstrings(monkeypatch):
    monkeypatch.setattr(forge, "TOOLCHAIN", "godot")
    text = forge.test_addendum()
    assert 'game.files()["data/world.json"]' in text
    assert "Files later chunks write do not exist yet" in text
    assert "never f-strings" in text and "{ }" in text


def test_godot_eval_switches_off_a_main_scene_that_does_not_exist_yet(godot_project, monkeypatch):
    sys.modules["module"].FILES = {"project.godot": 'config_version=5\n\n[application]\n'
                                   'run/main_scene="res://main.tscn"\n'}
    game.godot_eval("result = 1")
    seen = (godot_project / "godot.json.override").read_text()
    assert 'run/main_scene=""' in seen
    assert not (game.project_dir() / "override.cfg").exists()        # this run only


def test_godot_eval_runs_no_main_scene_even_one_that_exists(godot_project, monkeypatch):
    # The statements are a unit test run at the first frame: a main scene's
    # _ready() must not run before them (30 Oct: the harness moved from
    # _initialize to the first frame).
    sys.modules["module"].FILES = {"project.godot": '[application]\nrun/main_scene="res://main.tscn"\n',
                                   "main.tscn": "[gd_scene format=3]\n"}
    game.godot_eval("result = 1")
    assert 'run/main_scene=""' in (godot_project / "godot.json.override").read_text()
    assert not (game.project_dir() / "override.cfg").exists()
    assert game.missing_main_scene(game.project_dir()) is None


def test_missing_main_scene_cases(tmp_path):
    (tmp_path / "project.godot").write_text('[application]\nrun/main_scene="uid://abc"\n')
    assert game.missing_main_scene(tmp_path) is None
    (tmp_path / "project.godot").write_text('[application/run]\nmain_scene="res://scenes/main.tscn"\n')
    assert game.missing_main_scene(tmp_path) == "res://scenes/main.tscn"
    assert game.missing_main_scene(tmp_path / "nope") is None
