#!/usr/bin/env bash
#
# install-vulcan-strata.sh - Vulcan becomes forge's PRINCIPAL CODER.
#
# From 30 October 2026 Vulcan (RTX 4070 Ti Super 16 GB, 64 GB RAM, Kubuntu)
# no longer renders images or games. It runs Strata - Qwen3.8-Flash-Next
# with its experts in system RAM - as forge's [coder], and makes the 3D
# models (Hunyuan3D, forge-shape) for the specs that have them: the shape
# server stops Strata while it holds the GPU and starts it again after.
# Images move to Apollo (install-apollo-images.sh); gaming to Hades.
#
# Run on Vulcan as your normal user. Phases, in order:
#
#   check       what is here now
#   retire      stop the image and gaming jobs: sd-server, vulcan-gamemode,
#               Sunshine and the Plasma autologin are DISABLED, not removed
#               (`./install-vulcan-gaming.sh` and `systemctl --user enable
#               --now sd-server` bring them back); boots to the console,
#               which leaves Strata the 0.6 GB Plasma held
#   strata      install Strata (its own installer - shown to you first) and
#               download the model
#   service     the `strata` user service: the OpenAI-compatible API on
#               :8081 for the LAN, with an API key
#   key         save Strata's key to ~/.strata-api-key (for lab-agent) and
#               print the line that copies it to Gemini for forge
#   shape       point forge-shape at Strata: it stops `strata` (and
#               sd-server) while the shape model holds the GPU
#   firewall    allow :8081 from the LAN
#   gpu-unlock  OPTIONAL, asks first: Zagitta/gpu-unlock, a kernel module
#               that lifts GeForce's half-rate FP16/BF16 tensor throughput
#               (Ada is "Experimental" in its README). See below.
#   gpu-unlock-off   remove its kernel parameter again
#   status      Strata, the services, and whether the unlock is active
#
# Usage:  ./install-vulcan-strata.sh [phase ...]     (no phase: check..firewall)
#
# STRATA (github.com/Niko1221/Strata, MIT): commands used here are from its
# README and docs/AI_SETUP.md as of 30 Oct 2026 - `strata launch --model
# iq2_xs --yes --no-tui` starts the server in the background, `strata launch
# --stop` stops it and frees the GPU, `--port`, `--context`, `--access lan`
# (makes an API key; `strata launch --show-key` prints it), GET /health
# reports "loaded": true. If a later Strata changes any of that, `strata
# launch --help` is the authority. Model: iq2_xs (~40-42 GB, fits Vulcan's
# 64 GB once sd-server is gone). Avoid the "abliterated" Strata packs: forge
# runs the code it writes.
#
# GPU-UNLOCK: README claims for Ada: dense FP16/BF16 matmul up to ~+84-100%,
# LLM prefill +25-55%, image/3D generation +30-55%, quantized GGUF decode
# 0-15% (mostly long context), INT4/INT8 decode and games unchanged; outputs
# bit-identical. It builds a kernel module that loads before nvidia and
# needs `gpu_unlock.targets=all` on the kernel command line. For forge it
# mainly speeds up prompt processing (long chunk prompts) and Hunyuan3D;
# token generation stays memory-bound. Experimental on the 40 series, so
# it is its own phase, off by default, with an off switch.

set -euo pipefail

STRATA_PORT=8081
STRATA_MODEL="${STRATA_MODEL:-iq2_xs}"
STRATA_CONTEXT="${STRATA_CONTEXT:-64k}"      # forge's [coder] context = 65536
STRATA_INSTALL_URL="https://github.com/Niko1221/Strata/releases/latest/download/install.sh"
UNLOCK_REPO="https://github.com/Zagitta/gpu-unlock"
UNLOCK_DIR="$HOME/src/gpu-unlock"
LAN_SUBNET="192.168.50.0/24"
UNIT_DIR="$HOME/.config/systemd/user"

say()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
ok()   { printf '\033[1;32m ok\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m !!\033[0m %s\n' "$*"; }
info() { printf '    %s\n' "$*"; }
die()  { printf '\033[1;31mERR\033[0m %s\n' "$*" >&2; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }
ask()  { local a; read -r -p "$1 [y/N] " a; [[ "$a" =~ ^[Yy] ]]; }

[ "$(id -u)" -ne 0 ] || die "Run as your normal user, not root."
case "$(hostname | tr '[:upper:]' '[:lower:]')" in
    vulcan*) ;;
    *) warn "This is $(hostname), not Vulcan - carrying on, but check the paths." ;;
esac

strata_bin() {
    command -v strata 2>/dev/null || { [ -x "$HOME/.local/bin/strata" ] && echo "$HOME/.local/bin/strata"; } || true
}

unit_state() { systemctl --user is-active "$1" 2>/dev/null || true; }

phase_check() {
    say "[check] Vulcan now"
    have nvidia-smi && nvidia-smi --query-gpu=name,memory.used,memory.total --format=csv,noheader | sed 's/^/    GPU /'
    free -g | awk '/^Mem:/ {printf "    RAM %s GB total, %s GB available\n", $2, $7}'
    for u in sd-server coder forge-shape strata app-dev.lizardbyte.app.Sunshine; do
        info "$u: $(unit_state "$u")"
    done
    info "vulcan-gamemode (system): $(systemctl is-active vulcan-gamemode 2>/dev/null || true)"
    info "boot target: $(systemctl get-default)"
    local s; s=$(strata_bin)
    info "strata: ${s:-not installed}"
    grep -q "gpu_unlock.targets" /proc/cmdline && info "gpu-unlock: on the kernel command line" \
        || info "gpu-unlock: not enabled"
}

phase_retire() {
    say "[retire] Vulcan stops rendering and gaming (disabled, not removed)"
    for u in sd-server app-dev.lizardbyte.app.Sunshine; do
        if systemctl --user list-unit-files "$u.service" >/dev/null 2>&1 \
                && systemctl --user list-unit-files "$u.service" | grep -q "$u"; then
            systemctl --user disable --now "$u" >/dev/null 2>&1 || true
            ok "$u disabled"
        fi
    done
    if systemctl list-unit-files vulcan-gamemode.service 2>/dev/null | grep -q vulcan-gamemode; then
        sudo systemctl disable --now vulcan-gamemode >/dev/null 2>&1 || true
        ok "vulcan-gamemode disabled (it stopped the coder whenever Steam ran)"
    fi
    if [ -f /etc/sddm.conf.d/50-vulcan-autologin.conf ]; then
        sudo mv /etc/sddm.conf.d/50-vulcan-autologin.conf /etc/sddm.conf.d/50-vulcan-autologin.conf.off
        ok "Plasma autologin off (the file is kept as .off)"
    fi
    if [ "$(systemctl get-default)" != "multi-user.target" ]; then
        sudo systemctl set-default multi-user.target >/dev/null
        ok "boots to the console from now on - Plasma's ~0.6 GB of VRAM goes to Strata"
        info "a desktop when wanted: sudo systemctl start display-manager"
    fi
    info "Bazzite stays installed beside Kubuntu; boot it from the menu if ever needed."
}

phase_strata() {
    say "[strata] install Strata and download the $STRATA_MODEL model"
    if [ -z "$(strata_bin)" ]; then
        local tmp; tmp=$(mktemp -d)
        curl -fsSL "$STRATA_INSTALL_URL" -o "$tmp/install.sh" || die "could not download $STRATA_INSTALL_URL"
        info "Strata's installer: $tmp/install.sh  (sha256 $(sha256sum "$tmp/install.sh" | cut -c1-16)...)"
        info "Read it before it runs:  less $tmp/install.sh"
        ask "Run Strata's installer now?" || die "stopped - run this phase again when ready"
        sh "$tmp/install.sh"
        hash -r
    fi
    local s; s=$(strata_bin)
    [ -n "$s" ] || die "strata is not on PATH after the install - open a new shell and run: $0 strata"
    ok "strata: $s"
    free_gb=$(df -BG --output=avail "$HOME" | tail -1 | tr -dc 0-9)
    [ "${free_gb:-0}" -ge 50 ] || warn "only ${free_gb} GB free in $HOME - the model needs ~40-45 GB"
    say "First start: downloads the model (tens of GB) and loads it"
    "$s" launch --model "$STRATA_MODEL" --port "$STRATA_PORT" --context "$STRATA_CONTEXT" \
        --access lan --yes --no-tui
    "$s" launch --status || true
    "$s" launch --stop || true
    ok "model in place; the service (next phase) runs it from now on"
}

phase_service() {
    say "[service] strata.service - forge's coder on :$STRATA_PORT"
    local s; s=$(strata_bin)
    [ -n "$s" ] || die "Strata is not installed - run: $0 strata"
    mkdir -p "$UNIT_DIR"
    # `strata launch` starts the server in the background and returns, so
    # the unit is a oneshot that stays "active"; stop = `strata launch --stop`.
    cat > "$UNIT_DIR/strata.service" <<EOF
[Unit]
Description=Strata - Qwen3.8-Flash-Next for forge (principal coder, :$STRATA_PORT)
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=$s launch --model $STRATA_MODEL --port $STRATA_PORT --context $STRATA_CONTEXT --access lan --yes --no-tui
ExecStop=$s launch --stop
TimeoutStartSec=900

[Install]
WantedBy=default.target
EOF
    systemctl --user daemon-reload
    loginctl enable-linger "$USER" >/dev/null 2>&1 || warn "could not enable linger"
    systemctl --user enable --now strata
    say "Waiting for the model to load (GET /health: \"loaded\": true)"
    local i
    for i in $(seq 1 180); do
        if curl -fsS "http://127.0.0.1:$STRATA_PORT/health" 2>/dev/null | grep -q '"loaded": *true'; then
            ok "Strata answers on :$STRATA_PORT with the model loaded"
            return 0
        fi
        sleep 5
    done
    warn "not loaded after 15 minutes - $s launch --status; journalctl --user -u strata"
}

phase_key() {
    say "[key] Strata's API key"
    local s out key; s=$(strata_bin)
    out=$("$s" launch --show-key 2>&1 || true)
    key=$(printf '%s\n' "$out" | grep -oE '[A-Za-z0-9_-]{20,}' | tail -1 || true)
    if [ -z "$key" ]; then
        warn "could not read a key from \`strata launch --show-key\`; it said:"
        printf '%s\n' "$out" | sed 's/^/      /'
        info "put the key in ~/.strata-api-key by hand (chmod 600)"
        return 0
    fi
    umask 077
    printf '%s\n' "$key" > "$HOME/.strata-api-key"
    ok "saved to ~/.strata-api-key (lab-agent reads it)"
    if curl -fsS -H "Authorization: Bearer $key" "http://127.0.0.1:$STRATA_PORT/v1/models" >/dev/null 2>&1; then
        ok "the key works on /v1/models"
    else
        warn "the key did not open /v1/models - check it with: $s launch --show-key"
    fi
    info "On GEMINI, for forge:"
    info "  scp vulcan:.strata-api-key ~/.coder-api-key-strata && chmod 600 ~/.coder-api-key-strata"
}

phase_shape() {
    say "[shape] forge-shape stops Strata while it holds the GPU"
    local f="$UNIT_DIR/forge-shape.service"
    [ -f "$f" ] || { warn "forge-shape is not installed (install-vulcan-shape.sh) - skipped"; return 0; }
    if grep -q -- "--stop-unit strata" "$f"; then
        ok "already set"
    else
        sed -i -E 's#^(ExecStart=.*forge-shape-server\.py.*)$#\1 --stop-unit strata --stop-unit sd-server#' "$f"
        systemctl --user daemon-reload
        systemctl --user restart forge-shape
        ok "forge-shape: --stop-unit strata --stop-unit sd-server"
    fi
    grep -E '^ExecStart=' "$f" | sed 's/^/    /'
}

phase_firewall() {
    say "[firewall] :$STRATA_PORT from the LAN"
    if have ufw && sudo ufw status | grep -q "Status: active"; then
        sudo ufw allow from "$LAN_SUBNET" to any port "$STRATA_PORT" proto tcp comment "strata" >/dev/null
        ok "allowed $STRATA_PORT from $LAN_SUBNET"
    else
        info "ufw not active - nothing to do"
    fi
}

phase_gpu_unlock() {
    say "[gpu-unlock] Zagitta/gpu-unlock - OPTIONAL, experimental on the RTX 40 series"
    cat <<'EOF'
    What it does: a kernel module, loaded before the NVIDIA driver at boot,
    that lifts the GeForce cap on FP16/BF16/TF32 tensor throughput. Not a
    patched driver, not a VBIOS flash; off again by removing one kernel
    parameter (this script's gpu-unlock-off). The GPU may run hotter
    (power limits and throttling still apply).
    For forge on Vulcan: faster prompt processing and Hunyuan3D; token
    generation (memory-bound) unchanged.
EOF
    ask "Build and install it, and add gpu_unlock.targets=all to the kernel command line?" \
        || { info "skipped"; return 0; }
    have git || sudo apt install -y git
    have make || sudo apt install -y build-essential
    if [ -d "$UNLOCK_DIR/.git" ]; then
        git -C "$UNLOCK_DIR" pull --ff-only
    else
        mkdir -p "$(dirname "$UNLOCK_DIR")"
        git clone "$UNLOCK_REPO" "$UNLOCK_DIR"
    fi
    info "commit $(git -C "$UNLOCK_DIR" rev-parse --short HEAD) - its README is the reference:"
    info "  $UNLOCK_DIR/README.md"
    (cd "$UNLOCK_DIR" && make install) || die "make install failed - see the README's install section"
    if grep -q "gpu_unlock.targets" /etc/default/grub; then
        ok "kernel parameter already in /etc/default/grub"
    else
        sudo cp /etc/default/grub /etc/default/grub.before-gpu-unlock
        sudo sed -i -E 's/^(GRUB_CMDLINE_LINUX_DEFAULT=")([^"]*)"/\1\2 gpu_unlock.targets=all"/' /etc/default/grub
        sudo sed -i -E 's/^(GRUB_CMDLINE_LINUX_DEFAULT=") /\1/' /etc/default/grub
        ok "added gpu_unlock.targets=all (old file: /etc/default/grub.before-gpu-unlock)"
    fi
    sudo update-grub
    warn "Reboot to load it, then:  $0 status   (journalctl -k -b | grep gpu_unlock)"
}

phase_gpu_unlock_off() {
    say "[gpu-unlock-off] remove the kernel parameter"
    if grep -q "gpu_unlock.targets" /etc/default/grub; then
        sudo sed -i -E 's/ ?gpu_unlock\.targets=[^" ]*//' /etc/default/grub
        sudo update-grub
        ok "removed - reboot to run without it (the module stays installed but does nothing)"
        info "to remove the module too, see 'Uninstall' in $UNLOCK_DIR/README.md"
    else
        ok "not on the kernel command line"
    fi
}

phase_status() {
    say "[status]"
    local s; s=$(strata_bin)
    [ -n "$s" ] && { "$s" launch --status 2>&1 | sed 's/^/    /' || true; }
    curl -fsS "http://127.0.0.1:$STRATA_PORT/health" 2>/dev/null | sed 's/^/    health: /' && echo || info "health: no answer on :$STRATA_PORT"
    for u in strata forge-shape coder sd-server; do info "$u: $(unit_state "$u")"; done
    if grep -q "gpu_unlock.targets" /proc/cmdline; then
        info "gpu-unlock: on the command line; kernel log:"
        journalctl -k -b 2>/dev/null | grep gpu_unlock | tail -5 | sed 's/^/      /' || true
    else
        info "gpu-unlock: off"
    fi
}

ALL=(check retire strata service key shape firewall)
[ $# -gt 0 ] || set -- "${ALL[@]}"
for p in "$@"; do
    case "$p" in
        check|retire|strata|service|key|shape|firewall|status) "phase_$p" ;;
        gpu-unlock) phase_gpu_unlock ;;
        gpu-unlock-off) phase_gpu_unlock_off ;;
        *) die "unknown phase '$p' - see the top of this file" ;;
    esac
    echo
done
say "Next, on Gemini: python3 ~/forge/apply-roles.py, then forge check"
