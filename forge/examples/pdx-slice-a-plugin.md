# pdx-slice-a-plugin

Toolchain: fo4
License: MIT
Prior art: none

<!--
Portland Survival Map (Fallout 4), Slice A - part 2 of 2.
The plugin skeleton the Creation Kit work builds on: the nine factions, an
empty Portland worldspace, static records for the four props from
pdx-slice-a-props.md, and the contested Council Crest broadcast station as
a quest with a Papyrus API.

Canon (Portland Survival Map planning v3) followed here:
- plugin-only, no F4SE;
- new records only - nothing in Fallout4.esm is changed;
- Creation Club content stays out (a separate optional plugin later).

This skeleton uses no DLC records, so Fallout4.esm is its only master. A
DLC master is added when a later change needs one.

Not made here, on purpose:
- coordinates, terrain, cells or placed references. Those come from
  landmarks.json and pdx_transform.py in the Creation Kit steps (Steps 6 and
  8), and nothing in this spec invents a position;
- references to vanilla FormIDs or EditorIDs. Every EditorID below is a NEW
  record of this plugin, with the PDX_ prefix.
-->

## Goal

`PortlandSurvival.esp` and its compiled scripts, ready to copy into Data/
and open in the Creation Kit:

- **The nine factions of the map**, named.
- **The `Portland` worldspace record**, empty: its land and cells come
  later in the CK.
- **Four static records** whose models are the props from
  pdx-slice-a-props (the NIFs are converted from its GLBs).
- **"The Crest Signal" quest.** Its script tracks which faction controls
  the Council Crest broadcast station (or that it is contested), and other
  scripts change it through a small Papyrus API.

## Constraints

- **Papyrus** goes in `Scripts/Source/User/` with the namespace `PDX`. For
  example, `ScriptName PDX:Quests:CrestBroadcast extends Quest` lives in
  `Scripts/Source/User/PDX/Quests/CrestBroadcast.psc`.
- **The plugin** `PortlandSurvival.esp` is written by `Plugin/Program.cs`
  with Mutagen. `Main(args)` writes into args[0]; args[1] is the folder
  that holds Fallout4.esm. Fallout4.esm is the only master, and no record
  overrides or links to a master record, so the program never needs to
  read it: the plugin is a new `Fallout4Mod` with Fallout4.esm added to its
  master list. It is a normal plugin, not ESL-flagged (it will hold a
  worldspace).
- **Faction order** everywhere (the Factions property and the index used by
  the scripts) is the order of the table below, from 0 to 8.
- **Scripts:** no F4SE functions, and no `Debug.Notification` spam: one
  notification when the controller changes, nothing else.

## Interfaces

**Faction records** (FACT; EditorID and name exactly as listed; no
relations yet, Step 7's zone narratives decide those):

| index | EditorID | name | home (for the record's comment only) |
|---|---|---|---|
| 0 | PDX_FacRunway | The Runway | PDX airport |
| 1 | PDX_FacHeart | The Heart | Downtown, neutral |
| 2 | PDX_FacWatch | The Watch | Mt Tabor |
| 3 | PDX_FacReclaimers | The Reclaimers | Washington Park |
| 4 | PDX_FacClinic | The Clinic | OHSU, holds the tram |
| 5 | PDX_FacAcademy | The Academy | Reed College |
| 6 | PDX_FacRed | The Red | N Mississippi |
| 7 | PDX_FacPoint | The Point | SE Foster |
| 8 | PDX_FacGreen | The Green | Tryon Creek / Marquam forests |

**Other records:**

- WRLD `PDX_Portland`, named "Portland": no parent worldspace, no cells.
- Static records (STAT), each with this model path (relative to
  Data/Meshes) and object bounds left at 0, which the CK recalculates once
  the NIF exists:

  | EditorID | model path |
  |---|---|
  | `PDX_BensonBubbler` | `PDX\Props\BensonBubbler.nif` |
  | `PDX_TramCabinRuin` | `PDX\Props\TramCabinRuin.nif` |
  | `PDX_HeartMarketStall` | `PDX\Props\HeartMarketStall.nif` |
  | `PDX_ReclaimerWaterStill` | `PDX\Props\ReclaimerWaterStill.nif` |

- GLOB `PDX_CrestController`: a short (`s`), value -1, where -1 means
  contested.
- QUST `PDX_CrestBroadcast`, named "The Crest Signal", start game enabled.
  It carries the script `PDX:Quests:CrestBroadcast` with two properties
  filled:
  - `ControllerGlobal` = `PDX_CrestController`;
  - `Factions` = the nine FACT records in index order.

**Papyrus `PDX:Factions`** (no extends, all `Global`):

- `Int Function Count() Global`: returns 9.
- `String Function NameOf(Int index) Global`: the table's name, or "" when
  the index is out of range.
- `Int Function IndexOf(String name) Global`: the index of a name,
  case-insensitive (Papyrus string `==` already is), or -1.

**Papyrus `PDX:Quests:CrestBroadcast extends Quest`:**

- `GlobalVariable Property ControllerGlobal Auto Const Mandatory`
- `Faction[] Property Factions Auto Const Mandatory`
- `CustomEvent ControllerChanged` - declared at script level, no
  parameters. It is sent with a two-element Var array, the old index and
  then the new index: `Var[] kArgs = new Var[2]`, set `kArgs[0]` and
  `kArgs[1]`, then `SendCustomEvent("ControllerChanged", kArgs)`.
- `Int Function Controller()`: the global's value as Int; -1 means
  contested.
- `Faction Function ControllerFaction()`: Factions[Controller()], or None
  when contested.
- `Bool Function Seize(Int index)`:
  - returns False and changes nothing when the index is outside 0..8 or is
    already the controller;
  - otherwise sets the global, sends ControllerChanged, shows one
    notification "Council Crest now broadcasts for <name>" (the name from
    PDX:Factions.NameOf), and returns True.
- `Bool Function Contest()`: sets -1 (sending ControllerChanged and the
  notification "Council Crest is contested") and returns True when it was
  controlled; returns False when it was already contested.
- `String Function StatusLine()`: "Council Crest: contested", or
  "Council Crest: <name> broadcasting".
- `Event OnQuestInit()`: sets the global to -1 without a notification.

## Acceptance

Nothing runs the game. These check the compiled scripts and the plugin's
records.

1. `game.papyrus()` is ok with no errors.
   - `r.pex` holds `game.project_dir() / "Scripts/PDX/Factions.pex"` and
     `game.project_dir() / "Scripts/PDX/Quests/CrestBroadcast.pex"` (as
     absolute paths).
   - Each one's `game.pex_header(...)['ok']` is true, and its source ends
     with the right .psc file's name.
2. The compiler really checks the API. A scratch script that calls
   `Seize("The Clinic")` (a String where an Int belongs) fails with an error
   on that line. One that calls `PDX:Factions.NameOf(3)` and assigns the
   result to a String compiles.
3. `esp = game.esp_build()` gives `PortlandSurvival.esp`.
   - `game.esp_header(esp)['masters'] == ["Fallout4.esm"]`.
   - No record is an override.
   - The header is not flagged ESL.
4. The plugin holds exactly nine FACT records. Each EditorID has the name
   from the table, and no FACT has an XNAM (relation) subrecord.
5. `PDX_Portland` is a WRLD named "Portland" with no parent-worldspace
   subrecord (WNAM), and the plugin holds no CELL or REFR records.
6. Each of the four STATs has a model (`rec['model']`, its MODL path) that
   is exactly its table path.
7. `PDX_CrestController` is a GLOB of type short with value -1:
   `rec['glob_type'] == 's'` and `rec['value'] == -1.0`. (Fallout 4 keeps
   a global's type in FNAM and its value as a float in FLTV; there is no
   GLOB DATA.)
8. `PDX_CrestBroadcast` is a QUST named "The Crest Signal", with the start
   game enabled flag set: `rec['quest_flags'] & 0x0001` (DNAM's flags; there
   is no QUST DATA).
   - Its scripts (`rec['scripts']`, the decoded VMAD) are one script
     `PDX:Quests:CrestBroadcast` with a property `ControllerGlobal` (an
     object: the GLOB's FormID) and a property `Factions` (an object array:
     the nine FACT FormIDs in index order).
   - (Names in the raw VMAD are length-prefixed, not null-terminated.)
9. What forge will ship to outputs/build, `out = game.build_preview('fo4')`,
   holds `PortlandSurvival.esp`, `Scripts/PDX/Factions.pex` and
   `Scripts/PDX/Quests/CrestBroadcast.pex`. (outputs/build itself is made
   only after every chunk test has passed.)

## Playtest

In the Creation Kit, with the four NIFs from pdx-slice-a-props converted and
placed under Data/Meshes/PDX/Props/:

- PortlandSurvival.esp loads with Fallout4.esm, with no errors about this
  plugin in the warnings window.
- The Object Window shows the nine factions, the four statics (each
  showing its mesh in the preview) and the quest with its script and both
  properties filled.
- World > Worldspaces lists Portland. Its land and cells come in Step 6.
- In game (on Hades, which is the gaming PC now), with the plugin enabled:
  `cqf PDX_CrestBroadcast "PDX:Quests:CrestBroadcast.Seize" 4` on the
  console shows "Council Crest now broadcasts for The Clinic". Check the
  console syntax before relying on it - it is from memory.
