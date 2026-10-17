#!/usr/bin/env bash
# Airgap IMPORT on the offline host (docs/ANLEITUNG.md §7-§8). Minimal stand-in for `lgx install`, `lgx update-data`
# and `lgx rollback` of PLAN §10.6-§10.7 (M5): SHA-256 check (no signature yet), `docker load` + image-ID check,
# secrets with the ownership compose.yml documents, deploy/.env, restore into legilux_next + rename-swap (the old data
# stays as legilux_prev), compose up, /readyz smoke; DOCKER-USER rules and an egress/status check. Needs root, Docker
# Engine + compose plugin, coreutils, iptables (firewall/check); no python on the host, no network.
#
# Usage (BUNDLE = a directory written by scripts/airgap_export.sh, e.g. the copy on the USB disk):
#   airgap_import.sh verify BUNDLE                 SHA256SUMS + MANIFEST only; changes nothing
#   airgap_import.sh install BUNDLE [options]      first install or re-install (idempotent): images, files, secrets,
#                                                  deploy/.env, firewall, restore, start, smoke
#   airgap_import.sh update-data BUNDLE [options]  = install --data-only: new data bundle, previous one kept
#   airgap_import.sh rollback [options]            swap legilux <-> legilux_prev (and back), restart mcp
#   airgap_import.sh keys [options]                write the Mistral key(s): $MISTRAL_API_KEY, else hidden prompts
#                                                  (sudo: --preserve-env=MISTRAL_API_KEY,MISTRAL_EMBED_API_KEY);
#                                                  embeddings: $MISTRAL_EMBED_API_KEY or the prompt (default: same key)
#   airgap_import.sh firewall [options]            DOCKER-USER: drop NEW outbound from br-lgx-*, except
#                                                  br-lgx-encl -> LGX_MISTRAL_IP:LGX_MISTRAL_PORT (idempotent)
#   airgap_import.sh check [options]               compose status, /readyz, UI health, firewall, egress + Mistral probe
#   airgap_import.sh down [options]                stop the stack (database volume, data and secrets are kept)
# Options:
#   --home DIR         installation directory (default $LGX_HOME or /opt/legilux)
#   --project NAME     compose project name (default legilux; stored in deploy/.env)
#   --llm-url URL      chat endpoint of the on-prem Mistral, https://<domain>/v1 (stored in deploy/.env)
#   --llm-model ID     chat model id (stored in deploy/.env)
#   --embed-url URL    embeddings base URL without /v1 (default: --llm-url without /v1)
#   --mistral-ip IP    IP of the Mistral endpoint: extra_hosts + the only firewall exception
#   --with-rerank      enable the optional TEI reranker (profile rerank, LGX_RERANK=auto); default: OFF
#   --without-rerank   disable it again
#   --no-testui        without the Streamlit tester UI (profile testui is on by default)
#   --localhost        also publish UI (127.0.0.1:8501) and MCP (127.0.0.1:8000) on the host (compose.localhost.yml)
#   --data-only        install: skip docker load (= update-data)
#   --no-firewall      install: do not touch DOCKER-USER
#   --no-start         install: images, files, secrets, .env only (no restore, no compose up)
#
# Layout under --home: deploy/{compose.yml,compose.localhost.yml,.env}, secrets/ (0700), db/95_roles.sql,
# bundles/<release_id>/ (dump, release.json, sha256, eval/G0) with the symlinks bundles/current and bundles/previous,
# models/hf/ (only with the reranker), scripts/, docs/. Manual compose commands: cd <home>/deploy && docker compose …
# (deploy/.env carries COMPOSE_PROJECT_NAME, COMPOSE_FILE and COMPOSE_PROFILES).
set -euo pipefail

CMD=${1:-}
[ $# -gt 0 ] && shift
BUNDLE=""
case "$CMD" in
  verify|install|update-data)
    BUNDLE=${1:-}
    [ -n "$BUNDLE" ] && [ "${BUNDLE#-}" = "$BUNDLE" ] || { echo "airgap_import: $CMD needs BUNDLE (--help)" >&2; exit 2; }
    shift ;;
  rollback|keys|firewall|check|down) ;;
  -h|--help|help|"") awk 'NR > 1 && /^#/ {sub(/^# ?/, ""); print; next} NR > 1 {exit}' "$0"; exit 0 ;;
  *) echo "airgap_import: unknown command $CMD (--help)" >&2; exit 2 ;;
esac
HOME_DIR=${LGX_HOME:-/opt/legilux}
PROJECT=""
LLM_URL=""
LLM_MODEL=""
EMBED_URL=""
MISTRAL_IP=""
RERANK=""
TESTUI=1
LOCALHOST=0
DATA_ONLY=0
FIREWALL=1
START=1
while [ $# -gt 0 ]; do
  case "$1" in
    --home) HOME_DIR=$2; shift 2 ;;
    --project) PROJECT=$2; shift 2 ;;
    --llm-url) LLM_URL=$2; shift 2 ;;
    --llm-model) LLM_MODEL=$2; shift 2 ;;
    --embed-url) EMBED_URL=$2; shift 2 ;;
    --mistral-ip) MISTRAL_IP=$2; shift 2 ;;
    --with-rerank) RERANK=on; shift ;;
    --without-rerank) RERANK=off; shift ;;
    --no-testui) TESTUI=0; shift ;;
    --localhost) LOCALHOST=1; shift ;;
    --data-only) DATA_ONLY=1; shift ;;
    --no-firewall) FIREWALL=0; shift ;;
    --no-start) START=0; shift ;;
    *) echo "airgap_import: unknown option $1 (--help)" >&2; exit 2 ;;
  esac
done
[ "$CMD" = update-data ] && DATA_ONLY=1

log() { printf '[import] %s\n' "$*" >&2; }
die() { printf '[import] ERROR: %s\n' "$*" >&2; exit 1; }
need_root() { [ "$(id -u)" = 0 ] || die "run as root (sudo): secrets are chowned to uid 10001, iptables, docker"; }
safe() { [[ $2 =~ ^[A-Za-z0-9._:/@+-]*$ ]] || die "$1: unexpected characters in '$2'"; }
safe --home "$HOME_DIR"
[ -z "$PROJECT" ] || safe --project "$PROJECT"
for v in "$LLM_URL" "$LLM_MODEL" "$EMBED_URL" "$MISTRAL_IP"; do safe option "$v"; done
ENV_FILE="$HOME_DIR/deploy/.env"
HOME_OPT=""  # repeated in the hints this script prints when --home is not the default
[ "$HOME_DIR" = /opt/legilux ] || HOME_OPT=" --home $HOME_DIR"

# ------------------------------------------------------------------------------------------------ helpers
dc() { (cd "$HOME_DIR/deploy" && docker compose "$@"); }
kv() { awk -F= -v k="$2" '$1 == k {sub(/^[^=]*=/, ""); print; exit}' "$1" 2>/dev/null || true; }
get_env() { kv "$ENV_FILE" "$1"; }
set_env() { # KEY VALUE: replace or append one line of deploy/.env (values are checked by safe())
  local tmp
  tmp=$(mktemp "$ENV_FILE.XXXXXX")
  awk -v k="$1" -v v="$2" 'BEGIN {d = 0} index($0, k "=") == 1 {print k "=" v; d = 1; next} {print} END {if (!d) print k "=" v}' \
    "$ENV_FILE" > "$tmp"
  chmod 0600 "$tmp"
  mv "$tmp" "$ENV_FILE"
}
rand_hex() { od -An -N32 -tx1 /dev/urandom | tr -d ' \n'; }
psql_q() { dc exec -T -e "PGOPTIONS=-c client_min_messages=warning" db psql -U postgres -v ON_ERROR_STOP=1 -qtA "$@"; }
db_exists() { [ "$(psql_q -c "SELECT 1 FROM pg_database WHERE datname = '$1'")" = 1 ]; }
release_of() { psql_q -d "$1" -c "SELECT release_id FROM lgx_meta.release LIMIT 1" 2>/dev/null || true; }
url_host() { local h=${1#*://}; h=${h%%/*}; echo "${h%%:*}"; }
url_port() {
  local h=${1#*://}; h=${h%%/*}
  if [ "${h#*:}" != "$h" ]; then echo "${h##*:}"; elif [ "${1%%://*}" = http ]; then echo 80; else echo 443; fi
}

verify_bundle() {
  [ -d "$BUNDLE" ] || die "bundle directory $BUNDLE not found"
  BUNDLE=$(cd "$BUNDLE" && pwd)
  for f in MANIFEST.env MANIFEST.json SHA256SUMS images/IMAGES.tsv; do
    [ -f "$BUNDLE/$f" ] || die "$BUNDLE/$f missing: not a bundle of scripts/airgap_export.sh"
  done
  log "checking SHA256SUMS of $BUNDLE"
  (cd "$BUNDLE" && sha256sum -c --strict --quiet SHA256SUMS) || die "checksum mismatch: the bundle is damaged, copy it again"
  local extra
  extra=$(cd "$BUNDLE" && comm -23 <(find . -type f ! -name SHA256SUMS -printf '%P\n' | LC_ALL=C sort) \
    <(sed -E 's/^[0-9a-f]{64} [ *]//' SHA256SUMS | LC_ALL=C sort))
  [ -z "$extra" ] || die "files not covered by SHA256SUMS: $extra"
  [ "$(kv "$BUNDLE/MANIFEST.env" LGX_BUNDLE_FORMAT)" = legilux-airgap/1 ] || die "unknown bundle format"
  B_PLATFORM=$(kv "$BUNDLE/MANIFEST.env" LGX_PLATFORM)
  B_RELEASE=$(kv "$BUNDLE/MANIFEST.env" LGX_RELEASE_ID)
  B_STEM=$(kv "$BUNDLE/MANIFEST.env" LGX_DATA_STEM)
  B_EMBED=$(kv "$BUNDLE/MANIFEST.env" LGX_RELEASE_EMBEDDING)
  safe platform "$B_PLATFORM"; safe release "$B_RELEASE"; safe stem "$B_STEM"
  [ -f "$BUNDLE/data/$B_STEM.dump" ] || die "data/$B_STEM.dump missing"
  log "bundle ok: platform $B_PLATFORM, data $B_RELEASE (embedding: $B_EMBED)"
}

load_images() {
  local name ref id file got
  while IFS=$'\t' read -r name ref id file; do
    [ -n "$name" ] || continue
    safe image "$ref"
    log "docker load $file"
    docker load -q -i "$BUNDLE/$file" >/dev/null
    got=$(docker image inspect --format '{{.Id}}' "$ref" 2>/dev/null || true)
    [ "$got" = "$id" ] || die "image $ref: ID $got, expected $id (MANIFEST): refusing it"
  done < "$BUNDLE/images/IMAGES.tsv"
}

install_files() {
  local rel="$HOME_DIR/bundles/$B_RELEASE"
  install -d -m 0755 "$HOME_DIR" "$HOME_DIR/deploy" "$HOME_DIR/db" "$HOME_DIR/bundles" "$HOME_DIR/scripts" \
    "$HOME_DIR/docs" "$rel" "$rel/eval"
  install -d -m 0700 "$HOME_DIR/secrets"
  if [ "$DATA_ONLY" = 0 ]; then
    install -m 0644 "$BUNDLE"/deploy/compose.yml "$BUNDLE"/deploy/compose.localhost.yml "$BUNDLE"/deploy/.env.example \
      "$BUNDLE"/deploy/images.lock "$BUNDLE"/deploy/models.lock "$HOME_DIR/deploy/"
    install -m 0755 "$BUNDLE"/scripts/*.sh "$HOME_DIR/scripts/"
    [ ! -f "$BUNDLE/docs/ANLEITUNG.md" ] || install -m 0644 "$BUNDLE/docs/ANLEITUNG.md" "$HOME_DIR/docs/"
  fi
  install -m 0644 "$BUNDLE/db/95_roles.sql" "$HOME_DIR/db/"
  install -m 0644 "$BUNDLE/data/$B_STEM".{dump,release.json,sha256} "$BUNDLE/MANIFEST.json" "$rel/"
  (cd "$rel" && sha256sum -c --strict --quiet "$B_STEM.sha256") || die "copied data bundle does not match $B_STEM.sha256"
  cp -r "$BUNDLE/eval/." "$rel/eval/"
  chmod -R a+rX "$rel/eval"
  if [ -d "$BUNDLE/models/hf" ] && [ "$DATA_ONLY" = 0 ]; then
    install -d -m 0755 "$HOME_DIR/models/hf"
    cp -r "$BUNDLE/models/hf/." "$HOME_DIR/models/hf/"
    chmod -R a+rX "$HOME_DIR/models"
    "$HOME_DIR/scripts/fetch_models.sh" --verify-only --dest "$HOME_DIR/models/hf" --lock "$HOME_DIR/deploy/models.lock" \
      >/dev/null || die "reranker model files do not match deploy/models.lock"
  fi
  [ -e "$HOME_DIR/bundles/current" ] || ln -sfn "$B_RELEASE" "$HOME_DIR/bundles/current"
}

ensure_secrets() { # name owner mode generate
  local spec name owner mode gen f
  for spec in "pg_superuser_pw 0:0 0600 1" "pg_ro_pw 10001:10001 0400 1" "mcp_token 10001:10001 0400 1" \
    "llm_api_key 10001:10001 0400 0" "embed_api_key 10001:10001 0400 0"; do
    read -r name owner mode gen <<< "$spec"
    f="$HOME_DIR/secrets/$name"
    if [ ! -e "$f" ]; then
      if [ "$gen" = 1 ]; then
        rand_hex | install -o "${owner%:*}" -g "${owner#*:}" -m "$mode" /dev/stdin "$f"
      else
        install -o "${owner%:*}" -g "${owner#*:}" -m "$mode" /dev/null "$f"
      fi
      if [ "$gen" = 1 ]; then
        log "secret $name generated ($owner $mode)"
      else
        log "secret $name created EMPTY ($owner $mode): fill it with 'airgap_import.sh keys'"
      fi
    else
      chown "$owner" "$f"
      chmod "$mode" "$f"
    fi
  done
}

ensure_env() {
  if [ ! -f "$ENV_FILE" ]; then
    install -m 0600 /dev/null "$ENV_FILE"
    cat > "$ENV_FILE" <<EOF
# deploy/.env — written by scripts/airgap_import.sh ($(date -u +%Y-%m-%dT%H:%M:%SZ)); read by docker compose in
# this directory. Edit the Mistral lines (docs/ANLEITUNG.md §7.4), then: sudo $HOME_DIR/scripts/airgap_import.sh firewall$HOME_OPT
# and: cd $HOME_DIR/deploy && docker compose up -d --wait
COMPOSE_PROJECT_NAME=${PROJECT:-legilux}
COMPOSE_FILE=compose.yml
COMPOSE_PROFILES=testui
LGX_HOME=$HOME_DIR
LGX_PLATFORM=$B_PLATFORM
LGX_HOSTNAME=legilux.intra
# Chat: the on-prem Mistral endpoint (OpenAI-compatible, …/v1) and a model id that supports tool calling
LGX_LLM_BASE_URL=https://mistral.invalid/v1
LGX_LLM_MODEL=mistral-small-latest
# Embeddings: none (lexical search) unless the data bundle carries vectors; same domain, WITHOUT /v1
LGX_EMBED_PROVIDER=none
LGX_EMBED_BASE_URL=https://mistral.invalid
LGX_EMBED_MODEL=mistral-embed
# No upstream DNS in the airgap: the Mistral domain resolves to this IP (extra_hosts); the only firewall exception
LGX_MISTRAL_HOST=mistral.invalid
LGX_MISTRAL_IP=192.0.2.10
LGX_MISTRAL_PORT=443
# Optional TEI reranker: OFF (airgap_import.sh install --with-rerank)
LGX_RERANK=off
LGX_RERANK_URL=
EOF
    log "wrote $ENV_FILE (placeholders for the Mistral endpoint: edit it, docs/ANLEITUNG.md §7.4)"
  fi
  [ -z "$PROJECT" ] || set_env COMPOSE_PROJECT_NAME "$PROJECT"
  [ "$DATA_ONLY" = 1 ] || set_env LGX_PLATFORM "$B_PLATFORM"
  set_env LGX_HOME "$HOME_DIR"
  if [ -n "$LLM_URL" ]; then
    set_env LGX_LLM_BASE_URL "$LLM_URL"
    set_env LGX_MISTRAL_HOST "$(url_host "$LLM_URL")"
    set_env LGX_MISTRAL_PORT "$(url_port "$LLM_URL")"
    [ -n "$EMBED_URL" ] || EMBED_URL=${LLM_URL%/v1}
  fi
  [ -z "$EMBED_URL" ] || set_env LGX_EMBED_BASE_URL "${EMBED_URL%/}"
  [ -z "$LLM_MODEL" ] || set_env LGX_LLM_MODEL "$LLM_MODEL"
  [ -z "$MISTRAL_IP" ] || set_env LGX_MISTRAL_IP "$MISTRAL_IP"
  local profiles="" files=compose.yml
  [ "$TESTUI" = 0 ] || profiles=testui
  if [ "$RERANK" = on ] || { [ -z "$RERANK" ] && [ "$(get_env LGX_RERANK)" != off ] && [ -n "$(get_env LGX_RERANK)" ]; }; then
    [ -d "$HOME_DIR/models/hf" ] || die "--with-rerank: no models/hf (export the bundle with --with-rerank)"
    docker image inspect lgx.local/tei:cpu-1.9.4 >/dev/null 2>&1 || die "--with-rerank: image lgx.local/tei:cpu-1.9.4 missing"
    profiles=${profiles:+$profiles,}rerank
    set_env LGX_RERANK auto
    set_env LGX_RERANK_URL http://rerank:8080
  elif [ "$RERANK" = off ]; then
    set_env LGX_RERANK off
    set_env LGX_RERANK_URL ""
  fi
  set_env COMPOSE_PROFILES "$profiles"
  if [ "$LOCALHOST" = 1 ] || [ "$(get_env COMPOSE_FILE)" = compose.yml:compose.localhost.yml ]; then
    files=compose.yml:compose.localhost.yml
  fi
  set_env COMPOSE_FILE "$files"
  if [ "$B_EMBED" != none ] && [ "$(get_env LGX_EMBED_PROVIDER)" = none ]; then
    log "note: this data bundle carries vectors ($B_EMBED); for hybrid search set LGX_EMBED_PROVIDER=mistral after 'keys'"
  fi
}

firewall() {
  command -v iptables >/dev/null || die "iptables not found"
  local ip port rule
  ip=$(get_env LGX_MISTRAL_IP)
  port=$(get_env LGX_MISTRAL_PORT)
  ip=${MISTRAL_IP:-${ip:-192.0.2.10}}
  port=${port:-443}
  safe ip "$ip"; safe port "$port"
  iptables -N DOCKER-USER 2>/dev/null || true
  while read -r rule; do
    [ -n "$rule" ] || continue
    read -ra args <<< "${rule/#-A/-D}"
    iptables "${args[@]}"
  done < <(iptables -S DOCKER-USER | grep -- '--comment lgx-airgap' || true)
  # -I inserts at the top: the last one (the Mistral exception) is evaluated first
  iptables -I DOCKER-USER -i br-lgx-edge ! -o br-lgx-edge -m conntrack ! --ctstate ESTABLISHED,RELATED \
    -m comment --comment lgx-airgap -j DROP
  iptables -I DOCKER-USER -i br-lgx-front ! -o br-lgx-front -m comment --comment lgx-airgap -j DROP
  iptables -I DOCKER-USER -i br-lgx-back ! -o br-lgx-back -m comment --comment lgx-airgap -j DROP
  iptables -I DOCKER-USER -i br-lgx-encl ! -o br-lgx-encl -m comment --comment lgx-airgap -j DROP
  iptables -I DOCKER-USER -i br-lgx-encl -d "$ip/32" -p tcp --dport "$port" -m comment --comment lgx-airgap -j ACCEPT
  log "DOCKER-USER: NEW outbound connections from br-lgx-* dropped, except br-lgx-encl -> $ip:$port (re-run after a reboot)"
}

restore_release() {
  local rid=$B_RELEASE dump="$HOME_DIR/bundles/$B_RELEASE/$B_STEM.dump" got bad old
  if db_exists legilux && [ "$(release_of legilux)" = "$rid" ]; then
    log "database legilux already serves $rid: nothing to restore"
    return 0
  fi
  log "restoring $rid into legilux_next (pg_restore -j 4)"
  psql_q -c 'DROP DATABASE IF EXISTS legilux_next WITH (FORCE)' -c 'CREATE DATABASE legilux_next'
  dc cp "$dump" db:/tmp/lgx-restore.dump
  dc exec -T db pg_restore -U postgres -d legilux_next -j 4 --exit-on-error --no-owner --no-privileges /tmp/lgx-restore.dump
  dc exec -T db rm -f /tmp/lgx-restore.dump
  psql_q -d legilux_next < "$HOME_DIR/db/95_roles.sql"
  psql_q -d legilux_next -c ANALYZE
  got=$(release_of legilux_next)
  [ "$got" = "$rid" ] || die "restored database names release '$got', expected $rid"
  bad=$(psql_q -d legilux_next -c "SELECT string_agg(check_name || '=' || n, ', ') FROM lgx_meta.invariant_violations WHERE n <> 0")
  [ -z "$bad" ] || die "invariant violations in the restored release: $bad (legilux unchanged)"
  psql_q -c 'ALTER DATABASE legilux_next SET default_transaction_read_only = on'
  dc stop mcp >/dev/null 2>&1 || true
  if db_exists legilux; then
    old=$(release_of legilux)
    psql_q -c 'DROP DATABASE IF EXISTS legilux_prev WITH (FORCE)' \
      -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = 'legilux'" \
      -c 'ALTER DATABASE legilux RENAME TO legilux_prev' >/dev/null
    log "previous data $old kept as legilux_prev (rollback: scripts/airgap_import.sh rollback)"
    if [ -n "$old" ] && [ -d "$HOME_DIR/bundles/$old" ]; then ln -sfn "$old" "$HOME_DIR/bundles/previous"; fi
  fi
  psql_q -c 'ALTER DATABASE legilux_next RENAME TO legilux'
  ln -sfn "$rid" "$HOME_DIR/bundles/current"
  log "legilux now serves $rid"
}

set_ro_password() { # the password of lgx_ro = the pg_ro_pw secret (hex), sent on stdin, never on a command line
  local pw
  pw=$(cat "$HOME_DIR/secrets/pg_ro_pw")
  [[ $pw =~ ^[A-Za-z0-9._-]+$ ]] || die "secrets/pg_ro_pw: unexpected characters"
  printf "ALTER ROLE lgx_ro PASSWORD '%s';\n" "$pw" | psql_q >/dev/null
}

readyz() { # prints /readyz of mcp; exit 0 iff status ready (and the release, if given, matches)
  dc exec -T mcp python -c '
import json, sys, urllib.error, urllib.request
try:
    body = urllib.request.urlopen("http://localhost:8000/readyz", timeout=10).read()
except urllib.error.HTTPError as exc:
    body = exc.read()
d = json.loads(body)
print(json.dumps(d, indent=1, ensure_ascii=False))
want = sys.argv[1] if len(sys.argv) > 1 else ""
sys.exit(0 if d.get("status") == "ready" and (not want or d.get("release_id") == want) else 1)
' "$@"
}

start_stack() {
  log "docker compose up (profiles: $(get_env COMPOSE_PROFILES); files: $(get_env COMPOSE_FILE))"
  if [[ ",$(get_env COMPOSE_PROFILES)," != *,rerank,* ]]; then  # `up` leaves a disabled profile's container running
    dc --profile rerank rm -sf rerank >/dev/null 2>&1 || true
  fi
  dc up -d --wait --wait-timeout 900 --remove-orphans
  local running
  running=$(dc ps --services --status running)
  if grep -qx lgx-streamlit <<< "$running"; then dc restart lgx-streamlit >/dev/null; fi  # re-reads bundles/current/eval
  dc up -d --wait --wait-timeout 300 >/dev/null
}

# ------------------------------------------------------------------------------------------------ commands
case "$CMD" in
  verify)
    verify_bundle
    ;;
  install|update-data)
    need_root
    verify_bundle
    [ "$DATA_ONLY" = 1 ] || load_images
    [ "$DATA_ONLY" = 0 ] || [ -f "$ENV_FILE" ] || die "update-data needs an installed system ($ENV_FILE missing)"
    install_files
    ensure_secrets
    ensure_env
    [ "$FIREWALL" = 0 ] || firewall
    if [ "$START" = 0 ]; then log "--no-start: files and images installed, nothing started"; exit 0; fi
    dc up -d --wait --wait-timeout 300 db
    restore_release
    set_ro_password
    start_stack
    readyz "$B_RELEASE" >&2 || die "/readyz is not ready with $B_RELEASE (see above; docker compose logs mcp)"
    log "installed: data $B_RELEASE, platform $(get_env LGX_PLATFORM), project $(get_env COMPOSE_PROJECT_NAME)"
    [ "$(get_env LGX_MISTRAL_HOST)" != mistral.invalid ] || log "next: set the Mistral endpoint in $ENV_FILE (docs/ANLEITUNG.md §7.4)"
    ;;
  rollback)
    need_root
    db_exists legilux_prev || die "no legilux_prev: nothing to roll back to"
    cur=$(release_of legilux)
    prev=$(release_of legilux_prev)
    log "rollback: $cur -> $prev"
    dc stop mcp >/dev/null
    psql_q -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname IN ('legilux', 'legilux_prev')" \
      -c 'ALTER DATABASE legilux RENAME TO legilux_swap' -c 'ALTER DATABASE legilux_prev RENAME TO legilux' \
      -c 'ALTER DATABASE legilux_swap RENAME TO legilux_prev' >/dev/null
    [ ! -d "$HOME_DIR/bundles/$prev" ] || ln -sfn "$prev" "$HOME_DIR/bundles/current"
    [ ! -d "$HOME_DIR/bundles/$cur" ] || ln -sfn "$cur" "$HOME_DIR/bundles/previous"
    start_stack
    readyz "$prev" >&2 || die "/readyz is not ready after the rollback"
    log "legilux serves $prev again; $cur is legilux_prev (run rollback again to switch back)"
    ;;
  keys)
    need_root
    [ -d "$HOME_DIR/secrets" ] || die "$HOME_DIR/secrets missing: install first"
    key=${MISTRAL_API_KEY:-}
    ekey=${MISTRAL_EMBED_API_KEY:-}
    if [ -z "$key" ]; then
      read -rsp "Mistral API key (chat; input hidden): " key || true; echo >&2  # no terminal: empty -> "empty key"
      if [ -z "$ekey" ]; then read -rsp "Mistral API key for embeddings (Enter = the same key): " ekey || true; echo >&2; fi
    fi
    [ -n "$key" ] || die "empty key (sudo drops an exported MISTRAL_API_KEY: sudo --preserve-env=MISTRAL_API_KEY,MISTRAL_EMBED_API_KEY $0 keys$HOME_OPT)"
    ekey=${ekey:-$key}
    printf '%s' "$key" | install -o 10001 -g 10001 -m 0400 /dev/stdin "$HOME_DIR/secrets/llm_api_key"
    printf '%s' "$ekey" | install -o 10001 -g 10001 -m 0400 /dev/stdin "$HOME_DIR/secrets/embed_api_key"
    log "wrote secrets/llm_api_key and secrets/embed_api_key (10001:10001 0400)"
    running=$(dc ps --services --status running 2>/dev/null | grep -xE 'mcp|lgx-streamlit' || true)
    if [ -n "$running" ]; then
      # shellcheck disable=SC2086  # one service name per word
      dc up -d --wait --force-recreate --no-deps $running >/dev/null
      log "recreated: $(echo $running) (secrets are read at start)"
    fi
    ;;
  firewall)
    need_root
    firewall
    ;;
  check)
    need_root
    rc=0
    dc ps --format 'table {{.Service}}\t{{.Status}}\t{{.Ports}}'
    echo "--- /readyz (mcp)"
    readyz || rc=1
    if dc ps --services --status running | grep -qx lgx-streamlit; then
      echo "--- tester UI"
      dc exec -T lgx-streamlit python -c 'import urllib.request as u; print("health:", u.urlopen("http://127.0.0.1:8501/_stcore/health", timeout=5).read().decode())' || rc=1
    fi
    echo "--- firewall (DOCKER-USER, rules of this installation)"
    rules=$(iptables -L DOCKER-USER -n -v -x 2>/dev/null | grep 'lgx-airgap' || true)
    if [ -n "$rules" ]; then echo "$rules"; else echo "NOT INSTALLED: sudo $0 firewall$HOME_OPT"; rc=1; fi
    echo "--- egress probe from mcp: TCP to 1.1.1.1:443 must fail"
    drops() { iptables -L DOCKER-USER -n -v -x 2>/dev/null | awk '/lgx-airgap/ && /br-lgx-encl/ && /DROP/ {print $1; exit}'; }
    before=$(drops)
    if dc exec -T mcp python -c 'import socket; socket.create_connection(("1.1.1.1", 443), timeout=4)' 2>/dev/null; then
      echo "FAIL: mcp reached 1.1.1.1:443"; rc=1
    else
      after=$(drops)
      echo "blocked (DROP counter br-lgx-encl: ${before:-none} -> ${after:-none} packets)"
    fi
    echo "--- Mistral endpoint from lgx-streamlit/mcp (GET <base>/models without key: 401 = reachable)"
    probe='import os, sys, urllib.error, urllib.request
url = sys.argv[1].rstrip("/") + "/models"
try:
    urllib.request.urlopen(url, timeout=6); print(url, "-> 200")
except urllib.error.HTTPError as exc:
    print(url, "->", exc.code, "(reachable)")
except Exception as exc:
    print(url, "-> NOT reachable:", type(exc).__name__, exc); sys.exit(1)'
    svc=mcp
    if dc ps --services --status running | grep -qx lgx-streamlit; then svc=lgx-streamlit; fi
    dc exec -T "$svc" python -c "$probe" "$(get_env LGX_LLM_BASE_URL)" || rc=1
    echo "--- embeddings / reranker (from /readyz): vector_status, rerank_status above"
    exit "$rc"
    ;;
  down)
    need_root
    dc down
    ;;
esac
