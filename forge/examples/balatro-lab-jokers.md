# balatro-lab-jokers

Toolchain: lua-love
License: GPL-3.0
Prior art: auto
Assets dir: assets

<!--
A Balatro mod for Steamodded: five new Jokers themed on a home AI lab, with
their own pixel art. Balatro runs on LOVE 11 with LuaJIT, so the mod is plain
Lua 5.1. Nothing here needs the game's files: the tests load the mod into a
small stand-in for Steamodded and the game (tests/harness.lua) and run each
Joker's logic against hand-built contexts.

License: GPL-3.0 matches Steamodded and most Balatro mods, and lets forge's
GitHub search use Steamodded's own code and examples as references.
-->

## Goal

A Steamodded mod, "Lab Jokers", that adds five Jokers to Balatro. Each Joker has
a small, exact rule (below), its own card art at 1x and 2x, and a description
that shows its current values. The mod folder is the project root: copying it
into Balatro's `Mods` folder installs it.

Use what already exists on GitHub:
- Steamodded (github.com/Steamodded/smods, GPL-3.0) is the API. Follow its
  documented `SMODS.Joker`, `SMODS.Atlas` and mod-metadata formats, and its
  example mods, as references. Steamodded and Lovely are installed separately
  by the player: never vendor either into the mod.
- For the test harness's JSON output, vendor a small, well-tested Lua JSON
  library (for example rxi/json.lua, MIT) into `tests/` rather than writing
  one.

## Constraints

- Lua 5.1 as LuaJIT runs it: no `//`, no bitwise operators, no `utf8`
  library, no integer/float distinction. Every `.lua` file must load with
  `luajit`.
- No global names other than what Steamodded and the game define. All the
  mod's own helpers are `local` or live in one table `LABJ`.
- No randomness: every Joker is deterministic.
- Joker keys are written without the prefix (`key = "overclock"`);
  Steamodded makes them `j_labj_overclock`. The atlas keys are the same words.
- Values a Joker changes live in `card.ability.extra`, never in the
  definition (two copies of a Joker must not share their counters).
- Scaling Jokers do not grow when called with `context.blueprint` set.

## Interfaces

Files (paths from the mod root):

- `lab_jokers.json`: Steamodded mod metadata: `"id": "lab_jokers"`,
  `"name": "Lab Jokers"`, `"author": ["Paul"]`, `"description"`,
  `"prefix": "labj"`, `"main_file": "main.lua"`, `"version": "1.0.0"`,
  `"badge_colour": "3FC7EB"`, `"dependencies": ["Steamodded (>=1.0.0~BETA)"]`.
- `main.lua`: registers the five atlases and loads each Joker with
  `assert(SMODS.load_file("jokers/<name>.lua"))()`.
- `jokers/overclock.lua`, `jokers/cache_hit.lua`, `jokers/thermal_throttle.lua`,
  `jokers/power_saver.lua`, `jokers/garbage_collector.lua`: one
  `SMODS.Joker{...}` each.
- Atlases: `SMODS.Atlas{ key = "<name>", path = "j_<name>.png", px = 71, py = 95 }`
  per Joker; the images are `assets/1x/j_<name>.png` (71x95) and
  `assets/2x/j_<name>.png` (142x190). Each Joker uses `atlas = "<name>"`,
  `pos = { x = 0, y = 0 }`.
- Each Joker defines `loc_txt = { name = ..., text = {...} }`, `config = { extra = {...} }`,
  `rarity`, `cost`, `blueprint_compat`, `loc_vars(self, info_queue, card)`
  returning `{ vars = {...} }`, and `calculate(self, card, context)` (Power
  Saver: `calc_dollar_bonus(self, card)` instead).
- `tests/harness.lua`: a stand-in for Steamodded and the game, used only by
  tests. `local H = dofile("tests/harness.lua")` gives:
  - `H.load_mod()`: sets up the stand-ins, runs `main.lua` from the mod root
    and returns `{ jokers = {[full_key] = definition}, atlases = {[key] = definition} }`.
    `SMODS.Joker{...}` records `prefix .. "_" .. key` with `j_` in front
    (`j_labj_overclock`); `SMODS.Atlas{...}` records its key; `SMODS.load_file(path)`
    returns `loadfile(path)` relative to the mod root.
  - `H.card(rank, suit)`: a playing card. `rank` is 2..14 (11 J, 12 Q, 13 K,
    14 A); `suit` is "Spades", "Hearts", "Clubs" or "Diamonds".
    `card:get_id()` returns the rank, `card:is_suit(s)` compares the suit,
    `card:is_face()` is true for 11, 12 and 13.
  - `H.joker(full_key)`: a Joker card: `{ ability = { extra = <deep copy of config.extra> },
    config = { center = <definition> } }`.
  - `H.calc(joker, context)`: calls the definition's `calculate(def, joker, context)`
    and returns its result (nil when it returns nothing).
  - `H.dollars(joker)`: calls `calc_dollar_bonus(def, joker)`.
  - `H.vars(joker)`: `loc_vars(def, {}, joker).vars`.
  - `H.G`: the game stand-in, also set as the global `G`: `G.play`,
    `G.hand`, `G.jokers` (distinct tables, used as `context.cardarea`),
    `G.GAME.current_round.hands_left`, `G.GAME.current_round.discards_left`,
    and `G.C` colours (`MULT`, `CHIPS`, `MONEY`, `FILTER`, `RED`, `GREEN`).
  - `H.json(value)`: the value as JSON text (through the vendored library),
    so a Python test can run a Lua script and read what it printed.

## The Jokers

1. **Overclock** (`overclock`): rarity 1 (Common), cost 4, blueprint_compat true.
   `config.extra = { mult = 3 }`. When a played card is scored
   (`context.individual and context.cardarea == G.play`) and its rank is
   2, 3, 4 or 5: return `{ mult = card.ability.extra.mult }`.
   Text: "Each played {C:attention}2{}, {C:attention}3{}, {C:attention}4{} or {C:attention}5{}"
   / "gives {C:mult}+#1#{} Mult when scored". vars: `{ mult }`.
2. **Cache Hit** (`cache_hit`): rarity 2 (Uncommon), cost 6, blueprint_compat true.
   `config.extra = { chips = 0, chip_gain = 8 }`. In `context.before`, if the
   played hand contains a Pair (`next(context.poker_hands["Pair"])`) and not
   `context.blueprint`: chips += chip_gain and return
   `{ message = "Upgrade!", colour = G.C.CHIPS }`. In `context.joker_main`,
   when chips > 0: return `{ chips = card.ability.extra.chips }`.
   Text: "Gains {C:chips}+#1#{} Chips if played"
   / "hand contains a {C:attention}Pair{}" / "{C:inactive}(Currently {C:chips}+#2#{C:inactive} Chips)".
   vars: `{ chip_gain, chips }`.
3. **Thermal Throttle** (`thermal_throttle`): rarity 2, cost 6, blueprint_compat true.
   `config.extra = { xmult = 2 }`. In `context.joker_main`, when
   `G.GAME.current_round.hands_left == 0` (this is the round's last hand):
   return `{ xmult = card.ability.extra.xmult }`.
   Text: "{X:mult,C:white} X#1# {} Mult on the" / "{C:attention}final hand{} of the round".
   vars: `{ xmult }`.
4. **Power Saver** (`power_saver`): rarity 1, cost 5, blueprint_compat false.
   `config.extra = { dollars = 1 }`. `calc_dollar_bonus`: with
   `G.GAME.current_round.discards_left` = n > 0, return n * dollars; with
   n = 0 return nil. No `calculate` effect.
   Text: "Earn {C:money}$#1#{} at end of round" / "per {C:attention}discard{} left".
   vars: `{ dollars }`.
5. **Garbage Collector** (`garbage_collector`): rarity 3 (Rare), cost 7, blueprint_compat true.
   `config.extra = { xmult = 1, xmult_gain = 0.1 }`. In `context.discard`,
   when `context.other_card:is_face()` and not `context.blueprint`:
   xmult += xmult_gain and return `{ message = "Upgrade!", colour = G.C.MULT }`.
   In `context.joker_main`, when xmult > 1: return `{ xmult = card.ability.extra.xmult }`.
   Text: "Gains {X:mult,C:white} X#1# {} Mult for each" / "discarded {C:attention}face{} card"
   / "{C:inactive}(Currently {X:mult,C:white} X#2# {C:inactive} Mult)".
   vars: `{ xmult_gain, xmult }`.

## Assets

Card art for each Joker at Balatro's sizes. Each 1x/2x pair uses the same
prompt and seed, so both are the same picture. The art fills the whole card,
with a thin light border drawn into it, like the game's own Jokers.

```json
[
  {"id": "a_overclock_1x", "filename": "1x/j_overclock.png", "title": "Overclock 1x",
   "prompt": "pixel art character portrait for a vertical game card, framed by a thin cream border, showing a grinning jester made of a glowing computer CPU with lightning bolts, bold flat colours, thick dark outlines, art filling the whole frame, no text",
   "negative_prompt": "text, letters, numbers, playing card, corner index, suit symbol, pip, ace, white background, watermark, photo, blurry",
   "width": 768, "height": 1024, "seed": 4101, "pixelate": {"width": 71, "height": 95}, "palette": 24},
  {"id": "a_overclock_2x", "filename": "2x/j_overclock.png", "title": "Overclock 2x",
   "prompt": "pixel art character portrait for a vertical game card, framed by a thin cream border, showing a grinning jester made of a glowing computer CPU with lightning bolts, bold flat colours, thick dark outlines, art filling the whole frame, no text",
   "negative_prompt": "text, letters, numbers, playing card, corner index, suit symbol, pip, ace, white background, watermark, photo, blurry",
   "width": 768, "height": 1024, "seed": 4101, "pixelate": {"width": 71, "height": 95}, "palette": 24, "scale": 2},

  {"id": "a_cache_hit_1x", "filename": "1x/j_cache_hit.png", "title": "Cache Hit 1x",
   "prompt": "pixel art character portrait for a vertical game card, framed by a thin cream border, showing a cheerful jester juggling glowing blue memory chips, bold flat colours, thick dark outlines, art filling the whole frame, no text",
   "negative_prompt": "text, letters, numbers, playing card, corner index, suit symbol, pip, ace, white background, watermark, photo, blurry",
   "width": 768, "height": 1024, "seed": 4102, "pixelate": {"width": 71, "height": 95}, "palette": 24},
  {"id": "a_cache_hit_2x", "filename": "2x/j_cache_hit.png", "title": "Cache Hit 2x",
   "prompt": "pixel art character portrait for a vertical game card, framed by a thin cream border, showing a cheerful jester juggling glowing blue memory chips, bold flat colours, thick dark outlines, art filling the whole frame, no text",
   "negative_prompt": "text, letters, numbers, playing card, corner index, suit symbol, pip, ace, white background, watermark, photo, blurry",
   "width": 768, "height": 1024, "seed": 4102, "pixelate": {"width": 71, "height": 95}, "palette": 24, "scale": 2},

  {"id": "a_thermal_throttle_1x", "filename": "1x/j_thermal_throttle.png", "title": "Thermal Throttle 1x",
   "prompt": "pixel art character portrait for a vertical game card, framed by a thin cream border, showing a sweating red-faced jester fanning an overheating graphics card with flames, bold flat colours, thick dark outlines, art filling the whole frame, no text",
   "negative_prompt": "text, letters, numbers, playing card, corner index, suit symbol, pip, ace, white background, watermark, photo, blurry",
   "width": 768, "height": 1024, "seed": 4103, "pixelate": {"width": 71, "height": 95}, "palette": 24},
  {"id": "a_thermal_throttle_2x", "filename": "2x/j_thermal_throttle.png", "title": "Thermal Throttle 2x",
   "prompt": "pixel art character portrait for a vertical game card, framed by a thin cream border, showing a sweating red-faced jester fanning an overheating graphics card with flames, bold flat colours, thick dark outlines, art filling the whole frame, no text",
   "negative_prompt": "text, letters, numbers, playing card, corner index, suit symbol, pip, ace, white background, watermark, photo, blurry",
   "width": 768, "height": 1024, "seed": 4103, "pixelate": {"width": 71, "height": 95}, "palette": 24, "scale": 2},

  {"id": "a_power_saver_1x", "filename": "1x/j_power_saver.png", "title": "Power Saver 1x",
   "prompt": "pixel art character portrait for a vertical game card, framed by a thin cream border, showing a sleepy jester in a nightcap hugging a green power button and a gold coin, bold flat colours, thick dark outlines, art filling the whole frame, no text",
   "negative_prompt": "text, letters, numbers, playing card, corner index, suit symbol, pip, ace, white background, watermark, photo, blurry",
   "width": 768, "height": 1024, "seed": 4104, "pixelate": {"width": 71, "height": 95}, "palette": 24},
  {"id": "a_power_saver_2x", "filename": "2x/j_power_saver.png", "title": "Power Saver 2x",
   "prompt": "pixel art character portrait for a vertical game card, framed by a thin cream border, showing a sleepy jester in a nightcap hugging a green power button and a gold coin, bold flat colours, thick dark outlines, art filling the whole frame, no text",
   "negative_prompt": "text, letters, numbers, playing card, corner index, suit symbol, pip, ace, white background, watermark, photo, blurry",
   "width": 768, "height": 1024, "seed": 4104, "pixelate": {"width": 71, "height": 95}, "palette": 24, "scale": 2},

  {"id": "a_garbage_collector_1x", "filename": "1x/j_garbage_collector.png", "title": "Garbage Collector 1x",
   "prompt": "pixel art character portrait for a vertical game card, framed by a thin cream border, showing a jester sweeping crumpled face cards into a recycling bin, bold flat colours, thick dark outlines, art filling the whole frame, no text",
   "negative_prompt": "text, letters, numbers, playing card, corner index, suit symbol, pip, ace, white background, watermark, photo, blurry",
   "width": 768, "height": 1024, "seed": 4105, "pixelate": {"width": 71, "height": 95}, "palette": 24},
  {"id": "a_garbage_collector_2x", "filename": "2x/j_garbage_collector.png", "title": "Garbage Collector 2x",
   "prompt": "pixel art character portrait for a vertical game card, framed by a thin cream border, showing a jester sweeping crumpled face cards into a recycling bin, bold flat colours, thick dark outlines, art filling the whole frame, no text",
   "negative_prompt": "text, letters, numbers, playing card, corner index, suit symbol, pip, ace, white background, watermark, photo, blurry",
   "width": 768, "height": 1024, "seed": 4105, "pixelate": {"width": 71, "height": 95}, "palette": 24, "scale": 2}
]
```

## Preview tool

`tools/preview/main.lua` and `tools/preview/conf.lua`: a small LOVE 11 app
(window 800x300, title "Lab Jokers preview") that draws the five 2x Joker
images side by side on a dark green background (#2B4A3A), each with its name
underneath in white. It reads the PNGs from `../../assets/2x/` relative to
its own folder with `io.open` (LOVE's filesystem cannot read outside the app)
and turns the bytes into images with `love.filesystem.newFileData` and
`love.graphics.newImage`. It is for looking at the art; Steamodded ignores it.

## Captures

```json
[
  {"name": "preview", "cmd": ["love", "{project}/tools/preview"], "at": [2.5], "size": [800, 300]}
]
```

## Acceptance

Every Lua check runs under `luajit` through `tests/harness.lua`; values come
back to Python as JSON from `H.json`.

1. Every `.lua` file in the project (outside `tests/` vendored code) compiles
   with `luajit -bl` (or `luajit -e "assert(loadfile(...))"`), and none uses `//`.
2. `lab_jokers.json` parses as JSON and has id `lab_jokers`, prefix `labj`,
   main_file `main.lua`, an author list, a description, version `1.0.0`, and a
   dependency starting with `Steamodded`.
3. `H.load_mod()` registers exactly the jokers `j_labj_overclock`,
   `j_labj_cache_hit`, `j_labj_thermal_throttle`, `j_labj_power_saver`,
   `j_labj_garbage_collector`, and exactly the atlases `overclock`,
   `cache_hit`, `thermal_throttle`, `power_saver`, `garbage_collector`, each
   with px 71, py 95 and path `j_<name>.png`; each joker's `atlas` names an
   atlas that exists.
4. Rarity, cost and blueprint_compat are exactly as listed for all five
   (Overclock 1/4/true, Cache Hit 2/6/true, Thermal Throttle 2/6/true,
   Power Saver 1/5/false, Garbage Collector 3/7/true).
5. Overclock: a scored 4 of Hearts in `G.play` gives `{mult = 3}`; a 2, 3 and
   5 each give `{mult = 3}`; a 6 gives nil; an Ace (14) gives nil; a 4 with
   `cardarea = G.hand` gives nil; `H.vars` is `{3}`.
6. Cache Hit: starts with `H.vars` `{8, 0}`; `joker_main` gives nil at 0 chips;
   `before` with `poker_hands = {Pair = {{}}}` returns a message "Upgrade!"
   and makes chips 8; a second such hand makes 16; `before` with
   `poker_hands = {Pair = {}}` changes nothing; `before` with a Pair and
   `blueprint = true` changes nothing; then `joker_main` gives `{chips = 16}`
   and `H.vars` is `{8, 16}`.
7. Two Cache Hit copies keep separate counts: upgrading one leaves the
   other's chips at 0.
8. Thermal Throttle: `joker_main` with `hands_left = 0` gives `{xmult = 2}`;
   with `hands_left = 1` or `3` gives nil; `H.vars` is `{2}`.
9. Power Saver: `H.dollars` is 3 with `discards_left = 3`, 1 with 1, nil
   with 0; with dollars set to 2 in its extra and 3 discards left it is 6;
   it has no effect in `joker_main` (nil or no calculate).
10. Garbage Collector: `joker_main` gives nil at X1; discarding a King, then a
    Jack (both face cards) returns "Upgrade!" each time and leaves xmult 1.2
    (within 1e-9); discarding a 7 or an Ace changes nothing; a face-card
    discard with `blueprint = true` changes nothing; then `joker_main` gives
    xmult 1.2 (within 1e-9) and `H.vars` is `{0.1, 1.2}` (within 1e-9).
11. Every Joker's `loc_txt.text` has as many `#n#` placeholders as its
    `H.vars` has values, numbered 1..n.
12. All ten images exist in the project: `assets/1x/j_<name>.png` at 71x95
    and `assets/2x/j_<name>.png` at 142x190, none blank (game.is_blank).
13. The preview tool's files exist and both load with luajit's `loadfile`
    (LOVE itself is checked by the capture).

## Playtest

On Vulcan, in Balatro (Steam, via Proton):

Install to: ~/.local/share/Steam/steamapps/compatdata/2379780/pfx/drive_c/users/steamuser/AppData/Roaming/Balatro/Mods/lab_jokers
Launch: start Balatro from Steam (Lovely and Steamodded installed as below)

- One-time setup: put Lovely's Windows DLL from github.com/ethangreen-dev/lovely-injector
  releases into Balatro's game folder (Steam > Manage > Browse local files):
  `winmm.dll` as of v0.10.0 (older releases shipped `version.dll`). Set the
  launch options to `WINEDLLOVERRIDES="winmm=n,b" %command%` (with
  `version.dll`: `version=n,b`). Install Steamodded into
  `~/.local/share/Steam/steamapps/compatdata/2379780/pfx/drive_c/users/steamuser/AppData/Roaming/Balatro/Mods/`.
- `forge playtest balatro-lab-jokers` copies the build straight into
  `Mods/lab_jokers` (the "Install to" path above).
- The game's Mods menu lists "Lab Jokers" with no load errors.
- All five Jokers appear in Collection > Jokers with their art and readable descriptions.
- Overclock adds +3 Mult for each scored 2-5; Thermal Throttle shows X2 only on the last hand.
- Cache Hit and Garbage Collector show "Upgrade!" and their descriptions' current values go up.
- Power Saver pays the right amount at the end of a round with discards left.
- Nothing crashes when Blueprint copies each Joker.
