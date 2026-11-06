#!/usr/bin/env bash
#
# install-coder-models.sh - let forge choose each coder machine's model.
#
# Run on GEMINI, as your normal user, with coder-models.py next to this script:
#     ./install-coder-models.sh                 # apollo vulcan mercury
#     ./install-coder-models.sh vulcan          # just one machine
#
# What it does:
#   1. ssh from Gemini to each coder machine without a password (forge drives
#      the machines over ssh - nothing new listens on the network). Offers to
#      create a key and copy it if that does not work yet.
#   2. Copies coder-models.py to ~/llm-stack/ on each machine.
#   3. Apollo only: switches its llama-server to router mode (same model, same
#      name "tiel-coder", same tuned settings - forge sees no difference) so it
#      can hold more than one model. The old run script is kept;
#      `ssh apollo python3 ~/llm-stack/coder-models.py convert-router --undo`
#      puts it back. Vulcan and Mercury are already in router mode.
#   4. Timers on Gemini: nightly 03:17 `forge cache refresh` (newest commit of
#      every frequently used GitHub repository onto the SSD; each machine's
#      most used models kept current), and weekly (Mondays 08:41, after the cloud model watch):
#      `forge models refresh` - Claude searches for the best coding model per
#      language, forge checks each against Hugging Face, and the machines start
#      fetching the best Python and web models in the background.
#
set -euo pipefail

say()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
ok()   { printf '\033[1;32m ok\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m !!\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31mERR\033[0m %s\n' "$*" >&2; exit 1; }

HERE="$(cd "$(dirname "$0")" && pwd)"
[ -f "$HERE/coder-models.py" ] || die "coder-models.py is not next to this script."
[ "$(id -u)" -ne 0 ] || die "Run as your normal user."
case "$(hostname -s | tr '[:upper:]' '[:lower:]')" in
    gemini*) ;;
    *) warn "This is meant to run on Gemini (where forge runs). Continuing anyway." ;;
esac

HOSTS=("$@")
[ ${#HOSTS[@]} -eq 0 ] && HOSTS=(apollo vulcan mercury)
declare -A IP=([apollo]=192.168.50.8 [vulcan]=192.168.50.151 [mercury]=192.168.50.129)

# ---------------------------------------------------------------- ssh
if [ ! -f "$HOME/.ssh/id_ed25519" ] && [ ! -f "$HOME/.ssh/id_rsa" ]; then
    say "Creating an ssh key for forge (no passphrase, so timers can use it)"
    ssh-keygen -t ed25519 -N "" -f "$HOME/.ssh/id_ed25519" -C "forge@gemini" >/dev/null
fi
for h in "${HOSTS[@]}"; do
    if ! ssh -o BatchMode=yes -o ConnectTimeout=5 "$h" true 2>/dev/null; then
        if ! getent hosts "$h" >/dev/null && [ -n "${IP[$h]:-}" ]; then
            warn "'$h' does not resolve - adding a Host entry to ~/.ssh/config"
            mkdir -p "$HOME/.ssh"
            printf '\nHost %s\n    HostName %s\n    User %s\n' "$h" "${IP[$h]}" "$USER" >> "$HOME/.ssh/config"
            chmod 600 "$HOME/.ssh/config"
        fi
        say "Copying Gemini's ssh key to $h (asks for your password on $h once)"
        ssh-copy-id -o ConnectTimeout=5 "$h" || die "Could not reach $h over ssh."
        ssh -o BatchMode=yes "$h" true || die "ssh $h still asks for a password."
    fi
    ok "ssh $h works without a password"
done

# ---------------------------------------------------------- the script
for h in "${HOSTS[@]}"; do
    ssh -o BatchMode=yes "$h" 'mkdir -p ~/llm-stack ~/models/coders'
    scp -q "$HERE/coder-models.py" "$h:llm-stack/coder-models.py"
    ssh -o BatchMode=yes "$h" 'chmod +x ~/llm-stack/coder-models.py'
    ok "$h: ~/llm-stack/coder-models.py"
done

# ------------------------------------------------------ Apollo: router
if [[ " ${HOSTS[*]} " == *" apollo "* ]]; then
    st=$(ssh -o BatchMode=yes apollo python3 '~/llm-stack/coder-models.py' status || true)
    if echo "$st" | grep -q '"router": true'; then
        ok "apollo: already in router mode"
    else
        say "Apollo: switching llama-server to router mode (same model, same name)"
        echo "    It restarts Apollo's llama-server once; a forge run in progress would"
        echo "    wait for it (retry_minutes). Ctrl+C now to do it later."
        [ -t 0 ] && read -rp "    Press Enter to continue... "
        out=$(ssh -o BatchMode=yes apollo python3 '~/llm-stack/coder-models.py' convert-router) \
            || die "convert-router failed: $out"
        ok "apollo: $out"
        say "Waiting for Apollo's router, then loading tiel-coder once to check it"
        key="$HOME/.coder-api-key"
        for i in $(seq 1 60); do
            curl -sf "http://${IP[apollo]}:8080/health" >/dev/null && break
            sleep 3
        done
        if curl -sf -m 900 -H "Authorization: Bearer $(cat "$key" 2>/dev/null)" \
                -H 'Content-Type: application/json' "http://${IP[apollo]}:8080/v1/chat/completions" \
                -d '{"model":"tiel-coder","max_tokens":1,"messages":[{"role":"user","content":"ok"}]}' >/dev/null; then
            ok "apollo answers as tiel-coder in router mode"
        else
            warn "apollo did not answer - check: ssh apollo journalctl --user -u llama-server -n 50"
            warn "undo:  ssh apollo python3 ~/llm-stack/coder-models.py convert-router --undo"
        fi
    fi
fi

# --------------------------------------------------- the weekly timer
mkdir -p "$HOME/.config/systemd/user" "$HOME/forge/models"
cat > "$HOME/.config/systemd/user/forge-models.service" <<'EOF'
[Unit]
Description=forge - refresh the coding-model catalog and prefetch the best models
After=network-online.target

[Service]
Type=oneshot
EnvironmentFile=-%h/forge/env
Environment=PATH=%h/.npm-global/bin:%h/.local/bin:/usr/local/bin:/usr/bin:/bin
ExecStart=%h/forge/forge.py models refresh
TimeoutStartSec=3600
EOF
cat > "$HOME/.config/systemd/user/forge-models.timer" <<'EOF'
[Unit]
Description=Weekly coding-model refresh for forge (Mondays, after the model watch)

[Timer]
OnCalendar=Mon *-*-* 08:41:00
Persistent=true
RandomizedDelaySec=300

[Install]
WantedBy=timers.target
EOF
cat > "$HOME/.config/systemd/user/forge-cache.service" <<'EOF'
[Unit]
Description=forge - keep frequently used repositories and models current on the SSDs
After=network-online.target

[Service]
Type=oneshot
EnvironmentFile=-%h/forge/env
Environment=PATH=%h/.npm-global/bin:%h/.local/bin:/usr/local/bin:/usr/bin:/bin
ExecStart=%h/forge/forge.py cache refresh
TimeoutStartSec=3600
EOF
cat > "$HOME/.config/systemd/user/forge-cache.timer" <<'EOF'
[Unit]
Description=Nightly cache refresh for forge (GitHub repositories, coder models)

[Timer]
OnCalendar=*-*-* 03:17:00
Persistent=true
RandomizedDelaySec=600

[Install]
WantedBy=timers.target
EOF
systemctl --user daemon-reload
systemctl --user enable --now forge-models.timer forge-cache.timer
ok "forge-models.timer: $(systemctl --user list-timers forge-models.timer --no-legend | awk '{print $1, $2, $3}')"

cat <<'EOF'

Next:
  forge models refresh     # the first catalog now, instead of waiting for Monday
  forge models list        # what is in it, per language and machine
  forge models status      # each machine: models here, downloading, loaded
  forge cache list         # GitHub repositories kept on Gemini's SSD (refreshed nightly)

The planner then picks each machine's model per run; forge keeps TielCoder
until a new model has finished downloading and loaded. Settings: [models]
in ~/forge/config.toml (prefetch languages, disk budget per machine).
EOF
