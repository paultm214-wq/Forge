# rose-city-signal

Toolchain: godot
License: MIT
Prior art: auto
Assets dir: assets

<!--
A retro point-and-click adventure set in the post-Event Portland of the
"Portland Survival Map" project (factions, zones and landmarks from its v3
canon), built in Godot 4 at 320x200 like an early-90s adventure game.

Facts about real places come only from the "Facts" section below: each was
checked against the source named there. The "Never use" list holds errors
from earlier drafts of the map project; a test fails if any of them appears
in the game's text.

Real public places keep their real names (Pioneer Courthouse Square, Mt.
Tabor, OHSU, Reed College, Council Crest). Private businesses get parody
names (Rowell's City of Books, Hoodoo Doughnut, Hell's Point), so the game
can be shared without putting real shops into a post-apocalyptic story. To
use the real names instead, change them in this spec and in the "Never use"
test (Acceptance 11) before running it.
-->

## Goal

"Rose City Signal": a short point-and-click adventure, about an hour to play,
in the style of early-90s LucasArts and Sierra games: painted pixel-art rooms,
a cursor, an inventory bar, hotspots that name themselves on hover, and
puzzles solved by trading and using items. LucasArts rules apply: the player
can't die and can't get stuck. Every state the game can reach can still be
won, and a test proves it (Acceptance 7).

Story. Twelve years after the Event, Portland is split between factions. June
Ruiz is a bike courier for The Heart, the neutral traders who hold Pioneer
Courthouse Square. The old radio tower on Council Crest could reach every
faction at once, and The Heart wants it to broadcast a truce: a market day at
the Square, where everyone can trade under one roof. The tower needs three
things: someone who knows how to fix it, its transmitter tubes (The Clinic
took them up Marquam Hill), and a charged battery. The only way up Marquam
Hill is the OHSU Aerial Tram, which The Clinic guards and which has no power.

Tone: wry and warm, not grim. The factions are people with jobs, not monsters.
Nothing explicit: Hell's Point is a rock-and-roll bar where The Point runs its
cash trade, and its stage is empty.

Done means the game can be played from the title screen to the ending with
mouse or keyboard, every room has its painted background, and the whole game
is data-driven from `res://data/world.json`, which tests can play through.

## Facts

Use these in look texts, and nothing beyond them about real places. Sources
are the ones checked for the map project (v3) or for this spec (October 2026).

- Pioneer Courthouse Square: the central plaza of downtown, between SW
  Broadway and SW 6th; nicknamed "Portland's living room". (Wikipedia, v3)
- Powell's City of Books (parodied as Rowell's): fills a whole city block, at
  1005 W Burnside. (v3)
- Voodoo Doughnut (parodied as Hoodoo): the original shop is at 22 SW 3rd Ave
  in Old Town. (v3)
- Mt. Tabor: an extinct volcanic cinder cone in a city park, summit 636 ft
  (194 m), with open reservoirs. (Wikipedia, v3)
- Washington Park: 458 acres, holding the Oregon Zoo, the International Rose
  Test Garden (terraced, facing downtown) and the Portland Japanese Garden.
  (Wikipedia, v3)
- OHSU Aerial Tram: two stations about 1 km apart, due east-west; the upper
  station on Marquam Hill is about 500 ft higher than the lower one at South
  Waterfront, and the tram crosses I-5. (Wikipedia, v3)
- From OHSU, the Connor Trail drops into Marquam Nature Park, and the Marquam
  Trail climbs from there to Council Crest. (Oregon Hikers field guide, "4T
  Loop", checked for this spec)
- Council Crest: about 1,070 ft, one of the highest points in Portland, with
  a tall radio tower. (Wikipedia, v3: 1,071-1,073 ft)
- Reed College, 3203 SE Woodstock Blvd: its research reactor is a TRIGA Mark
  I at the bottom of a 25-foot-deep tank, licensed to 250 kW, running since
  1968, and the only reactor operated primarily by undergraduates. It is a
  research reactor, not a power plant: it never made the college's
  electricity. (reed.edu/reactor, checked for this spec)
- Devils Point (parodied as Hell's Point): a small rock-and-roll club at 5305
  SE Foster Rd. (Wikipedia, v3)
- St. Johns Bridge: a steel suspension bridge over the Willamette, opened
  June 13, 1931, with two 400 ft towers. (Wikipedia, checked for this spec)
- I-205 is the map's eastern edge; east of it is the Barrens. (map project
  canon)

## Never use

Errors from earlier drafts of the map project. None of these may appear in
the game's text (Acceptance 11 checks the exact strings in quotes):

- "Teton Creek" (it is Tryon Creek; Tryon Creek is not in this game)
- "Titty City" (it was never the club's name)
- "Queen Anne" (Pittock Mansion is a French Renaissance chateau)
- "643" or "907" for Mt. Tabor (the summit is 636 ft)
- "Portland Zoo" (it is the Oregon Zoo)
- "1916" or "bascule" for the St. Johns Bridge (suspension, 1931)
- "West Hills Park" and "Rock Bottom" (not real places)
- "old growth" and "old-growth" (Tryon Creek, which earlier drafts called old
  growth, was logged repeatedly until 1961)
- "523 SE Clay" and "120 NW 10th" (wrong addresses for the real shops)
- The real business names "Powell's", "Voodoo" and "Devils Point"/"Devil's
  Point" (parody names only)

Geography from earlier drafts, also wrong (not testable as strings; the map
data below is generated from verified coordinates): PDX is in NE Portland on
the Columbia, east of downtown, not west; Moda Center is on the east bank;
Providence Park is on the west side in Goose Hollow; the Columbia Slough runs
along the north edge near PDX, not beside downtown; N Mississippi Ave is a bar
and music strip; Montavilla is west of I-205; I-205 runs through Lents.

## Constraints

- Godot 4 (the sandbox's version), GDScript only, no C#, no addons that need
  native code. No audio.
- project.godot: viewport 320x200 (`display/window/size/viewport_width=320`,
  `viewport_height=200`), window 1280x800
  (`window_width_override=1280`, `window_height_override=800`),
  `display/window/stretch/mode="viewport"`,
  `display/window/stretch/scale_mode="integer"`, and nearest-neighbour
  filtering (`rendering/textures/canvas_textures/default_texture_filter=0`).
  Main scene `res://main.tscn`. Window title "Rose City Signal".
- Screen layout (320x200): the room view is y 0-143 (320x144), a message line
  y 144-155 (hovered hotspot's name, or the last result text), and the
  inventory bar y 156-199: up to 8 item slots of 32x32 plus a MAP button and
  scroll arrows when there are more than 8 items.
- Font: a pixel font under CC0 or the SIL OFL if prior art finds one (vendor
  it with its licence file); otherwise Godot's default font, drawn without
  antialiasing. All text fits the 320x200 screen.
- No randomness and no wall-clock time in game logic. The same actions always
  give the same state.
- All game rules live in `Adventure` (below), which has no nodes and loads
  `world.json`. Scenes only draw state and send actions to it, so every rule
  can be tested headless.

## Interfaces

Files:

- `res://scripts/adventure.gd`: `class_name Adventure extends RefCounted`,
  the whole game state and rules. Usable with `load(...).new()` without any
  autoload.
- `res://scripts/game.gd`: autoload `Game` (Node), holding one Adventure as
  `Game.adv` and emitting `signal changed` after every action.
- `res://data/world.json`: rooms, hotspots, items, NPC lines, trades, map.
- `res://main.tscn`: title screen ("ROSE CITY SIGNAL", assets/title.png,
  "New game", "Continue" when `user://save.json` exists), then the room view.
- `res://ui/room_view.tscn`: draws the current room's background, NPC sprites
  and hotspot highlights; has `@export var start_room := ""`. When it runs as
  the main scene with start_room set, it starts a new game in that room (for
  captures).
- `res://rooms/<room_id>.tscn`: one per room, each an instance of
  room_view.tscn with start_room set to its id.
- `res://ui/map_screen.tscn`: the travel map, drawn in code (below).
- `res://ui/dialogue_box.tscn`: NPC sprite at 2x on the left, text on the
  right, advances on click or `interact`.

Adventure API (exact names):

```gdscript
class_name Adventure extends RefCounted
func new_game(world_path := "res://data/world.json") -> void
func start_in(room_id: String) -> void            # new game, then move to room_id (captures only)
func current_room() -> String
func inventory() -> Array[String]                 # in the order items were received
func has_flag(name: String) -> bool
func hotspots() -> Array[String]                  # ids in the current room, in world.json order
func look(hotspot_id: String) -> String           # also accepts an item id in the inventory
func interact(hotspot_id: String) -> Dictionary   # {"ok": bool, "text": String}
func use_item(item_id: String, hotspot_id: String) -> Dictionary  # {"ok": bool, "text": String}
func travel(node_id: String) -> Dictionary        # {"ok": bool, "text": String}
func map_nodes() -> Array[Dictionary]             # [{"id", "label", "pos": [x, y], "open": bool}]
func is_won() -> bool
func to_dict() -> Dictionary                      # room, inventory, flags
func from_dict(d: Dictionary) -> void
func save_game(path := "user://save.json") -> void
func load_game(path := "user://save.json") -> bool
```

`ok` is false whenever nothing changed: then room, inventory and flags are
exactly as before, and `text` says why ("The guard shakes her head.").
Unknown ids give ok false, never an error.

Input actions (keyboard play, and how captures drive the game):
`next_hotspot` (Tab), `prev_hotspot` (Shift+Tab), `interact` (Enter, Space),
`look` (L), `next_item` (I: selects the next inventory item; the next
`interact` then uses it on the selected hotspot), `open_map` (M),
`cancel` (Escape: deselects the item, or closes the map or dialogue).
Mouse: left click on a hotspot walks there and interacts (or uses the
selected item); right click looks; left click on the floor walks.

Walking: June is a sprite sheet (assets/june.png, 3 frames: stand, step,
step) drawn at her feet position, flipped when walking left, moving at 60 px
per second within the room's `floor` band (a y range from world.json). An
action on a hotspot runs when she arrives at its `stand` point. Walking is
presentation only: Adventure never waits for it.

## World

`world.json` has: `"start": {"room": "pioneer_square", "inventory":
["courier_badge", "ration_chits", "empty_canteen"]}`, `"items"`, `"rooms"`,
`"map"`. Every hotspot has an id, a name (shown on hover), a `look` text, a
`rect` [x, y, w, h] in room coordinates (320x144), and a `stand` point.
NPCs also have `sprite` (res://assets/npcs/<id>.png) and three lines: `first`
(on first talk), `hint` (later talks until their trade is done) and `after`.
Write all texts in the tone above, one to three short sentences, using only
the Facts for anything real.

Rooms, in this order, and their hotspots, in this order (the first hotspot is
what `next_hotspot` selects first):

1. `pioneer_square` (map node downtown): `quartermaster` (NPC, Vee of The
   Heart), `brick_terraces` (look: the Square, "Portland's living room"),
   `to_bookstore` (exit), `to_doughnut_shop` (exit).
2. `bookstore`: `dark_stacks` (object), `store_sign` (look: Rowell's City of
   Books fills a whole city block), `to_square` (exit).
3. `doughnut_shop`: `doughnut_clerk` (NPC, Dot, The Heart), `pink_boxes`
   (object, look only), `to_square` (exit).
4. `mt_tabor`: `sentry` (NPC, Okafor of The Watch), `reservoir` (object),
   `lookout` (look: the cinder cone, 636 ft, open reservoirs; and the hint
   that the Council Crest tower's dish is fine but its tube rack is empty).
5. `rose_garden` (map node washington_park): `gardener` (NPC, Marisol of The
   Reclaimers), `rose_terraces` (look: the Rose Test Garden in Washington
   Park's 458 acres).
6. `hells_point` (map node foster): `bartender` (NPC, Lefty of The Point),
   `stage` (look: an empty stage; the real show is the cash box).
7. `reed_college`: `physicist` (NPC, Dr. Ames of The Academy),
   `reactor_door` (look: the TRIGA fact, including that it never made
   electricity and The Academy runs on rooftop solar; any item used on it:
   "Absolutely not.").
8. `tram_station` (map node south_waterfront): `clinic_guard` (NPC, Pruitt
   of The Clinic), `tram_panel` (object), `tram_car` (object; interacting
   rides up when allowed).
9. `marquam_hill`: `doctor` (NPC, Dr. Hale of The Clinic), `tram_car_down`
   (exit to tram_station), `to_council_crest` (exit; look: the Connor Trail
   into Marquam Nature Park, then the Marquam Trail up to Council Crest).
10. `council_crest`: `tower` (object), `view` (look: about 1,070 ft, one of
    the highest points in Portland), `to_marquam_hill` (exit).
11. `ending`: no hotspots. Its text and image show the market day at Pioneer
    Courthouse Square, with every faction there.

Items (id: name): `courier_badge` (Heart courier badge; look only, never
used up), `ration_chits`, `empty_canteen`, `flashlight`, `repair_manual`
(radio repair manual), `first_edition` (a first edition), `doughnut_box`,
`full_canteen` (canteen of reservoir water), `reclaimer_token`,
`rose_cutting`, `tram_fuse`, `charged_battery`, `transmitter_tubes`.

Rules. Each line is one action; "gives X" adds X to the inventory, "takes X"
removes it; everything not listed returns ok false and changes nothing.

- interact `quartermaster`: the first time, sets flag `briefed` and gives
  `flashlight` (her first line is the mission). Later: hint/after lines,
  ok true, no change.
- Any NPC interact: shows first/hint/after, ok true, changes nothing
  (except the quartermaster's first talk).
- use `flashlight` on `dark_stacks`: gives `repair_manual` and
  `first_edition`; sets `stacks_searched`. Keeps the flashlight. Again: ok
  false ("Nothing else down here but dust.").
- use `ration_chits` on `doughnut_clerk`: takes ration_chits, gives
  `doughnut_box`.
- use `doughnut_box` on `sentry`: takes it, sets `tabor_open`.
- use `empty_canteen` on `reservoir`: only with `tabor_open` (otherwise the
  sentry steps in front of the fence, ok false): takes empty_canteen, gives
  `full_canteen`.
- use `full_canteen` on `gardener`: takes it, gives `reclaimer_token` and
  `rose_cutting`.
- use `first_edition` on `bartender`: takes it, gives `tram_fuse`.
- use `rose_cutting` on `physicist`: takes it, gives `charged_battery`.
- use `reclaimer_token` on `clinic_guard`: sets `tram_allowed` and keeps the
  token (the guard only checks it).
- use `tram_fuse` on `tram_panel`: only with `tram_allowed` (otherwise the
  guard stops you): takes it, sets `tram_powered`.
- interact `tram_car`: with `tram_allowed` and `tram_powered`, moves to
  `marquam_hill` (ok true); otherwise ok false (no permission, or "the car
  is dead: the panel's fuse holder is empty").
- interact `doctor` while holding `repair_manual`: the first time, gives
  `transmitter_tubes` and sets `has_tubes` ("You can actually fix it? Take
  them."). Without the manual: hint line, no change.
- interact `tram_car_down`: moves to `tram_station`. interact
  `to_council_crest` / `to_marquam_hill`, and the downtown exits, move
  between their rooms.
- use `transmitter_tubes` on `tower`: takes them, sets `tubes_in`. Use
  `charged_battery` on `tower`: takes it, sets `battery_in`.
- use `repair_manual` on `tower`: with `tubes_in` and `battery_in`, sets
  `won` and moves to `ending`. Otherwise ok false and says what is still
  missing.
- travel: needs `briefed`, and the current room must not be `marquam_hill`
  or `council_crest` ("The only way down is the tram."). Open nodes and
  where they lead: downtown -> pioneer_square, washington_park ->
  rose_garden, south_waterfront -> tram_station, mt_tabor, reed_college,
  foster -> hells_point. marquam_hill, council_crest and st_johns are on the
  map but never open: their look texts say the tram is the only way up the
  hill, the trail starts at the Clinic, and the St. Johns Bridge (the 1931
  suspension bridge, contested north gate) is not on June's route today.

Nothing a player can do loses an item that's still needed: items are only
taken by the trade that needs them.

## Map

`map_screen.tscn` draws, in code, on assets/map_paper.png: the Columbia and
Willamette as 4 px polylines in #5B7A8C, I-205 as a dashed dark line
labelled "I-205 WALL", a 5x5 square in #B33A2E for each node (filled when
open, outlined when not), its label, and "THE BARRENS" east of I-205. Open nodes are clickable (keyboard: `next_hotspot` cycles
them, `interact` travels). Positions are in screen pixels (320x200; map area
x 4-233, the rest is a legend). They were generated by a script from the
map project's verified coordinates (equirectangular at 45.52 N, 13.26 px per
km); use these numbers exactly:

| node | label | pos |
|---|---|---|
| downtown | Downtown | [104, 120] |
| washington_park | Washington Park | [77, 119] |
| south_waterfront | South Waterfront | [112, 149] |
| marquam_hill | Marquam Hill | [97, 149] |
| council_crest | Council Crest | [74, 149] |
| mt_tabor | Mt. Tabor | [192, 130] |
| reed_college | Reed College | [154, 175] |
| foster | SE Foster | [177, 154] |
| st_johns | St. Johns Bridge | [16, 22] |

Lines (schematic, clipped to the map area):

- willamette: [[132,236],[118,200],[115,170],[114,139],[112,128],[114,114],[113,107],[107,99],[97,88],[56,59],[16,22],[10,-15]]
- columbia: [[10,-44],[62,-27],[103,-19],[144,-7],[186,3],[232,10]]
- i205: [[222,236],[223,177],[224,118],[224,59],[231,7]]

These go in `world.json` under `"map"` as `"nodes"` (id, label, pos, room or
null, look) and `"lines"`.

## Assets

Painted pixel art, VGA-adventure style. Backgrounds are rendered wide and
pixelated to the room view's 320x144. Characters and items have a magenta
background for chroma key. Composition notes in each prompt match the
hotspot positions (left, centre, right).

```json
[
  {"id": "bg_pioneer_square", "filename": "rooms/pioneer_square.png", "title": "Pioneer Courthouse Square",
   "prompt": "1990s point-and-click adventure game background, painted pixel art, a wide brick-paved city plaza with stepped brick terraces like an amphitheatre on the left, old downtown office buildings behind, a market stall with a canvas awning in the centre, two alley openings on the right, overcast evening, quiet post-apocalyptic city with weeds between the bricks, no people, no text",
   "negative_prompt": "text, letters, logo, watermark, people, photo, blurry, 3d render",
   "width": 1536, "height": 704, "seed": 5101, "pixelate": {"width": 320, "height": 144}, "palette": 32},
  {"id": "bg_bookstore", "filename": "rooms/bookstore.png", "title": "Rowell's City of Books",
   "prompt": "1990s point-and-click adventure game background, painted pixel art, the inside of an enormous old bookstore, tall wooden shelves crammed with books receding into darkness on the left, a hanging painted wayfinding sign in the centre, a doorway with daylight on the right, dust in the air, no people, no text",
   "negative_prompt": "text, letters, logo, watermark, people, photo, blurry, 3d render",
   "width": 1536, "height": 704, "seed": 5102, "pixelate": {"width": 320, "height": 144}, "palette": 32},
  {"id": "bg_doughnut_shop", "filename": "rooms/doughnut_shop.png", "title": "Hoodoo Doughnut",
   "prompt": "1990s point-and-click adventure game background, painted pixel art, a quirky small doughnut shop interior, a glass counter with trays of doughnuts on the left, a wall of stacked pink cardboard boxes in the centre, a street door on the right, candles and odd folk-art decorations, cosy, no people, no text",
   "negative_prompt": "text, letters, logo, watermark, people, photo, blurry, 3d render",
   "width": 1536, "height": 704, "seed": 5103, "pixelate": {"width": 320, "height": 144}, "palette": 32},
  {"id": "bg_mt_tabor", "filename": "rooms/mt_tabor.png", "title": "Mt. Tabor",
   "prompt": "1990s point-and-click adventure game background, painted pixel art, a forested hilltop park, a chain-link fence around a large open-air water reservoir with a stone gatehouse in the centre, a wooden lookout platform with a view over a distant city on the right, tall fir trees, late afternoon, no people, no text",
   "negative_prompt": "text, letters, logo, watermark, people, photo, blurry, 3d render",
   "width": 1536, "height": 704, "seed": 5104, "pixelate": {"width": 320, "height": 144}, "palette": 32},
  {"id": "bg_rose_garden", "filename": "rooms/rose_garden.png", "title": "Rose Test Garden",
   "prompt": "1990s point-and-click adventure game background, painted pixel art, terraced rose gardens on a hillside, overgrown rose beds in bloom, a gardener's tool shed and greenhouse on the left, stone steps down the terraces in the centre, a hazy city skyline below on the right, morning light, no people, no text",
   "negative_prompt": "text, letters, logo, watermark, people, photo, blurry, 3d render",
   "width": 1536, "height": 704, "seed": 5105, "pixelate": {"width": 320, "height": 144}, "palette": 32},
  {"id": "bg_hells_point", "filename": "rooms/hells_point.png", "title": "Hell's Point",
   "prompt": "1990s point-and-click adventure game background, painted pixel art, a dim rock-and-roll dive bar, a long bar with bottles and a cash register on the left, an empty small stage with a dark curtain and old amplifiers in the centre, a flickering red neon sign shaped like a devil's pitchfork on the right wall, no people, no text",
   "negative_prompt": "text, letters, logo, watermark, people, nudity, photo, blurry, 3d render",
   "width": 1536, "height": 704, "seed": 5106, "pixelate": {"width": 320, "height": 144}, "palette": 32},
  {"id": "bg_reed_college", "filename": "rooms/reed_college.png", "title": "Reed College",
   "prompt": "1990s point-and-click adventure game background, painted pixel art, a quiet college campus with brick gothic buildings, solar panels on the roofs, a heavy steel door with a radiation trefoil sign in the centre, a lawn with a workbench full of electronics on the left, ivy, overcast, no people, no text",
   "negative_prompt": "text, letters, logo, watermark, people, photo, blurry, 3d render",
   "width": 1536, "height": 704, "seed": 5107, "pixelate": {"width": 320, "height": 144}, "palette": 32},
  {"id": "bg_tram_station", "filename": "rooms/tram_station.png", "title": "Tram lower station",
   "prompt": "1990s point-and-click adventure game background, painted pixel art, the lower station of an aerial tram beside a river, a silver cable car parked in the station on the right, a control panel with an open fuse box on the left, cables rising steeply toward a hospital on a wooded hill in the background, sandbags, overcast, no people, no text",
   "negative_prompt": "text, letters, logo, watermark, people, photo, blurry, 3d render",
   "width": 1536, "height": 704, "seed": 5108, "pixelate": {"width": 320, "height": 144}, "palette": 32},
  {"id": "bg_marquam_hill", "filename": "rooms/marquam_hill.png", "title": "Marquam Hill",
   "prompt": "1990s point-and-click adventure game background, painted pixel art, the upper station of an aerial tram on a hospital campus on a hilltop, a silver cable car at the platform on the left, a field-hospital tent with red crosses in the centre, a forest trail entrance between tall firs on the right, misty, no people, no text",
   "negative_prompt": "text, letters, logo, watermark, people, photo, blurry, 3d render",
   "width": 1536, "height": 704, "seed": 5109, "pixelate": {"width": 320, "height": 144}, "palette": 32},
  {"id": "bg_council_crest", "filename": "rooms/council_crest.png", "title": "Council Crest",
   "prompt": "1990s point-and-click adventure game background, painted pixel art, a grassy hilltop summit, a very tall red and white lattice radio tower with an equipment cabinet at its base in the centre, a wide view over a misty river valley and distant snowy volcano on the right, a forest trail on the left, golden hour, no people, no text",
   "negative_prompt": "text, letters, logo, watermark, people, photo, blurry, 3d render",
   "width": 1536, "height": 704, "seed": 5110, "pixelate": {"width": 320, "height": 144}, "palette": 32},
  {"id": "bg_ending", "filename": "rooms/ending.png", "title": "Market day",
   "prompt": "1990s point-and-click adventure game background, painted pixel art, a festive market day in a brick-paved city plaza with stepped terraces, many small figures trading at stalls with colourful awnings, strings of lights, a radio on a crate, warm sunset, hopeful mood, no text",
   "negative_prompt": "text, letters, logo, watermark, photo, blurry, 3d render",
   "width": 1536, "height": 704, "seed": 5111, "pixelate": {"width": 320, "height": 144}, "palette": 32},

  {"id": "title", "filename": "title.png", "title": "Title screen",
   "prompt": "1990s adventure game title screen art, painted pixel art, a lone bike courier on a hill at dusk looking over a ruined river city with bridges, a tall radio tower on a far hill with a blinking light, roses growing through cracked pavement in the foreground, empty sky at the top for a title, no text",
   "negative_prompt": "text, letters, logo, watermark, photo, blurry, 3d render",
   "width": 1536, "height": 960, "seed": 5120, "pixelate": {"width": 320, "height": 200}, "palette": 32},
  {"id": "map_paper", "filename": "map_paper.png", "title": "Map paper",
   "prompt": "a blank sheet of old weathered parchment paper, stains and creases, flat, top-down, evenly lit, nothing drawn on it, no text",
   "negative_prompt": "text, letters, map, drawing, lines, watermark",
   "width": 1536, "height": 960, "seed": 5121, "pixelate": {"width": 320, "height": 200}, "palette": 16},

  {"id": "june", "filename": "june.png", "title": "June sprite sheet",
   "prompt": "full body pixel art game character, side view facing right, a young woman bike courier with a messenger bag, cycling cap, rolled-up sleeves, sturdy boots, flat colours, plain magenta background",
   "negative_prompt": "text, watermark, photo, blurry, multiple characters, background scenery",
   "width": 512, "height": 1024, "seed": 5130, "chroma_key": "#ff00ff", "chroma_tolerance": 60,
   "pixelate": {"width": 24, "height": 48}, "palette": 16,
   "frames": ["standing", "walking, left foot forward", "walking, right foot forward"], "sheet_columns": 3},

  {"id": "npc_quartermaster", "filename": "npcs/quartermaster.png", "title": "Vee",
   "prompt": "full body pixel art game character, front view, a middle-aged woman quartermaster with a clipboard, patched canvas apron, reading glasses on a cord, flat colours, plain magenta background",
   "negative_prompt": "text, watermark, photo, blurry, multiple characters, background scenery",
   "width": 512, "height": 1024, "seed": 5141, "chroma_key": "#ff00ff", "chroma_tolerance": 60, "pixelate": {"width": 32, "height": 64}, "palette": 16},
  {"id": "npc_doughnut_clerk", "filename": "npcs/doughnut_clerk.png", "title": "Dot",
   "prompt": "full body pixel art game character, front view, a cheerful young doughnut shop clerk with pink hair, an apron dusted with sugar, holding a pink box, flat colours, plain magenta background",
   "negative_prompt": "text, watermark, photo, blurry, multiple characters, background scenery",
   "width": 512, "height": 1024, "seed": 5142, "chroma_key": "#ff00ff", "chroma_tolerance": 60, "pixelate": {"width": 32, "height": 64}, "palette": 16},
  {"id": "npc_sentry", "filename": "npcs/sentry.png", "title": "Okafor",
   "prompt": "full body pixel art game character, front view, a tall calm lookout sentry in a long green raincoat with binoculars around his neck and a walking staff, flat colours, plain magenta background",
   "negative_prompt": "text, watermark, photo, blurry, multiple characters, background scenery, gun",
   "width": 512, "height": 1024, "seed": 5143, "chroma_key": "#ff00ff", "chroma_tolerance": 60, "pixelate": {"width": 32, "height": 64}, "palette": 16},
  {"id": "npc_gardener", "filename": "npcs/gardener.png", "title": "Marisol",
   "prompt": "full body pixel art game character, front view, an older woman gardener in a wide straw hat and work gloves holding pruning shears, a rose pinned to her overalls, flat colours, plain magenta background",
   "negative_prompt": "text, watermark, photo, blurry, multiple characters, background scenery",
   "width": 512, "height": 1024, "seed": 5144, "chroma_key": "#ff00ff", "chroma_tolerance": 60, "pixelate": {"width": 32, "height": 64}, "palette": 16},
  {"id": "npc_bartender", "filename": "npcs/bartender.png", "title": "Lefty",
   "prompt": "full body pixel art game character, front view, a grizzled rock-and-roll bartender with a grey pompadour, a leather vest over a band t-shirt, polishing a glass, flat colours, plain magenta background",
   "negative_prompt": "text, watermark, photo, blurry, multiple characters, background scenery",
   "width": 512, "height": 1024, "seed": 5145, "chroma_key": "#ff00ff", "chroma_tolerance": 60, "pixelate": {"width": 32, "height": 64}, "palette": 16},
  {"id": "npc_physicist", "filename": "npcs/physicist.png", "title": "Dr. Ames",
   "prompt": "full body pixel art game character, front view, a young physicist in a cardigan and safety glasses pushed up on her forehead, holding a multimeter, flat colours, plain magenta background",
   "negative_prompt": "text, watermark, photo, blurry, multiple characters, background scenery",
   "width": 512, "height": 1024, "seed": 5146, "chroma_key": "#ff00ff", "chroma_tolerance": 60, "pixelate": {"width": 32, "height": 64}, "palette": 16},
  {"id": "npc_clinic_guard", "filename": "npcs/clinic_guard.png", "title": "Pruitt",
   "prompt": "full body pixel art game character, front view, a stern woman guard in a white armband with a red cross, a raincoat and a clipboard, arms folded, flat colours, plain magenta background",
   "negative_prompt": "text, watermark, photo, blurry, multiple characters, background scenery, gun",
   "width": 512, "height": 1024, "seed": 5147, "chroma_key": "#ff00ff", "chroma_tolerance": 60, "pixelate": {"width": 32, "height": 64}, "palette": 16},
  {"id": "npc_doctor", "filename": "npcs/doctor.png", "title": "Dr. Hale",
   "prompt": "full body pixel art game character, front view, a tired older doctor in a stained white coat with a stethoscope, holding a small box of glass radio tubes, flat colours, plain magenta background",
   "negative_prompt": "text, watermark, photo, blurry, multiple characters, background scenery",
   "width": 512, "height": 1024, "seed": 5148, "chroma_key": "#ff00ff", "chroma_tolerance": 60, "pixelate": {"width": 32, "height": 64}, "palette": 16},

  {"id": "item_courier_badge", "filename": "items/courier_badge.png", "prompt": "pixel art game inventory icon, a round tin badge with a bicycle wheel and a rose, flat colours, plain magenta background", "width": 512, "height": 512, "seed": 5161, "chroma_key": "#ff00ff", "chroma_tolerance": 60, "pixelate": {"width": 24, "height": 24}, "palette": 16},
  {"id": "item_ration_chits", "filename": "items/ration_chits.png", "prompt": "pixel art game inventory icon, a small bundle of paper ration tickets tied with string, flat colours, plain magenta background", "width": 512, "height": 512, "seed": 5162, "chroma_key": "#ff00ff", "chroma_tolerance": 60, "pixelate": {"width": 24, "height": 24}, "palette": 16},
  {"id": "item_empty_canteen", "filename": "items/empty_canteen.png", "prompt": "pixel art game inventory icon, a dented empty metal canteen with an open cap, flat colours, plain magenta background", "width": 512, "height": 512, "seed": 5163, "chroma_key": "#ff00ff", "chroma_tolerance": 60, "pixelate": {"width": 24, "height": 24}, "palette": 16},
  {"id": "item_flashlight", "filename": "items/flashlight.png", "prompt": "pixel art game inventory icon, an old yellow flashlight, flat colours, plain magenta background", "width": 512, "height": 512, "seed": 5164, "chroma_key": "#ff00ff", "chroma_tolerance": 60, "pixelate": {"width": 24, "height": 24}, "palette": 16},
  {"id": "item_repair_manual", "filename": "items/repair_manual.png", "prompt": "pixel art game inventory icon, a thick grey technical manual with a radio tower drawn on the cover, flat colours, plain magenta background", "width": 512, "height": 512, "seed": 5165, "chroma_key": "#ff00ff", "chroma_tolerance": 60, "pixelate": {"width": 24, "height": 24}, "palette": 16},
  {"id": "item_first_edition", "filename": "items/first_edition.png", "prompt": "pixel art game inventory icon, an antique leather-bound book with gold edges, flat colours, plain magenta background", "width": 512, "height": 512, "seed": 5166, "chroma_key": "#ff00ff", "chroma_tolerance": 60, "pixelate": {"width": 24, "height": 24}, "palette": 16},
  {"id": "item_doughnut_box", "filename": "items/doughnut_box.png", "prompt": "pixel art game inventory icon, a closed pink cardboard doughnut box, flat colours, plain magenta background", "width": 512, "height": 512, "seed": 5167, "chroma_key": "#ff00ff", "chroma_tolerance": 60, "pixelate": {"width": 24, "height": 24}, "palette": 16},
  {"id": "item_full_canteen", "filename": "items/full_canteen.png", "prompt": "pixel art game inventory icon, a dented metal canteen with the cap on and water droplets, flat colours, plain magenta background", "width": 512, "height": 512, "seed": 5168, "chroma_key": "#ff00ff", "chroma_tolerance": 60, "pixelate": {"width": 24, "height": 24}, "palette": 16},
  {"id": "item_reclaimer_token", "filename": "items/reclaimer_token.png", "prompt": "pixel art game inventory icon, a carved wooden token with a leaf symbol on a cord, flat colours, plain magenta background", "width": 512, "height": 512, "seed": 5169, "chroma_key": "#ff00ff", "chroma_tolerance": 60, "pixelate": {"width": 24, "height": 24}, "palette": 16},
  {"id": "item_rose_cutting", "filename": "items/rose_cutting.png", "prompt": "pixel art game inventory icon, a rose cutting with one red bloom wrapped in damp cloth, flat colours, plain magenta background", "width": 512, "height": 512, "seed": 5170, "chroma_key": "#ff00ff", "chroma_tolerance": 60, "pixelate": {"width": 24, "height": 24}, "palette": 16},
  {"id": "item_tram_fuse", "filename": "items/tram_fuse.png", "prompt": "pixel art game inventory icon, a large industrial cartridge fuse with brass ends, flat colours, plain magenta background", "width": 512, "height": 512, "seed": 5171, "chroma_key": "#ff00ff", "chroma_tolerance": 60, "pixelate": {"width": 24, "height": 24}, "palette": 16},
  {"id": "item_charged_battery", "filename": "items/charged_battery.png", "prompt": "pixel art game inventory icon, a chunky battery pack with a green charge light, flat colours, plain magenta background", "width": 512, "height": 512, "seed": 5172, "chroma_key": "#ff00ff", "chroma_tolerance": 60, "pixelate": {"width": 24, "height": 24}, "palette": 16},
  {"id": "item_transmitter_tubes", "filename": "items/transmitter_tubes.png", "prompt": "pixel art game inventory icon, three glowing glass vacuum tubes in a small wooden crate, flat colours, plain magenta background", "width": 512, "height": 512, "seed": 5173, "chroma_key": "#ff00ff", "chroma_tolerance": 60, "pixelate": {"width": 24, "height": 24}, "palette": 16}
]
```

## Captures

```json
[
  {"name": "title", "scene": "res://main.tscn", "frames": 30, "fps": 30, "keep": [29]},
  {"name": "square", "scene": "res://rooms/pioneer_square.tscn", "frames": 30, "fps": 30, "keep": [29]},
  {"name": "briefing", "scene": "res://rooms/pioneer_square.tscn", "frames": 60, "fps": 30,
   "inputs": [{"frame": 10, "action": "next_hotspot", "pressed": true},
              {"frame": 11, "action": "next_hotspot", "pressed": false},
              {"frame": 20, "action": "interact", "pressed": true},
              {"frame": 21, "action": "interact", "pressed": false}],
   "keep": [59]},
  {"name": "map", "scene": "res://ui/map_screen.tscn", "frames": 30, "fps": 30, "keep": [29]},
  {"name": "tabor", "scene": "res://rooms/mt_tabor.tscn", "frames": 30, "fps": 30, "keep": [29]},
  {"name": "tram", "scene": "res://rooms/tram_station.tscn", "frames": 30, "fps": 30, "keep": [29]},
  {"name": "crest", "scene": "res://rooms/council_crest.tscn", "frames": 30, "fps": 30, "keep": [29]}
]
```

## Acceptance

Logic runs headless through `game.godot_eval` on `load("res://scripts/adventure.gd").new()`
after `new_game()`. Scenes run through `game.godot_frames`.

1. `game.godot_import()` reports no script errors; project.godot has the
   viewport, window override, stretch mode, integer scale mode, nearest
   filter, main scene and title from Constraints.
2. world.json is consistent: the 11 rooms and 13 items above, exactly; every
   hotspot id is unique within its room; every exit leads to a room that
   exists; every item a rule names exists; every room except `ending` has a
   `floor` band and every hotspot a rect inside 320x144 and a stand point
   inside the floor band; every image named (rooms, NPC sprites, item icons,
   june.png, title.png, map_paper.png) exists under assets/.
3. New game: room `pioneer_square`, inventory exactly `["courier_badge",
   "ration_chits", "empty_canteen"]`, no flags. `travel("mt_tabor")` is ok
   false and changes nothing until the quartermaster is talked to.
4. The walkthrough wins. Each step is ok true, and the inventory after the
   step is as given:
   1. interact quartermaster -> + flashlight, flag briefed
   2. interact to_bookstore; use flashlight on dark_stacks -> + repair_manual, first_edition
   3. interact to_square; interact to_doughnut_shop; use ration_chits on doughnut_clerk -> ration_chits replaced by doughnut_box
   4. travel mt_tabor; use doughnut_box on sentry; use empty_canteen on reservoir -> full_canteen
   5. travel washington_park; use full_canteen on gardener -> + reclaimer_token, rose_cutting
   6. travel foster; use first_edition on bartender -> + tram_fuse
   7. travel reed_college; use rose_cutting on physicist -> + charged_battery
   8. travel south_waterfront; use reclaimer_token on clinic_guard; use tram_fuse on tram_panel; interact tram_car -> room marquam_hill
   9. interact doctor -> + transmitter_tubes
   10. interact to_council_crest; use transmitter_tubes on tower; use charged_battery on tower; use repair_manual on tower -> `is_won()`, room `ending`

   At the end the inventory is exactly `["courier_badge", "flashlight",
   "repair_manual", "reclaimer_token"]`.
5. Gates (each ok false, room, inventory and flags unchanged): use
   empty_canteen on reservoir before the sentry has the doughnuts; interact
   tram_car before tram_allowed; use tram_fuse on tram_panel before
   tram_allowed; interact tram_car with tram_allowed but no fuse in the
   panel; travel from marquam_hill and from council_crest; use repair_manual
   on tower with only the tubes in; use flashlight on dark_stacks a second
   time; travel to marquam_hill, council_crest or st_johns from anywhere;
   use doughnut_box on reactor_door. Also: interact doctor without
   repair_manual is ok true but gives nothing and sets no flag.
6. Steps the story doesn't order can be done in another order: steps 1, 2,
   then 6 (Hell's Point before Mt. Tabor), then "travel downtown; interact
   to_doughnut_shop; use ration_chits on doughnut_clerk", then 4, 5, 7, 8,
   9, 10 also wins.
7. No dead ends: a breadth-first search from a new game over every action
   (interact and look on each hotspot of the current room, use_item of each
   inventory item on each hotspot, travel to each map node), keyed on
   `to_dict()`, finds at most 5,000 states, and from every state it finds,
   `won` is reachable. The search runs inside one `game.godot_eval` call.
8. Save and load: after walkthrough step 5, `save_game(<tmp path>)`, then a
   new Adventure's `load_game(<tmp path>)` gives an equal `to_dict()`, and
   finishing steps 6-10 from it wins.
9. `map_nodes()` returns the 9 nodes with exactly the labels and positions in
   the Map table; before briefing all are closed; after briefing exactly
   downtown, washington_park, south_waterfront, mt_tabor, reed_college and
   foster are open.
10. Facts in look texts: mt_tabor `lookout` contains "636"; rose_garden
    `rose_terraces` contains "458"; council_crest `view` contains "1,070";
    reed_college `reactor_door` contains "TRIGA"; pioneer_square
    `brick_terraces` contains "living room"; marquam_hill `to_council_crest`
    contains "Marquam Trail"; in world.json, map node st_johns's `look`
    contains "1931" and "suspension".
11. None of the "Never use" strings appears anywhere in world.json or in any
    .gd or .tscn file (case-insensitive): "Teton", "Titty City", "Queen
    Anne", "643", "907", "Portland Zoo", "1916", "bascule", "West Hills
    Park", "Rock Bottom", "old growth", "old-growth", "523 SE Clay", "120 NW
    10th", "Powell's", "Voodoo", "Devils Point", "Devil's Point".
12. Every room scene `res://rooms/<id>.tscn` (all 11) runs 30 frames with no
    script errors, and its last frame is not blank (`game.is_blank`).
13. Capture checks: the "briefing" capture's last frame differs from the
    "square" capture's last frame by more than 2% of pixels (`game.diff`):
    the dialogue box is open. The "map" capture is not blank and has pixels
    of both the river colour #5B7A8C and the node colour #B33A2E
    (`game.count_color`, tolerance 30).

## Playtest

What to check on Vulcan (`forge playtest rose-city-signal`):

- The game plays start to finish with the mouse alone, and again with the
  keyboard alone (Tab, Enter, I, M, Esc).
- Hotspot rectangles line up with what the backgrounds show (the reservoir,
  the tram panel, the tower). Note any room where they don't.
- Text never runs off the screen or under the inventory bar.
- June walks on the floor, not through walls or across the sky.
- The map reads as Portland: the Willamette between Downtown and the east
  side, Mt. Tabor and Reed to the east and south-east, Council Crest west of
  the tram.
- The ending feels earned: every faction you met is named in it.
