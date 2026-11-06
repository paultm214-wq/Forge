#!/usr/bin/env bash
#
# install-rgb-monitor.sh
#
# Installs OpenRGB and the llm-rgb-monitor daemon, which colours RGB lighting
# according to how hard the local llama.cpp server is working.
#
#   green   idle / light use
#   blue    actively generating
#   red     sustained heavy load
#   amber   llama-server unreachable (slow pulse)
#
# TARGET HARDWARE
#   Airgoo AG-DRGB08 8-port USB ARGB controller
#     A1  AG-NEON-DLS30-1 addressable light bar
#     A6  front fan
#     A7  top fan
#     A8  rear fan
#     A2-A5 unpopulated
#   AMD Wraith Prism CPU cooler (USB, 3 zones: logo / fan / ring)
#
# Both are USB devices, so the SMBus hardening below stays intact - nothing
# here needs i2c access.
#
# ===========================================================================
# WHY THIS VERSION IS SAFE AND THE PREVIOUS ONE WAS NOT
# ===========================================================================
#
# The earlier revision of this script drove the motherboard's JRAINBOW header
# through OpenRGB's SMBus path. That path has a body count: OpenRGB's MSI
# Mystic Light SMBus driver is disabled upstream because it permanently
# bricked RGB controllers on some MSI boards.
#
# A USB controller removes that entire failure class. It speaks a documented
# USB protocol, it is hot-pluggable, and nothing it does touches the SMBus.
#
# This script therefore goes further and actively PREVENTS SMBus access:
#   - it does NOT load i2c-dev or i2c-piix4
#   - it does NOT add acpi_enforce_resources=lax
#   - it REMOVES those settings if a previous run of the old script added them
#
# Without /dev/i2c-* present, OpenRGB's motherboard detectors find nothing to
# scan. That is a stronger guarantee than disabling detectors by name in
# OpenRGB's config, because it does not depend on getting the names right.
#
# ===========================================================================
# BEFORE RUNNING - PHYSICAL SETUP
# ===========================================================================
#
# 1. Move ONLY the 3-pin 5V ARGB leads from JRAINBOW to the controller.
#    ARGB fans have two separate cables: a 4-pin PWM/DC lead for fan power
#    and speed, and a 3-pin 5V ARGB lead for the lights. The fan leads stay
#    on the motherboard headers so fan curves keep working.
#
# 2. The controller needs SATA POWER from the PSU. You said no SATA devices
#    are connected, so you may not have a SATA power lead routed - check your
#    PSU's modular cable set before assuming. This is power only, not data.
#
# 3. Connect the controller's USB 2.0 cable to a 9-pin USB 2.0 header on the
#    B550M-VC. This is the data path - without it the controller powers the
#    LEDs but nothing can talk to it.
#
# 4. DO NOT leave any lead on JRAINBOW. Two controllers driving one strip is
#    a documented cause of flicker and stuck states.
#
# 5. WRAITH PRISM: connect its micro-USB lead to a USB 2.0 header on the
#    board. Without it the cooler still lights up from its own power and
#    cycles a default rainbow, which looks exactly like a software failure.
#
# 6. WRAITH PRISM: the ring has a small physical switch selecting between
#    static and rainbow default modes. It does not block USB control, but if
#    the ring ignores OpenRGB while the logo and fan respond, that switch is
#    the first thing to check.
#
# 7. You now have TWO USB 2.0 headers in use (controller + cooler). The
#    B550M-VC has two 9-pin USB 2.0 headers (JUSB3, JUSB4), each carrying two
#    ports, so this fits - but check nothing else is already occupying them.
#
# ===========================================================================
# OPENRGB VERSION MATTERS FOR THIS DEVICE
# ===========================================================================
# Airgoo's own FAQ points users to the NEWEST EXPERIMENTAL OpenRGB build, not
# the latest stable release. Distro packages are typically older still. If
# the detect phase does not see the controller, that is the first thing to
# change - grab an experimental AppImage from openrgb.org rather than
# assuming the hardware is faulty.
#
# ===========================================================================
#
# Usage:
#   chmod +x install-rgb-monitor.sh
#   ./install-rgb-monitor.sh                # all phases
#   ./install-rgb-monitor.sh detect         # just probe the hardware
#   ./install-rgb-monitor.sh udev           # write a rule for the device
#
# Phases: deps harden openrgb detect udev monitor service

set -euo pipefail

# ---- config ---------------------------------------------------------------

RGB_DIR="${RGB_DIR:-$HOME/llm-rgb}"
VENV="$RGB_DIR/venv"
MONITOR_SRC="${MONITOR_SRC:-$(dirname "$(readlink -f "$0")")/llm-rgb-monitor.py}"
MONITOR_DST="$RGB_DIR/llm-rgb-monitor.py"
CONF="$RGB_DIR/monitor.env"
SDK_PORT=6742

# Substrings matched against OpenRGB device names. Confirm the exact strings
# with the detect phase - vendors rename things between firmware revisions.
# "Airgoo" may appear as a generic name if OpenRGB has not learned this
# controller by name yet.
RGB_DEVICES_DEFAULT="Airgoo,Wraith"

# Known USB IDs, for the udev phase. VERIFY against lsusb rather than
# trusting these - the Wraith Prism is a Cooler Master design and its ID has
# varied across production runs.
WRAITH_ID="2516:0051"        # AMD Wraith Prism (Cooler Master vendor ID)

# ---- helpers --------------------------------------------------------------

BOLD=$'\033[1m'; RED=$'\033[31m'; YEL=$'\033[33m'; GRN=$'\033[32m'; RST=$'\033[0m'
say()  { printf '%s==> %s%s\n' "$BOLD" "$*" "$RST"; }
info() { printf '    %s\n' "$*"; }
warn() { printf '%s    WARNING: %s%s\n' "$YEL" "$*" "$RST"; }
die()  { printf '%s    ERROR: %s%s\n' "$RED" "$*" "$RST" >&2; exit 1; }
ok()   { printf '%s    OK: %s%s\n' "$GRN" "$*" "$RST"; }
have() { command -v "$1" &>/dev/null; }

confirm() {
    if [ -t 0 ]; then read -rp "    ${1:-Press Enter to continue, Ctrl+C to abort}... "
    else info "(non-interactive; continuing)"; fi
}

# ---- phase: deps ----------------------------------------------------------

phase_deps() {
    say "[deps] Base packages and Python environment"

    if [ "$(id -u)" -eq 0 ]; then
        die "Do not run this with sudo. It calls sudo where root is actually
    needed. Running the whole thing as root puts the venv, the daemon and the
    systemd USER service under /root, where your login session cannot reach
    them."
    fi

    sudo apt update
    # libfuse2t64: the OpenRGB AppImage needs FUSE 2, and Ubuntu 26.04 ships
    # FUSE 3 by default ("dlopen(): error loading libfuse.so.2").
    sudo apt install -y usbutils python3-venv python3-pip curl libfuse2t64

    # Note what is deliberately NOT installed: i2c-tools. Nothing here needs
    # SMBus access, and not having the tooling makes it harder to reach for
    # by accident later.

    say "Creating Python environment"
    mkdir -p "$RGB_DIR"
    [ -d "$VENV" ] || python3 -m venv "$VENV"
    # The daemon needs only the standard library: it drives and lists devices
    # through the openrgb CLI. (openrgb-python was used for the SDK path,
    # which cannot write to OpenRGB 1.0's protocol 6.)
    ok "venv ready at $VENV"
}

# ---- phase: harden --------------------------------------------------------

phase_harden() {
    say "[harden] Removing SMBus access so OpenRGB cannot reach the chipset"

    local changed=false

    # Undo anything the previous, SMBus-based version of this script set up.
    if [ -f /etc/modules-load.d/openrgb.conf ]; then
        sudo rm -f /etc/modules-load.d/openrgb.conf
        ok "Removed /etc/modules-load.d/openrgb.conf (i2c-dev, i2c-piix4)"
        changed=true
    fi
    if [ -f /etc/udev/rules.d/60-i2c-openrgb.rules ]; then
        sudo rm -f /etc/udev/rules.d/60-i2c-openrgb.rules
        ok "Removed the i2c udev rule"
        changed=true
    fi
    if grep -q 'acpi_enforce_resources=lax' /etc/default/grub 2>/dev/null; then
        warn "acpi_enforce_resources=lax is on your kernel command line."
        warn "Nothing here needs it. Removing it."
        sudo sed -i 's/ *acpi_enforce_resources=lax//' /etc/default/grub
        sudo update-grub
        warn "Reboot required for that to take effect."
        changed=true
    fi

    # Blacklist the SMBus modules outright.
    sudo tee /etc/modprobe.d/blacklist-openrgb-i2c.conf >/dev/null <<'EOF'
# Installed by install-rgb-monitor.sh
#
# RGB is driven entirely over USB here. Blocking the SMBus modules means
# OpenRGB's motherboard detectors have no /dev/i2c-* nodes to scan, which
# removes the failure class that has permanently bricked RGB controllers on
# some MSI boards.
#
# Delete this file only if you have a deliberate reason to allow SMBus access
# and understand that risk.
blacklist i2c-dev
blacklist i2c-piix4
EOF
    ok "Blacklisted i2c-dev and i2c-piix4"

    if lsmod | grep -qE '^i2c_dev|^i2c_piix4'; then
        warn "i2c modules are currently loaded from an earlier session."
        warn "They will not load after a reboot. To unload now:"
        warn "    sudo modprobe -r i2c_piix4 i2c_dev"
        warn "Until then, avoid running OpenRGB detection."
    else
        ok "No i2c modules currently loaded."
    fi

    [ "$changed" = true ] && warn "Reboot recommended to fully apply."
    return 0
}

# ---- phase: openrgb -------------------------------------------------------

phase_openrgb() {
    say "[openrgb] OpenRGB"

    # NOT the distro package. Ubuntu's openrgb is far too old for the Airgoo
    # AG-DRGB08 - an October 2025 build could not see the controller at all.
    # A current experimental AppImage is required.
    if dpkg -s openrgb >/dev/null 2>&1; then
        warn "The distro 'openrgb' package is installed. It is too old for the"
        warn "Airgoo controller and shadows nothing useful - removing it."
        sudo apt remove -y openrgb
        hash -r
    fi
    if ! have openrgb; then
        info "Download the newest EXPERIMENTAL AppImage from https://openrgb.org/"
        info "then:"
        info "    chmod +x OpenRGB*.AppImage"
        info "    sudo mv OpenRGB*.AppImage /usr/local/bin/openrgb"
        die "Install OpenRGB, then re-run: $0 openrgb"
    fi

    # offscreen: OpenRGB is a Qt app and aborts with no output when it has no
    # display - over SSH, or under systemd.
    local ver year
    ver=$(QT_QPA_PLATFORM=offscreen openrgb --version 2>&1 || true)
    if grep -q "libfuse.so.2" <<< "$ver"; then
        die "The AppImage cannot load FUSE 2. Run the deps phase first ($0 deps)."
    fi
    ok "$(command -v openrgb): $(grep -m1 -iE '^OpenRGB' <<< "$ver" || echo 'version unknown')"
    year=$(grep -m1 -i 'build date' <<< "$ver" | grep -oE '20[0-9]{2}' | head -n1 || true)
    if [ -n "$year" ] && [ "$year" -lt 2026 ]; then
        warn "This build dates from $year. The Airgoo AG-DRGB08 needs a 2026 or"
        warn "later build - replace it with a current experimental AppImage."
    fi

    # No OpenRGB SDK server. The daemon lists and drives devices through the
    # openrgb CLI, and a running server would hold the same USB devices.
    # Remove one left behind by an earlier version of this script.
    if [ -f /etc/systemd/system/openrgb-server.service ]; then
        sudo systemctl disable --now openrgb-server.service >/dev/null 2>&1 || true
        sudo rm -f /etc/systemd/system/openrgb-server.service
        sudo systemctl daemon-reload
        ok "Removed the old openrgb-server unit - it is no longer used."
    fi
}

# ---- phase: detect --------------------------------------------------------

phase_detect() {
    say "[detect] Is the controller visible?"

    info "USB devices (looking for the ARGB controller):"
    lsusb | sed 's/^/      /'
    echo
    info "If you cannot tell which line is the controller, unplug its USB"
    info "cable, run 'lsusb > /tmp/before', plug it back in, then:"
    info "    lsusb > /tmp/after; diff /tmp/before /tmp/after"
    echo

    say "Confirming SMBus is genuinely unreachable"
    if [ -e /dev/i2c-0 ] || ls /dev/i2c-* &>/dev/null; then
        warn "/dev/i2c-* nodes still exist - the harden phase needs a reboot."
    else
        ok "No /dev/i2c-* nodes. OpenRGB cannot touch the chipset."
    fi
    echo

    say "GPU sensors the monitor will use"
    "$VENV/bin/python" "$MONITOR_DST" --probe 2>/dev/null \
        || "$VENV/bin/python" "$MONITOR_SRC" --probe || true
    echo

    say "Devices OpenRGB can drive"
    set +e
    # Lists through the openrgb CLI - the same path the daemon uses to drive
    # the devices, so anything shown here is controllable.
    "$VENV/bin/python" "${MONITOR_DST:-$MONITOR_SRC}" --list 2>&1
    local rc=$?
    set -e

    if [ "$rc" -eq 2 ]; then
        echo
        warn "OpenRGB found no controllable devices. In order of likelihood:"
        warn "  1. OpenRGB is too old. Airgoo point at the newest EXPERIMENTAL"
        warn "     build - try that AppImage before anything else."
        warn "  2. The USB 2.0 header cable is not connected, or is on a"
        warn "     header that is not wired. LEDs can be lit from SATA power"
        warn "     with no data connection at all."
        warn "  3. udev rules missing, so the daemon (running as you) cannot"
        warn "     open the device. Compare root with your user:"
        warn "       sudo env QT_QPA_PLATFORM=offscreen openrgb --noautoconnect --list-devices"
        warn "       QT_QPA_PLATFORM=offscreen openrgb --noautoconnect --list-devices"
        warn "     If only root sees it, run the udev phase for that device."
    fi

    echo
    say "LED COUNTS - the thing most likely to look broken"
    cat <<'EOF'
    A generic ARGB controller cannot detect how many LEDs are on each port.
    OpenRGB exposes each port as a RESIZABLE zone defaulting to some
    arbitrary count, so until you set the real numbers you get partial
    lighting: a light bar lit for six LEDs and dark for the rest, or a fan
    ring where only half the diodes respond.

    Nothing in this script can discover those counts - only you can see them.

      1. Count the LEDs on each fixture, or check its spec:
           A1  AG-NEON-DLS30-1 light bar
           A6  front fan
           A7  top fan
           A8  rear fan
      2. Open the OpenRGB GUI once on a desktop session, or edit
         ~/.config/OpenRGB/sizes.ors, and set each zone's size.
      3. Leave A2-A5 at zero so the controller does not waste time driving
         ports with nothing on them.
      4. Save the configuration. OpenRGB persists zone sizes across restarts,
         so this is a one-time job.

    The Wraith Prism needs none of this - its three zones (logo, fan, ring)
    are fixed and known to OpenRGB.
EOF
}

# ---- phase: udev ----------------------------------------------------------

phase_udev() {
    say "[udev] Granting non-root access to the controller"

    info "Find the controller's ID in the form vvvv:pppp from lsusb, then"
    info "pass it as VID:PID. Example:"
    info "    $0 udev 1b1c:0c1a"
    echo

    local idpair="${1:-}"
    if [ -z "$idpair" ]; then
        lsusb | sed 's/^/      /'
        echo
        info "Known ID for the Wraith Prism: $WRAITH_ID (verify above)."
        info "Run this phase once per device:"
        info "    $0 udev <airgoo-vid:pid>"
        info "    $0 udev $WRAITH_ID"
        echo
        read -rp "    Enter a VID:PID (or Ctrl+C to skip): " idpair
    fi

    if ! [[ "$idpair" =~ ^[0-9a-fA-F]{4}:[0-9a-fA-F]{4}$ ]]; then
        die "Expected the form vvvv:pppp, got '$idpair'"
    fi

    local vid="${idpair%%:*}" pid="${idpair##*:}"
    # One file per device, so running this phase twice does not overwrite the
    # first device's rule with the second's.
    sudo tee "/etc/udev/rules.d/61-argb-${vid,,}-${pid,,}.rules" >/dev/null <<EOF
# RGB device ${vid,,}:${pid,,} - installed by install-rgb-monitor.sh
SUBSYSTEM=="usb", ATTRS{idVendor}=="${vid,,}", ATTRS{idProduct}=="${pid,,}", MODE="0666"
SUBSYSTEM=="hidraw", ATTRS{idVendor}=="${vid,,}", ATTRS{idProduct}=="${pid,,}", MODE="0666"
EOF
    sudo udevadm control --reload-rules && sudo udevadm trigger
    ok "Rule written for ${vid,,}:${pid,,}"
    info "'udevadm trigger' applies the rule to devices already connected. If"
    info "the device still is not accessible as your user, unplug and replug it."
}

# ---- phase: monitor -------------------------------------------------------

phase_monitor() {
    say "[monitor] Installing the daemon"

    [ -f "$MONITOR_SRC" ] || die "llm-rgb-monitor.py not found next to this script.
    Expected at: $MONITOR_SRC
    Set MONITOR_SRC=/path/to/llm-rgb-monitor.py and re-run."

    mkdir -p "$RGB_DIR"
    install -m 0755 "$MONITOR_SRC" "$MONITOR_DST"
    # Point the shebang at the venv, so running the daemon directly uses the
    # same Python as the service. With `#!/usr/bin/env python3` it picked up
    # the system Python instead.
    sed -i "1s|^#!.*|#!$VENV/bin/python|" "$MONITOR_DST"
    ok "Installed $MONITOR_DST"
    info "It drives devices through the openrgb CLI, by exact device name."

    # The key this machine's llama-server actually uses. Apollo (the coder)
    # has ~/.coder-api-key; Gemini has ~/.llama-api-key. Writing one fixed
    # name everywhere left Apollo's daemon sending no key at all.
    local keyfile="$HOME/.llama-api-key"
    if [ -f "$HOME/.coder-api-key" ]; then keyfile="$HOME/.coder-api-key"; fi

    if [ ! -f "$CONF" ]; then
        cat > "$CONF" <<EOF
# llm-rgb-monitor configuration. Restart after editing:
#   systemctl --user restart llm-rgb-monitor

# Device names as openrgb reports them. One process per device per update,
# so keep this list short.
RGB_DEVICES=Airgoo AG-DRGB08,AMD Wraith Prism

# CONFIRMED ZONE MAP (zone N = Channel A(N+1)):
#   zone 0  A1  light bar, 30 LEDs   - wall wash
#   zone 4  A5  rear fan,   8 LEDs
#   zone 5  A6  top fan,    8 LEDs
#   zone 6  A7  front fan,  8 LEDs
#   zones 1-3, 7 empty - leave at size 0
#
# Zone sizes persist in OpenRGB's own config, set once with:
#   openrgb --noautoconnect --device "Airgoo AG-DRGB08" --zone 0 --size 30 --mode direct --color 00FF00
#   openrgb --noautoconnect --device "Airgoo AG-DRGB08" --zone 4 --size 8 --mode direct --color 00FF00
#   (repeat for zones 5 and 6)
# The daemon then addresses the whole device at once and every sized zone
# takes the colour.

# DO NOT add the MSI controller here. It enumerates as
# "MSI PRO B550M-VC WIFI (MS-7C95)" and its mode list includes Double meteor,
# the effect named in the report where an MSI controller was permanently
# bricked. Your JRGB/JRAINBOW headers are empty; leave it alone.

RGB_HOST=127.0.0.1
RGB_PORT=${SDK_PORT}

# What to watch for activity and health. PER MACHINE:
#
#   Apollo   RGB_BACKEND=llama   RGB_DEVICES=Airgoo AG-DRGB08,AMD Wraith Prism
#            LLAMA_API_KEY_FILE=~/.coder-api-key
#            RGB_SCLK_DEVICES=AMD Wraith Prism
#   Mercury  RGB_BACKEND=none    RGB_DEVICES=Airgoo AG-DRGB08
#            RGB_SCLK_DEVICES=   (empty - no Wraith on Mercury)
#            Diffusion is compute-bound, so GPU power alone tracks it well.
#   Gemini   RGB_BACKEND=llama   devices depend on the board - see runbook
#
# A backend the daemon cannot reach is treated as "server down" and pulses
# amber forever - so match this to what the box actually runs.
RGB_BACKEND=llama
COMFY_URL=http://127.0.0.1:8188

# llama-server, for health and queue depth (backend=llama)
LLAMA_URL=http://127.0.0.1:8080
LLAMA_API_KEY_FILE=$keyfile

# 0.0-1.0. Eight ports of fans is a lot of light for a room.
RGB_BRIGHTNESS=1.0

RGB_INTERVAL=2.0
RGB_SMOOTHING=0.35

# Utilisation at or below this reads as fully idle. Raise it if the lights
# sit teal rather than green with the model loaded but unused.
RGB_IDLE_FLOOR=0.12

# Signal floor applied the instant llama-server reports a request in flight,
# so colour reacts before the GPU power rail has ramped.
RGB_ACTIVE_FLOOR=0.45

# max = either card lights it up; mean = overall utilisation
RGB_COMBINE=max

# --- Second metric on the Wraith Prism -------------------------------
# The case lighting tracks instantaneous GPU power (green -> blue -> red).
# The cooler tracks a 5-minute rolling average of shader clock across all
# discrete cards (just the 9060 XT on Apollo now), on a deliberately non-overlapping cool ramp (indigo -> cyan ->
# white), so the two readouts never show the same colour for different
# reasons. One says "what is happening now", the other "how hard has this
# machine been working lately".
#
# The clock average is NOT passed through the EMA - a slow-moving readout is
# the point.
RGB_SCLK_DEVICES=AMD Wraith Prism
RGB_SCLK_WINDOW=300
RGB_SCLK_FLOOR=0.05

# Per-device mode overrides. The Wraith exposes Direct but has been seen to
# ignore CLI colour writes under it. If it stays one colour while the case
# responds, try static here:
#   RGB_MODES=AMD Wraith Prism=static
RGB_MODES=
EOF
        ok "Wrote $CONF"
        warn "RGB_DEVICES is empty. Fill it in before starting the service."
    else
        info "Keeping existing $CONF"
    fi

    # A kept config can point at a key file that no longer exists - e.g. after
    # a machine changes role. Catch it here rather than as a stream of
    # "Invalid API Key" in llama-server's log.
    local cur
    cur=$(sed -n 's/^LLAMA_API_KEY_FILE=//p' "$CONF" | tail -n1)
    cur="${cur/#\~/$HOME}"
    if grep -q '^RGB_BACKEND=llama' "$CONF" && [ -n "$cur" ] && [ ! -f "$cur" ]; then
        warn "monitor.env points LLAMA_API_KEY_FILE at $cur, which does not exist."
        if [ -f "$keyfile" ]; then
            warn "This machine's key is $keyfile. Fix with:"
            warn "  sed -i 's|^LLAMA_API_KEY_FILE=.*|LLAMA_API_KEY_FILE=$keyfile|' $CONF"
        fi
    fi
}

# ---- phase: service -------------------------------------------------------

phase_service() {
    say "[service] systemd user unit"

    mkdir -p "$HOME/.config/systemd/user"
    cat > "$HOME/.config/systemd/user/llm-rgb-monitor.service" <<EOF
[Unit]
Description=RGB lighting driven by local LLM activity
After=network.target

[Service]
Type=simple
EnvironmentFile=$CONF
# openrgb is a Qt app and aborts with exit 255 and no output when it cannot
# reach a display. Set here as well as in the daemon, so a hand-run
# ExecStart also works.
Environment=QT_QPA_PLATFORM=offscreen
ExecStart=$VENV/bin/python $MONITOR_DST
Restart=on-failure
RestartSec=10

[Install]
WantedBy=default.target
EOF

    systemctl --user daemon-reload
    loginctl enable-linger "$USER" >/dev/null 2>&1 || \
        warn "Could not enable linger; the service stops when you log out."

    cat <<EOF

    Verify by hand before enabling:

        $MONITOR_DST --list
        # put the EXACT device names it prints into RGB_DEVICES in $CONF,
        # comma-separated - never the MSI motherboard controller
        set -a; . $CONF; set +a
        $MONITOR_DST --devices "\$RGB_DEVICES" --test

    --test cycles six colours across every listed device, printing how long
    each openrgb call takes. A fixture that stays dark is usually a zone
    that was never sized - see the zone map in $CONF.

    If it looks right:

        systemctl --user enable --now llm-rgb-monitor
        journalctl --user -u llm-rgb-monitor -f

    To watch the signal without touching hardware:

        $VENV/bin/python $MONITOR_DST --dry-run -v

EOF
}

# ---- main -----------------------------------------------------------------

ALL=(deps harden openrgb detect udev monitor service)

ARGS=()
for a in "$@"; do
    case "$a" in
        -h|--help) sed -n '2,90p' "$0"; exit 0 ;;
        *) ARGS+=("$a") ;;
    esac
done

if [ ${#ARGS[@]} -eq 0 ]; then
    PHASES=("${ALL[@]}")
else
    PHASES=("${ARGS[@]}")
fi

# `udev` optionally takes a VID:PID as the next argument.
i=0
while [ $i -lt ${#PHASES[@]} ]; do
    p="${PHASES[$i]}"
    case "$p" in
        udev)
            nxt="${PHASES[$((i+1))]:-}"
            if [[ "$nxt" =~ ^[0-9a-fA-F]{4}:[0-9a-fA-F]{4}$ ]]; then
                phase_udev "$nxt"; i=$((i+2)); continue
            fi
            phase_udev ""
            ;;
        deps|harden|openrgb|detect|monitor|service) "phase_$p" ;;
        *) die "Unknown phase '$p'. Valid: ${ALL[*]}" ;;
    esac
    echo
    i=$((i+1))
done

say "Done."
