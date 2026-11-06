"""forge_game - helpers forge puts in every test sandbox for projects in other
languages: games, emulators, mods, 3D models and reverse engineering. Tests use it as
`game` (`import forge_game as game`).

The project's files live in the module as FILES {path: text}; project_dir()
writes them out under /tmp so engines and compilers can work on them. Nothing
here touches the network.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import struct
import subprocess
import sys
import time
import zlib
from pathlib import Path

TMP = Path(os.environ.get("FORGE_TMP", "/tmp"))
VENDOR = Path("/vendor")
PRIVATE = Path("/private")
DEPS = Path("/deps")
DESIGN = Path(os.environ.get("FORGE_DESIGN", "/design"))
OUT = Path(os.environ.get("FORGE_OUT", "/out"))
# Files code never writes (forge renders or supplies them) - the same list
# as forge.py's BINARY_ASSET.
BINARY_ASSET = re.compile(r"\.(png|jpe?g|webp|gif|bmp|tga|dds|ktx2?|svgz|ico|icns|ogg|wav|mp3|"
                          r"flac|opus|glb|gltf|fbx|obj|blend|ttf|otf|woff2?|zip|pck|exe|so|dll)$",
                          re.I)
_project: Path | None = None
try:                       # engines write settings and caches under $HOME
    Path(os.environ.get("HOME", "/tmp/home")).mkdir(parents=True, exist_ok=True)
except OSError:
    pass


# ----------------------------------------------------------------- project

class Files(dict):
    """The project's files, keyed by path relative to the project root. A
    Godot path works too: files()["res://data/world.json"] is
    files()["data/world.json"]."""

    @staticmethod
    def _key(k):
        if isinstance(k, str):
            for prefix in ("res://", "./"):
                if k.startswith(prefix):
                    k = k[len(prefix):]
        return k

    def __getitem__(self, k):
        return super().__getitem__(self._key(k))

    def __contains__(self, k):
        k = self._key(k)
        if super().__contains__(k):
            return True
        # Images are not code: they are never in FILES, but a rendered one is
        # in the project (tests kept asserting "assets/map.png" in files()).
        if isinstance(k, str) and BINARY_ASSET.search(k):
            return rendered_asset(k) is not None
        return False

    def get(self, k, default=None):
        return super().get(self._key(k), default)


def files() -> dict:
    """The project's files: {relative path: text} ("res://..." also works)."""
    mod = sys.modules.get("module") or __import__("module")
    return Files(getattr(mod, "FILES", {}))


def rendered_asset(rel: str) -> Path | None:
    """The rendered file for a project path such as assets/rooms/a.png."""
    assets = Path("/assets")
    adir = os.environ.get("FORGE_ASSET_DIR", "assets").strip("/")
    rel = rel[6:] if rel.startswith("res://") else rel
    if adir and rel.startswith(adir + "/"):
        p = assets / rel[len(adir) + 1:]
        if p.is_file():
            return p
    return None


def project_dir(fresh: bool = False) -> Path:
    """Write the project's files (and any vendored code placed into it) to a
    directory once and return it. fresh=True makes a new copy."""
    global _project
    if _project is not None and not fresh:
        return _project
    d = TMP / f"project{'' if not fresh else '-' + str(time.monotonic_ns())}"
    if d.exists():
        shutil.rmtree(d)
    d.mkdir(parents=True)
    manifest = VENDOR / "manifest.json"
    if manifest.exists():
        # Vendored code the planner placed into the project: {"from", "to"} pairs.
        for item in json.loads(manifest.read_text()):
            for m in item.get("map") or []:
                s, t = VENDOR / item["dir"] / m["from"], d / m["to"]
                if s.is_dir():
                    shutil.copytree(s, t, dirs_exist_ok=True)
                elif s.is_file():
                    t.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy(s, t)
    assets = Path("/assets")
    if assets.is_dir() and any(assets.iterdir()):
        # Images the run rendered, at the spec's "Assets dir" (default assets/).
        adir = d / os.environ.get("FORGE_ASSET_DIR", "assets")
        for a in assets.rglob("*"):
            if a.is_file() and not a.is_symlink() and ".frame" not in a.name:
                t = adir / a.relative_to(assets)
                t.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy(a, t)
    tokens = DESIGN / "tokens.css"
    if tokens.is_file() and not (d / "design" / "tokens.css").exists():
        # The run's design tokens, for web pages the project serves.
        (d / "design").mkdir(exist_ok=True)
        shutil.copy(tokens, d / "design" / "tokens.css")
    skipped = []
    for rel, text in files().items():
        p = d / rel
        if BINARY_ASSET.search(rel):
            # Code cannot write an image: text in a .png's place (a
            # "placeholder") would overwrite the real render copied above.
            skipped.append(rel)
            continue
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    if skipped:
        print(f"forge_game: ignored {len(skipped)} image entr{'y' if len(skipped) == 1 else 'ies'} "
              f"in FILES (images come from forge's renders), e.g. {skipped[0]}", file=sys.stderr)
    if not fresh:
        _project = d
    return d


def private(name: str) -> Path:
    """A file you supplied (a ROM you own, a game's API files): read-only,
    never copied into the project or the commit."""
    p = PRIVATE / name
    assert p.exists(), f"private file {name!r} is not available in this sandbox"
    return p


class Result:
    def __init__(self, code: int, out: str):
        self.returncode, self.out = code, out

    @property
    def ok(self) -> bool:
        return self.returncode == 0

    def __repr__(self):
        return f"Result(returncode={self.returncode}, out={self.out[-300:]!r})"


def run(cmd, cwd=None, timeout: float = 120, env: dict | None = None, check: bool = False,
        input: str | None = None) -> Result:
    """Run a command, return Result(returncode, out) with stdout+stderr. With
    check=True a failure raises AssertionError showing the output's tail."""
    e = dict(os.environ)
    e.setdefault("HOME", str(TMP / "home"))
    Path(e["HOME"]).mkdir(parents=True, exist_ok=True)
    e.update(env or {})
    try:
        p = subprocess.run(cmd, cwd=cwd, env=e, capture_output=True, text=True,
                           timeout=timeout, input=input)
        r = Result(p.returncode, (p.stdout or "") + (p.stderr or ""))
    except subprocess.TimeoutExpired as exc:
        # The output so far, stderr included: Godot prints its errors there,
        # and a run that hangs after an error showed only its banner.
        def text(b):
            return (b or b"").decode(errors="replace") if isinstance(b, bytes) else (b or "")
        r = Result(124, text(exc.stdout) + text(exc.stderr) + f"\nTIMEOUT after {timeout}s")
    if check:
        assert r.ok, f"{' '.join(map(str, cmd))} failed (exit {r.returncode}):\n{r.out[-3000:]}"
    return r


# ------------------------------------------------------------------ Godot

GODOT = os.environ.get("GODOT", "godot")
SCRIPT_ERRORS = re.compile(r"(SCRIPT ERROR|Parse Error|ERROR: .*?(?:parse|compil|script|assign))", re.I)


def godot(*args, timeout: float = 120, project: Path | None = None,
          quit_after: int | None = None) -> Result:
    """godot --headless --path <project> <args>. quit_after: stop after that
    many main-loop iterations even if nothing calls quit() - a script that
    fails to load otherwise leaves Godot running until the timeout."""
    p = project or project_dir()
    extra = ["--quit-after", str(int(quit_after))] if quit_after else []
    return run([GODOT, "--headless", "--path", str(p), *extra, *map(str, args)], timeout=timeout)


def _ensure_imported(p: Path) -> None:
    """class_name scripts and textures need the import cache (.godot/)."""
    if not (p / ".godot").exists():
        godot("--import", project=p, timeout=300)


def godot_boot_report(frames: int = 180, timeout: float = 240) -> list[dict]:
    """The whole game, as a player would start it: import every scene and
    script, then run the main scene for `frames` main-loop iterations.
    Returns each distinct script error with the file it is in:
    [{"file": "scripts/room_view.gd", "line": 297, "error": "..."}].
    Image import noise is left out; everything else counts - this is the
    finished game, so nothing is "a later chunk's file" any more."""
    p = project_dir()
    out = godot_import(timeout=timeout).out
    if MAIN_SCENE.search((p / "project.godot").read_text(errors="replace")
                         if (p / "project.godot").exists() else ""):
        out += "\n" + godot(timeout=timeout, quit_after=frames).out
    lines = out.splitlines()
    seen, found = set(), []
    for i, ln in enumerate(lines):
        if not SCRIPT_ERRORS.search(ln) or (GODOT_NOISE.search(ln) and "SCRIPT ERROR" not in ln):
            continue
        where = None
        for nxt in lines[i + 1:i + 4]:
            m = re.search(r"\(res://([\w./@+-]+?\.(?:gd|tscn|tres|cs|gdshader)):(\d+)\)", nxt)
            if m and "__forge" not in m.group(1):
                where = (m.group(1), int(m.group(2)))
                break
        if where is None:
            m = re.search(r"res://([\w./@+-]+?\.(?:gd|tscn|tres|gdshader))", ln)
            where = (m.group(1), None) if m else (None, None)
        msg = re.sub(r"^\s*(USER )?SCRIPT ERROR:\s*|^\s*ERROR:\s*", "", ln).strip()
        key = (where[0], msg)
        if key in seen:
            continue
        seen.add(key)
        found.append({"file": where[0], "line": where[1], "error": msg})
    return found


def godot_import(timeout: float = 180) -> Result:
    """Import the project (scenes, scripts, resources) and report its errors.
    Assert `not game.script_errors(r.out)` to fail on any script error."""
    return godot("--import", timeout=timeout)


def _later_paths(p: Path | None) -> set[str]:
    """res:// paths project.godot names (autoloads, the main scene) that are
    not in the project yet - a later chunk writes them."""
    if p is None or not (p / "project.godot").is_file():
        return set()
    text = (p / "project.godot").read_text(errors="replace")
    named = set(re.findall(r'"\*?(res://[^"]+)"', text))
    return {n for n in named if not (p / n[6:]).exists()}


def script_errors(out: str, strict: bool = False) -> list[str]:
    """Script errors in Godot's output. Not counted (strict=False): errors
    only about an autoload or main scene project.godot names but a later
    chunk writes, and image-import noise - every early-chunk test in
    rose-city tripped on those and needed an audit to drop the check."""
    errs = [ln for ln in out.splitlines() if SCRIPT_ERRORS.search(ln)]
    if strict:
        return errs
    later = _later_paths(_project)
    keep = []
    for ln in errs:
        if GODOT_NOISE.search(ln) and not re.search(r"SCRIPT ERROR|Parse Error", ln):
            continue
        paths = {q.rstrip(".,'\"") for q in re.findall(r"res://[\w/.@+-]+", ln)}
        if paths and paths <= later:
            continue
        keep.append(ln)
    return keep


def gd_indent(code: str, depth: int = 1) -> str:
    """GDScript statements re-indented with tabs, `depth` levels in. Code
    written inside a Python string usually comes indented with spaces;
    putting a tab in front of that is "Mixed use of tabs and spaces", a
    parse error that made every godot_eval of such a test return nothing."""
    import textwrap
    lines = textwrap.dedent(code.expandtabs(4)).splitlines()
    widths = [len(ln) - len(ln.lstrip(" ")) for ln in lines if ln.strip()]
    unit = min([w for w in widths if w > 0], default=4)
    out = []
    for ln in lines:
        if not ln.strip():
            out.append("")
            continue
        w = len(ln) - len(ln.lstrip(" "))
        out.append("\t" * (depth + w // unit) + " " * (w % unit) + ln.lstrip(" "))
    return "\n".join(out)


GODOT_EVAL_ITERATIONS = 2000      # main-loop iterations before a stuck eval quits

# Lines in Godot's output that are about something other than the code under
# test: images still rendering (or text "placeholders" in their place), a main
# scene a later chunk writes, the import pass. They buried the real error in
# rose-city's failures ("Failed loading resource: res://main.tscn").
GODOT_NOISE = re.compile(
    r"(Not a PNG file|Error loading image|Failed loading resource: res://[\w/.-]+\.(?:png|jpe?g|webp"
    r"|svg|ogg|wav|mp3)|Failed loading resource: res://main\.tscn|Can't open file from path"
    r" 'res://[\w/.-]+\.(?:png|jpe?g|webp)'|No loader found for resource: res://[\w/.-]+\."
    r"(?:png|jpe?g|webp)|Error importing|at: (?:_load|load_image|import|_reimport)|"
    r"^\s*$|^Godot Engine v)", re.I)


def fix_eval_code(code: str) -> tuple[str, str, list[str]]:
    """Mend the GDScript slips tests keep making in godot_eval code, so a
    test's own typo does not cost a coder attempt and a 10-40 minute audit
    (every one of these was seen in rose-city's tests). Returns (statements
    for _initialize, functions for the class body, what was changed)."""
    import textwrap
    notes = []
    lines = textwrap.dedent(code.expandtabs(4)).splitlines()
    # 1. `func name(...):` at the top level of the snippet cannot live inside
    #    _initialize: move it (with its body) to the class.
    body, funcs, i = [], [], 0
    while i < len(lines):
        ln = lines[i]
        if re.match(r"^(?:static\s+)?func\s+\w+\s*\(", ln):
            block = [ln]
            i += 1
            while i < len(lines) and (not lines[i].strip() or lines[i][:1] in (" ", "\t")):
                block.append(lines[i])
                i += 1
            funcs.append(gd_indent("\n".join(block).rstrip(), 0))   # tabs, like the rest
            notes.append(f"moved `{ln.strip()[:60]}` out of the statements to the script")
            continue
        body.append(ln)
        i += 1
    text = "\n".join(body)
    # 2a. The harness declares `result`; a test that declares it again is a
    #     parse error ("result" redeclared).
    new = re.sub(r"^(\s*)var\s+result\s*(?::\s*\w+\s*)?(:?=)", lambda m: m.group(1) + "result =",
                 text, flags=re.M)
    if new != text:
        notes.append("`var result` -> `result` (the harness declares it)")
        text = new
    # 2b. `var x := <Variant>` is a warning a project may treat as an error
    #     ("The variable type is being inferred from a Variant value"): the
    #     test's own variables need no inferred types.
    new = re.sub(r"^(\s*var\s+\w+\s*):=", r"\1=", text, flags=re.M)
    if new != text:
        notes.append("`var x :=` -> `var x =` (no inferred types in test code)")
        text = new
    # 2. Python's `assert x, "msg"` -> assert(x, "msg")
    def _assert(m):
        notes.append("rewrote a Python-style assert as assert(...)")
        return f"{m.group(1)}assert({m.group(2).rstrip()})"
    text = re.sub(r"^(\s*)assert\s+(?!\()(.+)$", _assert, text, flags=re.M)
    # 3. A lambda in a variable is called with .call(): skey(x) -> skey.call(x)
    lambdas = set(re.findall(r"\bvar\s+(\w+)\s*(?::\s*Callable\s*)?:?=\s*func\b", text))
    for name in sorted(lambdas):
        new = re.sub(rf"(?<![\w.]){re.escape(name)}\s*\(", f"{name}.call(", text)
        if new != text:
            notes.append(f"lambda `{name}` is called with {name}.call(...)")
            text = new
    return text, "\n\n".join(funcs), notes


# Run before the test's statements: a SceneTree script has no get_tree() of
# its own - tests copy it from node code.
_EVAL_SHIMS = """
func get_tree() -> SceneTree:
\treturn self

func add_child(node: Node, readable: bool = false) -> void:
\troot.add_child(node, readable)

func remove_child(node: Node) -> void:
\troot.remove_child(node)

func get_node(path: NodePath) -> Node:
\treturn root.get_node(path)

func get_node_or_null(path: NodePath) -> Node:
\treturn root.get_node_or_null(path)

func has_node(path: NodePath) -> bool:
\treturn root.has_node(path)
"""


def _run_eval(p: Path, body: str, funcs: str, mode: str, timeout: float) -> "Result":
    """mode "frame" (default): the statements run at the first frame, once
    the root is in the tree - so root.add_child(node) runs node._ready() and
    autoload nodes are there (root.get_node("Game")). In _initialize the
    root is not in the tree yet, which is why rose-city's tests saw no
    _ready(). mode "initialize": the old way, at start-up."""
    if mode == "initialize":
        main = ("\nfunc _initialize():\n\tvar result = null\n" + body +
                "\n\tprint(\"FORGE_RESULT:\" + JSON.stringify(result))\n\tquit()\n")
    else:
        main = ("\nfunc _initialize():\n\tprocess_frame.connect(_forge_run, CONNECT_ONE_SHOT)\n"
                "\nfunc _forge_run():\n\tprint(\"FORGE_EVAL_STARTED\")\n\tvar result = null\n"
                + body + "\n\tprint(\"FORGE_RESULT:\" + JSON.stringify(result))\n\tquit()\n")
    (p / "__forge_eval.gd").write_text(
        "extends SceneTree\n" + _EVAL_SHIMS + ("\n" + funcs + "\n" if funcs else "") + main)
    return godot("-s", "res://__forge_eval.gd", timeout=timeout, quit_after=GODOT_EVAL_ITERATIONS)


EVAL_FAULT = re.compile(r"(?:Parse|Compile) Error[^\n]*\n\s*at: GDScript::reload "
                        r"\(res://__forge_eval\.gd")


def eval_script_fault(out: str) -> bool:
    return bool(EVAL_FAULT.search(out or ""))


def eval_failure_text(out: str, errs: list[str]) -> str:
    """What a failed godot_eval says: the script errors first (with where
    they are), then Godot's output with the known noise removed."""
    head = ""
    # Only an error whose own location is the eval script - a chunk's script
    # failing to compile also shows __forge_eval.gd, in its backtrace (rose-city
    # c8: inventory_bar.gd's parse error was blamed on the test).
    if eval_script_fault(out):
        head = ("THE TEST'S OWN GDSCRIPT (the code it passes to godot_eval, run as "
                "res://__forge_eval.gd) does not parse - this is the test's error, not the "
                "chunk's:\n")
    kept = [ln for ln in out.splitlines() if not GODOT_NOISE.search(ln)]
    return (head + ("Script errors:\n" + "\n".join(errs[:20]) + "\n\n" if errs else "")
            + "Godot's output (image/main-scene import noise removed):\n"
            + "\n".join(kept)[-2500:])
MAIN_SCENE = re.compile(r'^\s*(?:run/)?main_scene\s*=\s*"([^"]*)"', re.M)


def missing_main_scene(p: Path) -> str | None:
    """The main scene project.godot names, when that file is not in the
    project yet (a later chunk writes it). Godot loads the main scene even
    for a -s script, fails, and exits before the script runs."""
    try:
        m = MAIN_SCENE.search((p / "project.godot").read_text(errors="replace"))
    except OSError:
        return None
    if not m or not m.group(1).startswith("res://"):
        return None                    # none set, or a uid:// path forge cannot check
    return None if (p / m.group(1)[len("res://"):]).exists() else m.group(1)


def godot_eval(code: str, timeout: float = 120):
    """Run GDScript statements inside the project and return what they put in
    `result` (any JSON value), e.g.
        game.godot_eval("var p = load('res://player.gd').new()\\nresult = p.speed")
    The statements run synchronously at start-up (no frames pass): use
    godot_frames for anything that needs the game loop. Script errors Godot
    reports while they run are printed, so a wrong value comes with its cause."""
    p = project_dir()
    _ensure_imported(p)
    stmts, funcs, fixed = fix_eval_code(code)
    if fixed:
        print("godot_eval: mended the test's GDScript: " + "; ".join(dict.fromkeys(fixed)),
              file=sys.stderr)
    body = gd_indent(stmts)
    mode = os.environ.get("FORGE_GODOT_EVAL", "frame")
    # override.cfg (read at start-up, beside project.godot) runs no main
    # scene: the statements are a unit test, and a main scene a later chunk
    # writes made Godot stop before they ran.
    override = p / "override.cfg"
    ours = not override.exists()
    if ours:
        override.write_text('[application]\n\nrun/main_scene=""\n')
    try:
        r = _run_eval(p, body, funcs, mode, timeout)
        if mode == "frame" and "FORGE_RESULT:" not in r.out and "FORGE_EVAL_STARTED" not in r.out \
                and not script_errors(r.out, strict=True):
            # The first-frame run never started (an engine that does not
            # emit process_frame to a -s script?): the old way, once.
            print("godot_eval: the first-frame run did not start - running the statements "
                  "in _initialize instead", file=sys.stderr)
            r = _run_eval(p, body, funcs, "initialize", timeout)
    finally:
        if ours:
            override.unlink(missing_ok=True)
    errs = script_errors(r.out)
    if errs:
        # The test sees only the value; without this the coder never learns
        # e.g. that assigning an untyped Array to an Array[String] failed.
        print("godot_eval: Godot reported script errors:\n" + "\n".join(errs[:20]),
              file=sys.stderr)
    m = re.findall(r"FORGE_RESULT:(.*)", r.out)
    assert m, "godot_eval produced no result.\n" + eval_failure_text(r.out, errs)
    return json.loads(m[-1])


_INPUT_AUTOLOAD = """extends Node
# Added by forge: plays scripted input and fixes the random seed.
var _events = []
func _ready():
\tprocess_priority = -1000
\tseed(%d)
\tvar f = FileAccess.open("res://__forge_inputs.json", FileAccess.READ)
\tif f:
\t\t_events = JSON.parse_string(f.get_as_text())
func _process(_delta):
\tvar n = Engine.get_process_frames()
\tfor e in _events:
\t\tif int(e.get("frame", -1)) == n:
\t\t\tif e.get("pressed", true):
\t\t\t\tInput.action_press(e["action"])
\t\t\telse:
\t\t\t\tInput.action_release(e["action"])
"""


def godot_frames(scene: str | None = None, frames: int = 60, fps: int = 30,
                 inputs: list | None = None, size=(640, 360), seed: int = 0,
                 timeout: float = 300) -> list[Path]:
    """Run the game for `frames` frames at a fixed frame rate (the same frames
    every run) with scripted input, and return the frames as PNG paths.
    inputs: [{"frame": 10, "action": "ui_right", "pressed": true}, ...]."""
    src = project_dir()
    _ensure_imported(src)
    p = TMP / "project-movie"
    if p.exists():
        shutil.rmtree(p)
    shutil.copytree(src, p)
    (p / "__forge_input.gd").write_text(_INPUT_AUTOLOAD % int(seed))
    (p / "__forge_inputs.json").write_text(json.dumps(inputs or []))
    proj = p / "project.godot"
    text = proj.read_text() if proj.exists() else "config_version=5\n"
    line = 'ForgeInput="*res://__forge_input.gd"\n'
    if re.search(r"^\[autoload\]\s*$", text, re.M):
        text = re.sub(r"^\[autoload\]\s*$\n", "[autoload]\n\n" + line, text, count=1, flags=re.M)
    else:
        text += "\n[autoload]\n\n" + line
    proj.write_text(text)
    out = TMP / "frames"
    if out.exists():
        shutil.rmtree(out)
    out.mkdir()
    w, h = size
    cmd = ["xvfb-run", "-a", "-s", f"-screen 0 {w}x{h}x24", GODOT, "--path", str(p),
           "--rendering-driver", "opengl3", "--resolution", f"{w}x{h}",
           "--fixed-fps", str(fps), "--write-movie", str(out / "frame.png"),
           "--quit-after", str(frames)]
    if scene:
        cmd.append(scene)
    r = run(cmd, timeout=timeout)
    shots = sorted(out.glob("frame*.png"))
    assert shots, f"godot wrote no frames (exit {r.returncode}):\n{r.out[-3000:]}"
    return shots


# --------------------------------------------------------- any X11 program

def x11_frames(cmd, at=(1.0,), size=(640, 480), cwd=None, env: dict | None = None,
               settle: float = 0.0) -> list[Path]:
    """Start a virtual screen, run `cmd` on it and grab the whole screen at
    each time in `at` (seconds after start). For SDL, LOVE, Java and other
    programs that open a window. Times are wall-clock, so prefer
    godot_frames where it applies; here, check what a frame shows, not the
    exact pixels of an animation."""
    w, h = size
    disp = f":{90 + os.getpid() % 100}"
    xvfb = subprocess.Popen(["Xvfb", disp, "-screen", "0", f"{w}x{h}x24", "-nolisten", "tcp"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    shots = []
    out = TMP / "x11frames"
    out.mkdir(exist_ok=True)
    try:
        time.sleep(0.8)
        e = dict(os.environ, DISPLAY=disp, SDL_AUDIODRIVER="dummy", HOME=str(TMP / "home"))
        e.update(env or {})
        Path(e["HOME"]).mkdir(parents=True, exist_ok=True)
        proc = subprocess.Popen(cmd, cwd=cwd, env=e, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True)
        t0 = time.monotonic()
        for i, t in enumerate(sorted(at)):
            time.sleep(max(0.0, t - (time.monotonic() - t0)))
            f = out / f"shot{i:03d}.png"
            subprocess.run(["import", "-display", disp, "-window", "root", f"PNG32:{f}"],
                           capture_output=True, timeout=30)
            if f.exists():
                shots.append(f)
        proc.terminate()
        try:
            log = proc.communicate(timeout=5)[0]
        except subprocess.TimeoutExpired:
            proc.kill()
            log = ""
        assert shots, f"no screenshots taken; program output:\n{(log or '')[-3000:]}"
        return shots
    finally:
        xvfb.terminate()


def love_frames(at=(1.0,), size=(800, 600)) -> list[Path]:
    """Run a LOVE game from the project and grab frames."""
    return x11_frames(["love", str(project_dir())], at=at, size=size)


# --------------------------------------------------------- C, Rust, .NET

def cmake_build(target: str | None = None, config: str = "Release", timeout: float = 600) -> Path:
    """Configure and build the project with CMake; returns the build dir."""
    b = TMP / "build"
    run(["cmake", "-S", str(project_dir()), "-B", str(b), f"-DCMAKE_BUILD_TYPE={config}"],
        timeout=timeout, check=True)
    run(["cmake", "--build", str(b), "-j", "4"] + (["--target", target] if target else []),
        timeout=timeout, check=True)
    return b


def cargo(*args, timeout: float = 900) -> Result:
    """cargo <args>, offline, against crates forge fetched beforehand."""
    home = TMP / "cargo-home"
    home.mkdir(parents=True, exist_ok=True)
    if (DEPS / "config.toml").exists():
        shutil.copy(DEPS / "config.toml", home / "config.toml")
    env = {"CARGO_HOME": str(home), "CARGO_TARGET_DIR": str(TMP / "target"),
           "CARGO_NET_OFFLINE": "true"}
    return run(["cargo", *map(str, args), "--offline"], cwd=project_dir(),
               timeout=timeout, env=env)


def dotnet(*args, timeout: float = 600) -> Result:
    env = {"DOTNET_CLI_HOME": str(TMP / "home"), "DOTNET_CLI_TELEMETRY_OPTOUT": "1",
           "DOTNET_NOLOGO": "1", "NUGET_PACKAGES": str(TMP / "nuget")}
    return run(["dotnet", *map(str, args)], cwd=project_dir(), timeout=timeout, env=env)


# ------------------------------------------------------ reverse engineering

def rizin(commands: str, binary, timeout: float = 120) -> Result:
    """rizin -q -c '<commands>' <binary>, e.g. game.rizin('aaa; afl', path)."""
    return run(["rizin", "-q", "-c", commands, str(binary)], timeout=timeout)


def objdump(binary, *args, timeout: float = 60) -> Result:
    return run(["objdump", *map(str, args), str(binary)], timeout=timeout)


def ghidra(binary, script: str | None = None, *script_args, timeout: float = 900) -> Result:
    """Analyse a binary with Ghidra headless and optionally run a script from
    the project's ghidra_scripts/ folder (Java or Python)."""
    home = Path(os.environ.get("GHIDRA_INSTALL_DIR", "/opt/ghidra"))
    proj = TMP / "ghidra"
    proj.mkdir(exist_ok=True)
    cmd = [str(home / "support" / "analyzeHeadless"), str(proj), "forge",
           "-import", str(binary), "-overwrite", "-deleteProject"]
    if script:
        cmd += ["-scriptPath", str(project_dir() / "ghidra_scripts"), "-postScript", script,
                *map(str, script_args)]
    return run(cmd, timeout=timeout, env={"GHIDRA_USER_HOME": str(TMP / "home")})


def rea(*args, timeout: float = 900):
    """REA (rea-agents, github.com/morluto/rea) on the command line, offline.
    With "--json" among the arguments, returns the parsed JSON (raising with
    REA's output if it is not JSON); otherwise the Result. E.g.
        game.rea("analyze-javascript-application", str(path), "--json")
    `rea --help` lists its other analyses."""
    env = {"REA_GHIDRA_STARTUP_TIMEOUT_MS": str(int(timeout * 1000)),
           "XDG_CACHE_HOME": str(TMP / "home" / ".cache"),
           "XDG_CONFIG_HOME": str(TMP / "home" / ".config"),
           "XDG_DATA_HOME": str(TMP / "home" / ".local" / "share")}
    r = run(["rea", *map(str, args)], timeout=timeout, env=env)
    if "--json" not in args:
        return r
    text = r.out.strip()
    start = min([i for i in (text.find("{"), text.find("[")) if i >= 0] or [-1])
    assert start >= 0, f"rea gave no JSON (exit {r.returncode}):\n{r.out[-3000:]}"
    return json.JSONDecoder().raw_decode(text[start:])[0]


# --------------------------------------------------- emulator differential replay

def emulator_replay(original, candidate, trace, emulator, args=(), timeout: float = 120,
                    normalize_json: bool = True) -> dict:
    """Replay one deterministic input trace against original and recovered binaries.

    `emulator` and `args` are argv vectors, never shell strings. Each argv token
    may contain {binary} and {trace}; the trace is mounted/read-only by the
    caller's emulator wrapper. The wrapper should exit nonzero on emulator
    failure and emit the observable result (state digest, output, or transcript)
    on stdout. JSON stdout is canonicalized before comparison when requested.
    No binary is launched directly by this helper; the configured emulator is
    the only execution path.
    """
    original, candidate, trace = map(lambda x: str(Path(x).resolve()),
                                     (original, candidate, trace))
    for label, path in (("original", original), ("candidate", candidate), ("trace", trace)):
        assert Path(path).is_file(), f"{label} file does not exist: {path}"
    assert isinstance(emulator, (list, tuple)) and emulator, "emulator must be a non-empty argv list"
    assert isinstance(args, (list, tuple)), "args must be an argv list"
    def invoke(binary):
        argv = [str(x).format(binary=binary, trace=trace) for x in [*emulator, *args]]
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout,
                              stdin=subprocess.DEVNULL, env=dict(os.environ,
                              SDL_AUDIODRIVER="dummy", SDL_VIDEODRIVER="dummy"))
        out = proc.stdout.strip()
        if proc.returncode:
            raise AssertionError(f"emulator failed for {binary} (exit {proc.returncode}): "
                                 f"{proc.stderr[-2000:]}\n{out[-2000:]}")
        if normalize_json:
            try:
                out = json.dumps(json.loads(out), sort_keys=True, separators=(",", ":"))
            except (ValueError, TypeError):
                pass
        return {"returncode": proc.returncode, "stdout": out,
                "stderr": proc.stderr.strip()}
    baseline, recovered = invoke(original), invoke(candidate)
    same = baseline["stdout"] == recovered["stdout"]
    return {"pass": same, "original": baseline, "candidate": recovered,
            "difference": None if same else "observable emulator output differs"}


def emulator_assert_equivalent(original, candidate, trace, emulator, args=(), timeout: float = 120):
    """Assertion-shaped wrapper convenient for forge chunk verification tests."""
    result = emulator_replay(original, candidate, trace, emulator, args, timeout)
    assert result["pass"], json.dumps(result, indent=2)[:6000]
    return result


# --------------------------------------------------------- Godot UI audit

def godot_ui_audit() -> dict:
    """Facts about a Godot project's interface, read from its files (no
    engine): each Input Map action's devices, whether settings are saved
    (ConfigFile + user://), whether any menu sets keyboard focus
    (grab_focus), and the smallest font size set anywhere. forge's UX check
    reports them; game-ui.md says why they matter."""
    fs = files()
    proj = fs.get("project.godot", "")
    actions: dict = {}
    m = re.search(r"^\[input\]\s*$(.*?)(?=^\[|\Z)", proj, re.S | re.M)
    if m:
        for a in re.finditer(r"^([A-Za-z_][\w./-]*)\s*=\s*\{(.*?)^\}", m.group(1), re.S | re.M):
            kinds = set(re.findall(r"Object\((InputEvent\w+)", a.group(2)))
            actions[a.group(1)] = {
                "keyboard": "InputEventKey" in kinds,
                "gamepad": bool(kinds & {"InputEventJoypadButton", "InputEventJoypadMotion"}),
                "mouse": "InputEventMouseButton" in kinds}
    code = "\n".join(t for path, t in fs.items() if path.endswith(".gd"))
    scenes = "\n".join(t for path, t in fs.items() if path.endswith((".tscn", ".tres", ".gd")))
    sizes = [int(x) for x in re.findall(r"font_size\w*\s*[=:]\s*(\d+)", scenes)]
    vw = re.search(r"viewport_width\s*=\s*(\d+)", proj)
    vh = re.search(r"viewport_height\s*=\s*(\d+)", proj)
    return {"actions": actions,
            "actions_without_gamepad": sorted(n for n, d in actions.items() if not d["gamepad"]),
            "actions_without_keyboard": sorted(n for n, d in actions.items() if not d["keyboard"]),
            "settings_saved": "ConfigFile" in code and "user://" in code,
            "sets_focus": "grab_focus(" in code,
            "smallest_font_size": min(sizes) if sizes else None,
            "viewport": [int(vw.group(1)), int(vh.group(1))] if vw and vh else None}


# ------------------------------------------------------- shared helpers
#
# s&box, Fallout 4 and Garry's Mod run against game files forge mounts
# read-only (the s&box install, the Fallout 4 install and its extracted
# script sources, the Garry's Mod dedicated server). Engines and compilers
# want to write next to those files, so each gets a folder under /tmp that
# mirrors the install: real directories, symlinked files.


def mirror_tree(src: Path, dst: Path) -> Path:
    """dst mirrors src with real directories and symlinked files; links in
    src are recreated with the same target (relative links stay relative).
    Returns dst. Writing a NEW file anywhere in dst works; overwriting a
    mirrored file does not (it is the read-only original)."""
    src, dst = Path(src), Path(dst)
    for root, dirs, files in os.walk(src):
        here = Path(root)
        rel = here.relative_to(src)
        (dst / rel).mkdir(parents=True, exist_ok=True)
        for name in list(dirs) + files:
            s, t = here / name, dst / rel / name
            if s.is_symlink():
                if name in dirs:
                    dirs.remove(name)
                if not os.path.lexists(t):
                    os.symlink(os.readlink(s), t)
            elif name in files and not os.path.lexists(t):
                os.symlink(s, t)
    return dst


def _make_real(path: Path) -> None:
    """Replace a mirrored (symlinked) file with a writable copy."""
    path = Path(path)
    if path.is_symlink():
        target = path.resolve()
        path.unlink()
        if target.is_file():
            shutil.copy2(target, path)
            os.chmod(path, 0o644 | (os.stat(path).st_mode & 0o111))


class Checks:
    """Named checks reported by code running inside an engine.
    .ok is True when there is at least one check and every one passed."""

    def __init__(self, checks: list, values: dict | None = None, errors: list | None = None,
                 out: str = ""):
        self.checks, self.values, self.errors, self.out = checks, values or {}, errors or [], out

    @property
    def ok(self) -> bool:
        return bool(self.checks) and all(c.get("ok") for c in self.checks) and not self.errors

    @property
    def failed(self) -> list:
        return [c for c in self.checks if not c.get("ok")]

    def check(self, name: str) -> dict | None:
        return next((c for c in self.checks if c.get("name") == name), None)

    def assert_ok(self) -> "Checks":
        problems = [f"FAIL {c.get('name')}" + (f" ({c.get('detail')})" if c.get("detail") else "")
                    for c in self.failed]
        problems += [f"ERROR {e}" for e in self.errors]
        if not self.checks:
            problems.append("no checks were reported")
        assert not problems, "\n".join(problems) + f"\n--- output:\n{self.out[-3000:]}"
        return self

    def __repr__(self):
        return f"Checks(ok={self.ok}, checks={self.checks!r}, errors={self.errors!r})"


# ---------------------------------------------------------- .NET offline

NUGET_CACHE = Path(os.environ.get("FORGE_NUGET_CACHE", "/opt/forge-nuget"))
CS_ERROR = re.compile(r"\berror\s+(?:CS|NU|MSB|NETSDK)\d+\s*:.*")


def cs_errors(out: str) -> list[str]:
    """C#, NuGet and MSBuild errors in build output, each once."""
    seen, res = set(), []
    for ln in out.splitlines():
        m = CS_ERROR.search(ln)
        if m:
            # dotnet prints each error twice (with and without the project).
            key = re.sub(r"\s*\[[^\]]*\.csproj\]\s*$", "", ln.strip())
            if key not in seen:
                seen.add(key)
                res.append(key)
    return res


def _nuget_env() -> dict:
    """NuGet with no sources: packages come only from the image's cache
    (restored when the image was built). A symlink mirror under /tmp is the
    packages folder, so restore can write its own small files."""
    home = TMP / "home"
    cfg = home / ".nuget" / "NuGet" / "NuGet.Config"
    if not cfg.exists():
        cfg.parent.mkdir(parents=True, exist_ok=True)
        cfg.write_text('<?xml version="1.0" encoding="utf-8"?>\n<configuration>\n'
                       '  <packageSources>\n    <clear />\n  </packageSources>\n</configuration>\n')
    pkgs = TMP / "nuget"
    if not pkgs.exists():
        if NUGET_CACHE.is_dir():
            mirror_tree(NUGET_CACHE, pkgs)
        else:
            pkgs.mkdir(parents=True)
    return {"DOTNET_CLI_HOME": str(home), "HOME": str(home), "NUGET_PACKAGES": str(pkgs),
            "DOTNET_CLI_TELEMETRY_OPTOUT": "1", "DOTNET_NOLOGO": "1",
            "DOTNET_SKIP_FIRST_TIME_EXPERIENCE": "1", "DOTNET_GENERATE_ASPNET_CERTIFICATE": "false",
            "MSBUILDDISABLENODEREUSE": "1", "DOTNET_CLI_USE_MSBUILD_SERVER": "0",
            "DOTNET_CLI_UI_LANGUAGE": "en"}


# Keep the build to one process tree that ends with it: no compiler server,
# no MSBuild nodes left behind (the sandbox caps processes).
DOTNET_FLAGS = ["-nodeReuse:false", "-p:UseSharedCompilation=false"]


def trx_results(path: Path) -> list[dict]:
    """Test results from a .trx file: [{"name", "outcome", "message"}]."""
    import xml.etree.ElementTree as ET
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError):
        return []
    out = []
    for el in root.iter():
        if el.tag.endswith("UnitTestResult"):
            msg = ""
            for sub in el.iter():
                if sub.tag.endswith("Message") and sub.text:
                    msg = sub.text.strip()
                    break
            out.append({"name": el.get("testName"), "outcome": el.get("outcome"),
                        "message": msg})
    return out


# ------------------------------------------------------------------ s&box
#
# s&box code compiles against the install's managed assemblies (bin/managed)
# with s&box's source generator, as its own project generator sets it up,
# and runs under MSTest. The engine starts headless for scene tests: its
# Windows-only check for bin/win64/engine2.dll is met by an empty
# placeholder in a mirror of the install, native libraries load from
# bin/linuxsteamrt64, and SDL uses its offscreen video driver.

SBOX = Path(os.environ.get("FORGE_SBOX", "/sbox"))
SBOX_TEST_PACKAGES = (("Microsoft.NET.Test.Sdk", "17.12.0"), ("MSTest.TestAdapter", "3.6.4"),
                      ("MSTest.TestFramework", "3.6.4"))
SBOX_EXTRA_REFS = ("Facepunch.ActionGraphs.dll", "SkiaSharp.dll", "Microsoft.CodeAnalysis.dll",
                   "Microsoft.CodeAnalysis.CSharp.dll")
SBOX_ENV = {"SDL_VIDEODRIVER": "offscreen", "SDL_VIDEO_DRIVER": "offscreen",
            "SDL_AUDIODRIVER": "dummy", "SDL_AUDIO_DRIVER": "dummy"}


def sbox_csproj(name: str, compile_items: list[str], managed: Path | None) -> str:
    """A test project the way s&box's generator writes one: net10.0, C# 14,
    MSTest, the managed assemblies as references and the generator as an
    analyzer. managed=None leaves the s&box parts out (the image uses that
    to fill its package cache)."""
    refs = analyzers = ""
    if managed is not None:
        for f in sorted(Path(managed).glob("Sandbox.*.dll")):
            if f.name in ("Sandbox.Generator.dll", "Sandbox.CodeUpgrader.dll"):
                continue
            refs += f'\t\t<Reference Include="{f}" />\n'
        for extra in SBOX_EXTRA_REFS:
            if (Path(managed) / extra).exists():
                refs += f'\t\t<Reference Include="{Path(managed) / extra}" />\n'
        for a in ("Sandbox.CodeUpgrader.dll", "Sandbox.Generator.dll"):
            if (Path(managed) / a).exists():
                analyzers += f'\t\t<Analyzer Include="{Path(managed) / a}" />\n'
    pkgs = "".join(f'\t\t<PackageReference Include="{n}" Version="{v}" />\n'
                   for n, v in SBOX_TEST_PACKAGES)
    comp = "".join(f'\t\t<Compile Include="{c}" />\n' for c in compile_items)
    return f"""<Project Sdk="Microsoft.NET.Sdk.Razor">
\t<PropertyGroup>
\t\t<TargetFramework>net10.0</TargetFramework>
\t\t<AssemblyName>{name}</AssemblyName>
\t\t<LangVersion>14</LangVersion>
\t\t<Nullable>disable</Nullable>
\t\t<RootNamespace>Sandbox</RootNamespace>
\t\t<IsTestProject>true</IsTestProject>
\t\t<AppendTargetFrameworkToOutputPath>false</AppendTargetFrameworkToOutputPath>
\t\t<OutputPath>bin/</OutputPath>
\t\t<NoWarn>1701;1702;1591;CS1591</NoWarn>
\t</PropertyGroup>
\t<ItemGroup>
{pkgs}\t</ItemGroup>
\t<ItemGroup>
{analyzers}{refs}\t</ItemGroup>
\t<ItemGroup>
{comp}\t</ItemGroup>
</Project>
"""


def sbox_global_usings(managed: Path | None) -> str:
    """The namespaces s&box code expects without `using` lines (s&box's own
    projects have them as global usings). The static GlobalGameNamespace
    (Log and friends) only when the install's assemblies define it."""
    lines = ["global using System;", "global using System.Collections.Generic;",
             "global using System.Linq;", "global using Sandbox;"]
    if managed is not None:
        for f in sorted(Path(managed).glob("Sandbox.*.dll")):
            try:
                if b"GlobalGameNamespace" in f.read_bytes():
                    lines.append("global using static Sandbox.Internal.GlobalGameNamespace;")
                    break
            except OSError:
                continue
    return "\n".join(lines) + "\n"


def sbox_engine_dir() -> Path:
    """A writable stand-in for the s&box install (FACEPUNCH_ENGINE)."""
    e = TMP / "engine"
    marker = e / "bin" / "win64" / "engine2.dll"
    if marker.exists():
        return e
    assert (SBOX / "bin" / "managed").is_dir(), \
        f"the s&box install is not mounted at {SBOX} (set [games] sbox in forge's config.toml)"
    (e / "bin" / "win64").mkdir(parents=True, exist_ok=True)
    for x in SBOX.iterdir():
        n = x.name
        if n == "bin":
            continue
        if n in ("logs", ".source2", "download"):
            (e / n).mkdir(exist_ok=True)
        elif n == "config" and x.is_dir():
            shutil.copytree(x, e / n, symlinks=True, dirs_exist_ok=True)
        elif not os.path.lexists(e / n):
            os.symlink(x, e / n)
    for x in (SBOX / "bin").iterdir():
        if x.name != "win64" and not os.path.lexists(e / "bin" / x.name):
            os.symlink(x, e / "bin" / x.name)
    marker.write_bytes(b"")          # what TestAppSystem's Windows-only check looks for
    return e


def _sbox_env() -> dict:
    env = _nuget_env()
    env.update(SBOX_ENV)
    env["FACEPUNCH_ENGINE"] = str(sbox_engine_dir())
    env["LD_LIBRARY_PATH"] = ":".join(p for p in (str(SBOX / "bin" / "linuxsteamrt64"),
                                                  os.environ.get("LD_LIBRARY_PATH", "")) if p)
    return env


_SBOX_INIT = """
[TestClass]
public class ForgeEngine
{
\tpublic static TestAppSystem AppSystem;

\t[AssemblyInitialize]
\tpublic static void Start( TestContext context )
\t{
\t\tAppSystem = new TestAppSystem();
\t\tAppSystem.Init();
\t}

\t[AssemblyCleanup]
\tpublic static void Stop() => AppSystem?.Shutdown();
}
"""


def _sbox_run_tests(test_files: dict[str, str], code_globs: list[str], engine: bool,
                    timeout: float) -> tuple[Result, list[dict]]:
    proj = project_dir()
    d = TMP / f"sbox-tests-{time.monotonic_ns()}"
    d.mkdir(parents=True)
    items = [str(proj / g) for g in code_globs]
    (d / "ForgeTests.csproj").write_text(sbox_csproj("ForgeTests", items, SBOX / "bin" / "managed"))
    for name, text in test_files.items():
        (d / name).write_text(text)
    (d / "ForgeGlobalUsings.cs").write_text(sbox_global_usings(SBOX / "bin" / "managed"))
    if engine:
        (d / "ForgeEngine.cs").write_text(
            "using Microsoft.VisualStudio.TestTools.UnitTesting;\nusing Sandbox;\n" + _SBOX_INIT)
    res = d / "results"
    env = _sbox_env()
    t0 = time.monotonic()
    b = run(["dotnet", "build", *DOTNET_FLAGS], cwd=str(d), timeout=timeout, env=env)
    if not b.ok:
        return b, []
    left = max(60.0, timeout - (time.monotonic() - t0))
    r = run(["dotnet", "test", "--no-build", "--blame-hang-timeout", f"{int(max(30, left - 30))}s",
             "--results-directory", str(res), "--logger", "trx;LogFileName=results.trx",
             "--logger", "console;verbosity=normal"],
            cwd=str(d), timeout=left, env=env)
    return Result(r.returncode, b.out + "\n" + r.out), trx_results(res / "results.trx")


def sbox_build(code_globs=("Code/**/*.cs",), timeout: float = 600) -> Result:
    """Compile the project's code against s&box (no tests). Assert
    `not game.cs_errors(r.out)`."""
    proj = project_dir()
    d = TMP / f"sbox-build-{time.monotonic_ns()}"
    d.mkdir(parents=True)
    (d / "ForgeBuild.csproj").write_text(
        sbox_csproj("ForgeBuild", [str(proj / g) for g in code_globs], SBOX / "bin" / "managed")
        .replace("<IsTestProject>true</IsTestProject>", "<IsTestProject>false</IsTestProject>"))
    (d / "ForgeGlobalUsings.cs").write_text(sbox_global_usings(SBOX / "bin" / "managed"))
    return run(["dotnet", "build", *DOTNET_FLAGS], cwd=str(d), timeout=timeout, env=_sbox_env())


def _cs_ident(name: str) -> str:
    s = re.sub(r"\W", "_", str(name))
    return ("T_" + s) if not s or not s[0].isalpha() else s


def sbox_eval(code, engine: bool = True, code_globs=("Code/**/*.cs",), timeout: float = 900):
    """Run C# inside s&box and get back what it puts in `result` (as JSON).
    `code` is one snippet, or {name: snippet} to run several in one build
    (returns {name: value}). With engine=True (the default) the engine is
    started headless and each snippet runs inside a fresh Scene, `scene`:

        game.sbox_eval('var go = new GameObject();\\n'
                       'var h = go.Components.Create<Health>();\\n'
                       'h.Hurt( 30 );\\nresult = h.Current;')

    Return plain data (numbers, strings, bools, arrays, dictionaries,
    anonymous objects). A compile error or an exception in a snippet raises
    AssertionError with the message."""
    single = isinstance(code, str)
    snippets = {"result": code} if single else dict(code)
    outdir = TMP / f"sbox-results-{time.monotonic_ns()}"
    outdir.mkdir(parents=True)
    methods = []
    for name, body in snippets.items():
        ident = _cs_ident(name)
        inner = "\n".join("\t\t\t\t" + ln for ln in str(body).splitlines())
        scene_open = ("\t\t\tvar scene = new Scene();\n\t\t\tusing ( scene.Push() )\n\t\t\t{\n"
                      if engine else "\t\t\t{\n")
        methods.append(f"""
\t[TestMethod]
\tpublic void Eval_{ident}()
\t{{
\t\tobject result = null;
\t\ttry
\t\t{{
{scene_open}{inner}
\t\t\t}}
\t\t\tForgeOut.Write( "{ident}", result, null );
\t\t}}
\t\tcatch ( System.Exception e )
\t\t{{
\t\t\tForgeOut.Write( "{ident}", null, e.ToString() );
\t\t\tthrow;
\t\t}}
\t}}""")
    test = f"""using System;
using System.Linq;
using System.Collections.Generic;
using Microsoft.VisualStudio.TestTools.UnitTesting;
using Sandbox;

static class ForgeOut
{{
\tpublic static void Write( string name, object value, string error )
\t{{
\t\tstring json;
\t\ttry
\t\t{{
\t\t\tjson = error != null
\t\t\t\t? System.Text.Json.JsonSerializer.Serialize( new Dictionary<string, object> {{ ["__error"] = error }} )
\t\t\t\t: System.Text.Json.JsonSerializer.Serialize( new Dictionary<string, object> {{ ["value"] = value }},
\t\t\t\t\tnew System.Text.Json.JsonSerializerOptions {{ IncludeFields = true, MaxDepth = 16 }} );
\t\t}}
\t\tcatch ( System.Exception e )
\t\t{{
\t\t\tjson = System.Text.Json.JsonSerializer.Serialize( new Dictionary<string, object>
\t\t\t\t{{ ["__error"] = "result is not plain data (" + e.Message + "): return numbers, strings, arrays or anonymous objects" }} );
\t\t}}
\t\tSystem.IO.File.WriteAllText( System.IO.Path.Combine( {json.dumps(str(outdir))}, name + ".json" ), json );
\t}}
}}

[TestClass]
public class ForgeEval
{{{"".join(methods)}
}}
"""
    r, results = _sbox_run_tests({"ForgeEval.cs": test}, list(code_globs), engine, timeout)
    values = {}
    for name in snippets:
        f = outdir / f"{_cs_ident(name)}.json"
        if not f.exists():
            errs = cs_errors(r.out)
            raise AssertionError(
                ("s&box code did not compile:\n" + "\n".join(errs[:30])) if errs else
                f"snippet {name!r} produced no result (exit {r.returncode}):\n{r.out[-3000:]}")
        data = json.loads(f.read_text())
        if "__error" in data:
            raise AssertionError(f"snippet {name!r} raised:\n{data['__error'][:3000]}")
        values[name] = data.get("value")
    return values["result"] if single else values


def sbox_test(test_globs=("UnitTests/**/*.cs",), code_globs=("Code/**/*.cs",), engine: bool = True,
              timeout: float = 900) -> Checks:
    """Run the project's own MSTest tests (UnitTests/ by default) against
    its code. Returns Checks: one per test, .ok, .assert_ok()."""
    proj = project_dir()
    files = {}
    for g in test_globs:
        for p in sorted(proj.glob(g)):
            files[f"t{len(files)}_{p.name}"] = p.read_text()
    assert files, f"no test files match {list(test_globs)}"
    r, results = _sbox_run_tests(files, list(code_globs), engine, timeout)
    checks = [{"name": t["name"], "ok": t["outcome"] == "Passed",
               "detail": t["message"] or (None if t["outcome"] == "Passed" else t["outcome"])}
              for t in results]
    errors = cs_errors(r.out) if not results else []
    if not results and not errors:
        errors = [f"dotnet test reported no results (exit {r.returncode})"]
    return Checks(checks, errors=errors, out=r.out)


# ------------------------------------------------------------ Garry's Mod
#
# The dedicated server (Steam app 4020) runs headless with no network: the
# project is copied in as an addon, a test addon runs Lua inside the game
# once the map has loaded, writes what it found to data/, and the server
# is killed (srcds ignores polite stop signals).

GMOD = Path(os.environ.get("FORGE_GMOD", "/gmod-server"))
GMOD_WRITABLE = ("cfg", "data", "cache", "download", "downloadlists", "settings", "addons")
GMOD_COPY_MAX = 8 * 2**20        # files up to this size are copied, larger ones linked
# A Lua error on the server console: "[ERROR] lua/x.lua:3: attempt to ...",
# or the same line tagged with the addon's name instead of [ERROR].
GMOD_ERROR = re.compile(r"^\s*(?:\[ERROR\]\s*(.+)|(?:\[[^\]]+\]\s*)?"
                        r"((?:[\w./-]+\.lua|forge_test):\d+:\s.+))$")


def _mirror_top(src: Path, dst: Path, skip=()) -> None:
    """One level: directories linked, small files copied, big files linked."""
    dst.mkdir(parents=True, exist_ok=True)
    for x in src.iterdir():
        if x.name in skip or os.path.lexists(dst / x.name):
            continue
        if x.is_file() and not x.is_symlink() and x.stat().st_size <= GMOD_COPY_MAX:
            shutil.copy2(x, dst / x.name)
        else:
            os.symlink(x, dst / x.name)


def gmod_server_dir() -> Path:
    """A writable copy of the dedicated server's layout under /tmp."""
    d = TMP / "gmod"
    if (d / ".forge-ready").exists():
        return d
    assert (GMOD / "garrysmod").is_dir(), \
        f"the Garry's Mod dedicated server is not mounted at {GMOD} " \
        f"(install-forge-toolchains.sh gmod installs it)"
    _mirror_top(GMOD, d, skip={"garrysmod"})
    g = d / "garrysmod"
    _mirror_top(GMOD / "garrysmod", g, skip=set(GMOD_WRITABLE))
    for w in GMOD_WRITABLE:
        src = GMOD / "garrysmod" / w
        if w in ("cfg", "settings") and src.is_dir():
            shutil.copytree(src, g / w, symlinks=False, dirs_exist_ok=True)
        else:
            (g / w).mkdir(exist_ok=True)
    (d / ".forge-ready").write_text("")
    return d


def _lua_long_string(text: str) -> str:
    level = 0
    while f"]{'=' * level}]" in text:
        level += 1
    eq = "=" * level
    return f"[{eq}[\n{text}]{eq}]"


GMOD_HARNESS = r"""-- forge test harness (runs on the server once the map has loaded)
FORGE = FORGE or {}
local results, values, finished = {}, {}, false
function FORGE.check(name, ok, detail)
	results[#results + 1] = {name = tostring(name), ok = ok and true or false,
		detail = detail ~= nil and tostring(detail) or nil}
end
function FORGE.value(name, v) values[tostring(name)] = v end
function FORGE.finish()
	if finished then return end
	finished = true
	file.Write("forge_test.txt", util.TableToJSON({checks = results, values = values,
		map = game.GetMap()}))
	print("FORGE_TEST_DONE")
end
local code = %(code)s
hook.Add("InitPostEntity", "forge_test", function()
	timer.Simple(%(delay)s, function()
		local fn = CompileString(code, "forge_test", false)
		if isstring(fn) then
			FORGE.check("test code compiles", false, fn)
			FORGE.finish()
			return
		end
		local ok, err = xpcall(fn, debug.traceback)
		if not ok then
			FORGE.check("test code ran", false, err)
			FORGE.finish()
		elseif not FORGE.async then
			FORGE.finish()
		end
	end)
end)
timer.Simple(%(limit)s, function()
	FORGE.check("finished in time", false, "FORGE.finish() was not called within %(limit)s s")
	FORGE.finish()
end)
"""


def gmod_run(lua: str, map: str = "gm_construct", timeout: float = 240, delay: float = 1.0,
             limit: float = 120, args=()) -> Checks:
    """Start the dedicated server with the project installed as an addon,
    run `lua` on the server once the map has loaded, and return Checks.
    In the Lua, FORGE.check(name, ok, detail) records a check and
    FORGE.value(name, v) a value (.values in Python). Set FORGE.async = true
    and call FORGE.finish() yourself to keep going after timers or hooks:

        r = game.gmod_run('''
            local e = ents.Create("prop_physics")
            e:SetModel("models/props_c17/oildrum001.mdl"); e:Spawn()
            FORGE.check("spawned", IsValid(e))
        ''')
        r.assert_ok()

    Lua errors printed by the server ([ERROR] lines) are in .errors and
    make .ok False. Only server and shared code runs - there is no client."""
    srv = gmod_server_dir()
    g = srv / "garrysmod"
    for sub in ("addons/forge_project", "addons/forge_test"):
        shutil.rmtree(g / sub, ignore_errors=True)
    res_file = g / "data" / "forge_test.txt"     # file.Write allows only some extensions
    if res_file.exists():
        res_file.unlink()
    shutil.copytree(project_dir(), g / "addons" / "forge_project", symlinks=False)
    h = g / "addons" / "forge_test" / "lua" / "autorun" / "server"
    h.mkdir(parents=True)
    (g / "addons" / "forge_test" / "addon.json").write_text(
        json.dumps({"title": "forge test harness", "type": "tool", "tags": ["build"], "ignore": []}))
    (h / "forge_test.lua").write_text(GMOD_HARNESS % {
        "code": _lua_long_string(lua), "delay": float(delay), "limit": float(limit)})
    log_path = TMP / f"gmod-{time.monotonic_ns()}.log"
    env = dict(os.environ, HOME=str(TMP / "home"))
    Path(env["HOME"]).mkdir(parents=True, exist_ok=True)
    cmd = ["./srcds_run", "-game", "garrysmod", "-console", "-norestart", "-port", "27015",
           "+sv_lan", "1", "+maxplayers", "2", "+sv_hibernate_think", "1", *map_args(map), *args]
    with open(log_path, "w") as fh:
        proc = subprocess.Popen(cmd, cwd=str(srv), env=env, stdout=fh, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL, start_new_session=True)
        data = None
        t0 = time.monotonic()
        try:
            while time.monotonic() - t0 < timeout:
                if res_file.exists():
                    try:
                        data = json.loads(res_file.read_text())
                        break
                    except ValueError:
                        pass                     # still being written
                if proc.poll() is not None:
                    break
                time.sleep(0.5)
        finally:
            try:
                os.killpg(proc.pid, 9)
            except (ProcessLookupError, PermissionError):
                pass
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                pass
    out = log_path.read_text(errors="replace")
    errors = gmod_errors(out)
    if data is None:
        why = ("the server exited" if proc.returncode not in (None, -9) else
               f"no result within {timeout:.0f} s")
        return Checks([], errors=errors + [f"{why}; the test addon wrote nothing"], out=out)
    checks = data.get("checks") or []
    if isinstance(checks, dict):             # util.TableToJSON turns {} into an object
        checks = list(checks.values())
    return Checks(checks, values=data.get("values") or {}, errors=errors, out=out)


def map_args(name: str) -> list[str]:
    return ["+map", str(name)]


def gmod_errors(out: str) -> list[str]:
    """Lua errors from server output: each [ERROR] line with its stack."""
    lines, res, i = out.splitlines(), [], 0
    while i < len(lines):
        m = GMOD_ERROR.match(lines[i])
        if m:
            block = [(m.group(1) or m.group(2)).strip()]
            j = i + 1
            while j < len(lines) and re.match(r"^\s+\d+\.\s", lines[j]) and len(block) < 8:
                block.append(lines[j].strip())
                j += 1
            entry = "\n".join(block)
            if entry not in res:
                res.append(entry)
            i = j
            continue
        i += 1
    return res


GLUALINT = os.environ.get("GLUALINT", "glualint")
LINT_LINE = re.compile(r"^(?P<file>[^:]+):\s*\[(?P<level>error|warning)\]\s*(?P<msg>.*)$", re.I)


def glualint(path=None, timeout: float = 120) -> Result:
    """glualint (GLuaFixer, GLua's own syntax: != && || ! //) over the
    project's lua/ folder, or `path`. game.lint_errors(r.out) lists the
    syntax errors, game.lint_warnings(r.out) the style warnings."""
    target = Path(path) if path else project_dir() / "lua"
    if not target.exists():
        target = project_dir()
    return run([GLUALINT, "lint", str(target)], timeout=timeout)


def lint_errors(out: str) -> list[str]:
    return [ln.strip() for ln in out.splitlines()
            if (m := LINT_LINE.match(ln.strip())) and m.group("level").lower() == "error"]


def lint_warnings(out: str) -> list[str]:
    return [ln.strip() for ln in out.splitlines()
            if (m := LINT_LINE.match(ln.strip())) and m.group("level").lower() == "warning"]


# --------------------------------------------------------------- Fallout 4
#
# Papyrus: the Creation Kit's compiler is .NET and runs under Mono; it then
# calls PapyrusAssembler.exe, a native Windows program, which runs under
# Wine. A copy of the compiler folder gets a small stand-in assembler that
# does exactly that. Plugins: built by a C# program using Mutagen (from the
# image's package cache), read back with the plain-Python reader below.

FO4 = Path(os.environ.get("FORGE_FO4", "/fo4"))
FO4_SCRIPTS = Path(os.environ.get("FORGE_FO4_SCRIPTS", "/fo4-scripts"))
WINE_TEMPLATE = Path(os.environ.get("FORGE_WINE_TEMPLATE", "/opt/wineprefix"))
MUTAGEN_VERSION = os.environ.get("FORGE_MUTAGEN_VERSION", "0.54.4")
PAPYRUS_ERROR = re.compile(r"^(?P<file>.+?\.psc)\((?P<line>\d+),(?P<col>\d+)\):\s*(?P<msg>.+)$", re.I)
FO4_DLC_MASTERS = ("DLCRobot.esm", "DLCworkshop01.esm", "DLCCoast.esm", "DLCworkshop02.esm",
                   "DLCworkshop03.esm", "DLCNukaWorld.esm")


def _find_ci(root: Path, name: str, depth: int = 3) -> Path | None:
    name = name.lower()
    for dp, dirs, files in os.walk(root):
        if len(Path(dp).relative_to(root).parts) >= depth:
            dirs[:] = []
        for f in files:
            if f.lower() == name:
                return Path(dp) / f
    return None


def wine_env() -> dict:
    """A Wine prefix under /tmp: a mirror of the one built into the image,
    with its registry files real so Wine can update them."""
    prefix = TMP / "wine"
    if not (prefix / "system.reg").exists():
        assert WINE_TEMPLATE.is_dir(), f"no Wine prefix template at {WINE_TEMPLATE}"
        mirror_tree(WINE_TEMPLATE, prefix)
        for f in list(prefix.glob("*.reg")) + [prefix / ".update-timestamp"]:
            _make_real(f)
        os.chmod(prefix, 0o755)
    return {"WINEPREFIX": str(prefix), "WINEDEBUG": "-all", "WINEDLLOVERRIDES": "mscoree,mshtml=",
            "HOME": str(TMP / "home")}


def papyrus_compiler() -> Path:
    """A copy of the Creation Kit's Papyrus compiler folder whose
    PapyrusAssembler.exe is a stand-in that runs the real one under Wine."""
    shim = TMP / "papyrus-compiler"
    if (shim / "PapyrusCompiler.exe").exists():
        return shim / "PapyrusCompiler.exe"
    real = _find_ci(FO4, "PapyrusCompiler.exe")
    assert real, (f"PapyrusCompiler.exe not found under {FO4} - install the Creation Kit "
                  f"(set [games] fallout4 in forge's config.toml)")
    shim.mkdir(parents=True, exist_ok=True)
    asm = None
    for f in real.parent.iterdir():
        if f.name.lower() == "papyrusassembler.exe":
            asm = f
            continue
        if f.is_file():
            shutil.copy2(f, shim / f.name)
    assert asm, f"PapyrusAssembler.exe not found beside {real}"
    import shlex
    (shim / "PapyrusAssembler.exe").write_text(
        "#!/bin/bash\n# forge: run the real (Windows) assembler under Wine.\n"
        f"exec wine {shlex.quote(str(asm))} \"$@\"\n")
    os.chmod(shim / "PapyrusAssembler.exe", 0o755)
    return shim / "PapyrusCompiler.exe"


def papyrus_imports() -> list[Path]:
    """Script source folders the compiler imports from: Base, then the DLCs."""
    if not FO4_SCRIPTS.is_dir():
        return []
    dirs = sorted((p for p in FO4_SCRIPTS.iterdir() if p.is_dir()),
                  key=lambda p: (p.name.lower() != "base", p.name.lower()))
    return [d for d in dirs if any(d.glob("*.psc")) or any(d.glob("*.flg"))]


def papyrus_source_root() -> Path:
    """Scripts/Source/User in the project (the Creation Kit's layout)."""
    root = project_dir() / "Scripts" / "Source" / "User"
    if root.is_dir():
        return root
    pscs = sorted(project_dir().rglob("*.psc"), key=lambda p: len(p.parts))
    assert pscs, "the project has no .psc files"
    return pscs[0].parent


class Papyrus(Result):
    """A Papyrus compile: .ok, .out, .errors [{"file","line","col","message"}],
    .pex (compiled files)."""

    def __init__(self, code: int, out: str, errors: list, pex: list):
        super().__init__(code, out)
        self.errors, self.pex = errors, pex

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.errors


def papyrus_errors(out: str) -> list[dict]:
    res = []
    for ln in out.splitlines():
        m = PAPYRUS_ERROR.match(ln.strip())
        if m:
            e = {"file": m.group("file"), "line": int(m.group("line")), "col": int(m.group("col")),
                 "message": m.group("msg").strip()}
            if e not in res:
                res.append(e)
    return res


def papyrus(scripts=None, release: bool = False, final: bool = False, optimize: bool = False,
            timeout: float = 600) -> Papyrus:
    """Compile the project's Papyrus scripts (Scripts/Source/User/**.psc, or
    the given paths relative to it) to .pex files in Scripts/. Namespaced
    scripts live in subfolders: ScriptName MyMod:Quests:Tracker is
    Scripts/Source/User/MyMod/Quests/Tracker.psc."""
    src = papyrus_source_root()
    out_dir = project_dir() / "Scripts"
    out_dir.mkdir(parents=True, exist_ok=True)
    comp = papyrus_compiler()
    imports = [src] + papyrus_imports()
    flags = _find_ci(FO4_SCRIPTS, "Institute_Papyrus_Flags.flg") if FO4_SCRIPTS.is_dir() else None
    assert flags, (f"Institute_Papyrus_Flags.flg not found under {FO4_SCRIPTS} - "
                   f"install-forge-toolchains.sh fo4 extracts the base scripts there")
    if flags.parent not in imports:
        imports.append(flags.parent)
    files = ([str(s) for s in scripts] if scripts else
             [str(p.relative_to(src)) for p in sorted(src.rglob("*.psc"))])
    assert files, "no .psc files to compile"
    env = wine_env()
    mode = (["-release"] if release else []) + (["-final"] if final else []) + \
           (["-optimize"] if optimize else [])
    outs, worst = [], 0
    for f in files:
        r = run(["mono", str(comp), f, f"-f={flags.name}",
                 "-i=" + ";".join(str(i) for i in imports), f"-o={out_dir}", *mode],
                cwd=str(src), timeout=timeout, env=env)
        outs.append(f"--- {f} (exit {r.returncode})\n" + "\n".join(
            ln for ln in r.out.splitlines()
            if not re.search(r"runtime version supported|Using default runtime", ln)))
        worst = worst or r.returncode
    out = "\n".join(outs)
    return Papyrus(worst, out, papyrus_errors(out), sorted(out_dir.rglob("*.pex")))


def pex_header(path) -> dict:
    """A compiled script's header: magic (0xFA57C0DE for Fallout 4), version,
    game id (2), compile time, and the source/user/machine names."""
    b = Path(path).read_bytes()
    magic = struct.unpack_from("<I", b, 0)[0]
    pos, strs = 16, []
    for _ in range(3):
        n = struct.unpack_from("<H", b, pos)[0]
        pos += 2
        strs.append(b[pos:pos + n].decode("latin-1"))
        pos += n
    return {"magic": magic, "ok": magic == 0xFA57C0DE, "major": b[4], "minor": b[5],
            "game": struct.unpack_from("<H", b, 6)[0], "compiled": struct.unpack_from("<Q", b, 8)[0],
            "source": strs[0], "user": strs[1], "machine": strs[2], "size": len(b)}


def _plugin_env() -> dict:
    """Mutagen writes plugins only once it can read a load order: an
    AppData folder of its own with plugins.txt (both spellings - Linux is
    case-sensitive) listing the DLC masters this install has."""
    lad = TMP / "localappdata"
    d = lad / "Fallout4"
    if not (d / "plugins.txt").exists():
        d.mkdir(parents=True, exist_ok=True)
        data = FO4 / "Data"
        lines = ["# written by forge"] + [f"*{m}" for m in FO4_DLC_MASTERS if (data / m).exists()]
        for name in ("plugins.txt", "Plugins.txt"):
            (d / name).write_text("\n".join(lines) + "\n")
    env = _nuget_env()
    env.update({"LocalAppData": str(lad), "FORGE_FO4_DATA": str(FO4 / "Data")})
    return env


def mutagen_csproj(name: str) -> str:
    return f"""<Project Sdk="Microsoft.NET.Sdk">
\t<PropertyGroup>
\t\t<OutputType>Exe</OutputType>
\t\t<TargetFramework>net10.0</TargetFramework>
\t\t<AssemblyName>{name}</AssemblyName>
\t\t<Nullable>disable</Nullable>
\t\t<ImplicitUsings>disable</ImplicitUsings>
\t\t<NoWarn>$(NoWarn);CS1591</NoWarn>
\t</PropertyGroup>
\t<ItemGroup>
\t\t<PackageReference Include="Mutagen.Bethesda.Fallout4" Version="{MUTAGEN_VERSION}" />
\t</ItemGroup>
</Project>
"""


def esp_build(folder: str = "Plugin", timeout: float = 900) -> Path:
    """Build the project's plugin: run the C# program in Plugin/ (Program.cs
    using Mutagen) as `Program <out folder> <Fallout 4 Data folder>`; it
    writes the .esp/.esl/.esm into the out folder. forge writes the folder's
    .csproj (net10.0, Mutagen.Bethesda.Fallout4 from the image's cache).
    Returns the plugin's path."""
    d = project_dir() / folder
    assert d.is_dir() and any(d.glob("*.cs")), f"{folder}/ has no C# files"
    for old in d.glob("*.csproj"):
        old.unlink()
    (d / f"{Path(folder).name}.csproj").write_text(mutagen_csproj(Path(folder).name))
    out = TMP / f"plugin-out-{time.monotonic_ns()}"
    out.mkdir(parents=True)
    env = _plugin_env()
    b = run(["dotnet", "build", str(d), "-c", "Release", *DOTNET_FLAGS], cwd=str(d),
            timeout=timeout, env=env)
    if not b.ok:
        errs = cs_errors(b.out)
        raise AssertionError(("the plugin program did not compile:\n" + "\n".join(errs[:30]))
                             if errs else f"dotnet build failed (exit {b.returncode}):\n{b.out[-3000:]}")
    r = run(["dotnet", "run", "--project", str(d), "-c", "Release", "--no-build", "--",
             str(out), str(FO4 / "Data")], cwd=str(d), timeout=timeout, env=env)
    made = sorted(p for p in out.iterdir() if p.suffix.lower() in (".esp", ".esm", ".esl"))
    if not made:
        errs = cs_errors(r.out)
        raise AssertionError(("the plugin program did not compile:\n" + "\n".join(errs[:30]))
                             if errs else
                             f"the plugin program wrote no .esp/.esm/.esl (exit {r.returncode}):"
                             f"\n{r.out[-3000:]}")
    assert r.ok, f"the plugin program failed (exit {r.returncode}):\n{r.out[-3000:]}"
    return made[0]


# Plain-Python reader for Bethesda plugins (Fallout 4's format): enough to
# check what a plugin holds without the engine.
ESP_COMPRESSED = 0x00040000
ESP_FLAGS = {"master": 0x1, "localized": 0x80, "light": 0x200}


def _subrecords(data: bytes) -> list[tuple[str, bytes]]:
    out, pos, big = [], 0, None
    while pos + 6 <= len(data):
        typ = data[pos:pos + 4].decode("latin-1")
        size = struct.unpack_from("<H", data, pos + 4)[0]
        pos += 6
        if big is not None:
            size, big = big, None
        if typ == "XXXX":
            big = struct.unpack_from("<I", data, pos)[0]
            pos += size
            continue
        out.append((typ, data[pos:pos + size]))
        pos += size
    return out


def _zstring(b: bytes) -> str:
    b = b.split(b"\x00", 1)[0]
    try:
        return b.decode("utf-8")
    except UnicodeDecodeError:
        return b.decode("cp1252", "replace")


def esp_read(path) -> dict:
    """{"header": {...}, "records": [...]} for a .esp/.esm/.esl.
    header: masters, author, description, version, num_records, master,
    light, localized. Each record: type, formid, local_id (the low 24 bits),
    file (the plugin or master it belongs to), override (True when it
    changes a master's record), edid, full (None if localized), flags,
    subs {type: [bytes, ...]} - a LIST per signature, one item per
    subrecord, so the first MODL is subs['MODL'][0] - and group (top
    group's label). Decoded too (layouts from xEdit's wbDefinitionsFO4):
    model (the first MODL's path, or None); for GLOB, glob_type ('s' short,
    'l' long, 'f' float, 'b' bool: FNAM) and value (FLTV, a float - a short
    global's -1 is -1.0); for QUST, quest_flags (DNAM's first uint16;
    0x0001 is Start Game Enabled); scripts (the VMAD's scripts and their
    properties, see vmad_scripts, or None)."""
    raw = Path(path).read_bytes()
    assert raw[:4] == b"TES4", f"{path} is not a Bethesda plugin"
    records: list[dict] = []
    header: dict = {}

    def record(pos: int, group: str | None) -> int:
        typ = raw[pos:pos + 4].decode("latin-1")
        size, flags, formid = struct.unpack_from("<III", raw, pos + 4)
        data = raw[pos + 24:pos + 24 + size]
        if flags & ESP_COMPRESSED:
            data = zlib.decompress(data[4:])
        subs: dict[str, list[bytes]] = {}
        for t, b in _subrecords(data):
            subs.setdefault(t, []).append(b)
        if typ == "TES4":
            hedr = subs.get("HEDR", [b""])[0]
            header.update({
                "version": round(struct.unpack_from("<f", hedr, 0)[0], 3) if len(hedr) >= 4 else None,
                "num_records": struct.unpack_from("<i", hedr, 4)[0] if len(hedr) >= 8 else None,
                "masters": [_zstring(m) for m in subs.get("MAST", [])],
                "author": _zstring(subs["CNAM"][0]) if "CNAM" in subs else None,
                "description": _zstring(subs["SNAM"][0]) if "SNAM" in subs else None,
                "flags": flags, **{k: bool(flags & v) for k, v in ESP_FLAGS.items()}})
        else:
            records.append({"type": typ, "formid": formid, "local_id": formid & 0xFFFFFF,
                            "flags": flags, "group": group, "subs": subs,
                            "edid": _zstring(subs["EDID"][0]) if "EDID" in subs else None,
                            "_full": subs.get("FULL", [None])[0]})
        return pos + 24 + size

    def group(pos: int, label: str | None) -> int:
        size = struct.unpack_from("<I", raw, pos + 4)[0]
        gtype = struct.unpack_from("<i", raw, pos + 12)[0]
        lab = raw[pos + 8:pos + 12].decode("latin-1") if gtype == 0 else label
        end, p = pos + size, pos + 24
        while p < end:
            p = group(p, lab) if raw[p:p + 4] == b"GRUP" else record(p, lab)
        return end

    pos = 0
    while pos < len(raw):
        pos = group(pos, None) if raw[pos:pos + 4] == b"GRUP" else record(pos, None)
    masters = header.get("masters", [])
    me = Path(path).name
    for r in records:
        idx = r["formid"] >> 24
        r["file"] = masters[idx] if idx < len(masters) else me
        r["override"] = idx < len(masters)
        full = r.pop("_full")
        r["full"] = (None if full is None or header.get("localized") else _zstring(full))
        subs = r["subs"]
        r["model"] = _zstring(subs["MODL"][0]) if subs.get("MODL") else None
        if r["type"] == "GLOB":
            fnam = subs.get("FNAM", [b""])[0]
            fltv = subs.get("FLTV", [b""])[0]
            r["glob_type"] = chr(fnam[0]) if fnam else None
            r["value"] = struct.unpack_from("<f", fltv)[0] if len(fltv) >= 4 else None
        if r["type"] == "QUST":
            dnam = subs.get("DNAM", [b""])[0]
            r["quest_flags"] = struct.unpack_from("<H", dnam)[0] if len(dnam) >= 2 else None
        r["scripts"] = None
        if subs.get("VMAD"):
            try:
                r["scripts"] = vmad_scripts(subs["VMAD"][0])
            except (struct.error, ValueError, IndexError):
                r["scripts"] = None
        r["formid_hex"] = f"{r['formid']:08X}"
    return {"header": header, "records": records}


def esp_header(path) -> dict:
    return esp_read(path)["header"]


def esp_records(path, type: str | None = None) -> list[dict]:
    recs = esp_read(path)["records"]
    return [r for r in recs if type is None or r["type"] == type]


def esp_record(path, edid: str) -> dict | None:
    """The record with this EditorID (case-insensitive), or None."""
    return next((r for r in esp_read(path)["records"]
                 if r["edid"] and r["edid"].lower() == edid.lower()), None)


# ----------------------------------------------------------------- Blender

BLENDER = os.environ.get("BLENDER", "blender")
_BLENDER_N = [0]


def _blender_script(script) -> str:
    """A path to a .py file, or code text written to one."""
    s = str(script)
    if "\n" not in s and s.endswith(".py") and Path(s).exists():
        return s
    if "\n" not in s and s.endswith(".py") and (project_dir() / s).exists():
        return str(project_dir() / s)
    _BLENDER_N[0] += 1
    p = TMP / f"forge_blender_{_BLENDER_N[0]}.py"
    p.write_text(s)
    return str(p)


def blender(script, *args, blend=None, timeout: float = 600) -> Result:
    """Run a Python script inside Blender (headless, factory settings):
    `script` is a file (in the project or anywhere) or code text; `args`
    reach it after "--" (sys.argv[sys.argv.index('--') + 1:]); `blend` opens
    a .blend first. A Python error makes .ok False."""
    home = TMP / "home"
    env = {"BLENDER_USER_CONFIG": str(home / "blender" / "config"),
           "BLENDER_USER_SCRIPTS": str(home / "blender" / "scripts"),
           "BLENDER_USER_DATAFILES": str(home / "blender" / "datafiles")}
    cmd = [BLENDER, "-b", "--factory-startup", "-noaudio"]
    if blend:
        cmd.append(str(blend))
    cmd += ["--python-exit-code", "1", "--python", _blender_script(script), "--", *map(str, args)]
    return run(cmd, cwd=str(project_dir()), timeout=timeout, env=env)


_EVAL_WRAP = """import bpy, json, sys
def __forge_eval():
    result = None
%s
    return result
try:
    __r = __forge_eval()
except Exception:
    import traceback
    traceback.print_exc()
    sys.exit(1)
print("FORGE_RESULT:" + json.dumps(__r, default=str))
"""


def blender_eval(code: str, blend=None, timeout: float = 600):
    """Run Python inside Blender and return what it puts in `result` (any
    JSON value), e.g. game.blender_eval("bpy.ops.mesh.primitive_cube_add()\\n"
    "result = len(bpy.context.object.data.vertices)") -> 8."""
    body = "\n".join("    " + ln for ln in code.splitlines()) or "    pass"
    # The project's own packages import inside Blender too.
    head = f"import sys as _fs\n_fs.path.insert(0, {str(project_dir())!r})\n"
    r = blender(head + _EVAL_WRAP % body, blend=blend, timeout=timeout)
    m = re.findall(r"FORGE_RESULT:(.*)", r.out)
    assert m and r.ok, f"blender_eval failed (exit {r.returncode}):\n{r.out[-3000:]}"
    return json.loads(m[-1])


# Loads a model (or .blend) into an empty scene; shared by mesh_info and
# blender_render. Runs inside Blender.
_BLENDER_LOAD = r'''
import bpy, os, sys
def forge_load(path):
    ext = os.path.splitext(path)[1].lower()
    if ext == ".blend":
        bpy.ops.wm.open_mainfile(filepath=path)
        return
    bpy.ops.wm.read_homefile(use_empty=True)
    if ext in (".glb", ".gltf"):
        bpy.ops.import_scene.gltf(filepath=path)
    elif ext == ".obj":
        bpy.ops.wm.obj_import(filepath=path)
    elif ext == ".fbx":
        bpy.ops.import_scene.fbx(filepath=path)
    elif ext == ".stl":
        bpy.ops.wm.stl_import(filepath=path)
    elif ext == ".ply":
        bpy.ops.wm.ply_import(filepath=path)
    elif ext in (".usd", ".usda", ".usdc", ".usdz"):
        bpy.ops.wm.usd_import(filepath=path)
    else:
        raise SystemExit("forge: cannot import " + ext)
def forge_meshes():
    return [o for o in bpy.context.scene.objects if o.type == "MESH"]
'''

_MESH_INFO = _BLENDER_LOAD + r'''
import bmesh, json
from mathutils import Vector
path = sys.argv[sys.argv.index("--") + 1]
forge_load(path)
objs, lo, hi = [], None, None
tot = {"vertices": 0, "edges": 0, "faces": 0, "triangles": 0, "non_manifold_edges": 0,
       "boundary_edges": 0, "loose_vertices": 0}
mats = set()
for o in forge_meshes():
    bm = bmesh.new()
    bm.from_mesh(o.data)
    info = {"name": o.name, "vertices": len(bm.verts), "edges": len(bm.edges),
            "faces": len(bm.faces), "triangles": sum(len(f.verts) - 2 for f in bm.faces),
            "non_manifold_edges": sum(1 for e in bm.edges if not e.is_manifold),
            "boundary_edges": sum(1 for e in bm.edges if e.is_boundary),
            "loose_vertices": sum(1 for v in bm.verts if not v.link_edges),
            "uv_layers": len(o.data.uv_layers),
            "materials": [m.name for m in o.data.materials if m]}
    for v in bm.verts:
        w = o.matrix_world @ v.co
        lo = Vector(w) if lo is None else Vector((min(lo.x, w.x), min(lo.y, w.y), min(lo.z, w.z)))
        hi = Vector(w) if hi is None else Vector((max(hi.x, w.x), max(hi.y, w.y), max(hi.z, w.z)))
    bm.free()
    for k in tot:
        tot[k] += info[k]
    mats.update(info["materials"])
    objs.append(info)
res = dict(tot, objects=objs, mesh_objects=len(objs), materials=sorted(mats),
           watertight=bool(objs) and tot["non_manifold_edges"] == 0,
           bounds=[list(lo), list(hi)] if lo is not None else None,
           dimensions=list(hi - lo) if lo is not None else None)
print("FORGE_RESULT:" + json.dumps(res))
'''


def mesh_info(path, timeout: float = 300) -> dict:
    """Facts about a model (.glb/.gltf/.obj/.fbx/.stl/.ply/.usd or .blend),
    measured in Blender: vertices, edges, faces, triangles,
    non_manifold_edges, boundary_edges, loose_vertices, watertight,
    bounds [[min xyz], [max xyz]], dimensions [x, y, z] (Blender's Z-up,
    metres; glTF's Y-up is converted on import), materials, and per object
    the same plus uv_layers."""
    r = blender(_MESH_INFO, str(Path(path)), timeout=timeout)
    m = re.findall(r"FORGE_RESULT:(.*)", r.out)
    assert m and r.ok, f"mesh_info failed for {path} (exit {r.returncode}):\n{r.out[-3000:]}"
    return json.loads(m[-1])


VIEWS = {"front": (0.0, -1.0, 0.0), "back": (0.0, 1.0, 0.0), "left": (-1.0, 0.0, 0.0),
         "right": (1.0, 0.0, 0.0), "top": (0.0, -0.001, 1.0), "iso": (0.8, -1.0, 0.7)}

_RENDER = _BLENDER_LOAD + r'''
import json, math
from mathutils import Vector
a = sys.argv[sys.argv.index("--") + 1:]
path, outdir, w, h, samples = a[0], a[1], int(a[2]), int(a[3]), int(a[4])
views = json.loads(a[5])
forge_load(path)
scene = bpy.context.scene
meshes = forge_meshes()
if not meshes:
    raise SystemExit("forge: nothing to render - no mesh objects")
pts = [o.matrix_world @ Vector(c) for o in meshes for c in o.bound_box]
lo = Vector((min(p.x for p in pts), min(p.y for p in pts), min(p.z for p in pts)))
hi = Vector((max(p.x for p in pts), max(p.y for p in pts), max(p.z for p in pts)))
center, radius = (lo + hi) / 2, max((hi - lo).length / 2, 1e-3)
for o in [o for o in scene.objects if o.type in ("CAMERA", "LIGHT")]:
    bpy.data.objects.remove(o, do_unlink=True)
target = bpy.data.objects.new("forge_target", None)
target.location = center
scene.collection.objects.link(target)
cam_data = bpy.data.cameras.new("forge_cam")
cam_data.lens = 50
cam_data.clip_end = radius * 100
cam = bpy.data.objects.new("forge_cam", cam_data)
scene.collection.objects.link(cam)
c = cam.constraints.new("TRACK_TO")
c.target, c.track_axis, c.up_axis = target, "TRACK_NEGATIVE_Z", "UP_Y"
scene.camera = cam
sun_data = bpy.data.lights.new("forge_sun", "SUN")
sun_data.energy = 3.0
sun = bpy.data.objects.new("forge_sun", sun_data)
sun.rotation_euler = (math.radians(50), 0, math.radians(30))
scene.collection.objects.link(sun)
world = scene.world or bpy.data.worlds.new("forge_world")
scene.world = world
try:
    world.use_nodes = True
except Exception:
    pass
try:
    bg = next(n for n in world.node_tree.nodes if n.type == "BACKGROUND")
    bg.inputs[0].default_value = (0.6, 0.6, 0.62, 1.0)
    bg.inputs[1].default_value = 1.0
except Exception:
    world.color = (0.6, 0.6, 0.62)
scene.render.engine = "CYCLES"
scene.cycles.device = "CPU"
scene.cycles.samples = samples
try:
    scene.cycles.use_denoising = False
except Exception:
    pass
scene.render.resolution_x, scene.render.resolution_y = w, h
scene.render.resolution_percentage = 100
scene.render.image_settings.file_format = "PNG"
fov = cam_data.angle
dist = radius / math.sin(min(fov, math.radians(80)) / 2) * 1.1
stem = os.path.splitext(os.path.basename(path))[0]
done = []
for name, d in views:
    v = Vector(d).normalized()
    cam.location = center + v * dist
    scene.render.filepath = os.path.join(outdir, stem + "_" + name + ".png")
    bpy.ops.render.render(write_still=True)
    done.append(scene.render.filepath)
print("FORGE_RESULT:" + json.dumps(done))
'''


def blender_render(path, views=("iso",), size=(512, 512), samples: int = 16,
                   timeout: float = 900) -> list[Path]:
    """Render a model (or .blend) with an automatic camera, a sun and a grey
    background (Cycles on the CPU) from each view - front, back, left,
    right, top, iso, or (name, (x, y, z)) for a direction of your own.
    Returns the PNG paths (the spec's Captures put renders in the review)."""
    outdir = TMP / "renders"
    outdir.mkdir(parents=True, exist_ok=True)
    vs = [(v, VIEWS[v]) if isinstance(v, str) else (str(v[0]), tuple(v[1])) for v in views]
    r = blender(_RENDER, str(Path(path)), str(outdir), int(size[0]), int(size[1]), int(samples),
                json.dumps(vs), timeout=timeout)
    m = re.findall(r"FORGE_RESULT:(.*)", r.out)
    assert m and r.ok, f"blender_render failed for {path} (exit {r.returncode}):\n{r.out[-3000:]}"
    shots = [Path(p) for p in json.loads(m[-1])]
    missing = [p for p in shots if not p.exists()]
    assert not missing, f"Blender did not write {missing}:\n{r.out[-2000:]}"
    return shots


def blender_build(script: str = "build.py", out=None, timeout: float = 900) -> Path:
    """Run the project's build script in Blender with `--out <folder>`
    after "--"; it writes what the project makes (models, renders) there.
    Returns the folder."""
    out = Path(out) if out else TMP / "build"
    out.mkdir(parents=True, exist_ok=True)
    r = blender(script, "--out", str(out), timeout=timeout)
    assert r.ok, f"{script} failed in Blender (exit {r.returncode}):\n{r.out[-3000:]}"
    return out


# --------------------------------------------------------------- packaging
#
# What forge puts in outputs/build for the toolchains that compile: the mod
# folder to copy into the game (Fallout 4: Data/ layout), or what a Blender
# project's build script made.

FO4_DATA_DIRS = ("Interface", "Meshes", "Textures", "Sound", "Materials", "Strings", "Music",
                 "Vis", "MCM", "F4SE", "Video")


VMAD_TYPES = {0: "none", 1: "object", 2: "string", 3: "int", 4: "float", 5: "bool",
              6: "var", 7: "struct", 11: "object[]", 12: "string[]", 13: "int[]",
              14: "float[]", 15: "bool[]", 16: "var[]", 17: "struct[]"}


def vmad_scripts(data: bytes) -> list[dict]:
    """The scripts of a VMAD subrecord: [{name, flags, properties: [{name,
    type, flags, value}]}]. Layout from xEdit's wbDefinitionsFO4 (wbVMAD):
    int16 version, int16 object format, uint16 script count; each script a
    uint16-length string (NO null terminator), uint8 flags, uint16 property
    count; each property a uint16-length name, uint8 type, uint8 flags, then
    its value - an object is {formid, alias} (format 2: 2 unused bytes,
    int16 alias, uint32 FormID), arrays have a uint32 count. A quest's
    fragments and aliases after the scripts are not read."""
    pos = [0]

    def take(fmt):
        v = struct.unpack_from("<" + fmt, data, pos[0])
        pos[0] += struct.calcsize("<" + fmt)
        return v if len(v) > 1 else v[0]

    def lstr():
        n = take("H")
        s = data[pos[0]:pos[0] + n]
        pos[0] += n
        return s.decode("utf-8", "replace")

    _, fmt = take("hh")

    def obj():
        if fmt == 1:
            formid, alias, _ = take("Ihh")
        else:
            _, alias, formid = take("hhI")
        return {"formid": formid, "alias": alias}

    def value(t, depth=0):
        if depth > 4:
            raise ValueError("VMAD nests too deep")
        if t in (0, 6):
            return None
        if t == 1:
            return obj()
        if t == 2:
            return lstr()
        if t == 3:
            return take("i")
        if t == 4:
            return take("f")
        if t == 5:
            return bool(take("B"))
        if t == 7:
            return [member(depth + 1) for _ in range(take("I"))]
        if t == 16:
            return {"count": take("I")}
        if t == 17:
            return [[member(depth + 1) for _ in range(take("I"))] for _ in range(take("I"))]
        if 11 <= t <= 15:
            return [value(t - 10, depth + 1) for _ in range(take("I"))]
        raise ValueError(f"unknown VMAD property type {t}")

    def member(depth=0):
        name = lstr()
        t, flags = take("BB")
        return {"name": name, "type": VMAD_TYPES.get(t, t), "flags": flags,
                "value": value(t, depth)}

    scripts = []
    for _ in range(take("H")):
        name = lstr()
        flags = take("B")
        props = [member() for _ in range(take("H"))]
        scripts.append({"name": name, "flags": flags, "properties": props})
    return scripts


# --------------------------------------------------------------- Minecraft

MC = Path(os.environ.get("FORGE_MC", "/opt/mc"))
JAVA_ERROR = re.compile(r"^(?P<file>[^\s:]+\.java):(?P<line>\d+): error: (?P<msg>.*)$", re.M)


def mc_versions() -> dict:
    """What the image was built with: minecraft, paper_build, paper_api,
    mockbukkit, junit, api_version."""
    try:
        return json.loads((MC / "versions.json").read_text())
    except (OSError, ValueError):
        return {}


def mc_classpath(scope: str = "compile") -> list[Path]:
    """The image's jars: "compile" (the Paper API and what it needs) or
    "test" (plus MockBukkit and JUnit)."""
    return sorted((MC / "lib" / scope).glob("*.jar"))


def java_errors(out: str) -> list[str]:
    """javac's errors, each with the line that follows it (the source line)
    and its symbol/location notes, once each."""
    res, seen = [], set()
    lines = (out or "").splitlines()
    for i, ln in enumerate(lines):
        if JAVA_ERROR.match(ln.strip()):
            extra = [x.strip() for x in lines[i + 1:i + 5]
                     if x.strip().startswith(("symbol:", "location:", "required:", "found:", "reason:"))]
            text = " | ".join([ln.strip()] + extra)
            if text not in seen:
                seen.add(text)
                res.append(text)
    return res


def _javac(sources: list[Path], classpath: list[Path], dest: Path, timeout: float) -> Result:
    dest.mkdir(parents=True, exist_ok=True)
    argfile = dest.parent / f"{dest.name}.sources"
    argfile.write_text("\n".join(f'"{p}"' for p in sources))
    cmd = ["javac", "--release", str(mc_java_release()), "-encoding", "UTF-8", "-Xlint:none",
           "-Xmaxerrs", "50", "-d", str(dest)]
    if classpath:
        cmd += ["-cp", os.pathsep.join(map(str, classpath))]
    return run(cmd + ["@" + str(argfile)], timeout=timeout)


def mc_java_release() -> int:
    """The Java release the image's JDK targets (Paper 26.x needs 25)."""
    try:
        r = subprocess.run(["javac", "-version"], capture_output=True, text=True, timeout=60)
        m = re.search(r"javac (\d+)", (r.stdout or "") + (r.stderr or ""))
        return int(m.group(1)) if m else 25
    except (OSError, subprocess.SubprocessError):
        return 25


def mc_plugin_yml(proj: Path | None = None) -> tuple[Path | None, dict]:
    """The project's plugin descriptor (src/main/resources/paper-plugin.yml
    or plugin.yml) and its top-level keys (simple `key: value` lines)."""
    proj = proj or project_dir()
    for name in ("paper-plugin.yml", "plugin.yml"):
        p = proj / "src" / "main" / "resources" / name
        if p.is_file():
            keys = {}
            for ln in p.read_text(errors="replace").splitlines():
                m = re.match(r"^([A-Za-z][\w-]*)\s*:\s*(.*?)\s*$", ln)
                if m and m.group(2):
                    keys[m.group(1)] = m.group(2).strip("'\"")
            return p, keys
    return None, {}


class JavaBuild(Result):
    def __init__(self, code: int, out: str, jar: Path | None, classes: Path):
        super().__init__(code, out)
        self.jar, self.classes = jar, classes
        self.errors = java_errors(out)


def mc_compile(timeout: float = 600) -> JavaBuild:
    """Compile src/main/java against the Paper API and pack the plugin jar
    (classes plus src/main/resources). .ok, .errors (javac's), .jar."""
    proj = project_dir()
    srcs = sorted((proj / "src" / "main" / "java").rglob("*.java"))
    out = TMP / "mc-build"
    if out.exists():
        shutil.rmtree(out)
    classes = out / "classes"
    if not srcs:
        return JavaBuild(1, "no Java sources under src/main/java", None, classes)
    r = _javac(srcs, mc_classpath("compile"), classes, timeout)
    if not r.ok:
        return JavaBuild(r.returncode, r.out, None, classes)
    res = proj / "src" / "main" / "resources"
    if res.is_dir():
        shutil.copytree(res, classes, dirs_exist_ok=True)
    _, keys = mc_plugin_yml(proj)
    name = re.sub(r"[^\w.-]", "", keys.get("name", "plugin")) or "plugin"
    jar = out / f"{name}.jar"
    j = run(["jar", "--create", "--file", str(jar), "-C", str(classes), "."], timeout=120)
    if not j.ok:
        return JavaBuild(j.returncode, r.out + j.out, None, classes)
    return JavaBuild(0, r.out, jar, classes)


def mc_build(timeout: float = 600) -> Path:
    """The plugin jar; a compile error raises AssertionError listing javac's
    errors. The descriptor (plugin.yml or paper-plugin.yml) must be there."""
    b = mc_compile(timeout)
    assert b.ok, "javac failed:\n" + ("\n".join(b.errors) or b.out[-3000:])
    desc, keys = mc_plugin_yml()
    assert desc is not None, ("no plugin descriptor: put plugin.yml (or paper-plugin.yml) "
                              "in src/main/resources/")
    for k in ("name", "main", "version"):
        assert keys.get(k), f"{desc.name} has no {k}:"
    return b.jar


def junit_results(reports: Path) -> list[dict]:
    """Checks from JUnit's XML reports: {"name", "ok", "detail"} per test."""
    import xml.etree.ElementTree as ET
    out = []
    for f in sorted(reports.rglob("*.xml")):
        try:
            root = ET.parse(f).getroot()
        except ET.ParseError:
            continue
        for tc in root.iter("testcase"):
            cls, nm = tc.get("classname", ""), tc.get("name", "")
            name = nm if cls == nm or not cls else f"{cls}.{nm}"
            bad = tc.find("failure")
            if bad is None:
                bad = tc.find("error")
            skipped = tc.find("skipped") is not None
            detail = None
            if bad is not None:
                detail = ((bad.get("message") or "") + "\n" + (bad.text or ""))[:1500]
                sysout = tc.find("system-out")
                if sysout is not None and sysout.text and len(detail.strip()) < 200:
                    detail = (detail.strip() + "\n" + sysout.text.strip()[-1500:]).strip()
            if not skipped:
                out.append({"name": name, "ok": bad is None, "detail": detail})
    return out


def mc_test(timeout: float = 900) -> Checks:
    """Run the project's JUnit tests (src/test/java, with MockBukkit) with
    JUnit's console launcher. One check per test; .assert_ok()."""
    b = mc_compile(timeout)
    assert b.ok, "javac failed (src/main/java):\n" + ("\n".join(b.errors) or b.out[-3000:])
    proj = project_dir()
    tests = sorted((proj / "src" / "test" / "java").rglob("*.java"))
    assert tests, "no tests under src/test/java"
    tclasses = TMP / "mc-build" / "test-classes"
    cp = [b.classes] + mc_classpath("test")
    r = _javac(tests, cp, tclasses, timeout)
    assert r.ok, "javac failed (src/test/java):\n" + ("\n".join(java_errors(r.out)) or r.out[-3000:])
    tres = proj / "src" / "test" / "resources"
    if tres.is_dir():
        shutil.copytree(tres, tclasses, dirs_exist_ok=True)
    reports = TMP / "mc-build" / "junit-reports"
    shutil.rmtree(reports, ignore_errors=True)
    console = MC / "tools" / "junit-console.jar"
    # Everything on the JVM's own class path, not the launcher's
    # --class-path: MockBukkit.load defines its plugin proxy in a loader
    # whose parent is the system class loader, which must see the plugin
    # ("No jar file selected" otherwise).
    cmd = ["java", "-cp", os.pathsep.join(map(str, [console, tclasses] + cp)),
           "org.junit.platform.console.ConsoleLauncher", "execute", "--disable-banner",
           "--disable-ansi-colors", "--details=tree",
           "--scan-class-path", str(tclasses), "--reports-dir", str(reports)]
    t = run(cmd, timeout=timeout)
    checks = junit_results(reports)
    errors = [] if checks or t.ok else [f"JUnit did not run (exit {t.returncode})"]
    return Checks(checks, errors=errors, out=t.out)


MC_HARNESS = r'''package forgeharness;

%(imports)s
import org.bukkit.Bukkit;
import org.bukkit.plugin.java.JavaPlugin;

public final class ForgeHarness extends JavaPlugin {
    public static final class Forge {
        private final JavaPlugin plugin;
        private final java.util.List<java.util.Map<String, Object>> checks = new java.util.ArrayList<>();
        private final java.util.Map<String, Object> values = new java.util.LinkedHashMap<>();
        private boolean async, finished;

        Forge(JavaPlugin plugin) { this.plugin = plugin; }

        /** Record a named check. */
        public void check(String name, boolean ok) { check(name, ok, null); }
        public void check(String name, boolean ok, Object detail) {
            java.util.Map<String, Object> c = new java.util.LinkedHashMap<>();
            c.put("name", String.valueOf(name));
            c.put("ok", ok);
            if (detail != null) c.put("detail", String.valueOf(detail));
            checks.add(c);
        }
        /** Record a value (numbers, strings, booleans, lists, maps). */
        public void value(String name, Object v) { values.put(String.valueOf(name), v); }
        /** Keep going after the test code returns; call finish() yourself. */
        public void async() { async = true; }
        public boolean isAsync() { return async; }
        /** Run code on the server thread after some ticks (20 ticks = 1 s). */
        public void later(long ticks, Runnable r) {
            Bukkit.getScheduler().runTaskLater(plugin, () -> {
                try { r.run(); } catch (Throwable t) { check("later() code ran", false, trace(t)); finish(); }
            }, ticks);
        }
        public JavaPlugin plugin() { return plugin; }

        public synchronized void finish() {
            if (finished) return;
            finished = true;
            java.util.Map<String, Object> doc = new java.util.LinkedHashMap<>();
            doc.put("checks", checks);
            doc.put("values", values);
            try {
                java.nio.file.Files.writeString(
                    java.nio.file.Path.of(%(results)s), json(doc));
            } catch (java.io.IOException e) {
                plugin.getLogger().severe("forge: could not write the results: " + e);
            }
            plugin.getLogger().info("FORGE_TEST_DONE");
            Bukkit.getScheduler().runTask(plugin, Bukkit::shutdown);
        }

        static String trace(Throwable t) {
            java.io.StringWriter w = new java.io.StringWriter();
            t.printStackTrace(new java.io.PrintWriter(w));
            String s = w.toString();
            return s.length() > 4000 ? s.substring(0, 4000) : s;
        }

        static String json(Object o) {
            if (o == null) return "null";
            if (o instanceof Boolean || o instanceof Integer || o instanceof Long
                    || o instanceof Short || o instanceof Byte) return o.toString();
            if (o instanceof Number n) {
                double d = n.doubleValue();
                return Double.isFinite(d) ? Double.toString(d) : "null";
            }
            if (o instanceof java.util.Map<?, ?> m) {
                StringBuilder b = new StringBuilder("{");
                for (java.util.Map.Entry<?, ?> e : m.entrySet()) {
                    if (b.length() > 1) b.append(',');
                    b.append(json(String.valueOf(e.getKey()))).append(':').append(json(e.getValue()));
                }
                return b.append('}').toString();
            }
            if (o instanceof Iterable<?> it) {
                StringBuilder b = new StringBuilder("[");
                for (Object x : it) { if (b.length() > 1) b.append(','); b.append(json(x)); }
                return b.append(']').toString();
            }
            if (o.getClass().isArray()) {
                java.util.List<Object> l = new java.util.ArrayList<>();
                for (int i = 0; i < java.lang.reflect.Array.getLength(o); i++) l.add(java.lang.reflect.Array.get(o, i));
                return json(l);
            }
            String s = o.toString();
            StringBuilder b = new StringBuilder("\"");
            for (char c : s.toCharArray()) {
                switch (c) {
                    case '"' -> b.append("\\\"");
                    case '\\' -> b.append("\\\\");
                    case '\n' -> b.append("\\n");
                    case '\r' -> b.append("\\r");
                    case '\t' -> b.append("\\t");
                    default -> { if (c < 0x20) b.append(String.format("\\u%%04x", (int) c)); else b.append(c); }
                }
            }
            return b.append('"').toString();
        }
    }

    @Override
    public void onEnable() {
        final Forge FORGE = new Forge(this);
        Bukkit.getScheduler().runTaskLater(this, () -> {
            try {
                body(FORGE);
            } catch (Throwable t) {
                FORGE.check("test code ran", false, Forge.trace(t));
                FORGE.finish();
                return;
            }
            if (!FORGE.isAsync()) FORGE.finish();
        }, %(delay)d);
        Bukkit.getScheduler().runTaskLater(this, () -> {
            FORGE.check("finished in time", false, "FORGE.finish() was not called within %(limit)d s");
            FORGE.finish();
        }, %(limit)d * 20L);
    }

    @SuppressWarnings("unused")
    private void body(final Forge FORGE) throws Exception {
%(body)s
    }
}
'''

MC_DEFAULT_IMPORTS = ["java.util.*", "org.bukkit.*", "org.bukkit.entity.*", "org.bukkit.block.*",
                      "org.bukkit.inventory.*", "org.bukkit.command.*", "org.bukkit.plugin.*",
                      "org.bukkit.scoreboard.*", "org.bukkit.event.*"]


def _java_string(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def mc_harness_source(java: str, results: str, delay: int = 20, limit: int = 120) -> str:
    """The test plugin's Java: `java` is the body of a method that gets
    FORGE (check/value/async/later/finish). Leading `import ...;` lines in
    it are moved to the top of the file."""
    imports, body = [], []
    for ln in java.strip("\n").splitlines():
        if re.match(r"^\s*import\s+[\w.*]+\s*;\s*$", ln) and not body:
            imports.append(ln.strip())
        else:
            body.append(ln)
    imports += [f"import {i};" for i in MC_DEFAULT_IMPORTS
                if f"import {i};" not in imports]
    return MC_HARNESS % {"imports": "\n".join(imports),
                         "body": "\n".join("        " + ln for ln in body),
                         "results": _java_string(results), "delay": int(delay), "limit": int(limit)}


def _mc_harness_jar(java: str, results: str, delay: int, limit: int, project_jar: Path,
                    depend: str) -> Path:
    d = TMP / f"mc-harness-{time.monotonic_ns()}"
    src = d / "src" / "forgeharness" / "ForgeHarness.java"
    src.parent.mkdir(parents=True)
    src.write_text(mc_harness_source(java, results, delay, limit))
    classes = d / "classes"
    r = _javac([src], mc_classpath("compile") + [project_jar], classes, 600)
    if not r.ok:
        # Say where in the test's own Java each error is.
        lines = src.read_text().splitlines()
        first = next(i for i, ln in enumerate(lines) if "private void body(" in ln) + 2
        def where(m):
            n = int(m.group(1)) - first + 1
            return f"test code line {n}" if n >= 1 else f"harness line {m.group(1)}"
        errs = [re.sub(r"^\S*ForgeHarness\.java:(\d+)", where, e) for e in java_errors(r.out)]
        raise AssertionError("the test code did not compile:\n"
                             + "\n".join(errs or [r.out[-3000:]]))
    api = mc_versions().get("api_version", "")
    (classes / "plugin.yml").write_text(
        "name: ForgeHarness\nversion: '1'\nmain: forgeharness.ForgeHarness\n"
        + (f"api-version: '{api}'\n" if api else "")
        + (f"depend: [{depend}]\n" if depend else ""))
    jar = d / "ForgeHarness.jar"
    run(["jar", "--create", "--file", str(jar), "-C", str(classes), "."], timeout=120, check=True)
    return jar


MC_LOG_NOISE = re.compile(r"(update|version) check|Could not (resolve|contact)|UnknownHost|bStats|"
                          r"services\.api|sessionserver|ConnectException|Failed to fetch",
                          re.I)


def mc_errors(log: str) -> list[str]:
    """Server ERROR lines and plugin exceptions, without the offline noise
    (no update check, no Mojang services in the sandbox)."""
    out = []
    for ln in (log or "").splitlines():
        if ("/ERROR]" in ln or "Exception" in ln and "/WARN]" not in ln and "\tat " not in ln) \
                and not MC_LOG_NOISE.search(ln):
            out.append(ln.strip()[:400])
    return out[:40]


def mc_run(java: str, timeout: float = 360, delay: int = 20, limit: int = 120,
           properties: dict | None = None, memory: str = "1536M") -> Checks:
    """Start the Paper server with the project's plugin (and the project's
    datapacks/<name>/ folders) installed, run `java` on the server thread
    once it has started, and return Checks. In the Java, FORGE.check(name,
    ok[, detail]) records a check and FORGE.value(name, v) a value; call
    FORGE.async() and later FORGE.finish() to keep going (FORGE.later(ticks,
    runnable) schedules code; 20 ticks = 1 s):

        r = game.mc_run('''
            World w = Bukkit.getWorlds().get(0);
            FORGE.check("plugin on", Bukkit.getPluginManager().isPluginEnabled("Crest"));
            Entity pig = w.spawnEntity(w.getSpawnLocation(), EntityType.PIG);
            FORGE.check("spawned", pig.isValid());
        ''')
        r.assert_ok()

    Each run starts a server (~20-60 s): put many checks in one run.
    Server errors ([ERROR] lines, plugin exceptions) are in .errors and make
    .ok False."""
    jar = mc_build()
    _, keys = mc_plugin_yml()
    srv = TMP / f"mc-server-{time.monotonic_ns()}"
    shutil.copytree(MC / "server", srv, symlinks=False)
    for p in srv.rglob("*"):
        try:
            p.chmod(0o755 if p.is_dir() else 0o644)
        except OSError:
            pass
    plugins = srv / "plugins"
    plugins.mkdir(exist_ok=True)
    shutil.copy(jar, plugins / jar.name)
    results = srv / "forge_results.json"
    harness = _mc_harness_jar(java, str(results), delay, limit, jar, keys.get("name", ""))
    shutil.copy(harness, plugins / "ForgeHarness.jar")
    packs = project_dir() / "datapacks"
    if packs.is_dir():
        for pack in sorted(packs.iterdir()):
            if (pack / "pack.mcmeta").is_file():
                shutil.copytree(pack, srv / "world" / "datapacks" / pack.name, dirs_exist_ok=True)
    if properties:
        prop = srv / "server.properties"
        lines = [ln for ln in prop.read_text().splitlines()
                 if ln.split("=", 1)[0] not in properties]
        lines += [f"{k}={v}" for k, v in properties.items()]
        prop.write_text("\n".join(lines) + "\n")
    cmd = ["java", f"-Xmx{memory}", "-Xms256M", "-Dpaper.playerconnection.keepalive=120",
           "-jar", "paper.jar", "--nogui"]
    r = run(cmd, cwd=srv, timeout=timeout, input="")
    data = None
    if results.exists():
        try:
            data = json.loads(results.read_text())
        except ValueError:
            data = None
    errors = mc_errors(r.out)
    if data is None:
        why = ("the server timed out" if r.returncode == 124 else
               f"the server exited (code {r.returncode}) before the test finished")
        return Checks([{"name": "test ran", "ok": False, "detail": why}], errors=errors, out=r.out)
    return Checks(data.get("checks") or [], values=data.get("values") or {}, errors=errors,
                  out=r.out)


# ------------------------------------------------------------------ shell

SHELLCHECK_LINE = re.compile(r"^(?P<file>.+?):(?P<line>\d+):(?P<col>\d+): (?P<level>\w+): "
                             r"(?P<msg>.*?)(?: \[(?P<code>SC\d+)\])?$")
SHEBANG_SH = re.compile(r"^#!\s*(?:/usr)?/bin/(?:env\s+)?(?:ba|da|k|z)?sh\b")


def shell_files(proj: Path | None = None) -> list[Path]:
    """The project's shell scripts: *.sh / *.bash, and files without a
    suffix whose first line is a sh/bash shebang. Not .bats files (bats
    has its own syntax)."""
    proj = proj or project_dir()
    out = []
    for p in sorted(proj.rglob("*")):
        if not p.is_file() or p.suffix == ".bats":
            continue
        if p.suffix in (".sh", ".bash"):
            out.append(p)
            continue
        if p.suffix:
            continue                     # x.py, notes.txt: not shell even with a #! line
        try:
            with p.open("rb") as f:
                head = f.read(120).decode(errors="replace").splitlines()[:1]
        except OSError:
            continue
        if head and SHEBANG_SH.match(head[0]):
            out.append(p)
    return out


class ShellCheck(Result):
    @property
    def issues(self) -> list[dict]:
        """{"file", "line", "col", "level", "msg", "code"} per finding."""
        res = []
        for ln in self.out.splitlines():
            m = SHELLCHECK_LINE.match(ln.strip())
            if m:
                d = m.groupdict()
                d["line"], d["col"] = int(d["line"]), int(d["col"])
                res.append(d)
        return res


def shellcheck(paths=None, severity: str = "info", timeout: float = 300) -> ShellCheck:
    """ShellCheck the scripts (default: every shell file in the project) at
    `severity` and above (error, warning, info, style). Sourced files are
    followed (-x). The default, info, includes SC2086 (unquoted expansion).
    .ok is True when there is no finding; .issues lists
    them with file paths relative to the project."""
    proj = project_dir()
    files = [Path(p) if Path(p).is_absolute() else proj / p for p in paths] if paths else shell_files(proj)
    if not files:
        return ShellCheck(0, "")
    r = run(["shellcheck", "-f", "gcc", "-x", "--source-path=SCRIPTDIR", "-S", severity,
             *[str(f.relative_to(proj)) if f.is_relative_to(proj) else str(f) for f in files]],
            cwd=proj, timeout=timeout)
    return ShellCheck(r.returncode, r.out)


def shfmt_diff(paths=None, indent: int = 4, timeout: float = 120) -> Result:
    """`shfmt -d` (what shfmt would change; empty when formatted), indent
    spaces as given (0 = tabs)."""
    proj = project_dir()
    files = [str(p) for p in (paths or shell_files(proj))]
    return run(["shfmt", "-d", "-i", str(indent), "-ci", *files], cwd=proj, timeout=timeout)


TAP_LINE = re.compile(r"^(not ok|ok)\s+\d+\s*(?:-\s*)?(.*?)(?:\s+#\s*(skip|SKIP).*)?$")


def bats(paths=None, timeout: float = 900, env: dict | None = None) -> Checks:
    """Run bats tests (default: every .bats file in the project) and return
    one check per test. Tests can `bats_load_library bats-support`,
    `bats-assert` and `bats-file` (BATS_LIB_PATH is set)."""
    proj = project_dir()
    files = [str(Path(p)) for p in paths] if paths else [str(p.relative_to(proj))
                                                       for p in sorted(proj.rglob("*.bats"))]
    assert files, "no .bats files in the project"
    e = {"BATS_LIB_PATH": "/usr/lib/bats", "TMPDIR": str(TMP)}
    e.update(env or {})
    r = run(["bats", "--tap", *files], cwd=proj, timeout=timeout, env=e)
    checks, cur = [], None
    for ln in r.out.splitlines():
        m = TAP_LINE.match(ln)
        if m:
            if m.group(3):
                cur = None
                continue
            cur = {"name": m.group(2).strip(), "ok": m.group(1) == "ok", "detail": None}
            checks.append(cur)
        elif cur is not None and not cur["ok"] and ln.startswith("#"):
            cur["detail"] = ((cur["detail"] or "") + ln[1:].rstrip() + "\n")[-2000:]
    errors = [] if checks else [f"bats ran no tests (exit {r.returncode})"]
    return Checks(checks, errors=errors, out=r.out)


STUB_SCRIPT = """#!/bin/sh
# forge stub for %(name)s: records the call, prints what it was told, exits.
python3 - "$0" "$@" <<'PY'
import json, os, sys
with open(os.environ["FORGE_STUB_LOG"], "a") as f:
    f.write(json.dumps({"cmd": os.path.basename(sys.argv[1]), "args": sys.argv[2:],
                        "cwd": os.getcwd()}) + "\\n")
PY
%(body)s
"""


class ShellRun(Result):
    def __init__(self, code: int, out: str, calls: list[dict]):
        super().__init__(code, out)
        self.calls = calls

    def calls_to(self, name: str) -> list[list[str]]:
        """The argument lists of every call to a stubbed command, in order."""
        return [c["args"] for c in self.calls if c["cmd"] == name]


def sh_run(cmd, stubs: dict | None = None, env: dict | None = None, cwd=None,
           timeout: float = 120, input: str | None = None) -> ShellRun:
    """Run a command (a list, or a string for `bash -c`) from the project
    folder with some commands replaced by stubs that record their calls:
    stubs={"systemctl": "", "docker": {"stdout": "Docker version 27.0", "exit": 0},
           "apt-get": {"exit": 100}} (a string is shell code for the stub's
    body). r.calls lists every call; r.calls_to("systemctl") their args.
    The stubs come first on PATH; nothing reaches the real system (the
    sandbox has no network, no systemd and a read-only root)."""
    proj = project_dir()
    sdir = TMP / f"stubs-{time.monotonic_ns()}"
    sdir.mkdir(parents=True)
    log = sdir / "calls.jsonl"
    log.write_text("")
    for name, spec in (stubs or {}).items():
        if isinstance(spec, dict):
            body = ""
            if spec.get("stdout"):
                body += f"printf '%s\\n' {_sh_quote(str(spec['stdout']))}\n"
            if spec.get("stderr"):
                body += f"printf '%s\\n' {_sh_quote(str(spec['stderr']))} >&2\n"
            body += f"exit {int(spec.get('exit', 0))}\n"
        else:
            body = str(spec or "") + "\nexit 0\n"
        p = sdir / name
        p.write_text(STUB_SCRIPT % {"name": name, "body": body})
        p.chmod(0o755)
    e = {"PATH": f"{sdir}:{os.environ.get('PATH', '/usr/bin:/bin')}", "FORGE_STUB_LOG": str(log)}
    e.update(env or {})
    argv = ["bash", "-c", cmd] if isinstance(cmd, str) else [str(c) for c in cmd]
    r = run(argv, cwd=cwd or proj, timeout=timeout, env=e, input=input)
    calls = [json.loads(x) for x in log.read_text().splitlines() if x.strip()]
    return ShellRun(r.returncode, r.out, calls)


def _sh_quote(s: str) -> str:
    return "'" + s.replace("'", "'\\''") + "'"


def systemd_verify(paths=None, allow_missing: bool = True, timeout: float = 120) -> list[str]:
    """Problems `systemd-analyze verify` finds in unit files (default: every
    *.service/*.timer/*.socket/*.path/*.target/*.mount in the project).
    allow_missing drops "is not executable" for programs the unit runs
    that are not installed in the sandbox."""
    proj = project_dir()
    units = [Path(p) if Path(p).is_absolute() else proj / p for p in paths] if paths else sorted(
        p for p in proj.rglob("*") if p.suffix in (".service", ".timer", ".socket", ".path",
                                                    ".target", ".mount") and p.is_file())
    problems = []
    for u in units:
        r = run(["systemd-analyze", "verify", "--man=no", "--generators=no", str(u)],
                cwd=u.parent, timeout=timeout)
        for ln in r.out.splitlines():
            ln = ln.strip()
            if not ln or (allow_missing and "is not executable" in ln):
                continue
            if "Failed to " in ln and "bus" in ln.lower():
                continue                 # no D-Bus in the sandbox
            problems.append(ln)
    return problems


def yamllint(paths=None, timeout: float = 120) -> list[str]:
    """yamllint's errors and warnings (relaxed rules: line length and
    document start ignored), as "file:line:col: [level] message"."""
    proj = project_dir()
    files = [str(p) for p in (paths or sorted(list(proj.rglob("*.yml")) + list(proj.rglob("*.yaml"))))]
    if not files:
        return []
    conf = "{extends: relaxed, rules: {line-length: disable, document-start: disable}}"
    r = run(["yamllint", "-f", "parsable", "-d", conf, *files], cwd=proj, timeout=timeout)
    return [ln.strip() for ln in r.out.splitlines() if re.search(r":\d+:\d+: \[(error|warning)\]", ln)]


# ------------------------------------------------------------------ Node / TypeScript

NODE_TOOLS = Path(os.environ.get("FORGE_NODE_TOOLS", "/opt/node-tools"))
DEPS = Path(os.environ.get("FORGE_DEPS", "/deps"))
TS_ERROR = re.compile(r"^(?P<file>[^\s(][^(]*)\((?P<line>\d+),(?P<col>\d+)\): error (?P<code>TS\d+): (?P<msg>.*)$")


def node_versions() -> dict:
    """Node, TypeScript, vitest, tsx and jsdom versions in the image."""
    try:
        return json.loads((NODE_TOOLS / "versions.json").read_text())
    except (OSError, ValueError):
        return {}


def node_modules() -> Path:
    """The project's node_modules: the packages this run fetched (deps/,
    from package.json) and the image's tools (typescript, vitest, tsx,
    jsdom, @types/node) as links, so imports resolve from the project
    folder. Idempotent."""
    proj = project_dir()
    nm = proj / "node_modules"
    if nm.is_dir() and (nm / ".forge").exists():
        return nm
    if nm.is_symlink() or nm.is_file():
        nm.unlink()
    nm.mkdir(exist_ok=True)
    (nm / ".bin").mkdir(exist_ok=True)
    for src_root in (DEPS / "node_modules", NODE_TOOLS / "node_modules"):
        if not src_root.is_dir():
            continue
        for entry in sorted(src_root.iterdir()):
            if entry.name.startswith("."):
                continue
            pkgs = sorted(entry.iterdir()) if entry.name.startswith("@") and entry.is_dir() else [entry]
            for pkg in pkgs:
                rel = pkg.relative_to(src_root)
                dest = nm / rel
                if dest.exists() or dest.is_symlink():
                    continue             # the project's own version wins
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.symlink_to(pkg)
        bins = src_root / ".bin"
        if bins.is_dir():
            for b in bins.iterdir():
                d = nm / ".bin" / b.name
                if not (d.exists() or d.is_symlink()):
                    d.symlink_to(b.resolve())
    (nm / ".forge").write_text("linked by forge_game.node_modules()\n")
    return nm


def _node_env(extra: dict | None = None) -> dict:
    proj = project_dir()
    e = {"PATH": f"{proj / 'node_modules' / '.bin'}:{NODE_TOOLS / 'node_modules' / '.bin'}:"
                 f"{os.environ.get('PATH', '/usr/bin:/bin')}",
         "npm_config_cache": str(TMP / "npm-cache"), "NPM_CONFIG_UPDATE_NOTIFIER": "false",
         "CI": "1", "NO_COLOR": "1", "FORCE_COLOR": "0"}
    e.update(extra or {})
    return e


class TSBuild(Result):
    @property
    def errors(self) -> list[str]:
        """'file(line,col): error TSxxxx: message' lines."""
        return [ln.strip() for ln in self.out.splitlines() if TS_ERROR.match(ln.strip())]


def tsc(args=None, timeout: float = 300) -> TSBuild:
    """Type-check with TypeScript (tsc --noEmit): with the project's
    tsconfig.json when it has one, else strict ES2024/NodeNext over every
    .ts file outside node_modules. .ok, .errors."""
    proj = project_dir()
    node_modules()
    if args is None:
        if (proj / "tsconfig.json").exists():
            args = ["-p", ".", "--noEmit"]
        else:
            files = [str(p.relative_to(proj)) for p in sorted(proj.rglob("*.ts"))
                     if "node_modules" not in p.parts]
            args = ["--noEmit", "--strict", "--target", "es2024", "--module", "nodenext",
                    "--moduleResolution", "nodenext", "--types", "node", "--skipLibCheck", *files]
    r = run(["tsc", "--pretty", "false", *args], cwd=proj, timeout=timeout, env=_node_env())
    return TSBuild(r.returncode, r.out)


def vitest(paths=None, timeout: float = 900, env: dict | None = None) -> Checks:
    """Run the project's vitest tests (vitest run) and return one check per
    test ("describe > test" names). Environment jsdom works for DOM code."""
    proj = project_dir()
    node_modules()
    report = TMP / f"vitest-{time.monotonic_ns()}.json"
    r = run(["vitest", "run", "--reporter=json", f"--outputFile={report}", "--no-color",
             *[str(p) for p in (paths or [])]], cwd=proj, timeout=timeout, env=_node_env(env))
    checks, errors = [], []
    try:
        data = json.loads(report.read_text())
    except (OSError, ValueError):
        return Checks([], errors=[f"vitest wrote no report (exit {r.returncode})"], out=r.out)
    for f in data.get("testResults", []):
        rel = os.path.relpath(f.get("name", ""), proj)
        if not f.get("assertionResults") and f.get("status") == "failed":
            errors.append(f"{rel}: {(f.get('message') or '').strip()[:1500]}")
        for a in f.get("assertionResults", []):
            if a.get("status") in ("skipped", "pending", "todo"):
                continue
            name = " > ".join([*a.get("ancestorTitles", []), a.get("title", "")])
            detail = None
            if a.get("status") != "passed":
                detail = "\n".join(_node_trim(m) for m in a.get("failureMessages") or [])[:1500]
            checks.append({"name": name, "ok": a.get("status") == "passed", "detail": detail,
                           "file": rel})
    return Checks(checks, errors=errors, out=r.out)


def _node_trim(msg: str) -> str:
    """A failure message without the stack frames inside node_modules."""
    return "\n".join(ln for ln in msg.splitlines()
                     if "node_modules" not in ln and "node:internal" not in ln)


def node_run(script, args=(), timeout: float = 120, env: dict | None = None,
             input: str | None = None) -> Result:
    """Run a .js/.mjs file with node, or a .ts file with tsx, from the
    project folder."""
    proj = project_dir()
    node_modules()
    s = str(script)
    cmd = ["tsx", s] if s.endswith((".ts", ".mts", ".cts", ".tsx")) else ["node", s]
    return run([*cmd, *map(str, args)], cwd=proj, timeout=timeout, env=_node_env(env), input=input)


# ------------------------------------------------------------------ Go

GO_ERROR = re.compile(r"^(?:\./)?(?P<file>[\w./-]+\.go):(?P<line>\d+):(?P<col>\d+): (?P<msg>.+)$")


def _go_env(extra: dict | None = None) -> dict:
    """Offline Go: modules only from this run's deps/ (fetched before the
    sandbox started), build cache in TMP."""
    mod = DEPS / "gomod"
    e = {"GOPATH": str(TMP / "go"), "GOCACHE": str(TMP / "go-build"),
         "GOMODCACHE": str(mod if mod.is_dir() else TMP / "go" / "pkg" / "mod"),
         "GOPROXY": "off", "GOSUMDB": "off", "GOFLAGS": "-mod=mod", "GOTOOLCHAIN": "local",
         "GOTELEMETRY": "off"}
    e.update(extra or {})
    return e


def go_prepare() -> Path:
    """The project with the go.mod/go.sum that `go mod tidy` made when this
    run's modules were fetched (they list every module the code imports)."""
    proj = project_dir()
    for name in ("go.mod", "go.sum"):
        src = DEPS / name
        if src.is_file():
            shutil.copy(src, proj / name)
    return proj


class GoBuild(Result):
    @property
    def errors(self) -> list[str]:
        """'file.go:line:col: message' lines."""
        return [ln.strip().removeprefix("./") for ln in self.out.splitlines()
                if GO_ERROR.match(ln.strip())]


def go(*args, timeout: float = 600, env: dict | None = None, input: str | None = None) -> Result:
    """`go <args>` from the project folder, offline."""
    proj = go_prepare()
    return run(["go", *map(str, args)], cwd=proj, timeout=timeout, env=_go_env(env), input=input)


def go_build(pkgs: str = "./...", timeout: float = 600) -> GoBuild:
    """go build + go vet. .ok, .errors ('file.go:line:col: message')."""
    r = go("build", pkgs, timeout=timeout)
    if r.ok:
        r = go("vet", pkgs, timeout=timeout)
    return GoBuild(r.returncode, r.out)


def gofmt_files() -> list[str]:
    """Files gofmt would change (empty when formatted)."""
    proj = go_prepare()
    r = run(["gofmt", "-l", "."], cwd=proj, timeout=120, env=_go_env())
    return [ln.strip() for ln in r.out.splitlines() if ln.strip().endswith(".go")]


def go_test(pkgs: str = "./...", race: bool = False, timeout: float = 900,
            run_pattern: str | None = None) -> Checks:
    """go test -json: one check per test (subtests as Parent/sub). Build
    failures come back as errors. race=True adds the race detector."""
    args = ["test", "-json", "-count=1"] + (["-race"] if race else []) + \
           (["-run", run_pattern] if run_pattern else []) + [pkgs]
    r = go(*args, timeout=timeout)
    checks, errors, outputs = [], [], {}
    for ln in r.out.splitlines():
        if not ln.startswith("{"):
            if GO_ERROR.match(ln.strip()) or ln.startswith(("FAIL", "# ")):
                errors.append(ln.strip())
            continue
        try:
            ev = json.loads(ln)
        except ValueError:
            continue
        act, test, pkg = ev.get("Action"), ev.get("Test"), ev.get("Package", "")
        if act == "output":
            outputs.setdefault((pkg, test), []).append(ev.get("Output", ""))
        elif act == "build-output":
            errors.append((ev.get("Output") or "").rstrip())
        elif act in ("pass", "fail") and test:
            detail = None
            if act == "fail":
                lines = [o.strip() for o in outputs.get((pkg, test), [])
                         if o.strip() and not o.startswith(("=== RUN", "=== PAUSE", "=== CONT"))]
                detail = "\n".join(lines)[-1500:] or None
            checks.append({"name": test, "ok": act == "pass", "detail": detail, "package": pkg})
        elif act == "fail" and not test:
            lines = "".join(outputs.get((pkg, None), []))
            if "[setup failed]" in lines or "[build failed]" in lines or not any(
                    c.get("package") == pkg for c in checks):
                errors.append(f"{pkg}: {lines.strip()[-1500:] or 'failed'}")
    errors = [e for e in errors if e and not e.startswith("FAIL\t") and e != "FAIL"]
    return Checks(checks, errors=errors, out=r.out)


# ------------------------------------------------------------------ Home Assistant

HA_VERSIONS = Path(os.environ.get("FORGE_HA_VERSIONS", "/opt/ha-versions.json"))


def ha_versions() -> dict:
    """Home Assistant core, pytest-homeassistant-custom-component, pytest,
    Python and ESPHome versions in the image."""
    try:
        return json.loads(HA_VERSIONS.read_text())
    except (OSError, ValueError):
        return {}


def _ha_env(extra: dict | None = None) -> dict:
    proj = project_dir()
    paths = [str(proj)]
    wheels = TMP / "ha-site"
    if wheels.is_dir():
        paths.append(str(wheels))
    e = {"PYTHONPATH": ":".join(paths), "PYTHONDONTWRITEBYTECODE": "1"}
    e.update(extra or {})
    return e


def ha_requirements() -> list[str]:
    """Install the custom integrations' manifest requirements from the
    wheels this run fetched (deps/wheels) into TMP/ha-site. Returns what
    was installed; raises AssertionError when a requirement is missing."""
    proj = project_dir()
    reqs = []
    for m in sorted(proj.glob("custom_components/*/manifest.json")):
        try:
            reqs += json.loads(m.read_text()).get("requirements") or []
        except ValueError as exc:
            raise AssertionError(f"{m.relative_to(proj)} is not valid JSON: {exc}")
    if not reqs:
        return []
    wheels = DEPS / "wheels"
    site = TMP / "ha-site"
    if (site / ".done").exists():
        return reqs
    assert wheels.is_dir(), ("the integration's requirements " + ", ".join(reqs) + " were not "
                             "fetched (deps/wheels is missing)")
    r = run([sys.executable, "-m", "pip", "install", "--no-index", "--find-links", str(wheels),
             "--target", str(site), "--disable-pip-version-check", "--no-warn-conflicts", *reqs],
            timeout=600)
    assert r.ok, ("the integration's requirements are not installable offline (they are "
                  f"fetched as wheels before the sandbox starts):\n{r.out[-2000:]}")
    (site / ".done").write_text("")
    return reqs


def ha_test(paths=None, timeout: float = 900, args=()) -> Checks:
    """Run the project's pytest tests with pytest-homeassistant-custom-component
    (its `hass` fixture, async tests). The project's custom_components
    package is imported first so Home Assistant's loader finds the
    integrations under test; tests still need the enable_custom_integrations
    fixture (an autouse fixture in tests/conftest.py). One check per test."""
    proj = project_dir()
    cc = proj / "custom_components"
    if cc.is_dir() and not (cc / "__init__.py").exists():
        (cc / "__init__.py").write_text("")
    ha_requirements()
    report = TMP / f"ha-junit-{time.monotonic_ns()}"
    report.mkdir(parents=True)
    pyargs = ["-q", "-p", "no:cacheprovider", "-o", "asyncio_mode=auto",
              f"--junitxml={report / 'pytest.xml'}", "--timeout=120",
              *map(str, args), *[str(p) for p in (paths or ["tests"])]]
    boot = ("import sys; sys.path.insert(0, sys.argv[1]); sys.argv = ['pytest'] + sys.argv[2:]\n"
            "try:\n    import custom_components\nexcept ImportError:\n    pass\n"
            "import pytest; sys.exit(pytest.main(sys.argv[1:]))")
    r = run([sys.executable, "-c", boot, str(proj), *pyargs], cwd=proj, timeout=timeout,
            env=_ha_env())
    checks = junit_results(report)
    errors = [] if checks else [f"pytest ran no tests (exit {r.returncode})"]
    if r.returncode in (2, 3, 4) and "ERROR collecting" in r.out:
        errors.append("collection failed - see the output")
    return Checks(checks, errors=errors, out=r.out)


class HAConfig(Result):
    def __init__(self, code: int, out: str, report: dict):
        super().__init__(code, out)
        self.report = report

    def _flat(self, key: str) -> list[str]:
        res = []
        for domain, items in (self.report.get(key) or {}).items():
            for it in items:
                if isinstance(it, str):
                    res.append(f"{domain}: {it}")
        return res

    @property
    def errors(self) -> list[str]:
        return self._flat("errors")

    @property
    def warnings(self) -> list[str]:
        return self._flat("warnings")

    @property
    def components(self) -> list[str]:
        return list(self.report.get("components") or [])

    @property
    def ok(self) -> bool:
        return bool(self.report) and not self.errors and not self.warnings


def ha_check_config(config: str = "config", timeout: float = 300) -> HAConfig:
    """Home Assistant's own configuration check on a config folder in the
    project (configuration.yaml, its !include files, automations...). The
    project's custom_components are copied in when the config folder has
    none. .ok (no errors and no warnings), .errors, .warnings ("domain:
    message"), .components (what loaded). A secrets.yaml is needed only if
    the config uses !secret; give it dummy values in the project."""
    proj = project_dir()
    src = proj / config
    assert src.is_dir(), f"no {config}/ folder in the project"
    work = TMP / f"ha-config-{time.monotonic_ns()}"
    shutil.copytree(src, work)
    if (proj / "custom_components").is_dir() and not (work / "custom_components").exists():
        shutil.copytree(proj / "custom_components", work / "custom_components")
    r = run([sys.executable, "-m", "homeassistant", "--script", "check_config", "-c", str(work),
             "--json"], cwd=work, timeout=timeout, env=_ha_env())
    m = re.search(r"^\{.*\}\s*$", r.out, re.S | re.M)
    report = {}
    if m:
        try:
            report = json.loads(m.group(0))
        except ValueError:
            report = {}
    return HAConfig(r.returncode, r.out, report)


ESPHOME_ERROR_BLOCK = re.compile(r"^Failed config\s*$", re.M)


def esphome_config(path, timeout: float = 180) -> list[str]:
    """ESPHome's validation of a device YAML (no compile). [] when valid;
    otherwise the reported problems ("component: message")."""
    proj = project_dir()
    p = Path(path)
    p = p if p.is_absolute() else proj / p
    r = run(["esphome", "config", p.name], cwd=p.parent, timeout=timeout,
            env={"PLATFORMIO_CORE_DIR": str(TMP / "platformio"), "ESPHOME_NO_COLOR": "1"})
    if r.ok and not ESPHOME_ERROR_BLOCK.search(r.out):
        return []
    problems, comp = [], ""
    text = r.out.split("Failed config", 1)[-1] if "Failed config" in r.out else r.out
    for ln in text.splitlines():
        s = ln.rstrip()
        if re.match(r"^[\w.]+:\s*(\[source .*\])?\s*$", s):
            comp = s.split(":")[0]
        elif s.startswith("  ") and s.strip() and not re.match(r"^\s+[\w-]+:\s", s) \
                and not s.strip().startswith(("-", "[")):
            problems.append(f"{comp}: {s.strip()}" if comp else s.strip())
    return problems or [r.out.strip()[-1500:]]


# ------------------------------------------------------------------ C / C++

C_DIAG = re.compile(r"^(?P<file>[^:\s][^:]*):(?P<line>\d+):(?:(?P<col>\d+):)? "
                    r"(?P<level>fatal error|error|warning): (?P<msg>.*)$")
LINK_ERROR = re.compile(r"(undefined reference to [`'][^'`]+'|multiple definition of [`'][^'`]+'|"
                        r"cannot find -l\S+|ld(?:\.\w+)?: .*error.*)")
SANITIZER = re.compile(r"(==\d+==ERROR: \w+Sanitizer: .*|^SUMMARY: \w+Sanitizer: .*|"
                       r"^\S+:\d+:\d+: runtime error: .*|WARNING: ThreadSanitizer: .*)", re.M)


class CBuild(Result):
    def __init__(self, code: int, out: str, build_dir: Path):
        super().__init__(code, out)
        self.build_dir = build_dir

    def _diags(self, levels) -> list[str]:
        seen, res = set(), []
        for ln in self.out.splitlines():
            m = C_DIAG.match(ln.strip())
            if m and m.group("level") in levels:
                s = ln.strip()
                if _project is not None:
                    s = s.replace(str(_project) + "/", "")
                if s not in seen:
                    seen.add(s)
                    res.append(s)
        return res

    @property
    def errors(self) -> list[str]:
        """Compiler errors ('file:line:col: error: msg') and linker errors."""
        errs = self._diags(("error", "fatal error"))
        errs += [m.group(1) for ln in self.out.splitlines() for m in [LINK_ERROR.search(ln)] if m]
        if not errs and not self.ok:
            cm = [ln.strip() for ln in self.out.splitlines() if ln.startswith("CMake Error")]
            errs = cm or [self.out.strip()[-1500:]]
        return errs

    @property
    def warnings(self) -> list[str]:
        return self._diags(("warning",))

    def assert_ok(self) -> "CBuild":
        assert self.ok, "the build failed:\n" + "\n".join(self.errors[:20])
        return self


def _cc_flags(sanitize: str | None, coverage: bool) -> str:
    flags = []
    if sanitize:
        flags += [f"-fsanitize={sanitize}", "-fno-omit-frame-pointer", "-fno-sanitize-recover=all"]
    if coverage:
        flags += ["--coverage", "-O0"]
    return " ".join(flags)


def cc_build(config: str = "Debug", sanitize: str | None = None, compiler: str = "gcc",
             coverage: bool = False, target: str | None = None, defines: dict | None = None,
             werror: bool = False, timeout: float = 900) -> CBuild:
    """Build the project - CMake (CMakeLists.txt), Meson (meson.build) or make
    (Makefile), in that order - into its own build folder per settings.
    sanitize: "address,undefined" (ASan+UBSan), "thread", "memory" (clang);
    compiler: gcc or clang; coverage adds --coverage. Never raises: .ok,
    .errors ('file:line:col: error: msg' and linker errors), .warnings,
    .build_dir; .assert_ok()."""
    proj = project_dir()
    key = re.sub(r"[^\w]+", "-", f"{compiler}-{config}-{sanitize or 'plain'}-{'cov' if coverage else ''}")
    b = TMP / f"build-{key}"
    cc, cxx = ("clang", "clang++") if compiler == "clang" else ("gcc", "g++")
    flags = _cc_flags(sanitize, coverage) + (" -Werror" if werror else "")
    env = {"CC": cc, "CXX": cxx, "CCACHE_DIR": str(TMP / "ccache")}
    if (proj / "CMakeLists.txt").exists():
        cfg = ["cmake", "-S", str(proj), "-B", str(b), "-G", "Ninja", f"-DCMAKE_BUILD_TYPE={config}",
               f"-DCMAKE_C_COMPILER={cc}", f"-DCMAKE_CXX_COMPILER={cxx}",
               "-DCMAKE_EXPORT_COMPILE_COMMANDS=ON", f"-DCMAKE_C_FLAGS={flags}",
               f"-DCMAKE_CXX_FLAGS={flags}", f"-DCMAKE_EXE_LINKER_FLAGS={_cc_flags(sanitize, coverage)}",
               f"-DCMAKE_SHARED_LINKER_FLAGS={_cc_flags(sanitize, coverage)}",
               *[f"-D{k}={v}" for k, v in (defines or {}).items()]]
        r = run(cfg, cwd=proj, timeout=timeout, env=env)
        if r.ok:
            r2 = run(["cmake", "--build", str(b), "-j", "4"] + (["--target", target] if target else []),
                     cwd=proj, timeout=timeout, env=env)
            r = Result(r2.returncode, r.out + r2.out)
    elif (proj / "meson.build").exists():
        opts = [f"-Dbuildtype={'debug' if config == 'Debug' else 'release'}"]
        if sanitize:
            opts.append(f"-Db_sanitize={sanitize}")
        if coverage:
            opts.append("-Db_coverage=true")
        if werror:
            opts.append("-Dwerror=true")
        opts += [f"-D{k}={v}" for k, v in (defines or {}).items()]
        r = run(["meson", "setup", "--wipe" if b.exists() else "--reconfigure", str(b), str(proj),
                 *opts] if b.exists() else ["meson", "setup", str(b), str(proj), *opts],
                cwd=proj, timeout=timeout, env=env)
        if r.ok:
            r2 = run(["meson", "compile", "-C", str(b)] + ([target] if target else []),
                     cwd=proj, timeout=timeout, env=env)
            r = Result(r2.returncode, r.out + r2.out)
    elif (proj / "Makefile").exists() or (proj / "makefile").exists():
        b.mkdir(parents=True, exist_ok=True)
        r = run(["make", "-j4", f"CFLAGS+={flags}", f"CXXFLAGS+={flags}",
                 f"LDFLAGS+={_cc_flags(sanitize, coverage)}"] + ([target] if target else []),
                cwd=proj, timeout=timeout, env=env)
        b = proj
    else:
        return CBuild(2, "no CMakeLists.txt, meson.build or Makefile in the project", b)
    return CBuild(r.returncode, r.out, b)


def _san_env(extra: dict | None = None) -> dict:
    e = {"ASAN_OPTIONS": "detect_leaks=1:abort_on_error=0:halt_on_error=1:strict_string_checks=1",
         "UBSAN_OPTIONS": "print_stacktrace=1:halt_on_error=1",
         "TSAN_OPTIONS": "halt_on_error=1:second_deadlock_stack=1", "LSAN_OPTIONS": "verbosity=0"}
    e.update(extra or {})
    return e


def sanitizer_reports(out: str) -> list[str]:
    """AddressSanitizer / UBSan / TSan / LeakSanitizer findings in output."""
    return [m.group(1).strip() for m in SANITIZER.finditer(out or "")]


def ctest(sanitize: str | None = None, compiler: str = "gcc", config: str = "Debug",
          coverage: bool = False, timeout: float = 900, build: CBuild | None = None) -> Checks:
    """Build (cc_build with these settings) and run the tests: CTest for
    CMake (GoogleTest via gtest_discover_tests, Catch2 via catch_discover_tests,
    or add_test), `meson test` for Meson. One check per test; sanitizer
    findings in a test's output fail it and are listed in its detail."""
    b = build or cc_build(config=config, sanitize=sanitize, compiler=compiler, coverage=coverage,
                          timeout=timeout)
    if not b.ok:
        return Checks([], errors=["the build failed: " + "\n".join(b.errors[:15])], out=b.out)
    proj = project_dir()
    if (b.build_dir / "CTestTestfile.cmake").exists():
        xml = b.build_dir / "ctest-junit.xml"
        r = run(["ctest", "--test-dir", str(b.build_dir), "--output-junit", str(xml),
                 "--output-on-failure", "--timeout", "120", "-j", "4"],
                cwd=proj, timeout=timeout, env=_san_env())
        checks = junit_results(xml.parent) if xml.exists() else []
    elif (proj / "meson.build").exists():
        r = run(["meson", "test", "-C", str(b.build_dir), "--print-errorlogs"], cwd=proj,
                timeout=timeout, env=_san_env())
        log = b.build_dir / "meson-logs" / "testlog.junit.xml"
        checks = junit_results(log.parent) if log.exists() else []
    else:
        return Checks([], errors=["no CTest or meson tests (add enable_testing() and add_test / "
                                  "gtest_discover_tests)"], out=b.out)
    for c in checks:
        found = sanitizer_reports(c.get("detail") or "")
        if found:
            c["ok"] = False
            c["detail"] = "\n".join(found[:5]) + "\n" + (c.get("detail") or "")[-1200:]
    # CTest's JUnit file may leave a test's output out; --output-on-failure
    # printed it, so sanitizer findings there go to the failed tests.
    san = sanitizer_reports(r.out)
    if san:
        for c in checks:
            if not c["ok"] and "Sanitizer" not in (c.get("detail") or "") and \
                    "runtime error" not in (c.get("detail") or ""):
                c["detail"] = "\n".join(san[:5]) + "\n" + (c.get("detail") or "")
    errors = [] if checks else [f"no tests ran (exit {r.returncode})"]
    return Checks(checks, errors=errors, out=r.out)


def cc_run(program, args=(), input: str | None = None, timeout: float = 120,
           env: dict | None = None) -> Result:
    """Run a built program (a path, or a name searched for in the newest
    build folder) with sanitizer options set; r.sanitizer lists findings."""
    p = Path(program)
    if not p.is_absolute() and not (project_dir() / p).exists():
        builds = sorted(TMP.glob("build-*"), key=lambda d: d.stat().st_mtime, reverse=True)
        for d in builds:
            hit = [f for f in d.rglob(p.name) if f.is_file() and os.access(f, os.X_OK)]
            if hit:
                p = hit[0]
                break
    elif not p.is_absolute():
        p = project_dir() / p
    r = run([str(p), *map(str, args)], cwd=project_dir(), timeout=timeout, env=_san_env(env),
            input=input)
    r.sanitizer = sanitizer_reports(r.out)
    return r


def clang_tidy(files=None, checks: str = "bugprone-*,clang-analyzer-*,performance-*,"
               "-bugprone-easily-swappable-parameters", timeout: float = 900) -> list[str]:
    """clang-tidy findings ('file:line:col: warning: msg [check]') using the
    compile_commands.json of a build (made if needed)."""
    proj = project_dir()
    dbs = sorted(TMP.glob("build-*/compile_commands.json"))
    if not dbs:
        cc_build()
        dbs = sorted(TMP.glob("build-*/compile_commands.json"))
    assert dbs, "no compile_commands.json (clang-tidy needs a CMake or Meson build)"
    srcs = [str(proj / f) for f in files] if files else [
        str(p) for p in sorted(proj.rglob("*")) if p.suffix in (".c", ".cc", ".cpp", ".cxx")
        and not any(part.startswith("build") for part in p.relative_to(proj).parts)]
    r = run(["clang-tidy", "-p", str(dbs[0].parent), f"--checks={checks}", "--quiet", *srcs],
            cwd=proj, timeout=timeout)
    return [ln.strip().replace(str(proj) + "/", "") for ln in r.out.splitlines()
            if C_DIAG.match(ln.strip()) and str(proj) in ln]


def cppcheck(paths=None, timeout: float = 600) -> list[str]:
    """cppcheck findings (warning, performance, portability; not style) as
    'file:line:col: severity: msg [id]'."""
    proj = project_dir()
    targets = [str(p) for p in (paths or [])] or ["."]
    r = run(["cppcheck", "--enable=warning,performance,portability", "--inline-suppr", "--quiet",
             "--suppress=missingIncludeSystem", "--std=c++20",
             "--template={file}:{line}:{column}: {severity}: {message} [{id}]", *targets],
            cwd=proj, timeout=timeout)
    return [ln.strip() for ln in r.out.splitlines()
            if re.match(r"^[^:\s]+:\d+:\d+: \w+: ", ln.strip())]


def clang_format_files(style: str = "file") -> list[str]:
    """Source files clang-format would change ([] when formatted). With no
    .clang-format in the project, style "file" falls back to LLVM."""
    proj = project_dir()
    srcs = [p for p in sorted(proj.rglob("*")) if p.suffix in (".c", ".h", ".cc", ".cpp", ".hpp", ".cxx")]
    bad = []
    for p in srcs:
        r = run(["clang-format", f"--style={style}", "--fallback-style=LLVM", "--dry-run", "-Werror",
                 str(p)], cwd=proj, timeout=60)
        if not r.ok:
            bad.append(str(p.relative_to(proj)))
    return bad


def valgrind(program, args=(), input: str | None = None, timeout: float = 300) -> Result:
    """Run under valgrind memcheck (full leak check). r.errors: the error
    lines ('Invalid read of size 4', 'definitely lost: 40 bytes...')."""
    p = Path(program) if Path(program).is_absolute() else project_dir() / program
    r = run(["valgrind", "--leak-check=full", "--error-exitcode=99", "--errors-for-leak-kinds=definite",
             str(p), *map(str, args)], cwd=project_dir(), timeout=timeout, input=input)
    r.errors = [re.sub(r"^==\d+== ", "", ln).strip() for ln in r.out.splitlines()
                if re.search(r"==\d+== (Invalid |Conditional jump|Use of uninitialised|Mismatched free|"
                             r"\s*definitely lost: [1-9]|Syscall param)", ln)]
    return r


def coverage(build: CBuild | None = None) -> dict:
    """Line coverage per source file (percent) and "total", from gcovr after
    a coverage build ran its tests: ctest(coverage=True) first."""
    proj = project_dir()
    b = build.build_dir if build else next(iter(sorted(TMP.glob("build-*-cov*"))), None)
    assert b, "no coverage build - run game.ctest(coverage=True) first"
    summary = TMP / "coverage-summary.json"
    r = run(["gcovr", "--root", str(proj), "--json-summary", str(summary), str(b),
             "--exclude", ".*tests?/.*", "--exclude", ".*_deps/.*"], cwd=proj, timeout=300)
    assert summary.exists(), r.out[-2000:]
    data = json.loads(summary.read_text())
    res = {f["filename"]: f.get("line_percent", 0.0) for f in data.get("files", [])}
    res["total"] = data.get("line_percent", 0.0)
    return res


# ------------------------------------------------------------------ SQL

PG_BIN = Path(os.environ.get("FORGE_PG_BIN", "/usr/lib/postgresql/17/bin"))
_PG = {}
_MY = {}


class Postgres:
    """A throwaway PostgreSQL server in TMP (unix socket only, trust auth,
    fsync off) - started once per test process by game.pg()."""

    def __init__(self, root: Path):
        self.root, self.data, self.sock = root, root / "data", root / "sock"
        self.user = "postgres"

    def start(self) -> "Postgres":
        self.root.mkdir(parents=True, exist_ok=True)
        self.sock.mkdir(exist_ok=True)
        if not (self.data / "PG_VERSION").exists():
            run([str(PG_BIN / "initdb"), "-D", str(self.data), "-U", self.user, "-A", "trust",
                 "--no-sync", "-E", "UTF8", "--locale=C.UTF-8"], timeout=180, check=True)
        opts = (f"-k {self.sock} -c listen_addresses='' -c fsync=off -c synchronous_commit=off "
                f"-c full_page_writes=off -c max_connections=50")
        run([str(PG_BIN / "pg_ctl"), "-D", str(self.data), "-l", str(self.root / "log.txt"),
             "-o", opts, "-w", "-t", "60", "start"], timeout=120, check=True)
        return self

    def stop(self) -> None:
        run([str(PG_BIN / "pg_ctl"), "-D", str(self.data), "-m", "immediate", "stop"], timeout=60)

    def dsn(self, db: str = "postgres") -> str:
        """For psycopg: psycopg.connect(game.pg().dsn("app"))."""
        return f"host={self.sock} user={self.user} dbname={db}"

    def create_db(self, name: str, fresh: bool = True) -> str:
        if fresh:
            self.psql(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)', db="postgres")
        r = self.psql(f'CREATE DATABASE "{name}"', db="postgres")
        assert r.ok or "already exists" in r.out, r.out
        return name

    def psql(self, sql: str | None = None, db: str = "postgres", file=None,
             timeout: float = 300) -> Result:
        """psql with ON_ERROR_STOP: .ok, .out, .errors ('ERROR: ...' lines)."""
        cmd = ["psql", "-X", "-q", "-v", "ON_ERROR_STOP=1", "-h", str(self.sock), "-U", self.user,
               "-d", db]
        cmd += ["-f", str(file)] if file else ["-c", sql]
        r = run(cmd, cwd=project_dir(), timeout=timeout)
        r.errors = [ln.strip() for ln in r.out.splitlines() if re.search(r"\b(ERROR|FATAL):", ln)]
        return r

    def query(self, sql: str, params=None, db: str = "postgres") -> list[tuple]:
        """Rows of a query (psycopg)."""
        import psycopg
        with psycopg.connect(self.dsn(db), autocommit=True) as conn:
            cur = conn.execute(sql, params)
            return cur.fetchall() if cur.description else []


def pg(fresh_db: str | None = None) -> Postgres:
    """The test process's PostgreSQL server (started on first use);
    fresh_db="app" also (re)creates that database."""
    if "srv" not in _PG:
        import atexit
        _PG["srv"] = Postgres(TMP / "pg").start()
        atexit.register(_PG["srv"].stop)
    if fresh_db:
        _PG["srv"].create_db(fresh_db)
    return _PG["srv"]


def sql_files(where="migrations") -> list[Path]:
    """The .sql files of a folder in name order (V1__x.sql, 001_x.sql... -
    numbers compared as numbers)."""
    d = project_dir() / where
    key = lambda p: [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", p.name)]
    return sorted((p for p in d.glob("*.sql")), key=key)


def pg_apply(where="migrations", db: str = "app", fresh: bool = True) -> Result:
    """Apply a folder's migrations in order to a (fresh) database; stops at
    the first error. .ok, .errors ('psql:migrations/002_x.sql:7: ERROR: ...'),
    .applied (the files that ran)."""
    srv = pg()
    if fresh:
        srv.create_db(db)
    applied, out = [], ""
    for f in sql_files(where):
        r = srv.psql(db=db, file=f.relative_to(project_dir()))
        out += r.out
        if not r.ok:
            res = Result(r.returncode, out)
            res.errors, res.applied = r.errors or [r.out.strip()[-800:]], applied
            return res
        applied.append(str(f.relative_to(project_dir())))
    res = Result(0, out)
    res.errors, res.applied = [], applied
    return res


TAP_PG = re.compile(r"^(not ok|ok)\s+\d+\s*(?:-\s*)?(.*?)(?:\s+#\s*(SKIP|skip|TODO).*)?$")


def pg_test(paths="tests", db: str = "app", setup: str | None = "migrations",
            timeout: float = 900) -> Checks:
    """pgTAP tests (.sql files using plan(), ok(), is(), has_table(),
    throws_ok()... and finish()) run with pg_prove against `db`, after the
    `setup` migrations are applied to a fresh copy. CREATE EXTENSION pgtap is
    done for you. One check per assertion, named '<file>: <description>'."""
    srv = pg()
    if setup:
        a = pg_apply(setup, db=db)
        if not a.ok:
            return Checks([], errors=["migrations failed: " + "; ".join(a.errors[:3])], out=a.out)
    else:
        srv.create_db(db)
    srv.psql("CREATE EXTENSION IF NOT EXISTS pgtap", db=db)
    proj = project_dir()
    p = proj / paths if isinstance(paths, str) else None
    files = sorted(p.rglob("*.sql")) if p and p.is_dir() else [proj / x for x in
                                                                 ([paths] if isinstance(paths, str) else paths)]
    checks, errors, out = [], [], ""
    for f in files:
        rel = str(f.relative_to(proj))
        r = run(["pg_prove", "--verbose", "--failures", "-h", str(srv.sock), "-U", srv.user, "-d", db,
                 rel], cwd=proj, timeout=timeout)
        out += r.out
        n = 0
        cur = None
        for ln in r.out.splitlines():
            s = ln.strip()
            m = TAP_PG.match(s)
            if m and not m.group(3):
                n += 1
                cur = {"name": f"{rel}: {m.group(2) or 'test ' + str(n)}", "ok": m.group(1) == "ok",
                       "detail": None}
                checks.append(cur)
            elif cur is not None and not cur["ok"] and s.startswith("#"):
                cur["detail"] = ((cur["detail"] or "") + s[1:].strip() + "\n")[-1500:]
            elif re.search(r"\b(ERROR|FATAL):|Dubious|Bad plan|No plan found", s):
                errors.append(f"{rel}: {s}")
    if not checks:
        errors.append("pgTAP ran no tests")
    return Checks(checks, errors=errors, out=out)


def explain(sql: str, db: str = "app", analyze: bool = False) -> dict:
    """PostgreSQL's plan as JSON (EXPLAIN (FORMAT JSON)); with analyze=True
    the query runs. game.plan_nodes(plan) lists the node types and tables."""
    opts = "ANALYZE, BUFFERS, " if analyze else ""
    rows = pg().query(f"EXPLAIN ({opts}FORMAT JSON) {sql}", db=db)
    v = rows[0][0]
    return (json.loads(v) if isinstance(v, str) else v)[0]


def plan_nodes(plan: dict) -> list[tuple[str, str | None]]:
    """[(node type, relation or index name)] for every node of a plan, e.g.
    ('Index Scan', 'orders_customer_idx') or ('Seq Scan', 'orders')."""
    out = []

    def walk(n):
        out.append((n.get("Node Type"), n.get("Index Name") or n.get("Relation Name")))
        for c in n.get("Plans", []) or []:
            walk(c)
    walk(plan.get("Plan", plan))
    return out


class MariaDB:
    """A throwaway MariaDB server in TMP (unix socket, no network, no
    grant tables - every connection is root)."""

    def __init__(self, root: Path):
        self.root, self.data, self.socket = root, root / "data", root / "mysql.sock"

    def start(self) -> "MariaDB":
        self.root.mkdir(parents=True, exist_ok=True)
        if not (self.data / "mysql").exists():
            run(["mariadb-install-db", "--no-defaults", f"--datadir={self.data}",
                 "--auth-root-authentication-method=normal", "--skip-test-db"], timeout=300, check=True)
        import subprocess as sp
        self.proc = sp.Popen(["mariadbd", "--no-defaults", f"--datadir={self.data}",
                              f"--socket={self.socket}", "--skip-networking", "--skip-grant-tables",
                              f"--pid-file={self.root / 'pid'}", "--innodb-flush-log-at-trx-commit=0",
                              f"--log-error={self.root / 'error.log'}"],
                             stdout=sp.DEVNULL, stderr=sp.DEVNULL)
        for _ in range(120):
            if self.socket.exists() and self.sql("SELECT 1").ok:
                return self
            time.sleep(0.5)
        raise AssertionError("MariaDB did not start:\n" + (self.root / "error.log").read_text()[-2000:])

    def stop(self) -> None:
        if getattr(self, "proc", None):
            self.proc.terminate()
            try:
                self.proc.wait(30)
            except Exception:
                self.proc.kill()

    def sql(self, sql: str | None = None, db: str | None = None, file=None,
            timeout: float = 300) -> Result:
        cmd = ["mariadb", "--no-defaults", f"--socket={self.socket}", "-u", "root", "--batch"]
        if db:
            cmd.append(db)
        r = run(cmd + ([] if file else ["-e", sql]), cwd=project_dir(), timeout=timeout,
                input=(project_dir() / file).read_text() if file else None)
        r.errors = [ln.strip() for ln in r.out.splitlines() if re.match(r"^ERROR \d+", ln.strip())]
        return r

    def create_db(self, name: str) -> str:
        self.sql(f"DROP DATABASE IF EXISTS `{name}`; CREATE DATABASE `{name}`")
        return name

    def query(self, sql: str, params=None, db: str | None = None) -> list[tuple]:
        import pymysql
        conn = pymysql.connect(unix_socket=str(self.socket), user="root", database=db,
                               autocommit=True)
        try:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                return list(cur.fetchall()) if cur.description else []
        finally:
            conn.close()


def mariadb(fresh_db: str | None = None) -> MariaDB:
    """The test process's MariaDB server (started on first use, 5-20 s)."""
    if "srv" not in _MY:
        import atexit
        _MY["srv"] = MariaDB(TMP / "mariadb").start()
        atexit.register(_MY["srv"].stop)
    if fresh_db:
        _MY["srv"].create_db(fresh_db)
    return _MY["srv"]


def sqlfluff(paths=None, dialect: str = "postgres", rules: str | None = None,
             timeout: float = 300) -> list[str]:
    """sqlfluff lint findings as 'file:line:col: CODE message'. Layout
    rules, RF04 (keywords as names), AM04 (SELECT *), AL01 and CP02 are left
    out unless `rules` names them; a .sqlfluff file in the project overrides
    all of that."""
    proj = project_dir()
    targets = [str(p) for p in (paths or [])] or ["."]
    cmd = ["sqlfluff", "lint", "--format", "json", "--nofail", "--disable-progress-bar"]
    if not (proj / ".sqlfluff").exists():
        cmd += ["--dialect", dialect, "--exclude-rules", "layout,RF04,AM04,AL01,CP02"]
    if rules:
        cmd += ["--rules", rules]
    r = run(cmd + targets, cwd=proj, timeout=timeout)
    m = re.search(r"^\[.*\]\s*$", r.out, re.S | re.M)
    out = []
    for f in json.loads(m.group(0)) if m else []:
        for v in f.get("violations", []):
            ln = v.get("start_line_no", v.get("line_no"))
            col = v.get("start_line_pos", v.get("line_pos"))
            out.append(f"{f['filepath'].removeprefix('./')}:{ln}:{col}: {v.get('code')} {v.get('description')}")
    return out


def sql_parse_errors(sql: str, dialect: str = "postgres") -> list[str]:
    """Syntax problems sqlglot finds parsing `sql` in a dialect (postgres,
    mysql, sqlite, duckdb, tsql...); [] when it parses."""
    import sqlglot
    from sqlglot.errors import ParseError
    try:
        sqlglot.parse(sql, read=dialect, error_level=sqlglot.ErrorLevel.RAISE)
        return []
    except ParseError as exc:
        return [e.get("description", str(e)) + f" (line {e.get('line')}, col {e.get('col')})"
                for e in exc.errors] or [str(exc)]


# ------------------------------------------------------------------ microcontrollers

# Bare-metal targets forge can build AND run (QEMU boards with semihosting:
# printf goes to the test's output and main's return value is the exit code).
MCU_TARGETS = {
    "cortex-m0": {"cc": "arm-none-eabi-gcc", "flags": ["-mcpu=cortex-m0", "-mthumb"],
                  "qemu": ["qemu-system-arm", "-M", "microbit"],
                  "flash": (0x0, 0x40000), "ram": (0x20000000, 0x4000)},
    "cortex-m3": {"cc": "arm-none-eabi-gcc", "flags": ["-mcpu=cortex-m3", "-mthumb"],
                  "qemu": ["qemu-system-arm", "-M", "mps2-an385", "-cpu", "cortex-m3"],
                  "flash": (0x0, 0x400000), "ram": (0x20000000, 0x400000)},
    "cortex-m4": {"cc": "arm-none-eabi-gcc",
                  "flags": ["-mcpu=cortex-m4", "-mthumb", "-mfpu=fpv4-sp-d16", "-mfloat-abi=hard"],
                  "qemu": ["qemu-system-arm", "-M", "mps2-an386", "-cpu", "cortex-m4"],
                  "flash": (0x0, 0x400000), "ram": (0x20000000, 0x400000)},
    "cortex-m7": {"cc": "arm-none-eabi-gcc",
                  "flags": ["-mcpu=cortex-m7", "-mthumb", "-mfpu=fpv5-d16", "-mfloat-abi=hard"],
                  "qemu": ["qemu-system-arm", "-M", "mps2-an500", "-cpu", "cortex-m7"],
                  "flash": (0x0, 0x400000), "ram": (0x20000000, 0x400000)},
    "riscv32": {"cc": "riscv64-unknown-elf-gcc", "flags": ["-march=rv32imac", "-mabi=ilp32"],
                "qemu": ["qemu-system-riscv32", "-M", "virt", "-bios", "none"],
                "flash": (0x80000000, 0x200000), "ram": (0x80200000, 0x200000)},
    "avr": {"cc": "avr-gcc", "flags": ["-mmcu=atmega328p", "-DF_CPU=16000000UL"],
            "simavr": ["simavr", "-m", "atmega328p", "-f", "16000000"]},
}
MCU_SIZE = re.compile(r"^\s*(\d+)\s+(\d+)\s+(\d+)\s+\d+\s+[0-9a-f]+\s+\S+", re.M)


class MCUBuild(Result):
    def __init__(self, code: int, out: str, elf: Path, target: str):
        super().__init__(code, out)
        self.elf, self.target = elf, target

    @property
    def errors(self) -> list[str]:
        return CBuild(self.returncode, self.out, self.elf.parent).errors if not self.ok else []

    @property
    def warnings(self) -> list[str]:
        return CBuild(self.returncode, self.out, self.elf.parent).warnings

    @property
    def size(self) -> dict:
        return mcu_size(self.elf, self.target) if self.ok else {}

    def assert_ok(self) -> "MCUBuild":
        assert self.ok, f"the {self.target} build failed:\n" + "\n".join(self.errors[:20])
        return self


def _mcu_sources(paths, exclude=()) -> list[Path]:
    proj = project_dir()
    if paths:
        return [proj / p for p in paths]
    out = [p for p in sorted((proj / "src").rglob("*")) if p.suffix in (".c", ".cpp", ".S", ".s")]
    return [p for p in out if p.name not in exclude]


def mcu_build(target: str = "cortex-m3", sources=None, out: str = "firmware.elf",
              defines: dict | None = None, includes=("include", "src"), opt: str = "-O2",
              extra=(), exclude=(), timeout: float = 300) -> MCUBuild:
    """Compile bare-metal firmware for a target in MCU_TARGETS (cortex-m0/3/
    4/7, riscv32: picolibc with semihosting; avr: avr-libc, atmega328p at
    16 MHz) from src/ (or `sources`), with include/ and src/ on the include
    path. Never raises: .ok, .errors, .warnings, .elf, .size ({text, data,
    bss, flash, ram}); .assert_ok()."""
    t = MCU_TARGETS[target]
    proj = project_dir()
    b = TMP / f"mcu-{target}"
    b.mkdir(parents=True, exist_ok=True)
    elf = b / out
    srcs = _mcu_sources(sources, exclude)
    cmd = [t["cc"], *t["flags"], opt, "-g", "-Wall", "-Wextra", "-ffunction-sections",
           "-fdata-sections", *[f"-I{proj / i}" for i in includes],
           *[f"-D{k}={v}" if v is not None else f"-D{k}" for k, v in (defines or {}).items()]]
    if target == "avr":
        cmd += ["-Wl,--gc-sections"]
    else:
        f0, fn = t["flash"]
        r0, rn = t["ram"]
        cmd += ["--specs=picolibc.specs", "--oslib=semihost", "--crt0=semihost", "-T", "picolibc.ld",
                f"-Wl,--defsym=__flash={f0:#x}", f"-Wl,--defsym=__flash_size={fn:#x}",
                f"-Wl,--defsym=__ram={r0:#x}", f"-Wl,--defsym=__ram_size={rn:#x}",
                "-Wl,--gc-sections"]
    cmd += [*map(str, extra), *map(str, srcs), "-o", str(elf)]
    r = run(cmd, cwd=proj, timeout=timeout)
    return MCUBuild(r.returncode, r.out, elf, target)


def mcu_size(elf, target: str = "cortex-m3") -> dict:
    """{text, data, bss, flash (text+data), ram (data+bss)} in bytes."""
    tool = "avr-size" if target == "avr" else ("riscv64-unknown-elf-size" if target.startswith("riscv")
                                               else "arm-none-eabi-size")
    r = run([tool, str(elf)], timeout=60)
    m = MCU_SIZE.search(r.out)
    assert m, r.out
    text, data, bss = map(int, m.groups())
    return {"text": text, "data": data, "bss": bss, "flash": text + data, "ram": data + bss}


ANSI = re.compile(r"\x1b\[[0-9;]*m")


def mcu_run(build_or_elf, target: str | None = None, timeout: float = 30,
            args=()) -> Result:
    """Run firmware: QEMU (Cortex-M, RISC-V) with semihosting - printf output
    and main's return value as the exit code - or simavr (AVR: UART0 output;
    the run ends when the program sleeps with interrupts off, e.g. cli();
    sleep_cpu();). A program that never ends is stopped after `timeout`
    (returncode 124, out ends with TIMEOUT)."""
    if isinstance(build_or_elf, MCUBuild):
        elf, target = build_or_elf.elf, target or build_or_elf.target
    else:
        elf = Path(build_or_elf) if Path(build_or_elf).is_absolute() else project_dir() / build_or_elf
    target = target or "cortex-m3"
    t = MCU_TARGETS[target]
    if "simavr" in t:
        r = run([*t["simavr"], str(elf)], timeout=timeout)
        text = ANSI.sub("", r.out)
        lines = [ln[:-1] if ln.endswith(".") and not ln.startswith(("Loaded", "..")) else ln
                 for ln in text.splitlines()
                 if not re.match(r"^(Loaded \d+|\.\.\. |avr_|simavr|uart_pty)", ln)]
        return Result(r.returncode, "\n".join(lines) + ("\n" if lines else ""))
    cmd = [*t["qemu"], "-nographic", "-monitor", "none", "-serial", "none",
           "-semihosting-config", "enable=on,target=native" +
           "".join(f",arg={a}" for a in [elf.name, *map(str, args)]), "-kernel", str(elf)]
    return run(cmd, timeout=timeout)


UNITY_LINE = re.compile(r"^(?P<file>[^:\n]+):(?P<line>\d+):(?P<name>\w+):(?P<status>PASS|FAIL|IGNORE)"
                        r"(?::\s*(?P<msg>.*))?$", re.M)
UNITY = Path(os.environ.get("UNITY_PATH", "/opt/unity"))


def unity_runner(test_file: Path) -> str:
    """A Unity main() for every `void test_x(void)` in a test file."""
    names = re.findall(r"^\s*void\s+(test_\w+)\s*\(\s*void\s*\)", test_file.read_text(), re.M)
    return ("#include \"unity.h\"\n"
            "__attribute__((weak)) void setUp(void) {}\n__attribute__((weak)) void tearDown(void) {}\n"
            + "".join(f"extern void {n}(void);\n" for n in names)
            + "int main(void) {\n    UNITY_BEGIN();\n"
            + "".join(f"    RUN_TEST({n});\n" for n in names)
            + "    return UNITY_END();\n}\n")


def unity_test(target: str = "host", tests: str = "tests", sources=None, exclude=("main.c",),
               sanitize: bool = True, timeout: float = 600) -> Checks:
    """Unity unit tests: each tests/test_*.c is compiled with the project's
    src/ files (not main.c) and a generated runner, then run - on the host
    (gcc, with ASan/UBSan) or on a QEMU target ("cortex-m3", "riscv32"...).
    One check per test function."""
    proj = project_dir()
    files = sorted((proj / tests).glob("test_*.c"))
    assert files, f"no {tests}/test_*.c files"
    srcs = _mcu_sources(sources, exclude)
    checks, errors, out = [], [], ""
    for tf in files:
        runner = TMP / f"runner_{tf.stem}.c"
        runner.write_text(unity_runner(tf))
        unity_c = [UNITY / "src" / "unity.c"]
        defs = {"UNITY_INCLUDE_DOUBLE": None, "UNITY_OUTPUT_COLOR": None}
        if target == "host":
            exe = TMP / f"unity-host-{tf.stem}"
            cmd = ["gcc", "-std=c11", "-g", "-O1", "-Wall", "-Wextra", f"-I{UNITY / 'src'}",
                   f"-I{proj / 'include'}", f"-I{proj / 'src'}", "-DUNITY_INCLUDE_DOUBLE"]
            if sanitize:
                cmd += ["-fsanitize=address,undefined", "-fno-omit-frame-pointer",
                        "-fno-sanitize-recover=all"]
            cmd += [str(tf), str(runner), *map(str, unity_c), *map(str, srcs), "-o", str(exe), "-lm"]
            b = run(cmd, cwd=proj, timeout=timeout)
            if not b.ok:
                errors += [f"{tf.name}: " + e for e in CBuild(b.returncode, b.out, TMP).errors[:10]]
                out += b.out
                continue
            r = run([str(exe)], cwd=proj, timeout=timeout, env=_san_env())
        else:
            b = mcu_build(target, sources=[*srcs, tf, runner, *unity_c], out=f"unity-{tf.stem}.elf",
                          defines={"UNITY_INCLUDE_DOUBLE": None}, opt="-O1",
                          includes=("include", "src", str(UNITY / "src")))
            if not b.ok:
                errors += [f"{tf.name}: " + e for e in b.errors[:10]]
                out += b.out
                continue
            r = mcu_run(b, timeout=timeout)
        out += r.out
        found = [m.groupdict() for m in UNITY_LINE.finditer(r.out)]
        for m in found:
            if m["status"] == "IGNORE":
                continue
            checks.append({"name": f"{tf.stem}.{m['name']}", "ok": m["status"] == "PASS",
                           "detail": (f"{m['file']}:{m['line']}: {m['msg']}" if m["status"] == "FAIL"
                                      else None)})
        san = sanitizer_reports(r.out)
        if san:
            errors.append(f"{tf.name}: " + "; ".join(san[:3]))
        if not found:
            errors.append(f"{tf.name}: no Unity results (exit {r.returncode}): {r.out.strip()[-500:]}")
    return Checks(checks, errors=errors, out=out)


PICO_SDK = Path(os.environ.get("PICO_SDK_PATH", "/opt/pico-sdk"))


class PicoBuild(MCUBuild):
    @property
    def uf2(self) -> Path:
        return self.elf.with_suffix(".uf2")

    @property
    def size(self) -> dict:
        return mcu_size(self.elf, "cortex-m0") if self.ok else {}


def pico_build(board: str = "pico", target: str | None = None, defines: dict | None = None,
               path: str = ".", timeout: float = 900) -> PicoBuild:
    """Build a Raspberry Pi Pico SDK project (CMakeLists.txt with
    pico_sdk_init()) for a board: pico, pico_w, pico2, pico2_w (RP2350 Arm;
    PICO_PLATFORM=rp2350-riscv for the RISC-V cores). `target` is the
    executable (default: the first with a .uf2); `path` the folder with the
    project's CMakeLists.txt. .ok, .errors, .elf, .uf2, .size."""
    proj = project_dir()
    b = TMP / f"pico-{board}-{re.sub(r'[^\w]+', '_', path)}"
    cfg = ["cmake", "-S", str(proj / path), "-B", str(b), "-G", "Ninja", f"-DPICO_BOARD={board}",
           "-DCMAKE_BUILD_TYPE=Release",
           *[f"-D{k}={v}" for k, v in (defines or {}).items()]]
    r = run(cfg, cwd=proj, timeout=timeout, env={"PICO_SDK_PATH": str(PICO_SDK)})
    if r.ok:
        r2 = run(["cmake", "--build", str(b)] + (["--target", target] if target else []),
                 cwd=proj, timeout=timeout)
        r = Result(r2.returncode, r.out + r2.out)
    uf2s = sorted(b.glob(f"{target or '*'}.uf2"))
    elf = uf2s[0].with_suffix(".elf") if uf2s else b / f"{target or 'firmware'}.elf"
    return PicoBuild(r.returncode, r.out, elf, "pico")


UF2_FAMILIES = {0xe48bff56: "rp2040", 0xe48bff57: "absolute", 0xe48bff58: "data",
                0xe48bff59: "rp2350-arm-s", 0xe48bff5a: "rp2350-riscv"}


def uf2_info(path) -> dict:
    """A UF2 file's blocks: {"blocks", "families" (names, or hex ids),
    "start", "end", "bytes"}; raises AssertionError on a malformed file."""
    p = Path(path) if Path(path).is_absolute() else project_dir() / path
    data = p.read_bytes()
    assert data and len(data) % 512 == 0, f"{p.name}: not a whole number of 512-byte blocks"
    fams, start, end, total, n = set(), None, 0, 0, len(data) // 512
    for i in range(n):
        blk = data[i * 512:(i + 1) * 512]
        m0, m1, flags, addr, size, bno, nblk, fam = struct.unpack_from("<8I", blk, 0)
        assert m0 == 0x0A324655 and m1 == 0x9E5D5157, f"{p.name}: block {i} has a bad magic"
        assert struct.unpack_from("<I", blk, 508)[0] == 0x0AB16F30, f"{p.name}: block {i} bad end magic"
        if flags & 0x2000:
            fams.add(UF2_FAMILIES.get(fam, hex(fam)))
        start = addr if start is None else min(start, addr)
        end = max(end, addr + size)
        total += size
    return {"blocks": n, "families": sorted(fams), "start": start, "end": end, "bytes": total}


def mcu_objcopy(elf, fmt: str = "ihex", target: str = "cortex-m3") -> Path:
    """The firmware as Intel hex ("ihex") or a raw image ("binary")."""
    tool = {"avr": "avr-objcopy"}.get(target, "riscv64-unknown-elf-objcopy" if target.startswith("riscv")
                                      else "arm-none-eabi-objcopy")
    elf = Path(elf)
    out = elf.with_suffix(".hex" if fmt == "ihex" else ".bin")
    run([tool, "-O", fmt] + (["-R", ".eeprom"] if target == "avr" else []) + [str(elf), str(out)],
        timeout=60, check=True)
    return out


MPY_FAKES = Path(os.environ.get("FORGE_MPY_FAKES", "/opt/micropython-fakes"))
MPY_TEST_RUNNER = r'''
import sys
sys.path.insert(0, "@FAKES@")
sys.path.insert(0, "@SRC@")
sys.path.insert(0, "@DIR@")
import forge_hw
mod = __import__("@MOD@")
names = sorted(n for n in dir(mod) if n.startswith("test_"))
for n in names:
    forge_hw.reset()
    for m in ("setup", "setUp"):
        if hasattr(mod, m):
            getattr(mod, m)()
    try:
        getattr(mod, n)()
        print("FORGE-PASS", n)
    except Exception as e:
        print("FORGE-FAIL", n, type(e).__name__ + ":", e)
'''


def mpy_run(script, args=(), fakes: bool = True, timeout: float = 60,
            input: str | None = None) -> Result:
    """Run a script with the MicroPython unix port, src/ and (fakes=True)
    forge's fake `machine` module first on the path - Pin, Signal, ADC, PWM,
    I2C/SoftI2C, SPI, UART, Timer, WDT backed by forge_hw (see its header).
    The unix port has no real hardware."""
    proj = project_dir()
    s = Path(script) if Path(script).is_absolute() else proj / script
    paths = ([str(MPY_FAKES)] if fakes else []) + [str(proj / "src"), str(proj)]
    env = {"MICROPYPATH": ":".join(paths + [".frozen", "~/.micropython/lib", "/usr/lib/micropython"])}
    return run(["micropython", str(s), *map(str, args)], cwd=proj, timeout=timeout, env=env,
               input=input)


def mpy_test(tests: str = "tests", timeout: float = 120) -> Checks:
    """MicroPython tests under the unix port: every tests/test_*.py module's
    test_* functions (plain asserts), forge_hw reset before each, with the
    fake machine module. One check per function."""
    proj = project_dir()
    files = sorted((proj / tests).glob("test_*.py"))
    assert files, f"no {tests}/test_*.py files"
    checks, errors, out = [], [], ""
    for f in files:
        runner = TMP / f"mpy_runner_{f.stem}.py"
        runner.write_text(MPY_TEST_RUNNER.replace("@FAKES@", str(MPY_FAKES))
                          .replace("@SRC@", str(proj / "src")).replace("@DIR@", str(f.parent))
                          .replace("@MOD@", f.stem))
        r = run(["micropython", str(runner)], cwd=proj, timeout=timeout)
        out += r.out
        found = 0
        for ln in r.out.splitlines():
            m = re.match(r"^FORGE-(PASS|FAIL) (\w+)(?: (.*))?$", ln)
            if m:
                found += 1
                checks.append({"name": f"{f.stem}.{m.group(2)}", "ok": m.group(1) == "PASS",
                               "detail": m.group(3)})
        if not found:
            errors.append(f"{f.name}: {r.out.strip()[-800:] or 'no tests ran'}")
    return Checks(checks, errors=errors, out=out)


def esp_image_info(path, chip: str = "esp32") -> Result:
    """esptool's image_info on an ESP firmware .bin (no device needed): the
    header, segments and checksum; .ok is False for a malformed image."""
    p = Path(path) if Path(path).is_absolute() else project_dir() / path
    r = run(["esptool", "--chip", chip, "image_info", str(p)], timeout=60)
    if "command not found" in r.out or r.returncode == 127:
        r = run(["esptool.py", "--chip", chip, "image_info", str(p)], timeout=60)
    return r


# ------------------------------------------------------------------ devices: ADB

class FakeAndroid:
    """A scripted Android device behind game.adb_fake(): what `adb` (the
    real client, or any library that talks to the adb server) sees.
    shell: {command: stdout} or {command: (stdout, exit_code)} or
    {command: (stdout, stderr, exit_code)}, matched exactly, then by the
    longest prefix ending in "*" ("pm install *"); or a callable(cmd) giving
    one of those. Unknown commands print "<cmd>: not found" with exit 127.
    `getprop NAME` answers from props; `ls`/`cat`/`rm` and push/pull work on
    files {path: bytes}. Every request is in .calls."""

    def __init__(self, serial: str = "FORGE0001", model: str = "Pixel_8", shell=None,
                 props: dict | None = None, files: dict | None = None, state: str = "device"):
        self.serial, self.model, self.state = serial, model, state
        self.shell = shell or {}
        self.props = {"ro.product.model": model.replace("_", " "), "ro.build.version.sdk": "35",
                      "ro.build.version.release": "15", "ro.product.manufacturer": "Google",
                      "ro.serialno": serial}
        self.props.update(props or {})
        self.files = {k: (v.encode() if isinstance(v, str) else v) for k, v in (files or {}).items()}
        self.calls: list[tuple] = []

    def run_shell(self, cmd: str) -> tuple[bytes, bytes, int]:
        self.calls.append(("shell", cmd))
        spec = None
        if callable(self.shell):
            spec = self.shell(cmd)
        elif cmd in self.shell:
            spec = self.shell[cmd]
        else:
            pref = [k for k in self.shell if k.endswith("*") and cmd.startswith(k[:-1])]
            if pref:
                spec = self.shell[max(pref, key=len)]
        if spec is None:
            spec = self._builtin(cmd)
        if isinstance(spec, (str, bytes)):
            spec = (spec, b"", 0)
        elif len(spec) == 2:
            spec = (spec[0], b"", spec[1])
        out, err, code = spec
        enc = lambda x: x.encode() if isinstance(x, str) else (x or b"")
        return enc(out), enc(err), int(code)

    def _builtin(self, cmd: str):
        parts = cmd.split()
        if not parts:
            return ("", 0)
        if parts[0] == "getprop":
            if len(parts) == 1:
                return ("".join(f"[{k}]: [{v}]\n" for k, v in sorted(self.props.items())), 0)
            return (self.props.get(parts[1], "") + "\n", 0)
        if parts[0] == "echo":
            return (" ".join(parts[1:]) + "\n", 0)
        if parts[0] == "cat" and len(parts) == 2:
            if parts[1] in self.files:
                return (self.files[parts[1]], 0)
            return ("", f"cat: {parts[1]}: No such file or directory\n", 1)
        if parts[0] == "rm":
            for p in parts[1:]:
                if not p.startswith("-"):
                    self.files.pop(p, None)
            return ("", 0)
        if parts[0] == "ls":
            d = (parts[-1] if len(parts) > 1 and not parts[-1].startswith("-") else "/").rstrip("/") + "/"
            names = sorted({k[len(d):].split("/")[0] for k in self.files if k.startswith(d)})
            if not names and d.rstrip("/") not in self.files:
                return ("", f"ls: {d}: No such file or directory\n", 1)
            return ("".join(n + "\n" for n in names), 0)
        if parts[0] == "pm" and len(parts) > 1 and parts[1] == "install":
            return ("Success\n", 0)
        return ("", f"/system/bin/sh: {parts[0]}: not found\n", 127)


class AdbFake:
    """A fake adb SERVER on 127.0.0.1 (a free port), speaking the adb
    client's smart-socket protocol: host:version/devices/devices-l/features/
    transport, shell (v2: stdout, stderr and the exit code), exec, sync
    (stat, list, send, recv), reboot, get-state. While it runs,
    ANDROID_ADB_SERVER_PORT points the real `adb` there."""

    def __init__(self, devices):
        import socket
        import threading
        self.devices = {d.serial: d for d in devices}
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(16)
        self.port = self.sock.getsockname()[1]
        self.version = self._client_version()
        self._stop = False
        self.thread = threading.Thread(target=self._serve, daemon=True)

    @staticmethod
    def _client_version() -> int:
        r = run(["adb", "version"], timeout=20)
        m = re.search(r"Android Debug Bridge version \d+\.\d+\.(\d+)", r.out)
        return int(m.group(1)) if m else 41

    def __enter__(self):
        self.thread.start()
        self._old = os.environ.get("ANDROID_ADB_SERVER_PORT")
        os.environ["ANDROID_ADB_SERVER_PORT"] = str(self.port)
        return self

    def __exit__(self, *exc):
        self._stop = True
        try:
            self.sock.close()
        except OSError:
            pass
        if self._old is None:
            os.environ.pop("ANDROID_ADB_SERVER_PORT", None)
        else:
            os.environ["ANDROID_ADB_SERVER_PORT"] = self._old

    def adb(self, *args, timeout: float = 60, input: str | None = None) -> Result:
        """The real adb client against this server."""
        return run(["adb", *map(str, args)], timeout=timeout, input=input,
                   env={"ANDROID_ADB_SERVER_PORT": str(self.port)})

    # -- protocol ---------------------------------------------------------
    def _serve(self):
        import threading
        while not self._stop:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self._client, args=(conn,), daemon=True).start()

    @staticmethod
    def _read(conn, n: int) -> bytes:
        buf = b""
        while len(buf) < n:
            chunk = conn.recv(n - len(buf))
            if not chunk:
                raise EOFError
            buf += chunk
        return buf

    def _req(self, conn) -> str:
        n = int(self._read(conn, 4), 16)
        return self._read(conn, n).decode(errors="replace")

    @staticmethod
    def _okay(conn, payload: bytes | str | None = None):
        if payload is None:
            conn.sendall(b"OKAY")
        else:
            p = payload.encode() if isinstance(payload, str) else payload
            conn.sendall(b"OKAY" + f"{len(p):04x}".encode() + p)

    @staticmethod
    def _fail(conn, msg: str):
        m = msg.encode()
        conn.sendall(b"FAIL" + f"{len(m):04x}".encode() + m)

    def _pick(self, serial: str | None):
        if serial:
            return self.devices.get(serial)
        live = [d for d in self.devices.values() if d.state == "device"]
        return live[0] if len(live) == 1 else None

    def _client(self, conn):
        dev = None
        try:
            while True:
                req = self._req(conn)
                m = re.match(r"^host-serial:([^:]+):(.*)$", req)
                if m:
                    dev = self.devices.get(m.group(1))
                    req = "host:" + m.group(2)
                    if dev is None:
                        return self._fail(conn, f"device '{m.group(1)}' not found")
                if req == "host:version":
                    return self._okay(conn, f"{self.version:04x}")
                if req in ("host:devices", "host:devices-l"):
                    lines = []
                    for i, d in enumerate(self.devices.values(), 1):
                        extra = (f" product:{d.model.lower()} model:{d.model} device:{d.model.lower()} "
                                 f"transport_id:{i}") if req.endswith("-l") else ""
                        lines.append(f"{d.serial}\t{d.state}{extra}\n" if not extra
                                     else f"{d.serial:<22} {d.state}{extra}\n")
                    return self._okay(conn, "".join(lines))
                if req in ("host:features", "host:host-features"):
                    return self._okay(conn, "shell_v2")
                if req == "host:get-state":
                    d = dev or self._pick(None)
                    return self._okay(conn, d.state) if d else self._fail(conn, "no devices/emulators found")
                if req == "host:get-serialno":
                    d = dev or self._pick(None)
                    return self._okay(conn, d.serial) if d else self._fail(conn, "no devices found")
                if req.startswith("host:wait-for-") or req.startswith("host:wait-for"):
                    conn.sendall(b"OKAY")
                    conn.sendall(b"OKAY")
                    return
                if req == "host:kill":
                    conn.sendall(b"OKAY")
                    return
                t = re.match(r"^host:(?:tport:serial:|transport:)(.+)$", req)
                if t or req in ("host:tport:any", "host:transport-any", "host:tport:usb",
                                "host:transport-usb", "host:tport:local", "host:transport-local"):
                    dev = self._pick(t.group(1) if t else None)
                    if dev is None:
                        return self._fail(conn, f"device '{t.group(1)}' not found" if t
                                          else "no devices/emulators found")
                    conn.sendall(b"OKAY")
                    if req.startswith("host:tport:"):
                        conn.sendall(struct.pack("<Q", list(self.devices).index(dev.serial) + 1))
                    continue
                if dev is None:
                    return self._fail(conn, f"unknown host service '{req}'")
                return self._service(conn, dev, req)
        except (EOFError, OSError, ValueError):
            pass
        finally:
            try:
                conn.close()
            except OSError:
                pass

    def _service(self, conn, dev: FakeAndroid, req: str):
        m = re.match(r"^shell(?:,([^:]*))?:(.*)$", req, re.S)
        if m:
            opts = (m.group(1) or "").split(",")
            out, err, code = dev.run_shell(m.group(2))
            conn.sendall(b"OKAY")
            if "v2" in opts:
                if out:
                    conn.sendall(b"\x01" + struct.pack("<I", len(out)) + out)
                if err:
                    conn.sendall(b"\x02" + struct.pack("<I", len(err)) + err)
                conn.sendall(b"\x03" + struct.pack("<I", 1) + bytes([code & 0xFF]))
            else:
                conn.sendall(out + err)
            return
        if req.startswith("exec:"):
            out, err, code = dev.run_shell(req[5:])
            conn.sendall(b"OKAY" + out)
            return
        if req.startswith("reboot:"):
            dev.calls.append(("reboot", req[7:]))
            conn.sendall(b"OKAY")
            return
        if req == "sync:":
            conn.sendall(b"OKAY")
            return self._sync(conn, dev)
        dev.calls.append(("service", req))
        self._fail(conn, f"forge's fake adb does not do '{req}'")

    def _sync(self, conn, dev: FakeAndroid):
        while True:
            cmd = self._read(conn, 4)
            n = struct.unpack("<I", self._read(conn, 4))[0]
            arg = self._read(conn, n).decode(errors="replace") if n else ""
            if cmd == b"QUIT":
                return
            if cmd == b"STAT":
                dev.calls.append(("stat", arg))
                if arg in dev.files:
                    conn.sendall(b"STAT" + struct.pack("<III", 0o100644, len(dev.files[arg]), 1700000000))
                elif any(k.startswith(arg.rstrip("/") + "/") for k in dev.files) or arg in ("/", ""):
                    conn.sendall(b"STAT" + struct.pack("<III", 0o40755, 4096, 1700000000))
                else:
                    conn.sendall(b"STAT" + struct.pack("<III", 0, 0, 0))
            elif cmd == b"LIST":
                dev.calls.append(("list", arg))
                d = arg.rstrip("/") + "/"
                for name in sorted({k[len(d):].split("/")[0] for k in dev.files if k.startswith(d)}):
                    full = d + name
                    is_file = full in dev.files
                    nb = name.encode()
                    conn.sendall(b"DENT" + struct.pack("<IIII", 0o100644 if is_file else 0o40755,
                                                        len(dev.files.get(full, b"")), 1700000000,
                                                        len(nb)) + nb)
                conn.sendall(b"DONE" + b"\x00" * 16)
            elif cmd == b"SEND":
                path = arg.rsplit(",", 1)[0]
                data = b""
                while True:
                    c = self._read(conn, 4)
                    k = struct.unpack("<I", self._read(conn, 4))[0]
                    if c == b"DATA":
                        data += self._read(conn, k)
                    elif c == b"DONE":
                        break
                    else:
                        return self._fail(conn, "bad SEND")
                dev.files[path] = data
                dev.calls.append(("push", path, len(data)))
                conn.sendall(b"OKAY" + b"\x00" * 4)
            elif cmd == b"RECV":
                dev.calls.append(("pull", arg))
                if arg not in dev.files:
                    msg = b"remote object '" + arg.encode() + b"' does not exist"
                    conn.sendall(b"FAIL" + struct.pack("<I", len(msg)) + msg)
                    continue
                data = dev.files[arg]
                for i in range(0, len(data), 64 * 1024):
                    chunk = data[i:i + 64 * 1024]
                    conn.sendall(b"DATA" + struct.pack("<I", len(chunk)) + chunk)
                conn.sendall(b"DONE" + b"\x00" * 4)
            else:
                msg = f"forge's fake adb: sync {cmd!r} is not supported".encode()
                conn.sendall(b"FAIL" + struct.pack("<I", len(msg)) + msg)
                return


def adb_fake(*devices: FakeAndroid) -> AdbFake:
    """with game.adb_fake(game.FakeAndroid(shell={...}, files={...})) as srv:
    run code that calls `adb` (subprocess, scripts via game.run/sh_run) -
    ANDROID_ADB_SERVER_PORT points it at the fake; srv.adb(...) runs the
    real client; each device's .calls and .files show what happened."""
    return AdbFake(devices or [FakeAndroid()])


# ------------------------------------------------------------------ devices: storage, PTP, serial

def disk_image(path, size_mb: int = 64, partitions=None, label: str = "dos") -> Path:
    """An empty disk image (in TMP unless the path is absolute) with
    partitions: [{"size_mb": 32, "type": "c", "fs": "fat32", "label": "BOOT",
    "files": {"config.txt": "..."}}, {"type": "83", "fs": "ext4",
    "files": {...}}] (the last may leave out size_mb to fill the disk).
    FAT is written with mtools and ext4 with mkfs.ext4 -d - nothing is
    mounted. game.part_files(img, n) lists a FAT/ext4 partition's files."""
    p = Path(path) if Path(path).is_absolute() else TMP / path
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("wb") as f:
        f.truncate(size_mb * 1024 * 1024)
    parts = partitions or []
    if not parts:
        return p
    script = f"label: {label}\n" + "".join(
        (f",{x['size_mb']}M," if x.get("size_mb") else ",,") + str(x.get("type", "83")) + "\n"
        for x in parts)
    run(["sfdisk", "-q", str(p)], input=script, timeout=60, check=True)
    table = partition_table(p)
    for i, x in enumerate(parts):
        if not x.get("fs"):
            continue
        off, size = table[i]["start"] * 512, table[i]["size"] * 512
        _format_partition(p, off, size, x)
    return p


def partition_table(img) -> list[dict]:
    """sfdisk's view: [{"start" (sectors), "size" (sectors), "type"}]."""
    r = run(["sfdisk", "-J", str(img)], timeout=60, check=True)
    return json.loads(r.out[r.out.index("{"):])["partitiontable"]["partitions"]


def _format_partition(img: Path, off: int, size: int, spec: dict) -> None:
    fs = spec["fs"]
    part = TMP / f"part-{time.monotonic_ns()}.img"
    if fs.startswith("fat"):
        with part.open("wb") as f:
            f.truncate(size)
        bits = {"fat12": "12", "fat16": "16", "fat32": "32", "fat": "32"}[fs]
        run(["mkfs.fat", "-F", bits, "-n", spec.get("label", "FORGE")[:11], str(part)], timeout=60,
            check=True)
        for name, data in (spec.get("files") or {}).items():
            src = TMP / f"f-{time.monotonic_ns()}"
            src.write_bytes(data.encode() if isinstance(data, str) else data)
            dirs = name.strip("/").split("/")[:-1]
            for k in range(1, len(dirs) + 1):
                run(["mmd", "-D", "s", "-i", str(part), "::/" + "/".join(dirs[:k])], timeout=30)
            run(["mcopy", "-o", "-i", str(part), str(src), "::/" + name.strip("/")], timeout=30, check=True)
    elif fs.startswith("ext"):
        root = TMP / f"root-{time.monotonic_ns()}"
        for name, data in (spec.get("files") or {}).items():
            t = root / name.strip("/")
            t.parent.mkdir(parents=True, exist_ok=True)
            t.write_bytes(data.encode() if isinstance(data, str) else data)
        root.mkdir(exist_ok=True)
        with part.open("wb") as f:
            f.truncate(size)
        run([f"mkfs.{fs}", "-q", "-L", spec.get("label", "rootfs")[:16], "-d", str(root), str(part)],
            timeout=120, check=True)
    elif fs == "exfat":
        with part.open("wb") as f:
            f.truncate(size)
        run(["mkfs.exfat", "-L", spec.get("label", "FORGE")[:11], str(part)], timeout=60, check=True)
    else:
        raise AssertionError(f"unknown filesystem {fs}")
    with img.open("r+b") as dst, part.open("rb") as src:
        dst.seek(off)
        shutil.copyfileobj(src, dst)
    part.unlink()


def _fat_target(img, part: int | None) -> str:
    """mtools' image spec: the whole file, or a partition (1-based)."""
    if not part:
        return str(img)
    start = partition_table(img)[part - 1]["start"] * 512
    return f"{img}@@{start}"


def fat_files(img, part: int | None = None) -> dict[str, int]:
    """Every file on a FAT image or partition: {"/DCIM/IMG_1.JPG": size}."""
    r = run(["mdir", "-/", "-b", "-i", _fat_target(img, part), "::/"], timeout=60)
    out = {}
    for ln in r.out.splitlines():
        ln = ln.strip()
        if ln.startswith("::/") and not ln.endswith("/"):
            path = ln[2:]
            sz = run(["mdir", "-i", _fat_target(img, part), "::" + path], timeout=30).out
            m = re.search(r"\s(\d[\d ]*)\s+\d{4}-\d\d-\d\d", sz)
            out[path] = int(m.group(1).replace(" ", "")) if m else -1
    return out


def fat_read(img, path: str, part: int | None = None) -> bytes:
    """A file's bytes from a FAT image or partition (mtools)."""
    dst = TMP / f"fatread-{time.monotonic_ns()}"
    r = run(["mcopy", "-n", "-i", _fat_target(img, part), "::" + path, str(dst)], timeout=60)
    assert r.ok, r.out
    return dst.read_bytes()


def fat_write(img, path: str, data, part: int | None = None) -> None:
    src = TMP / f"fatwrite-{time.monotonic_ns()}"
    src.write_bytes(data.encode() if isinstance(data, str) else data)
    run(["mcopy", "-o", "-i", _fat_target(img, part), str(src), "::" + path], timeout=60, check=True)


def ext_read(img, path: str, part: int | None = None) -> bytes:
    """A file's bytes from an ext2/3/4 image or partition (debugfs)."""
    src = Path(img)
    if part:
        t = partition_table(img)[part - 1]
        src = TMP / f"ext-{time.monotonic_ns()}.img"
        with open(img, "rb") as f, src.open("wb") as o:
            f.seek(t["start"] * 512)
            o.write(f.read(t["size"] * 512))
    dst = TMP / f"extread-{time.monotonic_ns()}"
    r = run(["debugfs", "-R", f"dump {path} {dst}", str(src)], timeout=60)
    assert dst.exists(), r.out
    return dst.read_bytes()


def camera_dir(files: dict) -> tuple[list[str], Path]:
    """A folder libgphoto2 treats as a camera ("Directory Browse"), with
    files {"DCIM/100CANON/IMG_0001.JPG": bytes}: returns (the gphoto2
    arguments that select it, the folder). Code that imports from a camera
    with gphoto2 runs unchanged against it: gphoto2 *args --get-all-files."""
    d = TMP / f"camera-{time.monotonic_ns()}"
    for name, data in files.items():
        p = d / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data.encode() if isinstance(data, str) else data)
    d.mkdir(parents=True, exist_ok=True)
    return ["--camera", "Directory Browse", "--port", f"disk:{d}"], d


class SerialFake:
    """A virtual serial port pair (socat ptys): code under test opens
    .port like a real /dev/ttyUSB0; the fake answers on the other end with
    `responder(line_bytes) -> bytes | None` (or a dict {request: reply})
    and records every line in .received."""

    def __init__(self, responder=None, eol: bytes = b"\n"):
        import subprocess as sp
        import threading
        d = TMP / f"serial-{time.monotonic_ns()}"
        d.mkdir(parents=True)
        self.port, self._peer = str(d / "ttyFORGE0"), str(d / "peer")
        self.proc = sp.Popen(["socat", f"pty,raw,echo=0,link={self.port}",
                              f"pty,raw,echo=0,link={self._peer}"], stdout=sp.DEVNULL, stderr=sp.DEVNULL)
        for _ in range(100):
            if Path(self.port).exists() and Path(self._peer).exists():
                break
            time.sleep(0.05)
        self.responder, self.eol, self.received = responder, eol, []
        self._stop = False
        self.fd = os.open(self._peer, os.O_RDWR | os.O_NOCTTY)
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def _answer(self, line: bytes):
        r = self.responder
        if r is None:
            return None
        if isinstance(r, dict):
            return r.get(line.strip()) or r.get(line.strip().decode(errors="replace"))
        return r(line)

    def _loop(self):
        import select
        buf = b""
        while not self._stop:
            ready, _, _ = select.select([self.fd], [], [], 0.1)
            if not ready:
                continue
            try:
                chunk = os.read(self.fd, 4096)
            except OSError:
                return
            buf += chunk
            while self.eol in buf:
                line, buf = buf.split(self.eol, 1)
                line = line.rstrip(b"\r")
                self.received.append(line)
                ans = self._answer(line)
                if ans:
                    os.write(self.fd, ans.encode() if isinstance(ans, str) else ans)

    def send(self, data):
        """Unsolicited bytes from the device (a sensor pushing readings)."""
        os.write(self.fd, data.encode() if isinstance(data, str) else data)

    def close(self):
        self._stop = True
        self.thread.join(1)
        os.close(self.fd)
        self.proc.terminate()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def serial_fake(responder=None, eol: bytes = b"\n") -> SerialFake:
    """with game.serial_fake({b"AT": b"OK\\r\\n"}) as dev: open(dev.port)..."""
    return SerialFake(responder, eol)


def umockdev_run(cmd, devices=(), ioctls: dict | None = None, timeout: float = 120,
                 env: dict | None = None) -> Result:
    """Run a command against USB devices RECORDED on real hardware with
    umockdev-record (see ziggurat/README.md, "Recording a USB device"):
    devices are .umockdev files (sysfs/udev), ioctls {"/dev/bus/usb/001/005":
    "phone.ioctl"} the recorded usbdevfs traffic that libusb, libmtp and
    libgphoto2 replay. Paths are relative to the project."""
    proj = project_dir()
    args = ["umockdev-run"]
    for d in devices:
        args += ["--device", str(proj / d)]
    for node, rec in (ioctls or {}).items():
        args += ["--ioctl", f"{node}={proj / rec}"]
    argv = ["bash", "-c", cmd] if isinstance(cmd, str) else [str(c) for c in cmd]
    return run(args + ["--"] + argv, cwd=proj, timeout=timeout, env=env)


# ------------------------------------------------------------------ Raspberry Pi

RPI_ARCH = {"arm64": {"triplet": "aarch64-linux-gnu", "qemu": "qemu-aarch64", "cpu": "aarch64"},
            "armhf": {"triplet": "arm-linux-gnueabihf", "qemu": "qemu-arm", "cpu": "arm"}}


def rpi_build(arch: str = "arm64", sources=None, out: str = "app", libs=(), config: str = "Release",
              timeout: float = 900) -> CBuild:
    """Cross-compile for Raspberry Pi OS (Debian trixie): a CMake project
    (CMakeLists.txt, with a toolchain file forge writes; pkg-config finds the
    arm64 libraries - libgpiod, i2c) or, without one, the given C/C++ files
    (default src/*.c|cpp) linked with `libs` (["gpiod"]). arm64 for Pi 3/4/5
    and Zero 2 W on 64-bit OS; armhf for 32-bit (C library only). .ok,
    .errors, .build_dir; the program is build_dir / out (CMake: its target)."""
    a = RPI_ARCH[arch]
    tri = a["triplet"]
    proj = project_dir()
    b = TMP / f"rpi-{arch}"
    b.mkdir(parents=True, exist_ok=True)
    env = {"PKG_CONFIG_LIBDIR": f"/usr/lib/{tri}/pkgconfig:/usr/share/pkgconfig",
           "PKG_CONFIG_SYSROOT_DIR": "/"}
    if (proj / "CMakeLists.txt").exists():
        tc = b / "toolchain.cmake"
        tc.write_text(f"""set(CMAKE_SYSTEM_NAME Linux)
set(CMAKE_SYSTEM_PROCESSOR {a['cpu']})
set(CMAKE_C_COMPILER {tri}-gcc)
set(CMAKE_CXX_COMPILER {tri}-g++)
set(CMAKE_FIND_ROOT_PATH /usr/{tri} /usr/lib/{tri})
set(CMAKE_LIBRARY_ARCHITECTURE {tri})
set(CMAKE_FIND_ROOT_PATH_MODE_PROGRAM NEVER)
set(CMAKE_FIND_ROOT_PATH_MODE_LIBRARY BOTH)
set(CMAKE_FIND_ROOT_PATH_MODE_INCLUDE BOTH)
set(CMAKE_CROSSCOMPILING_EMULATOR {a['qemu']};-L;/usr/{tri})
""")
        r = run(["cmake", "-S", str(proj), "-B", str(b / "build"), "-G", "Ninja",
                 f"-DCMAKE_TOOLCHAIN_FILE={tc}", f"-DCMAKE_BUILD_TYPE={config}"],
                cwd=proj, timeout=timeout, env=env)
        if r.ok:
            r2 = run(["cmake", "--build", str(b / "build")], cwd=proj, timeout=timeout, env=env)
            r = Result(r2.returncode, r.out + r2.out)
        return CBuild(r.returncode, r.out, b / "build")
    srcs = [proj / s for s in sources] if sources else [
        p for p in sorted((proj / "src").rglob("*")) if p.suffix in (".c", ".cpp", ".cc")]
    cxx = any(p.suffix in (".cpp", ".cc") for p in srcs)
    cmd = [f"{tri}-{'g++' if cxx else 'gcc'}", "-O2", "-g", "-Wall", "-Wextra",
           f"-I{proj / 'include'}", *map(str, srcs), "-o", str(b / out), *[f"-l{x}" for x in libs]]
    r = run(cmd, cwd=proj, timeout=timeout, env=env)
    return CBuild(r.returncode, r.out, b)


def rpi_run(program, args=(), arch: str = "arm64", input: str | None = None,
            timeout: float = 120, env: dict | None = None) -> Result:
    """Run a cross-compiled program under qemu-user with the arm64 (or
    armhf) libraries - no Pi needed for logic, argument handling and output.
    Hardware access (/dev/gpiochip*, /dev/i2c-*) is not there: put it
    behind an interface the tests replace."""
    a = RPI_ARCH[arch]
    p = Path(program)
    if not p.is_absolute():
        cand = [TMP / f"rpi-{arch}" / program, TMP / f"rpi-{arch}" / "build" / program, project_dir() / program]
        p = next((c for c in cand if c.exists()), cand[0])
    return run([a["qemu"], "-L", f"/usr/{a['triplet']}", str(p), *map(str, args)],
               cwd=project_dir(), timeout=timeout, input=input, env=env)


class DTBuild(Result):
    def __init__(self, code: int, out: str, dtbo: Path):
        super().__init__(code, out)
        self.dtbo = dtbo

    @property
    def errors(self) -> list[str]:
        return [ln.strip() for ln in self.out.splitlines() if re.search(r"\b(ERROR|Error|error)\b", ln)]

    @property
    def warnings(self) -> list[str]:
        return [ln.strip() for ln in self.out.splitlines() if "Warning" in ln or "warning" in ln]


def dt_overlay(dts, out: str | None = None, timeout: float = 60) -> DTBuild:
    """Compile a device-tree overlay source (.dts with /plugin/;) to a
    .dtbo with dtc -@ (symbols kept, as the Pi's firmware needs). .ok,
    .errors, .warnings, .dtbo. Copy it to the boot partition's overlays/
    and add dtoverlay=<name> to config.txt."""
    p = Path(dts) if Path(dts).is_absolute() else project_dir() / dts
    o = TMP / "dtbo" / (out or p.stem + ".dtbo")
    o.parent.mkdir(parents=True, exist_ok=True)
    r = run(["dtc", "-@", "-I", "dts", "-O", "dtb", "-o", str(o), str(p)], cwd=project_dir(),
            timeout=timeout)
    return DTBuild(r.returncode, r.out, o)


def dt_decompile(dtb) -> str:
    """A .dtb/.dtbo back to source text (to check what a build produced)."""
    p = Path(dtb) if Path(dtb).is_absolute() else project_dir() / dtb
    return run(["dtc", "-I", "dtb", "-O", "dts", str(p)], timeout=60, check=True).out


def cloud_init_schema(path, timeout: float = 120) -> list[str]:
    """cloud-init's schema check of a user-data / network-config file
    (Raspberry Pi Imager and Pi OS read these from the boot partition):
    [] when valid, else the problems."""
    p = Path(path) if Path(path).is_absolute() else project_dir() / path
    r = run(["cloud-init", "schema", "--config-file", str(p)], timeout=timeout)
    if re.search(r"^Valid schema", r.out, re.M) and "Error" not in r.out:
        return []
    probs = [ln.strip() for ln in r.out.splitlines()
             if ln.strip() and not ln.startswith(("Invalid ", "Error: Invalid schema"))
             and not re.match(r"^\d{4}-\d\d-\d\d [\d:,]+ - .*\[(WARNING|INFO|DEBUG)\]", ln)]
    return probs or [r.out.strip()[-800:]]


PI_CONFIG_LINE = re.compile(r"^(?:\[[\w=, .-]+\]|[A-Za-z_][\w.-]*(?:\[\w+\])?=\S.*|include \S+)$")


def pi_config_problems(path="boot/config.txt") -> list[str]:
    """Syntax problems in a Raspberry Pi config.txt: lines that are not a
    comment, a [filter] section, `include file` or key=value; dtoverlay=/
    dtparam= lines with spaces around commas or '='. (It cannot know every
    firmware key - those still need the Pi documentation.)"""
    p = Path(path) if Path(path).is_absolute() else project_dir() / path
    probs = []
    for i, raw in enumerate(p.read_text().splitlines(), 1):
        ln = raw.strip()
        if not ln or ln.startswith("#"):
            continue
        if not PI_CONFIG_LINE.match(ln):
            probs.append(f"{p.name}:{i}: not key=value, [section] or include: {ln}")
        elif ln.startswith(("dtoverlay=", "dtparam=")) and re.search(r"\s,|,\s|\s=|=\s", ln):
            probs.append(f"{p.name}:{i}: no spaces allowed in {ln.split('=')[0]} lines: {ln}")
    return probs


# ------------------------------------------------------------------ ESP32 (ESP-IDF)

IDF_EXPORT = os.environ.get("IDF_TOOLS_EXPORT_CMD", "/opt/esp/idf/export.sh")
ESP_QEMU = {"esp32": ("qemu-system-xtensa", "esp32"), "esp32c3": ("qemu-system-riscv32", "esp32c3")}


def idf(*args, path: str = ".", timeout: float = 1800, env: dict | None = None) -> Result:
    """`idf.py <args>` on a project folder with ESP-IDF's environment, its
    build folder in TMP (the project stays clean). Offline: no Component
    Manager downloads."""
    proj = project_dir() / path
    b = TMP / f"idf-build-{re.sub(r'[^\w]+', '_', path)}"
    cmd = f". {shlex.quote(IDF_EXPORT)} >/dev/null 2>&1 && idf.py -C {shlex.quote(str(proj))} " \
          f"-B {shlex.quote(str(b))} " + " ".join(shlex.quote(str(a)) for a in args)
    e = {"IDF_COMPONENT_MANAGER": "0", "IDF_CCACHE_ENABLE": "0"}
    e.update(env or {})
    return run(["bash", "-c", cmd], cwd=proj, timeout=timeout, env=e)


class ESP32Build(Result):
    def __init__(self, code: int, out: str, build_dir: Path, target: str):
        super().__init__(code, out)
        self.build_dir, self.target = build_dir, target

    @property
    def errors(self) -> list[str]:
        return CBuild(self.returncode, self.out, self.build_dir).errors if not self.ok else []

    @property
    def app_bin(self) -> Path | None:
        desc = self.build_dir / "project_description.json"
        if desc.exists():
            name = json.loads(desc.read_text()).get("app_bin")
            if name:
                return self.build_dir / name
        bins = [p for p in self.build_dir.glob("*.bin")]
        return bins[0] if bins else None

    def assert_ok(self) -> "ESP32Build":
        assert self.ok, f"idf.py build ({self.target}) failed:\n" + "\n".join(self.errors[:20])
        return self


def esp32_build(target: str = "esp32", path: str = ".", timeout: float = 1800) -> ESP32Build:
    """set-target + build an ESP-IDF project (CMakeLists.txt with
    include($ENV{IDF_PATH}/tools/cmake/project.cmake), main/). .ok, .errors,
    .build_dir, .app_bin."""
    r = idf("set-target", target, path=path, timeout=timeout)
    if r.ok:
        r2 = idf("build", path=path, timeout=timeout)
        r = Result(r2.returncode, r.out + r2.out)
    return ESP32Build(r.returncode, r.out, TMP / f"idf-build-{re.sub(r'[^\w]+', '_', path)}", target)


def esp32_size(build: ESP32Build) -> dict:
    """idf.py size as JSON (flash, DRAM/IRAM use)."""
    path = os.path.relpath(build.build_dir, TMP).removeprefix("idf-build-")
    r = idf("size", "--format", "json2", path="." if path == "_" else path)
    m = re.search(r"^\{.*\}\s*$", r.out, re.S | re.M)
    return json.loads(m.group(0)) if m else {}


def esp32_run(build: ESP32Build, until: str | None = None, timeout: float = 60) -> Result:
    """Run the firmware in Espressif's QEMU (esp32, esp32c3), UART0 as the
    output, until a line matches `until` (a regex) or the timeout passes
    (returncode 124 then; 0 when `until` matched)."""
    import subprocess as sp
    qemu, machine = ESP_QEMU[build.target]
    flash = build.build_dir / "forge-flash.bin"
    m = idf("merge-bin", "--output", str(flash), "--fill-flash-size", "4MB",
            path="." if build.build_dir.name == "idf-build-_" else build.build_dir.name[len("idf-build-"):])
    if not m.ok or not flash.exists():
        r = run(["bash", "-c", f". {IDF_EXPORT} >/dev/null 2>&1 && cd {build.build_dir} && "
                 f"esptool.py --chip {build.target} merge_bin --fill-flash-size 4MB -o {flash} @flash_args"],
                timeout=300)
        assert flash.exists(), m.out[-1500:] + r.out[-1500:]
    cmd = (f". {IDF_EXPORT} >/dev/null 2>&1 && exec {qemu} -nographic -machine {machine} "
           f"-drive file={flash},if=mtd,format=raw -serial mon:stdio")
    p = sp.Popen(["bash", "-c", cmd], stdout=sp.PIPE, stderr=sp.STDOUT, text=True, errors="replace")
    out, t0, code = [], time.monotonic(), 124
    pat = re.compile(until) if until else None
    import select
    while time.monotonic() - t0 < timeout:
        ready, _, _ = select.select([p.stdout], [], [], 0.5)
        if not ready:
            if p.poll() is not None:
                code = p.returncode
                break
            continue
        ln = p.stdout.readline()
        if not ln:
            break
        out.append(ln)
        if pat and pat.search(ln):
            code = 0
            break
    p.kill()
    p.wait(10)
    text = "".join(out) + ("" if code != 124 else f"\nTIMEOUT after {timeout}s")
    return Result(code, text)


_PREVIEW_N = [0]


def build_preview(toolchain: str) -> Path:
    """What forge will put in outputs/build, made now into a scratch folder
    (outputs/build itself is only made after every chunk test has passed,
    so a test that looks there finds nothing). Returns the folder:
    build_preview('fo4') / 'Scripts/PDX/Factions.pex'."""
    _PREVIEW_N[0] += 1
    out = TMP / f"build-preview-{_PREVIEW_N[0]}"
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    package(toolchain, out)
    return out


def package(toolchain: str, out) -> list[str]:
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    made: list[str] = []
    if toolchain in ("python-app","wasm","kotlin-jvm","android","ros2","desktop","hdl","cad","infra"):
        return _extra_helpers["extra_package"](toolchain,out)
    proj = project_dir()
    if toolchain == "fo4":
        if list(proj.rglob("*.psc")):
            r = papyrus(release=True, optimize=True)
            assert r.ok, f"Papyrus did not compile:\n{r.out[-3000:]}"
            for p in r.pex:
                t = out / "Scripts" / p.relative_to(proj / "Scripts")
                t.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy(p, t)
                made.append(str(t.relative_to(out)))
        for folder in ("Plugin",):
            if (proj / folder).is_dir() and any((proj / folder).glob("*.cs")):
                esp = esp_build(folder)
                shutil.copy(esp, out / esp.name)
                made.append(esp.name)
        for dname in FO4_DATA_DIRS:
            if (proj / dname).is_dir():
                shutil.copytree(proj / dname, out / dname, dirs_exist_ok=True)
                made.append(dname + "/")
    elif toolchain == "blender":
        if (proj / "build.py").exists():
            blender_build("build.py", out=out)
            made += [str(p.relative_to(out)) for p in sorted(out.rglob("*")) if p.is_file()]
    elif toolchain == "minecraft":
        if (proj / "src" / "main" / "java").is_dir():
            jar = mc_build()
            shutil.copy(jar, out / jar.name)
            made.append(jar.name)
        packs = proj / "datapacks"
        if packs.is_dir():
            for pack in sorted(packs.iterdir()):
                if (pack / "pack.mcmeta").is_file():
                    shutil.copytree(pack, out / "datapacks" / pack.name, dirs_exist_ok=True)
                    made.append(f"datapacks/{pack.name}/")
    elif toolchain == "go":
        mains = sorted({p.parent for p in proj.rglob("*.go")
                        if not p.name.endswith("_test.go")
                        and re.search(r"^package main\b", p.read_text(errors="replace"), re.M)})
        for d in mains:
            name = proj.name if d == proj else d.name
            name = "app" if name.startswith("project") else name
            rel = "./" + str(d.relative_to(proj)) if d != proj else "."
            r = go("build", "-trimpath", "-ldflags=-s -w", "-o", str(out / name), rel,
                   env={"CGO_ENABLED": "0", "GOOS": "linux", "GOARCH": "amd64"})
            assert r.ok, f"go build {rel} failed:\n{r.out[-3000:]}"
            made.append(name)
    return made


# --------------------------------------------------------- image build CLI

def _cli(argv: list[str]) -> int:
    """`python3 forge_game.py sbox-template DIR` / `fo4-template DIR`: the
    projects install-forge-toolchains.sh restores (with network) to fill an
    image's NuGet cache with exactly what forge's projects will ask for."""
    if len(argv) != 2 or argv[0] not in ("sbox-template", "fo4-template"):
        print("usage: forge_game.py sbox-template|fo4-template DIR", file=sys.stderr)
        return 2
    d = Path(argv[1])
    d.mkdir(parents=True, exist_ok=True)
    if argv[0] == "sbox-template":
        (d / "ForgeTests.csproj").write_text(sbox_csproj("ForgeTests", [], None))
        (d / "Smoke.cs").write_text(
            "using Microsoft.VisualStudio.TestTools.UnitTesting;\n[TestClass]\npublic class Smoke\n"
            "{\n\t[TestMethod]\n\tpublic void Works() => Assert.AreEqual( 2, 1 + 1 );\n}\n")
    else:
        (d / "Plugin.csproj").write_text(mutagen_csproj("Plugin"))
        (d / "Program.cs").write_text(
            "using Mutagen.Bethesda.Fallout4;\nusing Mutagen.Bethesda.Plugins;\n"
            "static class P\n{\n\tstatic int Main()\n\t{\n"
            "\t\tvar mod = new Fallout4Mod( ModKey.FromFileName( \"Smoke.esp\" ), Fallout4Release.Fallout4 );\n"
            "\t\treturn mod.MiscItems.Count;\n\t}\n}\n")
    return 0


# -------------------------------------------------------------------- PNG

class Image:
    """A decoded PNG: .w, .h, .px(x, y) -> (r, g, b, a)."""

    def __init__(self, w: int, h: int, rgba: bytearray):
        self.w, self.h, self.rgba = w, h, rgba

    def px(self, x: int, y: int) -> tuple:
        i = 4 * (y * self.w + x)
        return tuple(self.rgba[i:i + 4])


def load_png(path) -> Image:
    raw = Path(path).read_bytes()
    assert raw[:8] == b"\x89PNG\r\n\x1a\n", f"{path} is not a PNG"
    pos, chunks, plte, trns = 8, [], None, None
    w = h = depth = ctype = 0
    while pos < len(raw):
        n, = struct.unpack(">I", raw[pos:pos + 4])
        kind, data = raw[pos + 4:pos + 8], raw[pos + 8:pos + 8 + n]
        pos += 12 + n
        if kind == b"IHDR":
            w, h, depth, ctype = struct.unpack(">IIBB", data[:10])
        elif kind == b"PLTE":
            plte = data
        elif kind == b"tRNS":
            trns = data
        elif kind == b"IDAT":
            chunks.append(data)
    assert depth in (1, 2, 4, 8, 16), f"{path}: bit depth {depth} is not a PNG depth"
    ch = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[ctype]
    data = zlib.decompress(b"".join(chunks))
    bits = depth * ch
    stride = (w * bits + 7) // 8
    unit = max(1, bits // 8)            # the filters' "bytes per pixel"
    out = bytearray(w * h * 4)
    prev = bytearray(stride)
    i = 0
    scale = 255 // ((1 << depth) - 1) if depth < 8 else 1
    for y in range(h):
        f = data[i]
        line = bytearray(data[i + 1:i + 1 + stride])
        i += 1 + stride
        for x in range(stride):
            a = line[x - unit] if x >= unit else 0
            b = prev[x]
            c = prev[x - unit] if x >= unit else 0
            if f == 1:
                line[x] = (line[x] + a) & 255
            elif f == 2:
                line[x] = (line[x] + b) & 255
            elif f == 3:
                line[x] = (line[x] + (a + b) // 2) & 255
            elif f == 4:
                pa, pb, pc = abs(b - c), abs(a - c), abs(a + b - 2 * c)
                pr = a if pa <= pb and pa <= pc else b if pb <= pc else c
                line[x] = (line[x] + pr) & 255
        if depth == 16:
            samples = line[0::2]        # the high byte of each sample
        elif depth == 8:
            samples = line
        else:
            per = 8 // depth
            mask = (1 << depth) - 1
            samples = bytearray((line[k // per] >> (8 - depth * (k % per + 1))) & mask
                                for k in range(w))
        for x in range(w):
            o = 4 * (y * w + x)
            s = samples[x * ch:(x + 1) * ch]
            if ctype == 0:
                g = s[0] * scale if depth < 8 else s[0]
                out[o:o + 4] = bytes((g, g, g, 255))
            elif ctype == 2:
                out[o:o + 4] = bytes((s[0], s[1], s[2], 255))
            elif ctype == 3:
                k = s[0]
                alpha = trns[k] if trns and k < len(trns) else 255
                out[o:o + 4] = bytes((plte[3 * k], plte[3 * k + 1], plte[3 * k + 2], alpha))
            elif ctype == 4:
                out[o:o + 4] = bytes((s[0], s[0], s[0], s[1]))
            else:
                out[o:o + 4] = s
        prev = line
    return Image(w, h, out)


def _img(x):
    """An Image from an Image or a PNG path (godot_frames returns paths, and
    tests kept passing them straight in)."""
    return x if hasattr(x, "px") else load_png(x)


def region_mean(img: Image, x0: int, y0: int, x1: int, y1: int) -> tuple:
    """Average (r, g, b) over a rectangle, end-exclusive."""
    img = _img(img)
    n, r, g, b = 0, 0, 0, 0
    for y in range(max(0, y0), min(img.h, y1)):
        for x in range(max(0, x0), min(img.w, x1)):
            p = img.px(x, y)
            r, g, b, n = r + p[0], g + p[1], b + p[2], n + 1
    assert n, "empty region"
    return (r / n, g / n, b / n)


def close(c1, c2, tol: float = 24) -> bool:
    return all(abs(a - b) <= tol for a, b in zip(c1[:3], c2[:3]))


def count_color(img: Image, rgb, tol: float = 24) -> int:
    img = _img(img)
    return sum(1 for y in range(img.h) for x in range(img.w) if close(img.px(x, y), rgb, tol))


def diff(a: Image, b: Image, tol: float = 24) -> float:
    """Fraction of pixels that differ by more than tol (images the same size)."""
    a, b = _img(a), _img(b)
    assert (a.w, a.h) == (b.w, b.h), f"sizes differ: {a.w}x{a.h} vs {b.w}x{b.h}"
    bad = sum(1 for y in range(a.h) for x in range(a.w) if not close(a.px(x, y), b.px(x, y), tol))
    return bad / (a.w * a.h)


def is_blank(img: Image, tol: float = 8) -> bool:
    """True if every pixel is (nearly) the same colour - a black or empty frame."""
    img = _img(img)
    first = img.px(0, 0)
    step = max(1, (img.w * img.h) // 4000)
    return all(close(first, img.px(i % img.w, i // img.w), tol) for i in range(0, img.w * img.h, step))


def keep(path, name: str) -> Path | None:
    """Save a frame for the review (only when forge is collecting captures)."""
    if not OUT.exists():
        return None
    d = OUT / "captures"
    d.mkdir(parents=True, exist_ok=True)
    dst = d / (name if name.endswith(".png") else name + ".png")
    shutil.copy(path, dst)
    return dst


if __name__ == "__main__":
    sys.exit(_cli(sys.argv[1:]))

from forge_extra_helpers import install as _install_extra_helpers
_extra_helpers = _install_extra_helpers(globals())
globals().update(_extra_helpers)
