# supply-drum

Toolchain: gmod
License: MIT

## Goal

A Garry's Mod addon with one scripted entity, `forge_supply_drum`: a physics
prop holding a number of supplies that players (or code) take from it. When
it runs out it turns grey and removes itself after a few seconds. A console
command spawns one in front of the caller. Server logic is tested on the
headless dedicated server (gm_construct); client drawing is syntax-checked.

## Constraints

- An addon: `addon.json` at the root, the entity in
  `lua/entities/forge_supply_drum/` as `shared.lua`, `init.lua` (server,
  `AddCSLuaFile("cl_init.lua")` and `AddCSLuaFile("shared.lua")`) and
  `cl_init.lua` (client: draws the model and the count above it with
  `cam.Start3D2D`).
- Model `models/props_c17/oildrum001.mdl` (in the base game content; the
  dedicated server has it).
- Physics: `PhysicsInit(SOLID_VPHYSICS)`, `SetMoveType(MOVETYPE_VPHYSICS)`,
  `SetSolid(SOLID_VPHYSICS)`, and the physics object woken on spawn.
- The count is a networked Int: `NetworkVar("Int", 0, "Supplies")` in
  `SetupDataTables`.
- GLua style is fine (`!=`, `&&`); glualint must report no errors.

## Interfaces

- `ENT.Type = "anim"`, `ENT.Base = "base_gmodentity"`, `ENT.PrintName = "Supply Drum"`,
  `ENT.Spawnable = true`
- Server: `ENT:TakeSupplies(amount)` - takes min(amount, left) (amount <= 0
  takes nothing), returns the number taken, and runs
  `hook.Run("ForgeSupplyTaken", self, taken, left_after)` when taken > 0.
  At 0 left: colour `Color(90, 90, 90)` and `SafeRemoveEntityDelayed(self, 5)`.
- Server: `ENT:Use(activator)` - takes 1 for a player.
- Default supplies: the convar `forge_supply_drum_amount` (FCVAR_ARCHIVE,
  default 10), read when the entity initialises.
- Console command `forge_spawn_supply_drum` (server): spawns one 64 units in
  front of the calling player's eyes; prints a message and does nothing when
  run from the server console (no player).

## Acceptance

All in ONE `game.gmod_run` (each run starts a server): spawn a drum with
`ents.Create("forge_supply_drum")`, SetPos(Vector(0, 0, 200)), Spawn(); then

1. IsValid, its class is forge_supply_drum and GetSupplies() == 10.
2. TakeSupplies(3) returns 3, GetSupplies() == 7; TakeSupplies(0) and
   TakeSupplies(-2) return 0.
3. A hook.Add("ForgeSupplyTaken", ...) handler sees (drum, 3, 7) for the call
   in 2.
4. TakeSupplies(50) returns 7 and the colour is (90, 90, 90).
5. After 2 seconds (FORGE.async, timer.Simple) the drum has fallen
   (z below 200) - physics works; after 6 seconds it is no longer valid.
6. The concommand exists (`concommand.GetTable()["forge_spawn_supply_drum"]`).
7. `game.glualint()` reports no errors; `r.errors` (Lua [ERROR] lines) is empty.
