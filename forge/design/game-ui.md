# forge game UI standard

Menus and interface for every forge game, on top of `house-style.md`. Based
on the Xbox Accessibility Guidelines (XAG 101-123) and the Game
Accessibility Guidelines. A game's design brief lists which of these apply
and how; anything it leaves out still applies.

## Menus every game has

- **Title / main menu**: Continue (when a save exists, first and focused),
  New game, Load (when saves exist), Settings, Help / How to play, Credits,
  Quit (desktop builds).
- **Pause menu** (any time in play, Esc / Start): Resume (focused), Settings,
  Help, Save (when the game saves by hand), Quit to title (confirmed if
  progress would be lost).
- **Settings**, grouped in tabs or sections, every change applied
  immediately, saved to the user folder (Godot: `user://settings.cfg`) and
  loaded at start, with "Reset to defaults":
  - Audio: master, music, effects, voice/dialogue volumes (sliders, 0-100%),
    mute when the window loses focus.
  - Display: window mode (windowed / fullscreen), resolution or UI scale,
    vsync; brightness or gamma for dark games.
  - Controls: rebinding for keyboard and gamepad (every action, conflicts
    shown), mouse/stick sensitivity, invert Y, hold-or-toggle for held
    actions.
  - Accessibility: text size (up to 200% of default), subtitles on/off with
    size and background, colour-blind-safe options (or no information by
    colour alone), screen shake and flashing off, reduce motion, game speed or
    assist options where they fit the game.
  - Language, when the game has more than one.
- **Save / load**: slots with name, date and a thumbnail or location;
  overwriting and deleting are confirmed.
- **Confirmations** for anything that loses progress, with the safe option
  focused.

## Navigation and input

- Every menu works with keyboard, mouse and gamepad, and with each alone
  (XAG 107, 112). Godot: each action in the Input Map has a keyboard event
  and a joypad event; menus use `ui_accept`, `ui_cancel` and the `ui_*`
  directions.
- Focus is always visible and starts on the most likely choice; it never
  disappears or lands on something invisible (XAG 113). Godot: call
  `grab_focus()` on the first control when a menu opens; set focus
  neighbours on irregular layouts.
- Back / cancel always returns one level, from every menu, with the same
  button (Esc, B / Circle).
- No time limits in menus; no input that needs rapid or simultaneous presses
  without an alternative.

## Text and readability (XAG 101, 102)

- Body text at least 18 px at 1080p for PC, 26 px for a TV/console layout,
  scaling with resolution; players can scale it to 200% without losing
  content.
- Text on a solid or darkened panel when over the game scene; contrast
  4.5:1.
- Lines of at most ~80 characters; line spacing at least 1.5.
- Pixel-art games: draw text with a pixel font at whole-number scales, never
  blurred by filtering.

## Feedback and clarity

- Important information is never colour-only or sound-only (XAG 103): add
  an icon, text or a shape.
- Subtitles for all speech, with the speaker's name (XAG 104).
- Tell players what the controls are, on screen, using the device they last
  used (keyboard glyphs or gamepad glyphs).
- Errors (a failed save, a missing controller) say what happened and what
  to do (XAG 115).
- Respect "reduce motion": no screen shake, flashing or camera bob when it is
  on (XAG 117, 118).

## Help in the game

- "How to play" from the title and pause menus: controls, goals and the
  rules a player needs, in short sections with pictures; searchable when
  there are more than a handful of topics. Its content is
  `help/help.json` (see house-style.md), which forge fills at commit from
  the game's user guide.

## Checklist for a menu chunk

- [ ] All menus above that the game needs, reachable from the title and
      pause menus.
- [ ] Keyboard, mouse and gamepad each work alone; focus always visible and
      set when a menu opens; Back works everywhere.
- [ ] Settings apply at once, persist, and reset.
- [ ] Text readable at the minimum size; nothing colour-only.
