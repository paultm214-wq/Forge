#!/usr/bin/env bash
#
# install-lab-dashboard.sh - the lab dashboard: one page for every machine.
#
#   On EVERY machine (Gemini, Apollo, Vulcan, Mercury; Metis and Hades optional):
#       ./install-lab-dashboard.sh agent
#   Then on Gemini only:
#       ./install-lab-dashboard.sh hub
#       ./install-lab-dashboard.sh controls    (buttons: restart servers, clear
#                                               VRAM, resume forge - see below)
#   Open http://192.168.50.96:8095 - in a browser, or on the iPad / iPhone:
#   Safari > Share > Add to Home Screen, and it opens full screen like an app.
#
#   ./install-lab-dashboard.sh agent --reconfigure   rewrites this machine's
#   lab-agent.toml for its current role (the old one is kept as .bak) - after
#   the October 30 role change (Vulcan codes, Apollo renders) run it on
#   Vulcan and Apollo, and `hub --reconfigure` on Gemini.
#
# CONTROLS: `controls` (on Gemini) makes a random token, puts it in the hub's
# config and - over ssh, with the keys forge already uses - in each
# machine's lab-agent.toml, and restarts them. The agents then accept a short
# FIXED list of actions from the hub only (restart their model or image
# server, unload models, stop/start Strata or the shape server); the hub
# accepts them only from the LAN. Set `pin = "1234"` in
# ~/.config/lab-dashboard.toml to also require a PIN (asked once per device).
#
# Run as your normal user, not with sudo: both parts are user services, like
# llama-server, so they can read your model servers' API keys and see which of
# your processes hold GPU memory. The script asks for sudo only to keep user
# services running without a login (linger) and to open the firewall port.
#
# MetisOMV is the exception: OMV has no everyday user, so there the agent is
# installed as root - as a SYSTEM service that runs under its own throwaway,
# unprivileged account (systemd DynamicUser), read-only everywhere. It has no
# model servers or GPUs to read, so it needs nothing more.
#
# Put lab-agent.py and lab-dashboard.py next to this script first.
#
set -euo pipefail

ROLE="${1:-}"
RECONF=false
[ "${2:-}" = "--reconfigure" ] && RECONF=true
APP_DIR="$HOME/apps/lab-dashboard"
UNIT_DIR="$HOME/.config/systemd/user"
AGENT_PORT=9101
HUB_PORT=8095
LAN="192.168.50.0/24"
HERE="$(cd "$(dirname "$0")" && pwd)"

say()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
ok()   { printf '\033[1;32m ok\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m !!\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31mERR\033[0m %s\n' "$*" >&2; exit 1; }

case "$ROLE" in agent|hub|controls) ;; *) die "usage: $0 agent|hub|controls [--reconfigure]" ;; esac
AS_SYSTEM=false
if [ "$(id -u)" -eq 0 ]; then
    host="$(hostname | tr '[:upper:]' '[:lower:]')"
    [ "$ROLE" = agent ] && [[ "$host" == metis* ]] || \
        die "Run as your normal user, not root (only Metis's agent installs as root)."
    AS_SYSTEM=true
    sudo() { "$@"; }        # already root
fi
python3 -c 'import sys; sys.exit(sys.version_info < (3, 11))' || die "Needs Python 3.11 or newer (tomllib)."

if $AS_SYSTEM; then
    APP_DIR=/opt/lab-dashboard
    UNIT_DIR=/etc/systemd/system
fi
mkdir -p "$APP_DIR" "$UNIT_DIR"
$AS_SYSTEM || mkdir -p "$HOME/.config"

linger() {
    if [ "$(loginctl show-user "$USER" -p Linger --value 2>/dev/null)" != "yes" ]; then
        say "Keeping user services running without a login (loginctl enable-linger)"
        sudo loginctl enable-linger "$USER"
    fi
}

open_port() {   # $1 port, $2 what
    if command -v ufw >/dev/null && sudo ufw status 2>/dev/null | grep -q "^Status: active"; then
        sudo ufw allow from "$LAN" to any port "$1" proto tcp comment "$2" >/dev/null
        ok "firewall: port $1 open to $LAN"
    fi
}

# ----------------------------------------------------------------- agent
if [ "$ROLE" = agent ]; then
    [ -f "$HERE/lab-agent.py" ] || die "lab-agent.py is not next to this script."
    install -m 755 "$HERE/lab-agent.py" "$APP_DIR/lab-agent.py"
    host="$(hostname | tr '[:upper:]' '[:lower:]')"
    conf="$HOME/.config/lab-agent.toml"
    $AS_SYSTEM && conf=/etc/lab-agent.toml
    keep_token=""
    if $RECONF && [ -f "$conf" ]; then
        keep_token=$(sed -n 's/^control_token *= *"\(.*\)"/\1/p' "$conf" | head -1)
        mv "$conf" "$conf.bak"
        say "Rewriting $conf (the old one is $conf.bak)"
    fi
    if [ -f "$conf" ]; then
        warn "Keeping your existing $conf"
    else
        say "Writing $conf for $host"
        {
            echo "port = $AGENT_PORT"
            case "$host" in
            gemini*)
                echo 'llm = [{name = "llama-server", url = "http://127.0.0.1:8080", key_file = "~/.llama-api-key", unit = "llama-server"}]'
                echo 'user_units = ["llama-server", "lab-dashboard", "forge-inbox.path", "forge-pending.timer"]'
                echo 'system_units = ["docker"]'
                echo 'disks = ["/", "~/models"]' ;;
            apollo*)
                # Coder (router) and, from 30 Oct, the primary image server.
                echo 'llm = [{name = "coder", url = "http://127.0.0.1:8080", key_file = "~/.coder-api-key", unit = "llama-server"}]'
                echo 'sd = [{name = "sd-server", url = "http://127.0.0.1:7860", unit = "sd-server"}]'
                echo 'user_units = ["llama-server", "sd-server"]'
                echo 'disks = ["/", "~/models"]' ;;
            vulcan*)
                # The principal coder: Strata (port 8081), the TielCoder router
                # kept as the fallback, and the shape server for 3D models.
                echo 'llm = [{name = "strata", url = "http://127.0.0.1:8081", key_file = "~/.strata-api-key", unit = "strata"},'
                echo '       {name = "coder", url = "http://127.0.0.1:8080", key_file = "~/.coder-api-key", unit = "coder"}]'
                echo 'user_units = ["strata", "coder", "forge-shape"]'
                echo 'control_units = ["strata", "forge-shape"]'
                echo 'disks = ["/", "~/models"]' ;;
            mercury*)
                echo 'llm = [{name = "coder", url = "http://127.0.0.1:8080", key_file = "~/.coder-api-key", unit = "coder"}]'
                echo 'sd = [{name = "sd-server", url = "http://127.0.0.1:7860", unit = "sd-server"}]'
                echo 'user_units = ["coder", "sd-server"]'
                echo 'disks = ["/", "~/models"]' ;;
            hades*)
                # The gaming PC (Bazzite, Arc B580). Nothing serves models yet.
                echo 'disks = ["/var"]' ;;
            metis*)
                echo 'system_units = ["nfs-server", "zfs-zed", "sanoid.timer", "apt-cacher-ng", "docker"]'
                # The root and each ZFS pool (its top dataset's mountpoint).
                disks='"/"'
                if command -v zpool >/dev/null; then
                    for pool in $(zpool list -H -o name 2>/dev/null); do
                        mp=$(zfs get -H -o value mountpoint "$pool" 2>/dev/null || true)
                        [ -d "$mp" ] && disks+=", \"$mp\""
                    done
                fi
                echo "disks = [$disks]" ;;
            *)
                warn "Unknown machine '$host': writing a config with no model servers - edit $conf"
                echo 'disks = ["/"]' ;;
            esac
            [ -n "$keep_token" ] && echo "control_token = \"$keep_token\""
        } > "$conf"
    fi
    if $AS_SYSTEM; then
        chmod 644 "$conf"
        cat > "$UNIT_DIR/lab-agent.service" <<EOF
[Unit]
Description=Lab dashboard agent (this machine's readings on port $AGENT_PORT)
After=network-online.target

[Service]
ExecStart=/usr/bin/python3 $APP_DIR/lab-agent.py
Environment=LAB_AGENT_CONFIG=$conf
# Its own throwaway account, read-only everywhere: it only reads /proc,
# /sys, disk usage and unit states.
DynamicUser=yes
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
NoNewPrivileges=yes
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
        systemctl daemon-reload
        systemctl enable --now lab-agent
        systemctl restart lab-agent
    else
        cat > "$UNIT_DIR/lab-agent.service" <<EOF
[Unit]
Description=Lab dashboard agent (this machine's readings on port $AGENT_PORT)
After=network-online.target

[Service]
ExecStart=/usr/bin/python3 $APP_DIR/lab-agent.py
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
EOF
        linger
        systemctl --user daemon-reload
        systemctl --user enable --now lab-agent
        systemctl --user restart lab-agent
    fi
    open_port "$AGENT_PORT" "lab-agent"
    sleep 4
    if python3 -c "import urllib.request, sys; urllib.request.urlopen('http://127.0.0.1:$AGENT_PORT/health', timeout=5)" 2>/dev/null; then
        ok "lab-agent is answering on port $AGENT_PORT"
        say "What it sees right now:"
        LAB_AGENT_CONFIG="$conf" python3 "$APP_DIR/lab-agent.py" --once | python3 -c '
import json, sys
d = json.load(sys.stdin)
G = 2 ** 30
for g in d["gpus"]:
    print("   GPU  %s: %.1f/%.1f GB, busy %s%%, temps %s" % (g["name"], g["vram_used"] / G, g["vram_total"] / G, g["busy"], g["temps"]))
for l in d["llm"]:
    print("   LLM  %s: %s %s" % (l["name"], l["state"], l.get("model") or ""))
for s in d["sd"]:
    print("   SD   %s: %s" % (s["name"], s["state"]))
for u, st in d["services"].items():
    print("   unit %s: %s" % (u, st))
h = d["host"]
print("   CPU  %s C, RAM %.1f/%.1f GB" % (h["cpu_temp"], h["ram_used"] / G, h["ram_total"] / G))
'
    else
        warn "lab-agent did not answer - see: journalctl $($AS_SYSTEM || echo --user) -u lab-agent -n 50"
    fi
    exit 0
fi

# -------------------------------------------------------------- controls
if [ "$ROLE" = controls ]; then
    hubconf="$HOME/.config/lab-dashboard.toml"
    [ -f "$hubconf" ] || die "Install the hub first: $0 hub"
    tok=$(sed -n 's/^control_token *= *"\(.*\)"/\1/p' "$hubconf" | head -1)
    if [ -z "$tok" ]; then
        tok=$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))')
        # Top-level keys must come before the first [[nodes]] table.
        python3 - "$hubconf" "$tok" <<'PY'
import sys
p, tok = sys.argv[1], sys.argv[2]
lines = open(p).read().splitlines(True)
i = next((n for n, l in enumerate(lines) if l.lstrip().startswith("[")), len(lines))
lines.insert(i, f'control_token = "{tok}"\n')
open(p, "w").write("".join(lines))
PY
        chmod 600 "$hubconf"
        ok "New control token in $hubconf"
    fi
    set_token='import re, sys, pathlib
p = pathlib.Path(sys.argv[1]).expanduser(); tok = sys.argv[2]
t = p.read_text() if p.exists() else ""
t = re.sub(r"(?m)^control_token *=.*\n?", "", t)
lines = t.splitlines(True)
i = next((n for n, l in enumerate(lines) if l.lstrip().startswith("[")), len(lines))
lines.insert(i, f"control_token = \"{tok}\"\n")
p.write_text("".join(lines)); p.chmod(0o600)'
    say "This machine (Gemini)"
    python3 -c "$set_token" "~/.config/lab-agent.toml" "$tok" && systemctl --user restart lab-agent \
        && ok "gemini: controls on" || warn "gemini: could not set the token"
    for h in ${CONTROL_HOSTS:-apollo vulcan mercury}; do
        if ssh -o BatchMode=yes -o ConnectTimeout=8 "$h" \
               "python3 -c '$set_token' '~/.config/lab-agent.toml' '$tok' && systemctl --user restart lab-agent" \
               </dev/null >/dev/null 2>&1; then
            ok "$h: controls on"
        else
            warn "$h: not reachable over ssh (or no lab-agent) - run there:  ./install-lab-dashboard.sh agent, then this again"
        fi
    done
    systemctl --user restart lab-dashboard && ok "hub restarted - the detail pages now have controls"
    say "Metis's agent stays read-only (a locked-down system service). Add hosts with CONTROL_HOSTS=\"apollo vulcan mercury hades\"."
    exit 0
fi

# ------------------------------------------------------------------- hub
[ -f "$HERE/lab-dashboard.py" ] || die "lab-dashboard.py is not next to this script."
install -m 755 "$HERE/lab-dashboard.py" "$APP_DIR/lab-dashboard.py"
conf="$HOME/.config/lab-dashboard.toml"
if $RECONF && [ -f "$conf" ]; then
    hub_keep=$(grep -E '^(control_token|pin|token) *=' "$conf" || true)
    mv "$conf" "$conf.bak"
    say "Rewriting $conf (the old one is $conf.bak)"
fi
if [ -f "$conf" ]; then
    warn "Keeping your existing $conf"
else
    { [ -n "${hub_keep:-}" ] && echo "$hub_keep"; cat <<'EOF'
port = 8095
forge_status = "~/forge/runs/.forge-status.json"
forge = "~/forge/forge.py"
forge_config = "~/forge/config.toml"
lan = "192.168.50.0/24"     # controls only from here
# pin = "1234"              # also ask for a PIN before any control
# allowed_hosts = ["lab.example.org"]   # names besides IPs and one-word/.local/.lan
#                                       # names that controls may be used through

[[nodes]]
name = "Gemini"
host = "127.0.0.1"
lan = "192.168.50.96"
role = "Planner · reviewer · chat · Home Assistant"

[[nodes]]
name = "Vulcan"
host = "192.168.50.151"
role = "Principal coder (Strata) · 3D models"

[[nodes]]
name = "Apollo"
host = "192.168.50.8"
role = "Images (full steps) · coder"

[[nodes]]
name = "Mercury"
host = "192.168.50.129"
role = "Images (Lightning) · triage"

[[nodes]]
name = "Hades"
host = "hades"           # its address once it has a DHCP reservation
role = "Gaming · spare GPU"
optional = true          # hidden until its lab-agent answers

[[nodes]]
name = "Metis"
host = "192.168.50.126"
role = "Backups · caches · git"
optional = true
EOF
    } > "$conf"
    ok "Wrote $conf"
fi
cat > "$UNIT_DIR/lab-dashboard.service" <<EOF
[Unit]
Description=Lab dashboard (every machine and forge on port $HUB_PORT)
After=network-online.target

[Service]
ExecStart=/usr/bin/python3 $APP_DIR/lab-dashboard.py
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
EOF
linger
systemctl --user daemon-reload
systemctl --user enable --now lab-dashboard
systemctl --user restart lab-dashboard
open_port "$HUB_PORT" "lab-dashboard"
sleep 4
if curl -sf "http://127.0.0.1:$HUB_PORT/health" >/dev/null; then
    ok "Dashboard: http://192.168.50.96:$HUB_PORT"
    curl -s "http://127.0.0.1:$HUB_PORT/api/state" | python3 -c '
import json, sys
for n in json.load(sys.stdin)["nodes"]:
    if n.get("absent"):
        continue
    a = n.get("activity") or {}
    print("   %-8s %-12s %s" % (n["name"], a.get("label", "?"), n.get("error") or ""))
' || true
else
    warn "The dashboard did not answer - see: journalctl --user -u lab-dashboard -n 50"
fi
if systemctl is-active --quiet apollo-dashboard 2>/dev/null; then
    warn "The old apollo-dashboard is still running on port 8090 (glassinv also wants 8090)."
    warn "Once you are happy with this one:  sudo systemctl disable --now apollo-dashboard"
fi
say "iPad / iPhone: open http://192.168.50.96:$HUB_PORT in Safari > Share > Add to Home Screen."
say "Buttons: ./install-lab-dashboard.sh controls  (once)"
