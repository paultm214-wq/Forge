"""4 November: pdx-slice-a-plugin - every test began with `import game`, which
no sandbox could import, and Plugin/Program.cs failed 28 attempts on Mutagen
names that do not exist; the audits "fixed" them with other invented names.
Plus the Blender API slips of pdx-slice-a-props."""
import os
import re
import subprocess
import sys
from pathlib import Path

import forge
from conftest import make_run

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
import forge_dotnet  # noqa: E402

sys.path.insert(0, str(HERE / "tools"))
import dotnet_api  # noqa: E402

ERRORS = """\
/tmp/project/Plugin/Program.cs(99,15): error CS1061: 'Quest' does not contain a definition for 'VMAD' and no accessible extension method 'VMAD' accepting a first argument of type 'Quest' could be found (are you missing a using directive or an assembly reference?) [/tmp/project/Plugin/Plugin.csproj]
/tmp/project/Plugin/Program.cs(98,11): error CS0246: The type or namespace name 'IModGetter' could not be found (are you missing a using directive or an assembly reference?) [/tmp/project/Plugin/Plugin.csproj]
/tmp/project/Plugin/Program.cs(6,24): error CS0234: The type or namespace name 'Synthesis' does not exist in the namespace 'Mutagen.Bethesda' (are you missing an assembly reference?)
/tmp/project/Plugin/Program.cs(139,44): error CS1061: 'FormKey' does not contain a definition for 'Value' and no accessible extension method 'Value' accepting a first argument of type 'FormKey' could be found
/tmp/project/Plugin/Program.cs(80,24): error CS0246: The type or namespace name 'StatRecord' could not be found
/tmp/project/Plugin/Program.cs(73,21): error CS1729: 'ScriptProperty' does not contain a constructor that takes 3 arguments
"""
USING_ONLY = ERRORS.splitlines()[1] + "\n"


# --------------------------------------------------------------- import game

def test_import_game_works_in_a_files_mode_test(tmp_path, monkeypatch):
    monkeypatch.setattr(forge, "TOOLCHAIN", "fo4")
    head = forge.IMPORT_ALL.format("module")
    (tmp_path / "forge_game.py").write_text("ANSWER = 42\n")
    (tmp_path / "module.py").write_text("")
    (tmp_path / "t.py").write_text(head + "import game\nprint(game.ANSWER)\n")
    env = dict(os.environ, PYTHONPATH=str(tmp_path))
    out = subprocess.run([sys.executable, "t.py"], cwd=tmp_path, env=env,
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "42"


def test_no_module_named_game_is_not_called_a_missing_package(monkeypatch):
    monkeypatch.setattr(forge, "TOOLCHAIN", "fo4")
    hints, _ = forge.mechanical_hints({"chunks": []}, {"id": "c1", "spec": ""}, "",
                                      "ModuleNotFoundError: No module named 'game'")
    assert not any("not installed" in h for h in hints)


# ----------------------------------------------------------- the API index

def test_the_mutagen_index_ships_and_knows_the_records():
    doc = forge_dotnet.load("fo4")
    assert doc and doc["version"] == "0.54.4"
    t = doc["types"]
    quest = dict(t["Mutagen.Bethesda.Fallout4.Quest"]["props"])
    assert quest["VirtualMachineAdapter"] == "Mutagen.Bethesda.Fallout4.QuestAdapter"
    assert "StartGameEnabled" in t["Mutagen.Bethesda.Fallout4.Quest.Flag"]["values"]
    assert dict(t["Mutagen.Bethesda.Plugins.FormKey"]["props"])["ID"] == "uint"


def test_compile_errors_get_the_real_names():
    hints, sure = forge_dotnet.compile_hints(ERRORS, "fo4", limit=10)
    text = "\n".join(hints)
    assert "add `using Mutagen.Bethesda.Plugins.Records;`" in text
    assert "`VirtualMachineAdapter`" in text                 # VMAD's real name
    assert "There is no `Mutagen.Bethesda.Synthesis`" in text
    assert "ID: uint" in text and "ModKey: ModKey" in text    # FormKey has no Value
    assert "`Static` (Mutagen.Bethesda.Fallout4)" in text      # StatRecord -> STAT
    assert "new ScriptProperty()" in text
    assert not sure                                          # some need a model's judgement
    _, sure = forge_dotnet.compile_hints(USING_ONLY, "fo4")
    assert sure


def test_api_facts_go_with_the_failure_once(monkeypatch):
    monkeypatch.setattr(forge, "TOOLCHAIN", "fo4")
    out = forge.with_api_facts(USING_ONLY)
    assert forge.API_FACTS_HEAD in out and "using Mutagen.Bethesda.Plugins.Records;" in out
    assert forge.with_api_facts(out) == out
    hints, covered = forge.mechanical_hints({"chunks": []}, {"id": "c3", "spec": ""}, "", out)
    assert covered and any("FACTS under" in h for h in hints)
    monkeypatch.setattr(forge, "TOOLCHAIN", "godot")         # no index: unchanged
    assert forge.with_api_facts(USING_ONLY) == USING_ONLY


def test_lessons_that_name_missing_members_are_dropped(tmp_path, monkeypatch):
    monkeypatch.setattr(forge, "TOOLCHAIN", "fo4")
    run = make_run(tmp_path)
    invented = ("In Mutagen, Fallout 4 quest start-game-enabled is set via "
                "Quest.Data.DataFlags |= QuestDataFlags.StartGameEnabled.")
    forge.add_lesson(run, invented, "c3", "code")
    assert not run.state.get("lessons_pending") and not run.state.get("lessons")
    real = "In Mutagen, set Quest.Data.Flags with Quest.Flag.StartGameEnabled."
    forge.add_lesson(run, real, "c3", "code")
    assert run.state["lessons_pending"]["c3"][0]["text"] == real
    papyrus = "Papyrus compiler error messages use lowercase type names."
    forge.add_lesson(run, papyrus, "c1", "tests")
    assert run.state["lessons_pending"]["c1"][0]["text"] == papyrus


def test_every_mutagen_name_in_the_coder_notes_exists():
    notes = forge.FO4_CODER_RULES.split("MUTAGEN (Plugin/Program.cs)")[1]
    assert forge_dotnet.unknown_names("Mutagen " + notes, "fo4") == []
    doc = forge_dotnet.load("fo4")
    for name in ("Fallout4Mod", "GlobalShort", "QuestData", "QuestAdapter", "ScriptEntry",
                 "ScriptObjectProperty", "ScriptObjectListProperty", "MasterReference",
                 "BinaryWriteParameters", "MastersListContentOption", "IModGetter", "FormLink",
                 "IFallout4MajorRecordGetter", "ModKey", "Fallout4Release"):
        assert forge_dotnet.find_types(doc, name), name
    for ns in ("Mutagen.Bethesda", "Mutagen.Bethesda.Fallout4", "Mutagen.Bethesda.Plugins",
               "Mutagen.Bethesda.Plugins.Records", "Mutagen.Bethesda.Plugins.Binary.Parameters"):
        assert ns in doc["namespaces"], ns


def test_the_fo4_coder_gets_the_members_of_the_spec_records(monkeypatch):
    monkeypatch.setattr(forge, "TOOLCHAIN", "fo4")
    monkeypatch.setattr(forge, "RUN_SPEC_TEXT",
                        (HERE / "examples" / "pdx-slice-a-plugin.md").read_text())
    note = forge.api_note()
    for want in ("- Faction : Fallout4MajorRecord", "- GlobalShort : Global", "- Quest.Flag (enum)",
                 "record groups", "Statics", "- ScriptObjectListProperty"):
        assert want in note, want
    assert len(note) < 9000
    assert note in forge.coder_system()
    monkeypatch.setattr(forge, "RUN_SPEC_TEXT", "Papyrus only, no plugin.")
    assert forge.api_note() == ""


def test_the_fo4_test_writer_knows_papyrus_errors_are_dicts():
    help_ = forge.TOOLCHAINS["fo4"]["test_help"]
    assert "e['message']" in help_ and "lower case" in help_
    assert "Scripts/Source/User/PDX/Test/Bad.psc" in help_


def test_signature_decoding():
    resolve = lambda table, row: {(1, 1): "System.Collections.Generic.List`1",
                                  (1, 2): "Mutagen.Bethesda.Fallout4.Quest"}[(table, row)]
    # GENERICINST CLASS List`1 <CLASS Quest>  (TypeDefOrRef: row << 2 | table)
    blob = bytes([0x15, 0x12, (1 << 2) | 1, 1, 0x12, (2 << 2) | 1])
    assert dotnet_api.Sig(blob, resolve).type() == "System.Collections.Generic.List<Mutagen.Bethesda.Fallout4.Quest>"
    # instance property of type short?   (PROPERTY|HASTHIS, 0 params, Nullable<short>)
    resolve2 = lambda table, row: "System.Nullable`1"
    blob = bytes([0x28, 0, 0x15, 0x11, (1 << 2) | 1, 1, 0x06])
    assert dotnet_api.Sig(blob, resolve2).prop() == "short?"


# ------------------------------------------------- pdx-slice-a-props (Blender)

def test_blender_notes_name_the_real_api():
    r = forge.BLENDER_CODER_RULES
    assert "decimate_type = 'COLLAPSE'" in r and "face.verts" in r
    assert "context='VERTS'" in r and "users_collection" in r
    assert re.search(r"measure and ground .*AFTER", r, re.S)


# ----------------------------------------------- 4 Nov, second plugin run

import struct  # noqa: E402

sys.path.insert(0, str(HERE / "tests"))
from test_game_helpers import grup, rec, sub  # noqa: E402

sys.path.insert(0, str(HERE))
import forge_game as game  # noqa: E402

ATTEMPT_2 = """ok=False


Traceback (most recent call last):
  File "/work/verify.py", line 93, in <module>
    null_idx = modl_bytes.index(b'\\x00')
               ^^^^^^^^^^^^^^^^^^^^^^^^^
ValueError: b'\\x00' is not in list
"""


def test_glob_quest_and_model_come_decoded(tmp_path):
    hdr = sub("HEDR", struct.pack("<fiI", 1.0, 3, 0x801)) + sub("MAST", b"Fallout4.esm\0") \
        + sub("DATA", b"\0" * 8)
    glob = rec("GLOB", 0, 0x01000800, sub("EDID", b"PDX_CrestController\0") + sub("FNAM", b"s")
               + sub("FLTV", struct.pack("<f", -1.0)))
    qust = rec("QUST", 0, 0x01000801, sub("EDID", b"PDX_CrestBroadcast\0")
               + sub("FULL", b"The Crest Signal\0")
               + sub("DNAM", struct.pack("<HBBfB", 0x0001, 0, 0, 0.0, 0)))
    stat = rec("STAT", 0, 0x01000802, sub("EDID", b"PDX_BensonBubbler\0")
               + sub("MODL", b"PDX\\Props\\BensonBubbler.nif\0"))
    esp = tmp_path / "P.esp"
    esp.write_bytes(rec("TES4", 0, 0, hdr) + grup("GLOB", glob) + grup("QUST", qust)
                    + grup("STAT", stat))
    g = game.esp_record(esp, "PDX_CrestController")
    assert g["glob_type"] == "s" and g["value"] == -1.0
    q = game.esp_record(esp, "PDX_CrestBroadcast")
    assert q["quest_flags"] & 0x0001
    s = game.esp_record(esp, "PDX_BensonBubbler")
    assert s["model"] == "PDX\\Props\\BensonBubbler.nif"
    assert isinstance(s["subs"]["MODL"], list)


def test_build_preview_packages_into_a_scratch_folder(tmp_path, monkeypatch):
    monkeypatch.setattr(game, "TMP", tmp_path / "tmp")
    made = []
    monkeypatch.setattr(game, "package", lambda tc, out: made.append((tc, out)) or [])
    out = game.build_preview("fo4")
    assert out.is_dir() and made == [("fo4", out)] and "build-preview" in out.name


def test_a_test_that_misreads_forge_game_is_told_so(monkeypatch):
    monkeypatch.setattr(forge, "TOOLCHAIN", "fo4")
    assert forge.with_api_facts(ATTEMPT_2) == ATTEMPT_2          # nothing says it is subs
    test = "    modl_bytes = rec['subs']['MODL']\n    null_idx = modl_bytes.index(b'\\x00')\n"
    out = forge.with_api_facts(ATTEMPT_2, "", test)
    assert "rec['subs']['MODL'][0]" in out and "THE TEST is wrong" in out
    assert forge.fail_signature(out) == forge.fail_signature(ATTEMPT_2)
    build = ("Traceback (most recent call last):\n  File \"/work/verify.py\", line 140, in <module>\n"
             "    assert (build_dir / 'x.esp').exists()\nAssertionError: outputs/build/x.esp missing\n")
    assert "game.build_preview('fo4')" in forge.with_api_facts(build)


def test_int_where_a_formkey_belongs_quotes_the_line(monkeypatch):
    monkeypatch.setattr(forge, "TOOLCHAIN", "fo4")
    err = ("/tmp/project/Plugin/Program.cs(3,40): error CS1503: Argument 1: cannot convert "
           "from 'int' to 'Mutagen.Bethesda.Plugins.FormKey'\n")
    module = "FILES = globals().setdefault('FILES', {})\nFILES['Plugin/Program.cs'] = r'''a\nb\n" \
             "        var faction = mod.Factions[i];\n'''\n"
    out = forge.with_api_facts(err, module)
    assert "`var faction = mod.Factions[i];`" in out
    assert "keyed by FormKey" in out and "record.FormKey" in out


def test_the_fo4_test_writer_knows_the_record_layouts():
    help_ = forge.TOOLCHAINS["fo4"]["test_help"]
    for want in ("rec['subs']['MODL'][0]", "rec['glob_type']", "quest_flags", "build_preview",
                 "no GLOB DATA"):
        assert want in help_, want


# --------------------------------------------- 4 Nov, retest and third run

def _ls(s):
    b = s.encode()
    return struct.pack("<H", len(b)) + b


def _obj(formid, alias=-1):
    return struct.pack("<hhI", 0, alias, formid)


def make_vmad():
    props = (_ls("ControllerGlobal") + struct.pack("<BB", 1, 1) + _obj(0x01000800)
             + _ls("Factions") + struct.pack("<BB", 11, 1) + struct.pack("<I", 2)
             + _obj(0x01000801) + _obj(0x01000802)
             + _ls("Label") + struct.pack("<BB", 2, 1) + _ls("hi")
             + _ls("Count") + struct.pack("<BB", 3, 1) + struct.pack("<i", -3))
    script = _ls("PDX:Quests:CrestBroadcast") + struct.pack("<B", 0) + struct.pack("<H", 4) + props
    return struct.pack("<hhH", 6, 2, 1) + script + b"\x02\x00fragments-not-read"


def test_vmad_is_decoded():
    s = game.vmad_scripts(make_vmad())
    assert [x["name"] for x in s] == ["PDX:Quests:CrestBroadcast"]
    p = {x["name"]: x for x in s[0]["properties"]}
    assert p["ControllerGlobal"]["type"] == "object"
    assert p["ControllerGlobal"]["value"] == {"formid": 0x01000800, "alias": -1}
    assert [o["formid"] for o in p["Factions"]["value"]] == [0x01000801, 0x01000802]
    assert p["Label"]["value"] == "hi" and p["Count"]["value"] == -3
    assert b"ControllerGlobal\x00" not in make_vmad()        # what the test assumed


def test_records_carry_their_scripts(tmp_path):
    hdr = sub("HEDR", struct.pack("<fiI", 1.0, 1, 0x801)) + sub("DATA", b"\0" * 8)
    q = rec("QUST", 0, 0x01000801, sub("EDID", b"Q\0") + sub("VMAD", make_vmad()))
    esp = tmp_path / "Q.esp"
    esp.write_bytes(rec("TES4", 0, 0, hdr) + grup("QUST", q))
    r = game.esp_record(esp, "Q")
    assert r["scripts"][0]["properties"][0]["name"] == "ControllerGlobal"


def test_a_null_terminated_vmad_check_is_called_a_test_fault(monkeypatch):
    monkeypatch.setattr(forge, "TOOLCHAIN", "fo4")
    out = ("- line 98: assert b\"ControllerGlobal\\x00\" in vmad, \"VMAD missing property name\"\n"
           "    -> AssertionError: VMAD missing property name 'ControllerGlobal'\n")
    facts = forge.with_api_facts(out)
    assert "no null terminator" in facts and "rec['scripts']" in facts


def test_a_read_only_mod_and_an_ambiguous_name_get_facts():
    out = ("/tmp/project/Plugin/Program.cs(66,17): error CS1929: 'IFallout4GroupGetter<IQuestGetter>' "
           "does not contain a definition for 'AddNew' and the best extension method overload "
           "'GroupMixIns.AddNew<TMajor>(IGroup<TMajor>)' requires a receiver of type "
           "'Mutagen.Bethesda.Plugins.Records.IGroup<TMajor>'\n"
           "/tmp/project/Plugin/Program.cs(178,32): error CS0104: 'Activator' is an ambiguous "
           "reference between 'Mutagen.Bethesda.Fallout4.Activator' and 'System.Activator'\n")
    hints, sure = forge_dotnet.compile_hints(out, "fo4")
    text = "\n".join(hints)
    assert "CreateFromBinaryOverlay" in text and "new Fallout4Mod(" in text
    assert "System.Activator" in text and sure


def test_a_lesson_claiming_missing_fields_is_dropped():
    lesson = ("When building a Papyrus VMAD with Mutagen, every ScriptObjectProperty / "
              "ScriptObjectListProperty must have its Type (and Access) field set.")
    assert forge_dotnet.unknown_names(lesson, "fo4") == ["ScriptObjectProperty.Type",
                                                        "ScriptObjectProperty.Access"]
    assert forge_dotnet.unknown_names("In Mutagen, a ScriptObjectProperty needs its Name and "
                                      "Flags set.", "fo4") == []


def test_the_coder_is_told_a_read_master_is_read_only():
    assert "IFallout4ModDisposableGetter" in forge.FO4_CODER_RULES
    spec = (HERE / "examples" / "pdx-slice-a-plugin.md").read_text()
    assert "reads masters from" not in spec and "rec['scripts']" in spec
