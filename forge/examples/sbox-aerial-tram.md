# sbox-aerial-tram

Toolchain: sbox
License: MIT
Prior art: none

<!--
An s&box prototype of an aerial tram like the one in the Portland map's
Slice A (OHSU's tram between the river and Marquam Hill): two cabins on one
line that leave and arrive together in opposite directions (jig-back),
with a smooth start and stop, a wait at each station, and riders who board,
ride along and get off.

It tries the sbox toolchain harder than sbox-health-pickups:
- components that create and own child GameObjects;
- world positions and parenting (riders move with the cabin);
- events;
- a pure motion-profile class tested with MSTest in UnitTests/, next to
  scene tests through game.sbox_eval.

Time is explicit (Step(dt)), so everything is exact and no test waits for
frames. All numbers in Acceptance were checked with a short script.
-->

## Goal

Game code for an aerial tram. The scene, models and UI come later.

- `TramProfile`: the motion maths, how far along the line a cabin is t
  seconds into a trip. It speeds up at a fixed rate, cruises, then slows
  down the same way, or never reaches cruising speed on a short line.
- `TramLine`: a component that runs two cabins between station A and
  station B.
- `TramRider`: a component that lets something board a cabin, ride, and
  get off.

## Constraints

- C# for s&box (net10.0, C# 14) in `Code/`. Components derive from
  `Sandbox.Component`; tunables are `[Property]`.
- No `Time.Now` or `Time.Delta` inside the logic: `Step(float dt)` takes
  the time. An OnUpdate that calls `Step(Time.Delta)` is fine, but no test
  relies on it.
- No NuGet packages, no reflection, no static mutable state.
- `TramProfile` is a `public static class` with no engine types, so the
  MSTest tests in `UnitTests/` (`[TestClass]`, `[TestMethod]`) cover it
  without a scene. Scene behaviour is tested through `game.sbox_eval`.
- Floating-point checks allow 1e-3.

## Interfaces

**`Code/TramProfile.cs`** (`public static class TramProfile`; length > 0,
speed > 0, accel > 0, or ArgumentOutOfRangeException):

- `float Duration( float length, float speed, float accel )`: the trip
  time.
  - When length >= speed^2 / accel, the trip is trapezoidal:
    2 x speed / accel + (length - speed^2 / accel) / speed.
  - Otherwise it is triangular: 2 x sqrt(length / accel).
- `float Distance( float t, float length, float speed, float accel )`:
  distance along the line at time t into the trip.
  - 0 for t <= 0, and length for t >= Duration.
  - Speeding up: 0.5 x accel x t^2.
  - Cruising: d_acc + speed x (t - t_acc), where t_acc = speed / accel and
    d_acc = speed^2 / (2 accel).
  - Slowing down: length - 0.5 x accel x (Duration - t)^2.
  - The triangular case is the same without the cruise.
- `float Speed( float t, float length, float speed, float accel )`: the
  speed at t (0 outside the trip).

**`Code/TramLine.cs`** (`public sealed class TramLine : Component`):

- Properties:
  - `[Property] public Vector3 StationA { get; set; }`
  - `[Property] public Vector3 StationB { get; set; }` (world positions)
  - `[Property] public float Speed { get; set; } = 200f;` (units per
    second)
  - `[Property] public float Accel { get; set; } = 50f;`
  - `[Property] public float DwellTime { get; set; } = 5f;`
- `public enum TramState { Dwelling, Moving }` (nested, or in the same
  file).
- `public TramState State { get; }`, starting as Dwelling.
- `public float Elapsed { get; }`: seconds into the current dwell or trip.
- `public float TripTime`: TramProfile.Duration of the line (|B - A|),
  with Speed and Accel.
- `public GameObject Cabin( int i )`, for i = 0 or 1 (other values:
  ArgumentOutOfRangeException).
  - Creates both cabins on first use, if they do not exist yet.
  - Each cabin is a new GameObject named "Cabin 0" or "Cabin 1", a child
    of the line's GameObject.
  - Cabin 0 starts at StationA and cabin 1 at StationB. This is lazy, so
    it does not depend on when OnAwake runs.
- `public int CabinStation( int i )`: 0 (A) or 1 (B) while dwelling,
  -1 while moving.
- `public void Step( float dt )` (dt < 0: ArgumentOutOfRangeException; 0
  does nothing):
  - Advances time and carries what is left over across phases, so one
    Step(30) does what many small steps do.
  - Dwelling for DwellTime, then Moving for TripTime, then Dwelling at the
    other end, and so on.
  - While moving, cabin 0 is at the start station + (end - start) x
    Distance / length. Cabin 1 does the same in the opposite direction.
  - Arriving snaps each cabin exactly to its station.
- Events:
  - `public event Action<int, int> Departed`: (cabin, station it left).
  - `public event Action<int, int> Arrived`: (cabin, station it reached).
  - For each departure or arrival, cabin 0's event comes before cabin 1's.
    Every phase change that a single Step crosses raises its events, in
    order.

**`Code/TramRider.cs`** (`public sealed class TramRider : Component`):

- `[Property] public float BoardRadius { get; set; } = 100f;`
- `public TramLine Line { get; }`, `public int CabinIndex { get; }`
  (-1 when not riding), `public bool Riding => CabinIndex >= 0;`
- `public bool Board( TramLine line, int cabin )`:
  - Refused (false, nothing changes) when already riding, when that cabin
    is not dwelling, or when this GameObject's world position is farther
    than BoardRadius from the cabin's.
  - Otherwise it makes this GameObject a child of the cabin, keeping its
    world position (`GameObject.SetParent( cabin, true )`), sets
    Line and CabinIndex, and returns true.
- `public bool Alight()`: refused when not riding or while the cabin is
  moving. Otherwise it un-parents, keeping the world position, sets
  CabinIndex -1 and Line null, and returns true.

## Acceptance

Profile (MSTest in UnitTests/, run with `game.sbox_test().assert_ok()`;
the same numbers through `game.sbox_eval(..., engine=False)` are fine too).
Line 1 is length 1118.033988 (from (0,0,0) to (1000,0,500)), speed 200,
accel 50; line 2 is length 400, same speed and accel:

1. Duration(line 1) = 9.590170; Duration(line 2) = 5.656854 (triangular).
2. Distance on line 1:
   - t = 2: 100;
   - t = 4: 400 (end of speeding up);
   - t = 5: 600;
   - t = Duration / 2: 559.016994 (half the length);
   - t = Duration - 1: 1093.033989;
   - t = -1: 0, and t = 20: 1118.033988.
   Speed is 100 at t = 2, 200 at t = 5 and 0 at t = 20.
3. Distance on line 2: t = 2 gives 100, Duration / 2 gives 200, t = 4
   gives 331.370850. Its top speed is at Duration / 2: 141.421356.
4. Length 0, speed -1 or accel 0 throws ArgumentOutOfRangeException.

Scene (one `game.sbox_eval({...})` with several snippets; each makes a
GameObject with a TramLine where StationA = (0,0,0), StationB =
(1000,0,500), Speed 200, Accel 50 and DwellTime 5):

5. Cabin(0) and Cabin(1) exist, are named "Cabin 0" and "Cabin 1", have the
   line's GameObject as Parent, and are at A and B. Calling Cabin(0) again
   returns the same object. Cabin(2) throws ArgumentOutOfRangeException.
   State Dwelling, CabinStation(0) 0 and CabinStation(1) 1, TripTime
   9.590170.
6. Step(5): Moving, Elapsed 0, Departed raised as (0,0), (1,1). Step(2):
   cabin 0 is at A + (B - A) x 100 / 1118.033988 and cabin 1 at B + (A - B)
   x 100 / 1118.033988; CabinStation is -1 for both.
7. From a fresh line, ONE Step(30) raises, in order:
   - Departed (0,0), (1,1);
   - Arrived (0,1), (1,0);
   - Departed (0,1), (1,0);
   - Arrived (0,0), (1,1).
   It ends Dwelling with Elapsed 0.819660, cabin 0 exactly at A and cabin 1
   exactly at B. Many Step(0.1) calls up to t = 30 give the same events and
   the same final positions.
8. Riders:
   - A GameObject with a TramRider at (30,0,0): Board(line, 0) is true.
     Its Parent is Cabin(0), and its world position is still (30,0,0).
   - Board again: false (already riding).
   - Step(5) then Step(2): its world position is cabin 0's world position
     + (30,0,0).
   - Alight() while moving: false.
   - After the trip ends (Step(8) more: 10 s into a 9.59 s trip), Alight()
     is true. Its Parent is
     no longer the cabin, and its world position equals where it was riding
     (B + (30,0,0)).
9. Refusals:
   - A rider at (500,0,0) (outside BoardRadius 100): Board is false.
   - A rider at (0,0,0) after Step(5) (cabin moving): Board is false.
   - Board(line, 1) for a rider at (1000,0,500) before any Step: true.
10. The project compiles with no errors (`game.sbox_build()`), and every
    MSTest test in UnitTests/ passes.

## Playtest

In the s&box editor, in a scene with two station markers about 1000 units
apart and 500 up, and a box model on each cabin:

- With OnUpdate calling Step(Time.Delta), the cabins pass each other in the
  middle, ease in and out of the stations, and pause 5 s at each end.
- A player GameObject given a TramRider and told to Board while standing in
  a cabin rides along without sliding, and stays where it was when it
  Alights at the top.
