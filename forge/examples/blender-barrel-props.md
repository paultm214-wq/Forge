# barrel-props

Toolchain: blender
License: MIT

## Goal

Game-ready props from one generated model and one procedural one. forge
renders a reference image of an oil barrel, Hunyuan3D 2.1 on Vulcan turns it
into a mesh, and a Blender build script cleans it up for a game engine:
merged, decimated to a triangle budget, standing on the ground at the
origin, real-world size, one named material. A procedural crate is built
beside it. Both export as GLB into outputs/build, and the review sees
renders of each.

## Constraints

- Blender 5 Python (bpy, bmesh), run headless: `build.py` at the project
  root, run as `blender -b --python build.py -- --out <folder>`; helpers in
  `props/` (a package `build.py` imports by adding its own folder to
  sys.path).
- Input: `assets/models/barrel.glb` (the shape below), opened with
  `bpy.ops.import_scene.gltf`. Never edit files under assets/.
- Output: `<out>/barrel.glb` and `<out>/crate.glb` (`bpy.ops.export_scene.gltf`,
  GLB, +Y up as glTF expects - the exporter's default).
- Units: metres, Z up inside Blender.

## Interfaces

- `props/cleanup.py`: `def game_ready(obj, height_m: float, max_tris: int,
  material: str) -> None` - joins/merges duplicate vertices (0.0001 m),
  rotates so the tallest bounding-box axis is Z, scales uniformly to
  `height_m` tall, moves it so its bounding-box bottom centre is at the
  origin, applies the transforms, decimates (collapse) until it has at most
  `max_tris` triangles, and gives it one Principled material named `material`.
- `props/crate.py`: `def build_crate(size_m: float = 1.0, bevel_m: float = 0.03)
  -> bpy.types.Object` - a bevelled cube, base at z = 0, material "CrateWood".

## Assets

```json
[
  {"id": "barrel", "filename": "refs/barrel.png", "title": "oil barrel reference",
   "prompt": "a dented rusty steel oil barrel, single object, centred, three-quarter view from slightly above, plain white background, soft studio light, no shadow, product photo",
   "width": 1024, "height": 1024, "seed": 11,
   "shape": {"filename": "models/barrel.glb", "max_faces": 20000}}
]
```

## Captures

```json
[
  {"name": "barrel-raw", "model": "assets/models/barrel.glb", "views": ["front", "iso"]},
  {"name": "barrel", "model": "{build}/barrel.glb", "views": ["front", "iso"]},
  {"name": "crate", "model": "{build}/crate.glb", "views": ["iso"]}
]
```

`{build}` is what build.py made, so the review sees the raw shape beside the
game-ready props.

## Acceptance

Run `out = game.blender_build()` once, then with `game.mesh_info`:

1. `out/barrel.glb`: one mesh object, triangles <= 3000 and >= 300, height
   (dimensions Z) 0.90 m within 0.01, the bounding box's lowest Z is 0 within
   0.001 and its X/Y centre is within 0.01 of the origin, material "Rust".
2. `out/crate.glb`: dimensions 1.0 x 1.0 x 1.0 within 0.01, watertight when
   checked in Blender before export (`game.blender_eval` building it with
   props.crate), lowest Z 0, material "CrateWood".
3. `game.blender_render(out / "barrel.glb", views=("front", "iso"))` and the
   same for crate.glb give frames that are not blank.
4. The source asset is untouched: `assets/models/barrel.glb` still has the
   shape server's face count (more than 3000 triangles).
