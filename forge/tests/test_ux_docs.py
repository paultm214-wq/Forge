"""Design, UX checks and user documentation for projects with an interface:
the design brief (house style, or Claude's adaptation of it), the rules
coders get, the report-only UX check (web: the forge_ux runner in the ux
image; Godot: the input and menu audit), and the illustrated user guide
and in-app help written at commit.

Nothing here calls Claude or Docker: a fake `claude` CLI and a fake sandbox
stand in. One test runs the real forge_ux runner when Playwright, Chromium
and axe-core are present (FORGE_AXE), and is skipped otherwise."""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

import forge
from conftest import make_run

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import forge_game as game  # noqa: E402

HERE = Path(__file__).resolve().parent.parent
WEB_SPEC = "# Notes app\n\nUI: web\n\nA small web app to keep notes.\n"


@pytest.fixture(autouse=True)
def ui_globals(monkeypatch):
    monkeypatch.setattr(forge, "UI_KIND", None)
    monkeypatch.setattr(forge, "DESIGN_RULES", "")
    monkeypatch.setattr(forge, "CURRENT_CFG", {})
    monkeypatch.setattr(forge, "_UX_IMAGE_OK", {})


FAKE_CLAUDE = r'''
import json, os, sys
from pathlib import Path
args = sys.argv[1:]
log = Path(os.environ["FAKE_CLAUDE_LOG"])
calls = json.loads(log.read_text()) if log.exists() else []
calls.append({"args": args, "cwd": os.getcwd(),
              "files": sorted(str(p) for p in Path(".").rglob("*") if p.is_file())})
log.write_text(json.dumps(calls))
mode = os.environ.get("FAKE_CLAUDE_MODE", "brief")
if mode == "error":
    print(json.dumps({"is_error": True, "result": os.environ.get("FAKE_CLAUDE_ERROR", "rate limited"),
                      "total_cost_usd": 0.01}))
elif mode == "brief":
    brief = {"design_md": "# Notes - design\n\nCalm and focused. Screens: List, Editor.",
             "tokens_css": os.environ.get("FAKE_TOKENS", ""),
             "rules": "Primary button bottom right. Autosave every edit.",
             "help": {"needed": True, "topics": ["Writing a note", "Search"]}}
    print(json.dumps({"result": "```json\n" + json.dumps(brief) + "\n```", "total_cost_usd": 0.25}))
elif mode == "docs":
    shots = sorted(p.name for p in Path("images").glob("*.png"))
    doc = {"title": "Notes help", "version": 1, "sections": [
        {"id": "overview", "title": "Overview", "keywords": ["about", "intro"],
         "body_md": "Notes keeps short notes.\n\n![The note list](images/" + (shots[0] if shots else "none.png") + ")"},
        {"id": "Writing notes", "title": "Writing a note", "keywords": ["new", "create", "edit"],
         "body_md": "1. Choose **New note**.\n2. Type.\n3. Press `Ctrl+S`.\n\n![missing](images/nope.png)"},
        {"id": "overview", "title": "Keyboard shortcuts", "keywords": ["keys"],
         "body_md": "| Key | Does |\n|---|---|\n| `/` | Search |\n\nNever type </script> here."}]}
    Path("help.json").write_text(json.dumps(doc))
    print(json.dumps({"result": "DONE", "total_cost_usd": 0.5}))
'''


@pytest.fixture
def fake_claude(tmp_path, monkeypatch):
    bindir = tmp_path / "claude-bin"
    bindir.mkdir()
    script = bindir / "claude"
    script.write_text(f"#!{sys.executable}\n{FAKE_CLAUDE}")
    script.chmod(0o755)
    log = tmp_path / "claude-calls.json"
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(log))

    def calls():
        return json.loads(log.read_text()) if log.exists() else []
    return calls


# ------------------------------------------------------------ detection

@pytest.mark.parametrize("spec,toolchain,kind", [
    ("UI: web\n", "python", "web"),
    ("UI: none\nA web app.", "python", None),
    ("UI: auto\nA Flask-free WSGI web app with HTML pages.", "python", "web"),
    ("A desktop app in tkinter.", "python", "gui"),
    ("A CSV parser.", "python", None),
    ("A platformer.", "godot", "game"),
    ("A mod.", "fo4", None),
    ("<!-- UI: web -->\nA CSV parser.", "python", None),
])
def test_spec_ui(spec, toolchain, kind):
    assert forge.spec_ui(spec, toolchain) == kind


# --------------------------------------------------------------- design

def test_no_ui_means_no_design(tmp_path):
    run = make_run(tmp_path, spec="# CSV\n\nA CSV parser.\n")
    assert forge.ensure_design(run, {}) is None
    assert not (run.root / "design").exists()
    assert forge.design_note(run) == "" and forge.ui_coder_note() == ""


def test_house_style_brief(tmp_path):
    run = make_run(tmp_path, spec=WEB_SPEC)
    assert forge.ensure_design(run, {"design": {"brief": "house"}}) == "web"
    d = run.root / "design"
    assert (d / "tokens.css").read_text() == (HERE / "design" / "tokens.css").read_text()
    assert "house style applies unchanged" in (d / "DESIGN.md").read_text()
    assert "## Principles" in (d / "DESIGN.md").read_text() or "house" in (d / "DESIGN.md").read_text()
    assert not (d / "game-ui.md").exists()
    assert forge.DESIGN_RULES == forge.HOUSE_RULES
    assert "design/tokens.css" in forge.ui_coder_note()


def test_game_gets_the_game_ui_standard(tmp_path, monkeypatch):
    monkeypatch.setattr(forge, "TOOLCHAIN", "godot")
    run = make_run(tmp_path, spec="# Game\n\nA platformer.\n")
    assert forge.ensure_design(run, {"design": {"brief": "house"}}) == "game"
    assert "Menus every game has" in (run.root / "design" / "DESIGN.md").read_text()


def test_house_design_dir_prefers_yours(tmp_path):
    assert forge.house_design_dir() == HERE / "design"
    mine = forge.FORGE_HOME / "design"
    mine.mkdir(parents=True)
    (mine / "house-style.md").write_text("# mine")
    assert forge.house_design_dir() == mine


def test_claude_adapts_the_house_style(tmp_path, fake_claude, monkeypatch):
    tokens = (HERE / "design" / "tokens.css").read_text().replace("#1f5fbf", "#0b6e4f")
    monkeypatch.setenv("FAKE_TOKENS", tokens)
    run = make_run(tmp_path, spec=WEB_SPEC)
    assert forge.ensure_design(run, {}) == "web"
    d = run.root / "design"
    assert "Calm and focused" in (d / "DESIGN.md").read_text()
    assert "#0b6e4f" in (d / "tokens.css").read_text()
    assert forge.DESIGN_RULES.startswith("Primary button")
    assert json.loads((d / "brief.json").read_text())["help"]["topics"] == ["Writing a note", "Search"]
    assert run.state["claude_cost_usd"] == pytest.approx(0.25)
    call = fake_claude()[0]
    assert {"SPEC.md", "house/house-style.md", "house/tokens.css"} <= set(call["files"])
    assert call["args"][call["args"].index("--permission-mode") + 1] == "plan"
    # Made once: a resume reuses it.
    assert forge.ensure_design(run, {}) == "web" and len(fake_claude()) == 1


def test_brief_tokens_without_core_tokens_are_ignored(tmp_path, fake_claude, monkeypatch):
    monkeypatch.setenv("FAKE_TOKENS", ":root { --bg: #000; }")
    run = make_run(tmp_path, spec=WEB_SPEC)
    forge.ensure_design(run, {})
    assert (run.root / "design" / "tokens.css").read_text() == (HERE / "design" / "tokens.css").read_text()


def test_claude_failure_falls_back_to_the_house_style(tmp_path, fake_claude, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "error")
    run = make_run(tmp_path, spec=WEB_SPEC)
    assert forge.ensure_design(run, {}) == "web"
    assert "house style applies unchanged" in (run.root / "design" / "DESIGN.md").read_text()
    assert forge.DESIGN_RULES == forge.HOUSE_RULES


def test_planner_sees_the_brief_and_the_help_contract(tmp_path):
    run = make_run(tmp_path, spec=WEB_SPEC + "\nHelp: yes\n")
    forge.ensure_design(run, {"design": {"brief": "house"}})
    note = forge.design_note(run)
    assert "## Design" in note and "## In-app help (required)" in note
    assert '"sections"' in note and "help/help.json" in note
    run2 = make_run(tmp_path / "b", spec=WEB_SPEC + "\nHelp: no\n")
    forge.ensure_design(run2, {"design": {"brief": "house"}})
    assert "In-app help" not in forge.design_note(run2)


def test_coders_get_the_design_rules(tmp_path):
    assert "USER INTERFACE" not in forge.coder_system()
    run = make_run(tmp_path, spec=WEB_SPEC)
    forge.ensure_design(run, {"design": {"brief": "house"}})
    assert "USER INTERFACE" in forge.coder_system()
    assert forge.HOUSE_RULES in forge.fix_system()


def test_tokens_meet_wcag_aa_in_both_themes():
    css = (HERE / "design" / "tokens.css").read_text()

    def theme(block):
        return dict(re.findall(r"(--[\w-]+):\s*(#[0-9a-fA-F]{6})", block))
    light = theme(css.split("@media (prefers-color-scheme: dark)")[0])
    dark = theme(css.split("@media (prefers-color-scheme: dark)")[1].split("}")[0])

    def lum(h):
        c = [int(h[i:i + 2], 16) / 255 for i in (1, 3, 5)]
        c = [x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4 for x in c]
        return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]

    def ratio(a, b):
        hi, lo = sorted((lum(a), lum(b)), reverse=True)
        return (hi + 0.05) / (lo + 0.05)
    for t in (light, {**light, **dark}):
        for fg in ("--text", "--text-muted", "--accent", "--danger", "--success", "--warning"):
            for bg in ("--bg", "--surface"):
                assert ratio(t[fg], t[bg]) >= 4.5, (fg, bg, t[fg], t[bg])
        assert ratio(t["--border-strong"], t["--bg"]) >= 3
    assert ratio(light["--on-accent"], light["--accent"]) >= 4.5


# ------------------------------------------------------------ the sandbox

def test_sandbox_profile_override(fake_docker, tmp_path):
    args = fake_docker(0, stdout="ok")
    forge.Sandbox({}).run({"t.py": "x"}, "t.py", profile={
        "image": "forge-sandbox-ux:latest", "memory": "2g", "tmpfs_mb": 1024, "pids": 1024,
        "exec_tmp": True, "env": {"HOME": "/tmp/home"}, "mounts": [(str(tmp_path), "/site")]})
    argv = json.loads(args.read_text())
    assert "forge-sandbox-ux:latest" in argv
    assert argv[argv.index("--memory") + 1] == "2g"
    assert argv[argv.index("--tmpfs") + 1] == "/tmp:rw,exec,size=1024m"
    assert argv[argv.index("--pids-limit") + 1] == "1024"
    assert f"{tmp_path}:/site:ro" in argv and "HOME=/tmp/home" in argv
    assert argv[argv.index("--network") + 1] == "none"


def test_sandbox_places_tokens_for_a_python_ui(tmp_path, monkeypatch, local_sandbox):
    run = make_run(tmp_path, spec=WEB_SPEC)
    monkeypatch.setattr(forge, "RUN_ROOT", run.root)
    forge.ensure_design(run, {"design": {"brief": "house"}})
    ok, out = local_sandbox.run(
        {"t.py": "from pathlib import Path\nprint(Path('design/tokens.css').read_text()[:20])"}, "t.py")
    assert ok and "forge house style" in out


def test_game_project_dir_gets_the_tokens(tmp_path, monkeypatch):
    design = tmp_path / "design"
    design.mkdir()
    (design / "tokens.css").write_text(":root{}")
    monkeypatch.setattr(game, "DESIGN", design)
    monkeypatch.setattr(game, "TMP", tmp_path / "t")
    monkeypatch.setattr(game, "VENDOR", tmp_path / "none")
    monkeypatch.setattr(game, "files", lambda: game.Files({"a.txt": "x"}))
    d = game.project_dir(fresh=True)
    assert (d / "design" / "tokens.css").read_text() == ":root{}"


# ------------------------------------------------------------ UX check

@pytest.mark.parametrize("spec,module,html,want", [
    ("Serve: `make_app(tmp, debug=False)`\n", "", False, ("wsgi", "make_app(tmp, debug=False)")),
    ("", "def make_app(data_dir):\n    pass\n", False, ("wsgi", "make_app(tmp)")),
    ("", "def create_app(config=None):\n    pass\n", False, ("wsgi", "create_app()")),
    ("", "def make_app(a, b):\n    pass\napp = 1\n", False, ("wsgi", "app")),
    ("", "def application(environ, start):\n    pass\n", False, ("wsgi", "application")),
    ("", "X = 1\n", True, ("static", "/site")),
    ("", "X = 1\n", False, None),
])
def test_serve_target(tmp_path, spec, module, html, want):
    run = make_run(tmp_path, spec="# W\n" + spec)
    if html:
        (run.root / "outputs").mkdir()
        (run.root / "outputs" / "index.html").write_text("<h1>x</h1>")
    assert forge.serve_target(run, module) == want


def test_spec_screens():
    spec = ('# W\n\n## Screens\n\n```json\n[{"name": "Home", "path": "/"}, {"name": "Bad", "path": "x"}]\n```\n'
            "\n## Acceptance\n")
    assert forge.spec_screens(spec) == [{"name": "Home", "path": "/"}]
    assert forge.spec_screens("# W\n") == []
    with pytest.raises(forge.ForgeError):
        forge.spec_screens("## Screens\n```json\n[{\n```\n")


WEB_REPORT = {"base": "http://127.0.0.1:1", "errors": [], "pages": [
    {"path": "/", "axe": [{"id": "color-contrast", "impact": "serious", "nodes": 3},
                          {"id": "region", "impact": "moderate", "nodes": 1}],
     "focus_invisible": ["a: Home"], "horizontal_scroll_mobile": True, "keyboard_trap": False},
    {"path": "/new", "axe": [{"id": "label", "impact": "critical", "nodes": 2}],
     "focus_invisible": [], "horizontal_scroll_mobile": False, "keyboard_trap": True}]}


def test_ux_summary_web_and_game():
    s = forge.ux_summary(WEB_REPORT)
    assert s["pages"] == 2 and s["focus_invisible"] == 1
    assert [r["id"] for r in s["rules"]] == ["label", "color-contrast", "region"]
    assert "6 accessibility problem(s) in 3 rule(s)" in s["line"]
    assert "sideways scrolling on a phone: /" in s["line"] and "keyboard trap: /new" in s["line"]
    g = forge.ux_summary({"actions": {"jump": {}}, "actions_without_gamepad": ["jump"],
                          "actions_without_keyboard": [], "settings_saved": False, "sets_focus": True})
    assert g["line"] == "1 input action(s): 1 without a gamepad binding, 0 without a key; settings are not saved"


class FakeUxSandbox:
    unsafe_local = False
    outputs_max_bytes = 50 * 2**20

    def __init__(self, report):
        self.report, self.calls = report, []

    def run(self, files, entry, args=None, out_dir=None, max_out=4000, timeout=None, profile=None):
        self.calls.append({"files": sorted(files), "entry": entry, "args": args, "profile": profile})
        (out_dir / "ux").mkdir(parents=True)
        (out_dir / "ux" / "ux-report.json").write_text(json.dumps(self.report))
        (out_dir / "ux" / "home-desktop.png").write_bytes(b"\x89PNG")
        return True, "ux: 2 page(s) checked"


def test_ux_check_web(tmp_path, monkeypatch):
    run = make_run(tmp_path, spec=WEB_SPEC + "\nServe: make_app(tmp)\n\n## Screens\n\n"
                   '```json\n[{"name": "Home", "path": "/"}]\n```\n')
    forge.ensure_design(run, {"design": {"brief": "house"}})
    monkeypatch.setattr(forge, "ux_image_ready", lambda image: True)
    sb = FakeUxSandbox(WEB_REPORT)
    report = forge.ux_check(run, {"ux": {"max_pages": 5}}, sb, "def make_app(d): pass\n")
    assert report == WEB_REPORT
    call = sb.calls[0]
    assert call["entry"] == "forge_ux.py" and call["files"] == ["forge_ux.py", "module.py"]
    assert call["args"][:3] == ["OUT", "wsgi", "make_app(tmp)"]
    assert json.loads(call["args"][3]) == [{"name": "Home", "path": "/"}] and call["args"][4] == "5"
    assert call["profile"]["image"] == "forge-sandbox-ux:latest" and call["profile"]["exec_tmp"]
    assert (run.root / "outputs" / "ux" / "home-desktop.png").exists()
    assert not (run.root / "outputs.ux").exists()
    assert run.state["ux"]["pages"] == 2
    md = forge.ux_report_md(run)
    assert "## User experience (reported, not enforced)" in md and "label (critical): 2 element(s)" in md


def test_ux_check_is_skipped_without_the_image(tmp_path, fake_docker):
    fake_docker(1, stderr="No such image")
    run = make_run(tmp_path, spec=WEB_SPEC + "\nServe: app\n")
    forge.ensure_design(run, {"design": {"brief": "house"}})
    sb = FakeUxSandbox(WEB_REPORT)
    assert forge.ux_check(run, {}, sb, "app = 1\n") is None
    assert sb.calls == [] and "ux" not in run.state


def test_ux_check_off_or_no_ui(tmp_path, monkeypatch):
    monkeypatch.setattr(forge, "ux_image_ready", lambda image: True)
    sb = FakeUxSandbox(WEB_REPORT)
    run = make_run(tmp_path, spec="# CSV\n")
    forge.ensure_design(run, {})
    assert forge.ux_check(run, {}, sb, "") is None
    run2 = make_run(tmp_path / "b", spec=WEB_SPEC + "\nServe: app\n")
    forge.ensure_design(run2, {"design": {"brief": "house"}})
    assert forge.ux_check(run2, {"ux": {"enabled": False}}, sb, "") is None
    assert sb.calls == []


GODOT_PROJECT = '''config_version=5

[application]
run/main_scene="res://main.tscn"

[display]
window/size/viewport_width=640
window/size/viewport_height=360

[input]
jump={
"deadzone": 0.5,
"events": [Object(InputEventKey,"keycode":32), Object(InputEventJoypadButton,"button_index":0)]
}
fire={
"deadzone": 0.5,
"events": [Object(InputEventMouseButton,"button_index":1)]
}
'''


def test_godot_ui_audit(monkeypatch):
    fs = {"project.godot": GODOT_PROJECT,
          "menu.gd": "func _ready():\n\t$Start.grab_focus()\n\tvar c = ConfigFile.new()\n\tc.save(\"user://settings.cfg\")\n",
          "hud.tscn": "theme_override_font_sizes/font_size = 12\n",
          "title.tres": "default_font_size = 24\n"}
    monkeypatch.setattr(game, "files", lambda: game.Files(fs))
    a = game.godot_ui_audit()
    assert a["actions"]["jump"] == {"keyboard": True, "gamepad": True, "mouse": False}
    assert a["actions_without_gamepad"] == ["fire"] and a["actions_without_keyboard"] == ["fire"]
    assert a["settings_saved"] and a["sets_focus"]
    assert a["smallest_font_size"] == 12 and a["viewport"] == [640, 360]


# ------------------------------------------------------------ documentation

def test_clean_help(tmp_path):
    (tmp_path / "list.png").write_bytes(b"x")
    doc = forge.clean_help({"title": "H", "sections": [
        {"id": "Getting Started", "title": "Start", "body_md": "![List](images/list.png) ![Gone](images/gone.png)"},
        {"id": "getting-started", "title": "Again", "keywords": ["a"] * 20},
        {"title": ""}, "junk"]}, tmp_path)
    ids = [s["id"] for s in doc["sections"]]
    assert ids == ["getting-started", "getting-started-2"]
    assert "![List](images/list.png)" in doc["sections"][0]["body_md"]
    assert "Gone" in doc["sections"][0]["body_md"] and "gone.png" not in doc["sections"][0]["body_md"]
    assert len(doc["sections"][1]["keywords"]) == 12
    with pytest.raises(ValueError):
        forge.clean_help({"sections": []}, tmp_path)


def test_md_to_html():
    out = forge.md_to_html(
        "### Steps\n\n1. Open **File**\n2. Choose *Save*\n\n- a\n- b `<x>`\n\n"
        "| Key | Does |\n|---|---|\n| `/` | Search |\n\n> Tip: try it\n\n"
        "```\n<b>raw</b>\n```\n\n[ok](https://example.org) [bad](javascript:alert(1)) "
        '![a "quoted" alt](images/x.png)\n\n<script>alert(1)</script>', level_shift=1)
    assert "<h4>Steps</h4>" in out
    assert "<ol><li>Open <strong>File</strong></li><li>Choose <em>Save</em></li></ol>" in out
    assert "<code>&lt;x&gt;</code>" in out
    assert '<th scope="col">Key</th>' in out and "<td><code>/</code></td>" in out
    assert '<aside class="note">' in out and "&lt;b&gt;raw&lt;/b&gt;" in out
    assert '<a href="https://example.org">ok</a>' in out and "javascript:" not in out
    assert 'alt="a &quot;quoted&quot; alt"' in out
    assert "<script>" not in out


def test_render_docs(tmp_path):
    images = tmp_path / "images"
    images.mkdir()
    (images / "home.png").write_bytes(b"png")
    (images / "unused.png").write_bytes(b"png")
    doc = {"title": "Notes help", "version": 1, "sections": [
        {"id": "overview", "title": "Overview", "keywords": ["about"],
         "body_md": "Hello.\n\n![Home](home.png)"},
        {"id": "keys", "title": "Keyboard shortcuts", "keywords": ["keys"], "body_md": "Never </script>."}]}
    docs = tmp_path / "docs"
    forge.render_docs(doc, images, docs, HERE / "design" / "tokens.css")
    page = (docs / "index.html").read_text()
    assert '<input id="q" type="search"' in page and "const INDEX = " in page
    assert '<a href="#keys">Keyboard shortcuts</a>' in page and '<section id="overview"' in page
    assert '<img src="images/home.png" alt="Home"' in page
    assert "--bg: #ffffff" in page                       # the tokens are inlined
    assert page.count("</script>") == 1                  # the body cannot close the script
    assert 'role="status"' in page and 'class="skip-link"' in page
    assert sorted(p.name for p in (docs / "images").iterdir()) == ["home.png"]
    guide = (docs / "USER_GUIDE.md").read_text()
    assert "1. [Overview](#overview)" in guide and "![Home](images/home.png)" in guide
    assert json.loads((docs / "help.json").read_text())["sections"][0]["id"] == "overview"


def test_docs_search_works_in_a_browser(tmp_path):
    pw = pytest.importorskip("playwright.sync_api")
    doc = {"title": "Help", "version": 1, "sections": [
        {"id": "a", "title": "Exporting", "keywords": ["save", "csv"], "body_md": "Use Export."},
        {"id": "b", "title": "Keyboard", "keywords": ["keys"], "body_md": "Press slash."}]}
    forge.render_docs(doc, tmp_path, tmp_path / "docs", HERE / "design" / "tokens.css")
    try:
        with pw.sync_playwright() as p:
            browser = p.chromium.launch(chromium_sandbox=False)
            page = browser.new_page()
            page.goto((tmp_path / "docs" / "index.html").as_uri() + "?q=csv")
            assert page.is_visible("#a") and not page.is_visible("#b")
            assert page.text_content("#count") == "1 of 2 sections match"
            page.fill("#q", "nothing-matches")
            page.dispatch_event("#q", "input")
            assert page.is_visible("#none")
            page.keyboard.press("Escape")
            assert page.is_visible("#b") and page.input_value("#q") == ""
            browser.close()
    except Exception as exc:          # no browser installed here
        if "Executable doesn't exist" in str(exc):
            pytest.skip("no Chromium for Playwright")
        raise


def test_write_docs_with_claude(tmp_path, fake_claude, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "docs")
    run = make_run(tmp_path, spec=WEB_SPEC + "\nHelp: yes\n")
    forge.ensure_design(run, {"design": {"brief": "house"}})
    (run.root / "outputs" / "ux").mkdir(parents=True)
    (run.root / "outputs" / "ux" / "home-desktop.png").write_bytes(b"png")
    assert forge.write_docs(run, {}, "def make_app(d): pass\n") is None
    call = fake_claude()[0]
    assert {"SPEC.md", "module.py", "design/DESIGN.md", "images/home-desktop.png"} <= set(call["files"])
    assert call["args"][call["args"].index("--permission-mode") + 1] == "acceptEdits"
    docs = run.root / "docs"
    doc = json.loads((docs / "help.json").read_text())
    assert [s["id"] for s in doc["sections"]] == ["overview", "writing-notes", "overview-2"]
    assert (docs / "images" / "home-desktop.png").exists() and (docs / "index.html").exists()
    assert "nope.png" not in (docs / "USER_GUIDE.md").read_text()
    assert run.state["claude_cost_usd"] == pytest.approx(0.5)
    dest = tmp_path / "commit"
    forge.place_help(run, dest)
    assert (dest / "help" / "help.json").exists() and (dest / "help" / "images" / "home-desktop.png").exists()


def test_write_docs_failure_is_a_note(tmp_path, fake_claude, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "error")
    monkeypatch.setenv("FAKE_CLAUDE_ERROR", "Invalid API key")
    run = make_run(tmp_path, spec=WEB_SPEC)
    forge.ensure_design(run, {"design": {"brief": "house"}})
    assert forge.write_docs(run, {}, "") == "no user guide: Invalid API key"
    assert len(fake_claude()) == 1                    # a real error: no retries
    assert not (run.root / "docs").exists() and "deferred" not in run.state


def test_write_docs_waits_for_the_internet(tmp_path, fake_claude, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "error")       # "rate limited": transient
    run = make_run(tmp_path, spec=WEB_SPEC)
    forge.ensure_design(run, {"design": {"brief": "house"}})
    note = forge.write_docs(run, {}, "")
    assert note.startswith("user guide deferred (rate limited)")
    assert len(fake_claude()) == 1 + forge.CLAUDE_RETRIES
    assert run.state["deferred"]["docs"]["reason"] == "rate limited"


def test_no_docs_without_ui_or_when_off(tmp_path, fake_claude):
    run = make_run(tmp_path, spec="# CSV\n")
    forge.ensure_design(run, {})
    assert forge.write_docs(run, {}, "") is None
    run2 = make_run(tmp_path / "b", spec=WEB_SPEC)
    forge.ensure_design(run2, {"design": {"brief": "house"}})
    assert forge.write_docs(run2, {"docs": {"enabled": False}}, "") is None
    assert fake_claude() == []


def test_place_help_only_when_help_is_wanted(tmp_path):
    run = make_run(tmp_path, spec=WEB_SPEC + "\nHelp: no\n")
    (run.root / "docs").mkdir()
    (run.root / "docs" / "help.json").write_text("{}")
    forge.place_help(run, tmp_path / "dest")
    assert not (tmp_path / "dest" / "help").exists()


def test_report_has_the_ux_section(tmp_path):
    plan = {"project": "notes", "summary": "Notes.", "chunks": [{"id": "c1", "title": "app"}]}
    run = make_run(tmp_path, spec=WEB_SPEC, plan=plan, verified={"c1": "X = 1\n"}, state={
        "ux": forge.ux_summary(WEB_REPORT),
        "reviews": [{"source": "claude", "issues": [
            {"chunk": "c1", "description": "The delete button is beside save.", "fix": "Move it.",
             "category": "ux", "severity": "minor"},
            {"chunk": "c1", "description": "A real bug.", "severity": "major"}]}]})
    forge.ensure_design(run, {"design": {"brief": "house"}})
    (run.root / "docs").mkdir()
    (run.root / "docs" / "index.html").write_text("x")
    report = forge.build_report(run, "X = 1\n", ["a note"])
    assert "## User experience (reported, not enforced)" in report
    assert "- [c1] The delete button is beside save. Fix: Move it." in report
    assert "A real bug" not in report.split("## User experience")[1].split("## Notes")[0]
    assert "User guide: docs/index.html" in report
    assert report.index("## User experience") < report.index("## Notes")


# ------------------------------------------------------------ the runner itself

def test_forge_ux_runner_on_a_real_page(tmp_path):
    """forge_ux.py against a small WSGI app in real Chromium with axe-core:
    needs Playwright's Chromium and FORGE_AXE (axe.min.js)."""
    pytest.importorskip("playwright.sync_api")
    axe = os.environ.get("FORGE_AXE", "/opt/axe/axe.min.js")
    if not Path(axe).exists():
        pytest.skip("no axe-core (set FORGE_AXE to axe.min.js)")
    work = tmp_path / "work"
    work.mkdir()
    (work / "module.py").write_text('''
def make_app(data):
    pages = {
        "/": '<html lang="en"><head><title>Home</title></head><body><main><h1>Notes</h1>'
             '<a href="/new">New note</a> <a href="/logout">Log out</a></main></body></html>',
        "/new": '<html><head><title>New</title></head><body><h1>New</h1><input id="t">'
                '<div style="width:900px">wide</div><img src="x.png"></body></html>'}

    def app(environ, start):
        body = pages.get(environ["PATH_INFO"])
        start("200 OK" if body else "404 Not Found", [("Content-Type", "text/html")])
        return [(body or "nope").encode()]
    return app
''')
    out = tmp_path / "out"
    p = subprocess.run([sys.executable, str(HERE / "forge_ux.py"), str(out), "wsgi", "make_app(tmp)",
                        json.dumps([{"name": "Home", "path": "/"}]), "5"],
                       cwd=work, capture_output=True, text=True, timeout=240,
                       env={**os.environ, "FORGE_AXE": axe})
    if "Executable doesn't exist" in p.stderr:
        pytest.skip("no Chromium for Playwright")
    assert p.returncode == 0, p.stderr[-2000:]
    rep = json.loads((out / "ux" / "ux-report.json").read_text())
    by = {pg["path"]: pg for pg in rep["pages"]}
    assert set(by) == {"/", "/new"}                      # /logout is never visited
    assert by["/"]["has_main"] and by["/"]["lang"] == "en" and by["/"]["focusable"] >= 2
    rules = {v["id"] for v in by["/new"]["axe"]}
    assert {"html-has-lang", "image-alt", "label"} <= rules
    assert by["/new"]["horizontal_scroll_mobile"]
    assert (out / "ux" / "home-desktop.png").exists() and (out / "ux" / "new-mobile.png").exists()
    s = forge.ux_summary(rep)
    assert s["pages"] == 2 and "sideways scrolling on a phone: /new" in s["line"]


# ------------------------------------------------------------ end to end

def test_end_to_end_web_project_carries_its_design(tmp_path):
    """A whole run of a spec with `UI: web`: the planner sees the brief, coders
    get its rules, the commit carries design/ and REPORT.md's UX section, and
    a missing Claude only costs the user guide (a note), never the commit."""
    import test_forge as tf
    from conftest import FakeServer
    plan = dict(tf.TWO_CHUNKS, acceptance_ok=True, missing=[])
    srv = FakeServer([
        ("into a plan that", lambda u, b: json.dumps(plan)),
        ("INTERFACE CONTRACT for a module", tf.contract_reply),
        ("You are REVISING", lambda u, b: f"```python\n{tf.C2_FIXED}```"),
        ("precise Python engineer", tf.coder_reply),
        ("You review generated code", lambda u, b: '{"approved": true, "issues": []}'),
    ])
    try:
        cfg = tf.e2e_cfg(tmp_path, srv.url)
        cfg["design"] = {"brief": "house"}
        spec = tmp_path / "boxer.md"
        spec.write_text(tf.SPEC.replace("\n", "\n\nUI: web\nHelp: yes\n", 1))
        run = forge.Run.create(tmp_path / "runs", spec)
        branch = forge.execute(run, cfg)
        assert branch.startswith("forge/boxer-")
        plan_call = next(b for p, b in srv.calls if p == "into a plan that")
        assert "## Design (design/DESIGN.md" in plan_call["messages"][1]["content"]
        assert "## In-app help (required)" in plan_call["messages"][1]["content"]
        coder_call = next(b for p, b in srv.calls if p == "precise Python engineer")
        assert "THIS PROJECT HAS A USER INTERFACE" in coder_call["messages"][0]["content"]
        files = subprocess.run(["git", "-C", str(tmp_path / "repo"), "show", "--stat", "--format=", branch],
                               capture_output=True, text=True).stdout
        assert "projects/boxer/design/DESIGN.md" in files and "projects/boxer/design/tokens.css" in files
        report = (run.root / "REPORT.md").read_text()
        assert "## User experience (reported, not enforced)" in report
        assert "no user guide: Claude is disabled in config" in report
    finally:
        srv.close()


def test_section_headings_never_skip_a_level():
    assert forge.heading_shift("### a\n#### b") == 0
    assert forge.heading_shift("# a\n## b") == 2
    assert forge.heading_shift("no headings") == 0
    assert "<h3>a</h3>" in forge.md_to_html("# a", forge.heading_shift("# a"))
