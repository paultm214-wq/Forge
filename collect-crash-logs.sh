#!/usr/bin/env bash
#
# collect-crash-logs.sh
#
# Gathers everything relevant to a HOST crash (not a service crash) on the
# 5700G + RX 9070 XT + RX 9060 XT box, and writes a single file you can read
# or paste.
#
#   ./collect-crash-logs.sh            # look at the previous boot
#   ./collect-crash-logs.sh -2         # two boots ago
#
# Read-only. Nothing here changes system state.

set -uo pipefail

BOOT="${1:--1}"
OUT="$HOME/crash-report-$(date +%Y%m%d-%H%M%S).txt"

hdr() { printf '\n\n========== %s ==========\n' "$*"; }

{
printf 'Crash report generated %s\n' "$(date -Is)"
printf 'Examining boot offset: %s\n' "$BOOT"

hdr "BOOT HISTORY"
# The key question: how many boots ended uncleanly, and when. A clean
# shutdown logs a stop sequence; a hard power loss just stops mid-sentence.
journalctl --list-boots 2>/dev/null | tail -25

hdr "HOW THE PREVIOUS BOOT ENDED (last 120 lines)"
# If the tail is ordinary chatter with no shutdown messages, the machine
# died without warning - which points at power, thermal cutout, or a kernel
# hang rather than anything software noticed.
journalctl -b "$BOOT" -n 120 --no-pager 2>/dev/null

hdr "KERNEL: GPU FAULTS, RESETS, RING TIMEOUTS"
# amdgpu ring timeouts and resets are the signature of a hung GPU. A
# 'GPU reset begin' that never completes usually takes the box with it.
journalctl -k -b "$BOOT" --no-pager 2>/dev/null \
  | grep -iE 'amdgpu|drm|gpu reset|ring .* timeout|GPU fault|VM_L2|page fault|hang|IOMMU|PCIe.*error|AER' \
  | tail -80

hdr "KERNEL: MACHINE CHECK, THERMAL, POWER"
# MCE = hardware-level fault (CPU, memory, bus). Thermal events and sudden
# throttling point at cooling or PSU sag rather than software.
journalctl -k -b "$BOOT" --no-pager 2>/dev/null \
  | grep -iE 'mce|machine check|hardware error|thermal|critical temp|throttl|over-current|overcurrent|power supply|undervolt' \
  | tail -40

hdr "KERNEL: OOM KILLER / MEMORY PRESSURE"
# 64GB should be plenty, but --no-mmap removal and large context buffers
# changed the host memory profile. Worth ruling out.
journalctl -k -b "$BOOT" --no-pager 2>/dev/null \
  | grep -iE 'out of memory|oom-kill|oom_reaper|killed process' \
  | tail -20

hdr "KERNEL: PANIC / OOPS / BUG"
journalctl -k -b "$BOOT" --no-pager 2>/dev/null \
  | grep -iE 'kernel panic|Oops|BUG:|general protection|RIP:|Call Trace|watchdog' \
  | tail -40

hdr "LLAMA-SERVER AROUND THE END OF THAT BOOT"
journalctl --user-unit llama-server -b "$BOOT" -n 60 --no-pager 2>/dev/null \
  || journalctl -b "$BOOT" -n 60 --no-pager -t run_llm.sh 2>/dev/null \
  || echo "(no llama-server journal for that boot)"

hdr "CORE DUMPS RECORDED"
coredumpctl list --no-pager 2>/dev/null | tail -20 || echo "(coredumpctl unavailable)"

hdr "CURRENT GPU STATE"
rocm-smi 2>/dev/null || echo "(rocm-smi unavailable)"

hdr "GPU POWER CAPS AND LIMITS"
for c in /sys/class/drm/card*/device/hwmon/hwmon*/; do
    [ -d "$c" ] || continue
    printf '%s\n' "$c"
    for f in power1_cap power1_cap_max power1_average power1_input temp1_input temp2_input; do
        [ -f "$c$f" ] && printf '   %-18s %s\n' "$f" "$(cat "$c$f" 2>/dev/null)"
    done
done

hdr "CPU / SYSTEM TEMPS"
sensors 2>/dev/null || echo "(lm-sensors not installed: sudo apt install lm-sensors)"

hdr "UPTIME AND LOAD"
uptime
printf '\n'
free -h

hdr "PCIe LINK STATE OF BOTH GPUS"
# A card that has dropped to a lower link speed, or vanished, points at the
# Oculink connection or power delivery rather than software.
for d in $(lspci -D | grep -iE 'vga|display' | cut -d' ' -f1); do
    printf '%s\n' "$d"
    lspci -vv -s "$d" 2>/dev/null | grep -iE 'LnkCap:|LnkSta:|DevSta:|Subsystem:' | sed 's/^/   /'
done

hdr "JOURNAL PERSISTENCE"
# If this is 'volatile', logs do NOT survive a reboot and everything above
# for a previous boot will be empty. Fix before the next crash.
printf 'Storage setting: '
grep -hE '^\s*Storage=' /etc/systemd/journald.conf /etc/systemd/journald.conf.d/*.conf 2>/dev/null || echo "(default: auto)"
printf '/var/log/journal exists: '
[ -d /var/log/journal ] && echo "yes (persistent)" || echo "NO - logs are volatile, see note"

} > "$OUT" 2>&1

echo "Wrote $OUT"
echo
echo "Quick triage - the lines that matter most:"
echo
grep -iE 'gpu reset|ring .* timeout|machine check|mce|kernel panic|out of memory|Oops|BUG:' "$OUT" \
    | head -20 || echo "  (none of the headline signatures found)"
echo
if ! [ -d /var/log/journal ]; then
    echo "WARNING: /var/log/journal does not exist, so the journal is volatile"
    echo "and previous-boot logs are lost on reboot. Enable persistence now:"
    echo "    sudo mkdir -p /var/log/journal && sudo systemd-tmpfiles --create --prefix /var/log/journal"
    echo "    sudo systemctl restart systemd-journald"
fi
