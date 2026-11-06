"""Regression tests for forge's helpers - one per incident its comments
record (glassinv, gardeninv, frigate-digest ...), so none comes back."""

import ast
import os
import tempfile
import unittest
from pathlib import Path

from _load import forge

f = forge


class Assembly(unittest.TestCase):
    def test_later_class_adds_methods_instead_of_replacing(self):
        # glassinv c3-c5: a second `class Store:` replaced the first.
        c1 = "from dataclasses import dataclass\nclass Store:\n    def a(self):\n        return 1\n"
        c2 = "class Store:\n    def b(self):\n        return 2\n"
        ns = {}
        exec(f.assemble({"c1": c1, "c2": c2}, ["c1", "c2"]), ns)
        s = ns["Store"]()
        self.assertEqual((s.a(), s.b()), (1, 2))

    def test_repeated_bases_still_extend(self):
        c1 = "class Base:\n    pass\nclass Store(Base):\n    def a(self):\n        return 1\n"
        c2 = "class Store(Base):\n    def b(self):\n        return 2\n"
        ns = {}
        exec(f.assemble({"c1": c1, "c2": c2}, ["c1", "c2"]), ns)
        self.assertEqual(ns["Store"]().a(), 1)

    def test_same_member_name_replaces_old_member(self):
        c1 = "class S:\n    def a(self):\n        return 1\n"
        c2 = "class S:\n    def a(self):\n        return 9\n"
        ns = {}
        exec(f.assemble({"c1": c1, "c2": c2}, ["c1", "c2"]), ns)
        self.assertEqual(ns["S"]().a(), 9)

    def test_split_imports_keeps_decorators_and_comments(self):
        src = "import os\n# a note\n@staticmethod\ndef f():\n    pass\n"
        imports, body = f.split_imports(src)
        self.assertEqual(imports, ["import os"])
        self.assertIn("# a note", body[0])
        self.assertIn("@staticmethod", body[0])

    def test_binding_conflict_datetime(self):
        chunks = {"c1": "from datetime import datetime\n", "c4": "import datetime\n"}
        self.assertTrue(f.binding_conflicts(chunks, ["c1", "c4"], "c4"))

    def test_decorated_redefinition_is_reported(self):
        chunks = {"c1": "class S:\n    pass\n",
                  "c2": "from dataclasses import dataclass\n@dataclass\nclass S:\n    x: int = 0\n"}
        self.assertTrue(f.class_extension_conflicts(chunks, ["c1", "c2"], "c2"))


class Extraction(unittest.TestCase):
    def test_code_containing_fences(self):
        # glassinv check 31: a test of a Markdown reply contains ```.
        reply = "text\n```python\nx = '''\n```\n'''\ny = 2\n```\nmore"
        self.assertIn("y = 2", f.extract_code(reply))

    def test_trim_keeps_first_line_indent(self):
        # glassinv run 3: strip() put `def` at column 0 over an indented body.
        self.assertEqual(f._trim_code("\n\n    def a(self):\n        pass\n"),
                         "    def a(self):\n        pass")

    def test_bare_methods_are_wrapped_in_their_class(self):
        verified = {"c1": "class Store:\n    def __init__(self):\n        self.x = 1\n"}
        chunk = {"spec": "Add add_lot to Store."}
        # The reply's first line lost its indent; the rest kept it.
        reply = "def add_lot(self, n):\n        return n\n\n    def lots(self):\n        return []\n"
        code, cls = f.wrap_bare_methods(reply, verified, chunk)
        self.assertEqual(cls, "Store")
        ast.parse(code)

    def test_json_after_a_brace_in_prose(self):
        self.assertEqual(f.extract_json('returns {project, chunks}: {"a": 1}'), {"a": 1})

    def test_json_with_raw_newlines_in_strings(self):
        self.assertEqual(f.extract_json('{"a": "x\ny"}'), {"a": "x\ny"})

    def test_no_json_is_a_forge_error(self):
        with self.assertRaises(f.BadJSON):
            f.extract_json("nothing here")


class Contract(unittest.TestCase):
    plan = {"chunks": [{"id": "c1"}, {"id": "c2"}],
            "contract": {"names": [{"name": "Store.add", "owner": "c1", "signature": "(self, x)"},
                                   {"name": "view", "owner": "c2", "signature": "(req)"}]}}

    def test_other_owners_methods_are_removed_and_the_class_still_parses(self):
        code = "class Store:\n\n    def add(self, x):\n        return x\n"
        new, removed, _ = f.enforce_contract(code, "c2", self.plan)
        self.assertEqual(removed, ["Store.add (owned by c1)"])
        ast.parse(new)

    def test_local_stand_in_is_a_problem(self):
        # glassinv c7: nested stand-ins hid c9's views.
        code = "def router(req):\n    def view(req):\n        return 1\n    return view(req)\n"
        _, _, problems = f.enforce_contract(code, "c1", self.plan)
        self.assertTrue(any("local `view`" in p for p in problems))

    def test_signature_compatibility(self):
        node = ast.parse("def f(a, b, *, strict=False): pass").body[0]
        self.assertTrue(f.signature_compatible(node, "(a, b)"))
        node = ast.parse("def f(a): pass").body[0]
        self.assertFalse(f.signature_compatible(node, "(a, b)"))

    def test_bare_string_hooks_are_normalised(self):
        # glassinv change planning: validation crashed on bare strings.
        con = f.normalize_hooks({"routes": ["GET /supplies/<kind>/<id> - list"],
                                 "names": ["Store.delete(self, kind, id) [c3] - remove"]})
        self.assertEqual(con["routes"][0]["path"], "/supplies/<kind>/<id>")
        self.assertEqual(con["names"][0]["owner"], "c3")


class Spec(unittest.TestCase):
    def test_fixture_needs_the_word_not_a_substring(self):
        spec = ("## Latest rates (copy verbatim)\n```python\nRATES = {}\n```\n"
                "## Test fixture (copy verbatim)\n```python\nGLASS = []\n```\n")
        kinds = {h: fx for h, fx, _ in f.verbatim_sections(spec)}
        self.assertFalse(kinds["Latest rates (copy verbatim)"])
        self.assertTrue(kinds["Test fixture (copy verbatim)"])

    def test_heading_comment_inside_python_block(self):
        spec = "## Vocab (copy verbatim)\n```python\n# not a heading\nV = 1\n```\n"
        self.assertEqual(f.verbatim_code(spec).strip(), "# not a heading\nV = 1")

    def test_acceptance_items(self):
        spec = "## Acceptance\n\n1. foo -> bar\n2) baz\n- R3: qux\nShows 10 items per page\n"
        self.assertEqual(f.acceptance_items(spec), ["1", "2", "R3"])

    def test_assets_json_is_validated(self):
        spec = "## Assets\n```json\n[{\"id\": \"a1\", \"filename\": \"../x.png\", \"prompt\": \"p\"}]\n```\n"
        with self.assertRaises(f.ForgeError):
            f.spec_assets(spec)


class Diagnosis(unittest.TestCase):
    plan = {"chunks": [{"id": "c1"}], "contract": {"schema_sql":
            "CREATE TABLE lots (id INTEGER, name TEXT);"}}

    def test_missing_f_prefix(self):
        hints, sure = f.mechanical_hints(self.plan, {"id": "c1"}, "",
                                         'sqlite3.OperationalError: near "{": syntax error')
        self.assertTrue(sure and "f-string" in hints[0])

    def test_unknown_column_lists_the_schema(self):
        hints, sure = f.mechanical_hints(self.plan, {"id": "c1"}, "",
                                         "sqlite3.OperationalError: no such column: lots.title")
        self.assertTrue(sure and "lots(id, name)" in hints[0])

    def test_count_checks_counts_raise_and_exit(self):
        self.assertEqual(f.count_checks("assert x\nraise SystemExit(1)\nimport sys\nsys.exit(1)\n"), 3)


class Outputs(unittest.TestCase):
    def test_symlink_in_outputs_is_refused(self):
        # A symlink made in the container resolves on the HOST.
        src, dst = Path(tempfile.mkdtemp()), Path(tempfile.mkdtemp())
        os.symlink("/etc/hostname", src / "x.txt")
        with self.assertRaises(f.ForgeError):
            f.copy_outputs(src, dst / "o", 2**20)

    def test_docker_failure_is_not_a_test_result(self):
        self.assertTrue(f.docker_failed(1, "Cannot connect to the Docker daemon at unix://"))
        self.assertFalse(f.docker_failed(1, "AssertionError: 2 != 3"))


class Images(unittest.TestCase):
    def png(self, w, h):
        px = bytes(v for y in range(h) for x in range(w) for v in (x * 30 % 256, y * 40 % 256, 200))
        return f.png_encode(w, h, 3, px), px

    def test_png_roundtrip(self):
        raw, px = self.png(8, 6)
        w, h, ch, out = f.png_decode(raw)
        self.assertEqual((w, h, ch, bytes(out)), (8, 6, 3, px))

    def test_game_art_and_sheet(self):
        raw, _ = self.png(8, 6)
        art = f.postprocess_image({"pixelate": {"width": 4, "height": 3}, "palette": "pico8",
                                   "scale": 2}, raw)
        self.assertEqual(f.png_size(art), (8, 6))
        self.assertEqual(f.png_size(f.pack_sheet([art, art, art], 2)), (16, 12))


class FilesMode(unittest.TestCase):
    def test_files_container_roundtrip(self):
        files = {"scripts/player.gd": "extends Node\n", "a.txt": "ends with quote'\n",
                 "b.txt": "has ''' inside\n"}
        code = f.files_to_python(files)
        compile(code, "x", "exec")
        self.assertEqual(f.python_to_files(code), files)

    def test_unsafe_path_in_reply(self):
        with self.assertRaises(f.ForgeError):
            f.parse_files_reply("FILE: ../evil.sh\n```sh\nrm -rf ~\n```\n")

    def test_forge_game_is_a_real_module(self):
        from _load import FORGE_GAME_PY
        ast.parse(FORGE_GAME_PY.read_text())
        self.assertEqual(f.forge_game_source(), FORGE_GAME_PY.read_text())


if __name__ == "__main__":
    unittest.main()
