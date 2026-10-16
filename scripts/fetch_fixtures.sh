#!/usr/bin/env bash
# Large CC-BY parser fixtures (tests marked `large`) -> data/fixtures-large/, pinned by sha256.
#
# The committed fixtures (tests/fixtures/legilux/, <= ~6 MB, CC BY 4.0 only) cover the unit tests; the
# codes (civil 5.4 MB, travail 4.1 MB, ...), the 6.5 MB code-recueil and the 1.9 MB a936 original are
# listed under "large" in tests/fixtures/legilux/fixtures.json with their sha256 and fetched on demand.
#
# Sources, in order: an existing copy in the destination, the content-addressed raw cache
# (data/raw/<sha[:2]>/<sha>.<ext>, filled by `lgx fetch --sample`), then a polite GET of
# https://legilux.public.lu/filestore/eli/etat/leg/... only (project User-Agent, >= 1.2 s apart).
# A download whose sha256 differs from the pin (Legilux republished the file) is rejected and
# reported; the large tests then skip. Re-pin deliberately by updating fixtures.json.
#
# --trim rebuilds the committed trimmed a936 fixture from the full file: every base64 image payload
# is replaced by a 1x1 PNG (CC BY 4.0 allows adaptation; the change is recorded in fixtures.json).
#
# Usage: scripts/fetch_fixtures.sh [--offline] [--trim] [--list] [NAME ...]
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
MANIFEST="$ROOT/tests/fixtures/legilux/fixtures.json"
DEST=${LGX_FIXTURES_LARGE:-"$ROOT/data/fixtures-large"}
RAW=${LGX_DATA_DIR:-"$ROOT/data"}/raw
UA=${LGX_USER_AGENT:-"LegiLux-Local-Clone/0.1 (+https://github.com/masterries/LegiLux-Full-Local-MCP-Clone)"}
SPACING=${LGX_RATE_LIMIT_S:-1.2}
ALLOWED_PREFIX="https://legilux.public.lu/filestore/eli/etat/leg/"
OFFLINE=0
TRIM=0
LIST=0
NAMES=()
for arg in "$@"; do
  case "$arg" in
    --offline) OFFLINE=1 ;;
    --trim) TRIM=1 ;;
    --list) LIST=1 ;;
    -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
    *) NAMES+=("$arg") ;;
  esac
done

sha256() { sha256sum "$1" | cut -d' ' -f1; }

# name<TAB>url<TAB>sha256<TAB>file name, from fixtures.json (python -I: no imports from the cwd)
entries() {
  python3 -I - "$MANIFEST" "${NAMES[@]+"${NAMES[@]}"}" <<'PY'
import json, sys
doc = json.load(open(sys.argv[1], encoding="utf-8"))
wanted = set(sys.argv[2:])
for name, f in doc["large"].items():
    if not wanted or name in wanted:
        print("\t".join((name, f["url"], f["sha256"], f["path"], str(f["bytes"]))))
PY
}

if [[ $LIST -eq 1 ]]; then
  entries | awk -F'\t' '{printf "%-24s %10s  %s\n", $1, $5, $4}'
  exit 0
fi

mkdir -p "$DEST"
last_request=0
ok=0
failed=0
while IFS=$'\t' read -r name url pin file _bytes; do
  target="$DEST/$file"
  if [[ -f "$target" && "$(sha256 "$target")" == "$pin" ]]; then
    echo "ok      $name (present)"; ok=$((ok + 1)); continue
  fi
  ext=".${file##*.}"
  cached="$RAW/${pin:0:2}/$pin$ext"
  if [[ -f "$cached" ]]; then
    cp "$cached" "$target.tmp" && mv "$target.tmp" "$target"
    echo "ok      $name (raw cache)"; ok=$((ok + 1)); continue
  fi
  if [[ $OFFLINE -eq 1 ]]; then
    echo "missing $name (offline)"; failed=$((failed + 1)); continue
  fi
  if [[ "$url" != "$ALLOWED_PREFIX"* || "$url" == *.docx ]]; then
    echo "refused $name: $url is outside $ALLOWED_PREFIX" >&2; failed=$((failed + 1)); continue
  fi
  now=$(date +%s.%N)
  wait=$(python3 -I -c "import sys; print(max(0.0, float(sys.argv[1]) - (float(sys.argv[2]) - float(sys.argv[3]))))" \
    "$SPACING" "$now" "$last_request")
  sleep "$wait"
  last_request=$(date +%s.%N)
  if ! curl -fsS --proto '=https' --max-filesize 67108864 --retry 3 --retry-delay 5 \
      -A "$UA" -H 'Accept-Encoding: gzip' --compressed -o "$target.tmp" "$url"; then
    rm -f "$target.tmp"; echo "error   $name: GET failed" >&2; failed=$((failed + 1)); continue
  fi
  got=$(sha256 "$target.tmp")
  if [[ "$got" != "$pin" ]]; then
    rm -f "$target.tmp"
    echo "changed $name: sha256 $got != pinned $pin (Legilux republished it; re-pin in fixtures.json)" >&2
    failed=$((failed + 1)); continue
  fi
  mv "$target.tmp" "$target"; echo "ok      $name (downloaded)"; ok=$((ok + 1))
done < <(entries)

if [[ $TRIM -eq 1 ]]; then
  python3 -I - "$MANIFEST" "$DEST" "$ROOT/tests/fixtures/legilux" <<'PY'
import hashlib, json, re, sys
from pathlib import Path
doc = json.load(open(sys.argv[1], encoding="utf-8"))
small = doc["files"]["a936_jo_trimmed"]
full = Path(sys.argv[2]) / doc["large"][small["derived_from"]]["path"]
tiny = b"iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
data = re.sub(rb"base64,[A-Za-z0-9+/=\s]+", b"base64," + tiny, full.read_bytes())
if hashlib.sha256(data).hexdigest() != small["sha256"]:
    sys.exit("trimmed a936 does not match its pin")
(Path(sys.argv[3]) / small["path"]).write_bytes(data)
print("ok      a936_jo_trimmed (rebuilt)")
PY
fi

echo "large fixtures: $ok ok, $failed missing/failed -> $DEST"
[[ $failed -eq 0 ]]
