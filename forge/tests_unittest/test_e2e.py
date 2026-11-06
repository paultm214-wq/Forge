"""End to end: `forge run` against a fake planner/coder and a fake
sd-server, with the sandbox running locally (unsafe_local_exec - test use
only). A three-chunk project plus two image assets goes from spec to commit:

  sequential   the first coder answer is wrong, so a diagnosis and a retry
               follow; the chunk that names badge.png waits for its render;
               the weak test of c2 is strengthened; the sprite sheet's frame 1
               gets frame 0's raw render as reference; ladder stats are kept.
  parallel     two helper coders and loop.parallel_chunks = 2: c1 and c2
               build side by side, c3 after both.
"""

import base64
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

from _load import FORGE_PY, forge
from fakes import FakeLLMServer, FakeSD

SPEC = """# mathkit

Small math helpers.

## Acceptance

1. add(2, 3) == 5
2. scale(2, 3) == 6
3. combo(1, 2, 3) == 9

## Assets

```json
[{"id": "a1", "filename": "badge.png", "prompt": "a round badge", "width": 16, "height": 16, "seed": 1},
 {"id": "a2", "filename": "hero.png", "prompt": "a hero", "width": 32, "height": 32, "seed": 2,
  "chroma_key": "#ff00ff", "pixelate": {"width": 8, "height": 8}, "frames": 2, "sheet_columns": 2}]
```
"""

PLAN = {"project": "mathkit", "summary": "Small math helpers.", "acceptance_ok": True,
        "missing": [], "chunks": [
            {"id": "c1", "kind": "code", "title": "add", "spec": "add(a, b) returns a + b.",
             "depends_on": [], "thinking": False,
             "verify": "assert add(2, 3) == 5, 'add adds'\nprint('PASS')"},
            {"id": "c2", "kind": "code", "title": "scale",
             "spec": "scale(x, k) returns x * k. The title screen shows badge.png.",
             "depends_on": [], "thinking": False,
             "verify": "assert scale(2, 3) is not None\nprint('PASS')"},
            {"id": "c3", "kind": "code", "title": "combo",
             "spec": "combo(a, b, k) returns scale(add(a, b), k).", "depends_on": ["c1", "c2"],
             "thinking": False, "verify": "assert combo(1, 2, 3) == 9\nprint('PASS')"}]}

CONTRACT = {"names": [{"name": "add", "owner": "c1", "signature": "(a, b)"},
                      {"name": "scale", "owner": "c2", "signature": "(x, k)"},
                      {"name": "combo", "owner": "c3", "signature": "(a, b, k)"}],
            "schema_sql": "", "rules": [], "routes": [], "dom_hooks": [], "payloads": []}

CODE = {"c1": "def add(a, b):\n    return a + b\n",
        "c2": "def scale(x, k):\n    return x * k\n",
        "c3": "def combo(a, b, k):\n    return scale(add(a, b), k)\n"}

CONFIG = """
[planner]
url = "{llm}"
model = "planner"
kind = "qwen38"
context = 100000
max_tokens = 8000
thinking_max_tokens = 8000
review_max_tokens = 8000
retry_minutes = 0.2

[coder]
url = "{llm}"
model = "tiel-coder"
kind = "tiel"
context = 60000
max_tokens = 8000
thinking_max_tokens = 8000
retry_minutes = 0.2
{coders}
[images]
enabled = true
timeout = 60

[[images.servers]]
name = "sd"
url = "{sd}"

[sandbox]
unsafe_local_exec = true
timeout = 60

[loop]
max_attempts = 4
parallel_chunks = {width}
diagnosis_wait = 5
test_strength = true
min_kill = 0.75
learn_ladder = true

[claude]
enabled = false

[git]
repo = "{tmp}/projects"

[cache]
enabled = false

[models]
enabled = false

[prior_art]
enabled = false

[paths]
runs = "{tmp}/runs"
inbox = "{tmp}/inbox"
"""

HELPERS = """
[[coders]]
name = "h1"
url = "{llm}"
image_server = ""

[[coders]]
name = "h2"
url = "{llm}"
image_server = ""
"""


class Script:
    """The fake models' answers, by system prompt."""

    def __init__(self, first_c1_wrong: bool, coder_delay: float = 0.0, plan: dict = PLAN):
        self.first_c1_wrong, self.coder_delay, self.plan = first_c1_wrong, coder_delay, plan
        self.coder_calls: dict[str, int] = {}
        self.lock = threading.Lock()

    def __call__(self, system: str, user: str, body: dict) -> str:
        if system.startswith("You are the planner") and "into a plan" in system:
            return json.dumps(self.plan)
        if system.startswith("You write the INTERFACE CONTRACT"):
            return json.dumps(CONTRACT)
        if system.startswith("You are a precise Python engineer"):
            cid = next(c for c in CODE if f"## YOUR TASK: {c} " in user)
            with self.lock:
                n = self.coder_calls[cid] = self.coder_calls.get(cid, 0) + 1
            time.sleep(self.coder_delay)
            if cid == "c1" and n == 1 and self.first_c1_wrong:
                return "```python\ndef add(a, b):\n    return a - b\n```"
            return f"```python\n{CODE[cid]}```"
        if system.startswith("You diagnose"):
            return "add subtracts; it must return a + b."
        if system.startswith("A coding model keeps failing"):
            return json.dumps({"verdict": "code", "reason": "the code is wrong"})
        if system.startswith("A chunk's verification test still PASSES"):
            return "```python\nassert scale(2, 3) == 6, 'scale multiplies'\n```"
        if system.startswith("You review generated code"):
            return json.dumps({"approved": True, "issues": []})
        return "ok"


def forge_run(tmp: Path, llm: str, sd: str, width: int, helpers: bool):
    cfg = tmp / "config.toml"
    cfg.write_text(CONFIG.format(llm=llm, sd=sd, tmp=tmp, width=width,
                                 coders=HELPERS.format(llm=llm) if helpers else ""))
    spec = tmp / "mathkit.md"
    spec.write_text(SPEC)
    home = tmp / "home"
    home.mkdir()
    p = subprocess.run([sys.executable, str(FORGE_PY), "--config", str(cfg), "run", str(spec)],
                       capture_output=True, text=True, timeout=300, cwd=tmp,
                       env={**os.environ, "HOME": str(home)})
    runs = [d for d in (tmp / "runs").iterdir() if d.is_dir()]
    return p, runs[0] if runs else None, home


class EndToEnd(unittest.TestCase):
    def test_sequential(self):
        tmp = Path(tempfile.mkdtemp(prefix="forge-e2e-"))
        script = Script(first_c1_wrong=True)
        llm, sd = FakeLLMServer(script), FakeSD(delay=1.5)
        try:
            p, run, home = forge_run(tmp, llm.url, sd.url, width=1, helpers=False)
        finally:
            llm.close()
            sd.close()
        self.assertEqual(p.returncode, 0, p.stdout[-3000:] + p.stderr[-3000:])
        state = json.loads((run / "state.json").read_text())
        self.assertEqual(state["phase"], "committed")
        self.assertEqual(state["chunks"]["c1"]["attempts"], 2)       # wrong, then right
        log = (run / "forge.log").read_text()
        self.assertLess(log.index("[a1] saved assets/badge.png"), log.index("[c2] attempt 1"))
        plan = json.loads((run / "plan.json").read_text())
        c2 = next(c for c in plan["chunks"] if c["id"] == "c2")
        self.assertIn("added by the test-strength check", c2["verify"])
        self.assertIn("## Test strength", (run / "REPORT.md").read_text())
        self.assertTrue((home / "forge" / "ladder-stats.json").exists())
        hero = [r for r in sd.requests if "hero" in r.get("prompt", "")]
        self.assertEqual(forge.png_size(base64.b64decode(hero[1]["extra_images"][-1])), (32, 32))
        branches = subprocess.run(["git", "-C", str(tmp / "projects"), "branch", "--list", "forge/*"],
                                  capture_output=True, text=True).stdout
        self.assertIn(state["branch"], branches)

    def test_parallel(self):
        tmp = Path(tempfile.mkdtemp(prefix="forge-e2e-"))
        # c2 uses no image here, so nothing holds it back from building beside c1.
        plan = json.loads(json.dumps(PLAN))
        plan["chunks"][1]["spec"] = "scale(x, k) returns x * k."
        llm = FakeLLMServer(Script(first_c1_wrong=False, coder_delay=1.0, plan=plan))
        sd = FakeSD(delay=0.2)
        try:
            p, run, _ = forge_run(tmp, llm.url, sd.url, width=2, helpers=True)
        finally:
            llm.close()
            sd.close()
        self.assertEqual(p.returncode, 0, p.stdout[-3000:] + p.stderr[-3000:])
        state = json.loads((run / "state.json").read_text())
        self.assertEqual(state["phase"], "committed")
        log = (run / "forge.log").read_text()
        self.assertRegex(log, r"\[c[12]\] building alongside c[12]")
        # Each got a share of the three writers: c2's first attempt did not
        # wait for c1 to be done.
        self.assertLess(log.index("[c2] attempt 1/"), log.index("[c1] VERIFIED"))
        self.assertLess(max(log.index("[c1] VERIFIED"), log.index("[c2] VERIFIED")),
                        log.index("[c3] attempt 1"))


if __name__ == "__main__":
    unittest.main()
