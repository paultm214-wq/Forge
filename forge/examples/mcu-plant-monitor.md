# mcu-plant-monitor

Toolchain: mcu
License: MIT
Prior art: none

<!--
A soil-moisture monitor in three forms that share one tested C core: bare
Cortex-M firmware run under QEMU, a Raspberry Pi Pico build, and a
MicroPython version for boards flashed with MicroPython. It tries the mcu
toolchain:
- Unity tests on the host (with sanitizers) and on an emulated Cortex-M4;
- firmware whose self-test runs under QEMU and returns its result as the
  exit code;
- a Pico SDK build for RP2040 and RP2350 with size limits;
- MicroPython code tested against the fake machine module.
The sensor numbers are the spec's own, not taken from a datasheet.
-->

## Goal

Each moisture reading is a raw 12-bit ADC value. The monitor:
- calibrates it to a percentage;
- smooths it over the last 8 readings;
- decides whether to water, with hysteresis so the pump does not chatter;
- refuses to water more often than a minimum interval allows.

## Constraints

- The core is C17 in `include/moisture.h` and `src/moisture.c`:
  - no heap and no floating point (Cortex-M0 and M3 have no FPU);
  - fixed-width types;
  - all state lives in a caller-owned struct.
- `src/main.c` is the firmware self-test (below). It is left out of the
  unit tests.
- Pico version: `pico/CMakeLists.txt` and `pico/main.c`:
  - it uses the same `moisture.c` (path `../src/moisture.c`);
  - it reads ADC0 on GPIO26 every second and drives a pump on GPIO15;
  - its stdio goes over USB.
- MicroPython version: `src/plant.py`, which uses `machine.ADC(26)` and
  `machine.Pin(15, Pin.OUT)`, with the same rules as the C core.

## Interfaces

`moisture.h`:

```c
typedef struct {
    uint16_t dry_raw, wet_raw;     /* calibration: raw at 0% and 100% */
    uint16_t buf[8]; uint8_t head, count;
    uint8_t on_below, off_above;   /* hysteresis, percent */
    uint32_t min_gap_ms, last_on_ms;
    bool pumping, ever_on;
} moisture_t;

void moisture_init(moisture_t *m, uint16_t dry_raw, uint16_t wet_raw,
                   uint8_t on_below, uint8_t off_above, uint32_t min_gap_ms);
uint8_t moisture_percent(const moisture_t *m, uint16_t raw);  /* clamped 0..100 */
uint8_t moisture_push(moisture_t *m, uint16_t raw);           /* returns smoothed % */
bool moisture_pump(moisture_t *m, uint8_t smoothed, uint32_t now_ms);
```

- **Percent.** Linear between `dry_raw` (0%) and `wet_raw` (100%). Dry
  readings are HIGHER than wet ones, so dry_raw > wet_raw. Integer
  arithmetic rounds to the nearest whole percent and clamps to 0-100.
- **Smoothing.** The integer mean, rounded to nearest, of the last up to 8
  percentages.
- **Pump.**
  - It turns on when the smoothed value is below `on_below`, but only if it
    was never on before or `min_gap_ms` has passed since it last turned on.
    The comparison must stay correct when `now_ms` wraps past 2^32.
  - It turns off when the smoothed value is above `off_above`.
  - Between the two thresholds it keeps its state.

**`main.c`.** It initialises a monitor with dry 3000, wet 1200, on below
30, off above 45 and gap 60000. It then feeds this script:
- 10 readings of 2800, with times 0, 1000, 2000...;
- then readings of 1500 until the pump goes off.

It prints one line per reading, `t=<ms> pct=<smoothed> pump=<0|1>`. It
returns 0 if the pump went on exactly once and off exactly once, and 1
otherwise.

## Acceptance

1. `game.unity_test().assert_ok()` with at least 10 tests in
   `tests/test_moisture.c`, covering:
   - percent at dry, at wet, at the midpoint (2100 gives 50), and beyond
     both ends (clamped);
   - smoothing over fewer and more than 8 readings;
   - the hysteresis band;
   - the minimum gap;
   - the gap across a `now_ms` wrap (last on at 0xFFFFF000, now at
     0x00010000 with a gap of 60000: allowed).
2. `game.unity_test("cortex-m4").assert_ok()`: the same tests on an
   emulated Cortex-M4.
3. For cortex-m0, cortex-m3 and riscv32:
   - `game.mcu_build(t)` is ok with no warnings;
   - `game.mcu_run(...)` exits 0;
   - its output has a `pump=1` line, then a later `pump=0` line.
4. `mcu_build("cortex-m0").size["flash"]` is under 16384 and `["ram"]`
   under 4096.
5. `game.pico_build("pico", path="pico")` and
   `game.pico_build("pico2", path="pico")` are ok. The pico UF2's
   `uf2_info` families are `["rp2040"]`.
6. `game.mpy_test().assert_ok()`, where `tests/test_plant.py` uses
   forge_hw:
   - ADC values that make the soil dry turn GPIO15 on;
   - wet values turn it off;
   - a second dry spell within the gap does not turn it on.
