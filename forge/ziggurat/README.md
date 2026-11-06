# Ziggurat as forge's offline store

Forge builds and tests with no network inside its sandboxes, but it still
reaches the internet in three places:

- **Building the toolchain images.** Debian packages, pip, npm, git clones
  and Docker base images.
- **Downloading a project's dependencies before each run.** npm packages,
  Go modules, Python wheels and Rust crates.
- **Planning a run.** Reference pages come from Kiwix, which is already on
  Ziggurat at :4242.

Ziggurat (Unraid, 192.168.50.143) can hold caches for all three, so a slow
or dead internet connection stops nothing that has been fetched once.

## 1. The package caches (compose.yaml)

Five caching proxies run here. Each one fetches from the internet the
first time a package is asked for, then serves it from Ziggurat's disks.

| Port | Service | Caches | Used by |
|---|---|---|---|
| 3142 | apt-cacher-ng | Debian packages | `install-forge-toolchains.sh` (every image) |
| 3141 | proxpi | PyPI | image builds (pip); Home Assistant wheels per run |
| 4873 | Verdaccio | npm | node projects' packages per run |
| 3300 | Athens | Go modules | go projects' modules per run |
| 5005 | registry | Docker Hub | base images (python, node, golang...) |

On Ziggurat:

```bash
mkdir -p /mnt/user/appdata/forge-mirrors/verdaccio
mkdir -p /mnt/user/forge-mirrors/{apt-cacher-ng,pypi,npm,go,docker}
cp verdaccio-config.yaml /mnt/user/appdata/forge-mirrors/verdaccio/config.yaml
chown -R 10001:65533 /mnt/user/forge-mirrors/npm      # Verdaccio runs as uid 10001
```

Make `/mnt/user/forge-mirrors` a share first (Shares > Add Share). Put it
on the array; it does not need the cache pool. Then start the stack:

- **With the Docker Compose Manager plugin:** create a stack named
  `forge-mirrors` with `compose.yaml`, then *Compose Up*.
- **Without the plugin:** run `docker compose -f compose.yaml up -d` from
  a terminal on Ziggurat.

Check each service from Gemini:

```bash
curl -s -o /dev/null -w '%{http_code}\n' http://192.168.50.143:3142/      # 406 = apt-cacher-ng is up
curl -fsS http://192.168.50.143:3141/index/pip/ | head -3
curl -fsS http://192.168.50.143:4873/-/ping
curl -fsS http://192.168.50.143:3300/healthz
curl -fsS http://192.168.50.143:5005/v2/ && echo registry ok
```

## 2. Pointing Gemini at them

**Toolchain image builds** read `~/forge/mirrors.env`:

```bash
cat > ~/forge/mirrors.env <<'EOF'
MIRROR_APT=http://192.168.50.143:3142
MIRROR_PYPI=http://192.168.50.143:3141/index/
MIRROR_NPM=http://192.168.50.143:4873/
MIRROR_GO=http://192.168.50.143:3300
EOF
```

- The installer checks each mirror before building and uses only the ones
  that answer. It prints `ok apt through ...` or a warning for each.
- The settings are passed as build arguments, so nothing about the mirrors
  is baked into the images.
- Debian's package sources in the images are plain http, which is what
  apt-cacher-ng caches.

**Per-run downloads** read `[mirrors]` in `~/forge/config.toml`:

```toml
[mirrors]
pypi = "http://192.168.50.143:3141/index/"
npm  = "http://192.168.50.143:4873/"
go   = "http://192.168.50.143:3300"
```

- These feed forge's download-only containers.
- If a mirror stops answering, forge logs it and goes to the public
  registry instead.
- Go always falls back to proxy.golang.org on its own.

**Docker base images** go through the registry cache once Docker on
Gemini knows about it. Edit `/etc/docker/daemon.json`, then run
`sudo systemctl restart docker`:

```json
{ "registry-mirrors": ["http://192.168.50.143:5005"],
  "insecure-registries": ["192.168.50.143:5005"] }
```

This mirror only applies to Docker Hub images; other registries are
pulled directly.

## 3. Keeping the built images (`--save` / `--load`)

The toolchain images take 1-3 GB each and up to half an hour to build.
Keep a copy on Ziggurat:

```bash
sudo mkdir -p /mnt/ziggurat && sudo mount -t nfs 192.168.50.143:/mnt/user/forge-mirrors /mnt/ziggurat
./install-forge-toolchains.sh --save /mnt/ziggurat/images            # all built images
./install-forge-toolchains.sh --save /mnt/ziggurat/images mcu sql    # just these
./install-forge-toolchains.sh --load /mnt/ziggurat/images            # restore, no internet needed
```

The saved files are zstd-compressed `docker save` archives, about a third
of the image size.

Before mounting, export the share over NFS: Shares > forge-mirrors >
NFS > Export: Yes, Security: Private, rule `192.168.50.96(rw,sec=sys)`.

To mount it at every boot, put this line in `/etc/fstab`:

```
192.168.50.143:/mnt/user/forge-mirrors /mnt/ziggurat nfs defaults,_netdev,x-systemd.automount 0 0
```

`[cache] dir` in `~/forge/config.toml` can point there too. Forge keeps its
GitHub tarballs and Rust crates in that folder, and its npm, Go and wheel
caches in `npm/`, `gomod/` and `wheels/` inside it. With Ziggurat's space,
raise `max_gb` (for example to 500).

## 4. The offline library (Kiwix)

`kiwix-forge-library.py` gained two groups:

- `hardware-qa` covers unix, raspberrypi, electronics, arduino, iot,
  android, dba, networkengineering, robotics, security and serverfault Q&A.
- `wikis` covers the Arch wiki, man pages from mankier, the Minecraft wiki
  and Wikipedia's computing articles.

The devdocs group now adds node, npm, go, postgresql, mariadb, duckdb,
vitest, docker and jq. Every name was checked against the Kiwix catalog on
5 November 2026.

```bash
python3 kiwix-forge-library.py /mnt/ziggurat-media/Books/Zim --groups devdocs hardware-qa wikis --dry-run
```

Restart kiwix-serve afterwards. `forge check` on Gemini then lists which
reference books forge can use.

## 5. Recording a USB device (for the devices toolchain)

The sandbox has no USB. Tests that need a real phone, camera or reader
replay a recording made once on a machine where the device is plugged in.
Record with umockdev (`sudo apt install umockdev`):

```bash
lsusb                                            # find bus/device, e.g. 001/012
umockdev-record /dev/bus/usb/001/012 > phone.umockdev
umockdev-record --ioctl /dev/bus/usb/001/012=phone.ioctl /dev/bus/usb/001/012 -- mtp-files
```

Put both files in the project, for example under `tests/recordings/`.
Then `game.umockdev_run("mtp-files", ["tests/recordings/phone.umockdev"],
{"/dev/bus/usb/001/012": "tests/recordings/phone.ioctl"})` replays them.
The replay only covers the requests that were recorded.

Recordings can contain serial numbers and file names from the device.
Make them with a test phone or camera, not a personal one.
