#!/usr/bin/env bash
# Schnellstart: LegiLux auf EINEM Linux-Rechner mit Internet zum Laufen bringen (docs/SCHNELLSTART.md).
#
# Prüft die Voraussetzungen, richtet die Python-Umgebung ein (uv sync), baut beim ersten Mal die Stichprobe (≈ 50
# Werke, höflicher Abruf von Legilux, ≈ 10–20 min; nie der Vollabzug), legt auf Wunsch den Mistral-Schlüssel ab und
# startet den Dev-Stack: PostgreSQL, MCP-Server und Streamlit-Test-UI, nur auf 127.0.0.1. Der Chat geht an Mistral
# SaaS (https://api.mistral.ai/v1) und nur mit Schlüssel; Embeddings und Reranker bleiben aus. Beliebig oft
# wiederholbar: Vorhandenes wird nicht neu gebaut. Braucht kein sudo. Vollabzug, Embeddings, Airgap: docs/ANLEITUNG.md.
#
# Aufruf: scripts/quickstart.sh [start|stop|status|help]          (make quickstart = start)
#   start    (Standard) prüfen, uv sync, Stichprobe bauen (falls keine da ist), Schlüssel, Stack starten, Adressen
#   stop     Stack anhalten (= make dev-down; Datenbank-Volume und Stichprobe bleiben, `start` startet wieder)
#   status   Container, /readyz und Adressen
#
# Umgebung (alles optional):
#   MISTRAL_API_KEY     Chat-Schlüssel; ersetzt secrets/llm_api_key. Ohne ihn fragt `start` verdeckt nach, solange
#                       secrets/llm_api_key fehlt (nur Enter = ohne Chat)
#   LGX_T               Stichtag der Stichprobe (Standard: die neueste in data/bundles/, sonst heute)
#   LGX_DEV_UI_PORT, LGX_DEV_MCP_PORT     Ports von Test-UI und MCP-Server (Standard 8501, 8000)
#   LGX_LLM_BASE_URL, LGX_LLM_MODEL       anderer Chat-Endpunkt / anderes Modell (Standard Mistral SaaS,
#                                         mistral-small-latest)
#   LGX_DEV_CA_BUNDLE   CA-Datei eines TLS-abfangenden Proxys für die Image-Builds (nur hinter so einem Proxy)
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$ROOT"
CMD=${1:-start}
COMPOSE=(docker compose -f deploy/compose.dev.yml)
KEY_FILE=secrets/llm_api_key
BUILD_DB=${LGX_DEV_DB:-lgx-dev-db}           # Build-PostgreSQL von `make ingest-sample` (127.0.0.1:55432)
MIN_COMPOSE=2.24.0
SELF=./scripts/quickstart.sh
KEY_CHANGED=0
T=

log() { printf '\n[quickstart] %s\n' "$*"; }
info() { printf '  %s\n' "$*"; }
warn() { printf '  Hinweis: %s\n' "$*" >&2; }
die() { printf '\n[quickstart] FEHLER: %s\n' "$*" >&2; exit 1; }
mk() { make -C "$ROOT" --no-print-directory "$@"; }
port_in_use() { timeout 2 bash -c ": </dev/tcp/127.0.0.1/$1" 2>/dev/null; }
container_running() { docker ps --format '{{.Names}}' | grep -x "$1" >/dev/null; }
stack_running() { [ -n "$("${COMPOSE[@]}" ps -q 2>/dev/null || true)" ]; }
published() { "${COMPOSE[@]}" port "$1" "$2" 2>/dev/null | head -n 1 || true; }   # z. B. 127.0.0.1:8501
json_field() { sed -n "s/.*\"$1\":\"\\([^\"]*\\)\".*/\\1/p" <<<"$2"; }
free_gb() { df -Pk "$1" 2>/dev/null | awk 'NR == 2 {print int($4 / 1048576)}'; }

check_docker() {
  command -v docker >/dev/null || die "Docker fehlt: Docker Engine mit Compose-Plugin installieren,
  https://docs.docker.com/engine/install/ (schnell: curl -fsSL https://get.docker.com | sudo sh)"
  local err
  if ! err=$(docker info 2>&1 >/dev/null); then
    case $err in
      *"permission denied"*)
        die "Docker ist für $(id -un) nicht ohne sudo nutzbar. Einmalig: sudo usermod -aG docker $(id -un),
  dann ab- und wieder anmelden (oder: newgrp docker) und $SELF erneut starten" ;;
      *) die "Docker-Dienst nicht erreichbar (${err%%$'\n'*}). Starten: sudo systemctl enable --now docker" ;;
    esac
  fi
}

check_prereqs() {
  log "1/5 Voraussetzungen prüfen"
  [ "$(uname -s)" = Linux ] || die "nur Linux wird unterstützt (gefunden: $(uname -s))"
  [ "$(uname -m)" = x86_64 ] || warn "getestet nur auf x86_64 (gefunden: $(uname -m)); die Images werden lokal gebaut"
  local missing=() c v
  for c in git make curl; do command -v "$c" >/dev/null || missing+=("$c"); done
  [ ${#missing[@]} -eq 0 ] || die "es fehlt: ${missing[*]} (Debian/Ubuntu: sudo apt-get install -y ${missing[*]})"
  check_docker
  v=$(docker compose version --short 2>/dev/null) || die "Docker-Compose-Plugin fehlt (docker compose):
  https://docs.docker.com/compose/install/linux/"
  v=${v#v}
  [ "$(printf '%s\n' "$MIN_COMPOSE" "$v" | sort -V | head -n 1)" = "$MIN_COMPOSE" ] ||
    die "Docker Compose $v ist zu alt (nötig ≥ $MIN_COMPOSE): https://docs.docker.com/compose/install/linux/"
  docker buildx version >/dev/null 2>&1 || warn "docker buildx fehlt; scheitert der Image-Build: Paket docker-buildx-plugin"
  if [ -n "${HTTPS_PROXY:-${https_proxy:-}}" ] && [ -z "${LGX_DEV_CA_BUNDLE:-}" ]; then
    warn "HTTPS_PROXY ist gesetzt. Fängt der Proxy TLS ab, scheitert sonst erst der Image-Build in Schritt 5
  (invalid peer certificate: UnknownIssuer). Dann mit der CA-Datei des Proxys starten:
  LGX_DEV_CA_BUNDLE=/pfad/ca.crt $SELF"
  fi
  info "Docker $(docker version --format '{{.Server.Version}}' 2>/dev/null), Compose $v"

  if ! command -v uv >/dev/null && [ -x "$HOME/.local/bin/uv" ]; then PATH=$HOME/.local/bin:$PATH; fi
  if ! command -v uv >/dev/null; then
    local answer=n
    if [ -t 0 ]; then
      read -rp "  uv fehlt. Jetzt mit dem offiziellen Installer nach ~/.local/bin installieren
  (curl -LsSf https://astral.sh/uv/install.sh | sh)? [J/n] " answer || true
      answer=${answer:-j}
    fi
    case $answer in
      [jJyY]*) curl -LsSf https://astral.sh/uv/install.sh | sh; PATH=$HOME/.local/bin:$PATH ;;
      *) die "uv fehlt. Installieren: curl -LsSf https://astral.sh/uv/install.sh | sh (https://docs.astral.sh/uv/)" ;;
    esac
    command -v uv >/dev/null || die "uv nach der Installation nicht gefunden: neue Shell öffnen, $SELF erneut starten"
  fi
  export PATH
  info "$(uv --version)"

  local disk droot ddisk mem_mb cpus
  disk=$(free_gb "$ROOT")
  droot=$(docker info --format '{{.DockerRootDir}}' 2>/dev/null || true)
  ddisk=$(if [ -n "$droot" ]; then free_gb "$droot"; fi)
  mem_mb=$(awk '/^MemTotal:/ {print int($2 / 1024)}' /proc/meminfo)
  cpus=$(nproc)
  info "frei: ${disk:-?} GB hier, ${ddisk:-?} GB für Docker ($droot); RAM $((mem_mb / 1024)) GB; $cpus CPU-Kerne"
  [ "${disk:-99}" -ge 10 ] && [ "${ddisk:-99}" -ge 10 ] ||
    warn "empfohlen ≈ 10 GB frei (Images ≈ 2 GB, Stichprobe und Datenbanken ≈ 3 GB, Python ≈ 0,5 GB)"
  [ "$mem_mb" -ge 7500 ] || warn "empfohlen 8 GB RAM; mit weniger kann das PDF-Parsing der Stichprobe scheitern"
  [ "$cpus" -ge 4 ] || warn "empfohlen 4 CPU-Kerne; mit weniger dauert der erste Bau länger"

  if ! stack_running; then
    local p
    for p in "${LGX_DEV_UI_PORT:-8501}" "${LGX_DEV_MCP_PORT:-8000}"; do
      ! port_in_use "$p" || die "Port 127.0.0.1:$p ist belegt (ss -ltnp | grep :$p). Andere Ports:
  LGX_DEV_UI_PORT=8502 LGX_DEV_MCP_PORT=8002 $SELF"
    done
  fi
}

pick_t() {
  if [ -n "${LGX_T:-}" ]; then
    T=$LGX_T
  else
    local bundles
    bundles=$(shopt -s nullglob; printf '%s\n' data/bundles/data-*-sample.dump | sort | tail -n 1)
    if [ -n "$bundles" ]; then T=${bundles#data/bundles/data-}; T=${T%-sample.dump}
    else T=$(TZ=Europe/Luxembourg date +%F); fi
  fi
  [[ $T =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]] || die "LGX_T=$T: erwartet JJJJ-MM-TT"
}

step_bundle() {
  pick_t
  local dump=data/bundles/data-$T-sample.dump started=0
  if [ -f "$dump" ]; then
    log "3/5 Stichprobe: vorhanden ($dump), wird nicht neu gebaut"
    return
  fi
  log "3/5 Stichprobe bauen (Stichtag $T) – nur beim ersten Mal, ≈ 10–20 min"
  info "Höflicher Abruf von legilux.public.lu (≤ 1 Anfrage/s): robots.txt, ≈ 12 SPARQL-Abfragen (Metadaten ≈ 140 MB),"
  info "≈ 50 Dateien; dann Parser (XML, HTML, PDF), Datenbank, Prüfungen (Gates), Paket in data/bundles/."
  info "Nie der Vollabzug. Bricht es ab (Netz, Legilux): $SELF erneut starten, Geholtes bleibt im Cache data/."
  if ! container_running "$BUILD_DB"; then
    started=1
    if ! docker ps -a --format '{{.Names}}' | grep -x "$BUILD_DB" >/dev/null && port_in_use "${LGX_DB_PORT:-55432}"; then
      die "Port 127.0.0.1:${LGX_DB_PORT:-55432} (Build-Datenbank) ist belegt. Anderer Port: LGX_DB_PORT=55433 $SELF"
    fi
  fi
  if ! mk ingest-sample LGX_T="$T" INGEST_FLAGS=--include-m2; then
    [ "$started" = 0 ] || docker stop "$BUILD_DB" >/dev/null 2>&1 || true
    die "Stichprobe nicht gebaut (Ausgabe oben). $SELF erneut starten setzt fort; Details: docs/ANLEITUNG.md § 3.1"
  fi
  if [ "$started" = 1 ]; then
    docker stop "$BUILD_DB" >/dev/null
    info "Build-Datenbank $BUILD_DB angehalten (nur zum Bauen nötig; ihre Daten bleiben im Volume)"
    info "Platz sparen (≈ 1,8 GB; ein späterer Bau legt sie neu an):"
    info "  docker rm -f $BUILD_DB && docker volume rm ${LGX_DEV_DB_VOLUME:-lgx-dev-pgdata}"
  fi
}

key_readable_in_container() {   # die UI läuft als uid 10001 ohne Zusatzrechte
  local owner mode
  read -r owner mode < <(stat -c '%u %a' "$KEY_FILE")
  [ "$owner" = 10001 ] || [ $((8#$mode & 4)) -ne 0 ]
}

step_key() {
  log "4/5 Mistral-Schlüssel (Chat)"
  local key=${MISTRAL_API_KEY:-} tmp
  if [ -z "$key" ] && [ ! -e "$KEY_FILE" ] && [ -t 0 ]; then
    read -rsp "  Mistral-API-Schlüssel (Eingabe verdeckt; nur Enter = ohne Chat): " key || true
    echo
  fi
  key=$(printf '%s' "$key" | tr -d '[:space:]')
  if [ -n "$key" ] || [ -e "$KEY_FILE" ]; then
    mkdir -p secrets
    # Schutz vor anderen Benutzern: das Verzeichnis (0700). Die Datei selbst ist 0444, damit uid 10001 im
    # UI-Container sie ohne sudo/chown lesen kann (Docker bindet sie mit Eigentümer und Modus des Hosts ein).
    if [ -O secrets ]; then chmod 0700 secrets; else warn "secrets/ gehört nicht $(id -un): Rechte nicht geändert"; fi
  fi
  if [ -n "$key" ]; then
    [ -w secrets ] || die "secrets/ ist nicht beschreibbar: sudo chown $(id -un) secrets"
    tmp=$(mktemp secrets/.llm_api_key.XXXXXX)
    printf '%s\n' "$key" >"$tmp"
    chmod 0444 "$tmp"
    if [ -r "$KEY_FILE" ] && cmp -s "$tmp" "$KEY_FILE"; then
      rm -f "$tmp"
      info "unverändert: $KEY_FILE"
    else
      mv -f "$tmp" "$KEY_FILE"
      KEY_CHANGED=1
      info "gespeichert: $KEY_FILE (gitignored; Verzeichnis secrets/ nur für $(id -un) lesbar)"
    fi
  elif [ -e "$KEY_FILE" ]; then
    info "vorhanden: $KEY_FILE (ersetzen: MISTRAL_API_KEY=… $SELF; entfernen: rm $KEY_FILE, dann $SELF)"
    if ! key_readable_in_container; then
      if [ -O "$KEY_FILE" ]; then chmod 0444 "$KEY_FILE"; info "Rechte auf 0444 gesetzt (lesbar für die Test-UI)"
      else die "$KEY_FILE ist für die Test-UI (uid 10001) nicht lesbar: sudo chmod 0444 $KEY_FILE"; fi
    fi
  else
    info "kein Schlüssel: Chat aus; Recherche directe, Évaluation und Corpus gehen ohne LLM"
    info "(später: MISTRAL_API_KEY=… $SELF)"
  fi
}

recreate_ui_if_key_changed() {
  # Compose erkennt einen geänderten Secret-Pfad nicht (der Konfigurations-Hash bleibt gleich) und die UI liest den
  # Schlüssel nur beim Start: passt der eingebundene Schlüssel nicht, den UI-Container entfernen; dev-up legt ihn neu an.
  local cid src
  cid=$("${COMPOSE[@]}" ps -aq lgx-streamlit 2>/dev/null || true)
  [ -n "$cid" ] || return 0
  src=$(docker inspect -f '{{range .Mounts}}{{if eq .Destination "/run/secrets/llm_api_key"}}{{.Source}}{{end}}{{end}}' "$cid")
  if [ -e "$KEY_FILE" ]; then
    [ "$KEY_CHANGED" = 0 ] && [ "$src" -ef "$KEY_FILE" ] && return 0
  else
    [ "$src" = /dev/null ] && return 0
  fi
  info "Test-UI wird neu erstellt (Schlüssel geändert; er wird nur beim Start gelesen)"
  "${COMPOSE[@]}" rm -sf lgx-streamlit >/dev/null 2>&1
}

summary() {
  local ui mcp ready status rel chat token=${LGX_DEV_MCP_TOKEN:-lgx-dev-mcp-token}
  local base=${LGX_LLM_BASE_URL:-https://api.mistral.ai/v1} model=${LGX_LLM_MODEL:-mistral-small-latest}
  ui=$(published lgx-streamlit 8501)
  mcp=$(published mcp 8000)
  [ -n "$ui" ] && [ -n "$mcp" ] || die "Test-UI oder MCP-Server laufen nicht: $SELF status, make dev-logs"
  ready=$(curl -fsS --max-time 10 "http://$mcp/readyz" 2>/dev/null || true)
  status=$(json_field status "$ready")
  rel=$(json_field release_id "$ready")
  if [ -e "$KEY_FILE" ]; then
    chat="Mistral, $base, Modell $model (Fragen und gefundene Texte gehen an diesen Host)"
  else
    chat="aus (kein Schlüssel); « Recherche directe » braucht keinen"
  fi
  cat <<EOF

LegiLux läuft: MCP-Status ${status:-unbekannt}, Datenstand ${rel:-unbekannt} (Stichprobe)
  Test-UI   http://$ui
  MCP       http://$mcp/mcp   (Header Authorization: Bearer $token – nur Dev)
  Status    http://$mcp/readyz
  Chat      $chat

Von einem anderen Rechner aus (alle Ports lauschen nur auf 127.0.0.1): dort
  ssh -L ${ui##*:}:127.0.0.1:${ui##*:} $(id -un)@<rechner>        (MCP dazu: -L ${mcp##*:}:127.0.0.1:${mcp##*:})
mit <rechner> = Name oder IP, unter der du diesen Rechner per SSH erreichst; dann im Browser http://127.0.0.1:${ui##*:}.

Ausprobieren: Tab « Recherche directe », Tool search_legislation, « art. L. 121-1 C. trav. »;
mit Schlüssel im Chat « Que dit l'article L. 121-1 du Code du travail ? ».
Status: $SELF status · Stoppen: $SELF stop · Logs: make dev-logs SERVICE=mcp
Mehr: docs/SCHNELLSTART.md, docs/ANLEITUNG.md
EOF
  [ "$status" = ready ] || warn "/readyz meldet nicht « ready »: make dev-logs SERVICE=mcp"
}

step_up() {
  log "5/5 Dev-Stack starten (Images bauen: beim ersten Mal 5–15 min, danach Sekunden)"
  chmod -R a+rX data/bundles db/schema 2>/dev/null || true   # db-load liest ohne Zusatzrechte (auch bei umask 077)
  recreate_ui_if_key_changed
  mk dev-up LGX_T="$T" || die "Dev-Stack nicht gestartet (Ausgabe oben). Erneut starten baut die Stichprobe nicht neu.
  TLS-Fehler beim Image-Build (invalid peer certificate, certificate verify failed)? TLS-abfangender Proxy:
  LGX_DEV_CA_BUNDLE=/pfad/ca.crt $SELF
  Sonst: $SELF status, make dev-logs SERVICE=mcp, docs/SCHNELLSTART.md"
  local ready rel want="data-${T//-/}-sample"
  ready=$(curl -fsS --max-time 10 "http://$(published mcp 8000)/readyz" 2>/dev/null || true)
  rel=$(json_field release_id "$ready")
  if [ -n "$rel" ] && [ "$rel" != "$want" ]; then
    warn "der Stack zeigt $rel, nicht $want: die Dev-Datenbank wird nur einmal geladen. Neu laden:
  make dev-down DEV_DOWN_FLAGS=-v && $SELF"
  fi
}

cmd_start() {
  check_prereqs
  log "2/5 Python-Umgebung (uv sync; beim ersten Mal 1–3 min)"
  mk sync
  step_bundle
  step_key
  step_up
  summary
  printf '\n[quickstart] fertig nach %d min %d s\n' $((SECONDS / 60)) $((SECONDS % 60))
}

cmd_stop() {
  check_docker
  log "Dev-Stack anhalten (Datenbank-Volume und Stichprobe bleiben)"
  mk dev-down
  if container_running "$BUILD_DB"; then
    info "Die Build-Datenbank $BUILD_DB läuft noch (nur zum Bauen nötig): docker stop $BUILD_DB"
  fi
  info "Wieder starten: $SELF"
}

cmd_status() {
  check_docker
  if ! stack_running; then
    echo "Dev-Stack läuft nicht. Starten: $SELF"
    return 0
  fi
  mk dev-ps
  if [ -e "$KEY_FILE" ]; then info "Schlüssel: $KEY_FILE vorhanden"; else info "Schlüssel: keiner (Chat aus)"; fi
  if container_running "$BUILD_DB"; then info "Build-Datenbank $BUILD_DB: läuft"; fi
  summary
}

case $CMD in
  start) cmd_start ;;
  stop) cmd_stop ;;
  status) cmd_status ;;
  help|-h|--help) awk 'NR > 1 && /^#/ {sub(/^# ?/, ""); print; next} NR > 1 {exit}' "$0" ;;
  *) echo "quickstart: unbekannter Befehl $CMD (start|stop|status|help)" >&2; exit 2 ;;
esac
