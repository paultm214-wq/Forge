#!/usr/bin/env bash
#
# forgejo-setup.sh - run INSIDE the Forgejo container (LXC) on Metis, as root.
#
# Configures the Forgejo you already have running for the lab, keeps its
# data backed up to MetisOMV, and then forgejo-link.sh (on Gemini) points
# forge at it.
#
#   ./forgejo-setup.sh check            # find Forgejo: binary, app.ini, user, service
#   ./forgejo-setup.sh config           # lab settings in app.ini (backed up first)
#   ./forgejo-setup.sh admin NAME EMAIL # an admin account, if there is none yet
#   ./forgejo-setup.sh backup           # nightly `forgejo dump`, pushed to MetisOMV
#   ./forgejo-setup.sh status
#
# What `config` sets, and why:
#   [server]  ROOT_URL / DOMAIN / SSH_DOMAIN = this container's LAN address,
#             so clone URLs and links work from Gemini; OFFLINE_MODE = true
#             (no CDN or Gravatar - the web UI works with the internet down).
#   [service] DISABLE_REGISTRATION, REQUIRE_SIGNIN_VIEW = true: nobody can sign
#             up, and nothing - repositories, the API - is visible without
#             logging in. DEFAULT_KEEP_EMAIL_PRIVATE = true.
#   [repository] DEFAULT_PRIVATE = private.
#   [openid]  sign-in and sign-up off.
#   [cron.update_checker] ENABLED = false: no calls home.
# Anything else in app.ini is left as it is.
#
# Finds Forgejo whether it was installed as a binary with a systemd service
# (the usual LXC setup) or runs in Docker inside the container. Override
# what it finds with FORGEJO_INI=, FORGEJO_SERVICE=, FORGEJO_USER=,
# FORGEJO_HOST= (the address clients use) in front of the command.

set -euo pipefail

METIS_IP="192.168.50.126"          # MetisOMV, where backups go
BACKUP_USER="metisbackup"
BACKUP_KEY="/root/.ssh/metis_backup"
DUMP_DIR="/var/backups/forgejo"
DUMP_KEEP=3
DUMP_TIME="03:20"                  # after the machines' backups, before Metis's 04:00 snapshot

BOLD=$'\033[1m'; RED=$'\033[31m'; YEL=$'\033[33m'; GRN=$'\033[32m'; RST=$'\033[0m'
say()  { printf '%s==> %s%s\n' "$BOLD" "$*" "$RST"; }
info() { printf '    %s\n' "$*"; }
warn() { printf '%s    WARNING: %s%s\n' "$YEL" "$*" "$RST"; }
die()  { printf '%s    ERROR: %s%s\n' "$RED" "$*" "$RST" >&2; exit 1; }
ok()   { printf '%s    OK: %s%s\n' "$GRN" "$*" "$RST"; }
have() { command -v "$1" &>/dev/null; }

[ "$(id -u)" -eq 0 ] || die "Run as root inside the Forgejo container."

# ============================================================================
# Finding Forgejo
# ============================================================================

MODE=""            # systemd | docker
SERVICE=""         # systemd unit or docker container name
BIN=""
INI=""
RUN_AS=""
WORK=""

detect() {
    SERVICE="${FORGEJO_SERVICE:-}"
    if [ -z "$SERVICE" ] && have systemctl; then
        SERVICE=$(systemctl list-unit-files --type=service --no-legend 2>/dev/null |
                  awk '$1 ~ /^(forgejo|gitea)\.service$/ {print $1; exit}')
    fi
    if [ -n "$SERVICE" ] && [[ "$SERVICE" == *.service ]]; then
        MODE=systemd
        local exec; exec=$(systemctl show -p ExecStart --value "$SERVICE" 2>/dev/null)
        BIN=$(echo "$exec" | sed -n 's/.*path=\([^ ;]*\).*/\1/p')
        [ -n "$BIN" ] || BIN=$(command -v forgejo || true)
        INI="${FORGEJO_INI:-$(echo "$exec" | grep -oE -- '(--config|-c)[ =][^ ;]+' | head -1 | sed -E 's/^(--config|-c)[ =]//')}"
        RUN_AS="${FORGEJO_USER:-$(systemctl show -p User --value "$SERVICE")}"
        WORK=$(systemctl show -p WorkingDirectory --value "$SERVICE")
    elif have docker && docker ps --format '{{.Names}} {{.Image}}' | grep -qiE 'forgejo|gitea'; then
        MODE=docker
        SERVICE="${FORGEJO_SERVICE:-$(docker ps --format '{{.Names}} {{.Image}}' | grep -iE 'forgejo|gitea' | awk '{print $1; exit}')}"
        local data; data=$(docker inspect "$SERVICE" --format \
            '{{range .Mounts}}{{if eq .Destination "/data"}}{{.Source}}{{end}}{{end}}')
        INI="${FORGEJO_INI:-${data:+$data/gitea/conf/app.ini}}"
        RUN_AS="${FORGEJO_USER:-git}"
    else
        die "No forgejo.service and no Forgejo container found. Set FORGEJO_SERVICE= (and FORGEJO_INI=)."
    fi
    if [ -z "$INI" ]; then
        local c
        for c in /etc/forgejo/app.ini /var/lib/forgejo/custom/conf/app.ini \
                 /home/git/forgejo/custom/conf/app.ini /etc/gitea/app.ini; do
            [ -f "$c" ] && { INI="$c"; break; }
        done
    fi
    [ -n "$INI" ] && [ -f "$INI" ] || die "Could not find app.ini. Set FORGEJO_INI=/path/to/app.ini."
    [ -n "$RUN_AS" ] || RUN_AS=$(ini_get "" RUN_USER)
    [ -n "$RUN_AS" ] || RUN_AS=git
    [ -n "$WORK" ] || WORK=$(ini_get "" WORK_PATH)
}

# The value of KEY in [SECTION] ("" = before the first section).
ini_get() {
    awk -v sec="$1" -v key="$2" '
        /^[ \t]*\[/ { cur = $0; gsub(/^[ \t]*\[|\][ \t]*$/, "", cur); next }
        cur == sec && $0 ~ "^[ \t]*" key "[ \t]*=" {
            sub(/^[^=]*=[ \t]*/, ""); sub(/[ \t]+$/, ""); print; exit }
    ' "$INI"
}

# Set KEY = VALUE in [SECTION], replacing the key or adding it (and the
# section) - everything else in the file is kept, including its owner.
ini_set() {
    local tmp; tmp=$(mktemp)
    awk -v sec="$1" -v key="$2" -v val="$3" '
        function add() { if (insec && !done) { print key " = " val; done = 1 } }
        function flush() { printf "%s", blanks; blanks = "" }
        /^[ \t]*$/ { blanks = blanks $0 "\n"; next }
        /^[ \t]*\[/ {
            add(); flush()
            cur = $0; gsub(/^[ \t]*\[|\][ \t]*$/, "", cur)
            insec = (cur == sec); if (insec) found = 1
            print; next
        }
        insec && $0 ~ "^[ \t]*" key "[ \t]*=" {
            flush(); if (!done) { print key " = " val; done = 1 }; next }
        { flush(); print }
        END { add(); flush(); if (!found) { print ""; print "[" sec "]"; print key " = " val } }
    ' "$INI" > "$tmp"
    cat "$tmp" > "$INI"
    rm -f "$tmp"
}

# Run the forgejo CLI as Forgejo's user.
fj() {
    if [ "$MODE" = docker ]; then
        docker exec -u "$RUN_AS" "$SERVICE" forgejo "$@" 2>/dev/null || \
            docker exec -u "$RUN_AS" "$SERVICE" gitea "$@"
    else
        local args=(--config "$INI")
        [ -n "$WORK" ] && args+=(--work-path "$WORK")
        runuser -u "$RUN_AS" -- "$BIN" "${args[@]}" "$@"
    fi
}

restart() {
    if [ "$MODE" = docker ]; then docker restart "$SERVICE" >/dev/null
    else systemctl restart "$SERVICE"; fi
}

http_port() { local p; p=$(ini_get server HTTP_PORT); echo "${p:-3000}"; }

# Is Forgejo up? Any HTTP answer from its health endpoint counts except a
# 5xx: with REQUIRE_SIGNIN_VIEW the API itself answers 403 to anonymous
# requests, which is the setting working, not Forgejo being down. Tries
# localhost, then the LAN address (HTTP_ADDR may bind only that).
answering() {
    local host code
    for host in 127.0.0.1 "$(lan_ip)"; do
        [ -n "$host" ] || continue
        code=$(curl -s -m 3 -o /dev/null -w '%{http_code}' "http://$host:$(http_port)/api/healthz" || true)
        if [ "$code" != 000 ] && [ "${code:0:1}" != 5 ]; then return 0; fi
    done
    return 1
}

lan_ip() {
    [ -n "${FORGEJO_HOST:-}" ] && { echo "$FORGEJO_HOST"; return; }
    ip -4 route get "$METIS_IP" 2>/dev/null | awk '{for (i = 1; i < NF; i++) if ($i == "src") {print $(i + 1); exit}}'
}

# ============================================================================
# Phases
# ============================================================================

phase_check() {
    say "[check] Forgejo in this container"
    detect
    ok "$MODE: $SERVICE"
    info "app.ini:  $INI"
    info "runs as:  $RUN_AS${WORK:+, work path $WORK}"
    [ "$MODE" = systemd ] && info "binary:   $BIN ($("$BIN" --version 2>/dev/null | head -1))"
    info "address:  http://$(lan_ip):$(http_port)/"
    [ "$(ini_get security INSTALL_LOCK)" = "true" ] || \
        warn "INSTALL_LOCK is not true: finish the web installer (http://$(lan_ip):$(http_port)/) first."
    if answering; then
        ok "answering on :$(http_port)"
    else
        warn "not answering on :$(http_port)"
    fi
}

phase_config() {
    say "[config] lab settings in app.ini"
    detect
    [ "$(ini_get security INSTALL_LOCK)" = "true" ] || \
        die "Finish Forgejo's web installer first (INSTALL_LOCK is not true), then run this."
    local host port; host=$(lan_ip); port=$(http_port)
    [ -n "$host" ] || die "Could not tell this container's LAN address. Set FORGEJO_HOST=."
    local backup; backup="$INI.bak-$(date +%Y%m%d-%H%M%S)"
    cp -p "$INI" "$backup"
    ok "backed up to $backup"
    ini_set server ROOT_URL "http://$host:$port/"
    ini_set server DOMAIN "$host"
    ini_set server SSH_DOMAIN "$host"
    ini_set server OFFLINE_MODE true
    ini_set service DISABLE_REGISTRATION true
    ini_set service REQUIRE_SIGNIN_VIEW true
    ini_set service DEFAULT_KEEP_EMAIL_PRIVATE true
    ini_set repository DEFAULT_PRIVATE private
    ini_set openid ENABLE_OPENID_SIGNIN false
    ini_set openid ENABLE_OPENID_SIGNUP false
    ini_set cron.update_checker ENABLED false
    diff -u "$backup" "$INI" | sed -n '3,$p' | grep '^[-+]' | sed 's/^/      /' || true
    restart
    for _ in $(seq 1 30); do
        answering && break
        sleep 1
    done
    answering || die "Forgejo did not come back - restore with: cp -p $backup $INI, then restart it."
    ok "Forgejo restarted: http://$host:$port/"
    info "Next: ./forgejo-setup.sh admin NAME EMAIL (if you have no admin account yet),"
    info "then ./forgejo-setup.sh backup, then forgejo-link.sh on Gemini."
}

phase_admin() {
    local name="$1" email="$2"
    say "[admin] administrator account"
    detect
    if fj admin user list --admin 2>/dev/null | awk 'NR > 1' | grep -q .; then
        ok "an administrator already exists:"
        fj admin user list --admin | sed 's/^/      /'
        return 0
    fi
    fj admin user create --admin --username "$name" --email "$email" --random-password \
        --must-change-password=false | sed 's/^/      /'
    ok "created $name - the password is printed above; keep it in your password manager"
}

write_backup_runner() {
    cat <<RUNNER
#!/usr/bin/env bash
# /usr/local/sbin/forgejo-backup - generated by forgejo-setup.sh. Runs as root.
set -euo pipefail
log() { echo "\$(date '+%F %T') \$*"; }
install -d -m 700 -o "$RUN_AS" "$DUMP_DIR"
f="$DUMP_DIR/forgejo-dump-\$(date +%Y%m%d-%H%M%S).tar.gz"
RUNNER
    if [ "$MODE" = docker ]; then
        cat <<RUNNER
docker exec -u "$RUN_AS" "$SERVICE" sh -c 'cd /tmp && forgejo dump --type tar.gz --file /tmp/forgejo-dump.tar.gz --skip-log'
docker cp "$SERVICE:/tmp/forgejo-dump.tar.gz" "\$f"
docker exec "$SERVICE" rm -f /tmp/forgejo-dump.tar.gz
RUNNER
    else
        cat <<RUNNER
cd "$DUMP_DIR"
runuser -u "$RUN_AS" -- "$BIN" --config "$INI" ${WORK:+--work-path "$WORK"} dump --type tar.gz --file "\$f" --skip-log
RUNNER
    fi
    cat <<RUNNER
log "dumped \$(du -h "\$f" | cut -f1) to \$f"
# Keep the newest $DUMP_KEEP here; MetisOMV's snapshots keep the history.
ls -1t "$DUMP_DIR"/forgejo-dump-*.tar.gz | tail -n +$((DUMP_KEEP + 1)) | xargs -r rm -f
rsync -a --delete -e "ssh -i $BACKUP_KEY -o BatchMode=yes -o StrictHostKeyChecking=accept-new -o ConnectTimeout=20" \\
      "$DUMP_DIR/" "$BACKUP_USER@$METIS_IP:"
log "pushed to MetisOMV"
RUNNER
}

phase_backup() {
    say "[backup] nightly forgejo dump, pushed to MetisOMV ($METIS_IP)"
    detect
    have rsync && have ssh || { apt-get update -qq && apt-get install -y -qq rsync openssh-client; }
    write_backup_runner > /usr/local/sbin/forgejo-backup
    chmod 755 /usr/local/sbin/forgejo-backup
    bash -n /usr/local/sbin/forgejo-backup || die "generated runner failed its syntax check"
    if [ ! -f "$BACKUP_KEY" ]; then
        install -d -m 700 /root/.ssh
        ssh-keygen -q -t ed25519 -N '' -C "metis-backup-forgejo" -f "$BACKUP_KEY"
    fi
    cat > /etc/systemd/system/forgejo-backup.service <<'EOF'
[Unit]
Description=Dump Forgejo and push it to MetisOMV
After=network-online.target
[Service]
Type=oneshot
ExecStart=/usr/local/sbin/forgejo-backup
Nice=10
EOF
    cat > /etc/systemd/system/forgejo-backup.timer <<EOF
[Unit]
Description=Nightly Forgejo backup
[Timer]
OnCalendar=*-*-* $DUMP_TIME:00
RandomizedDelaySec=120
Persistent=true
[Install]
WantedBy=timers.target
EOF
    systemctl daemon-reload
    systemctl enable forgejo-backup.timer >/dev/null
    ok "timer: nightly at $DUMP_TIME (enabled)"
    cat <<EOF

    ${BOLD}Authorise this container on MetisOMV${RST} - as root there:

      ./metis-setup.sh datasets user        # once, if forgejo is new to it
      ./metis-setup.sh add-host forgejo '$(cat "$BACKUP_KEY.pub")'

    Then here:  systemctl start forgejo-backup && journalctl -u forgejo-backup -n 20
EOF
}

phase_status() {
    detect
    say "Forgejo ($MODE: $SERVICE)"
    info "address:   http://$(lan_ip):$(http_port)/"
    info "settings:  registration $( [ "$(ini_get service DISABLE_REGISTRATION)" = true ] && echo off || echo ON ), sign-in to view $( [ "$(ini_get service REQUIRE_SIGNIN_VIEW)" = true ] && echo required || echo NOT required )"
    local last; last=$(ls -1t "$DUMP_DIR"/forgejo-dump-*.tar.gz 2>/dev/null | head -1 || true)
    info "last dump: ${last:-none yet}"
    systemctl list-timers forgejo-backup.timer --no-pager 2>/dev/null | sed 's/^/    /' || true
}

case "${1:-help}" in
    check|config|backup|status) "phase_$1" ;;
    admin) [ $# -eq 3 ] || die "usage: admin NAME EMAIL"; phase_admin "$2" "$3" ;;
    *) awk 'NR > 1 && /^set -euo/ {exit} NR > 1' "$0" ;;
esac
