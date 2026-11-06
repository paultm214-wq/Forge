"""forge_game's s&box, Garry's Mod, Fallout 4 and Blender helpers, run against
fake installs and fake programs (dotnet, mono, wine, srcds_run, blender) on
PATH: what each helper writes, runs and reads back - no game, engine or
Docker needed. The real thing is `forge selftest <toolchain>` on Gemini."""
import ast
import json
import os
import struct
import sys
import types
import zlib
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import forge_game as game  # noqa: E402


@pytest.fixture
def project(tmp_path, monkeypatch):
    """game.TMP in tmp_path and a `module` holding FILES."""
    monkeypatch.setattr(game, "TMP", tmp_path / "tmp")
    (tmp_path / "tmp").mkdir()
    monkeypatch.setattr(game, "_project", None)
    monkeypatch.setenv("HOME", str(tmp_path / "tmp" / "home"))
    mod = types.ModuleType("module")
    mod.FILES = {}
    monkeypatch.setitem(sys.modules, "module", mod)
    return mod


@pytest.fixture
def bindir(tmp_path, monkeypatch):
    d = tmp_path / "bin"
    d.mkdir()
    monkeypatch.setenv("PATH", f"{d}{os.pathsep}{os.environ['PATH']}")

    def add(name, body):
        p = d / name
        p.write_text(f"#!{sys.executable}\n" + body)
        p.chmod(0o755)
        return p
    return add


# ----------------------------------------------------------------- shared

def test_mirror_tree_links_files_keeps_links_and_allows_new_files(tmp_path):
    src = tmp_path / "src"
    (src / "drive_c" / "windows").mkdir(parents=True)
    (src / "drive_c" / "windows" / "k.dll").write_text("dll")
    (src / "system.reg").write_text("reg")
    (src / "dosdevices").mkdir()
    os.symlink("../drive_c", src / "dosdevices" / "c:")
    os.symlink("/", src / "dosdevices" / "z:")
    dst = game.mirror_tree(src, tmp_path / "dst")
    assert (dst / "drive_c" / "windows").is_dir() and not (dst / "drive_c").is_symlink()
    assert (dst / "drive_c" / "windows" / "k.dll").is_symlink()
    assert os.readlink(dst / "dosdevices" / "c:") == "../drive_c"
    assert (dst / "dosdevices" / "c:" / "windows" / "k.dll").read_text() == "dll"
    assert os.readlink(dst / "dosdevices" / "z:") == "/"
    (dst / "drive_c" / "new.txt").write_text("x")          # new files land in dst
    assert not (src / "drive_c" / "new.txt").exists()
    game._make_real(dst / "system.reg")
    assert not (dst / "system.reg").is_symlink() and (dst / "system.reg").read_text() == "reg"
    (dst / "system.reg").write_text("changed")
    assert (src / "system.reg").read_text() == "reg"


def test_checks_report_failures_and_errors():
    ok = game.Checks([{"name": "a", "ok": True}])
    assert ok.ok and ok.assert_ok() is ok
    bad = game.Checks([{"name": "a", "ok": True}, {"name": "b", "ok": False, "detail": "x=3"}],
                      errors=["lua/x.lua:1: boom"], out="log")
    assert not bad.ok and bad.failed == [{"name": "b", "ok": False, "detail": "x=3"}]
    with pytest.raises(AssertionError, match=r"FAIL b \(x=3\)[\s\S]*ERROR lua/x.lua:1: boom"):
        bad.assert_ok()
    with pytest.raises(AssertionError, match="no checks were reported"):
        game.Checks([]).assert_ok()


def test_cs_errors_dedupes_dotnets_doubled_lines():
    out = ("Code/A.cs(3,5): error CS0103: The name 'X' does not exist [/t/F.csproj]\n"
           "warning CS0168: unused\n"
           "Code/A.cs(3,5): error CS0103: The name 'X' does not exist [/t/F.csproj]\n"
           "error NU1101: Unable to find package Foo\n")
    assert game.cs_errors(out) == ["Code/A.cs(3,5): error CS0103: The name 'X' does not exist",
                                   "error NU1101: Unable to find package Foo"]


def test_trx_results(tmp_path):
    trx = tmp_path / "r.trx"
    trx.write_text("""<?xml version="1.0"?>
<TestRun xmlns="http://microsoft.com/schemas/VisualStudio/TeamTest/2010"><Results>
<UnitTestResult testName="Good" outcome="Passed" />
<UnitTestResult testName="Bad" outcome="Failed"><Output><ErrorInfo>
<Message>Assert.AreEqual failed. Expected:&lt;39&gt;. Actual:&lt;38&gt;.</Message>
</ErrorInfo></Output></UnitTestResult></Results></TestRun>""")
    assert game.trx_results(trx) == [
        {"name": "Good", "outcome": "Passed", "message": ""},
        {"name": "Bad", "outcome": "Failed",
         "message": "Assert.AreEqual failed. Expected:<39>. Actual:<38>."}]
    assert game.trx_results(tmp_path / "missing.trx") == []


def test_nuget_env_has_no_sources_and_mirrors_the_cache(project, tmp_path, monkeypatch):
    cache = tmp_path / "nuget-cache"
    (cache / "mstest.testframework" / "3.6.4").mkdir(parents=True)
    (cache / "mstest.testframework" / "3.6.4" / "x.nupkg").write_text("pkg")
    monkeypatch.setattr(game, "NUGET_CACHE", cache)
    env = game._nuget_env()
    cfg = Path(env["HOME"]) / ".nuget" / "NuGet" / "NuGet.Config"
    assert "<clear />" in cfg.read_text()
    pkgs = Path(env["NUGET_PACKAGES"])
    assert (pkgs / "mstest.testframework" / "3.6.4" / "x.nupkg").is_symlink()
    assert env["MSBUILDDISABLENODEREUSE"] == "1" and env["DOTNET_CLI_USE_MSBUILD_SERVER"] == "0"


# ------------------------------------------------------------------ s&box

def fake_sbox(tmp_path):
    s = tmp_path / "sbox"
    m = s / "bin" / "managed"
    m.mkdir(parents=True)
    for n in ("Sandbox.Engine.dll", "Sandbox.System.dll", "Sandbox.Generator.dll",
              "Sandbox.CodeUpgrader.dll", "SkiaSharp.dll", "Other.dll"):
        (m / n).write_text("")
    (s / "bin" / "linuxsteamrt64").mkdir()
    (s / "bin" / "win64").mkdir()
    (s / "bin" / "win64" / "tier0.dll").write_text("")
    (s / "config").mkdir()
    (s / "config" / "a.cfg").write_text("cfg")
    (s / "core").mkdir()
    (s / ".source2").mkdir()
    return s


FAKE_DOTNET = r'''
import json, os, re, sys
from pathlib import Path
args = sys.argv[1:]
cwd = Path.cwd()
log = Path(os.environ["FAKE_LOG"])
with open(log, "a") as fh:
    fh.write(json.dumps({"args": args, "cwd": str(cwd),
                         "env": {k: os.environ.get(k) for k in ("FACEPUNCH_ENGINE", "SDL_VIDEODRIVER",
                                 "LD_LIBRARY_PATH", "NUGET_PACKAGES", "LocalAppData",
                                 "FORGE_FO4_DATA")}}) + "\n")
srcs = "".join(p.read_text() for p in cwd.rglob("*.cs"))
if args[0] == "build":
    if "NoSuchThing" in srcs:
        for _ in range(2):
            print("ForgeEval.cs(30,10): error CS0103: The name 'NoSuchThing' does not exist "
                  "in the current context [/t/ForgeTests.csproj]")
        sys.exit(1)
    print("Build succeeded.")
    sys.exit(0)
if args[0] == "test":
    res = Path(args[args.index("--results-directory") + 1])
    res.mkdir(parents=True, exist_ok=True)
    names = re.findall(r"public void (\w+)\(\)", srcs)
    m = re.search(r'Path\.Combine\( "([^"]+)", name', srcs)
    if m:
        for ident in re.findall(r'ForgeOut\.Write\( "(\w+)", result', srcs):
            value = {"boom": None}.get(ident, 42)
            data = {"__error": "System.Exception: boom"} if ident == "boom" else {"value": value}
            Path(m.group(1), ident + ".json").write_text(json.dumps(data))
    rows = "".join(f'<UnitTestResult testName="{n}" outcome="{"Failed" if "Bad" in n else "Passed"}">'
                   f'<Output><ErrorInfo><Message>m</Message></ErrorInfo></Output></UnitTestResult>'
                   for n in names)
    (res / "results.trx").write_text(f'<TestRun xmlns="x"><Results>{rows}</Results></TestRun>')
    print("Passed!")
    sys.exit(0)
if args[0] == "run":
    out = Path(args[args.index("--") + 1])
    (out / "Made.esp").write_bytes(b"TES4")
    sys.exit(0)
sys.exit(2)
'''


def test_sbox_csproj_references_the_install_like_sboxs_generator(tmp_path):
    s = fake_sbox(tmp_path)
    text = game.sbox_csproj("T", ["/tmp/project/Code/**/*.cs"], s / "bin" / "managed")
    assert '<Project Sdk="Microsoft.NET.Sdk.Razor">' in text
    assert "<TargetFramework>net10.0</TargetFramework>" in text and "<LangVersion>14</LangVersion>" in text
    assert f'<Reference Include="{s}/bin/managed/Sandbox.Engine.dll" />' in text
    assert f'<Reference Include="{s}/bin/managed/SkiaSharp.dll" />' in text
    assert "Other.dll" not in text
    assert f'<Analyzer Include="{s}/bin/managed/Sandbox.Generator.dll" />' in text
    assert 'Reference Include="' + str(s / "bin" / "managed" / "Sandbox.Generator.dll") not in text
    assert '<PackageReference Include="MSTest.TestFramework" Version="3.6.4" />' in text
    assert '<Compile Include="/tmp/project/Code/**/*.cs" />' in text
    bare = game.sbox_csproj("T", [], None)
    assert "Reference Include" not in bare.replace("PackageReference", "") and "Analyzer" not in bare


def test_sbox_global_usings_add_the_game_namespace_only_if_the_install_has_it(tmp_path):
    s = fake_sbox(tmp_path)
    m = s / "bin" / "managed"
    assert "GlobalGameNamespace" not in game.sbox_global_usings(m)
    (m / "Sandbox.Game.dll").write_bytes(b"MZ...Sandbox.Internal\0GlobalGameNamespace\0")
    assert "global using static Sandbox.Internal.GlobalGameNamespace;" in game.sbox_global_usings(m)


def test_sbox_engine_dir_is_a_writable_stand_in(project, tmp_path, monkeypatch):
    s = fake_sbox(tmp_path)
    monkeypatch.setattr(game, "SBOX", s)
    e = game.sbox_engine_dir()
    assert (e / "bin" / "win64" / "engine2.dll").read_bytes() == b""
    assert not (e / "bin" / "win64").is_symlink()
    assert (e / "bin" / "managed").is_symlink() and (e / "core").is_symlink()
    assert (e / "config" / "a.cfg").read_text() == "cfg" and not (e / "config").is_symlink()
    assert (e / ".source2").is_dir() and not (e / ".source2").is_symlink()
    assert game.sbox_engine_dir() == e                     # made once


def test_sbox_engine_dir_says_what_is_missing(project, tmp_path, monkeypatch):
    monkeypatch.setattr(game, "SBOX", tmp_path / "nothing")
    with pytest.raises(AssertionError, match=r"\[games\] sbox"):
        game.sbox_engine_dir()


def test_sbox_eval_builds_runs_and_reads_results(project, tmp_path, monkeypatch, bindir):
    monkeypatch.setattr(game, "SBOX", fake_sbox(tmp_path))
    monkeypatch.setenv("FAKE_LOG", str(tmp_path / "dotnet.log"))
    bindir("dotnet", FAKE_DOTNET)
    project.FILES = {"Code/Health.cs": "public class Health {}\n"}
    v = game.sbox_eval({"a b": "result = 1;", "second": "result = 2;"})
    assert v == {"a b": 42, "second": 42}
    calls = [json.loads(ln) for ln in (tmp_path / "dotnet.log").read_text().splitlines()]
    assert [c["args"][0] for c in calls] == ["build", "test"]
    assert "-nodeReuse:false" in calls[0]["args"] and "--no-build" in calls[1]["args"]
    env = calls[1]["env"]
    assert env["SDL_VIDEODRIVER"] == "offscreen" and env["FACEPUNCH_ENGINE"].endswith("/engine")
    assert env["LD_LIBRARY_PATH"].startswith(str(tmp_path / "sbox" / "bin" / "linuxsteamrt64"))
    d = Path(calls[0]["cwd"])
    src = (d / "ForgeEval.cs").read_text()
    assert "var scene = new Scene();" in src and "public void Eval_a_b()" in src
    usings = (d / "ForgeGlobalUsings.cs").read_text()
    assert "global using Sandbox;" in usings and "GlobalGameNamespace" not in usings
    assert (d / "ForgeEngine.cs").exists()                 # engine started for the tests
    assert str(game.project_dir() / "Code/**/*.cs") in (d / "ForgeTests.csproj").read_text()


def test_sbox_eval_single_snippet_engine_off(project, tmp_path, monkeypatch, bindir):
    monkeypatch.setattr(game, "SBOX", fake_sbox(tmp_path))
    monkeypatch.setenv("FAKE_LOG", str(tmp_path / "dotnet.log"))
    bindir("dotnet", FAKE_DOTNET)
    assert game.sbox_eval("result = 1;", engine=False) == 42
    d = Path(json.loads((tmp_path / "dotnet.log").read_text().splitlines()[0])["cwd"])
    assert "new Scene()" not in (d / "ForgeEval.cs").read_text()
    assert not (d / "ForgeEngine.cs").exists()


def test_sbox_eval_reports_compile_errors_and_exceptions(project, tmp_path, monkeypatch, bindir):
    monkeypatch.setattr(game, "SBOX", fake_sbox(tmp_path))
    monkeypatch.setenv("FAKE_LOG", str(tmp_path / "dotnet.log"))
    bindir("dotnet", FAKE_DOTNET)
    with pytest.raises(AssertionError, match=r"did not compile:\n.*CS0103") as exc:
        game.sbox_eval("result = NoSuchThing.Value;")
    assert str(exc.value).count("CS0103") == 1
    with pytest.raises(AssertionError, match="'boom' raised:\nSystem.Exception: boom"):
        game.sbox_eval({"boom": "throw new System.Exception();"})


def test_sbox_test_runs_the_projects_unit_tests(project, tmp_path, monkeypatch, bindir):
    monkeypatch.setattr(game, "SBOX", fake_sbox(tmp_path))
    monkeypatch.setenv("FAKE_LOG", str(tmp_path / "dotnet.log"))
    bindir("dotnet", FAKE_DOTNET)
    project.FILES = {"Code/S.cs": "class S {}\n",
                     "UnitTests/T.cs": "[TestClass] class T { [TestMethod] public void Good() {} "
                                       "[TestMethod] public void Bad() {} }\n"}
    r = game.sbox_test()
    assert [c["name"] for c in r.checks] == ["Good", "Bad"]
    assert not r.ok and r.failed[0]["name"] == "Bad"


# ------------------------------------------------------------ Garry's Mod

SRCDS = r'''
import json, os, sys, time
from pathlib import Path
g = Path.cwd() / "garrysmod"
assert (g / "addons" / "forge_project" / "addon.json").exists()
harness = (g / "addons" / "forge_test" / "lua" / "autorun" / "server" / "forge_test.lua").read_text()
assert "FORGE.check" in harness and "CompileString(code" in harness
Path(os.environ["FAKE_LOG"]).write_text(json.dumps({"argv": sys.argv[1:], "harness": harness}))
print("[ERROR] lua/autorun/server/x.lua:3: attempt to index a nil value")
print("  1. unknown - lua/autorun/server/x.lua:3")
print("hello")
sys.stdout.flush()
(g / "data" / "forge_test.txt").write_text(json.dumps(
    {"checks": {"1": {"name": "add", "ok": True}, "2": {"name": "spawned", "ok": True}},
     "values": {"map": "gm_construct"}, "map": "gm_construct"}))
time.sleep(600)
'''


def fake_gmod(tmp_path, srcds_body):
    s = tmp_path / "gmod-server"
    g = s / "garrysmod"
    for d in ("cfg", "lua/includes", "maps", "data", "cache", "addons/old"):
        (g / d).mkdir(parents=True)
    (g / "cfg" / "server.cfg").write_text("hostname x")
    (g / "sv.db").write_text("db")
    with open(g / "garrysmod_dir.vpk", "wb") as fh:
        fh.truncate(game.GMOD_COPY_MAX + 1)
    (s / "bin").mkdir()
    run = s / "srcds_run"
    run.write_text(f"#!{sys.executable}\n" + srcds_body)
    run.chmod(0o755)
    return s


def test_gmod_server_dir_mirrors_the_server_writably(project, tmp_path, monkeypatch):
    monkeypatch.setattr(game, "GMOD", fake_gmod(tmp_path, ""))
    d = game.gmod_server_dir()
    g = d / "garrysmod"
    assert (d / "bin").is_symlink() and not (d / "srcds_run").is_symlink()
    assert os.access(d / "srcds_run", os.X_OK)
    assert (g / "garrysmod_dir.vpk").is_symlink()          # big: linked
    assert not (g / "sv.db").is_symlink()                   # small: a writable copy
    assert (g / "lua").is_symlink()
    assert (g / "cfg" / "server.cfg").read_text() == "hostname x" and not (g / "cfg").is_symlink()
    for w in ("data", "cache", "addons", "download", "settings"):
        assert (g / w).is_dir() and not (g / w).is_symlink()
    assert not (g / "addons" / "old").exists()              # the server's addons stay out


def test_gmod_run_returns_checks_errors_and_kills_the_server(project, tmp_path, monkeypatch):
    monkeypatch.setattr(game, "GMOD", fake_gmod(tmp_path, SRCDS))
    monkeypatch.setenv("FAKE_LOG", str(tmp_path / "srcds.json"))
    project.FILES = {"addon.json": "{}", "lua/autorun/server/x.lua": "X = 1\n"}
    import time as _t
    t0 = _t.monotonic()
    r = game.gmod_run("FORGE.check('add', 1 + 1 == 2) -- ]] ]=] tricky", timeout=60)
    assert _t.monotonic() - t0 < 30                         # killed, not waited out
    assert [c["name"] for c in r.checks] == ["add", "spawned"]
    assert r.values == {"map": "gm_construct"}
    assert r.errors == ["lua/autorun/server/x.lua:3: attempt to index a nil value\n"
                        "1. unknown - lua/autorun/server/x.lua:3"]
    assert not r.ok
    seen = json.loads((tmp_path / "srcds.json").read_text())
    assert seen["argv"][:2] == ["-game", "garrysmod"] and "+map" in seen["argv"]
    assert "-- ]] ]=] tricky" in seen["harness"]            # the long string survived


def test_gmod_run_without_a_result_says_why(project, tmp_path, monkeypatch):
    monkeypatch.setattr(game, "GMOD", fake_gmod(tmp_path, "print('[ERROR] lua/a.lua:1: bad')\n"))
    project.FILES = {"addon.json": "{}"}
    r = game.gmod_run("x", timeout=20)
    assert not r.ok and r.checks == []
    assert r.errors[0] == "lua/a.lua:1: bad"
    assert "wrote nothing" in r.errors[-1]


def test_gmod_errors_with_and_without_the_error_tag():
    out = ("[ERROR] forge_test:12: attempt to index local 't' (a nil value)\n"
           "  1. unknown - forge_test:12\n"
           "[forge_project] lua/autorun/server/x.lua:3: attempt to call a nil value\n"
           "lua/entities/e/init.lua:9: bad argument #1\n"
           "Loading map gm_construct: 12.5 s\n"
           "[S_API] SteamAPI_Init(): Loaded local 'steamclient.so' OK.\n")
    assert game.gmod_errors(out) == [
        "forge_test:12: attempt to index local 't' (a nil value)\n1. unknown - forge_test:12",
        "lua/autorun/server/x.lua:3: attempt to call a nil value",
        "lua/entities/e/init.lua:9: bad argument #1"]


def test_lua_long_string_never_closes_early():
    for text in ("plain", "a]]b", "a]=]b]]c", "]==]"):
        s = game._lua_long_string(text)
        eq = s[1:s.index("[", 1)]
        assert s.startswith(f"[{eq}[\n") and s.endswith(f"]{eq}]")
        assert f"]{eq}]" not in s[len(eq) + 3:-len(eq) - 2]


def test_lint_output_parsing():
    out = ("lua/a.lua: [Error] line 2, column 1 - line 2, column 7: Unexpected 'return'\n"
           "lua/b.lua: [Warning] line 1, column 1 - line 1, column 2: Trailing whitespace\n")
    assert game.lint_errors(out) == ["lua/a.lua: [Error] line 2, column 1 - line 2, column 7: "
                                     "Unexpected 'return'"]
    assert len(game.lint_warnings(out)) == 1


# --------------------------------------------------------------- Fallout 4

def sub(t, data):
    return t.encode() + struct.pack("<H", len(data)) + data


def rec(t, flags, formid, data):
    return t.encode() + struct.pack("<III", len(data), flags, formid) + b"\0" * 8 + data


def grup(label, contents):
    return b"GRUP" + struct.pack("<I", 24 + len(contents)) + label.encode() + \
        struct.pack("<i", 0) + b"\0" * 8 + contents


def make_esp(path, localized=False, light=False):
    hdr = (sub("HEDR", struct.pack("<fiI", 1.0, 4, 0x801)) + sub("CNAM", b"forge\0")
           + sub("MAST", b"Fallout4.esm\0") + sub("DATA", b"\0" * 8))
    flags = (0x80 if localized else 0) | (0x200 if light else 0)
    tag = rec("MISC", 0, 0x01000800, sub("EDID", b"ForgeTag\0")
              + sub("FULL", (struct.pack("<I", 5) if localized else b"Forge tag\0"))
              + sub("DATA", struct.pack("<if", 7, 0.5)))
    over = rec("MISC", 0, 0x0001F2A3, sub("EDID", b"shipment_Copper\0")
               + sub("DATA", struct.pack("<if", 99, 1.0)))
    raw = sub("EDID", b"ForgeQuest\0") + sub("FULL", b"Quest\0")
    quest = rec("QUST", 0x00040000, 0x01000801, struct.pack("<I", len(raw)) + zlib.compress(raw))
    big = b"\x07" * 70000
    huge = rec("STAT", 0, 0x01000802, sub("EDID", b"Big\0") + sub("XXXX", struct.pack("<I", len(big)))
               + b"MODT" + struct.pack("<H", 0) + big + sub("OBND", b"\0" * 12))
    path.write_bytes(rec("TES4", flags, 0, hdr) + grup("MISC", tag + over) + grup("QUST", quest)
                     + grup("STAT", huge))
    return path


def test_esp_reader(tmp_path):
    esp = make_esp(tmp_path / "Forge.esp")
    d = game.esp_read(esp)
    h = d["header"]
    assert h["masters"] == ["Fallout4.esm"] and h["author"] == "forge" and h["version"] == 1.0
    assert h["num_records"] == 4 and not h["master"] and not h["localized"] and not h["light"]
    tag = game.esp_record(esp, "forgetag")
    assert tag["type"] == "MISC" and tag["file"] == "Forge.esp" and not tag["override"]
    assert tag["full"] == "Forge tag" and tag["local_id"] == 0x800 and tag["group"] == "MISC"
    assert struct.unpack_from("<if", tag["subs"]["DATA"][0]) == (7, 0.5)
    over = [r for r in game.esp_records(esp, "MISC") if r["override"]]
    assert len(over) == 1 and over[0]["file"] == "Fallout4.esm" and over[0]["formid_hex"] == "0001F2A3"
    q = game.esp_record(esp, "ForgeQuest")                 # a compressed record
    assert q["type"] == "QUST" and q["full"] == "Quest"
    st = game.esp_record(esp, "Big")                       # XXXX: a subrecord over 64 KB
    assert len(st["subs"]["MODT"][0]) == 70000 and st["subs"]["OBND"][0] == b"\0" * 12
    assert [r["type"] for r in game.esp_records(esp)] == ["MISC", "MISC", "QUST", "STAT"]


def test_esp_reader_flags_and_localized_names(tmp_path):
    esp = make_esp(tmp_path / "L.esl", localized=True, light=True)
    h = game.esp_header(esp)
    assert h["localized"] and h["light"]
    assert game.esp_record(esp, "ForgeTag")["full"] is None
    with pytest.raises(AssertionError, match="not a Bethesda plugin"):
        (tmp_path / "x.esp").write_bytes(b"nope")
        game.esp_read(tmp_path / "x.esp")


def make_pex(path, source="ForgeSelftest.psc"):
    def ws(s):
        return struct.pack("<H", len(s)) + s.encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(struct.pack("<IBBHQ", 0xFA57C0DE, 3, 9, 2, 1793227259)
                     + ws(source) + ws("somebody") + ws("box"))


def test_pex_header(tmp_path):
    make_pex(tmp_path / "a.pex", "/work/src/A.psc")
    h = game.pex_header(tmp_path / "a.pex")
    assert h["ok"] and (h["major"], h["minor"], h["game"]) == (3, 9, 2)
    assert h["source"] == "/work/src/A.psc" and h["user"] == "somebody" and h["machine"] == "box"


def test_papyrus_errors():
    out = ("Compiling \"Foo.psc\"...\n"
           "/tmp/project/Scripts/Source/User/Foo.psc(4,11): variable undefinedThing is undefined\n"
           "/tmp/project/Scripts/Source/User/Foo.psc(4,4): type mismatch on return\n"
           "No output generated for Foo.psc, compilation failed.\n")
    errs = game.papyrus_errors(out)
    assert errs[0] == {"file": "/tmp/project/Scripts/Source/User/Foo.psc", "line": 4, "col": 11,
                       "message": "variable undefinedThing is undefined"}
    assert len(errs) == 2


# A fake Mono that does what PapyrusCompiler.exe does: check the file, write
# the assembly, run PapyrusAssembler.exe from its own folder in the output
# folder, as `PapyrusAssembler <name> /Q`.
FAKE_MONO = r'''
import json, os, subprocess, sys
from pathlib import Path
args = sys.argv[1:]
comp, src = Path(args[0]), args[1]
opts = dict(a.split("=", 1) for a in args[2:] if "=" in a)
log = Path(os.environ["FAKE_LOG"])
with open(log, "a") as fh:
    fh.write(json.dumps({"args": args, "cwd": os.getcwd()}) + "\n")
text = Path(src).read_text()
if "undefinedThing" in text:
    print(f"{Path.cwd() / src}(4,12): variable undefinedThing is undefined")
    print(f"No output generated for {src}, compilation failed.")
    sys.exit(255)
assert os.path.basename(opts["-f"]) == "Institute_Papyrus_Flags.flg"
assert any(Path(i, opts["-f"]).exists() for i in opts["-i"].split(";")), opts["-i"]
rel = Path(src)
out = Path(opts["-o"]) / rel.parent
out.mkdir(parents=True, exist_ok=True)
(out / (rel.stem + ".pas")).write_text(".info\n")
name = ":".join(list(rel.parent.parts) + [rel.stem])
r = subprocess.run([str(comp.parent / "PapyrusAssembler.exe"), name, "/Q"], cwd=out)
sys.exit(0 if r.returncode == 0 else 255)
'''

FAKE_WINE = r'''
import os, struct, sys
from pathlib import Path
real, name = sys.argv[1], sys.argv[2]
assert real.endswith("PapyrusAssembler.exe") and "papyrus-compiler" not in real, real
prefix = Path(os.environ["WINEPREFIX"])
assert (prefix / "system.reg").is_file() and not (prefix / "system.reg").is_symlink()
stem = name.split(":")[-1]
assert Path(stem + ".pas").exists()
def ws(s): return struct.pack("<H", len(s)) + s.encode()
Path(stem + ".pex").write_bytes(struct.pack("<IBBHQ", 0xFA57C0DE, 3, 9, 2, 0) + ws(stem) + ws("u") + ws("m"))
'''


def fake_fo4(tmp_path, monkeypatch):
    fo4 = tmp_path / "Fallout 4"
    pc = fo4 / "Papyrus Compiler"
    pc.mkdir(parents=True)
    for n in ("PapyrusCompiler.exe", "PapyrusAssembler.exe", "PCompiler.dll"):
        (pc / n).write_text(n)
    (fo4 / "Data").mkdir()
    (fo4 / "Data" / "Fallout4.esm").write_text("")
    (fo4 / "Data" / "DLCRobot.esm").write_text("")
    scripts = tmp_path / "fo4-scripts"
    (scripts / "Base").mkdir(parents=True)
    (scripts / "Base" / "Institute_Papyrus_Flags.flg").write_text("flags")
    (scripts / "Base" / "Quest.psc").write_text("ScriptName Quest")
    (scripts / "DLC01").mkdir()
    (scripts / "DLC01" / "DLC01Script.psc").write_text("ScriptName DLC01Script")
    (scripts / "Empty").mkdir()
    wp = tmp_path / "wineprefix"
    (wp / "drive_c" / "windows").mkdir(parents=True)
    for n in ("system.reg", "user.reg", "userdef.reg", ".update-timestamp"):
        (wp / n).write_text(n)
    (wp / "dosdevices").mkdir()
    os.symlink("../drive_c", wp / "dosdevices" / "c:")
    monkeypatch.setattr(game, "FO4", fo4)
    monkeypatch.setattr(game, "FO4_SCRIPTS", scripts)
    monkeypatch.setattr(game, "WINE_TEMPLATE", wp)
    return fo4


FO4_FILES = {"Scripts/Source/User/ForgeSelftest.psc": "ScriptName ForgeSelftest extends Quest\n",
             "Scripts/Source/User/ForgeSelftest/Util.psc": "ScriptName ForgeSelftest:Util\n",
             "Plugin/Program.cs": "class P {}\n", "Plugin/Old.csproj": "<Project/>",
             "Interface/x.swf": "swf"}


def test_papyrus_compiles_through_the_assembler_shim(project, tmp_path, monkeypatch, bindir):
    fake_fo4(tmp_path, monkeypatch)
    monkeypatch.setenv("FAKE_LOG", str(tmp_path / "mono.log"))
    bindir("mono", FAKE_MONO)
    bindir("wine", FAKE_WINE)
    project.FILES = dict(FO4_FILES)
    r = game.papyrus()
    assert r.ok, r.out
    names = sorted(str(p.relative_to(game.project_dir() / "Scripts")) for p in r.pex)
    assert names == ["ForgeSelftest.pex", "ForgeSelftest/Util.pex"]
    assert all(game.pex_header(p)["ok"] for p in r.pex)
    calls = [json.loads(ln) for ln in (tmp_path / "mono.log").read_text().splitlines()]
    assert sorted(c["args"][1] for c in calls) == ["ForgeSelftest.psc", "ForgeSelftest/Util.psc"]
    imports = dict(a.split("=", 1) for a in calls[0]["args"][2:] if "=" in a)["-i"].split(";")
    assert imports[0] == str(game.project_dir() / "Scripts/Source/User")
    assert imports[1:] == [str(tmp_path / "fo4-scripts" / "Base"), str(tmp_path / "fo4-scripts" / "DLC01")]
    shim = game.TMP / "papyrus-compiler" / "PapyrusAssembler.exe"
    assert "exec wine" in shim.read_text() and "'" in shim.read_text()   # the spaced path is quoted


def test_papyrus_reports_errors_with_lines(project, tmp_path, monkeypatch, bindir):
    fake_fo4(tmp_path, monkeypatch)
    monkeypatch.setenv("FAKE_LOG", str(tmp_path / "mono.log"))
    bindir("mono", FAKE_MONO)
    bindir("wine", FAKE_WINE)
    project.FILES = {"Scripts/Source/User/Bad.psc": "Return undefinedThing\n"}
    r = game.papyrus(release=True)
    assert not r.ok and r.errors[0]["line"] == 4 and "undefinedThing" in r.errors[0]["message"]
    assert "-release" in json.loads((tmp_path / "mono.log").read_text().splitlines()[0])["args"]


def test_papyrus_needs_the_creation_kit(project, tmp_path, monkeypatch):
    fake_fo4(tmp_path, monkeypatch)
    (game.FO4 / "Papyrus Compiler" / "PapyrusCompiler.exe").unlink()
    project.FILES = dict(FO4_FILES)
    with pytest.raises(AssertionError, match="install the Creation Kit"):
        game.papyrus()


def test_esp_build_writes_the_csproj_and_load_order(project, tmp_path, monkeypatch, bindir):
    fake_fo4(tmp_path, monkeypatch)
    monkeypatch.setenv("FAKE_LOG", str(tmp_path / "dotnet.log"))
    bindir("dotnet", FAKE_DOTNET)
    project.FILES = dict(FO4_FILES)
    esp = game.esp_build()
    assert esp.name == "Made.esp"
    plugin = game.project_dir() / "Plugin"
    assert sorted(p.name for p in plugin.glob("*.csproj")) == ["Plugin.csproj"]
    assert 'Include="Mutagen.Bethesda.Fallout4" Version="0.54.4"' in (plugin / "Plugin.csproj").read_text()
    calls = [json.loads(ln) for ln in (tmp_path / "dotnet.log").read_text().splitlines()]
    assert [c["args"][0] for c in calls] == ["build", "run"]
    assert calls[1]["args"][-1] == str(game.FO4 / "Data")
    lad = Path(calls[1]["env"]["LocalAppData"])
    for name in ("plugins.txt", "Plugins.txt"):
        assert (lad / "Fallout4" / name).read_text().splitlines() == ["# written by forge", "*DLCRobot.esm"]


def test_package_fo4_makes_a_data_folder(project, tmp_path, monkeypatch, bindir):
    fake_fo4(tmp_path, monkeypatch)
    monkeypatch.setenv("FAKE_LOG", str(tmp_path / "log"))
    bindir("mono", FAKE_MONO)
    bindir("wine", FAKE_WINE)
    bindir("dotnet", FAKE_DOTNET)
    project.FILES = dict(FO4_FILES)
    out = tmp_path / "pkg"
    made = game.package("fo4", out)
    assert sorted(made[:2]) == ["Scripts/ForgeSelftest.pex", "Scripts/ForgeSelftest/Util.pex"]
    assert made[2:] == ["Made.esp", "Interface/"]
    assert (out / "Interface" / "x.swf").read_text() == "swf"
    mono = [json.loads(ln) for ln in (tmp_path / "log").read_text().splitlines() if '"cwd"' in ln
            and "PapyrusCompiler" in ln]
    assert all("-release" in c["args"] and "-optimize" in c["args"] for c in mono)


# ----------------------------------------------------------------- Blender

FAKE_BLENDER = r'''
import json, os, sys
from pathlib import Path
args = sys.argv[1:]
Path(os.environ["FAKE_LOG"]).write_text(json.dumps(args))
script = Path(args[args.index("--python") + 1]).read_text()
rest = args[args.index("--") + 1:]
if "__forge_eval" in script:
    if "raise_please" in script:
        print("Traceback: boom"); sys.exit(1)
    print("FORGE_RESULT:8")
elif "non_manifold_edges" in script:
    print("FORGE_RESULT:" + json.dumps({"vertices": 8, "faces": 6, "watertight": True,
                                        "dimensions": [2, 2, 2], "path": rest[0]}))
elif "forge_cam" in script:
    out = Path(rest[1])
    done = []
    for name, _ in json.loads(rest[5]):
        p = out / (Path(rest[0]).stem + "_" + name + ".png")
        p.write_bytes(b"\x89PNG\r\n\x1a\n")
        done.append(str(p))
    print("FORGE_RESULT:" + json.dumps(done))
else:
    Path(rest[rest.index("--out") + 1], "model.glb").write_bytes(b"glTF")
'''


def test_blender_eval_command_line(project, tmp_path, monkeypatch, bindir):
    monkeypatch.setenv("FAKE_LOG", str(tmp_path / "blender.json"))
    bindir("blender", FAKE_BLENDER)
    assert game.blender_eval("result = 8") == 8
    args = json.loads((tmp_path / "blender.json").read_text())
    assert args[:3] == ["-b", "--factory-startup", "-noaudio"]
    assert args[args.index("--python-exit-code") + 1] == "1"
    with pytest.raises(AssertionError, match="blender_eval failed"):
        game.blender_eval("raise_please()")


def test_blender_opens_a_blend_first(project, tmp_path, monkeypatch, bindir):
    monkeypatch.setenv("FAKE_LOG", str(tmp_path / "blender.json"))
    bindir("blender", FAKE_BLENDER)
    game.blender_eval("result = 1", blend="scene.blend")
    args = json.loads((tmp_path / "blender.json").read_text())
    assert args.index("scene.blend") < args.index("--python")


def test_mesh_info_render_and_build(project, tmp_path, monkeypatch, bindir):
    monkeypatch.setenv("FAKE_LOG", str(tmp_path / "blender.json"))
    bindir("blender", FAKE_BLENDER)
    project.FILES = {"build.py": "print('build')\n"}
    info = game.mesh_info("assets/models/barrel.glb")
    assert info["watertight"] and info["path"] == "assets/models/barrel.glb"
    shots = game.blender_render("m/barrel.glb", views=("front", ("low", (1, -1, 0.1))))
    assert [p.name for p in shots] == ["barrel_front.png", "barrel_low.png"]
    args = json.loads((tmp_path / "blender.json").read_text())
    rest = args[args.index("--") + 1:]
    assert json.loads(rest[5]) == [["front", [0.0, -1.0, 0.0]], ["low", [1, -1, 0.1]]]
    out = game.blender_build()
    assert (out / "model.glb").read_bytes() == b"glTF"
    args = json.loads((tmp_path / "blender.json").read_text())
    assert args[args.index("--python") + 1] == str(game.project_dir() / "build.py")
    assert game.package("blender", tmp_path / "pkg") == ["model.glb"]


def test_blender_scripts_are_valid_python():
    for src in (game._MESH_INFO, game._RENDER, game._EVAL_WRAP % "    result = 1"):
        ast.parse(src)


# ------------------------------------------------------- image-build CLI

def test_cli_writes_the_templates_the_images_restore(tmp_path):
    assert game._cli(["sbox-template", str(tmp_path / "s")]) == 0
    proj = (tmp_path / "s" / "ForgeTests.csproj").read_text()
    assert proj == game.sbox_csproj("ForgeTests", [], None)
    assert game._cli(["fo4-template", str(tmp_path / "f")]) == 0
    assert "Mutagen.Bethesda.Fallout4" in (tmp_path / "f" / "Plugin.csproj").read_text()
    assert "Fallout4Release.Fallout4" in (tmp_path / "f" / "Program.cs").read_text()
    assert game._cli(["nope"]) == 2
