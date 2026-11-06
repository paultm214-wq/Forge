# pdx-slice-a-props

Toolchain: blender
License: MIT
Prior art: none

<!--
Portland Survival Map (Fallout 4), Slice A - part 1 of 2.
This run makes the props; pdx-slice-a-plugin.md (Toolchain: fo4) makes the
plugin whose static records point at them. Run this one first.

Out of scope here, done by hand afterwards: converting the GLBs to NIF with
collision for Fallout 4. That needs a Blender NIF add-on (PyNifly is the one
usually named - check that your version supports Fallout 4 and Blender 5
before relying on it). forge's sandbox has no NIF exporter.

Sizes below are game-scale choices for a compressed map, NOT measurements of
the real objects - adjust them before running if you want them closer to
life.
-->

## Goal

Four game-ready props for the first buildable area of the Portland map
(downtown, Washington Park, OHSU and the tram), made from generated
reference images: forge renders each image on Apollo, Hunyuan 3D on Vulcan
turns it into a mesh, and `build.py` cleans every mesh up for a game engine.
That means one joined mesh, a triangle budget, a fixed height in metres,
standing on the ground at the origin, and one named material. `build.py`
writes a GLB per prop plus `manifest.json`, which the plugin run and the NIF
conversion read.

## Constraints

- Blender 5 Python (bpy, bmesh), headless. `build.py` lives at the project
  root and runs as `blender -b --python build.py -- --out <folder>`.
  Helpers go in `pdxprops/`, a package `build.py` imports by adding its own
  folder to sys.path.
- Inputs: the four shapes below, `assets/models/<id>.glb`, opened with
  `bpy.ops.import_scene.gltf`. Never write under assets/.
- Outputs, all in `<out>/`:
  - `<Name>.glb` for each prop (`bpy.ops.export_scene.gltf`, GLB, glTF's
    default +Y up);
  - `manifest.json`.
- Units: metres, Z up inside Blender. Fallout 4's own units (1 m is about
  70 units) are applied when the NIF is made, not here.
- Every prop is a single object named exactly `<Name>`, with no loose
  parts: pieces smaller than 1% of the largest piece's vertex count are
  deleted.
- No textures. Each prop gets one Principled material named
  `PDX_<Name>_Mat` whose base colour comes from the table below, so the
  untextured mesh still reads in renders.

## Interfaces

- `pdxprops/cleanup.py`:
  `def game_ready(obj, name: str, height_m: float, max_tris: int, colour: tuple[float, float, float]) -> dict`.
  It:
  - joins the imported pieces and merges duplicate vertices (0.0001 m);
  - drops loose parts under 1%;
  - keeps the import's orientation. glTF's +Y up is already Blender's +Z,
    and a wide prop such as the stall or the cabin is wider than it is tall,
    so it never rotates to the tallest axis;
  - scales uniformly to `height_m` tall;
  - moves the bounding-box bottom centre to the origin and applies all
    transforms;
  - decimates (collapse) until there are at most `max_tris` triangles;
  - recalculates normals outside, names the object `name`, and gives it
    the material described above.

  It returns `{"name", "triangles", "dimensions": [x, y, z], "material"}`.
- `pdxprops/props.py`: `PROPS`, a list of dicts with the keys `id`, `name`,
  `height_m`, `max_tris`, `colour` and `nif`, exactly as in the table below.
- `manifest.json`: `{"props": [{"name", "glb", "nif", "triangles",
  "dimensions_m": [x, y, z]}]}`, in the table's order. `nif` is the path the
  plugin's static records use, relative to Data/Meshes.

| id | name | height_m | max_tris | colour (linear RGB) | nif |
|---|---|---|---|---|---|
| bubbler | BensonBubbler | 1.0 | 3000 | (0.42, 0.30, 0.16) | PDX\Props\BensonBubbler.nif |
| tramcab | TramCabinRuin | 4.0 | 6000 | (0.55, 0.56, 0.58) | PDX\Props\TramCabinRuin.nif |
| stall | HeartMarketStall | 2.6 | 4000 | (0.40, 0.25, 0.14) | PDX\Props\HeartMarketStall.nif |
| still | ReclaimerWaterStill | 2.0 | 4000 | (0.30, 0.38, 0.32) | PDX\Props\ReclaimerWaterStill.nif |

## Assets

```json
[
  {"id": "bubbler", "filename": "refs/bubbler.png", "title": "Portland-style four-bowl drinking fountain",
   "prompt": "a weathered bronze public drinking fountain with four small round bowls on a short fluted column, Portland street furniture, verdigris patina, single object, centred, three-quarter view from slightly above, isolated on a pure white background, floating, no floor, no ground plane, no shadow, even soft light",
   "width": 1024, "height": 1024, "seed": 501,
   "shape": {"filename": "models/bubbler.glb", "max_faces": 20000}},
  {"id": "tramcab", "filename": "refs/tramcab.png", "title": "ruined aerial tram cabin",
   "prompt": "a derelict silver aerial tramway cabin resting on the ground, rounded capsule shape, broken windows, dented panels, no cable, single object, centred, three-quarter view, isolated on a pure white background, floating, no floor, no ground plane, no shadow, even soft light",
   "width": 1024, "height": 1024, "seed": 502,
   "shape": {"filename": "models/tramcab.glb", "max_faces": 30000}},
  {"id": "stall", "filename": "refs/stall.png", "title": "post-apocalyptic market stall",
   "prompt": "a post-apocalyptic scrap-wood market stall with a corrugated metal roof and a plank counter, empty, single object, centred, three-quarter view, isolated on a pure white background, floating, no floor, no ground plane, no shadow, even soft light",
   "width": 1024, "height": 1024, "seed": 503,
   "shape": {"filename": "models/stall.glb", "max_faces": 20000}},
  {"id": "still", "filename": "refs/still.png", "title": "improvised water still",
   "prompt": "an improvised rain water purification still made from a steel drum, copper pipes and a funnel on a wooden stand, single object, centred, three-quarter view, isolated on a pure white background, floating, no floor, no ground plane, no shadow, even soft light",
   "width": 1024, "height": 1024, "seed": 504,
   "shape": {"filename": "models/still.glb", "max_faces": 20000}}
]
```

## Captures

```json
[
  {"name": "bubbler-raw", "model": "assets/models/bubbler.glb", "views": ["iso"]},
  {"name": "BensonBubbler", "model": "{build}/BensonBubbler.glb", "views": ["front", "iso"]},
  {"name": "TramCabinRuin", "model": "{build}/TramCabinRuin.glb", "views": ["front", "iso"]},
  {"name": "HeartMarketStall", "model": "{build}/HeartMarketStall.glb", "views": ["front", "iso"]},
  {"name": "ReclaimerWaterStill", "model": "{build}/ReclaimerWaterStill.glb", "views": ["front", "iso"]}
]
```

## Acceptance

Run `out = game.blender_build()` once, then use `game.mesh_info`:

1. `out` holds exactly the four GLBs named in the table, plus
   `manifest.json`.
2. For each prop:
   - there is one mesh object, named `<Name>`;
   - its triangles are at most max_tris and at least 200;
   - dimensions Z equals height_m within 1%;
   - the bounding box's lowest Z is 0 within 0.001;
   - its X/Y centre is within 1% of height_m of the origin;
   - it has exactly one material, `PDX_<Name>_Mat`.
3. `manifest.json` lists the four props in the table's order. Each entry's
   `triangles` and `dimensions_m` match `game.mesh_info` of its GLB within
   1%, and each `nif` is exactly the table's path.
4. `game.blender_render(out / "<Name>.glb", views=("front", "iso"))` gives
   frames that are not blank, for every prop.
5. The inputs are untouched: every `assets/models/<id>.glb` still has more
   triangles than its prop's max_tris.
6. `pdxprops.cleanup.game_ready` on a test object (a box 3 x 1 x 0.5 m with
   a 4-vertex loose fragment beside it, height_m 1.0) gives one object with
   no fragment, standing on z = 0 and centred on X/Y. It is NOT rotated:
   its dimensions are 6 x 2 x 1, uniformly scaled from 3 x 1 x 0.5.
