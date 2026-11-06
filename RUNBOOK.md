# Local AI Stack Runbook

**Apollo** — 192.168.50.96 — Ubuntu 26.04 LTS
5700G · RX 9070 XT (gfx1201) · RX 9060 XT (gfx1200) · 64GB DDR4

**HAOS VM** — 192.168.50.162 — on the Minisforum UM890 Pro (Proxmox)
Home Assistant + Frigate + Arc A750 passthrough

> Revision 2. Rewritten after the first build. Everything below was either
> verified working or diagnosed from a failure — it is not a plan.

---

## 1. Hardware notes that cost real time

**The two GPUs are different architectures.** 9070 XT = gfx1201, 9060 XT = gfx1200. Every ROCm package and the llama.cpp build need both targets. A gfx1201-only build leaves the second card invisible.

**Device enumeration**, confirmed by `rocm-smi`:

| Device | Card | DID | Power cap |
|---|---|---|---|
| 0 | RX 9070 XT | 0x7550 | 304W |
| 1 | RX 9060 XT | 0x7590 | 182W |
| 2 | Vega iGPU | 0x1638 | — |

Device 0 is the faster card, so `--tensor-split 3,2` weights correctly. The iGPU enumerates as a third ROCm agent and must never be selected — hence `--device ROCm0,ROCm1`.

**The 5700G is PCIe 3.0 only.** 9070 XT at Gen3 x16; 9060 XT at Gen3 x4 through Oculink. Fine for inference — only activations cross the bus during generation.

**Power is not a constraint.** 1000W main PSU, plus the Oculink enclosure has its own 800W supply. I chased this theory hard and it was wrong twice.

**The 990 Pro was bad.** Btrfs forced the root filesystem read-only after metadata corruption. It passed SMART but repeatedly failed OS installs — a marginal-drive profile. Replaced.

**The RAM is over spec.** 4×16GB dual-rank at DDR4-3600 is 2DPC 2R, and MSI's own table caps that at 3466 — with a Vermeer-class CPU, not Cezanne's weaker controller. Two separately-bought 2×16 kits were never validated as a four. See §7.

---

## 2. What the stack actually looks like

```
  Apollo 192.168.50.96                     HAOS 192.168.50.162
  ─────────────────────                    ───────────────────
  llama-server     :8080  ←──────────────  llama.cpp integration
    Qwen3.8-27B UD-Q4_K_XL                 Frigate GenAI (llamacpp)
    + mmproj (vision)                      Wyoming STT/TTS
    MTP speculative decoding
                                           Frigate add-on (Full Access)
  Open WebUI       :3000                     Arc A750 → OpenVINO @ 10ms
  SearXNG          :8888                     9 cameras, go2rtc restream
  wyoming-whisper  :10300
  wyoming-piper    :10200                  Voice PE (on-device wake word)
```

**~30 tok/s** generation, 82% draft acceptance, ~920 tok/s prefill on a 40K prompt.

---

## 3. llama-server

Run as your **normal user**, never sudo. The script escalates where needed. Running it as root puts the model, API key and systemd *user* service under `/root`.

Key flags and why:

| Flag | Reason |
|---|---|
| `--tensor-split 3,2` | Weighted to the faster, higher-bandwidth card |
| `--device ROCm0,ROCm1` | Excludes the iGPU |
| `--spec-type draft-mtp` | MTP head ships inside the Unsloth GGUF; ~20 → 30 tok/s |
| `--spec-draft-n-max 2` | Dense-model recommendation; MoE favours 3 |
| `--ctx-checkpoints 0` | **Workaround for a GPU page fault — see below** |
| `--chat-template-kwargs '{"reasoning_effort":"low"}'` | Default is `xhigh`; unusable for voice |
| `--alias qwen3.8-27b` | Clean model name in HA and Open WebUI |
| `--api-key-file` | LAN exposure without auth = free GPU for any IoT device |

**`--no-mmap` was removed** — this build rejects the bare flag. Minor loss; with `-ngl 999` the weights are in VRAM.

### The checkpoint crash

After ~1h43m and a thousand tasks, the server took a GPU page fault:

```
common_prompt_checkpoint::update_tgt
  → llama_context::state_seq_get_data
    → ggml_backend_cuda_buffer_get_tensor → hipMemcpyAsync
      → Memory access fault by GPU node-1
```

It died pulling KV state **off the second device** while creating a context checkpoint. Not memory pressure — the crash came six minutes after a clean completion. `--ctx-checkpoints 0` fixes it.

The cost is prefix reuse across similar prompts, which matters most for the HA agent, where every request carries the same large system prompt. Retry the default (32) after a llama.cpp update.

A gfx1200+gfx1201 tensor-split is almost certainly untested upstream. Worth reporting — the trace is clean and reproducible.

### Context sizing

VRAM is lopsided at `3,2` — card 0 fills first while card 1 keeps headroom. `-c 49152` with q8_0 KV cache is comfortable. Measure under load, not idle; compute buffers only materialise during prefill.

Raise context and batch size **separately**. Doing both at once is how you can't tell which hit the ceiling.

### Operating it

```bash
systemctl --user restart llama-server        # after editing run_llm.sh
systemctl --user enable --now llama-server   # once; starts at boot thereafter
journalctl --user -u llama-server -f
curl -s http://localhost:8080/v1/models -H "Authorization: Bearer $(cat ~/.llama-api-key)"
```

Confirm `loginctl show-user "$USER" --property=Linger` says `yes`, or the service dies when you log out.

**Cold start takes ~5 minutes** for 18GB from a cold page cache. `TimeoutStartSec=1800` prevents systemd killing a load mid-flight, which would look exactly like a crash loop.

---

## 4. Things that fail silently

**ROCm libraries aren't on the linker path.** Works by hand, exits 127 under systemd — `libhipblas.so.3: cannot open shared object file`. systemd doesn't read your shell profile. Fixed system-wide via `/etc/ld.so.conf.d/rocm.conf` + `ldconfig`.

**ufw blocks the Docker bridge.** LAN rules cover `192.168.50.0/24`; containers arrive from `172.17.x.x`. Open WebUI shows an empty model list while `curl` from the host works perfectly, and nothing logs a refusal. Needs `sudo ufw allow from 172.16.0.0/12 to any port 8080 proto tcp`.

**`huggingface_hub[cli]`** — the extra no longer exists in 1.x; the `hf` command is in the base package. Harmless warning, but the download can also die inside the Xet transfer layer; `HF_HUB_DISABLE_XET=1` falls back to plain HTTP and resumes more reliably.

**A volatile journal erases its own evidence.** If `/var/log/journal` doesn't exist, every crash wipes the logs that would explain it. Enable persistence *before* anything can go wrong — it's three lines and it is the difference between diagnosing a crash and guessing at it.

---

## 5. Home Assistant

**Conversation agent:** Settings → Devices & Services → **llama.cpp** (first-party since 2026.8). URL `http://192.168.50.96:8080/v1`, key from `~/.llama-api-key`. Enable Control Home Assistant, then expose entities deliberately — a 27B given 600 entities produces a huge system prompt and slow, confused answers.

The native integration can't set `chat_template_kwargs`. If you want per-integration reasoning control, the HACS integration `skye-harris/hass_local_openai_llm` exposes it.

**Voice:** the Voice PE does wake-word detection **on-device**, so the `wyoming-openwakeword` container is redundant — stop it, leave it in the compose file for future satellites. You still need Whisper (:10300) and Piper (:10200) via Wyoming Protocol.

Consider the hybrid agent setting: local intent matching first, LLM fallback. "Turn off the kitchen lights" resolves instantly; "is anyone in the back yard?" goes to the model.

---

## 6. Frigate

Running as the **Frigate NVR (Full Access)** add-on — the standard add-on's Protection Mode blocks `/dev/dri`. Arc A750 passed through to the HAOS VM: q35, OVMF, ballooning disabled, Above 4G Decoding and ReBAR on.

**Detection: 10ms on the Arc**, versus 50–150ms on CPU.

Three config traps, all of which cost a debugging cycle:

**`environment_vars` does not feed config substitution.** It sets environment for the ffmpeg/go2rtc subprocesses only. `{FRIGATE_MQTT_USER}` raises `KeyError` at validation. Frigate's maintainers state plainly that moving secrets out of the config file isn't supported for add-ons. Credentials are inline; that file is a secret.

**The bundled OpenVINO model needs an explicit `model:` block.** Without it, Frigate sizes the detection shared-memory segment for its *default* model, the sizes don't match, and every camera process dies with `TypeError: buffer is too small for requested array`. Detect FPS still reads 5 because the capture threads are fine — it's the processors that are dead. `labelmap_path` isn't optional either; omit it and people get reported as bicycles.

**`record.retain` was removed in 0.17.** Tiered now: `continuous` → `motion` → `alerts`/`detections`. Note `alerts` and `detections` still use a nested `retain:` block; only the top-level one went away.

**GenAI is a map of named providers** in 0.18, and there's a dedicated `llamacpp` provider — use it rather than `openai`, which historically ignored `base_url` and sent everything to api.openai.com. Note: **no `/v1` suffix** for this provider, unlike everything else in the stack. 0.18 also added a Chat interface with a tool-calling agent over your camera data.

**Storage.** ~10.8 GB per Mbps per camera per day. Nine cameras at 4 Mbps is ~390 GB/day of continuous recording — and H.264 costs 30–50% more than the H.265 you were running. `continuous: days: 1` is deliberately conservative. Move the HAOS data disk to something larger *before* recording fills it; Frigate's cleanup protects Frigate, not Home Assistant's database.

**Camera gotchas:** `Multi-layer HEVC coding is not implemented` means the camera's main stream uses a codec ffmpeg can't parse — set H.264 and disable any "H.265+" smart codec. A go2rtc 404 on `127.0.0.1:8554` means the stream isn't registered, usually because go2rtc couldn't reach the camera at startup.

---

## 7. Memory — unresolved

Four dual-rank 16GB DIMMs at 3600 exceeds MSI's stated 2DPC 2R limit of 3466, on a controller rated for 3200. Two separately-purchased kits were never binned together.

Start here on the rebuild:

| Setting | Value |
|---|---|
| A-XMP | **Disabled** (XMP also applies aggressive secondaries) |
| DRAM Frequency | **2933** |
| FCLK | **1466** (1:1 — mismatched FCLK is its own instability source) |
| DRAM Voltage | 1.35V — do not exceed; the manual warns ≤1.35V protects the CPU |
| Timings | Auto |

Fall back to 2666/FCLK 1333 if needed. **Run MemTest86 overnight** before rebuilding — four passes. You lose nothing measurable: weights live in VRAM, and RAM speed only affects load times.

Keeping 64GB is justified — the 18GB model file stays in page cache, which is what makes restarts fast. If instability persists, a single matched 2×32GB kit is 1DPC instead of 2DPC and gives real headroom.

---

## 8. RGB

USB only. OpenRGB's MSI Mystic Light SMBus driver is disabled upstream after it permanently bricked controllers on some boards, so the installer **blacklists `i2c-dev` and `i2c-piix4`** — no i2c nodes means the motherboard detectors have nothing to scan. Stronger than disabling detectors by name.

- **Airgoo AG-DRGB08** — A1 light bar (30 LEDs), A6 front fan, A7 top, A8 rear
- **AMD Wraith Prism** — USB, three fixed zones

The light bar renders as a **bar graph**: LEDs fill with load, each lit LED coloured for its own position, so the bar runs green→blue→red along its length. Fans and the Wraith take a flat colour.

Power draw drives the signal, not `gpu_busy_percent` — decode is memory-bandwidth bound, so busy% sits near 100% whenever the model emits anything and would pin the lights red permanently.

Airgoo point at the **newest experimental** OpenRGB build. If detection finds nothing, that's the first thing to change.

---

## 9. Outstanding

- MemTest86 before rebuilding; extended SMART on the old 990 Pro in a USB enclosure
- Rotate the camera password — find-and-replace `CHANGE_ME_camera_password`
- Mosquitto `logins:` entry, then fill `FRIGATE_MQTT_PASSWORD`
- Motion masks on front_yard and back_yard
- Enable Frigate enrichments **one at a time**, watching the System page
- **Put all of this in git** on the Proxmox box — the configs are the accumulated result of a long debugging session and are worth more than the model file
- [stew675/llama-cpp-rdna-boosts](https://github.com/stew675/llama-cpp-rdna-boosts) — RDNA4 patches, tested on this exact model and quant; block 15 frees ~800 MiB/GPU. Only once the machine is stable.

---

## 10. Files

| File | Purpose |
|---|---|
| `install-llm-server.sh` | Phased, idempotent. `base rocm verify build model server services agents firewall` |
| `frigate-config-haos-addon.yml` | `/addon_configs/<slug>/config.yml` — must be `config.yml` |
| `install-rgb-monitor.sh` | `deps harden openrgb detect udev monitor service` |
| `llm-rgb-monitor.py` | The daemon |
| `collect-crash-logs.sh` | Read-only diagnostic collector |
