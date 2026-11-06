# raspberrypi-door-sensor

Toolchain: raspberrypi
License: MIT
Prior art: none

<!--
A Raspberry Pi service for the garage, with everything needed to set it up
from a blank SD card:
- a door reed switch;
- a BME280 temperature/humidity sensor;
- MQTT messages for Home Assistant;
- a systemd unit;
- config.txt and cloud-init user-data;
- a small C helper for the buzzer.
It tries the raspberrypi toolchain:
- gpiozero's MockFactory;
- an I2C sensor behind an interface;
- a cross-compiled helper run under qemu;
- a device-tree overlay;
- cloud-init and config.txt checks;
- an SD card boot partition.
MQTT is only formatted, never sent, in tests.
-->

## Goal

- `doorsensor/` is a Python package run as a service. It reports the door
  state (open/closed, with a 50 ms debounce) and, every 60 seconds, the
  temperature and humidity as JSON MQTT payloads.
- `buzzer.c` beeps a pattern on GPIO 18 through libgpiod.
- The boot files set up I2C, the sensor overlay and the user that runs the
  service.

## Constraints

- **Python 3.11+.** gpiozero `Button(17, pull_up=True, bounce_time=0.05)`
  is the reed switch: closed means pressed.
- **The sensor** is read through a `SensorReader` protocol with `read() ->
  (celsius, humidity)`:
  - the real one uses smbus2 on bus 1 at address 0x76;
  - tests pass a fake.
- **Readings.** Temperature and humidity are rounded to 0.1. A reading
  that raises OSError is skipped and counted in a `sensor_errors` field.
- **MQTT** goes through a `Publisher` protocol with `publish(topic, payload:
  str, retain: bool)`. The real one would wrap paho-mqtt; it is NOT
  included here.
  - Door: topic `garage/door`, payload `{"state": "open"|"closed", "at":
    ISO-8601 UTC}`, retained.
  - Climate: topic `garage/climate`, payload `{"temperature": t,
    "humidity": h, "sensor_errors": n}`.
- **`buzzer.c`** uses libgpiod v2 on the chip given by `--chip`
  (default `/dev/gpiochip0`). `buzzer --pattern 3x100` means three 100 ms
  beeps. Bad arguments exit 2. `--dry-run` prints the on/off schedule
  without opening the chip.
- **Boot files:**
  - `boot/config.txt` turns on I2C (`dtparam=i2c_arm=on`) and loads
    `dtoverlay=garage-bme280`;
  - `overlays/garage-bme280-overlay.dts` adds the sensor at 0x76 on i2c1
    (compatible "bosch,bme280");
  - `boot/user-data` (cloud-config) creates user `garage` in the gpio and
    i2c groups and enables `doorsensor.service`.
- **`systemd/doorsensor.service`** has Type=simple, User=garage,
  `ExecStart=/usr/bin/python3 -m doorsensor`, Restart=on-failure,
  RestartSec=5 and WantedBy=multi-user.target.

## Acceptance

1. Python tests with `Device.pin_factory = MockFactory()`, a fake reader
   and a fake publisher, driving the clock directly (no sleeps):
   - **Opening.** Driving pin 17 high publishes one retained
     `{"state": "open"}`.
   - **Bounce.** A 20 ms bounce back to low publishes nothing.
   - **Closing.** A real close publishes `closed`.
   - **Climate.** After 60 s of fake time there is one climate message
     with values rounded to 0.1.
   - **Sensor errors.** A reader that raises twice gives `sensor_errors:
     2` in the next message.
2. `game.rpi_build("arm64", sources=["src/buzzer.c"], libs=["gpiod"],
   out="buzzer")` is ok, with no warnings.
3. Running the helper with `game.rpi_run`:
   - `buzzer --dry-run --pattern 3x100` prints 6 lines alternating
     `on`/`off` with times 0, 100, 200, 300, 400, 500;
   - `--pattern x` exits 2.
4. `game.dt_overlay("overlays/garage-bme280-overlay.dts").ok`, and the
   decompiled overlay contains "bosch,bme280" and `reg = <0x76>`.
5. These checks all come back empty:
   - `game.cloud_init_schema("boot/user-data")`;
   - `game.pi_config_problems("boot/config.txt")`;
   - `game.systemd_verify()`;
   - `game.shellcheck()`, if there are any scripts.
6. `game.disk_image` with a 256 MB FAT32 `bootfs` partition (holding
   config.txt, user-data and `overlays/garage-bme280.dtbo`) and an ext4
   rootfs: `game.fat_files(img, 1)` lists those three.
