"""5 November: the general-purpose toolchains - shell, node, go,
homeassistant. forge_game's helpers run against fake programs on PATH
(shellcheck, bats, tsc, vitest, go, python's homeassistant), the
download-only dependency plans, the failure facts, and the toolchain
tables. The real tools are `forge selftest shell|node|go|homeassistant`."""
import json
import os
import sys
import types
from pathlib import Path

import pytest

import forge

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import forge_game as game  # noqa: E402

NEW = ("shell", "node", "go", "homeassistant")


@pytest.fixture
def project(tmp_path, monkeypatch):
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


# ------------------------------------------------------------------ tables

def test_every_new_toolchain_is_wired_everywhere():
    inst = (Path(forge.__file__).parent / "install-forge-toolchains.sh").read_text()
    for t in NEW:
        tc = forge.TOOLCHAINS[t]
        assert tc["lang"] and tc["fence"] and tc["test_help"] and tc["coder_help"]
        assert t in forge.TOOLCHAIN_LANG and forge.TOOLCHAIN_LANG[t] in forge.LANGS
        assert t in forge.KIWIX_TOOLCHAIN_BOOKS
        assert t in forge.SELFTEST_FILES and t in forge.SELFTEST_STEPS
        compile(forge.SELFTEST_SCRIPT % forge.SELFTEST_STEPS[t], t, "exec")
        assert f"\n{t})\n" in inst, t
    assert forge.LANG_ALIASES["golang"] == "go"
    assert set(forge.DEPS_TOOLCHAINS) == {"node", "go", "homeassistant", "python-app", "desktop", "ros2", "kotlin-jvm", "android"}
    assert "go" in forge.PACKAGED and "minecraft" in forge.PACKAGED


def test_test_help_names_only_real_helpers():
    import re
    for t in NEW:
        for name in set(re.findall(r"game\.(\w+)\(", forge.TOOLCHAINS[t]["test_help"])):
            assert callable(getattr(game, name, None)), (t, name)


# ------------------------------------------------------------------ shell

def test_shell_files_finds_scripts_by_suffix_and_shebang(project):
    project.FILES = {"a.sh": "echo a\n", "bin/tool": "#!/usr/bin/env bash\necho t\n",
                     "notes.txt": "#!/bin/bash in prose\n", "x.py": "#!/usr/bin/env python3\n",
                     "t/x.bats": "@test x { true; }\n", "s": "#!/bin/sh\n"}
    names = sorted(str(p.relative_to(game.project_dir())) for p in game.shell_files())
    assert names == ["a.sh", "bin/tool", "s"]


def test_shellcheck_issues_are_parsed(project, bindir):
    project.FILES = {"a.sh": "rm $1\n"}
    bindir("shellcheck", "import sys\nprint('a.sh:1:4: note: Double quote to prevent globbing "
                         "and word splitting. [SC2086]')\nprint('a.sh:2:1: error: oops')\nsys.exit(1)\n")
    r = game.shellcheck()
    assert not r.ok
    assert r.issues[0] == {"file": "a.sh", "line": 1, "col": 4, "level": "note",
                           "msg": "Double quote to prevent globbing and word splitting.",
                           "code": "SC2086"}
    assert r.issues[1]["code"] is None


def test_bats_tap_becomes_checks(project, bindir):
    project.FILES = {"tests/a.bats": "@test x { true; }\n"}
    bindir("bats", "import sys, os\nassert os.environ['BATS_LIB_PATH']\nprint('1..3')\n"
                   "print('ok 1 adds numbers')\nprint('not ok 2 rejects junk')\n"
                   "print('# (in test file tests/a.bats, line 7)')\nprint('#   `assert_failure` failed')\n"
                   "print('ok 3 later # skip not yet')\nsys.exit(1)\n")
    r = game.bats()
    assert [c["name"] for c in r.checks] == ["adds numbers", "rejects junk"]
    assert r.checks[0]["ok"] and not r.checks[1]["ok"]
    assert "assert_failure" in r.checks[1]["detail"]
    with pytest.raises(AssertionError, match="FAIL rejects junk"):
        r.assert_ok()


def test_sh_run_stubs_record_calls_and_fake_output(project):
    project.FILES = {"install.sh": "set -e\nv=$(docker version)\necho \"got $v\"\n"
                                   "systemctl daemon-reload\nsystemctl enable --now 'a b.service'\n"
                                   "apt-get install x || echo apt failed\n"}
    r = game.sh_run(["bash", "install.sh"],
                    stubs={"docker": {"stdout": "27.1.1"}, "systemctl": "",
                           "apt-get": {"exit": 100, "stderr": "E: no network"}})
    assert r.ok, r.out
    assert "got 27.1.1" in r.out and "apt failed" in r.out and "E: no network" in r.out
    assert r.calls_to("systemctl") == [["daemon-reload"], ["enable", "--now", "a b.service"]]
    assert r.calls_to("apt-get") == [["install", "x"]]
    assert [c["cmd"] for c in r.calls] == ["docker", "systemctl", "systemctl", "apt-get"]


def test_systemd_verify_drops_sandbox_noise(project, bindir):
    project.FILES = {"a.service": "[Service]\nExecStart=/opt/x\nRestartt=always\n"}
    bindir("systemd-analyze", "print('/p/a.service:3: Unknown key \\'Restartt\\' in section [Service], ignoring.')\n"
                              "print('a.service: Command /opt/x is not executable: No such file or directory')\n"
                              "print('Failed to connect to bus: No such file or directory')\n"
                              "raise SystemExit(1)\n")
    probs = game.systemd_verify()
    assert len(probs) == 1 and "Restartt" in probs[0]
    assert len(game.systemd_verify(allow_missing=False)) == 2


# ------------------------------------------------------------------ node

def test_node_modules_links_fetched_packages_over_the_image_tools(project, tmp_path, monkeypatch):
    tools, deps = tmp_path / "tools", tmp_path / "deps"
    for root, pkgs in ((tools / "node_modules", ["vitest", "typescript", "@types/node"]),
                       (deps / "node_modules", ["zod", "typescript", "@types/react"])):
        for p in pkgs:
            (root / p).mkdir(parents=True)
            (root / p / "package.json").write_text(json.dumps({"name": p, "root": str(root)}))
        (root / ".bin").mkdir()
    (tools / "node_modules" / ".bin" / "vitest").write_text("")
    monkeypatch.setattr(game, "NODE_TOOLS", tools)
    monkeypatch.setattr(game, "DEPS", deps)
    project.FILES = {"package.json": "{}"}
    nm = game.node_modules()
    linked = {p.name: p.resolve().parents[2 if p.parent.name.startswith("@") else 1].name
              for p in [*nm.iterdir(), *nm.glob("@*/*")] if not p.name.startswith((".", "@"))}
    assert linked == {"zod": "deps", "typescript": "deps", "vitest": "tools",
                      "node": "tools", "react": "deps"}
    assert (nm / ".bin" / "vitest").is_symlink()
    assert game.node_modules() == nm          # idempotent


def test_tsc_errors_and_vitest_report(project, bindir, tmp_path, monkeypatch):
    monkeypatch.setattr(game, "NODE_TOOLS", tmp_path / "none")
    monkeypatch.setattr(game, "DEPS", tmp_path / "none")
    project.FILES = {"tsconfig.json": "{}", "src/a.ts": "x"}
    bindir("tsc", "import sys\nassert '--pretty' in sys.argv and '-p' in sys.argv\n"
                  "print(\"src/a.ts(4,7): error TS2322: Type 'number' is not assignable to type 'string'.\")\n"
                  "sys.exit(2)\n")
    b = game.tsc()
    assert not b.ok and b.errors == ["src/a.ts(4,7): error TS2322: Type 'number' is not "
                                     "assignable to type 'string'."]
    proj = str(game.project_dir())
    report = {"testResults": [
        {"name": proj + "/tests/a.test.ts", "status": "failed", "message": "", "assertionResults": [
            {"ancestorTitles": ["score"], "title": "adds", "status": "passed", "failureMessages": []},
            {"ancestorTitles": ["score"], "title": "bad", "status": "failed",
             "failureMessages": ["AssertionError: expected 11 to be 12\n    at /tmp/p/tests/a.test.ts:5:54\n"
                                 "    at file:///opt/node-tools/node_modules/vitest/dist/x.js:1:1"]},
            {"ancestorTitles": [], "title": "later", "status": "skipped"}]},
        {"name": proj + "/tests/b.test.ts", "status": "failed",
         "message": "Failed to load url zod", "assertionResults": []}]}
    bindir("vitest", "import sys, json\nout = [a.split('=', 1)[1] for a in sys.argv if a.startswith('--outputFile=')][0]\n"
                     f"open(out, 'w').write(json.dumps({json.dumps(report)!r} and {report!r}))\nsys.exit(1)\n")
    r = game.vitest()
    assert [c["name"] for c in r.checks] == ["score > adds", "score > bad"]
    assert "a.test.ts:5:54" in r.checks[1]["detail"] and "node_modules" not in r.checks[1]["detail"]
    assert r.errors == ["tests/b.test.ts: Failed to load url zod"]


# ------------------------------------------------------------------ go

def test_go_test_json_becomes_checks(project, bindir, tmp_path, monkeypatch):
    deps = tmp_path / "deps"
    deps.mkdir()
    (deps / "go.mod").write_text("module example.com/x\n\ngo 1.27\n\nrequire a.b/c v1.0.0\n")
    (deps / "go.sum").write_text("a.b/c v1.0.0 h1:x\n")
    monkeypatch.setattr(game, "DEPS", deps)
    project.FILES = {"go.mod": "module example.com/x\n\ngo 1.27\n"}
    events = [
        {"Action": "run", "Package": "x/s", "Test": "TestA"},
        {"Action": "pass", "Package": "x/s", "Test": "TestA"},
        {"Action": "output", "Package": "x/s", "Test": "TestB/sub", "Output": "=== RUN   TestB/sub\n"},
        {"Action": "output", "Package": "x/s", "Test": "TestB/sub", "Output": "    s_test.go:9: want 12, got 11\n"},
        {"Action": "fail", "Package": "x/s", "Test": "TestB/sub"},
        {"Action": "fail", "Package": "x/s", "Test": "TestB"},
        {"Action": "fail", "Package": "x/s"},
        {"Action": "build-output", "ImportPath": "x/t", "Output": "t/t.go:3:2: undefined: Foo\n"},
        {"Action": "output", "Package": "x/t", "Output": "FAIL\tx/t [build failed]\n"},
        {"Action": "fail", "Package": "x/t"},
    ]
    bindir("go", "import sys, os, json\n"
                 "assert os.environ['GOPROXY'] == 'off' and os.environ['GOFLAGS'] == '-mod=mod'\n"
                 f"assert sys.argv[1:4] == ['test', '-json', '-count=1']\n"
                 f"for e in {events!r}: print(json.dumps(e))\nsys.exit(1)\n")
    r = game.go_test()
    assert [(c["name"], c["ok"]) for c in r.checks] == [("TestA", True), ("TestB/sub", False),
                                                        ("TestB", False)]
    assert r.checks[1]["detail"] == "s_test.go:9: want 12, got 11"
    assert "t/t.go:3:2: undefined: Foo" in r.errors
    assert any("[build failed]" in e for e in r.errors)
    # go_prepare put tidy's go.mod / go.sum into the project
    assert "require a.b/c" in (game.project_dir() / "go.mod").read_text()


def test_go_build_errors(project, bindir, tmp_path, monkeypatch):
    monkeypatch.setattr(game, "DEPS", tmp_path / "none")
    project.FILES = {"go.mod": "module x\n"}
    bindir("go", "import sys\nprint('# x/score')\nprint('score/bad.go:3:25: cannot use \"x\" as int value')\nsys.exit(1)\n")
    b = game.go_build()
    assert b.errors == ['score/bad.go:3:25: cannot use "x" as int value']


# ------------------------------------------------------------------ Home Assistant

def test_ha_check_config_report(project, monkeypatch):
    project.FILES = {"config/configuration.yaml": "homeassistant:\n",
                     "custom_components/demo/manifest.json": "{}"}
    seen = {}
    report = {"total_errors": 1, "errors": {"automation": ["bad trigger", {"x": 1}]},
              "warnings": {"input_boolean": ["invalid boolean value offf"]},
              "components": ["homeassistant", "automation"]}

    def fake_run(cmd, cwd=None, timeout=0, env=None, **kw):
        seen["cmd"], seen["cwd"] = cmd, Path(cwd)
        return game.Result(0, "Testing configuration\n" + json.dumps(report, indent=2) + "\n")
    monkeypatch.setattr(game, "run", fake_run)
    c = game.ha_check_config()
    assert seen["cmd"][1:5] == ["-m", "homeassistant", "--script", "check_config"]
    assert (seen["cwd"] / "custom_components" / "demo" / "manifest.json").exists()
    assert c.errors == ["automation: bad trigger"]
    assert c.warnings == ["input_boolean: invalid boolean value offf"]
    assert c.components == ["homeassistant", "automation"] and not c.ok


def test_esphome_problems(project, monkeypatch):
    project.FILES = {"esphome/porch.yaml": "esphome:\n"}
    out = ("INFO Reading configuration porch.yaml...\nFailed config\n\n"
           "binary_sensor.gpio: [source porch.yaml:15]\n  platform: gpio\n  \n"
           "  Invalid pin number: 99 (must be 0-39).\n  pin: GPIO99\n  name: Porch Motion\n")
    monkeypatch.setattr(game, "run", lambda *a, **k: game.Result(0, out))
    assert game.esphome_config("esphome/porch.yaml") == [
        "binary_sensor.gpio: Invalid pin number: 99 (must be 0-39)."]
    monkeypatch.setattr(game, "run", lambda *a, **k: game.Result(0, "INFO Configuration is valid!\n"))
    assert game.esphome_config("esphome/porch.yaml") == []


def test_ha_test_imports_the_projects_custom_components_first(project, monkeypatch, tmp_path):
    monkeypatch.setattr(game, "DEPS", tmp_path / "none")
    project.FILES = {"custom_components/demo/manifest.json": '{"requirements": []}',
                     "tests/test_x.py": ""}
    seen = {}

    def fake_run(cmd, cwd=None, timeout=0, env=None, **kw):
        seen["cmd"], seen["env"] = cmd, env
        xml = [a.split("=", 1)[1] for a in cmd if a.startswith("--junitxml=")][0]
        Path(xml).write_text('<testsuites><testsuite><testcase classname="tests.test_x" name="test_a"/>'
                             '</testsuite></testsuites>')
        return game.Result(0, "1 passed")
    monkeypatch.setattr(game, "run", fake_run)
    r = game.ha_test()
    assert r.ok and r.checks[0]["name"] == "tests.test_x.test_a"
    assert "import custom_components" in seen["cmd"][2]
    assert (game.project_dir() / "custom_components" / "__init__.py").exists()


# ------------------------------------------------------------------ dependency plans

def test_deps_plans():
    tools_only = forge._deps_plan("node", {"package.json": json.dumps(
        {"devDependencies": {"vitest": "*", "typescript": "*"}})})
    assert tools_only is None
    p = forge._deps_plan("node", {"package.json": json.dumps({"dependencies": {"zod": "^4"}}),
                                  "src/a.ts": "secret code"})
    assert p and "--ignore-scripts" in p["script"] and set(p["files"]) == {"package.json"}
    assert "--offline" in p["offline"](p["script"])

    assert forge._deps_plan("go", {"go.mod": "module x\n\ngo 1.27\n",
                                   "a.go": 'package a\n\nimport (\n\t"fmt"\n\t"net/http"\n)\n'}) is None
    g = forge._deps_plan("go", {"go.mod": "module x\n\ngo 1.27\n",
                                "a_test.go": 'package a\n\nimport (\n\t"testing"\n\n\tcmp "github.com/google/go-cmp/cmp"\n)\n'})
    assert g and "go mod tidy" in g["script"] and g["env"]["GOMODCACHE"] == "/deps/gomod"
    g2 = forge._deps_plan("go", {"go.mod": "module x\n", "b.go": 'package b\nimport "golang.org/x/text"\n'})
    assert g2 and g2["key"] != g["key"]

    assert forge._deps_plan("homeassistant", {"custom_components/d/manifest.json":
                                              '{"requirements": []}'}) is None
    h = forge._deps_plan("homeassistant", {"custom_components/d/manifest.json":
                                           '{"requirements": ["pyhumps==3.8.0"]}'})
    assert "--only-binary :all:" in h["script"] and "pyhumps==3.8.0" in h["script"]
    assert forge._deps_plan("python", {"package.json": "{}"}) is None


def test_failure_facts_for_missing_packages(monkeypatch):
    monkeypatch.setattr(forge, "TOOLCHAIN", "node")
    f = forge.harness_facts("Error: Cannot find package 'zod' imported from /tmp/project/src/a.ts")
    assert len(f) == 1 and "'zod'" in f[0] and "package.json" in f[0]
    assert forge.harness_facts("Cannot find module 'node:fs'") == []
    monkeypatch.setattr(forge, "TOOLCHAIN", "go")
    f = forge.harness_facts("a_test.go:6:2: cannot find module providing package github.com/x/y: "
                            "module lookup disabled by GOPROXY=off")
    assert len(f) == 1 and "github.com/x/y" in f[0]
    monkeypatch.setattr(forge, "TOOLCHAIN", "homeassistant")
    assert "manifest.json" in forge.harness_facts("Setup failed for 'demo': Integration not found.")[0]
