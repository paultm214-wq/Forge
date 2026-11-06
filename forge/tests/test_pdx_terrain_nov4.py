"""4 November, pdx-slice-a-terrain: GeoTiff.width set in __init__ was
"not defined" by the contract check (6 attempts lost), and the planner's
paraphrase of the TIFF/GeoTIFF facts was wrong in every coder's prompt."""
import ast
from pathlib import Path

import forge

HERE = Path(__file__).resolve().parent.parent

GEOTIFF = '''
class GeoTiff:
    def __init__(self, path):
        self.width = 20
        self.height, self.epsg = 18, 26910
        self.origin: tuple = (0.0, 0.0)
        self._cache = {}

    def value(self, col, row):
        return None
'''


def test_instance_attributes_count_as_defined():
    have = forge._defined(ast.parse(GEOTIFF))
    for name in ("GeoTiff.width", "GeoTiff.height", "GeoTiff.epsg", "GeoTiff.origin",
                 "GeoTiff.value"):
        assert name in have, name
    assert "GeoTiff.path" not in have           # a parameter is not an attribute


def test_the_contract_check_accepts_attributes_set_in_init():
    plan = {"chunks": [{"id": "c3"}],
            "contract": {"names": [{"name": "GeoTiff", "owner": "c3"},
                                   {"name": "GeoTiff.width", "owner": "c3"},
                                   {"name": "GeoTiff.value", "owner": "c3",
                                    "signature": "(self, col, row)"}]}}
    _, _, problems = forge.enforce_contract(GEOTIFF, "c3", plan)
    assert not any("width" in p for p in problems), problems


def test_reference_facts_reach_the_coder_word_for_word(monkeypatch):
    spec = (HERE / "examples" / "pdx-slice-a-terrain.md").read_text()
    ref = forge.spec_reference(spec)
    assert ref.startswith("Exact format facts")
    assert "1024 GTModelTypeGeoKey (1 = projected, 2 = geographic" in ref
    assert "11 FLOAT 4, 12 DOUBLE 8" in ref
    assert "## Interfaces" not in ref and "inverse_compress" not in ref
    monkeypatch.setattr(forge, "RUN_SPEC_TEXT", spec)
    plan = {"summary": "s", "chunks": [{"id": "c3", "spec": "Key 1024: 1=geographic",
                                         "verify": "assert True"}]}

    class Coder:
        ctx = 200000
        name = "coder"

        def count(self, text):
            return len(text) // 3

    monkeypatch.setattr(forge, "fit", lambda llm, fixed, extra, **k: "\n\n".join(fixed))
    prompt = forge.build_chunk_prompt(Coder(), plan, plan["chunks"][0], {}, None, None)
    assert "## REFERENCE FACTS" in prompt and "12 DOUBLE 8" in prompt


def test_no_reference_section_no_block():
    assert forge.spec_reference("# x\n\n## Goal\n\ntext\n") == ""


def test_a_heading_inside_a_code_fence_does_not_end_the_section():
    spec = "## Reference facts\n\nA\n```\n## not a heading\n```\nB\n\n## Interfaces\nC\n"
    assert forge.spec_reference(spec) == "A\n```\n## not a heading\n```\nB"
