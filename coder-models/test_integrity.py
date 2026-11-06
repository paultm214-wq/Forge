"""A downloaded model must never become ready without a published digest."""
import hashlib
import importlib.util
from pathlib import Path

import pytest


@pytest.mark.parametrize("digest", ["", "bad", "0" * 64, "correct"])
def test_worker_requires_matching_published_digest(tmp_path, monkeypatch, digest):
    spec = importlib.util.spec_from_file_location("cm_integrity", Path(__file__).with_name("coder-models.py"))
    cm = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cm)
    for attr, path in {"STACK": tmp_path, "MODELS": tmp_path / "models",
                       "STATE": tmp_path / "state.json", "PRESETS": tmp_path / "presets.ini",
                       "LOCK": tmp_path / ".lock"}.items():
        monkeypatch.setattr(cm, attr, path)
    cm.save_state({"models": {"demo": {"job": {
        "repo": "a/b", "revision": "a" * 40, "files": ["model.gguf"]}}}})
    target = cm.MODELS / "demo"
    target.mkdir(parents=True)
    (target / "model.gguf").write_bytes(b"weights")
    oid = hashlib.sha256(b"weights").hexdigest() if digest == "correct" else digest
    monkeypatch.setattr(cm, "published", lambda *args: {"model.gguf": (7, oid)})
    code = cm.download_worker("demo")
    if digest == "correct":
        assert code == 0
        assert cm.load_state()["models"]["demo"]["state"] == "ready"
        assert "[demo]" in cm.PRESETS.read_text()
    else:
        assert code == 1
        assert cm.load_state()["models"]["demo"]["state"] == "failed"
        assert "SHA-256" in cm.load_state()["models"]["demo"]["error"]
        assert not cm.PRESETS.exists()
