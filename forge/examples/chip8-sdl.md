# chip8

Toolchain: c-sdl
License: MIT

## Goal

A CHIP-8 emulator in C11 with an SDL2 window: the classic 64x32 monochrome
display scaled up, the 16-key hex keypad on the keyboard, 60 Hz delay and
sound timers, and a beep. A headless self-test binary runs a ROM for a given
number of cycles and prints the machine state, so every instruction can be
tested exactly. It plays the many public-domain and homebrew CHIP-8 games.

## Constraints

- C11, SDL2 only, built with CMake (`cmake -S . -B build && cmake --build build`).
- Two targets: `chip8` (the SDL emulator) and `chip8_selftest` (no SDL).
- The CPU core (chip8.c / chip8.h) has no SDL or I/O: the front end and the
  self-test both drive it, so its behaviour is identical in both.
- ROM files: a path ending in `.hex` is hex text (whitespace and `#` comments
  ignored); anything else is a raw binary. Programs load at 0x200; the
  standard 80-byte font loads at 0x050.
- Original COSMAC VIP behaviour where implementations differ: 8XY6/8XYE shift
  VY into VX; FX55/FX65 leave I = I + X + 1; BNNN jumps to NNN + V0;
  8XY1/8XY2/8XY3 reset VF to 0. FX0A: while no key is down, chip8_step
  leaves PC unchanged; once one is, VX = the lowest key held and PC moves on.
- The emulator runs 700 instructions per second by default (`--speed N`),
  decrements timers at 60 Hz, and draws with XOR and sprite wrap-around at the
  screen edge for the start position, clipping for the rest of the sprite.

## Interfaces

- `chip8.h`: `typedef struct Chip8 { uint8_t mem[4096]; uint8_t v[16]; uint16_t i, pc;
  uint16_t stack[16]; uint8_t sp, dt, st; uint8_t display[32][64]; uint8_t keys[16];
  int waiting_key; } Chip8;`
- `void chip8_init(Chip8 *c);` - zeroes everything, loads the font at 0x050, pc = 0x200
- `int chip8_load(Chip8 *c, const uint8_t *rom, size_t n);` - 0, or -1 if n > 3584
- `int chip8_step(Chip8 *c);` - one instruction; 0, or -1 on an unknown opcode
- `void chip8_tick_timers(Chip8 *c);` - one 60 Hz tick
- `chip8_selftest <rom> <cycles> [--keys HEX]` prints exactly:
  `PC=XXXX I=XXXX SP=X DT=XX ST=XX` then `V=` and 16 two-digit hex values
  separated by spaces, then 32 lines of 64 characters (`#` lit, `.` dark).
  `--keys 5A` holds keys 5 and A down for the whole run. An unknown opcode
  prints `BAD OPCODE XXXX at XXXX` and exits 2; a ROM chip8_load refuses
  prints `ROM TOO LARGE` and exits 3.
- `chip8 [--scale N] [--speed N] <rom>` - the SDL window. Keys: 1234 / QWER /
  ASDF / ZXCV map to 123C / 456D / 789E / A0BF. Esc quits.

## Test ROMs

These are written for this spec (own work, public domain). Each chunk that
needs one writes it to roms/<name>.hex exactly as given.

roms/digit0.hex - draws the font digit 0 at (0, 0), then loops:
```
00E0 6000 F029 6100 D115 120A
```

roms/add.hex - VA = 0xFF + 0x01 with carry, VB = 5 + 3:
```
6AFF 6C01 8AC4 6B05 6D03 8BD4 120C
```

roms/call.hex - calls a subroutine at 0x20A that sets V0 = 0x2A, returns, loops:
```
220A 6101 1204 0000 0000 602A 00EE
```

roms/key.hex - waits for a key into V3, then loops:
```
F30A 1202
```

## Acceptance

1. `chip8_selftest roms/digit0.hex 6` -> `PC=020A I=0050 SP=0 DT=00 ST=00`;
   display rows 0..4 start with `####`, `#..#`, `#..#`, `#..#`, `####` and the
   rest of every row is dots; rows 5..31 are all dots.
2. `chip8_selftest roms/add.hex 6` -> VA = 00, VB = 08, VF = 00 (8BD4 ran last,
   no carry), PC=020C. After 3 cycles VA = 00 and VF = 01.
3. `chip8_selftest roms/call.hex 4` -> V0 = 2A, V1 = 01, SP=0, PC=0204.
4. `chip8_selftest roms/key.hex 10` -> PC=0200 (still waiting, V3 = 00);
   with `--keys 7` -> V3 = 07 and PC=0202.
5. A ROM of the single word `FFFF` -> prints `BAD OPCODE FFFF at 0200`, exit 2.
6. Drawing the same sprite twice at the same place clears it and sets VF = 1
   (a test ROM written in the test itself, e.g. `6000 F029 D005 D005 1208`).
7. `chip8_load` of 3585 bytes returns -1 (a test of the core through a small
   C driver, or through the self-test refusing the ROM with exit 3).
8. Timers: a ROM `6A3C FA15 1204` run for 2 cycles leaves DT=3C; the
   self-test does not tick timers (they tick only at 60 Hz in the emulator).
9. The SDL emulator starts under Xvfb with roms/digit0.hex and stays up for
   2 seconds; a capture at 1 s shows lit pixels in the top-left 32x40 region
   at scale 8 and nothing lit elsewhere.

## Captures

```json
[
  {"name": "digit0", "cmd": ["{build}/chip8", "--scale", "8", "{project}/roms/digit0.hex"],
   "at": [1.0], "size": [512, 256]}
]
```

## Playtest

- Load a few public-domain games you have (Private files below, or copy
  them to Vulcan): do they play at a sensible speed?
- Keypad mapping feels right; Esc quits cleanly
- The beep sounds while ST > 0 and stops cleanly

<!--
To test against your own ROMs too, add them like this (never committed):

## Private files
- pong: ~/roms/pong-homebrew.ch8

## Provenance
- pong: my own homebrew, I wrote it
-->
