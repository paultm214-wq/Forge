"""Identifiers from a job must be whole-string matches: `$` also matched
before a trailing newline, and an alias "x\\n" rewrote another model's preset."""
import importlib.util
from pathlib import Path

import pytest


@pytest.fixture
def cm(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("cm_ident", Path(__file__).with_name("coder-models.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "PRESETS", tmp_path / "presets.ini")
    return mod


GOOD = {"alias": "qwen-coder", "repo": "org/model-GGUF", "revision": "a" * 40,
        "files": ["q4/model-Q4_K_M.gguf"]}


def test_a_good_job_is_valid(cm):
    assert cm.valid_job(dict(GOOD)) is None


@pytest.mark.parametrize("key,value", [
    ("alias", "evil\n"), ("repo", "org/model\n"), ("revision", "a" * 40 + "\n"),
    ("files", ["model.gguf\n"]),
])
def test_trailing_newline_is_refused(cm, key, value):
    assert cm.valid_job({**GOOD, key: value}) is not None


def test_digest_with_trailing_newline_is_not_a_digest(cm):
    assert cm.OID_RE.match("0" * 64)
    assert not cm.OID_RE.match("0" * 64 + "\n")


def test_presets_keep_each_model_its_own_section(cm):
    cm.write_presets(["version = 1"], {"good": {"model": "/m/good.gguf"},
                                        "other": {"model": "/m/other.gguf"}})
    assert cm.read_presets()[1] == {"good": {"model": "/m/good.gguf"},
                                    "other": {"model": "/m/other.gguf"}}
