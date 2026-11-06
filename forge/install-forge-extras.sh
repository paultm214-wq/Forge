#!/usr/bin/env bash
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ALL=(python-app wasm kotlin-jvm hdl cad infra android ros2 desktop frontend python-app-database node-database go-database)
if [[ "${1:-}" == --list ]]; then printf '%s\n' "${ALL[@]}"; exit 0; fi
names=("$@"); ((${#names[@]})) || names=("${ALL[@]}")
for name in "${names[@]}"; do [[ " ${ALL[*]} " == *" $name "* ]] || { echo "Unknown target: $name" >&2;exit 2; };done
secrets=()
[[ ! -f /etc/ssl/certs/ca-certificates.crt ]] || secrets+=(--secret id=system_ca,src=/etc/ssl/certs/ca-certificates.crt)
if [[ -n "${CODEX_PROXY_CERT:-}" && -f "$CODEX_PROXY_CERT" ]]; then secrets+=(--secret "id=proxy_ca,src=$CODEX_PROXY_CERT"); fi
build() { docker build "${secrets[@]}" --progress=plain -f "$HERE/toolchain-images/$1.Dockerfile" -t "$2" "${@:3}" "$HERE/toolchain-images"; }
ensure() { docker image inspect "$2" >/dev/null 2>&1 || build "$1" "$2"; }
ensure base forge-sandbox-extra-base:latest
failed=0
for name in "${names[@]}"; do
    case "$name" in
      android)
        if [[ "${ANDROID_SDK_LICENSE_ACCEPTED:-}" != yes ]]; then
            echo 'Android SDK requires explicit ANDROID_SDK_LICENSE_ACCEPTED=yes' >&2;failed=1;continue
        fi
        ensure kotlin-jvm forge-sandbox-kotlin-jvm:latest
        build android forge-sandbox-android:latest --build-arg ANDROID_SDK_LICENSE_ACCEPTED=yes || failed=1 ;;
      desktop) ensure python-app forge-sandbox-python-app:latest;build desktop forge-sandbox-desktop:latest || failed=1 ;;
      frontend) ensure python-app forge-sandbox-python-app:latest;build frontend forge-sandbox-ux:latest || failed=1 ;;
      python-app-database) ensure python-app forge-sandbox-python-app:latest;build database forge-sandbox-python-app-database:latest || failed=1 ;;
      node-database)
        ensure python-app forge-sandbox-python-app:latest;ensure frontend forge-sandbox-ux:latest
        build database forge-sandbox-node-database:latest --build-arg BASE=forge-sandbox-ux:latest || failed=1 ;;
      go-database)
        ensure python-app forge-sandbox-python-app:latest;ensure go-profile forge-sandbox-go-profile-base:latest
        build database forge-sandbox-go-database:latest --build-arg BASE=forge-sandbox-go-profile-base:latest || failed=1 ;;
      *) build "$name" "forge-sandbox-$name:latest" || failed=1 ;;
    esac
done
exit "$failed"
