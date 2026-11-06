"""Loads forge and coder-models.py for the tests, in a throwaway HOME.

forge reads ~/forge (FORGE_HOME) when it is imported, so HOME is pointed at a
temporary folder before that: nothing a test does touches your real ~/forge,
~/llm-stack or ~/.ssh. Point FORGE_PY / CODER_MODELS_PY at other copies to
test those; by default the files next to tests_unittest/ are used (forge.py, else
forge6.py; coder-models.py there or in ../coder-models/).
"""

from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
HOME = Path(tempfile.mkdtemp(prefix="forge-test-home-"))
os.environ["HOME"] = str(HOME)
os.environ.pop("GITHUB_TOKEN", None)


def _find(env: str, *names: str) -> Path:
    if os.environ.get(env):
        return Path(os.environ[env]).resolve()
    for n in names:
        if (ROOT / n).exists():
            return ROOT / n
    raise FileNotFoundError(f"none of {names} next to tests/ (or set {env})")


FORGE_PY = _find("FORGE_PY", "forge.py", "forge6.py")
# coder-models.py is beside forge.py on Gemini's copy, in ../coder-models/ in
# the bundle.
CODER_MODELS_PY = _find("CODER_MODELS_PY", "coder-models.py", "../coder-models/coder-models.py")
FORGE_GAME_PY = FORGE_PY.with_name("forge_game.py")


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


class QuietLog:
    """Stands in for forge's log: keeps the lines, prints nothing."""

    def __init__(self):
        self.lines: list[str] = []

    def attach(self, path):
        pass

    def __call__(self, msg: str, level: str = "INFO"):
        self.lines.append(f"{level} {msg}")


forge = load("forge", FORGE_PY)
forge.log = QuietLog()


def coder_models():
    """coder-models.py with its state in this test's HOME."""
    os.environ["STACK_DIR"] = str(HOME / "llm-stack")
    os.environ["CODER_MODELS_DIR"] = str(HOME / "models" / "coders")
    return load("coder_models", CODER_MODELS_PY)


def unsafe_sandbox(**kw):
    """The sandbox without Docker - generated code runs as you. Tests only."""
    return forge.Sandbox({"unsafe_local_exec": True, "timeout": 60, **kw})


def make_run(root: Path, plan: dict, verified: dict[str, str] | None = None,
             spec: str = "# Demo\n\n## Acceptance\n\n1. it works\n",
             state: dict | None = None):
    """A run directory as forge leaves it after planning."""
    import json
    root.mkdir(parents=True, exist_ok=True)
    for d in ("chunks", "verified", "assets"):
        (root / d).mkdir(exist_ok=True)
    (root / "SPEC.md").write_text(spec)
    (root / "plan.json").write_text(json.dumps(plan, indent=2))
    st = state or {"phase": "build", "reviews": [], "claude_cost_usd": 0.0,
                   "chunks": {cid: {"status": "verified"} for cid in (verified or {})}}
    (root / "state.json").write_text(json.dumps(st, indent=2))
    for cid, code in (verified or {}).items():
        (root / "verified" / f"{cid}.py").write_text(code)
    return forge.Run(root)


class FakeLLM:
    """Enough of forge's LLM for prompt building and one-shot calls."""

    def __init__(self, replies=None, name="fake", model="fake-model"):
        self.replies = replies or []
        self.calls: list[tuple[str, str]] = []
        self.name, self.model = name, model
        self.context, self.prompt_budget = 200000, 150000
        self.max_tokens = self.thinking_max_tokens = 4000
        self.temperature, self.kind = 0.2, "plain"
        self.last = {}

    def tokens(self, text: str) -> int:
        return len(text) // 4 + 1

    def chat(self, system, user, thinking=False, **kw):
        self.calls.append((system, user))
        r = self.replies.pop(0) if self.replies else ""
        return r(system, user) if callable(r) else r
