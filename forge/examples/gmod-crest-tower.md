# gmod-crest-tower

Toolchain: gmod
License: MIT
Prior art: none

<!--
A Garry's Mod prototype of the Portland map's contested broadcast station
(the Council Crest tower that pdx-slice-a-plugin tracks in Papyrus): a
capture point that the nine factions take from each other by standing near
it. It tries the gmod toolchain harder than gmod-supply-drum:
- an entity with networked state that thinks on its own;
- ents.FindInSphere and networked faction tags;
- convars read live, hooks, a registered net message;
- persistence under data/;
- a bot made with player.CreateNextBot (the test server runs +maxplayers 2,
  so one fits) to call a console command as a player.
Most of the logic is a plain method with explicit time, so it is tested
exactly; one async part checks that Think really drives it.
-->

## Goal

A server addon with one scripted entity, `forge_crest_tower`. Things
tagged with a faction near it capture it over time: one faction alone
builds capture progress, two or more make it contested (frozen), nobody
near lets the progress decay. When the progress reaches the capture time
the tower changes hands, everyone hears about it, and the controller is
saved so it survives a map restart. Two console commands spawn a tower and
print its status.

## Constraints

- An addon: `addon.json` at the root.
  - The entity is in `lua/entities/forge_crest_tower/`: `shared.lua`,
    `init.lua` (server; `AddCSLuaFile("cl_init.lua")` and
    `AddCSLuaFile("shared.lua")`) and `cl_init.lua` (client: draws the
    model, plus the controller's name and a progress bar above it with
    `cam.Start3D2D`).
  - The shared faction table is in `lua/autorun/sh_forge_crest.lua`.
  - The commands, convars and net string are in
    `lua/autorun/server/sv_forge_crest.lua`.
- Model `models/props_c17/lamppost03a_off.mdl` (from memory of the HL2
  props - not checked). If `util.IsValidModel` says no on the server, fall
  back to `models/props_c17/oildrum001.mdl`, which gmod-supply-drum used. The
  tower is frozen: physics initialised, then
  `GetPhysicsObject():EnableMotion(false)`.
- Networked state through `SetupDataTables`:
  - `NetworkVar("Int", 0, "Controller")` (-1 = nobody);
  - `NetworkVar("Int", 1, "Capturer")` (-1 = nobody);
  - `NetworkVar("Float", 0, "Progress")` (seconds);
  - `NetworkVar("Bool", 0, "Contested")`;
  - `NetworkVar("Bool", 1, "Manual")`.
- Faction indexes 0..8 everywhere. A thing's faction is
  `ent:GetNW2Int("ForgeFaction", -1)`; anything outside 0..8 has none.
- No client code runs in tests (there is no client); glualint must report
  no errors. GLua style (`!=`, `&&`) is fine.

## Interfaces

**Shared (`lua/autorun/sh_forge_crest.lua`):** the global table
`FORGE_CREST` with `FORGE_CREST.Factions`, indexed 0 to 8 (index 0 set
explicitly; `#` does not count it): 0 "The Runway", 1 "The Heart",
2 "The Watch", 3 "The Reclaimers", 4 "The Clinic", 5 "The Academy",
6 "The Red", 7 "The Point", 8 "The Green". Also
`FORGE_CREST.NameOf(i)`: the name, or "nobody" for -1 or any other index.

**Server convars** (`CreateConVar`, FCVAR_ARCHIVE, read every time they are
used, never cached):

- `forge_crest_capture_time`, default 10 (seconds);
- `forge_crest_decay`, default 0.5 (progress lost per second);
- `forge_crest_radius`, default 256 (units).

**Entity** (`ENT.Type = "anim"`, `ENT.Base = "base_gmodentity"`,
`ENT.PrintName = "Crest Tower"`, `ENT.Spawnable = true`):

- `ENT:Advance(dt, counts)` (server): one step of the rules, where `counts`
  maps faction index to how many of that faction are near (missing or 0 =
  none; dt <= 0 does nothing). In order:
  1. **Nobody present:** Contested false; Progress = max(0, Progress - dt
     x decay); at 0, Capturer = -1.
  2. **Two or more factions present:** Contested true; nothing else
     changes.
  3. **Exactly one faction f present, n of it** (Contested false):
     - if f is the Controller (a defender alone), progress decays as in
       rule 1;
     - else if Capturer is another faction and Progress > 0, Progress =
       max(0, Progress - dt x min(n, 3)). At 0, Capturer = -1; time left
       over in this step is dropped;
     - otherwise Capturer = f and Progress += dt x min(n, 3). When
       Progress >= capture time, the tower is captured: old = Controller,
       Controller = f, Progress = 0, Capturer = -1; then
       `hook.Run("ForgeCrestCaptured", self, old, f)`, the state is saved,
       and the net message "ForgeCrestCaptured" is broadcast with
       `net.WriteEntity(self)`, `net.WriteInt(old, 8)`,
       `net.WriteInt(f, 8)`.
- `ENT:Think()` (server): unless Manual, counts the things near it
  (`ents.FindInSphere(self:GetPos(), radius)`, not itself, with a faction
  0..8, and players only when `Alive()`) and calls `Advance(now - last
  think time, counts)`. Then `self:NextThink(CurTime() + 0.25)` and
  `return true`.
- `ENT:Neutralize()` (server): Controller -1, Progress 0, Capturer -1,
  Contested false; `hook.Run("ForgeCrestCaptured", self, old, -1)` and a
  save, only when the controller was not already -1.
- **Persistence:** `forge_crest/<map>.txt` under DATA
  (`file.CreateDir("forge_crest")`; `.txt` because file.Write takes only
  some extensions) holds `util.TableToJSON({controller = n})`.
  - Every change of Controller writes it.
  - `ENT:Initialize` reads it (`file.Read(path, "DATA")`) and starts with
    that controller, or -1 when the file is missing or unreadable.
  - `<map>` is `game.GetMap()`.

**Server file (`sv_forge_crest.lua`):**

- `util.AddNetworkString("ForgeCrestCaptured")`.
- Console command `forge_crest_spawn`: for a player, spawns a tower 96
  units in front of their eyes (`ply:GetShootPos() +
  ply:GetAimVector() * 96`). From the server console (no player) it prints
  "forge_crest_spawn: run this as a player" and spawns nothing.
- Console command `forge_crest_status`: one line per tower,
  `Crest tower <EntIndex>: <controller name>, <capturer name> <progress
  rounded to 0.1>/<capture time>` plus " (contested)" when contested. It
  goes to the player with `ply:PrintMessage(HUD_PRINTCONSOLE, line)`, or
  to the server console with `print` when there is no player.

## Acceptance

All in ONE `game.gmod_run` (each run starts a server). It starts by
deleting `forge_crest/gm_construct.txt` if present, and makes every tower
for checks 2-5 with `ents.Create("forge_crest_tower")`, a position, `Spawn()`
and `SetManual(true)`, so their Think never interferes.

1. `FORGE_CREST.Factions[0]` is "The Runway" and `[8]` "The Green";
   `NameOf(-1)` and `NameOf(9)` are "nobody";
   `util.NetworkStringToID("ForgeCrestCaptured")` is not 0; both console
   commands are in `concommand.GetTable()`; the three convars exist with
   their defaults.
2. A fresh tower: IsValid, class forge_crest_tower, Controller -1,
   Capturer -1, Progress 0, Contested false, and its physics object does
   not move (`IsMotionEnabled()` false).
3. Capture time 10, decay 0.5:
   - Advance(4, {[2]=1}): Capturer 2, Progress 4.
   - Advance(1, {[2]=3}): Progress 7.
   - Advance(5, {[2]=1, [5]=1}): Contested true, Progress 7.
   - Advance(3, {[2]=1}): Contested false, Controller 2, Progress 0,
     Capturer -1.
   - A `hook.Add("ForgeCrestCaptured", ...)` handler saw exactly one call,
     (tower, -1, 2).
4. Same tower:
   - Advance(2, {[5]=2}): Capturer 5, Progress 4.
   - Advance(2, {}): Progress 3.
   - Advance(1, {[2]=1}) (the defender alone): Progress 2.5, Capturer 5.
   - Advance(10, {}): Progress 0, Capturer -1.
   - Then Advance(2, {[7]=1}) (Progress 2, Capturer 7) and Advance(1,
     {[4]=2}): Progress 0, Capturer -1, Controller still 2.
   - Advance(0, {[4]=1}) and Advance(-1, {[4]=1}) change nothing.
5. Persistence:
   - After check 3, `util.JSONToTable(file.Read("forge_crest/gm_construct.txt",
     "DATA")).controller` is 2.
   - A second tower spawned now starts with Controller 2.
   - Neutralize() on it: the file says -1, the hook saw (tower2, 2, -1),
     and a second Neutralize() runs no hook.
6. **Think drives it (async):** set `forge_crest_capture_time` to 2 with
   `GetConVar(...):SetFloat(2)`. Spawn a tower that is NOT manual at
   Vector(2000, 2000, 100), and two frozen `prop_physics` oil drums with
   `SetNW2Int("ForgeFaction", 6)` within 100 units of it.
   - After 4 s (timer.Simple), its Controller is 6.
   - Then a third frozen drum with faction 1 within 100 units: 1.5 s
     later Contested is true.
   - Then that drum is removed and the first two drums get faction -1:
     2 s later Contested is false and Progress is 0.
7. **A player, through a bot:** `player.CreateNextBot("Forge Bot")` is
   valid.
   - Calling `concommand.GetTable()["forge_crest_spawn"](bot,
     "forge_crest_spawn", {}, "")` adds one forge_crest_tower within 128
     units of `bot:GetShootPos()`.
   - Calling it with a NULL player (`NULL`) adds none.
   - `forge_crest_status` with the NULL player runs without a Lua error.
8. `r.errors` (Lua [ERROR] lines) is empty and `game.glualint()` reports no
   errors for the addon.

## Playtest

On a listen server with the addon:

- `forge_crest_spawn` while looking at the ground puts a lamppost in front
  of you showing "nobody" and an empty bar.
- `lua_run Entity(1):SetNW2Int("ForgeFaction", 4)`, then standing next to
  it: the bar fills over 10 s and the label becomes "The Clinic".
- A bot with another faction standing with you makes it say contested.
- `forge_crest_status` in the console lists the tower. After `changelevel
  gm_construct` and a new spawn, the tower remembers The Clinic.
