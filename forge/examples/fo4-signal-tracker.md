# signal-tracker

Toolchain: fo4
License: MIT

## Goal

A small Fallout 4 mod skeleton: a quest that counts "signals" the player has
tuned, with a Papyrus API other scripts call, a global variable holding the
count, and a misc item the player gets at the start. It shows forge's
Fallout 4 path end to end: Papyrus compiled to .pex, the plugin built with
Mutagen, both checked without running the game, and outputs/build ready to
copy into Data/.

All records are NEW records of this plugin (no overrides of Fallout4.esm
records), so nothing here depends on the base game's FormIDs.

## Constraints

- Papyrus in `Scripts/Source/User/` with a namespace:
  `ScriptName SignalTracker:Quests:Tracker extends Quest` lives in
  `Scripts/Source/User/SignalTracker/Quests/Tracker.psc`.
- The plugin `SignalTracker.esp` (Fallout4.esm as its only master) is
  written by `Plugin/Program.cs` with Mutagen: `Main(args)` writes into
  args[0] and reads masters from args[1] (Fallout 4's Data folder).
- The quest record carries the script (its VMAD names
  `SignalTracker:Quests:Tracker`) with the property `SignalCount` filled with
  the global below.
- No F4SE, no other mods.

## Interfaces

- Papyrus `SignalTracker:Quests:Tracker extends Quest`:
  - `GlobalVariable Property SignalCount Auto`
  - `Int Function Tune(Int amount)` - amount <= 0 returns the count unchanged;
    otherwise adds it (SignalCount.Mod(amount)) and returns the new count as Int
  - `Int Function Count()` - the count as Int
  - `Event OnQuestInit()` - sets the count to 0
- Papyrus `SignalTracker:Util` (no extends, all `Global`):
  - `Int Function Clamp(Int value, Int lo, Int hi) Global`
- Plugin records (EditorIDs exactly):
  - QUST `SignalTrackerQuest`, name "Signal Tracker", start game enabled
  - GLOB `SignalTrackerCount`, a short or int global, value 0
  - MISC `SignalTrackerTuner`, name "Signal Tuner", value 25, weight 0.5

## Acceptance

1. `game.papyrus()` -> ok, no errors; the .pex files are
   `Scripts/SignalTracker/Quests/Tracker.pex` and `Scripts/SignalTracker/Util.pex`;
   each `game.pex_header(...)['ok']`.
2. A deliberately wrong call (Tune with a String) in a scratch script fails
   with an error on its line - the compiler is really checking types.
3. `esp = game.esp_build()` -> SignalTracker.esp; `game.esp_header(esp)['masters']`
   == ["Fallout4.esm"]; no record is an override.
4. `game.esp_record(esp, "SignalTrackerTuner")`: type MISC, full "Signal Tuner",
   DATA unpacks (struct "<if") to (25, 0.5).
5. `SignalTrackerQuest` is QUST with full "Signal Tracker" and a VMAD subrecord
   containing the bytes `SignalTracker:Quests:Tracker` and `SignalCount`.
6. `SignalTrackerCount` is GLOB.
7. outputs/build has SignalTracker.esp and both .pex files under Scripts/.
