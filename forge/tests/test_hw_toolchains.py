"""5 November (evening): c-cpp, sql, mcu, devices and raspberrypi - the
helpers' parsing against fake programs, the fake adb server against a
client written to the adb protocol, the MicroPython fakes under CPython,
UF2 parsing, disk images (when mtools/sfdisk exist), Ziggurat mirrors.
The real tools run in `forge selftest <toolchain>`."""
import json
import os
import shutil
import socket
import struct
import sys
import types
from pathlib import Path

import pytest

import forge

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
import forge_game as game  # noqa: E402

NEW = ("c-cpp", "sql", "mcu", "esp32", "devices", "raspberrypi")


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


def test_new_toolchains_are_wired_everywhere():
    inst = (HERE / "install-forge-toolchains.sh").read_text()
    for t in NEW:
        tc = forge.TOOLCHAINS[t]
        assert tc["lang"] and tc["fence"] and tc["test_help"] and tc["coder_help"]
        assert forge.TOOLCHAIN_LANG[t] in forge.LANGS
        assert t in forge.KIWIX_TOOLCHAIN_BOOKS and t in forge.SELFTEST_FILES
        compile(forge.SELFTEST_SCRIPT % forge.SELFTEST_STEPS[t], t, "exec")
        assert f"\n{t})\n" in inst and f"        {t}) cmd=" in inst, t


def test_test_help_names_only_real_helpers():
    import re
    for t in NEW:
        for name in set(re.findall(r"game\.(\w+)\(", forge.TOOLCHAINS[t]["test_help"])):
            assert callable(getattr(game, name, None)), (t, name)


def test_kiwix_books_are_in_the_library_script():
    import importlib.util
    spec = importlib.util.spec_from_file_location("kfl", HERE / "kiwix-forge-library.py")
    k = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(k)
    groups = {b for g in k.GROUPS.values() for b in g}
    need = {b for v in forge.KIWIX_TOOLCHAIN_BOOKS.values() for b in v}
    assert need <= groups, need - groups


# ------------------------------------------------------------------ C / C++

def test_cbuild_errors_and_warnings():
    out = ("/tmp/project/src/a.cpp:5:41: error: 'hitz' was not declared in this scope\n"
           "src/b.c:3:1: warning: unused variable 'x' [-Wunused-variable]\n"
           "/usr/bin/ld: a.o: in function `main':\nmain.cpp:(.text+0x1): undefined reference to `score(int)'\n")
    b = game.CBuild(1, out, Path("/tmp/b"))
    assert any("hitz" in e for e in b.errors)
    assert "undefined reference to `score(int)'" in b.errors
    assert b.warnings == ["src/b.c:3:1: warning: unused variable 'x' [-Wunused-variable]"]
    with pytest.raises(AssertionError, match="hitz"):
        b.assert_ok()


def test_sanitizer_reports():
    out = ("==12==ERROR: AddressSanitizer: heap-buffer-overflow on address 0x1\n"
           "src/ring.c:7:13: runtime error: index 8 out of bounds for type 'int [8]'\n"
           "SUMMARY: AddressSanitizer: heap-buffer-overflow src/ring.c:7 in ring_push\n")
    found = game.sanitizer_reports(out)
    assert len(found) == 3 and "runtime error" in found[1]


def test_junit_names_and_system_out(tmp_path):
    (tmp_path / "r.xml").write_text(
        '<testsuites><testsuite><testcase name="Ring.Keeps" classname="Ring.Keeps" status="fail">'
        '<failure message="Failed"/><system-out>ring_test.cpp:9: Failure\nExpected: 52</system-out>'
        '</testcase><testcase name="test_a" classname="tests.test_x"/></testsuite></testsuites>')
    checks = game.junit_results(tmp_path)
    assert checks[0]["name"] == "Ring.Keeps" and "Expected: 52" in checks[0]["detail"]
    assert checks[1]["name"] == "tests.test_x.test_a"


def test_cc_build_cmake_flags(project, bindir):
    project.FILES = {"CMakeLists.txt": "project(x)\n"}
    bindir("cmake", "import sys, json, os\nopen(os.environ['HOME'] + '/args', 'a').write(json.dumps(sys.argv[1:]) + '\\n')\n")
    b = game.cc_build(sanitize="address,undefined", compiler="clang")
    assert b.ok
    calls = [json.loads(x) for x in open(os.environ["HOME"] + "/args")]
    cfg = calls[0]
    assert "-DCMAKE_C_COMPILER=clang" in cfg and "-G" in cfg
    assert any(a.startswith("-DCMAKE_CXX_FLAGS=-fsanitize=address,undefined") for a in cfg)
    assert calls[1][:2] == ["--build", str(b.build_dir)]


# ------------------------------------------------------------------ SQL

def test_plan_nodes():
    plan = {"Plan": {"Node Type": "Bitmap Heap Scan", "Relation Name": "orders",
                     "Plans": [{"Node Type": "Bitmap Index Scan", "Index Name": "orders_customer_idx"}]}}
    assert game.plan_nodes(plan) == [("Bitmap Heap Scan", "orders"),
                                     ("Bitmap Index Scan", "orders_customer_idx")]


def test_sql_files_sort_numbers_as_numbers(project):
    project.FILES = {"migrations/10_c.sql": "", "migrations/2_b.sql": "", "migrations/001_a.sql": "",
                     "migrations/notes.txt": ""}
    assert [p.name for p in game.sql_files()] == ["001_a.sql", "2_b.sql", "10_c.sql"]


def test_pg_test_parses_pg_prove(project, bindir, monkeypatch):
    project.FILES = {"tests/t.sql": "select 1"}

    class Srv:
        sock, user = Path("/tmp/s"), "postgres"

        def create_db(self, n):
            return n

        def psql(self, *a, **k):
            r = game.Result(0, "")
            r.errors = []
            return r
    monkeypatch.setattr(game, "pg", lambda *a: Srv())
    bindir("pg_prove", "print('tests/t.sql ..\\nok 1 - has orders\\nnot ok 2 - totals add up\\n"
                       "# Failed test 2: \"totals add up\"\\n#         have: 14\\n#         want: 15\\n"
                       "ok 3 - later # SKIP no\\n1..3')\nraise SystemExit(1)\n")
    r = game.pg_test(setup=None)
    assert [c["name"] for c in r.checks] == ["tests/t.sql: has orders", "tests/t.sql: totals add up"]
    assert "want: 15" in r.checks[1]["detail"] and not r.ok


# ------------------------------------------------------------------ mcu

def test_unity_runner_and_output():
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "test_x.c"
        f.write_text("void setUp(void) {}\nvoid test_a(void) {}\nstatic void helper(void) {}\n"
                     "void test_b ( void ) { }\n")
        src = game.unity_runner(f)
    assert "RUN_TEST(test_a);" in src and "RUN_TEST(test_b);" in src and "helper" not in src
    out = ("tests/test_x.c:4:test_a:PASS\ntests/test_x.c:9:test_b:FAIL: Expected 3 Was 4\n"
           "tests/test_x.c:12:test_c:IGNORE\n")
    found = [m.groupdict() for m in game.UNITY_LINE.finditer(out)]
    assert [(m["name"], m["status"]) for m in found] == [("test_a", "PASS"), ("test_b", "FAIL"),
                                                         ("test_c", "IGNORE")]
    assert found[1]["msg"] == "Expected 3 Was 4"


def _uf2_block(addr, n, total, fam):
    hdr = struct.pack("<8I", 0x0A324655, 0x9E5D5157, 0x2000, addr, 256, n, total, fam)
    return hdr + b"\0" * 476 + struct.pack("<I", 0x0AB16F30)


def test_uf2_info(tmp_path):
    p = tmp_path / "a.uf2"
    p.write_bytes(_uf2_block(0x10000000, 0, 2, 0xe48bff56) + _uf2_block(0x10000100, 1, 2, 0xe48bff56))
    info = game.uf2_info(p)
    assert info == {"blocks": 2, "families": ["rp2040"], "start": 0x10000000, "end": 0x10000200,
                    "bytes": 512}
    p.write_bytes(b"x" * 512)
    with pytest.raises(AssertionError, match="bad magic"):
        game.uf2_info(p)


def test_mcu_size_parses(bindir):
    bindir("arm-none-eabi-size", "print('   text\\t   data\\t    bss\\t    dec\\t    hex\\tfilename')\n"
                                 "print('   7228\\t     80\\t   4104\\t  11412\\t   2c94\\tm3.elf')\n")
    assert game.mcu_size("m3.elf") == {"text": 7228, "data": 80, "bss": 4104, "flash": 7308,
                                       "ram": 4184}


def test_micropython_fakes_under_cpython(monkeypatch):
    monkeypatch.syspath_prepend(str(HERE / "mcu" / "micropython_fakes"))
    for m in ("machine", "forge_hw"):
        sys.modules.pop(m, None)
    import forge_hw
    import machine
    forge_hw.reset()
    led = machine.Pin(25, machine.Pin.OUT)
    led.toggle()
    assert forge_hw.pin(25) == 1
    forge_hw.i2c_device(0x76, {0xD0: b"\x60", 0xD1: b"\x01"})
    bus = machine.I2C(0, scl=machine.Pin(5), sda=machine.Pin(4))
    assert bus.scan() == [0x76] and bus.readfrom_mem(0x76, 0xD0, 2) == b"\x60\x01"
    with pytest.raises(OSError):
        bus.readfrom_mem(0x40, 0, 1)
    forge_hw.set_adc(26, [100, 200])
    adc = machine.ADC(26)
    assert [adc.read_u16(), adc.read_u16(), adc.read_u16()] == [100, 200, 200]
    seen = []
    t = machine.Timer(mode=machine.Timer.PERIODIC, period=250, callback=lambda t: seen.append(1))
    forge_hw.tick(1000)
    assert len(seen) == 4
    t.deinit()
    forge_hw.tick(1000)
    assert len(seen) == 4
    u = machine.UART(0, 115200)
    forge_hw.uart_feed(0, b"OK\r\nREADY\r\n")
    assert u.readline() == b"OK\r\n" and u.any() == 7
    u.write("AT\r\n")
    assert forge_hw.uart_sent(0) == b"AT\r\n"
    got = []
    btn = machine.Pin(15, machine.Pin.IN, machine.Pin.PULL_UP)
    btn.irq(lambda p: got.append(p.value()), trigger=machine.Pin.IRQ_FALLING)
    forge_hw.edge(15, 0)
    forge_hw.edge(15, 1)
    assert got == [0]
    for m in ("machine", "forge_hw"):
        sys.modules.pop(m, None)


# ------------------------------------------------------------------ devices: the fake adb server

def _adb_req(port, *reqs):
    """Talk to the fake server like the adb client does."""
    s = socket.create_connection(("127.0.0.1", port), timeout=5)
    out = []
    for r in reqs:
        s.sendall(f"{len(r):04x}{r}".encode())
        out.append(s.recv(4))
    return s, out


def _read_all(s):
    data = b""
    while True:
        c = s.recv(65536)
        if not c:
            return data
        data += c


def test_adb_fake_protocol(monkeypatch):
    monkeypatch.setattr(game.AdbFake, "_client_version", staticmethod(lambda: 41))
    dev = game.FakeAndroid(shell={"false": ("", 1), "pm install *": "Success\n"},
                           files={"/sdcard/a.txt": b"hello"})
    with game.adb_fake(dev) as srv:
        assert os.environ["ANDROID_ADB_SERVER_PORT"] == str(srv.port)
        s, (ok,) = _adb_req(srv.port, "host:version")
        assert ok == b"OKAY" and _read_all(s) == b"0004" + b"0029"
        s, _ = _adb_req(srv.port, "host:devices")
        assert _read_all(s).endswith(b"FORGE0001\tdevice\n")
        # shell v2 after selecting the device: stdout packet then exit code
        s, (a, b) = _adb_req(srv.port, "host:tport:serial:FORGE0001", "shell,v2,raw:getprop ro.build.version.sdk")
        assert a == b"OKAY"
        data = b + _read_all(s)
        tid, rest = data[:8], data[8:]
        assert rest[:4] == b"OKAY" and rest[4:5] == b"\x01" and b"35\n" in rest
        assert rest.endswith(b"\x03\x01\x00\x00\x00\x00")
        s, (a, b) = _adb_req(srv.port, "host:transport:FORGE0001", "shell,v2,raw:false")
        assert (b + _read_all(s)).endswith(b"\x03\x01\x00\x00\x00\x01")
        # sync: pull a file, push one
        s, (a, b) = _adb_req(srv.port, "host:transport-any", "sync:")
        path = b"/sdcard/a.txt"
        s.sendall(b"RECV" + struct.pack("<I", len(path)) + path)
        got = b""
        while not got.endswith(b"DONE" + b"\x00" * 4):
            got += s.recv(4096)
        assert got == b"DATA" + struct.pack("<I", 5) + b"hello" + b"DONE" + b"\x00" * 4
        dest = b"/sdcard/b.bin,33188"
        s.sendall(b"SEND" + struct.pack("<I", len(dest)) + dest + b"DATA" + struct.pack("<I", 3) + b"xyz"
                  + b"DONE" + struct.pack("<I", 0))
        assert s.recv(8) == b"OKAY\x00\x00\x00\x00"
        s.sendall(b"QUIT" + struct.pack("<I", 0))
        s, (bad,) = _adb_req(srv.port, "host:transport:NOPE")
        assert bad == b"FAIL"
    assert dev.files["/sdcard/b.bin"] == b"xyz"
    assert ("pull", "/sdcard/a.txt") in dev.calls and ("push", "/sdcard/b.bin", 3) in dev.calls
    assert "ANDROID_ADB_SERVER_PORT" not in os.environ or os.environ["ANDROID_ADB_SERVER_PORT"] != str(srv.port)


def test_fake_android_shell_rules():
    d = game.FakeAndroid(shell={"pm list *": "package:a\n", "pm list packages -3": "package:b\n"},
                         files={"/sdcard/x/1.jpg": b"1", "/sdcard/x/2.jpg": b"2"})
    assert d.run_shell("pm list packages -3")[0] == b"package:b\n"
    assert d.run_shell("pm list users")[0] == b"package:a\n"
    assert d.run_shell("ls /sdcard/x")[0] == b"1.jpg\n2.jpg\n"
    assert d.run_shell("getprop ro.product.model")[0] == b"Pixel 8\n"
    assert d.run_shell("reboot-now")[2] == 127


@pytest.mark.skipif(not (shutil.which("mcopy") and shutil.which("sfdisk") and shutil.which("mkfs.fat")),
                    reason="mtools/sfdisk/dosfstools not installed here (they are in the image)")
def test_disk_image_fat(project):
    img = game.disk_image("t.img", 40, [{"size_mb": 33, "type": "c", "fs": "fat32",
                                         "files": {"a/b.txt": "hi"}}, {"type": "83"}])
    assert game.fat_files(img, 1) == {"/a/b.txt": 2}
    assert game.fat_read(img, "/a/b.txt", 1) == b"hi"


# ------------------------------------------------------------------ Raspberry Pi

def test_pi_config_problems(project):
    project.FILES = {"boot/config.txt": "# c\n[all]\ndtparam=i2c_arm=on\ndtoverlay=a, b\nnonsense line\n"
                                        "[pi5]\ninclude extra.txt\narm_freq=1800\n"}
    probs = game.pi_config_problems()
    assert len(probs) == 2 and "config.txt:4:" in probs[0] and "config.txt:5:" in probs[1]


def test_cloud_init_schema_filters_log_lines(project, bindir):
    project.FILES = {"user-data": "#cloud-config\n"}
    bindir("cloud-init", "print('2026-11-06 00:35:48,987 - log_util.py[WARNING]: Getting data from x')\n"
                         "print(\"Error: Cloud config schema errors: bogus_key: Additional properties "
                         "are not allowed ('bogus_key' was unexpected)\")\nprint('Error: Invalid schema: user-data')\n"
                         "raise SystemExit(1)\n")
    probs = game.cloud_init_schema("user-data")
    assert len(probs) == 1 and "bogus_key" in probs[0]


# ------------------------------------------------------------------ mirrors

def test_mirror_env(monkeypatch):
    monkeypatch.setattr(forge, "CURRENT_CFG", {"mirrors": {"npm": "http://z:4873/", "go": "http://z:3300",
                                                            "pypi": "http://z:3141/index/"}})
    monkeypatch.setattr(forge, "_MIRROR_UP", {"http://z:4873/": True, "http://z:3300": True,
                                              "http://z:3141/index/": False})
    assert forge.mirror_env("node") == {"npm_config_registry": "http://z:4873/"}
    assert forge.mirror_env("go")["GOPROXY"].startswith("file:///cache/cache/download,http://z:3300|")
    assert forge.mirror_env("homeassistant") == {}
    monkeypatch.setattr(forge, "CURRENT_CFG", {})
    assert forge.mirror_url("npm") is None


def test_installer_declares_mirror_args_after_every_from():
    inst = (HERE / "install-forge-toolchains.sh").read_text()
    assert "/^FROM /a ARG PIP_INDEX_URL" in inst and "MIRROR_ARGS" in inst
    assert '"--save"' in inst and '"--load"' in inst
    compose = (HERE / "ziggurat" / "compose.yaml").read_text()
    for port in ("3142:3142", "3141:5000", "4873:4873", "3300:3000", "5005:5000"):
        assert port in compose


def test_forge_game_defines_each_name_once():
    """5 Nov: the ESP32 helpers first reused the name esp_build, which
    silently replaced Fallout 4's plugin builder."""
    import ast
    import collections
    tree = ast.parse((HERE / "forge_game.py").read_text())
    names = collections.Counter(n.name for n in tree.body
                                if isinstance(n, (ast.FunctionDef, ast.ClassDef)))
    assert [n for n, c in names.items() if c > 1] == []
