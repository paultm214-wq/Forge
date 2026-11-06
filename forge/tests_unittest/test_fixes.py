"""forge: fixes 4-12 of the review (1-3 are in test_coder_models.py)."""

import base64
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from _load import HERE, FakeLLM, forge, make_run, unsafe_sandbox
from fakes import FakeSD

f = forge


def tmpdir() -> Path:
    return Path(tempfile.mkdtemp(prefix="forge-test-"))


class FailureReport(unittest.TestCase):
    """Fix 4: the all-failures report keeps its FIRST entries."""

    def test_first_failure_survives(self):
        module = "def f(x):\n    return x\n"
        test = "\n".join(f"assert f({i}) == -1, 'value {i} ' + 'x' * 250" for i in range(15))
        every = f.all_failures(unsafe_sandbox(), module, test)
        self.assertEqual(f._EVERY_ENTRY.search(every).group(1), "1")
        self.assertIn("15 failing statement", every)
        hints, _ = f.mechanical_hints({"chunks": []}, {"id": "c1"}, module, "", every)
        self.assertIsInstance(hints, list)

    def test_ordinary_output_is_still_trimmed(self):
        _, out = unsafe_sandbox().run({"t.py": "print('x' * 10000)"}, "t.py")
        self.assertLess(len(out), 4100)


class ImagesWait(unittest.TestCase):
    """Fix 5: a chunk that uses an image starts only once it has rendered;
    chunks that do not wait build meanwhile."""

    def setUp(self):
        self.run = make_run(tmpdir() / "r", {"project": "demo", "chunks": [
            {"id": "c1", "kind": "code", "spec": "Draw badge.png on the title.",
             "verify": "print('PASS')", "depends_on": []},
            {"id": "c2", "kind": "code", "spec": "Sum.", "verify": "print('PASS')",
             "depends_on": []},
            {"id": "c3", "kind": "code", "spec": "Both.", "verify": "print('PASS')",
             "depends_on": ["c1", "c2"]},
            {"id": "a1", "kind": "image", "filename": "badge.png", "prompt": "a badge"}]},
            state={"phase": "build", "chunks": {}, "reviews": []})
        self.events = []

    def fake_build(self, sleep=0.05, fail=None):
        def build(run, cfg, planner, coder, sandbox, chunk):
            t0 = time.monotonic()
            time.sleep(sleep)
            if fail and chunk["id"] in fail:
                self.events.append((chunk["id"], t0, time.monotonic()))
                raise fail[chunk["id"]]
            with f.Run.lock:
                run.state["chunks"][chunk["id"]] = {"status": "verified"}
                run.save()
            self.events.append((chunk["id"], t0, time.monotonic()))
        return build

    def test_chunk_waits_and_others_go_first(self):
        pool = FakePool(self.run, ["a1"], after=0.3)
        with mock.patch.object(f, "run_code_chunk", self.fake_build()):
            f.build_code_chunks(self.run, {}, None, None, None, pool)
        order = [e[0] for e in self.events]
        self.assertEqual(order, ["c2", "c1", "c3"])
        self.assertGreaterEqual(self.events[1][1], pool.done_at)
        self.assertEqual(f.chunk_images(self.run.plan, self.run.plan["chunks"][0]), ["a1"])

    def test_filename_match_needs_a_boundary(self):
        plan = {"chunks": [{"id": "a1", "kind": "image", "filename": "badge.png"}]}
        self.assertEqual(f.chunk_images(plan, {"spec": "uses bigbadge.png"}), [])
        self.assertEqual(f.chunk_images(plan, {"spec": "res://assets/badge.png"}), ["a1"])
        self.assertEqual(f.chunk_images(plan, {"spec": "The title shows badge.png."}), ["a1"])
        self.assertEqual(f.chunk_images(plan, {"spec": "not badge.png.bak"}), [])


class FakePool:
    """ImagePool's waiting interface: the renders finish `after` seconds in."""

    def __init__(self, run, pending, after):
        self.run, self.pending, self.after = run, set(pending), after
        self.t0, self.done_at = time.monotonic(), None

    def unfinished(self, ids):
        if self.pending and time.monotonic() - self.t0 >= self.after:
            with f.Run.lock:
                for i in self.pending:
                    self.run.state["chunks"][i] = {"status": "verified"}
                self.run.save()
            self.pending.clear()
            self.done_at = time.monotonic()
        return [i for i in ids if i in self.pending]

    def wait_change(self, timeout=30):
        time.sleep(0.02)


class SheetReference(unittest.TestCase):
    """Fix 6: later frames get frame 0's unprocessed render as reference."""

    def test_reference_is_the_raw_render(self):
        sd = FakeSD()
        try:
            run = make_run(tmpdir() / "r", {"project": "demo", "chunks": []},
                           state={"phase": "build", "chunks": {}})
            cfg = {"images": {"enabled": True, "timeout": 30, "servers": [{"name": "sd", "url": sd.url}]}}
            chunk = {"id": "a2", "kind": "image", "filename": "hero.png", "prompt": "a hero",
                     "width": 32, "height": 32, "seed": 3, "chroma_key": "#ff00ff",
                     "pixelate": {"width": 8, "height": 8}, "frames": 2, "sheet_columns": 2}
            f.render_sheet(run, cfg, chunk, cfg["images"]["servers"][0], "sd", 0)
            ref = base64.b64decode(sd.requests[1]["extra_images"][-1])
            self.assertEqual(f.png_size(ref), (32, 32))           # not the 8x8 pixel art
            self.assertEqual(f.png_size((run.root / "assets" / "hero.png").read_bytes()), (16, 8))
        finally:
            sd.close()


class ImageCost(unittest.TestCase):
    """Fix 7: a sprite sheet costs every frame; tiling costs its pass."""

    def test_frames_and_tiling(self):
        one = f.image_cost({"width": 512, "height": 512, "steps": 20}, {})
        self.assertAlmostEqual(f.image_cost({"width": 512, "height": 512, "steps": 20,
                                             "frames": ["a", "b", "c", "d"]}, {}), 4 * one)
        self.assertAlmostEqual(f.image_cost({"width": 512, "height": 512, "steps": 20,
                                             "tileable": True}, {}), 1.6 * one)


class DownstreamNoAudit(unittest.TestCase):
    """Fix 8: attempts that pass their own test but break a verified
    dependent never send that (passing) test to the audit."""

    def test_no_audit_on_downstream_breaks(self):
        c1_bad = "def f(x):\n    return x\n\ndef g():\n    return 0\n"
        c1_good = "def f(x):\n    return x\n\ndef g():\n    return 1\n"
        run = make_run(tmpdir() / "r", {"project": "demo", "summary": "s", "chunks": [
            {"id": "c1", "kind": "code", "spec": "f and g", "depends_on": [],
             "verify": "assert f(1) == 1\nprint('PASS')"},
            {"id": "c2", "kind": "code", "spec": "h", "depends_on": ["c1"],
             "verify": "assert h() == 2, 'h is g() + 1'\nprint('PASS')"}]},
            verified={"c2": "def h():\n    return g() + 1\n"},
            state={"phase": "build", "reviews": [],
                   "chunks": {"c1": {"status": "pending"}, "c2": {"status": "verified"}}})
        planner = FakeLLM(replies=[lambda s, u: (
            '{"verdict": "test", "reason": "x", "test": "print(1)"}' if s.startswith("A coding model")
            else "Diagnosis: g must return 1.")] * 20, name="planner")
        coder = FakeLLM(replies=[f"```python\n{c}```" for c in (c1_bad, c1_bad, c1_good)], name="coder")
        cfg = {"loop": {"max_attempts": 3, "diagnosis_wait": 5}}
        f.HELPERS[:] = []
        code = f.run_code_chunk(run, cfg, planner, coder, unsafe_sandbox(),
                                run.plan["chunks"][0])
        self.assertEqual(code.strip(), c1_good.strip())
        self.assertFalse([c for c in planner.calls if c[0].startswith("A coding model")])
        self.assertEqual(run.state["chunks"]["c2"]["status"], "verified")


class UpstreamOutsideBuild(unittest.TestCase):
    """Fix 9: an UpstreamFault during fixes rebuilds the culprit instead of
    ending the run."""

    def test_fault_goes_to_fix_upstream(self):
        fault = f.UpstreamFault("c3", "c1", "route /x")
        seen = []
        with mock.patch.object(f, "run_code_chunk", side_effect=fault), \
                mock.patch.object(f, "fix_upstream", lambda *a: seen.append(a[-1])):
            f.build_or_fix_upstream(None, {}, None, None, None, {"id": "c3"})
        self.assertEqual(seen, [fault])


class RunLock(unittest.TestCase):
    """Fix 10: one forge process per run."""

    def test_second_process_is_refused(self):
        run = make_run(tmpdir() / "r", {"project": "demo", "chunks": []})
        holder = subprocess.Popen(
            [sys.executable, "-c",
             f"import sys, time; sys.path.insert(0, {str(HERE)!r})\n"
             f"from _load import forge\n"
             f"with forge.run_lock(forge.Run({str(run.root)!r})):\n"
             f"    print('held', flush=True); time.sleep(60)\n"],
            stdout=subprocess.PIPE, text=True)
        try:
            self.assertEqual(holder.stdout.readline().strip(), "held")
            with self.assertRaises(f.ForgeError) as cm:
                with f.run_lock(run):
                    pass
            self.assertIn("in use by pid", str(cm.exception))
        finally:
            holder.kill()
            holder.wait()
            holder.stdout.close()
        with f.run_lock(run), f.run_lock(run):        # free again, and re-entrant
            pass


class CommitClean(unittest.TestCase):
    """Fix 11: a change's commit holds only what the new version produces;
    the history of change requests stays."""

    def make(self, root, base=None, outputs=("old.txt",)):
        run = make_run(root, {"project": "demo", "chunks": [
            {"id": "c1", "kind": "code", "verify": "print('PASS')"}]},
            state={"phase": "build", "chunks": {}, "reviews": [],
                   **({"base_branch": base, "change": {"title": "t"}} if base else {})})
        (root / "module.py").write_text("X = 1\n")
        (root / "CHANGE.md").write_text("# change\n")
        for o in outputs:
            (root / "outputs").mkdir(exist_ok=True)
            (root / "outputs" / o).write_text(o)
        return run

    def test_stale_outputs_go(self):
        tmp = tmpdir()
        cfg = {"git": {"repo": str(tmp / "repo")}}
        b1 = f.commit(self.make(tmp / "r1"), cfg, "report 1")
        b2 = f.commit(self.make(tmp / "r2", base=b1, outputs=("new.txt",)), cfg, "report 2")
        names = subprocess.run(["git", "-C", str(tmp / "repo"), "ls-tree", "-r", "--name-only", b2],
                               capture_output=True, text=True).stdout.split()
        self.assertIn("projects/demo/outputs/new.txt", names)
        self.assertNotIn("projects/demo/outputs/old.txt", names)
        self.assertIn("projects/demo/changes/r1.md", names)
        self.assertIn("projects/demo/changes/r2.md", names)


class SplitKeepsDecorators(unittest.TestCase):
    """Fix 12: a moved helper keeps its decorator."""

    def test_decorator_moves_with_its_function(self):
        src = ("import contextlib\n@contextlib.contextmanager\ndef ctx():\n"
               "    yield later_name()\nwith ctx() as v:\n    assert v\nx = 1; y = 2\n")
        keep, move = f.split_later_checks(src, {"later_name"})
        self.assertIn("@contextlib.contextmanager\ndef ctx():", move)
        self.assertIn("x = 1", keep)
        ns = {"later_name": lambda: True}
        exec(move, ns)                         # runs: the with-check works again


if __name__ == "__main__":
    unittest.main()
