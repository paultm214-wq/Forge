#!/usr/bin/env bash
#
# install-vulcan-gaming.sh
#
# Vulcan as a gaming PC AND an AI worker, on the same Kubuntu install - no
# more rebooting into Bazzite. Run AFTER install-vulcan-imagegen.sh and
# install-coder-helper.sh.
#
#   * Plasma starts at boot with autologin, on the HDMI dummy plug, so
#     Moonlight can always connect - and you can still play at the desk.
#   * Sunshine (LizardByte) streams it to Moonlight, encoding on NVENC.
#   * vulcan-gamemode gives games the GPU: while a Steam game runs or a
#     Moonlight stream is open, the coder and sd-server are stopped and the
#     card gets its full power limit; afterwards they come back, at a lower
#     power limit that costs AI work little.
#   * vulcan-mode switches by hand: `play`, `auto`, `headless` (stop Plasma
#     for a long unattended job - frees ~0.6GB of VRAM), `desktop`, `status`.
#
# WHY NOT A PLASMA-FREE STEAM SESSION (gamescope from a TTY)?
#   Measured on Vulcan: Plasma costs ~0.63GB of VRAM at idle. A gamescope
#   session with Steam's gamepad UI is not free either (Steam's own UI is a
#   GPU-rendered browser), so it would save a few hundred MB at most - and
#   only while NOT gaming, since game mode stops the AI work anyway. In
#   exchange it puts gamescope's embedded mode and Sunshine's KMS capture of
#   it on NVIDIA's proprietary driver, the least mature combination of all.
#   `vulcan-mode headless` gets the whole 0.63GB back when you won't be
#   playing, with none of that risk.
#
# Usage:  ./install-vulcan-gaming.sh [phase ...]
# Phases: check desktop steam sunshine gamemode firewall

set -euo pipefail

# ============================================================================
# CONFIG
# ============================================================================

GAME_USER="${GAME_USER:-$USER}"
AI_POWER_PCT=80          # GPU power limit for AI work, % of default (100 = off)
GRACE_SECONDS=30         # wait this long after a game exits before resuming AI
AI_SERVICES="coder sd-server"
AUTOLOCK=false           # Plasma's idle screen lock: a stream would land on it
LAN_SUBNET="192.168.50.0/24"
CONFIGURE_UFW=true
SUNSHINE_MIN_DRIVER=590  # LizardByte's packages use CUDA 13.1 (driver >= 590.48)

# ============================================================================
# HELPERS
# ============================================================================

BOLD=$'\033[1m'; RED=$'\033[31m'; YELLOW=$'\033[33m'; GREEN=$'\033[32m'; RESET=$'\033[0m'
say()  { printf '%s==> %s%s\n' "$BOLD" "$*" "$RESET"; }
info() { printf '    %s\n' "$*"; }
warn() { printf '%s    WARNING: %s%s\n' "$YELLOW" "$*" "$RESET"; }
die()  { printf '%s    ERROR: %s%s\n' "$RED" "$*" "$RESET" >&2; exit 1; }
ok()   { printf '%s    OK: %s%s\n' "$GREEN" "$*" "$RESET"; }
have() { command -v "$1" &>/dev/null; }

# ============================================================================
# PHASE: check
# ============================================================================

phase_check() {
    say "[check] Vulcan"
    [ "$(id -u)" -ne 0 ] || die "Run as your normal user, not with sudo."
    have nvidia-smi || die "No NVIDIA driver - run install-vulcan-imagegen.sh first."
    local drv; drv=$(nvidia-smi --query-gpu=driver_version --format=csv,noheader | head -n1)
    info "NVIDIA driver $drv"
    if [ "${drv%%.*}" -lt "$SUNSHINE_MIN_DRIVER" ]; then
        warn "Sunshine's packages are built with CUDA 13.1 and want driver >= 590.48."
        warn "Older drivers lose CUDA capture/encode paths. Update the driver, e.g.:"
        warn "    sudo ubuntu-drivers install"
    fi
    local ms; ms=$(cat /sys/module/nvidia_drm/parameters/modeset 2>/dev/null || echo "?")
    if [ "$ms" = "Y" ]; then
        ok "nvidia-drm modeset is on (needed for Wayland and Sunshine's KMS capture)"
    else
        warn "nvidia-drm modeset is '$ms'. Add nvidia-drm.modeset=1 to the kernel command"
        warn "line (/etc/default/grub, then sudo update-grub) and reboot."
    fi
    local outs; outs=$(grep -l '^connected' /sys/class/drm/card*-*/status 2>/dev/null | wc -l)
    info "Connected display outputs: $outs (the HDMI dummy plug counts as one)"
    [ "$outs" -ge 1 ] || warn "No display connected - plug in the HDMI dummy plug before streaming."
    for s in $AI_SERVICES; do
        if systemctl --user cat "$s" >/dev/null 2>&1; then ok "AI service '$s' found"
        else warn "AI service '$s' not found - game mode will skip it"; fi
    done
}

# ============================================================================
# PHASE: desktop
# ============================================================================

phase_desktop() {
    say "[desktop] Plasma at boot, logged in automatically"
    # install-vulcan-imagegen.sh set a text console so no desktop held VRAM.
    # For gaming and streaming the session has to exist. ~0.63GB, measured.
    sudo systemctl set-default graphical.target >/dev/null
    ok "Boots into the desktop (graphical.target)"

    local session="plasma"
    [ -f /usr/share/wayland-sessions/plasma.desktop ] || session="plasmawayland"
    sudo mkdir -p /etc/sddm.conf.d
    sudo tee /etc/sddm.conf.d/50-vulcan-autologin.conf >/dev/null <<EOF
# install-vulcan-gaming.sh: log in at boot so Moonlight can always connect,
# even after a reboot with nobody at the desk.
[Autologin]
User=$GAME_USER
Session=$session
Relogin=false
EOF
    ok "Autologin: $GAME_USER into the $session (Wayland) session"

    if [ "$AUTOLOCK" = false ] && have kwriteconfig6; then
        kwriteconfig6 --file kscreenlockerrc --group Daemon --key Autolock false
        ok "Idle screen lock off (a stream would otherwise open on the lock screen)"
    fi

    # A worker that suspends itself stops answering forge. Plasma's power
    # management would happily put an idle desktop to sleep.
    sudo systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target >/dev/null
    ok "Suspend and hibernate disabled system-wide"

    local preset="$HOME/llm-stack/coder-presets.ini"
    if [ -f "$preset" ]; then
        local n; n=$(sed -n 's/^n-cpu-moe *= *//p' "$preset")
        info "Coder n-cpu-moe is ${n:-unset}. With Plasma running you measured 0.71GB of"
        info "VRAM free at 12 - below the ~1GB to keep. 13 frees ~0.4GB more:"
        info "    sed -i 's/^n-cpu-moe = .*/n-cpu-moe = 13/' $preset"
        info "    systemctl --user restart coder"
    fi
    warn "Reboot to start the desktop: sudo reboot"
}

# ============================================================================
# PHASE: steam
# ============================================================================

phase_steam() {
    say "[steam] Steam, GameMode, MangoHud"
    if have steam; then
        ok "Steam already installed"
    else
        sudo dpkg --add-architecture i386
        sudo apt update
        local deb; deb=$(mktemp --suffix=.deb)
        wget -qO "$deb" https://repo.steampowered.com/steam/archive/stable/steam_latest.deb \
            || die "Could not download Valve's Steam package."
        sudo apt install -y "$deb"
        rm -f "$deb"
        ok "Steam installed from Valve's package"
    fi
    # GameMode: CPU governor and scheduling boost while a game runs. Use it per
    # game with the launch option:  gamemoderun %command%
    # MangoHud: frame-time overlay, for checking that AI work really stopped:
    #   mangohud %command%   (or both: gamemoderun mangohud %command%)
    sudo apt install -y gamemode mangohud
    ok "GameMode and MangoHud installed"
    info "Tips that save VRAM and CPU for the AI work when you are NOT playing:"
    info "  * Do not start Steam at login (Steam > Settings > Interface). Moonlight's"
    info "    'Steam Big Picture' app starts it when you connect."
    info "  * Steam > Settings > Interface: turn off 'GPU accelerated rendering in web"
    info "    views' - the store and library pages then hold less VRAM."
    info "  * Quit Steam when done; minimised to the tray it keeps its VRAM."
    info "  * Proton-GE for games that need it: ProtonUp-Qt (Flatpak net.davidotek.pupgui2)."
}

# ============================================================================
# PHASE: sunshine
# ============================================================================

phase_sunshine() {
    say "[sunshine] LizardByte Sunshine, NVENC, starts with the desktop"
    if ! have sunshine; then
        # LizardByte's own apt repository; the script picks the Ubuntu release.
        curl -1sLf 'https://dl.cloudsmith.io/public/lizardbyte/stable/cfg/setup/bash.deb.sh' \
            | sudo -E bash
        sudo apt update
        sudo apt install -y sunshine
    fi
    ok "Sunshine $(sunshine --version 2>/dev/null | head -n1 || echo installed)"
    # The package sets cap_sys_admin on the binary for KMS capture.
    getcap "$(readlink -f "$(command -v sunshine)")" 2>/dev/null | grep -q cap_sys_admin \
        && ok "KMS capture capability set" \
        || warn "No cap_sys_admin on the sunshine binary - reinstall the package."
    sudo usermod -a -G input "$GAME_USER"     # virtual gamepads, mouse, keyboard

    local conf="$HOME/.config/sunshine/sunshine.conf"
    mkdir -p "$(dirname "$conf")"
    touch "$conf"
    set_conf() {   # only if the key is not already set - your web-UI edits win
        grep -q "^$1 *=" "$conf" || echo "$1 = $2" >> "$conf"
    }
    set_conf encoder nvenc
    set_conf capture kms
    set_conf origin_web_ui_allowed lan
    # Any Moonlight stream = game mode: AI work pauses while you stream, and
    # resumes ~GRACE_SECONDS after you disconnect.
    set_conf global_prep_cmd '[{"do":"/usr/local/bin/vulcan-mode play","undo":"/usr/local/bin/vulcan-mode auto","elevated":false}]'
    ok "Configured $conf (NVENC, KMS capture, game mode while streaming)"

    systemctl --user daemon-reload
    systemctl --user enable app-dev.lizardbyte.app.Sunshine.service >/dev/null 2>&1 \
        || systemctl --user enable sunshine >/dev/null 2>&1 \
        || warn "Could not enable Sunshine's user service - start it from the desktop once."
    ok "Sunshine starts with the desktop session"
    cat <<EOF

    First run, from the desktop or any LAN browser:
      1. Open https://$(hostname -I | awk '{print $1}'):47990 and create the Sunshine login.
      2. In Moonlight, add this PC (it usually appears by itself) and type the
         PIN it shows into Sunshine's PIN page.
      3. Use the 'Steam Big Picture' app in Moonlight for couch play, 'Desktop'
         for everything else. Either one pauses the AI work while it runs.

EOF
}

# ============================================================================
# PHASE: gamemode
# ============================================================================

phase_gamemode() {
    say "[gamemode] games come first: vulcan-gamemode + vulcan-mode"
    have python3 || sudo apt install -y python3
    have notify-send || sudo apt install -y libnotify-bin

    sudo install -m 755 /dev/stdin /usr/local/sbin/vulcan-gamemode <<'PYGM'
#!/usr/bin/env python3
"""
vulcan-gamemode - games come first on Vulcan.

Runs as root (systemd: vulcan-gamemode.service). Every few seconds it decides
whether someone is PLAYING:

  * a Steam game is running - Steam starts every game under its `reaper`
    process with `SteamLaunch AppId=<id>` on the command line, or
  * a Moonlight stream is open - Sunshine's global_prep_cmd runs
    `vulcan-mode play` when a stream starts and `vulcan-mode auto` when it
    ends, which creates and removes /run/user/<uid>/vulcan-play, or
  * you said so: `vulcan-mode play` by hand (for a non-Steam game).

PLAY: the AI services (coder, sd-server) are stopped - that frees ALL their
VRAM, including the few hundred MB an idle CUDA process holds - and the GPU
gets its full power limit. forge sees Vulcan go away and carries on without
it: a coder attempt moves to Apollo or Mercury, an unpinned render moves to
Mercury.

WORK: once nothing has been playing for GRACE seconds, the services that
were running before are started again, and the GPU goes back to the AI power
limit (AI_POWER_PCT of the card's default). Token generation is limited by
memory bandwidth rather than compute, so a lower power limit costs the coder
little; image sampling is compute-bound and slows somewhat - measure with
~/sd-lightning-test.py and pick your own number (100 = no limit).

Settings: /etc/vulcan-gamemode.conf. Log: journalctl -u vulcan-gamemode -f
"""
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path

CONF = Path(os.environ.get("VULCAN_GAMEMODE_CONF", "/etc/vulcan-gamemode.conf"))
PROC = Path(os.environ.get("VULCAN_GAMEMODE_PROC", "/proc"))
STATE_DIR = Path(os.environ.get("VULCAN_GAMEMODE_STATE", "/run/vulcan-gamemode"))


def load_conf() -> dict:
    conf = {"USER": "", "SERVICES": "coder sd-server", "AI_POWER_PCT": "80",
            "GRACE": "30", "POLL": "3", "NOTIFY": "yes"}
    if CONF.exists():
        for line in CONF.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                conf[k.strip()] = " ".join(shlex.split(v))
    return conf


def log(msg: str) -> None:
    print(msg, flush=True)


def run(cmd: list[str], check: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, check=check)


# -- detection ----------------------------------------------------------------

def steam_game() -> str | None:
    """The AppId of a running Steam game, or None."""
    for d in PROC.iterdir():
        if not d.name.isdigit():
            continue
        try:
            argv = (d / "cmdline").read_bytes().split(b"\0")
        except OSError:
            continue
        if not argv or os.path.basename(argv[0]) != b"reaper" or b"SteamLaunch" not in argv:
            continue
        for a in argv:
            if a.startswith(b"AppId="):
                appid = a[6:].decode(errors="replace")
                if appid and appid != "0":
                    return appid
    return None


def play_flag(uid: int) -> bool:
    return Path(f"/run/user/{uid}/vulcan-play").exists() or (STATE_DIR / "force-play").exists()


# -- actions ------------------------------------------------------------------

def user_systemctl(user: str, *args: str) -> subprocess.CompletedProcess:
    return run(["systemctl", "--user", f"--machine={user}@", *args])


def active_services(user: str, services: list[str]) -> list[str]:
    return [s for s in services if user_systemctl(user, "is-active", "--quiet", s).returncode == 0]


def power_limits() -> tuple[float, float, float] | None:
    r = run(["nvidia-smi", "--query-gpu=power.default_limit,power.min_limit,power.max_limit",
             "--format=csv,noheader,nounits"])
    try:
        return tuple(float(x) for x in r.stdout.strip().splitlines()[0].split(","))
    except (ValueError, IndexError):
        return None


def set_power(pct: float) -> None:
    lim = power_limits()
    if not lim:
        return
    default, lo, hi = lim
    watts = max(lo, min(hi, default * pct / 100))
    r = run(["nvidia-smi", "-pl", f"{watts:.0f}"])
    log(f"power limit {watts:.0f} W ({pct:.0f}% of {default:.0f} W)"
        + ("" if r.returncode == 0 else f" - FAILED: {r.stderr.strip() or r.stdout.strip()}"))


def notify(user: str, uid: int, text: str) -> None:
    run(["runuser", "-u", user, "--", "env",
         f"DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/{uid}/bus",
         "notify-send", "-a", "Vulcan", "-i", "applications-games", "Vulcan", text])


# -- main loop ----------------------------------------------------------------

def main() -> int:
    conf = load_conf()
    user = conf["USER"]
    if not user:
        log("USER is not set in /etc/vulcan-gamemode.conf")
        return 1
    uid = int(run(["id", "-u", user]).stdout.strip() or -1)
    services = conf["SERVICES"].split()
    ai_pct, grace, poll = float(conf["AI_POWER_PCT"]), float(conf["GRACE"]), float(conf["POLL"])
    notify_on = conf["NOTIFY"].lower() in ("yes", "true", "1")
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    paused_file = STATE_DIR / "paused-services"

    # Survive a restart of this service mid-game: what was stopped is on disk.
    paused = paused_file.read_text().split() if paused_file.exists() else []
    mode = "play" if paused else "work"
    if mode == "work":
        set_power(ai_pct)
    log(f"started in {mode} mode; watching for Steam games and the play flag")
    idle_since = None
    once = os.environ.get("VULCAN_GAMEMODE_ONCE")      # tests: one decision, then exit

    while True:
        appid = steam_game()
        flag = play_flag(uid)
        playing = bool(appid or flag)
        why = f"Steam game {appid}" if appid else "stream / vulcan-mode play" if flag else ""
        if playing and mode == "work":
            paused = active_services(user, services)
            paused_file.write_text(" ".join(paused))
            for s in paused:
                user_systemctl(user, "stop", s)
            set_power(100)
            mode, idle_since = "play", None
            log(f"PLAY ({why}): paused {', '.join(paused) or 'nothing (was idle)'}")
            if notify_on and paused:
                notify(user, uid, f"Game mode: paused {', '.join(paused)} - the GPU is all yours.")
        elif not playing and mode == "play":
            idle_since = idle_since or time.monotonic()
            if time.monotonic() - idle_since >= grace:
                for s in paused:
                    user_systemctl(user, "start", s)
                set_power(ai_pct)
                log(f"WORK: resumed {', '.join(paused) or 'nothing'}")
                if notify_on and paused:
                    notify(user, uid, f"Game over: resumed {', '.join(paused)}.")
                paused_file.unlink(missing_ok=True)
                paused, mode, idle_since = [], "work", None
        elif playing:
            idle_since = None
        if once:
            return 0
        time.sleep(poll)


if __name__ == "__main__":
    sys.exit(main())
PYGM
    ok "Installed /usr/local/sbin/vulcan-gamemode"

    sudo tee /etc/vulcan-gamemode.conf >/dev/null <<EOF
# vulcan-gamemode settings (written by install-vulcan-gaming.sh).
# After editing: sudo systemctl restart vulcan-gamemode
USER=$GAME_USER
SERVICES=$AI_SERVICES
# GPU power limit while doing AI work, % of the card's default (100 = no cap).
# Games always get 100.
AI_POWER_PCT=$AI_POWER_PCT
# Seconds after the last game exits (or the stream ends) before AI resumes.
GRACE=$GRACE_SECONDS
POLL=3
NOTIFY=yes
EOF
    ok "Wrote /etc/vulcan-gamemode.conf"

    sudo tee /usr/local/bin/vulcan-mode >/dev/null <<'EOF'
#!/usr/bin/env bash
# vulcan-mode - switch Vulcan between gaming and AI work by hand.
flag="/run/user/$(id -u)/vulcan-play"
case "${1:-status}" in
    play)     touch "$flag"
              echo "Game mode: AI work pauses within a few seconds." ;;
    auto)     rm -f "$flag"
              echo "Automatic: AI work resumes shortly after the last game exits." ;;
    headless) echo "Stopping the desktop (and any stream) - frees ~0.6GB of VRAM for AI work."
              sudo systemctl stop display-manager ;;
    desktop)  sudo systemctl start display-manager ;;
    status)   if [ -e "$flag" ]; then echo "play flag: ON (vulcan-mode auto to clear)"
              else echo "play flag: off"; fi
              for s in coder sd-server; do
                  printf '%-10s %s\n' "$s" "$(systemctl --user is-active "$s" 2>/dev/null)"
              done
              printf 'desktop    %s\n' "$(systemctl is-active display-manager)"
              nvidia-smi --query-gpu=memory.used,memory.total,power.draw,power.limit \
                  --format=csv,noheader | sed 's/^/gpu        /'
              journalctl -u vulcan-gamemode -n 3 --no-pager -o cat 2>/dev/null | sed 's/^/log        /' ;;
    *)        echo "usage: vulcan-mode play|auto|headless|desktop|status"; exit 2 ;;
esac
EOF
    sudo chmod 755 /usr/local/bin/vulcan-mode
    ok "Installed /usr/local/bin/vulcan-mode"

    sudo tee /etc/systemd/system/vulcan-gamemode.service >/dev/null <<'EOF'
[Unit]
Description=Vulcan game mode - pause AI work while a game or stream runs
After=multi-user.target nvidia-persistenced.service

[Service]
Type=simple
ExecStart=/usr/local/sbin/vulcan-gamemode
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
    # Keeps the driver initialised between jobs, so the power limit sticks
    # and CUDA start-up is quicker.
    sudo systemctl enable --now nvidia-persistenced >/dev/null 2>&1 || true
    sudo systemctl daemon-reload
    sudo systemctl enable --now vulcan-gamemode
    ok "vulcan-gamemode running - journalctl -u vulcan-gamemode -f"
    info "Non-Steam game? Run 'vulcan-mode play' first and 'vulcan-mode auto' after."
}

# ============================================================================
# PHASE: firewall
# ============================================================================

phase_firewall() {
    [ "$CONFIGURE_UFW" = true ] || { info "Firewall configuration disabled."; return 0; }
    say "[firewall] Sunshine, LAN only"
    have ufw || sudo apt install -y ufw
    local p
    for p in 47984 47989 47990 48010; do
        sudo ufw allow from "$LAN_SUBNET" to any port "$p" proto tcp comment "sunshine" >/dev/null
    done
    for p in 47998 47999 48000 48002 48010; do
        sudo ufw allow from "$LAN_SUBNET" to any port "$p" proto udp comment "sunshine" >/dev/null
    done
    ok "Sunshine ports open to $LAN_SUBNET only (web UI 47990 included)"
}

# ============================================================================
# MAIN
# ============================================================================

ALL_PHASES=(check desktop steam sunshine gamemode firewall)

main() {
    local -a phases
    if [ $# -gt 0 ]; then phases=("$@"); else phases=("${ALL_PHASES[@]}"); fi
    local p
    for p in "${phases[@]}"; do
        case "$p" in
            check|desktop|steam|sunshine|gamemode|firewall) "phase_$p"; echo ;;
            *) die "Unknown phase '$p'. Valid: ${ALL_PHASES[*]}" ;;
        esac
    done
    say "Done."
    cat <<EOF
    Reboot, then check:   vulcan-mode status
    Start a Steam game:   coder and sd-server stop, GPU at full power
    Quit it:              they come back after ${GRACE_SECONDS}s, GPU at ${AI_POWER_PCT}%
EOF
}

main "$@"
