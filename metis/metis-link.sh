#!/usr/bin/env bash
#
# metis-link.sh - run on Gemini (192.168.50.96), Apollo (.8), Vulcan (.151)
# and Mercury (.129), as your normal user. On Vulcan, from Kubuntu (the
# Bazzite side is a separate install and is not backed up).
#
# Connects this machine to MetisOMV (192.168.50.126):
#
#   nfs      mounts the shared pool at /mnt/metis  (library/exchange/scratch)
#   backup   nightly rsync of configs, keys and state to MetisZFS/backups
#   caches   use Metis's apt, PyPI and Docker caches - with a fallback to the
#            internet whenever Metis does not answer
#   archive  (Gemini) monthly, append-only archive of forge projects/runs
#   verify   runs a backup now, then proves it can be read back
#   status   last backup result
#
# ORDER: run 'backup' here first - it prints a public key and the exact
# command to authorise it on MetisOMV. Run that there, then 'verify' here.
#
# WHAT IS BACKED UP - and what deliberately is not
# ------------------------------------------------
# Backed up: everything that took real effort to get right. run scripts,
# systemd units, API keys, forge (config, env, run history), forge-projects,
# the llm-stack compose files, Open WebUI's chat database, RGB config, Pi
# config, and the /etc changes the installers made (ufw, modprobe, ld.so,
# udev, apt sources).
#
# NOT backed up: models, llama.cpp / stable-diffusion.cpp builds, venvs,
# caches - and Steam: its install, every game library (steamapps/,
# SteamLibrary/) and the game files forge downloads or extracts
# (~/forge/games: the Garry's Mod server, Fallout 4's script sources), plus
# forge's repository and toolchain caches. All re-downloadable or
# rebuildable - and at tens of GB they would blow straight through the
# per-host quota. *.gguf and *.safetensors are excluded outright as a safety
# net. --delete-excluded removes anything newly excluded from the copy on
# Metis at the next run (ZFS snapshots keep it until they expire).
#
# Usage:  ./metis-link.sh [nfs|backup|caches|archive|verify|status|all]

set -euo pipefail

# ============================================================================
# CONFIG
# ============================================================================

METIS_IP="192.168.50.126"
APT_CACHE_PORT=3142
PYPI_CACHE_PORT=5001
REGISTRY_CACHE_PORT=5000
BACKUP_USER="metisbackup"
REMOTE="${BACKUP_USER}@${METIS_IP}"
NFS_MOUNT="/mnt/metis"
NFS_SHARE="metis-shared"

# Which machine is this? Derived from the hostname; override if yours differ.
HOST_ROLE="${HOST_ROLE:-$(hostname -s | tr '[:upper:]' '[:lower:]')}"

# Staggered so the machines never contend for Metis's network link at once.
# MetisOMV snapshots at 04:00, after all of them (and Forgejo's dump at
# 03:20) have finished. A machine that is off - Vulcan in Bazzite - catches
# up when it is next up (Persistent=true).
declare -A BACKUP_TIME=( [gemini]="02:00" [apollo]="02:20" [mercury]="02:40" [vulcan]="03:00" )

# Optional: Home Assistant webhook, POSTed on backup failure so a silent
# failure cannot go unnoticed for weeks. e.g.
#   http://192.168.50.162:8123/api/webhook/metis-backup-failed
HA_WEBHOOK_URL="${HA_WEBHOOK_URL:-}"

CONF_DIR="/etc/metis-backup"
STATE_DIR="/var/lib/metis-backup"
BACKUP_KEY="/root/.ssh/metis_backup"
ARCHIVE_KEY="/root/.ssh/metis_archive"

# ============================================================================
# HELPERS
# ============================================================================

BOLD=$'\033[1m'; RED=$'\033[31m'; YEL=$'\033[33m'; GRN=$'\033[32m'; RST=$'\033[0m'
say()  { printf '%s==> %s%s\n' "$BOLD" "$*" "$RST"; }
info() { printf '    %s\n' "$*"; }
warn() { printf '%s    WARNING: %s%s\n' "$YEL" "$*" "$RST"; }
die()  { printf '%s    ERROR: %s%s\n' "$RED" "$*" "$RST" >&2; exit 1; }
ok()   { printf '%s    OK: %s%s\n' "$GRN" "$*" "$RST"; }
have() { command -v "$1" &>/dev/null; }

check_role() {
    case "$HOST_ROLE" in
        gemini|apollo|vulcan|mercury) ;;
        *) die "Hostname '$HOST_ROLE' is not gemini, apollo, vulcan or mercury.
    Run with HOST_ROLE=gemini (etc.) in front of the command." ;;
    esac
    [ "$(id -u)" -ne 0 ] || die "Run as your normal user; it uses sudo where needed.
    (Under sudo, \$HOME would be /root and the backup would miss your files.)"
}

# ============================================================================
# PHASE: nfs
# ============================================================================

phase_nfs() {
    say "[nfs] mounting the shared pool at $NFS_MOUNT"
    check_role
    sudo apt-get install -y nfs-common

    if [ "$(id -u)" != 1000 ]; then
        warn "Your uid is $(id -u), not 1000. The share maps every write to"
        warn "uid 1000, so files will show another owner here. Harmless, but"
        warn "surprising - see SHARE_UID in metis-setup.sh."
    fi

    sudo mkdir -p "$NFS_MOUNT"

    # OMV exports under an NFSv4 pseudo-root at /export, so the v4 path is
    # usually /<share> - but it varies with OMV configuration. Try both and
    # keep whichever actually mounts, rather than guessing.
    local src="" cand out errs=""
    for cand in ${NFS_PATH:-"/$NFS_SHARE" "/export/$NFS_SHARE"}; do
        if out=$(sudo mount -t nfs4 -o ro,soft,timeo=50,retrans=1 \
                     "$METIS_IP:$cand" "$NFS_MOUNT" 2>&1); then
            sudo umount "$NFS_MOUNT"
            src="$METIS_IP:$cand"
            break
        fi
        errs+="      $cand: ${out##*: }"$'\n'
    done
    if [ -z "$src" ]; then
        warn "Could not mount $NFS_SHARE from $METIS_IP. What the mount said:"
        printf '%s' "$errs"
        info "What Metis exports (showmount -e $METIS_IP):"
        showmount -e "$METIS_IP" 2>&1 | sed 's/^/      /' || true
        cat <<EOF
    - "access denied": the export has no entry for THIS machine
      ($(hostname -I | awk '{print $1}')), or OMV's pending changes were not applied.
    - "No such file or directory": the export lives at another path - rerun
      with the path showmount lists, e.g.  NFS_PATH=/export/$NFS_SHARE ./metis-link.sh nfs
    - "Connection refused" / "timed out": NFS is not enabled in OMV
      (Services > NFS > Settings), or a firewall is in the way.
    - Nothing listed at all: the shared folder may not be exported yet.
EOF
        return 1
    fi
    ok "export found at $src"

    # hard: a stalled server makes I/O wait, not fail - failing writes can
    # silently corrupt files. nofail + automount: boot never hangs waiting
    # for Metis, and the mount reappears by itself after Metis reboots.
    local opts="rw,hard,noatime,nofail,_netdev,x-systemd.automount"
    opts+=",x-systemd.idle-timeout=600,x-systemd.mount-timeout=30"
    local line="$src  $NFS_MOUNT  nfs4  $opts  0 0"
    sudo sed -i "\\|[[:space:]]$NFS_MOUNT[[:space:]]|d" /etc/fstab
    echo "$line" | sudo tee -a /etc/fstab >/dev/null
    sudo systemctl daemon-reload
    # The automount unit systemd generates from that line; restarting
    # remote-fs.target does not always start a NEW one.
    local unit; unit="$(systemd-escape --path "$NFS_MOUNT").automount"
    sudo systemctl restart remote-fs.target
    sudo systemctl start "$unit" || true

    if ! ls "$NFS_MOUNT/library" &>/dev/null; then
        if findmnt -t nfs4 "$NFS_MOUNT" &>/dev/null; then
            warn "$NFS_MOUNT is mounted, but has no library/ folder. It holds:"
            ls -A "$NFS_MOUNT" 2>&1 | head -10 | sed 's/^/      /'
            warn "The OMV shared folder probably points somewhere other than the"
            warn "Metis1000/shared dataset - check Storage > Shared Folders > metis-shared."
            return 1
        fi
        warn "The automount did not come up:"
        systemctl status "$unit" "${unit%.automount}.mount" --no-pager 2>&1 | tail -15 | sed 's/^/      /'
        return 1
    fi
    if ls "$NFS_MOUNT/library" &>/dev/null; then
        ok "mounted: $(ls "$NFS_MOUNT" | tr '\n' ' ')"
        local t="$NFS_MOUNT/scratch/.write-test-$HOST_ROLE"
        if touch "$t" 2>/dev/null && rm -f "$t"; then
            ok "writable"
        else
            warn "Mounted but NOT writable. Check the export is Read/Write and the"
            warn "extra options include all_squash,anonuid=1000,anongid=1000."
        fi
    fi
    info "If Metis goes away, anything reading $NFS_MOUNT WAITS until it returns"
    info "(hard mount) rather than erroring. Keep models and hot scratch local."
}

# ============================================================================
# PHASE: backup
# ============================================================================

write_paths() {
    # Absolute paths; anything missing on this machine is skipped at run time,
    # so one list serves all three.
    cat <<EOF
# Managed by metis-link.sh - one path per line. Missing paths are skipped.
$HOME/run_llm.sh
$HOME/run_sd.sh
$HOME/run_coder.sh
$HOME/sd-lightning-test.py
$HOME/forge-shape
$HOME/.config/sunshine
$HOME/.config/forge
$HOME/.config/systemd/user
$HOME/.llama-api-key
$HOME/.coder-api-key
$HOME/.ssh
$HOME/.pi
$HOME/.profile
$HOME/.bashrc
$HOME/llm-rgb
$HOME/llm-stack
$HOME/forge
$HOME/forge-projects
$HOME/models/qwen-image-2.1/paths.env
/etc/ufw
/etc/modprobe.d
/etc/ld.so.conf.d
/etc/udev/rules.d
/etc/apt/sources.list.d
/etc/apt/keyrings
/etc/systemd/system
/etc/sddm.conf.d
/etc/vulcan-gamemode.conf
/usr/local/sbin/vulcan-gamemode
/usr/local/bin/vulcan-mode
/etc/metis-backup
/etc/apt/apt.conf.d/01metis-cache
/etc/profile.d/metis-cache.sh
/etc/docker/daemon.json
/etc/fstab
/etc/default/grub
/etc/hostname
/etc/hosts
/root/.ssh
$STATE_DIR
EOF
}

write_excludes() {
    cat <<'EOF'
# Managed by metis-link.sh. Rebuildable or huge - never back these up.
*.gguf
*.safetensors
*.bin
.venvs/
venv/
__pycache__/
node_modules/
.cache/
.npm/
.npm-global/
llm-rgb/venv/
# Steam and every game library: all of it downloads again (Vulcan, Gemini).
.steam/
.local/share/Steam/
.var/app/com.valvesoftware.Steam/
steamapps/
SteamLibrary/
# forge: game files it downloads or extracts (the Garry's Mod dedicated
# server, Fallout 4's script sources), and its caches of GitHub repositories
# and toolchain downloads - all fetched or rebuilt again on demand.
forge/games/
forge/cache/
forge/toolchain-cache/
forge/toolchain-logs/
# Open WebUI's live database files. The runner takes a consistent copy with
# SQLite's online-backup API instead; copying a live SQLite file can produce
# a corrupt backup.
webui.db
webui.db-wal
webui.db-shm
EOF
}

write_runner() {
    cat <<'RUNNER'
#!/usr/bin/env bash
# /usr/local/sbin/metis-backup - generated by metis-link.sh. Runs as root.
set -uo pipefail
source /etc/metis-backup/config

log() { echo "$(date '+%F %T') $*"; }
mkdir -p "$STATE_DIR"
LIST=$(mktemp); trap 'rm -f "$LIST"' EXIT

fail() {
    log "FAILED: $*"
    printf '%s FAILED %s\n' "$(date -Is)" "$*" > "$STATE_DIR/LAST_RESULT"
    if [ -n "${HA_WEBHOOK_URL:-}" ]; then
        curl -fsS -m 10 -X POST -H 'Content-Type: application/json' \
             -d "{\"host\":\"$HOST_ROLE\",\"status\":\"failed\",\"detail\":\"$*\"}" \
             "$HA_WEBHOOK_URL" >/dev/null 2>&1 || true
    fi
    exit 1
}

# --- Open WebUI: a CONSISTENT copy of its SQLite database ------------------
# Only on Gemini, only if the container is running.
if command -v docker >/dev/null && docker ps --format '{{.Names}}' 2>/dev/null | grep -qx open-webui; then
    if docker exec open-webui python3 -c "
import sqlite3
s = sqlite3.connect('/app/backend/data/webui.db')
d = sqlite3.connect('/app/backend/data/webui.db.snapshot')
s.backup(d); d.close(); s.close()" 2>/dev/null; then
        # Find the volume on the host rather than assuming its name - compose
        # prefixes it with the project directory.
        SRC=$(docker inspect open-webui --format \
              '{{range .Mounts}}{{if eq .Destination "/app/backend/data"}}{{.Source}}{{end}}{{end}}')
        mkdir -p "$STATE_DIR/openwebui"
        # Deliberately NOT named webui.db - the excludes drop that name so a
        # live copy can never slip in, and would silently drop this too.
        cp "$SRC/webui.db.snapshot" "$STATE_DIR/openwebui/webui-snapshot.db"
        # Uploaded RAG documents too. The vector index rebuilds from these.
        [ -d "$SRC/uploads" ] && rsync -a --delete "$SRC/uploads/" "$STATE_DIR/openwebui/uploads/"
        log "Open WebUI database snapshotted"
    else
        log "WARN: could not snapshot Open WebUI's database"
    fi
fi

# --- file list: existing paths only ----------------------------------------
while IFS= read -r p; do
    case "$p" in ''|'#'*) continue ;; esac
    [ -e "$p" ] && printf '%s\n' "${p#/}" >> "$LIST"
done < /etc/metis-backup/paths

# Manifest: what was sent and when. MetisOMV's 'status' reads its date.
{ date -Is; echo "host=$HOST_ROLE"; cat "$LIST"; } > "$STATE_DIR/MANIFEST"

# --- push ------------------------------------------------------------------
# -r is required explicitly: --files-from turns off the recursion -a implies.
# --fake-super on the receiver (-M): the backup account on Metis is not root,
# so it cannot chown. fake-super stores real owner/mode in xattrs instead,
# and restores them when pulled back with the same flag.
# --delete keeps a true mirror; history lives in Metis's ZFS snapshots.
log "backing up $(wc -l < "$LIST") paths to $REMOTE"
rsync -aHAXr --numeric-ids --delete --delete-excluded --partial \
      --files-from="$LIST" --exclude-from=/etc/metis-backup/excludes \
      -M--fake-super \
      -e "ssh -i $BACKUP_KEY -o BatchMode=yes -o StrictHostKeyChecking=accept-new -o ConnectTimeout=20" \
      / "$REMOTE:"
rc=$?

# 24 = "some files vanished during the transfer" - normal for live logs and
# sockets, and not a failed backup.
if [ $rc -eq 0 ] || [ $rc -eq 24 ]; then
    log "OK (rsync exit $rc)"
    printf '%s OK\n' "$(date -Is)" > "$STATE_DIR/LAST_RESULT"
    exit 0
fi
fail "rsync exit $rc"
RUNNER
}

phase_backup() {
    say "[backup] nightly backup to MetisOMV"
    check_role
    sudo apt-get install -y rsync openssh-client curl

    sudo install -d -m 755 "$CONF_DIR"
    sudo install -d -m 700 "$STATE_DIR"
    write_paths    | sudo tee "$CONF_DIR/paths"    >/dev/null
    write_excludes | sudo tee "$CONF_DIR/excludes" >/dev/null
    sudo tee "$CONF_DIR/config" >/dev/null <<EOF
HOST_ROLE=$HOST_ROLE
REMOTE=$REMOTE
BACKUP_KEY=$BACKUP_KEY
STATE_DIR=$STATE_DIR
HA_WEBHOOK_URL=$HA_WEBHOOK_URL
EOF
    write_runner | sudo tee /usr/local/sbin/metis-backup >/dev/null
    sudo chmod 755 /usr/local/sbin/metis-backup
    sudo bash -n /usr/local/sbin/metis-backup || die "Generated runner failed syntax check."
    ok "runner: /usr/local/sbin/metis-backup"

    # Unattended key: no passphrase is unavoidable. rrsync on Metis is what
    # makes that acceptable - the key can do nothing except rsync into this
    # machine's own backup directory.
    if ! sudo test -f "$BACKUP_KEY"; then
        sudo install -d -m 700 /root/.ssh
        sudo ssh-keygen -q -t ed25519 -N '' -C "metis-backup-$HOST_ROLE" -f "$BACKUP_KEY"
    fi

    local when="${BACKUP_TIME[$HOST_ROLE]}"
    sudo tee /etc/systemd/system/metis-backup.service >/dev/null <<'EOF'
[Unit]
Description=Back up this machine to MetisOMV
After=network-online.target
Wants=network-online.target
[Service]
Type=oneshot
ExecStart=/usr/local/sbin/metis-backup
Nice=10
IOSchedulingClass=idle
EOF
    sudo tee /etc/systemd/system/metis-backup.timer >/dev/null <<EOF
[Unit]
Description=Nightly MetisOMV backup
[Timer]
OnCalendar=*-*-* $when:00
RandomizedDelaySec=120
# Catch up after downtime - these machines have a reset history.
Persistent=true
[Install]
WantedBy=timers.target
EOF
    sudo systemctl daemon-reload
    sudo systemctl enable metis-backup.timer
    ok "timer: nightly at $when (enabled; not yet started - verify first)"

    local pub; pub=$(sudo cat "$BACKUP_KEY.pub")
    cat <<EOF

    ${BOLD}Authorise this machine on MetisOMV${RST} - run there, as root:

      ./metis-setup.sh add-host $HOST_ROLE '$pub'

    Then back here:  ./metis-link.sh verify
EOF
}

# ============================================================================
# PHASE: caches
# ============================================================================

phase_caches() {
    say "[caches] Metis's apt, PyPI and Docker caches, with a fallback"
    check_role

    # apt: asked per http:// repository; Metis's apt-cacher-ng when it
    # answers within a second, otherwise straight to the mirror. (HTTPS
    # repositories always go direct.)
    sudo tee /usr/local/bin/metis-apt-proxy >/dev/null <<EOF
#!/bin/bash
# Generated by metis-link.sh. apt runs this to choose a proxy.
if timeout 1 bash -c "</dev/tcp/$METIS_IP/$APT_CACHE_PORT" 2>/dev/null; then
    echo "http://$METIS_IP:$APT_CACHE_PORT"
else
    echo DIRECT
fi
EOF
    sudo chmod 755 /usr/local/bin/metis-apt-proxy
    echo 'Acquire::http::Proxy-Auto-Detect "/usr/local/bin/metis-apt-proxy";' |
        sudo tee /etc/apt/apt.conf.d/01metis-cache >/dev/null
    ok "apt: $(/usr/local/bin/metis-apt-proxy)"

    # pip and uv: set for login shells only when Metis's cache answers, so
    # an install never hangs on a Metis that is down. Services and timers
    # keep using pypi.org directly.
    sudo tee /etc/profile.d/metis-cache.sh >/dev/null <<EOF
# Generated by metis-link.sh: PyPI through Metis's cache when it answers.
if timeout 0.5 bash -c "</dev/tcp/$METIS_IP/$PYPI_CACHE_PORT" 2>/dev/null; then
    export PIP_INDEX_URL="http://$METIS_IP:$PYPI_CACHE_PORT/index/"
    export PIP_TRUSTED_HOST="$METIS_IP"
    export UV_DEFAULT_INDEX="http://$METIS_IP:$PYPI_CACHE_PORT/index/"
    export UV_INDEX_URL="http://$METIS_IP:$PYPI_CACHE_PORT/index/"
fi
EOF
    ok "pip/uv: /etc/profile.d/metis-cache.sh (new login shells)"

    # Docker: Metis's registry mirrors Docker Hub; when it does not answer,
    # Docker pulls from Docker Hub itself. Reloaded, not restarted: running
    # containers keep running.
    if have docker; then
        sudo install -d /etc/docker
        sudo python3 - "$METIS_IP:$REGISTRY_CACHE_PORT" <<'PY'
import json, os, sys
p = "/etc/docker/daemon.json"
mirror = sys.argv[1]
try:
    cfg = json.load(open(p)) if os.path.exists(p) and os.path.getsize(p) else {}
except ValueError:
    sys.exit(f"{p} is not valid JSON - fix it first, then run this again")
cfg.setdefault("registry-mirrors", [])
if "http://" + mirror not in cfg["registry-mirrors"]:
    cfg["registry-mirrors"].append("http://" + mirror)
cfg.setdefault("insecure-registries", [])
if mirror not in cfg["insecure-registries"]:
    cfg["insecure-registries"].append(mirror)
if os.path.exists(p):
    os.replace(p, p + ".bak")
json.dump(cfg, open(p, "w"), indent=2)
PY
        sudo systemctl reload docker && ok "docker: mirror http://$METIS_IP:$REGISTRY_CACHE_PORT" \
            || warn "docker did not reload - sudo systemctl reload docker"
    else
        info "docker: not installed here - skipped"
    fi
}

# ============================================================================
# PHASE: archive (Gemini)
# ============================================================================

write_archiver() {
    cat <<'ARCH'
#!/usr/bin/env bash
# /usr/local/sbin/metis-archive - generated by metis-link.sh. Runs as root.
# Monthly: a full git bundle of forge-projects, plus one tarball per forge
# run older than 30 days. Pushed append-only - Metis refuses deletes and
# overwrites for this key, so filenames carry dates and are never reused.
set -euo pipefail
source /etc/metis-backup/config
STAGE=$(mktemp -d); trap 'rm -rf "$STAGE"' EXIT
log() { echo "$(date '+%F %T') $*"; }

if [ -d "$USER_HOME/forge-projects/.git" ]; then
    # Runs as root on a repo the user owns; without safe.directory, git
    # refuses with "detected dubious ownership".
    git -c safe.directory="$USER_HOME/forge-projects" \
        -C "$USER_HOME/forge-projects" bundle create \
        "$STAGE/forge-projects-$(date +%F).bundle" --all
    log "bundled forge-projects"
fi

mkdir -p "$STAGE/runs"
if [ -d "$USER_HOME/forge/runs" ]; then
    find "$USER_HOME/forge/runs" -mindepth 1 -maxdepth 1 -type d -mtime +30 |
    while read -r d; do
        tar -C "$(dirname "$d")" -czf "$STAGE/runs/$(basename "$d").tar.gz" "$(basename "$d")"
    done
    log "packed $(ls "$STAGE/runs" | wc -l) run(s) older than 30 days"
fi

rsync -aHr --numeric-ids -M--fake-super \
      -e "ssh -i $ARCHIVE_KEY -o BatchMode=yes -o StrictHostKeyChecking=accept-new" \
      "$STAGE/" "$REMOTE:"
log "archive pushed"
ARCH
}

phase_archive() {
    say "[archive] monthly append-only archive to the 20TB drive"
    check_role
    [ "$HOST_ROLE" = gemini ] || { info "Archive runs on Gemini only - skipping."; return 0; }
    sudo test -f "$CONF_DIR/config" || die "Run the backup phase first."
    sudo tee -a "$CONF_DIR/config" >/dev/null <<EOF
ARCHIVE_KEY=$ARCHIVE_KEY
USER_HOME=$HOME
EOF
    write_archiver | sudo tee /usr/local/sbin/metis-archive >/dev/null
    sudo chmod 755 /usr/local/sbin/metis-archive
    sudo bash -n /usr/local/sbin/metis-archive || die "Generated archiver failed syntax check."

    # A SEPARATE key: the archive key is append-only on Metis, the backup key
    # is not. Keeping them apart means the backup key can never delete from
    # the archive.
    sudo test -f "$ARCHIVE_KEY" || \
        sudo ssh-keygen -q -t ed25519 -N '' -C "metis-archive-$HOST_ROLE" -f "$ARCHIVE_KEY"

    sudo tee /etc/systemd/system/metis-archive.service >/dev/null <<'EOF'
[Unit]
Description=Append-only archive of forge projects to MetisOMV
After=network-online.target
[Service]
Type=oneshot
ExecStart=/usr/local/sbin/metis-archive
Nice=10
EOF
    sudo tee /etc/systemd/system/metis-archive.timer >/dev/null <<'EOF'
[Unit]
Description=Monthly forge archive
[Timer]
OnCalendar=*-*-01 05:00:00
Persistent=true
[Install]
WantedBy=timers.target
EOF
    sudo systemctl daemon-reload
    sudo systemctl enable metis-archive.timer
    local pub; pub=$(sudo cat "$ARCHIVE_KEY.pub")
    cat <<EOF

    ${BOLD}Authorise the archive key on MetisOMV${RST} - as root there:

      ./metis-setup.sh add-archive $HOST_ROLE '$pub'
EOF
}

# ============================================================================
# PHASE: verify
# ============================================================================

phase_verify() {
    say "[verify] back up now, then prove it reads back"
    check_role
    sudo systemctl start metis-backup.service || true
    local result; result=$(sudo cat "$STATE_DIR/LAST_RESULT" 2>/dev/null || echo "none")
    case "$result" in
        *OK*) ok "backup: $result" ;;
        *)    sudo journalctl -u metis-backup.service -n 20 --no-pager | sed 's/^/      /'
              die "backup did not succeed: $result" ;;
    esac

    # An untested backup is a hope, not a backup. Pull the manifest back
    # through the same restricted key and compare it with the local one.
    local tmp; tmp=$(mktemp -d)
    sudo rsync -a -M--fake-super \
        -e "ssh -i $BACKUP_KEY -o BatchMode=yes" \
        "$REMOTE:${STATE_DIR#/}/MANIFEST" "$tmp/MANIFEST" \
        || die "Could not read the backup back from Metis."
    if sudo cmp -s "$STATE_DIR/MANIFEST" "$tmp/MANIFEST"; then
        ok "restore test: manifest read back identical"
    else
        die "restore test: manifest differs from what was sent"
    fi
    sudo rm -rf "$tmp"

    sudo systemctl start metis-backup.timer
    ok "nightly timer active: $(systemctl list-timers metis-backup.timer --no-pager | awk 'NR==2{print $1,$2,$3}')"
}

phase_status() {
    check_role
    say "backup status ($HOST_ROLE)"
    info "last result: $(sudo cat "$STATE_DIR/LAST_RESULT" 2>/dev/null || echo 'never run')"
    systemctl list-timers 'metis-*' --no-pager | sed 's/^/    /'
    mountpoint -q "$NFS_MOUNT" 2>/dev/null && info "shared: mounted at $NFS_MOUNT" \
        || info "shared: not mounted (automount activates on first access)"
}

# ============================================================================
# MAIN
# ============================================================================

cmd="${1:-help}"
case "$cmd" in
    nfs|backup|caches|archive|verify|status) "phase_$cmd" ;;
    all) phase_nfs || warn "The shared pool is not mounted - carrying on with the backup and caches.
    Once the export works:  ./metis-link.sh nfs"
         phase_backup; phase_caches; [ "$HOST_ROLE" = gemini ] && phase_archive || true ;;
    *) awk 'NR > 1 && /^set -euo/ {exit} NR > 1' "$0"; exit 0 ;;
esac
