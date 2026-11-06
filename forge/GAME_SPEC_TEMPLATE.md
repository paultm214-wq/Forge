# <Game, emulator, mod or RE project name>

<!--
A spec for a project that is not plain Python: a game (Godot, LOVE, C/SDL,
Rust), an emulator, a mod, or reverse engineering of code you are free to
use. Drop a filled-in copy into ~/forge/inbox/ on Gemini, or `forge run it.md`.

Everything in SPEC_TEMPLATE.md still applies - above all the Acceptance
section. What is new is below. Delete the sections you do not need.

Before planning, forge searches GitHub for freely licensed code that already
does part of the job (an addon, a library, an emulator core) and builds on it
instead of rewriting it. Only permissive licences are used unless the project
itself says it is GPL (see License below).
-->

Toolchain: godot
<!-- one of: godot, c-sdl, rust, lua-love, dotnet, java, re, sbox, gmod, fo4,
     blender (or leave the line out for Python). Its sandbox image comes from
     install-forge-toolchains.sh; `forge selftest <name>` checks it.
     Game mods read the game's own files, read-only, from [games] in config.toml:
       sbox     C# for s&box: code in Code/, MSTest tests in UnitTests/; tests run
                C# inside the headless engine (game.sbox_eval)
       gmod     a Garry's Mod addon (addon.json, lua/...); tests run Lua on a
                headless dedicated server on gm_construct (game.gmod_run) and
                glualint; client-side code is only syntax-checked
       fo4      Fallout 4: Papyrus in Scripts/Source/User/ (compiled to .pex),
                the plugin built by Plugin/Program.cs with Mutagen; tests check
                compiled scripts and the plugin's records - nothing runs the game.
                outputs/build is the Data folder to install
       blender  Python scripts for Blender 5 (bpy): make or change models,
                measure them (game.mesh_info), render them (game.blender_render);
                a build.py's output becomes outputs/build -->

License: MIT
<!-- The licence YOUR project will carry. "License: GPL-3.0" also lets forge
     build on GPL code; anything else keeps prior art to permissive licences. -->

Prior art: auto
<!-- "Prior art: none" skips the GitHub search for this spec. -->

Assets dir: assets
<!-- where rendered images land in the project (Godot: res://assets/...). -->

UI: game
<!-- game (the default for godot, lua-love, c-sdl, rust and sbox), gui, web
     or none. A game gets the full menu set from ~/forge/design/game-ui.md -
     title, pause, settings (audio, display, controls with rebinding,
     accessibility), save/load, confirmations, How to play - working with
     keyboard, mouse and gamepad, adapted to this game in a design brief.
     Godot projects get an input and menu audit after each build (gamepad
     bindings, focus, saved settings, smallest font), and every game an
     illustrated player's guide at commit. All of it is reported, not
     enforced. -->

Help: auto
<!-- yes / no / auto: an in-game "How to play" screen, its content filled at
     commit from the player's guide (help/help.json in the project). -->

## Goal

What the game or tool is, who plays or uses it, what "done" looks like.

## Constraints

- Engine / language version: Godot 4.x, GDScript only (no C#)
- Window size, target frame rate, input scheme (keyboard actions by name)
- Determinism: game logic takes a seed and no wall-clock time, so tests and
  frame captures are the same every run

## Interfaces

Cross-file names other code depends on, in the project's own language:
scenes, node paths, signals, input actions, public functions.

- `res://main.tscn` - root scene, a Node2D named Main
- `Player.gd`: `signal died`, `func hurt(amount: int) -> void`
- Input actions: `move_left`, `move_right`, `jump`

## Assets

Images render on Vulcan/Mercury while the code is built; a chunk whose task
or test names an image's file (player.png) waits for that render before it
starts. Game-art steps run after the render, in this order: chroma_key ->
pixelate -> palette -> scale. `frames` makes a sprite sheet: the same seed
for every frame, and frame 1's render (before the game-art steps) is a
reference image for the rest so the character stays the same.

```json
[
  {"id": "a1", "filename": "player.png", "title": "player sprite sheet",
   "prompt": "a small fox adventurer, side view, flat colours, plain magenta background",
   "width": 1024, "height": 1024, "seed": 7,
   "chroma_key": "#ff00ff", "chroma_tolerance": 60,
   "pixelate": {"width": 32, "height": 32},
   "palette": "pico8",
   "scale": 2,
   "frames": ["standing", "left foot forward", "right foot forward", "jumping"],
   "sheet_columns": 4},
  {"id": "a2", "filename": "tiles.png", "prompt": "grass and dirt platform tiles",
   "width": 1024, "height": 1024, "tileable": true,
   "pixelate": {"width": 64, "height": 64}, "palette": 16}
]
```

palette: pico8, gameboy, gameboy-gray, c64, cga, a list of hex colours
(["0f380f", "306230", ...]), or a number for the best N colours.

**3D models.** Add `"shape"` to an image asset and forge also turns the
render into an untextured model (Hunyuan3D 2.1 on Vulcan, `[shapes]` in
config.toml). The image is the model's only input, so prompt for ONE whole
object, centred, three-quarter view, plain white background, no shadow; the
background is removed before the shape is made. A chunk that names the model
file waits for it. Models land beside the images (assets/models/barrel.glb).

```json
[
  {"id": "m1", "filename": "refs/barrel.png", "title": "oil barrel reference",
   "prompt": "a dented rusty oil barrel, single object, centred, three-quarter view, plain white background, studio light, no shadow",
   "width": 1024, "height": 1024, "seed": 3,
   "shape": {"filename": "models/barrel.glb", "max_faces": 8000}}
]
```

shape keys: filename (required, .glb), max_faces (20000), steps (30),
guidance (5.0), octree_resolution (256; 384 is finer and slower), seed (0),
remove_background (default: when the image has no transparency), timeout.
Texturing is not available (it needs 21 GB of VRAM); a `blender` project can
add materials.

## Captures

Frames forge captures from the running program after the build. Claude's
review looks at every one, so a broken scene or a black screen is caught
even when every test passes. Godot runs in movie-maker mode at a fixed frame
rate, so captures are identical run to run; scripted inputs press actions on
given frames.

```json
[
  {"name": "start", "scene": "res://main.tscn", "frames": 30, "fps": 30, "keep": [29]},
  {"name": "jump", "scene": "res://main.tscn", "frames": 90, "fps": 30,
   "inputs": [{"frame": 30, "action": "jump", "pressed": true},
              {"frame": 36, "action": "jump", "pressed": false}],
   "keep": [40, 89]}
]
```

For other toolchains give a command and times instead:
`{"name": "title", "cmd": ["{project}/build/game"], "at": [1.0, 3.0], "size": [640, 480]}`
(LOVE: `{"name": "title", "love": true, "at": [1.0]}`). Models (blender
toolchain): `{"name": "barrel", "model": "assets/models/barrel.glb",
"views": ["front", "iso"], "size": [512, 512]}` renders each view;
`"{build}/barrel.glb"` is a model the project's build.py made.

## Private files

Files you supply that must never be copied into the project, a commit or a
prompt: a ROM you dumped from your own cartridge, a game's mod API files, a
binary you wrote. Mounted read-only in the test sandbox; tests reach them
with `game.private("name")`.

- my-rom: ~/roms/homebrew-pong.ch8

## Provenance

Required whenever there are private files or the toolchain is `re`. For each
file (by name) and for any binary being analysed: where it came from and why
you may use it - your own work, an open licence (MIT, GPL, CC0...), public
domain, or written permission. forge refuses to run without it; it records
your statement but cannot verify it. forge never downloads ROMs or games.

- my-rom: my own homebrew, I wrote it in 2024
- target binary: built from the MIT-licensed source at github.com/<owner>/<repo>

## Acceptance

Checkable facts. For games, test the LOGIC with known answers (a jump peaks
at 64 px after 0.25 s at 60 fps; three hits empty the health bar) and the
SCENE by running it (the main scene loads with no script errors; after 30
frames the player sprite is on screen: game.count_color(...) > 0).

1. <behaviour> -> <exact expected result>
2. <scene loads> -> no errors from game.godot_import(), main.tscn runs 60 frames
3. <capture check> -> frame 29 of "start" is not blank

For mods, test what can run: s&box components in a scene (Hurt(30) leaves 70
health); a GMod entity spawned on gm_construct (it falls, its hook fires);
Fallout 4 scripts compile and the plugin holds the records you expect (the
item "MyModTag" is MISC with value 7; Fallout4.esm is the only master); a
model has at most 8000 triangles and is watertight.

## Playtest

What a person should check on Vulcan (`forge playtest <project>` copies the
build there with this list as PLAYTEST.md):

- Controls feel responsive; jump height feels right
- No visual glitches when the player walks off a ledge
- Sound plays (if any)
