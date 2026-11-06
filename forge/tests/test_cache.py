"""The SSD cache of GitHub repositories, and frequently used models."""
import json
import time

import pytest

import forge
from test_prior_art import FakeGH, FakePlanner, make_run, tarball


class CountingGH(FakeGH):
    def __init__(self):
        super().__init__()
        self.tarballs, self.heads, self.down = 0, "a" * 40, False

    def tarball(self, repo, sha):
        self.tarballs += 1
        return super().tarball(repo, sha)

    def head_sha(self, repo, branch):
        if self.down:
            raise forge.urllib.error.URLError("offline")
        return self.heads

    def json(self, path):
        return {"default_branch": "main"}


def picks():
    return FakePlanner([{"searches": [{"q": "chip-8", "language": None}]},
                        {"picks": [{"repo": "good/chip8", "use": "vendor", "why": "core"}]},
                        {"map": [], "python_path": ["src"], "notes": "n"}])


def test_second_run_downloads_nothing(tmp_path):
    gh = CountingGH()
    forge.prior_art(make_run(tmp_path / "a"), {}, picks(), gh)
    assert gh.tarballs == 1
    run2 = make_run(tmp_path / "b")
    forge.prior_art(run2, {}, picks(), gh)
    assert gh.tarballs == 1                                    # from the SSD
    assert (run2.root / "vendor" / "good__chip8" / "src" / "chip8.py").exists()
    rc = forge.RepoCache(gh, forge.cache_dir({}))
    assert len(rc.meta("good/chip8")["uses"]) == 2


def test_offline_uses_the_newest_cached_commit(tmp_path):
    gh = CountingGH()
    forge.prior_art(make_run(tmp_path / "a"), {}, picks(), gh)
    gh.down = True
    run2 = make_run(tmp_path / "b")
    forge.prior_art(run2, {}, picks(), gh)
    man = json.loads((run2.root / "vendor" / "manifest.json").read_text())
    assert man[0]["sha"] == "a" * 40 and gh.tarballs == 1


def test_refresh_precaches_frequent_and_listed_repos(tmp_path):
    gh = CountingGH()
    for i in range(2):                                        # used twice: frequent
        forge.prior_art(make_run(tmp_path / f"r{i}"), {}, picks(), gh)
    rc = forge.RepoCache(gh, forge.cache_dir({}))
    rc.use("once/used")
    cfg = {"cache": {"repos": ["pinned/lib"], "max_gb": 1}}
    for n, sha in enumerate(("b" * 40, "c" * 40)):            # two upstream commits later
        gh.heads = sha
        time.sleep(0.01)
        out = forge.cache_refresh(cfg, gh)
        assert f"good/chip8@{sha[:10]}" in out["fetched"]
        assert f"pinned/lib@{sha[:10]}" in out["fetched"]
    assert not any("once/used" in f for f in out["fetched"])
    tars = sorted(p.name for p in (rc.root / "good__chip8").glob("*.tar.gz"))
    assert tars == ["b" * 40 + ".tar.gz", "c" * 40 + ".tar.gz"]   # newest two kept
    # Over budget: the least used goes first, listed ones never.
    out = forge.cache_refresh({"cache": {"repos": ["pinned/lib"], "max_gb": 0}}, gh)
    assert "good/chip8" in out["dropped"] and "pinned/lib" in forge.RepoCache(gh, forge.cache_dir({})).repos()


def test_frequent_models():
    for a, n in (("jscoder", 5), ("rusty", 2), ("old", 1)):
        for _ in range(n):
            forge.record_use("apollo", a)
    u = json.loads((forge.models_dir() / "usage.json").read_text())
    u["apollo"]["old"] = [time.time() - 200 * 86400]
    (forge.models_dir() / "usage.json").write_text(json.dumps(u))
    assert forge.frequent_models("apollo", {}) == ["jscoder", "rusty"]
    assert forge.frequent_models("apollo", {"models": {"keep_frequent": 1}}) == ["jscoder"]
