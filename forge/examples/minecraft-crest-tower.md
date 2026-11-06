# minecraft-crest-tower

Toolchain: minecraft
License: MIT
Prior art: none

<!--
The Council Crest capture point again (gmod-crest-tower, pdx-slice-a-plugin),
as a Paper plugin, with the same rules and the same numbers so the three
implementations can be compared. It tries the minecraft toolchain end to
end:
- the rules in a plain Java class, tested exactly in JUnit;
- the plugin wired up in MockBukkit (command, config, a custom event);
- a real headless Paper server: a task on the scheduler drives the tower
  from entities standing near it.
Every API name below is a Bukkit/Paper name; a wrong one comes back from
javac with the members the jar really has.
-->

## Goal

A Paper plugin, `ForgeCrest`. A crest tower is a location in a world.
Entities tagged with a faction near it capture it over time: one faction
alone builds capture progress, two or more make it contested (frozen),
nobody near lets the progress decay. When the progress reaches the capture
time the tower changes hands, a `CrestCapturedEvent` is called, everyone
online is told, and the controller is saved in the plugin's config so it
survives a restart. `/crest spawn` and `/crest status` manage it.

## Constraints

- Package `forge.crest`; main class `forge.crest.ForgeCrest extends
  JavaPlugin` (public, not final). `plugin.yml`: name ForgeCrest, version
  1.0, main forge.crest.ForgeCrest, api-version as the image's Paper
  (game.mc_versions()), command `crest` with usage `/crest spawn|status`.
- `forge.crest.CrestRules` is plain Java (no Bukkit types): the rules, the
  state and the numbers, so JUnit tests it without a server.
- A thing's faction is a scoreboard tag `forge_faction_<n>`
  (`Entity.getScoreboardTags()`), n in 0..8; anything else has none.
  Players count only when not dead.
- Config keys (plugin config.yml, defaults written with
  `saveDefaultConfig()` from `src/main/resources/config.yml`):
  `capture-time: 10.0`, `decay: 0.5`, `radius: 16.0`, `controller: -1`.
  Read every time they are used.
- No Thread.sleep; the tower is driven by
  `Bukkit.getScheduler().runTaskTimer(plugin, task, 5L, 5L)` (every 5
  ticks = 0.25 s).

## Interfaces

**`CrestRules`** - fields read through getters: `controller()`,
`capturer()`, `progress()` (double seconds), `contested()`.
`CrestRules(int controller)` starts with Capturer -1, Progress 0, not
contested. `Integer advance(double dt, Map<Integer, Integer> counts,
double captureTime, double decay)`: one step; counts maps faction to how
many are near (missing or 0 = none; dt <= 0 does nothing). It returns the
NEW controller when this step captured the tower, else null. In order:
1. **Nobody present:** contested false; progress = max(0, progress - dt x
   decay); at 0, capturer = -1.
2. **Two or more factions present:** contested true; nothing else changes.
3. **Exactly one faction f present, n of it** (contested false):
   - f is the controller (a defender alone): progress decays as in rule 1;
   - else if capturer is another faction and progress > 0: progress =
     max(0, progress - dt x min(n, 3)); at 0, capturer = -1; time left over
     in this step is dropped;
   - otherwise capturer = f and progress += dt x min(n, 3); when progress
     >= captureTime: controller = f, progress 0, capturer -1, return f.
`void neutralize()`: controller -1, progress 0, capturer -1, contested
false. `static String nameOf(int f)`: 0 "The Runway", 1 "The Heart",
2 "The Watch", 3 "The Reclaimers", 4 "The Clinic", 5 "The Academy",
6 "The Red", 7 "The Point", 8 "The Green", anything else "nobody".

**`CrestCapturedEvent extends org.bukkit.event.Event`**: `getTower()`
(the CrestTower), `getOldController()`, `getNewController()`; a static
HandlerList with `getHandlers()` and `static getHandlerList()`.

**`CrestTower`**: `CrestTower(ForgeCrest plugin, Location where)`;
`location()`, `rules()`; `boolean manual` (public field, default false);
`tick(double dt)`: unless manual, counts the faction-tagged entities
within radius of the location (`World.getNearbyEntities(location, r, r,
r)` then a distance check) and calls `step(dt, counts)`.
`step(double dt, Map<Integer,Integer> counts)` calls rules().advance with
the config's numbers; on a capture it calls the event
(`Bukkit.getPluginManager().callEvent`), saves `controller` to the config
(`getConfig().set` + `saveConfig()`) and sends every online player
"The crest tower falls to <name>.".

**`ForgeCrest`**: `towers()` (List<CrestTower>, unmodifiable),
`CrestTower spawnTower(Location)` (places a BEACON block there, starts it
with the saved controller, adds it to the list), the 5-tick timer calling
`tick(0.25)` on every tower, and the `/crest` executor:
- `spawn` from a player: a tower at the block 3 ahead of their eyes
  (`getEyeLocation().add(getLocation().getDirection().multiply(3))`),
  replying "Crest tower placed."; from the console: "Run /crest spawn as a
  player." and nothing spawned.
- `status`: one line per tower (built by `List<String> statusLines()` on
  ForgeCrest, then sent), `Crest tower <i>: <controller name>,
  <capturer name> <progress to 1 decimal>/<capture time>` plus
  " (contested)".
- anything else: false (Bukkit then shows the usage).

## Acceptance

1. **JUnit, `CrestRulesTest`** (capture time 10, decay 0.5), on one
   `new CrestRules(-1)`:
   - advance(4, {2:1}): capturer 2, progress 4, returns null;
   - advance(1, {2:3}): progress 7; advance(5, {2:1, 5:1}): contested,
     progress 7;
   - advance(3, {2:1}): returns 2; controller 2, progress 0, capturer -1;
   - advance(2, {5:2}): capturer 5, progress 4; advance(2, {}): 3;
     advance(1, {2:1}) (defender alone): 2.5, capturer 5;
     advance(10, {}): 0, capturer -1;
   - advance(2, {7:1}) then advance(1, {4:2}): progress 0, capturer -1,
     controller 2; advance(0, {4:1}) and advance(-1, {4:1}) change nothing;
   - nameOf(0) "The Runway", nameOf(8) "The Green", nameOf(9) and
     nameOf(-1) "nobody".
2. **JUnit + MockBukkit, `PluginTest`**: `MockBukkit.mock()`, load
   ForgeCrest; `server.addPlayer()` runs `crest status` with no towers
   and gets no line; the console's `crest spawn` (via
   `server.dispatchCommand(server.getConsoleSender(), "crest spawn")`)
   spawns nothing; the player's `crest spawn` replies "Crest tower
   placed." (`player.nextMessage()`) and `towers().size()` is 1; a
   listener registered in the test sees exactly one CrestCapturedEvent
   (-1 to 3) after `step(10, {3:1})` on that tower (manual), and
   `getConfig().getInt("controller")` is then 3.
3. `game.mc_build()` makes a jar with plugin.yml, config.yml and
   forge/crest/ForgeCrest.class.
4. **A real server, one `game.mc_run`:**
   - the plugin is enabled (`Bukkit.getPluginManager().getPlugin("ForgeCrest")`);
   - `spawnTower` at the world's spawn + (40, 0, 40) puts a BEACON there;
   - **driven by the scheduler (async):** set `capture-time` to 2 in the
     config, spawn two armor stands within 4 blocks of a second, NOT
     manual tower with `addScoreboardTag("forge_faction_6")`, wait 80
     ticks (`FORGE.later`): its controller is 6, and the plugin's config
     says 6.
5. In the same run, `statusLines()` has, for the second tower of check 4
   (index 1 in towers(); indexes start at 0), exactly
   `Crest tower 1: The Red, nobody 0.0/2.0`.

## Playtest

On a Paper server with the jar in plugins/: `/crest spawn`, then
`/tag @s add forge_faction_2` and stand next to the beacon for 10 seconds.
