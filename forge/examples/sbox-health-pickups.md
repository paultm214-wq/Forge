# health-pickups

Toolchain: sbox
License: MIT

## Goal

Two s&box components for a small arena game: `Health` (damage, healing,
death, a short invulnerability window after a hit) and `HealthPickup` (heals
whoever touches it once, then respawns after a delay). Game code only - the
scene, models and UI come later. Everything is plain methods with explicit
time, so it is tested exactly inside the engine without waiting for frames.

## Constraints

- C# for s&box (net10.0, C# 14) in `Code/`. Components derive from
  `Sandbox.Component`; tunables are `[Property]`.
- No `Time.Now` inside the logic: methods take the current time in seconds
  (`float now`). OnUpdate may call them with `Time.Now` later.
- No NuGet packages, no reflection, no static mutable state.
- MSTest tests for the pure logic in `UnitTests/` (class `[TestClass]`,
  methods `[TestMethod]`); scene tests go through `game.sbox_eval`.

## Interfaces

- `Code/Health.cs`: `public sealed class Health : Component`
  - `[Property] public int Max { get; set; } = 100;`
  - `[Property] public float InvulnerableFor { get; set; } = 0.5f;`
  - `public int Current { get; private set; }` - starts at Max (set in OnAwake
    AND when first read before OnAwake ran: `Current` is Max on a new component)
  - `public bool Dead => Current <= 0;`
  - `public bool Hurt( int amount, float now )` - false (and no change) when dead,
    amount <= 0, or `now` is less than InvulnerableFor after the last hit that
    landed; otherwise Current = max(0, Current - amount), returns true
  - `public int Heal( int amount )` - adds up to Max, returns how much was added;
    0 when dead or amount <= 0
  - `public event Action<int> Changed` - raised with the new Current on every change
- `Code/HealthPickup.cs`: `public sealed class HealthPickup : Component`
  - `[Property] public int Amount { get; set; } = 25;`
  - `[Property] public float RespawnAfter { get; set; } = 10f;`
  - `public bool Available( float now )`
  - `public bool TryUse( Health target, float now )` - when available and the
    target is alive and below Max: heals it, becomes unavailable until
    `now + RespawnAfter`, returns true. Otherwise false and nothing changes.

## Acceptance

1. A new Health in a scene: Current 100, Dead false.
2. Hurt(30, 0) -> true, Current 70; Hurt(30, 0.2) -> false (invulnerable),
   Current 70; Hurt(30, 0.6) -> true, Current 40.
3. Hurt(500, 10) -> Current 0, Dead true; then Heal(50) -> 0, Hurt(1, 20) -> false.
4. Heal on 90/100 with 25 -> returns 10, Current 100.
5. Changed fires once per change with the new value: Hurt(30, 0) then Heal(5)
   -> [70, 75]; a refused Hurt fires nothing.
6. Pickup (Amount 25, RespawnAfter 10): target at 60 -> TryUse(t, 0) true, 85;
   TryUse(t, 5) false; Available(9.9) false, Available(10) true; a full-health
   target -> false and the pickup stays available.
7. All of the above with both components on GameObjects in one scene
   (`go.Components.Create<Health>()`), checked with one `game.sbox_eval({...})`.
8. The project compiles with no errors (`game.sbox_build()`), and the MSTest
   tests in UnitTests/ pass (`game.sbox_test().assert_ok()`).
