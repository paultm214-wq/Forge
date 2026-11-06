"""Toolchains (FILES mode), game art, private files and provenance, captures,
playtest - all without Docker, a GPU or the network."""
import io
import json
import tarfile

import pytest

import forge


# ------------------------------------------------------------ FILES mode

REPLY = '''Here you go.

FILE: scripts/player.gd
```gdscript
extends CharacterBody2D
var speed = 200
```

FILE: `main.tscn`
```
[gd_scene format=3]
```

```lua file=conf.lua
function love.conf(t) t.window.title = "x" end
```
'''


def test_parse_files_reply_reads_every_block():
    files = forge.parse_files_reply(REPLY)
    assert list(files) == ["scripts/player.gd", "main.tscn", "conf.lua"]
    assert files["scripts/player.gd"] == "extends CharacterBody2D\nvar speed = 200\n"


@pytest.mark.parametrize("bad", ["../etc/passwd", "/abs/x.gd", "a/../../b"])
def test_parse_files_reply_refuses_unsafe_paths(bad):
    with pytest.raises(forge.ForgeError):
        forge.parse_files_reply(f"FILE: {bad}\n```\nx\n```\n")


@pytest.mark.parametrize("text", ["plain\n", "has ''' quotes\n", "ends with backslash \\",
                                  "tab\tand \\n escape\n", "crlf\r\nline\n"])
def test_files_round_trip(text):
    files = {"a.gd": text, "dir/b.c": "int main(){return 0;}\n"}
    code = forge.files_to_python(files)
    assert forge.python_to_files(code) == files
    ns = {}
    exec(code, ns)
    assert ns["FILES"] == files


def test_chunks_assemble_into_one_files_dict():
    c1 = forge.files_to_python({"a.gd": "A\n"})
    c2 = forge.files_to_python({"b.gd": "B\n"})
    module = forge.assemble({"c1": c1, "c2": c2}, ["c1", "c2"])
    ns = {}
    exec(module, ns)
    assert ns["FILES"] == {"a.gd": "A\n", "b.gd": "B\n"}


def test_enforce_files_drops_other_owners_and_reports_missing(monkeypatch):
    monkeypatch.setattr(forge, "TOOLCHAIN", "godot")
    plan = {"contract": {"files": [{"path": "a.gd", "owner": "c1"},
                                   {"path": "b.gd", "owner": "c2"},
                                   {"path": "c.gd", "owner": "c1"}]}}
    code = forge.files_to_python({"a.gd": "A\n", "b.gd": "B\n"})
    code, taken, problems = forge.enforce_files(code, "c1", plan)
    assert list(forge.python_to_files(code)) == ["a.gd"]
    assert taken == ["b.gd (owned by c2)"]
    assert len(problems) == 1 and "c.gd" in problems[0]


def test_reply_to_code_and_block_render_in_files_mode(monkeypatch):
    monkeypatch.setattr(forge, "TOOLCHAIN", "godot")
    code, _ = forge.reply_to_code(REPLY, {}, {"id": "c1"})
    assert "FILES[" in code
    shown = forge.Block("c1", code, "full", 1).render()
    assert "FILE: scripts/player.gd" in shown and "```gdscript" in shown
    names = forge.Block("c1", code, "names", 1).render()
    assert "FILE: main.tscn" in names and "extends" not in names
    assert "FILE:" in forge.coder_system() and "GDScript" in forge.coder_system()


def test_python_mode_unchanged():
    assert forge.coder_system() is forge.CODER_SYSTEM
    assert forge.fix_system() is forge.FIX_SYSTEM
    assert forge.contract_system() is forge.CONTRACT_SYSTEM
    assert "import forge_game" not in forge.IMPORT_ALL.format(mod="module")


def test_spec_toolchain():
    assert forge.spec_toolchain("# x\nToolchain: Godot\n") == "godot"
    assert forge.spec_toolchain("# x\n") == "python"
    with pytest.raises(forge.ForgeError):
        forge.spec_toolchain("Toolchain: cobol\n")


def test_validate_contract_files(monkeypatch):
    monkeypatch.setattr(forge, "TOOLCHAIN", "godot")
    plan = {"chunks": [{"id": "c1"}, {"id": "c2"}]}
    ok = {"names": [{"name": "Player.jump", "owner": "c1", "signature": "func jump() -> void"}],
          "files": [{"path": "player.gd", "owner": "c1"}]}
    assert forge.validate_contract(ok, plan) == []
    bad = {"names": [], "files": [{"path": "../x", "owner": "c1"},
                                  {"path": "a.gd", "owner": "c9"}]}
    errs = forge.validate_contract(bad, plan)
    assert len(errs) == 2


def test_files_mode_test_runs_in_sandbox(monkeypatch, local_sandbox):
    """A chunk test builds the project folder with the game helper."""
    monkeypatch.setattr(forge, "TOOLCHAIN", "lua-love")
    module = forge.files_to_python({"main.lua": "print('hi')\n", "lib/util.lua": "return {}\n"})
    test = ("d = game.project_dir()\n"
            "assert (d / 'lib' / 'util.lua').read_text() == 'return {}\\n'\n"
            "assert sorted(game.files()) == ['lib/util.lua', 'main.lua']\n"
            "r = game.run(['python3', '-c', 'print(40 + 2)'])\n"
            "assert r.ok and r.out.strip() == '42', r.out\n"
            "print('PASS')\n")
    assert not forge.test_name_problems(test, "spec", {"chunks": [{"id": "c1"}]}, "c1")
    ok, out = local_sandbox.run(forge.verify_files(module, test), "verify.py")
    assert ok, out


def test_toolchain_profile():
    sb = forge.Sandbox({"memory": "1g", "toolchains": {"rust": {"memory": "6g"}}})
    assert sb.profile()["image"] == "forge-sandbox:latest"
    forge.set_toolchain("rust")
    try:
        p = sb.profile()
        assert p["image"] == "forge-sandbox-rust:latest" and p["memory"] == "6g"
        assert p["timeout"] >= 900
    finally:
        forge.set_toolchain("python")


def test_docker_command_mounts_read_only(monkeypatch, tmp_path):
    """The toolchain image, the mounts and the exec /tmp reach docker run."""
    seen = {}

    class P:
        returncode, stdout, stderr = 0, "PASS", ""

    def fake_run(cmd, **kw):
        seen["cmd"] = cmd
        return P()
    monkeypatch.setattr(forge.subprocess, "run", fake_run)
    monkeypatch.setattr(forge, "TOOLCHAIN", "godot")
    monkeypatch.setattr(forge, "SANDBOX_MOUNTS", [(str(tmp_path), "/vendor")])
    ok, _ = forge.Sandbox({}).run({"module.py": "FILES = {}\n", "verify.py": "print(1)"}, "verify.py")
    cmd = " ".join(seen["cmd"])
    assert ok and "forge-sandbox-godot:latest python3 verify.py" in cmd
    assert f"{tmp_path}:/vendor:ro" in cmd and "--network none" in cmd
    assert "/tmp:rw,exec,size=1536m" in cmd


# ---------------------------------------------------------------- art

def solid(w, h, rgb):
    return forge.png_encode(w, h, 3, bytes(rgb) * (w * h))


def test_chroma_pixelate_palette_scale():
    w, h = 8, 8
    px = bytearray()
    for y in range(h):
        for x in range(w):
            px += bytes((255, 0, 255)) if x < 4 else bytes((250, 10, 10))
    raw = forge.png_encode(w, h, 3, bytes(px))
    out = forge.postprocess_image({"chroma_key": "#ff00ff", "pixelate": {"width": 2, "height": 2},
                                   "palette": "pico8", "scale": 3}, raw)
    W, H, ch, p = forge.png_decode(out)
    assert (W, H, ch) == (6, 6, 4)
    left, right = p[0:4], p[4 * 5:4 * 6]
    assert left[3] == 0                         # keyed out
    assert tuple(right) == (255, 0, 77, 255)    # pico8 red ff004d


def test_median_cut_reduces_colours():
    px = bytearray()
    for i in range(64):
        px += bytes((i * 4, 255 - i * 4, 128, 255))
    out = forge.img_palette(px, 4)
    cols = {tuple(out[i:i + 3]) for i in range(0, len(out), 4)}
    assert len(cols) <= 4


def test_pack_sheet():
    sheet = forge.pack_sheet([solid(4, 4, (255, 0, 0)), solid(4, 4, (0, 255, 0)),
                              solid(4, 4, (0, 0, 255))], 2)
    w, h, ch, p = forge.png_decode(sheet)
    assert (w, h) == (8, 8)
    assert tuple(p[4 * 4:4 * 4 + 3]) == (0, 255, 0)     # frame 2, top right
    assert tuple(p[4 * (4 * 8):4 * (4 * 8) + 3]) == (0, 0, 255)


def test_spec_assets_validates_art_keys():
    ok = '## Assets\n```json\n[{"id": "a1", "filename": "a.png", "prompt": "p", "palette": "gameboy", "frames": 4}]\n```\n'
    assert forge.spec_assets(ok)[0]["frames"] == 4
    bad = ok.replace('"palette": "gameboy"', '"pixelate": 16')
    with pytest.raises(forge.ForgeError):
        forge.spec_assets(bad)


# ------------------------------------------------- private files, provenance

def test_provenance_gate(tmp_path, monkeypatch):
    rom = tmp_path / "game.ch8"
    rom.write_bytes(b"\x00\xe0")
    spec = f"# x\n\n## Private files\n- test-rom: {rom}\n"
    with pytest.raises(forge.ForgeError, match="Provenance"):
        forge.check_private_files(spec)
    with pytest.raises(forge.ForgeError, match="test-rom"):
        forge.check_private_files(spec + "\n## Provenance\n- test-rom: found it online\n")
    good = spec + "\n## Provenance\n- test-rom: my own homebrew, I wrote it\n"
    assert forge.check_private_files(good) == {"test-rom": rom}
    monkeypatch.setattr(forge, "TOOLCHAIN", "re")
    with pytest.raises(forge.ForgeError, match="Provenance"):
        forge.check_private_files("# re project\n")


def test_private_file_missing(tmp_path):
    with pytest.raises(forge.ForgeError, match="not found"):
        forge.check_private_files(f"## Private files\n- x: {tmp_path}/nope\n")


# ------------------------------------------------------------- captures

def test_captures_parse_and_script():
    spec = ('## Captures\n```json\n[{"name": "title", "scene": "res://main.tscn", "frames": 30, '
            '"keep": [0, 29]}, {"name": "win", "cmd": ["{project}/build/game"], "at": [1.5]}]\n```\n')
    items = forge.spec_captures(spec)
    assert [i["name"] for i in items] == ["title", "win"]
    compile(forge.captures_script(items), "captures.py", "exec")


# ------------------------------------------------------------- playtest

def test_playtest_copies_and_writes_checklist(tmp_path, monkeypatch):
    run_dir = tmp_path / "run"
    (run_dir / "outputs" / "project").mkdir(parents=True)
    (run_dir / "SPEC.md").write_text("# g\nToolchain: godot\n\n## Playtest\n- Jump feels right\n")
    (run_dir / "plan.json").write_text(json.dumps({"project": "jumper", "toolchain": "godot",
                                                   "chunks": []}))
    calls = []

    class P:
        returncode, stdout, stderr = 0, "", ""
    monkeypatch.setattr(forge.subprocess, "run", lambda cmd, **kw: calls.append(cmd) or P())
    assert forge.cmd_playtest(forge.Run(run_dir), {}, None) == 0
    note = (run_dir / "outputs" / "project" / "PLAYTEST.md").read_text()
    assert "- Jump feels right" in note and "godot --path ~/playtest/jumper" in note
    assert calls[0][:3] == ["rsync", "-a", "--delete"] and calls[0][-1] == "vulcan:playtest/jumper/"


# ------------------------------------------------- end to end, FILES mode

def test_end_to_end_files_mode(tmp_path):
    import subprocess
    from conftest import FakeServer
    spec_text = ("# lunar\n\nToolchain: lua-love\n\nA LOVE game.\n\n## Acceptance\n"
                 "1. main.lua draws\n2. conf.lua sets the title\n")
    plan = {"project": "lunar", "summary": "a game", "acceptance_ok": True, "missing": [],
            "chunks": [
                {"id": "c1", "kind": "code", "title": "main", "spec": "write main.lua",
                 "depends_on": [], "verify": "d = game.project_dir()\n"
                 "assert 'love.draw' in (d / 'main.lua').read_text()\nprint('PASS')"},
                {"id": "c2", "kind": "code", "title": "conf", "spec": "write conf.lua",
                 "depends_on": ["c1"], "verify": "assert 'lunar' in game.files()['conf.lua']\n"
                 "assert 'main.lua' in game.files()\nprint('PASS')"}]}
    contract = {"names": [], "files": [{"path": "main.lua", "owner": "c1"},
                                       {"path": "conf.lua", "owner": "c2"}],
                "schema_sql": "", "rules": [], "routes": [], "dom_hooks": [], "payloads": []}

    def coder(u, b):
        if "YOUR TASK: c1" in u:
            return "FILE: main.lua\n```lua\nfunction love.draw() end\n```\n"
        return "FILE: conf.lua\n```lua\nfunction love.conf(t) t.window.title = 'lunar' end\n```\n"

    srv = FakeServer([
        ("look for existing, freely licensed code", lambda u, b: '{"searches": []}'),
        ("into a plan that", lambda u, b: json.dumps(plan)),
        ("INTERFACE CONTRACT for a module", lambda u, b: json.dumps(contract)),
        ("precise engineer implementing ONE chunk", coder),
        ("You review generated code", lambda u, b: '{"approved": true, "issues": []}'),
    ])
    try:
        cfg = {"planner": {"url": srv.url, "context": 200_000, "max_tokens": 8000},
               "coder": {"url": srv.url, "context": 100_000, "max_tokens": 8000},
               "sandbox": {"unsafe_local_exec": True, "timeout": 60},
               "loop": {"test_audit": False, "staged_plan": False},
               "claude": {"enabled": False},
               "git": {"repo": str(tmp_path / "repo"), "remote": "nowhere"},
               "paths": {"runs": str(tmp_path / "runs")}}
        sp = tmp_path / "lunar.md"
        sp.write_text(spec_text)
        run = forge.Run.create(tmp_path / "runs", sp)
        branch = forge.execute(run, cfg)
        assert json.loads((run.root / "plan.json").read_text())["toolchain"] == "lua-love"
        assert (run.root / "outputs" / "project" / "conf.lua").read_text().count("lunar") == 1
        files = subprocess.run(["git", "-C", str(tmp_path / "repo"), "show", "--stat",
                                "--format=", branch], capture_output=True, text=True).stdout
        assert "outputs/project/main.lua" in files and "tests/forge_game.py" in files
        coder_prompts = [b for p, b in srv.calls if p == "precise engineer implementing ONE chunk"]
        assert "HOW THE TEST RUNS IT" in coder_prompts[0]["messages"][1]["content"]
    finally:
        srv.close()


# ---------------------------------- real tools, when this machine has them

import shutil as _sh


@pytest.mark.skipif(not (_sh.which("cmake") and _sh.which("cc")), reason="needs cmake and a C compiler")
def test_c_project_builds_and_runs_in_files_mode(monkeypatch, local_sandbox, tmp_path):
    monkeypatch.setattr(forge, "TOOLCHAIN", "c-sdl")
    monkeypatch.setenv("FORGE_TMP", str(tmp_path))
    module = forge.files_to_python({
        "CMakeLists.txt": "cmake_minimum_required(VERSION 3.16)\nproject(t C)\n"
                          "add_executable(hello src/hello.c)\n",
        "src/hello.c": '#include <stdio.h>\nint main(void){printf("%d\\n", 6*7);return 0;}\n'})
    test = ("b = game.cmake_build()\n"
            "r = game.run([str(b / 'hello')], check=True)\n"
            "assert r.out.strip() == '42', r.out\nprint('PASS')\n")
    ok, out = local_sandbox.run(forge.verify_files(module, test), "verify.py")
    assert ok, out


@pytest.mark.skipif(not (_sh.which("Xvfb") and _sh.which("import") and _sh.which("display")),
                    reason="needs Xvfb and ImageMagick")
def test_x11_frames_captures_a_window(monkeypatch, local_sandbox, tmp_path):
    """The capture path every non-Godot toolchain uses: a program under Xvfb,
    screenshots at given times, read back with game.load_png."""
    monkeypatch.setattr(forge, "TOOLCHAIN", "c-sdl")
    monkeypatch.setenv("FORGE_TMP", str(tmp_path))
    red = forge.png_encode(64, 64, 3, bytes((255, 0, 0)) * 4096)
    (tmp_path / "red.png").write_bytes(red)
    test = (f"shots = game.x11_frames(['display', '-geometry', '+0+0', {str(tmp_path / 'red.png')!r}],"
            " at=(3.0,), size=(160, 120))\n"
            "img = game.load_png(shots[0])\n"
            "assert (img.w, img.h) == (160, 120)\n"
            "assert game.close(img.px(10, 10)[:3], (255, 0, 0)), img.px(10, 10)\n"
            "assert not game.is_blank(img)\nprint('PASS')\n")
    ok, out = local_sandbox.run(forge.verify_files(forge.files_to_python({"x": "y\n"}), test),
                                "verify.py")
    assert ok, out


def test_commented_sections_are_ignored(tmp_path):
    spec = "# x\n<!--\n## Private files\n- a: /nope\nToolchain: rust\n-->\n"
    assert forge.check_private_files(spec) == {}
    assert forge.spec_toolchain(spec) == "python"


@pytest.mark.skipif(not (_sh.which("Xvfb") and _sh.which("import") and _sh.which("display")),
                    reason="needs Xvfb and ImageMagick")
def test_build_files_outputs_writes_project_and_captures(monkeypatch, local_sandbox, tmp_path):
    monkeypatch.setattr(forge, "TOOLCHAIN", "c-sdl")
    monkeypatch.setenv("FORGE_TMP", str(tmp_path / "sbx"))
    xpm = ('/* XPM */\nstatic char *p[] = {"8 8 1 1", "r c #00ff00",'
           + ",".join(['"rrrrrrrr"'] * 8) + "};\n")
    root = tmp_path / "run"
    (root / "assets").mkdir(parents=True)
    (root / "SPEC.md").write_text(
        '# x\n## Captures\n```json\n[{"name": "pic", "cmd": ["display", "-geometry", "+0+0", '
        '"-resize", "800%", "{project}/art/p.xpm"], "at": [3.0], "size": [120, 90]}]\n```\n')
    run = forge.Run(root)
    module = forge.files_to_python({"art/p.xpm": xpm, "README.md": "hi\n"})
    files = forge.build_files_outputs(run, local_sandbox, module)
    names = {p.relative_to(root / "outputs").as_posix() for p in files}
    assert {"project/art/p.xpm", "project/README.md", "captures/pic_0000.png"} <= names
    img = forge.png_decode((root / "outputs" / "captures" / "pic_0000.png").read_bytes())
    w, h, ch, px = img
    inside = 4 * (20 * w + 20)
    assert (w, h) == (120, 90) and tuple(px[inside:inside + 3]) == (0, 255, 0)


def test_assets_keep_subfolders(tmp_path):
    spec = ('## Assets\n```json\n[{"id": "a1", "filename": "1x/j_x.png", "prompt": "p"},'
            '{"id": "a2", "filename": "2x/j_x.png", "prompt": "p"}]\n```\n')
    assert [a["filename"] for a in forge.spec_assets(spec)] == ["1x/j_x.png", "2x/j_x.png"]
    with pytest.raises(forge.ForgeError):
        forge.spec_assets(spec.replace("1x/j_x.png", "../j_x.png"))
    src = tmp_path / "assets"
    (src / "1x").mkdir(parents=True)
    (src / "1x" / "j_x.png").write_bytes(b"a")
    (src / "j_x.frame0.png").write_bytes(b"f")
    forge.copy_assets(src, tmp_path / "out")
    assert [p.relative_to(tmp_path / "out").as_posix() for p in (tmp_path / "out").rglob("*.png")] == ["1x/j_x.png"]


def test_playtest_install_to_and_launch(tmp_path, monkeypatch):
    run_dir = tmp_path / "run"
    (run_dir / "outputs" / "project").mkdir(parents=True)
    (run_dir / "SPEC.md").write_text(
        "# m\nToolchain: lua-love\n\n## Playtest\n\nInstall to: ~/Games/Mods/lab\n"
        "Launch: start the game from Steam\n- Mod loads\n")
    (run_dir / "plan.json").write_text(json.dumps({"project": "m", "chunks": []}))
    calls = []

    class P:
        returncode, stdout, stderr = 0, "", ""
    monkeypatch.setattr(forge.subprocess, "run", lambda cmd, **kw: calls.append(cmd) or P())
    forge.cmd_playtest(forge.Run(run_dir), {}, "vulcan")
    assert calls[0][-1] == "vulcan:Games/Mods/lab/"
    note = (run_dir / "outputs" / "project" / "PLAYTEST.md").read_text()
    assert "start the game from Steam" in note and "Install to" not in note


def test_rea_helper_and_toolchain_help(monkeypatch, local_sandbox, tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "rea").write_text('#!/bin/sh\necho "REA analysing"\n'
                                'echo \'{"modules": ["main.js"], "args": "\'"$*"\'"}\'\n')
    (bindir / "rea").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}:{forge.os.environ['PATH']}")
    monkeypatch.setenv("FORGE_TMP", str(tmp_path / "sbx"))
    monkeypatch.setattr(forge, "TOOLCHAIN", "re")
    test = ("r = game.rea('analyze-javascript-application', '/x', '--json')\n"
            "assert r['modules'] == ['main.js'], r\n"
            "assert 'analyze-javascript-application /x --json' in r['args']\n"
            "assert game.rea('--help').ok\nprint('PASS')\n")
    ok, out = local_sandbox.run(forge.verify_files(forge.files_to_python({"a.txt": "x\n"}), test),
                                "verify.py")
    assert ok, out
    assert "morluto/rea" in forge.toolchain_help("re")
    (forge.FORGE_HOME / "toolchain-help").mkdir(parents=True)
    (forge.FORGE_HOME / "toolchain-help" / "re.txt").write_text("Usage: rea <command>\n  analyze-dotnet-assembly")
    assert "analyze-dotnet-assembly" in forge.toolchain_help("re")
    assert "analyze-dotnet-assembly" in forge.test_addendum()
