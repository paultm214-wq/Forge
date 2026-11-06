#!/usr/bin/env bash
#
# gmod-probe.sh - can forge test Garry's Mod addons on a headless server?
#
# Run on Gemini as your normal user (needs Docker):
#     bash gmod-probe.sh
#
# 1. Installs the Garry's Mod DEDICATED SERVER (Steam app 4020, a free
#    download; anonymous login, no account) into ~/gmod-probe/server with
#    SteamCMD in a container. Your installed game is not used or changed.
#    Several GB the first time; later runs only check for updates.
# 2. Adds a test addon and starts the server headless with NO network
#    (as forge's sandbox would): the addon checks GLua syntax, the map,
#    spawning an entity, physics and timers, writes the results, and quits.
# 3. Tries glualint (GLuaFixer's GLua linter) on the addon if a Linux
#    release can be downloaded.
# Report: ~/gmod-probe/report.txt - send me that file.
#
set -uo pipefail
OUT="$HOME/gmod-probe"
SRV="$OUT/server"
IMG="${STEAMCMD_IMAGE:-steamcmd/steamcmd:latest}"
mkdir -p "$SRV" "$OUT/tools"
R="$OUT/report.txt"; : > "$R"
log() { printf '%s\n' "$*" | tee -a "$R"; }
sec() { log ""; log "=== $* ==="; }

sec "host"
log "date: $(date -Is)  host: $(hostname)  docker: $(docker --version 2>&1)"
docker pull -q "$IMG" >/dev/null 2>&1 || log "could not pull $IMG"
log "image: $IMG"

sec "install / update the dedicated server (app 4020, anonymous)"
out="$(timeout 3600 docker run --rm --user "$(id -u):$(id -g)" -e HOME=/tmp/home \
    -v "$SRV":/server "$IMG" \
    +force_install_dir /server +login anonymous +app_update 4020 validate +quit 2>&1)"
log "exit code: $?"
printf '%s\n' "$out" | grep -E "Success|Error|ERROR|fully installed|state is" | tail -8 | tee -a "$R" >/dev/null
log "size: $(du -sh "$SRV" | cut -f1)"
log "srcds files: $(ls "$SRV" | grep -E '^srcds' | tr '\n' ' ')"
for f in srcds_linux srcds_linux64 bin/linux64/srcds; do
    [ -e "$SRV/$f" ] && log "  $f: $(file -b "$SRV/$f" | cut -c1-70)"
done
log "version: $(grep -h -m1 -i 'PatchVersion' "$SRV"/garrysmod/steam.inf 2>/dev/null)"
log "maps: $(ls "$SRV"/garrysmod/maps 2>/dev/null | grep -c '\.bsp$') (gm_construct: $([ -f "$SRV/garrysmod/maps/gm_construct.bsp" ] && echo yes || echo NO))"

sec "test addon"
A="$SRV/garrysmod/addons/forge_probe"
rm -rf "$A"; mkdir -p "$A/lua/autorun/server"
cat > "$A/addon.json" <<'EOF'
{"title": "forge probe", "type": "tool", "tags": ["build"], "ignore": []}
EOF
cat > "$A/lua/autorun/server/forge_probe.lua" <<'EOF'
-- Runs inside the headless server. Results go to data/forge_probe.txt
-- and the console; then the server quits.
local results = {}
local function check(name, ok, detail)
	results[#results + 1] = (ok and "PASS " or "FAIL ") .. name
		.. (detail ~= nil and (" (" .. tostring(detail) .. ")") or "")
end

-- GLua's own syntax: != && || ! and // comments (plain Lua rejects these)
local a = 1
check("glua syntax", (a != 2) && !(a == 2) || false)
check("lua version", _VERSION ~= nil, _VERSION)
check("jit", jit ~= nil, jit and jit.version)

local function finish()
	local text = table.concat(results, "\n")
	file.Write("forge_probe.txt", text)
	print("FORGE_PROBE_BEGIN\n" .. text .. "\nFORGE_PROBE_END")
	game.ConsoleCommand("quit\n")
end

hook.Add("InitPostEntity", "forge_probe", function()
	check("map loaded", game.GetMap() == "gm_construct", game.GetMap())
	check("entities on map", #ents.GetAll() > 0, #ents.GetAll())
	local ent = ents.Create("prop_physics")
	if IsValid(ent) then
		ent:SetModel("models/props_c17/oildrum001.mdl")
		ent:SetPos(Vector(0, 0, 200))
		ent:Spawn()
	end
	check("entity spawned", IsValid(ent))
	local z0 = IsValid(ent) and ent:GetPos().z or 0
	timer.Simple(2, function()
		check("timer ran", true)
		local phys = IsValid(ent) and ent:GetPhysicsObject()
		check("physics object", phys ~= nil and phys ~= false and IsValid(phys))
		check("physics moved it", IsValid(ent) and ent:GetPos().z ~= z0,
			IsValid(ent) and string.format("z %.1f -> %.1f", z0, ent:GetPos().z))
		finish()
	end)
end)

-- Never hang the probe: quit after 60 s of game time regardless.
timer.Simple(60, function() check("finished before the 60 s limit", false); finish() end)
EOF
log "written to $A"

sec "run the server headless, no network"
start=$(date +%s)
# srcds ignores the polite stop signal, so the container gets a name and is
# killed outright when the time is up (timeout alone left it running).
CN="forge-gmod-probe-$$"
( sleep 300; docker kill "$CN" >/dev/null 2>&1 ) &
watchdog=$!
out="$(docker run --rm --name "$CN" --network none --user "$(id -u):$(id -g)" -e HOME=/tmp/home \
    -v "$SRV":/server -w /server --entrypoint bash "$IMG" -c \
    './srcds_run -game garrysmod -console -norestart -port 27015 +sv_lan 1 +maxplayers 2 +sv_hibernate_think 1 +map gm_construct' 2>&1)"
rc=$?
kill "$watchdog" 2>/dev/null
log "exit code: $rc  (137 = killed at the 300 s limit)  seconds: $(( $(date +%s) - start ))"
printf '%s\n' "$out" | sed -n '/FORGE_PROBE_BEGIN/,/FORGE_PROBE_END/p' | tee -a "$R" >/dev/null
log "--- results file:"
cat "$SRV/garrysmod/data/forge_probe.txt" 2>/dev/null | tee -a "$R" >/dev/null || log "(no results file)"
log "--- last server output lines:"
printf '%s\n' "$out" | grep -v '^\s*$' | tail -40 | tee -a "$R" >/dev/null
log "--- errors in the output:"
printf '%s\n' "$out" | grep -iE "error|lua|failed|cannot|missing" | grep -v FORGE_PROBE | sort -u | head -30 | tee -a "$R" >/dev/null

sec "glualint (optional)"
url="$(curl -fsSL https://api.github.com/repos/FPtje/GLuaFixer/releases/latest 2>/dev/null \
    | grep -o '"browser_download_url": *"[^"]*[Ll]inux[^"]*"' | grep -viE 'aarch64|arm64' | head -1 \
    | sed 's/.*"\(http[^"]*\)"/\1/')"
if [ -n "$url" ]; then
    log "release asset: $url"
    curl -fsSL -o "$OUT/tools/glualint.zip" "$url" && (cd "$OUT/tools" && unzip -oq glualint.zip 2>/dev/null)
    GL="$(find "$OUT/tools" -type f -name 'glualint*' ! -name '*.zip' | head -1)"
    if [ -n "$GL" ]; then
        chmod +x "$GL"
        log "$("$GL" --version 2>&1 | head -1)"
        "$GL" lint "$A/lua" 2>&1 | head -20 | tee -a "$R" >/dev/null
        log "glualint exit code: ${PIPESTATUS[0]}"
    else
        log "no glualint binary in the download"
    fi
else
    log "no Linux release asset found on GitHub (FPtje/GLuaFixer)"
fi

echo
echo "Report: $R"
