# homeassistant-lab-monitor

Toolchain: homeassistant
License: MIT
Prior art: none

<!--
The lab in Home Assistant: a custom integration that shows whether each
lab service answers, set up from the UI (a config flow), with an
automation that raises a notification when one goes down, and an ESPHome
sensor for the rack's temperature. It tries the homeassistant toolchain:
pytest-homeassistant-custom-component's hass fixture and MockConfigEntry,
a config flow test, a DataUpdateCoordinator with its fetch patched (no
network), Home Assistant's own config check, and ESPHome validation.
-->

## Goal

`custom_components/lab_monitor`: the user adds a service (name and URL)
from Settings > Devices & services. The integration polls the URL every
60 seconds and has one binary sensor per entry (device class
connectivity, on when the URL answers with HTTP status below 400).
`config/` holds a Home Assistant configuration with an automation using
it, and `esphome/rack-temp.yaml` a temperature sensor for the rack.

## Constraints

- Domain `lab_monitor`. manifest.json: name "Lab Monitor", version
  "0.1.0", config_flow true, iot_class "local_polling", requirements [],
  dependencies [], codeowners [], documentation
  "https://example.invalid/lab_monitor".
- HTTP through Home Assistant's shared session
  (`homeassistant.helpers.aiohttp_client.async_get_clientsession(hass)`),
  10 second timeout. The fetch is one module-level coroutine
  `async_probe(session, url) -> int | None` (the status, or None when the
  request fails) so tests patch it.
- `DataUpdateCoordinator` (passing `config_entry=entry`) with
  `update_interval=timedelta(seconds=60)`; the entity is a
  `CoordinatorEntity` + `BinarySensorEntity` with `_attr_has_entity_name
  = True`, unique_id `<entry_id>_online`, device class CONNECTIVITY, and
  extra state attributes `url` and `status` (the last HTTP status or
  None).
- Config flow (`config_flow.py`, `ConfigFlow, domain="lab_monitor"`):
  one `user` step with `name` and `url` (voluptuous schema); a URL that
  does not start with http:// or https:// shows the form again with
  errors {"url": "invalid_url"}; the same URL twice aborts with
  "already_configured" (unique id = the URL). Title = the name.
  strings.json and translations/en.json have the step, the error and the
  abort texts.
- `async_setup_entry` forwards to the binary_sensor platform;
  `async_unload_entry` unloads it.
- The config in `config/configuration.yaml` must pass Home Assistant's
  check with the integration present: `homeassistant:` (name Lab, metric,
  time_zone America/Los_Angeles), `default_config:` is NOT used (it pulls
  integrations the check cannot load offline), and an `automation:` list
  with one automation: trigger `state` on `binary_sensor.coder_online`
  `to: "off"` `for: "00:02:00"`, action `persistent_notification.create`
  with title "Lab" and message "{{ trigger.to_state.name }} is down".
- `esphome/rack-temp.yaml`: name `rack-temp`, esp32 board `esp32dev`,
  logger, api, a `dht` sensor (model DHT22) on GPIO4 with temperature
  "Rack Temperature" and humidity "Rack Humidity", update_interval 60s,
  wifi with `ssid: !secret wifi_ssid` and `password: !secret
  wifi_password`, and `esphome/secrets.yaml` with dummy values (never
  real credentials).

## Acceptance

1. pytest (`tests/`, conftest enabling custom integrations), with
   `async_probe` patched:
   - config flow: the form shows; a good name/url creates an entry titled
     with the name; `ftp://x` shows the form with invalid_url; a second
     entry with the same url aborts already_configured;
   - a MockConfigEntry set up with the probe returning 200: one binary
     sensor, state "on", attributes url and status 200;
   - the probe returning 503, then None, after
     `async_fire_time_changed(hass, utcnow() + timedelta(seconds=61))`:
     the state is "off" both times, status 503 then None;
   - unloading the entry leaves the entity unavailable.
2. `game.ha_test().assert_ok()` with at least 6 tests.
3. `c = game.ha_check_config("config")`: c.ok, and "automation" in
   c.components.
4. `game.esphome_config("esphome/rack-temp.yaml") == []`.
5. `game.yamllint() == []` (custom tags like !secret are fine).
