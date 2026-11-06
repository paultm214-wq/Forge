"""forge's side of the sbox, gmod, fo4 and blender toolchains: game folders
mounted read-only, per-toolchain limits, packaging into outputs/build,
model captures and `forge selftest` - without Docker (a fake `docker` on
PATH records what forge would run)."""
import ast
import json

import pytest

import forge
from conftest import make_run


@pytest.fixture
def games(tmp_path, monkeypatch):
    paths = {}
    for key in forge.GAME_DEFAULTS:
        d = tmp_path / "games" / key
        d.mkdir(parents=True)
        paths[key] = d
    monkeypatch.setattr(forge, "GAME_PATHS", dict(paths))
    return paths


@pytest.mark.parametrize("name,lang", [("sbox", "csharp"), ("gmod", "lua"), ("fo4", "csharp"),
                                       ("blender", "python")])
def test_new_toolchains_are_known(name, lang):
    assert forge.spec_toolchain(f"# X\n\nToolchain: {name}\n") == name
    assert forge.TOOLCHAIN_LANG[name] == lang
    tc = forge.TOOLCHAINS[name]
    assert tc["test_help"] and tc["fence"] and tc["pids"] > 256


def test_toolchain_mounts_resolve_and_say_what_is_missing(games, monkeypatch):
    assert forge.toolchain_mounts("fo4") == [(str(games["fallout4"].resolve()), "/fo4"),
                                             (str(games["fo4_scripts"].resolve()), "/fo4-scripts")]
    assert forge.toolchain_mounts("blender") == []
    games["gmod_server"].rmdir()
    with pytest.raises(forge.ForgeError, match=r"\[games\] gmod_server .* install-forge-toolchains.sh gmod"):
        forge.toolchain_mounts("gmod")


def test_set_game_paths_reads_config(tmp_path):
    forge.set_game_paths({"games": {"sbox": str(tmp_path / "s")}})
    try:
        assert forge.GAME_PATHS["sbox"] == tmp_path / "s"
        assert forge.GAME_PATHS["gmod_server"] == forge.expand(forge.GAME_DEFAULTS["gmod_server"])
    finally:
        forge.set_game_paths({})


def test_sandbox_mounts_game_folders_read_only_with_the_toolchains_limits(games, monkeypatch,
                                                                          fake_docker):
    argfile = fake_docker(0, "ok")
    monkeypatch.setattr(forge, "TOOLCHAIN", "gmod")
    sb = forge.Sandbox({"toolchains": {"gmod": {"cpus": "6"}}})
    ok, _ = sb.run({"t.py": "print(1)"}, "t.py")
    args = json.loads(argfile.read_text())
    assert ok
    assert f"{games['gmod_server'].resolve()}:/gmod-server:ro" in args
    assert args[args.index("--pids-limit") + 1] == "512"
    assert args[args.index("--cpus") + 1] == "6"
    assert args[args.index("--memory") + 1] == "3g"
    assert "forge-sandbox-gmod:latest" in args


def test_python_runs_get_no_game_mounts(games, fake_docker):
    argfile = fake_docker(0, "ok")
    forge.Sandbox({}).run({"t.py": "print(1)"}, "t.py")
    args = json.loads(argfile.read_text())
    assert not any(":/fo4" in a or ":/sbox" in a for a in args)
    assert args[args.index("--pids-limit") + 1] == "256"


def test_captures_script_renders_models(monkeypatch):
    items = [{"name": "barrel", "model": "assets/models/barrel.glb", "views": ["front", "iso"],
              "size": [256, 256]}]
    src = forge.captures_script(items, "blender")
    ast.parse(src)
    assert "game.blender_render(game.project_dir() / model" in src
    assert "BUILD = ''" in src                              # no {build}: nothing built
    built = forge.captures_script([{"name": "b", "model": "{build}/b.glb"}], "blender")
    assert "BUILD = str(game.blender_build())" in built
    assert "from pathlib import Path" in src


def test_build_files_outputs_packages_fo4_into_outputs_build(tmp_path, monkeypatch):
    monkeypatch.setattr(forge, "TOOLCHAIN", "fo4")
    run = make_run(tmp_path, spec="# M\n\nToolchain: fo4\n", plan={"chunks": []})
    seen = {}

    class SB:
        outputs_max_bytes = 10 * 2**20

        def run(self, files, entry, args=None, out_dir=None, **kw):
            seen["script"] = files[entry]
            seen["args"] = args
            (out_dir / "build" / "Scripts").mkdir(parents=True)
            (out_dir / "build" / "Scripts" / "A.pex").write_bytes(b"pex")
            (out_dir / "build" / "Mod.esp").write_bytes(b"TES4")
            return True, "packaged: Scripts/A.pex, Mod.esp\n"

    module = forge.files_to_python({"Scripts/Source/User/A.psc": "ScriptName A\n"})
    forge.build_files_outputs(run, SB(), module)
    out = run.root / "outputs"
    assert (out / "build" / "Mod.esp").read_bytes() == b"TES4"
    assert (out / "build" / "Scripts" / "A.pex").exists()
    assert (out / "project" / "Scripts" / "Source" / "User" / "A.psc").exists()
    assert "game.package('fo4', sys.argv[1] + '/build')" in seen["script"]
    assert seen["args"] == ["OUT"]


def test_godot_projects_are_not_packaged(tmp_path, monkeypatch):
    monkeypatch.setattr(forge, "TOOLCHAIN", "godot")
    run = make_run(tmp_path, spec="# G\n", plan={"chunks": []})

    class SB:
        outputs_max_bytes = 2**20

        def run(self, *a, **kw):
            raise AssertionError("no sandbox run expected")

    forge.build_files_outputs(run, SB(), forge.files_to_python({"main.gd": "x\n"}))
    assert not (run.root / "outputs" / "build").exists()


@pytest.mark.parametrize("name", ["sbox", "gmod", "fo4", "blender"])
def test_selftest_scripts_are_valid(name):
    ast.parse(forge.SELFTEST_SCRIPT % forge.SELFTEST_STEPS[name])
    files = forge.SELFTEST_FILES[name]
    assert forge.python_to_files(forge.files_to_python(files)) == files


def test_cmd_selftest_runs_the_sample_in_the_toolchains_sandbox(games, monkeypatch, capsys):
    seen = {}

    def fake_run(self, files, entry, args=None, out_dir=None, max_out=4000, timeout=None):
        seen.update(toolchain=forge.TOOLCHAIN, files=files, entry=entry, max_out=max_out)
        return True, "PASS a\nSELFTEST OK"

    monkeypatch.setattr(forge.Sandbox, "run", fake_run)
    assert forge.cmd_selftest({"sandbox": {}}, "fo4") == 0
    assert seen["toolchain"] == "fo4" and seen["entry"] == "selftest.py"
    assert "Plugin/Program.cs" in forge.python_to_files(seen["files"]["module.py"])
    out = capsys.readouterr().out
    assert "forge-sandbox-fo4:latest" in out and "-> /fo4" in out and "selftest fo4: OK" in out


def test_cmd_selftest_fails_when_the_sandbox_fails(games, monkeypatch):
    monkeypatch.setattr(forge.Sandbox, "run", lambda self, *a, **kw: (False, "FAIL x"))
    assert forge.cmd_selftest({"sandbox": {}}, "blender") == 1
    with pytest.raises(forge.ForgeError, match="selftest knows"):
        forge.cmd_selftest({}, "godot")


def test_selftest_png_is_a_real_png():
    raw = forge.selftest_png(48)
    w, h, ch, px = forge.png_decode(raw)
    assert (w, h, ch) == (48, 48, 3)
    assert tuple(px[0:3]) == (255, 255, 255)               # white corner
    mid = (24 * 48 + 24) * 3
    assert px[mid] > 100 and px[mid + 1] < 60               # red ball in the middle
