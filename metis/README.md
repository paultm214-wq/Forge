# Metis: backups, shared storage, caches and Forgejo

| Machine | Address | Role here |
|---|---|---|
| MetisOMV (VM on Metis) | 192.168.50.126 | backups (ZFS + snapshots), shared NFS pool, archive, package and image caches |
| Forgejo (LXC on Metis) | its own address | git host for forge's projects; backed up nightly to MetisOMV |
| Gemini | 192.168.50.96 | forge, planner; backs up nightly 02:00; monthly archive |
| Apollo | 192.168.50.8 | coder; backs up 02:20 |
| Mercury | 192.168.50.129 | images, helper coder; backs up 02:40 |
| Vulcan (Kubuntu) | 192.168.50.151 | images, shapes, helper coder, gaming; backs up 03:00 (catches up after a night in Bazzite) |

MetisOMV snapshots the backups at 04:00, after everything has pushed.

## Order

1. **On MetisOMV, as root:** `./metis-setup.sh` (checks, datasets for all
   five hosts, the backup account, snapshots), then `./metis-setup.sh nfs`
   (prints the OMV export steps) and `./metis-setup.sh caches` (apt, PyPI
   and Docker caches; needs Docker - OMV's compose plugin - for the last two).
   Already set up for three hosts? Rerun `./metis-setup.sh datasets user` to
   add Vulcan's and Forgejo's backup directories.
2. **In the Forgejo container, as root:** `./forgejo-setup.sh check`, then
   `config`, then `admin NAME EMAIL` if you have no admin yet, then
   `backup`. Run the `add-host forgejo ...` line it prints on MetisOMV.
3. **On each machine, as your user:** `./metis-link.sh all` (shared pool,
   nightly backup, caches; the monthly archive on Gemini). Run the
   `add-host` line it prints on MetisOMV, then `./metis-link.sh verify`.
   On Vulcan, set `HOST_ROLE=vulcan` in front if its Kubuntu hostname is
   different.
4. **On Gemini:** sign in to Forgejo as **forge-bot** and create a token
   (Settings > Applications; repository read and write, user read), then
   `SHARE_WITH=<your user> ./forgejo-link.sh http://192.168.50.80:3000`.
   The repository belongs to forge-bot and you are an admin on it, so your
   own token never sits on Gemini. `forge check` shows the git line.

## What is not backed up

Models and builds, venvs and caches, and **all of Steam**: `~/.steam`,
`~/.local/share/Steam`, the Flatpak Steam folder, and any `steamapps/` or
`SteamLibrary/` folder wherever it is. On Gemini also forge's downloaded
game files (`~/forge/games`: the Garry's Mod server, Fallout 4's script
sources) and its repository and toolchain caches. All of it downloads again.
The next backup removes anything newly excluded from the copy on Metis;
older snapshots keep it until they expire.

## When something is down

- **Metis down:** apt and Docker go straight to the internet; pip uses
  PyPI directly in new shells; the NFS mount waits (hard mount) - keep hot
  work local. forge keeps committing locally; pushes wait for `forge
  pending`, which pushes them once Forgejo answers.
- **Internet down:** the caches serve what they already hold; forge works
  from its offline library and defers Claude's review (see forge's
  CHANGES.md).
