"""5 November: the minecraft toolchain (Paper plugins) - the jar index, the
javac-error facts, the test harness's Java, JUnit reports - and the shared
API-facts plumbing it plugs into."""
import base64
import json
import sys
from pathlib import Path

import forge

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "tools"))
import forge_game as game  # noqa: E402
import forge_java  # noqa: E402
import java_api  # noqa: E402

# A jar compiled from demo/api/Shape.java (interface: area(), default
# label()) and demo/api/Square.java (implements Shape; public static final
# int SIDES, public final double side, ctor (double), area(), @Deprecated
# perimeter(), protected grow(int, String[]), package-private hidden(),
# public enum Colour {RED, GREEN}, package-private nested Secret,
# names(Map) -> List).
DEMO_JAR = (
    "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"
)


def test_the_jar_index_reads_class_files(tmp_path):
    jar = tmp_path / "demo.jar"
    jar.write_bytes(base64.b64decode("".join(DEMO_JAR)))
    types = java_api.load([str(jar)], ["demo"])
    assert set(types) == {"demo.api.Shape", "demo.api.Square", "demo.api.Square.Colour"}
    sq = types["demo.api.Square"]
    assert sq["kind"] == "class" and sq["interfaces"] == ["demo.api.Shape"]
    assert ["SIDES", "int", "static"] in sq["props"] and ["side", "double"] in sq["props"]
    names = {m[0]: m for m in sq["methods"]}
    assert names["grow"][2] == ["int", "java.lang.String[]"]        # protected counts
    assert "hidden" not in names                                    # package-private does not
    assert names["names"][1] == "java.util.List" and sq["ctors"] == [["double"]]
    assert sq["deprecated"] == ["perimeter"]
    assert types["demo.api.Shape"]["kind"] == "interface"
    assert types["demo.api.Square.Colour"]["values"] == ["RED", "GREEN"]


def test_paper_index_ships_and_answers():
    doc = forge_java.load("minecraft")
    assert doc and any("Paper API" in n for n in doc["names"])
    assert "org.bukkit.entity.Player" in doc["types"]
    # World.spawnEntity comes from an interface two levels up
    assert "spawnEntity" in forge_java.method_names(doc, "org.bukkit.World")


JAVAC = """/tmp/project/src/main/java/forge/crest/Crest.java:12: error: cannot find symbol
        player.sendMesage("x");
              ^
  symbol:   method sendMesage(String)
  location: variable player of type Player
/tmp/project/src/main/java/forge/crest/Crest.java:20: error: cannot find symbol
        Location l = null;
        ^
  symbol:   class Location
  location: class Crest
/tmp/project/src/main/java/forge/crest/Crest.java:4: error: package org.bukkit.entitty does not exist
"""


def test_javac_errors_get_the_real_names(monkeypatch):
    hints, sure = forge_java.compile_hints(JAVAC, "minecraft", limit=10)
    text = "\n".join(hints)
    assert "Closest real ones: `sendMessage`" in text
    assert "add `import org.bukkit.Location;`" in text
    assert "`org.bukkit.entity`" in text
    assert sure
    monkeypatch.setattr(forge, "TOOLCHAIN", "minecraft")
    out = forge.with_api_facts(JAVAC)
    assert forge.API_FACTS_HEAD in out and "import org.bukkit.Location;" in out
    hints, covered = forge.mechanical_hints({"chunks": []}, {"id": "c1", "spec": ""}, "", out)
    assert covered and any("FACTS under" in h for h in hints)


def test_a_java_lesson_naming_a_missing_method_is_dropped(tmp_path, monkeypatch):
    from conftest import make_run
    monkeypatch.setattr(forge, "TOOLCHAIN", "minecraft")
    run = make_run(tmp_path)
    forge.add_lesson(run, "In Paper, send chat with Player.sendChat(String).", "c2", "code")
    assert not run.state.get("lessons_pending")
    forge.add_lesson(run, "In Paper, Player.sendMessage(String) sends chat.", "c2", "code")
    assert run.state["lessons_pending"]["c2"]


def test_every_api_name_in_the_minecraft_notes_exists():
    assert forge_java.unknown_names("Paper " + forge.MINECRAFT_CODER_RULES, "minecraft") == []
    assert forge_java.unknown_names("Paper " + forge.TOOLCHAINS["minecraft"]["test_help"],
                                    "minecraft") == []


def test_java_errors_keep_their_notes():
    errs = game.java_errors(JAVAC)
    assert errs[0].startswith("/tmp/project/src/main/java/forge/crest/Crest.java:12: error: "
                              "cannot find symbol | symbol:   method sendMesage(String)")
    assert len(errs) == 3


def test_the_harness_hoists_imports_and_escapes_the_results_path():
    src = game.mc_harness_source('import org.bukkit.boss.BarColor;\nFORGE.check("x", true);\n',
                                 '/tmp/a "b"/r.json', delay=5, limit=30)
    assert src.index("import org.bukkit.boss.BarColor;") < src.index("public final class")
    assert 'FORGE.check("x", true);' in src.split("private void body(")[1]
    assert 'java.nio.file.Path.of("/tmp/a \\"b\\"/r.json")' in src
    assert "}, 5);" in src and "30 * 20L" in src
    assert "import java.util.*;" in src


def test_junit_reports_become_checks(tmp_path):
    (tmp_path / "TEST-junit-jupiter.xml").write_text(
        '<testsuite><testcase classname="a.B" name="ok()"/>'
        '<testcase classname="a.B" name="bad()"><failure message="expected 2">trace</failure></testcase>'
        '<testcase classname="a.B" name="skip()"><skipped/></testcase></testsuite>')
    checks = game.junit_results(tmp_path)
    assert [(c["name"], c["ok"]) for c in checks] == [("a.B.ok()", True), ("a.B.bad()", False)]
    assert "expected 2" in checks[1]["detail"]


def test_server_log_errors_skip_offline_noise():
    log = ("[12:00:01 INFO]: Done (3.2s)!\n"
           "[12:00:02 ERROR]: Could not resolve host sessionserver.mojang.com\n"
           "[12:00:03 ERROR]: Error occurred while enabling Crest v1 (Is it up to date?)\n"
           "java.lang.NullPointerException: boom\n"
           "\tat forge.Crest.onEnable(Crest.java:10)\n")
    log = log.replace("[12:00:02 ERROR]", "[12:00:02 Server thread/ERROR]").replace(
        "[12:00:03 ERROR]", "[12:00:03 Server thread/ERROR]")
    errs = game.mc_errors(log)
    assert any("enabling Crest" in e for e in errs) and any("NullPointerException" in e for e in errs)
    assert not any("sessionserver" in e for e in errs)


def test_minecraft_is_a_toolchain_with_a_selftest():
    assert forge.TOOLCHAINS["minecraft"]["fence"] == "java"
    assert "minecraft" in forge.SELFTEST_FILES and "minecraft" in forge.SELFTEST_STEPS
    assert forge.TOOLCHAIN_LANG["minecraft"] == "java"
    plugin = forge.SELFTEST_FILES["minecraft"]["src/main/java/forge/selftest/ForgeSelftest.java"]
    assert "public class ForgeSelftest" in plugin          # MockBukkit cannot load a final one
