"""Change planning: string contract hooks, checks that need a later chunk,
and resuming after a stop part-way (glassinv-change-20261021-173457)."""
import pathlib, subprocess, sys


def test_change_planning_scenario():
    script = pathlib.Path(__file__).with_name("change_resume_scenario.py")
    out = subprocess.run([sys.executable, str(script)], capture_output=True, text=True, timeout=300)
    assert out.returncode == 0, out.stderr[-2000:]
    lines = [l for l in out.stdout.splitlines() if ":" in l]
    assert lines and all("False" not in l for l in lines), out.stdout
    assert "routes normalized: [{'method': 'DELETE'" in out.stdout
