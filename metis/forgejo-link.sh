#!/usr/bin/env bash
#
# forgejo-link.sh - run on GEMINI, as your normal user, after
# forgejo-setup.sh has configured Forgejo on Metis.
#
#   ./forgejo-link.sh http://<forgejo-address>:3000
#
# It:
#   1. checks the token you give it (Forgejo > Settings > Applications >
#      Generate new token; permissions: repository Read and write, user Read),
#   2. creates a PRIVATE repository forge-projects under your account (or
#      uses the one that is there),
#   3. points ~/forge-projects at it as `origin` - the token is kept in
#      ~/.config/forge/git-credentials (mode 600), used only for this
#      repository and never written into the remote URL,
#   4. pushes main and every forge/* branch made so far,
#   5. sets [git] remote = "origin" and auto_push = true in
#      ~/forge/config.toml (the old file is kept beside it).
#
# From then on forge pushes each branch it commits. You review and merge
# on Forgejo; before each commit forge fast-forwards the local main from
# Forgejo, so new branches start from what you merged. When Forgejo does not
# answer, the push waits for `forge pending` (the 30-minute timer) - nothing
# is lost. Re-running this script is safe.
#
# Use a token of the account forge should push as - a bot account like
# forge-bot keeps your own account's token off Gemini. SHARE_WITH=<your user>
# then adds you to the repository as an admin collaborator, to review and
# merge. FORGEJO_TOKEN=... in the environment skips the prompt; REPO=name
# changes the repository name; OWNER=<user or org> uses a repository made
# in the web UI under another owner, with the token's account as a collaborator.

set -euo pipefail

BOLD=$'\033[1m'; RED=$'\033[31m'; YEL=$'\033[33m'; GRN=$'\033[32m'; RST=$'\033[0m'
say()  { printf '%s==> %s%s\n' "$BOLD" "$*" "$RST"; }
info() { printf '    %s\n' "$*"; }
warn() { printf '%s    WARNING: %s%s\n' "$YEL" "$*" "$RST"; }
die()  { printf '%s    ERROR: %s%s\n' "$RED" "$*" "$RST" >&2; exit 1; }
ok()   { printf '%s    OK: %s%s\n' "$GRN" "$*" "$RST"; }

[ $# -ge 1 ] || { awk 'NR > 1 && /^set -euo/ {exit} NR > 1' "$0"; exit 1; }
[ "$(id -u)" -ne 0 ] || die "Run as your normal user (the one forge runs as)."
URL="${1%/}"
[[ "$URL" =~ ^https?://[^/]+$ ]] || die "Give the address as http://host:port (no path), e.g. the one forgejo-setup.sh printed."
REPO="${REPO:-forge-projects}"
LOCAL="$HOME/forge-projects"
CONFIG="$HOME/forge/config.toml"
CRED_DIR="$HOME/.config/forge"
CRED="$CRED_DIR/git-credentials"

BODY=$(mktemp)
trap 'rm -f "$BODY"' EXIT
api() {   # METHOD PATH [JSON]: the reply in $BODY, the HTTP status in $CODE
    CODE=$(curl -sS -m 20 -o "$BODY" -w '%{http_code}' -X "$1" \
                -H "Authorization: token $TOKEN" -H "Content-Type: application/json" \
                ${3:+--data "$3"} "$URL/api/v1$2") || die "no answer from $URL"
}
json() { python3 -c "import json, sys; print(json.load(sys.stdin)$1)"; }

say "[forgejo] $URL"
TOKEN="${FORGEJO_TOKEN:-}"
if [ -z "$TOKEN" ]; then
    read -r -s -p "    Access token (repository: read and write; user: read): " TOKEN
    echo
fi
[ -n "$TOKEN" ] || die "No token."
# With the token: Forgejo set to require sign-in answers 403 to anonymous calls.
api GET /version
case "$CODE" in
    200) ;;
    401|403) die "The token was refused (HTTP $CODE) - check it was copied whole." ;;
    *) die "Forgejo at $URL answered HTTP $CODE to /api/v1/version - is the address right?" ;;
esac
ok "Forgejo $(json "['version']" < "$BODY")"
api GET /user
[ "$CODE" = 200 ] || die "The token was refused (HTTP $CODE). It needs the 'user: read' permission."
LOGIN=$(json "['login']" < "$BODY")
ok "token belongs to $LOGIN"

OWNER="${OWNER:-$LOGIN}"
say "[repository] $OWNER/$REPO"
api GET "/repos/$OWNER/$REPO"
if [ "$CODE" = 200 ]; then
    ok "exists"
    if ! json "['permissions']['push']" < "$BODY" 2>/dev/null | grep -qx True; then
        die "$LOGIN cannot push to $OWNER/$REPO. Add $LOGIN as a collaborator with Write
    (repository Settings > Collaborators), then run this again."
    fi
elif [ "$CODE" = 404 ] && [ "$OWNER" != "$LOGIN" ]; then
    die "$OWNER/$REPO does not exist, or $LOGIN cannot see it. Create it as $OWNER in the
    web UI (private, empty, default branch main), add $LOGIN under Settings >
    Collaborators with Write, then run this again."
elif [ "$CODE" = 404 ]; then
    api POST /user/repos "{\"name\": \"$REPO\", \"private\": true, \"auto_init\": false, \"default_branch\": \"main\", \"description\": \"forge: every project, one branch per run\"}"
    if [ "$CODE" != 201 ]; then
        msg=$(json ".get('message', '')" < "$BODY" 2>/dev/null || true)
        die "Forgejo would not let $LOGIN create $REPO (HTTP $CODE${msg:+: $msg}).
    Usually one of:
    - $LOGIN may not create repositories: Site Administration > User Accounts >
      $LOGIN > Edit - 'Maximum number of repositories' must not be 0, and the
      account must not be Restricted.
    - The token covers only selected repositories; it needs access to all of
      $LOGIN's repositories (repository: read and write, user: read).
    Or skip creating it here: make $REPO yourself (private, empty, branch main),
    add $LOGIN as a collaborator with Write, and run
      OWNER=<its owner> ./forgejo-link.sh $URL"
    fi
    ok "created (private)"
else
    die "Unexpected HTTP $CODE looking for $OWNER/$REPO."
fi
# A bot account owns the repository; you review and merge as yourself.
if [ -n "${SHARE_WITH:-}" ] && [ "$SHARE_WITH" != "$OWNER" ]; then
    api PUT "/repos/$OWNER/$REPO/collaborators/$SHARE_WITH" '{"permission": "admin"}'
    if [ "$CODE" = 204 ]; then ok "$SHARE_WITH can review and merge (admin on $OWNER/$REPO)"
    else warn "could not add $SHARE_WITH as a collaborator (HTTP $CODE) - add them in the repository's settings"; fi
fi

say "[git] $LOCAL -> origin"
[ -d "$LOCAL/.git" ] || die "$LOCAL is not a git repository yet - forge creates it on its first commit."
install -d -m 700 "$CRED_DIR"
host="${URL#*://}"
scheme="${URL%%://*}"
# One line per host; replace ours, keep any others.
touch "$CRED"
chmod 600 "$CRED"
grep -v "@$host\$" "$CRED" > "$CRED.tmp" || true
printf '%s://%s:%s@%s\n' "$scheme" "$LOGIN" "$TOKEN" "$host" >> "$CRED.tmp"
mv "$CRED.tmp" "$CRED"
chmod 600 "$CRED"
git -C "$LOCAL" config credential.helper "store --file=$CRED"
remote_url="$URL/$OWNER/$REPO.git"
if git -C "$LOCAL" remote get-url origin &>/dev/null; then
    old=$(git -C "$LOCAL" remote get-url origin)
    [ "$old" = "$remote_url" ] || { warn "origin was $old - now $remote_url"; git -C "$LOCAL" remote set-url origin "$remote_url"; }
else
    git -C "$LOCAL" remote add origin "$remote_url"
fi
ok "origin = $remote_url"

export GIT_TERMINAL_PROMPT=0
git -C "$LOCAL" push -u origin main
n=$(git -C "$LOCAL" for-each-ref --format='%(refname)' 'refs/heads/forge/' | wc -l)
if [ "$n" -gt 0 ]; then
    git -C "$LOCAL" push origin 'refs/heads/forge/*:refs/heads/forge/*'
fi
ok "pushed main and $n forge branch(es)"

say "[forge] $CONFIG"
[ -f "$CONFIG" ] || die "$CONFIG not found."
cp -p "$CONFIG" "$CONFIG.bak-$(date +%Y%m%d-%H%M%S)"
python3 - "$CONFIG" <<'PY'
import re, sys, tomllib
p = sys.argv[1]
text = open(p).read()
m = re.search(r"(?m)^\[git\]\s*$", text)
if not m:
    text = text.rstrip("\n") + '\n\n[git]\nrepo = "~/forge-projects"\nremote = "origin"\nauto_push = true\n'
else:
    end = re.search(r"(?m)^\[", text[m.end():])
    body = text[m.end(): m.end() + end.start()] if end else text[m.end():]
    new = body
    for key, val, note in (("remote", '"origin"', "Forgejo on Metis (forgejo-link.sh)"),
                           ("auto_push", "true", "every committed branch; merging stays yours")):
        line = f"{key} = {val}".ljust(26) + f"# {note}"
        if re.search(rf"(?m)^{key}\s*=", new):
            new = re.sub(rf"(?m)^{key}\s*=.*$", lambda mm: line, new, count=1)
        else:
            new = "\n" + line + new
    text = text[:m.end()] + new + (text[m.end() + end.start():] if end else "")
tomllib.loads(text)          # never leave a broken config behind
open(p, "w").write(text)
g = tomllib.loads(text)["git"]
print(f"    git: remote = {g.get('remote')!r}, auto_push = {g.get('auto_push')}")
PY
ok "forge pushes each new branch to $OWNER/$REPO (as $LOGIN)"
info "Check it:  forge check     (the 'git' line should say OK)"
info "Branches:  $URL/$OWNER/$REPO/branches"
