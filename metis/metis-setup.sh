#!/usr/bin/env bash
#
# metis-setup.sh - run ON MetisOMV (192.168.50.126) as root.
#
# Sets up four things for Gemini (192.168.50.96), Apollo (.8), Vulcan (.151),
# Mercury (.129) and the Forgejo container:
#
#   BACKUPS   MetisZFS/backups/<host>
#             Each machine pushes nightly with rsync over SSH. Each machine's
#             key is locked by rrsync to ITS OWN directory - no shell, no
#             access to the other machines' backups.
#             sanoid snapshots the backups daily AFTER the push window. A
#             client can overwrite or delete its own mirror, but it cannot
#             touch a ZFS snapshot - so a bad script, a bug, or a compromised
#             client cannot destroy history.
#
#   SHARED    Metis1000/shared, exported over NFS
#             library/   read-mostly: texture packs, reference assets
#             exchange/  handoff between machines (Mercury -> forge, etc.)
#             scratch/   cross-machine temp. HOT scratch belongs on each
#                        machine's local NVMe - see the note below.
#
#   ARCHIVE   the 20TB XFS drive, forge-archive/<host>
#             Append-only from the clients: rrsync -no-del -no-overwrite, so
#             an archived file can be added but never deleted or replaced.
#
#   CACHES    Metis1000/caches - package and image caches for the lab, so a
#             rebuild of a sandbox image or a reinstall does not download the
#             same files again (and still works while the internet is slow):
#               apt     apt-cacher-ng (Debian package)    :3142
#               pypi    proxpi (MIT, Docker)              :5001  /index/
#               docker  Docker's registry, pull-through   :5000  (Docker Hub)
#             Clients use them with fallbacks (metis-link.sh caches): apt and
#             Docker go straight to the internet when Metis does not answer.
#             Caches are re-downloadable, so they are not snapshotted or
#             backed up.
#
# WHY rsync + ZFS SNAPSHOTS rather than restic or borg
# ----------------------------------------------------
# The target is already ZFS. rsync keeps a plain-file mirror (restore is a
# copy, browsable in .zfs/snapshot/), and snapshots give versioning that is
# copy-on-write - unchanged files cost nothing across snapshots. Server-side
# snapshots are also the strongest protection available here, because the
# client cannot delete them. restic's main advantages (client-side encryption
# and cross-host dedup) matter less on a trusted LAN with small config-sized
# backups.
#
# THROUGHPUT - worth knowing before relying on SHARED
# --------------------------------------------------
# Metis's PCIe slot holds the SAS HBA; a 2.5GbE NIC sits in the M.2 slot that
# originally held Wi-Fi (a PCIe x1 link, ample for 2.5GbE). That gives
# roughly 280 MB/s to clients that are themselves on 2.5GbE, and ~110 MB/s to
# any still on 1GbE.
#
# At that speed the POOLS can become the limit for sustained writes: RAIDZ1
# writes at the pace of its slowest member, and Metis1000 includes a Patriot
# P210, a budget drive whose sustained write rate drops once its cache fills.
# Loading a texture pack or handing off images is fine; hot scratch still
# belongs on each machine's local NVMe.
#
# CHECK: MetisOMV is a VM, so it only gets 2.5GbE if the Proxmox bridge it
# sits on is attached to the 2.5GbE NIC rather than the onboard 1GbE port.
#
# OMV NOTE: OMV owns sshd_config, /etc/exports and its user database, and
# regenerates them from its own config. This script never edits those files.
# The NFS export is done in the OMV web UI (the 'nfs' phase prints the exact
# values), and the backup user is a system account OMV leaves alone.
#
# Usage (as root on MetisOMV):
#   ./metis-setup.sh                          # check datasets user snapshots
#   ./metis-setup.sh add-host   HOST 'ssh-ed25519 AAAA... comment'
#   ./metis-setup.sh add-archive HOST 'ssh-ed25519 AAAA... comment'
#   ./metis-setup.sh nfs                      # print OMV UI steps
#   ./metis-setup.sh caches                   # apt, PyPI and Docker caches
#   ./metis-setup.sh archive-mirror           # optional 2nd copy on the 10TB
#   ./metis-setup.sh status
#
# HOST is gemini, apollo, vulcan, mercury or forgejo (the Forgejo container,
# which pushes its nightly `forgejo dump` - see forgejo-setup.sh).

set -euo pipefail

# ============================================================================
# CONFIG
# ============================================================================

BACKUP_PARENT="MetisZFS/backups"
SHARED_DS="Metis1000/shared"
BACKUP_USER="metisbackup"
BACKUP_HOME="/var/lib/metis-backup"

# Per-host quota. A misconfigured include (say, someone's ~/models) would
# otherwise fill the pool; the quota fails that backup loudly instead.
declare -A QUOTA=( [gemini]=100G [apollo]=50G [vulcan]=50G [mercury]=50G [forgejo]=100G )
HOSTS=(gemini apollo vulcan mercury forgejo)

# Clients allowed to mount SHARED and to use the caches.
GEMINI_IP="192.168.50.96"
APOLLO_IP="192.168.50.8"
VULCAN_IP="192.168.50.151"
MERCURY_IP="192.168.50.129"
CLIENT_IPS=("$GEMINI_IP" "$APOLLO_IP" "$VULCAN_IP" "$MERCURY_IP")

# Caches: one dataset, not snapshotted (everything in it can be downloaded
# again). The quota keeps a runaway cache from filling the pool.
CACHE_DS="Metis1000/caches"
CACHE_QUOTA="300G"
APT_PORT=3142
PYPI_PORT=5001
REGISTRY_PORT=5000
PYPI_CACHE_BYTES=$((60 * 1024 * 1024 * 1024))
# Pinned images (each at least two weeks old when chosen).
PROXPI_IMAGE="epicwink/proxpi:1.3.0"
REGISTRY_IMAGE="registry:3.1.1"

# The shared tree is owned by uid/gid 1000, which is the first user on each
# fresh Kubuntu install. NFS maps every client write to it (all_squash), so
# ownership is consistent regardless of who writes.
SHARE_UID=1000
SHARE_GID=1000

# Leave empty to auto-detect the ~20TB XFS mount; set it if detection picks
# the wrong disk.
ARCHIVE_MOUNT=""
ARCHIVE_SUBDIR="forge-archive"
# Optional second copy of the archive (the 'archive-mirror' phase). Leave
# empty to auto-detect the ~10TB BTRFS mount.
MIRROR_MOUNT=""

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

need_root() { [ "$(id -u)" -eq 0 ] || die "Run as root on MetisOMV."; }

mountpoint_of() { zfs get -H -o value mountpoint "$1"; }

rrsync_bin() {
    local p
    for p in /usr/bin/rrsync /usr/share/doc/rsync/scripts/rrsync; do
        [ -x "$p" ] && { echo "$p"; return; }
    done
    die "rrsync not found. It ships with the rsync package: apt install rsync"
}

# Largest mounted filesystem of a given type whose size is at least $2 TB.
detect_mount() {
    local fstype="$1" min_tb="$2"
    findmnt -rn -t "$fstype" -b -o TARGET,SIZE 2>/dev/null |
        awk -v min="$min_tb" '$2 >= min*1e12 { print $2, $1 }' |
        sort -rn | head -n1 | cut -d' ' -f2-
}

archive_root() {
    local m="${ARCHIVE_MOUNT:-$(detect_mount xfs 15)}"
    [ -n "$m" ] || die "Could not find the 20TB XFS mount. Set ARCHIVE_MOUNT."
    echo "$m/$ARCHIVE_SUBDIR"
}

valid_host() {
    case " ${HOSTS[*]} " in *" $1 "*) ;; *) die "Host must be one of: ${HOSTS[*]}";; esac
}

# ============================================================================
# PHASE: check
# ============================================================================

phase_check() {
    say "[check] MetisOMV prerequisites"
    need_root
    have zfs || die "zfs command not found."
    for pool in MetisZFS Metis1000; do
        local health
        health=$(zpool list -H -o health "$pool" 2>/dev/null || echo MISSING)
        [ "$health" = "ONLINE" ] || die "Pool $pool is $health."
        ok "pool $pool ONLINE"
    done
    have rsync || apt-get install -y rsync
    ok "rrsync: $(rrsync_bin)"
    systemctl is-active --quiet ssh || \
        warn "SSH is not running. Enable it in OMV: Services > SSH."
    info "archive target: $(archive_root)"
}

# ============================================================================
# PHASE: datasets
# ============================================================================

phase_datasets() {
    say "[datasets] backup, shared and archive locations"
    need_root

    if ! zfs list -H "$BACKUP_PARENT" &>/dev/null; then
        # xattr=sa: the clients back up with --fake-super, which stores real
        # ownership and permissions in extended attributes. sa keeps those in
        # the inode instead of hidden directories - much faster.
        zfs create -o compression=zstd -o atime=off -o xattr=sa "$BACKUP_PARENT"
        ok "created $BACKUP_PARENT"
    fi
    local h
    for h in "${HOSTS[@]}"; do
        local ds="$BACKUP_PARENT/$h"
        if ! zfs list -H "$ds" &>/dev/null; then
            zfs create -o quota="${QUOTA[$h]}" "$ds"
            ok "created $ds (quota ${QUOTA[$h]})"
        fi
    done

    if ! zfs list -H "$SHARED_DS" &>/dev/null; then
        # ONE dataset with plain subdirectories - not a dataset each. OMV
        # exports through a bind mount, which does not include child
        # filesystems; children would appear to clients as empty folders.
        # recordsize=1M suits large library files. lz4 aborts early on data
        # that is already compressed (most texture formats), so it costs
        # almost nothing.
        zfs create -o compression=lz4 -o atime=off -o xattr=sa \
                   -o recordsize=1M "$SHARED_DS"
        ok "created $SHARED_DS"
    fi
    local smp; smp=$(mountpoint_of "$SHARED_DS")
    mkdir -p "$smp"/{library,exchange,scratch}
    chown -R "$SHARE_UID:$SHARE_GID" "$smp"
    chmod 2775 "$smp" "$smp"/{library,exchange,scratch}
    ok "shared tree: $smp/{library,exchange,scratch} owned by $SHARE_UID:$SHARE_GID"

    local ar; ar=$(archive_root)
    for h in "${HOSTS[@]}"; do mkdir -p "$ar/$h"; done
    ok "archive tree: $ar"
}

# ============================================================================
# PHASE: user
# ============================================================================

phase_user() {
    say "[user] restricted backup account"
    need_root
    if ! id "$BACKUP_USER" &>/dev/null; then
        # System account (uid < 1000) so it stays out of OMV's user list.
        # Shell is /bin/sh, NOT bash: rrsync's manpage documents a security
        # issue when a restricted user's login shell is bash.
        adduser --system --group --shell /bin/sh \
                --home "$BACKUP_HOME" "$BACKUP_USER"
        ok "created $BACKUP_USER"
    fi
    install -d -m 700 -o "$BACKUP_USER" -g "$BACKUP_USER" "$BACKUP_HOME/.ssh"
    touch "$BACKUP_HOME/.ssh/authorized_keys"
    chown "$BACKUP_USER:$BACKUP_USER" "$BACKUP_HOME/.ssh/authorized_keys"
    chmod 600 "$BACKUP_HOME/.ssh/authorized_keys"

    # OMV only lets members of _ssh log in over SSH (AllowGroups root _ssh).
    if getent group _ssh &>/dev/null; then
        usermod -aG _ssh "$BACKUP_USER"
        ok "$BACKUP_USER added to _ssh (OMV's SSH login group)"
    else
        warn "No _ssh group found - check sshd AllowGroups manually."
    fi

    local h ar; ar=$(archive_root)
    for h in "${HOSTS[@]}"; do
        chown "$BACKUP_USER:$BACKUP_USER" "$(mountpoint_of "$BACKUP_PARENT/$h")"
        chmod 700 "$(mountpoint_of "$BACKUP_PARENT/$h")"
        chown "$BACKUP_USER:$BACKUP_USER" "$ar/$h"
        chmod 700 "$ar/$h"
    done
    ok "each host directory owned by $BACKUP_USER, mode 700"
    info "Backups contain API keys and credentials. Mode 700 limits them to"
    info "$BACKUP_USER and root on this machine."
}

# ============================================================================
# PHASE: snapshots
# ============================================================================

phase_snapshots() {
    say "[snapshots] sanoid - versioning the client cannot touch"
    need_root
    have sanoid || apt-get install -y sanoid
    install -d /etc/sanoid
    cat > /etc/sanoid/sanoid.conf <<'EOF'
# Managed by metis-setup.sh

# Backups: clients push 02:00-03:30; the daily snapshot is taken at 04:00, so
# every snapshot captures a complete night.
[MetisZFS/backups]
    use_template = backups
    recursive = yes

# Shared: protects the library against an accidental delete from any client.
# Scratch churn is held by these snapshots for up to a week.
[Metis1000/shared]
    use_template = shared

[template_backups]
    frequently = 0
    hourly = 0
    daily = 30
    weekly = 8
    monthly = 12
    yearly = 0
    daily_hour = 4
    daily_min = 0
    autosnap = yes
    autoprune = yes

[template_shared]
    frequently = 0
    hourly = 0
    daily = 7
    weekly = 0
    monthly = 0
    yearly = 0
    daily_hour = 5
    daily_min = 0
    autosnap = yes
    autoprune = yes
EOF
    systemctl enable --now sanoid.timer
    ok "sanoid.timer enabled - 30 daily, 8 weekly, 12 monthly for backups"
    info "Restore by browsing:  <dataset mountpoint>/.zfs/snapshot/<name>/"
}

# ============================================================================
# add-host / add-archive
# ============================================================================

# Replace any existing line for this host+purpose, so re-running is safe.
put_key() {
    local tag="$1" line="$2" ak="$BACKUP_HOME/.ssh/authorized_keys"
    local tmp; tmp=$(mktemp)
    grep -v " metis:${tag}\$" "$ak" > "$tmp" || true
    printf '%s metis:%s\n' "$line" "$tag" >> "$tmp"
    install -m 600 -o "$BACKUP_USER" -g "$BACKUP_USER" "$tmp" "$ak"
    rm -f "$tmp"
}

check_pubkey() {
    case "$1" in
        ssh-ed25519\ *|ssh-rsa\ *|ecdsa-sha2-*) ;;
        *) die "That does not look like an SSH public key." ;;
    esac
    # Only the key type and data; any comment is replaced with our tag.
    echo "$1" | awk '{print $1, $2}'
}

cmd_add_host() {
    need_root; valid_host "$1"
    local key; key=$(check_pubkey "$2")
    local dir; dir=$(mountpoint_of "$BACKUP_PARENT/$1")
    # restrict: no pty, no forwarding. rrsync confines this key to ONE
    # directory and to rsync alone. Read-write: the host keeps a mirror
    # (--delete) and can read it back to restore. History is protected by
    # the snapshots, not by forbidding deletes.
    put_key "backup-$1" "command=\"$(rrsync_bin) $dir\",restrict $key"
    ok "backup key for $1 -> $dir"
}

cmd_add_archive() {
    need_root; valid_host "$1"
    local key; key=$(check_pubkey "$2")
    local dir; dir="$(archive_root)/$1"
    # -no-del       rejects every --delete* and --remove* option
    # -no-overwrite enforces --ignore-existing: existing files are never
    #               replaced. Together: append-only.
    put_key "archive-$1" "command=\"$(rrsync_bin) -no-del -no-overwrite $dir\",restrict $key"
    ok "append-only archive key for $1 -> $dir"
}

# ============================================================================
# PHASE: nfs (printed instructions - OMV owns /etc/exports)
# ============================================================================

phase_nfs() {
    say "[nfs] export $SHARED_DS - do this in the OMV web UI"
    local smp; smp=$(mountpoint_of "$SHARED_DS")
    cat <<EOF

    1. Storage > Shared Folders > Create
         Name:        metis-shared
         File system: the one backed by $SHARED_DS  ($smp)
         Relative path: /

    2. Services > NFS > Settings: Enabled, save.

    3. Services > NFS > Shares > Create  - ONCE PER CLIENT:
         Shared folder: metis-shared
         Client:        one entry for each of: ${CLIENT_IPS[*]}
         Permission:    Read/Write
         Extra options: no_subtree_check,all_squash,anonuid=$SHARE_UID,anongid=$SHARE_GID

       Per-client entries rather than the whole /24: NFS authenticates by
       address only, so every other LAN device (cameras, tablets, IoT) is
       kept off it. all_squash maps every write to $SHARE_UID:$SHARE_GID, so
       files stay consistently owned whichever machine writes them.

    4. Apply the pending configuration (the yellow banner).

    Clients then run:  ./metis-link.sh nfs
    It tries both of OMV's export paths and keeps whichever mounts.
EOF
}

# ============================================================================
# PHASE: caches
# ============================================================================

phase_caches() {
    say "[caches] apt, PyPI and Docker image caches on $CACHE_DS"
    need_root
    if ! zfs list -H "$CACHE_DS" &>/dev/null; then
        zfs create -o compression=lz4 -o atime=off -o recordsize=1M \
                   -o quota="$CACHE_QUOTA" "$CACHE_DS"
        ok "created $CACHE_DS (quota $CACHE_QUOTA, no snapshots)"
    fi
    local cmp; cmp=$(mountpoint_of "$CACHE_DS")
    mkdir -p "$cmp"/{apt,pypi,registry}

    # --- apt: the Debian package, its cache moved onto the dataset ---------
    # No HTTP tunnels: apt-cacher-ng then only caches plain-HTTP repositories
    # and cannot be used as a general proxy. HTTPS repositories go direct.
    echo "apt-cacher-ng apt-cacher-ng/tunnelenable boolean false" | debconf-set-selections
    have apt-cacher-ng || DEBIAN_FRONTEND=noninteractive apt-get install -y apt-cacher-ng
    local conf=/etc/apt-cacher-ng/acng.conf
    chown apt-cacher-ng:apt-cacher-ng "$cmp/apt"
    chmod 755 "$cmp/apt"
    # apt-cacher-ng runs as its own user, which must be able to reach the
    # cache through every parent folder. A pool's top folder is sometimes
    # root-only (700): give "others" traverse (x) only - they still cannot
    # list or read anything there.
    local d="$cmp/apt"
    while d=$(dirname "$d"); [ "$d" != / ]; do
        if ! runuser -u apt-cacher-ng -- test -x "$d" 2>/dev/null; then
            chmod o+x "$d"
            info "allowed apt-cacher-ng to pass through $d (o+x; no listing or reading)"
        fi
    done
    runuser -u apt-cacher-ng -- test -w "$cmp/apt" || die "apt-cacher-ng cannot write to $cmp/apt"
    [ -f "$conf.orig" ] || cp "$conf" "$conf.orig"
    if ! grep -qx "CacheDir: $cmp/apt" "$conf"; then
        sed -i '/^CacheDir:/d' "$conf"
        printf '\nCacheDir: %s\n' "$cmp/apt" >> "$conf"
    fi
    # Start only once ZFS has mounted the cache - otherwise, at boot, it
    # would fill the empty mountpoint folder underneath it.
    install -d /etc/systemd/system/apt-cacher-ng.service.d
    cat > /etc/systemd/system/apt-cacher-ng.service.d/metis.conf <<EOF
[Unit]
After=zfs-mount.service
RequiresMountsFor=$cmp/apt
EOF
    systemctl daemon-reload
    systemctl enable apt-cacher-ng >/dev/null 2>&1
    if systemctl restart apt-cacher-ng; then
        ok "apt-cacher-ng on :$APT_PORT, cache in $cmp/apt"
    else
        warn "apt-cacher-ng did not start with its cache on $cmp/apt:"
        journalctl -u apt-cacher-ng -n 15 --no-pager | sed 's/^/      /'
        cp "$conf.orig" "$conf"
        rm -f /etc/systemd/system/apt-cacher-ng.service.d/metis.conf
        systemctl daemon-reload
        if systemctl restart apt-cacher-ng; then
            warn "apt-cacher-ng is running with its default cache (/var/cache/apt-cacher-ng) instead;"
            warn "send the lines above and it can be moved onto the dataset."
        else
            warn "apt-cacher-ng does not start even with its defaults - see the lines above."
        fi
    fi

    # --- PyPI and Docker Hub: two small containers --------------------------
    if ! have docker; then
        warn "Docker is not installed on this OMV. Install it the OMV way - omv-extras,"
        warn "then System > Plugins > openmediavault-compose - and run this phase again."
        return 0
    fi
    chown 1000:1000 "$cmp/pypi"
    cat > "$cmp/compose.yml" <<EOF
# Managed by metis-setup.sh caches. Everything here can be downloaded again.
name: metis-caches
services:
  pypi:
    image: $PROXPI_IMAGE
    restart: unless-stopped
    user: "1000:1000"
    ports: ["$PYPI_PORT:5000"]
    environment:
      PROXPI_CACHE_DIR: /cache
      PROXPI_CACHE_SIZE: "$PYPI_CACHE_BYTES"
      # Wait for a download to finish (and be cached) instead of sending the
      # client to pypi.org after 0.9 s, the default.
      PROXPI_DOWNLOAD_TIMEOUT: "60"
      PROXPI_CONNECT_TIMEOUT: "5"
      PROXPI_READ_TIMEOUT: "60"
    volumes: ["$cmp/pypi:/cache"]
  registry:
    image: $REGISTRY_IMAGE
    restart: unless-stopped
    ports: ["$REGISTRY_PORT:5000"]
    environment:
      # Pull-through cache of Docker Hub; read-only for clients (a proxy
      # registry refuses pushes).
      REGISTRY_PROXY_REMOTEURL: https://registry-1.docker.io
      REGISTRY_PROXY_TTL: 336h
      REGISTRY_STORAGE_DELETE_ENABLED: "true"
    volumes: ["$cmp/registry:/var/lib/registry"]
EOF
    docker compose -f "$cmp/compose.yml" up -d
    have curl || DEBIAN_FRONTEND=noninteractive apt-get install -y -qq curl >/dev/null
    sleep 3
    local p
    for p in "$PYPI_PORT/index/" "$REGISTRY_PORT/v2/"; do
        if curl -fsS -m 10 -o /dev/null "http://127.0.0.1:$p"; then ok "answering: :$p"
        else warn "not answering yet: http://127.0.0.1:$p (docker compose -f $cmp/compose.yml logs)"; fi
    done
    info "The compose file is $cmp/compose.yml. If you manage containers in OMV's"
    info "Compose plugin, you can paste it there instead."
    info "Clients: ./metis-link.sh caches on each machine."
}

# ============================================================================
# PHASE: archive-mirror (optional)
# ============================================================================

phase_archive_mirror() {
    say "[archive-mirror] second copy of the archive on the 10TB drive"
    need_root
    local src; src=$(archive_root)
    local mm="${MIRROR_MOUNT:-$(detect_mount btrfs 8)}"
    [ -n "$mm" ] || die "Could not find the 10TB BTRFS mount. Set MIRROR_MOUNT."
    local dst="$mm/forge-archive-mirror"
    mkdir -p "$dst"
    cat > /etc/systemd/system/metis-archive-mirror.service <<EOF
[Unit]
Description=Mirror forge archive to the second disk
[Service]
Type=oneshot
# No --delete: the mirror only ever grows, matching the append-only archive.
ExecStart=/usr/bin/rsync -aHAX --numeric-ids $src/ $dst/
EOF
    cat > /etc/systemd/system/metis-archive-mirror.timer <<'EOF'
[Unit]
Description=Weekly archive mirror
[Timer]
OnCalendar=Sun 06:00
Persistent=true
[Install]
WantedBy=timers.target
EOF
    systemctl daemon-reload
    systemctl enable --now metis-archive-mirror.timer
    ok "weekly mirror: $src -> $dst"
    info "The 20TB is a single disk with no redundancy, and XFS does not"
    info "checksum data. This copy is what makes the archive survive a drive."
}

# ============================================================================
# status
# ============================================================================

cmd_status() {
    say "backups"
    zfs list -o name,used,avail,quota -r "$BACKUP_PARENT" 2>/dev/null | sed 's/^/    /'
    local h
    for h in "${HOSTS[@]}"; do
        local m; m="$(mountpoint_of "$BACKUP_PARENT/$h")/var/lib/metis-backup/MANIFEST"
        if [ -f "$m" ]; then
            printf '    %-8s last backup %s\n' "$h" "$(date -r "$m" '+%Y-%m-%d %H:%M')"
        else
            printf '    %-8s %sno backup received yet%s\n' "$h" "$YEL" "$RST"
        fi
    done
    say "latest snapshots"
    zfs list -t snapshot -o name,creation -s creation -r "$BACKUP_PARENT" 2>/dev/null |
        tail -n 4 | sed 's/^/    /'
    say "shared"
    zfs list -o name,used,avail "$SHARED_DS" 2>/dev/null | sed 's/^/    /'
    say "caches"
    zfs list -o name,used,avail,quota "$CACHE_DS" 2>/dev/null | sed 's/^/    /' || echo "    not set up"
    local name port
    for name in apt:$APT_PORT pypi:$PYPI_PORT docker:$REGISTRY_PORT; do
        port=${name#*:}
        if timeout 3 bash -c "</dev/tcp/127.0.0.1/$port" 2>/dev/null; then
            printf '    %-7s :%s answering\n' "${name%%:*}" "$port"
        else
            printf '    %-7s :%s %snot answering%s\n' "${name%%:*}" "$port" "$YEL" "$RST"
        fi
    done
    say "authorized keys"
    grep -o 'metis:[a-z-]*' "$BACKUP_HOME/.ssh/authorized_keys" 2>/dev/null |
        sed 's/^/    /' || echo "    none yet"
}

# ============================================================================
# MAIN
# ============================================================================

case "${1:-all}" in
    all)            phase_check; phase_datasets; phase_user; phase_snapshots
                    echo; info "Next: ./metis-setup.sh nfs, then run metis-link.sh on each machine." ;;
    check|datasets|user|snapshots|nfs|caches)
                    "phase_$1" ;;
    archive-mirror) phase_archive_mirror ;;
    add-host)       [ $# -eq 3 ] || die "usage: add-host HOST 'PUBKEY'"; cmd_add_host "$2" "$3" ;;
    add-archive)    [ $# -eq 3 ] || die "usage: add-archive HOST 'PUBKEY'"; cmd_add_archive "$2" "$3" ;;
    status)         cmd_status ;;
    *) die "Unknown command '$1'. See the header for usage." ;;
esac
