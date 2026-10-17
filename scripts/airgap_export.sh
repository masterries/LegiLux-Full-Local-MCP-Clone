#!/usr/bin/env bash
# Airgap bundle EXPORT, run on the connected build host (docs/ANLEITUNG.md §6). Minimal stand-in for the signed
# platform/data bundles of PLAN §10.5 (M5): SHA-256 only, no signature yet.
#
# Builds the runtime images from this checkout (lgx-db, lgx-mcp, lgx-streamlit) and writes ONE directory that holds
# everything the airgap host needs: the images (docker save), the data bundle (pg_dump + release.json + .sha256), the
# compose files, the import script, the G0 smoke set, the licences and, only with --with-rerank, the pinned TEI image
# and the reranker model (the reranker stays OFF on the airgap host unless it is enabled there too).
# MANIFEST.json names every image with its image ID (checked after `docker load`); SHA256SUMS covers every file.
#
# Usage: scripts/airgap_export.sh [--data FILE.dump] [--out DIR] [--platform VERSION] [--with-rerank]
#                                 [--no-build] [--ca-bundle FILE] [--force]
#   --data FILE     data bundle (default: the newest data/bundles/*.dump); FILE.release.json and the .sha256 file
#                   written by `lgx package` must sit next to it (data-<T>[-sample].{dump,release.json,sha256})
#   --out DIR       parent directory (default data/airgap, gitignored); the bundle is
#                   DIR/legilux-airgap-<platform>-<release_id>/
#   --platform V    image tag, = LGX_PLATFORM of deploy/compose.yml (default 2026.10.0)
#   --with-rerank   also export the pinned TEI image (deploy/images.lock) and data/models/hf (≈ +2.4 GB)
#   --no-build      retag the existing lgx.local/*:dev images instead of building them (they must be current)
#   --ca-bundle F   CA bundle of a TLS-intercepting proxy for the image builds (default: $LGX_DEV_CA_BUNDLE)
#   --force         replace an existing bundle directory of the same name
#
# Bundle layout:
#   MANIFEST.json                     platform, release, git commit, images {name: {ref, id, file}}, created_at
#   MANIFEST.env                      the same as KEY=VALUE lines (read by airgap_import.sh without python)
#   SHA256SUMS                        sha256 of every other file (checked with `sha256sum -c --strict SHA256SUMS`)
#   images/<name>.tar                 one `docker save` per image; images/IMAGES.tsv: name, ref, image ID, file
#   data/data-<T>[-sample].{dump,release.json,sha256}
#   deploy/{compose.yml,compose.localhost.yml,.env.example,images.lock,models.lock}
#   db/95_roles.sql                   role + GRANTs, re-applied after every restore
#   eval/G0/smoke.json                G0 smoke set for the Eval tab of the tester UI
#   scripts/{airgap_import.sh,fetch_models.sh}
#   models/hf/<name>@<revision>/      only with --with-rerank
#   docs/ANLEITUNG.md, README.md, LICENSES/
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
DATA=""
OUT="$ROOT/data/airgap"
PLATFORM=2026.10.0
WITH_RERANK=0
BUILD=1
FORCE=0
CA_BUNDLE=${LGX_DEV_CA_BUNDLE:-}
while [ $# -gt 0 ]; do
  case "$1" in
    --data) DATA=$2; shift 2 ;;
    --out) OUT=$2; shift 2 ;;
    --platform) PLATFORM=$2; shift 2 ;;
    --with-rerank) WITH_RERANK=1; shift ;;
    --no-build) BUILD=0; shift ;;
    --ca-bundle) CA_BUNDLE=$2; shift 2 ;;
    --force) FORCE=1; shift ;;
    -h|--help) awk 'NR > 1 && /^#/ {sub(/^# ?/, ""); print; next} NR > 1 {exit}' "$0"; exit 0 ;;
    *) echo "airgap_export: unknown option $1 (--help)" >&2; exit 2 ;;
  esac
done
log() { printf '[export] %s\n' "$*" >&2; }
die() { printf '[export] ERROR: %s\n' "$*" >&2; exit 1; }

command -v docker >/dev/null || die "docker not found"
command -v python3 >/dev/null || die "python3 not found"
[[ $PLATFORM =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]] || die "--platform: letters, digits, . _ - only"

# ------------------------------------------------------------------------------------------------ data bundle
if [ -z "$DATA" ]; then
  DATA=$(ls -1t "$ROOT"/data/bundles/*.dump 2>/dev/null | head -n 1 || true)
  [ -n "$DATA" ] || die "no data/bundles/*.dump: run 'make ingest-sample' (or the full ingest with --package) first"
  log "data bundle: $DATA (newest; --data to choose)"
fi
[ -f "$DATA" ] || die "data bundle $DATA not found"
DATA=$(cd "$(dirname "$DATA")" && pwd)/$(basename "$DATA")
BASE=${DATA%.dump}
STEM=$(basename "$BASE")
for f in "$BASE.release.json" "$BASE.sha256"; do [ -f "$f" ] || die "$f missing (written by lgx package)"; done
log "checking $STEM.sha256"
(cd "$(dirname "$DATA")" && sha256sum -c --strict --quiet "$STEM.sha256") || die "data bundle checksum mismatch"
RELEASE_ID=$(python3 -I -c 'import json,sys; print(json.load(open(sys.argv[1]))["release_id"])' "$BASE.release.json")
[[ $RELEASE_ID =~ ^[A-Za-z0-9._-]+$ ]] || die "unexpected release_id $RELEASE_ID"

NAME="legilux-airgap-$PLATFORM-$RELEASE_ID"
mkdir -p "$OUT"
OUT=$(cd "$OUT" && pwd)
DEST="$OUT/$NAME"
if [ -e "$DEST" ]; then
  [ "$FORCE" = 1 ] || die "$DEST exists (--force replaces it)"
  rm -rf "$DEST"
fi
mkdir -p "$DEST"/{images,data,deploy,db,eval,scripts,docs}
log "bundle: $DEST"

# ------------------------------------------------------------------------------------------------ images
DB_REF="lgx.local/lgx-db:18-0.8.7-$PLATFORM"
MCP_REF="lgx.local/lgx-mcp:$PLATFORM"
UI_REF="lgx.local/lgx-streamlit:$PLATFORM"
TEI_REF="lgx.local/tei:cpu-1.9.4"
SECRET=()
if [ -n "$CA_BUNDLE" ]; then
  [ -f "$CA_BUNDLE" ] || die "--ca-bundle $CA_BUNDLE not found"
  SECRET=(--secret "id=ca_bundle,src=$CA_BUNDLE")
fi
if [ "$BUILD" = 1 ]; then
  log "building $DB_REF, $MCP_REF, $UI_REF (docker layer cache; pinned bases)"
  docker build -q -t "$DB_REF" "$ROOT/db" >/dev/null
  docker build -q "${SECRET[@]}" -f "$ROOT/docker/lgx-mcp.Dockerfile" -t "$MCP_REF" "$ROOT" >/dev/null
  docker build -q "${SECRET[@]}" -f "$ROOT/ui/Dockerfile" -t "$UI_REF" "$ROOT" >/dev/null
else
  for pair in "lgx.local/lgx-db:dev $DB_REF" "lgx.local/lgx-mcp:dev $MCP_REF" "lgx.local/lgx-streamlit:dev $UI_REF"; do
    read -r src dst <<< "$pair"
    docker image inspect "$src" >/dev/null 2>&1 || die "image $src missing (make dev-up builds it)"
    docker tag "$src" "$dst"
  done
fi
IMAGES=("lgx-db=$DB_REF" "lgx-mcp=$MCP_REF" "lgx-streamlit=$UI_REF")
if [ "$WITH_RERANK" = 1 ]; then
  TEI_PIN=$(awk '$1 == "tei-cpu" {print $2}' "$ROOT/deploy/images.lock")
  [ -n "$TEI_PIN" ] || die "tei-cpu missing in deploy/images.lock"
  docker image inspect "$TEI_PIN" >/dev/null 2>&1 || { log "pulling $TEI_PIN"; docker pull -q "$TEI_PIN" >/dev/null; }
  docker tag "$TEI_PIN" "$TEI_REF"
  IMAGES+=("tei=$TEI_REF")
  log "verifying the reranker model against deploy/models.lock"
  "$ROOT/scripts/fetch_models.sh" --verify-only >/dev/null || die "reranker model missing or changed: make fetch-models"
fi
IMG_TSV="$DEST/images/IMAGES.tsv"
: > "$IMG_TSV"
for item in "${IMAGES[@]}"; do
  name=${item%%=*}
  ref=${item#*=}
  id=$(docker image inspect --format '{{.Id}}' "$ref")
  log "docker save $ref ($id)"
  docker save -o "$DEST/images/$name.tar" "$ref"
  printf '%s\t%s\t%s\timages/%s.tar\n' "$name" "$ref" "$id" "$name" >> "$IMG_TSV"
done

# ------------------------------------------------------------------------------------------------ files
cp -p "$DATA" "$BASE.release.json" "$BASE.sha256" "$DEST/data/"
cp -p "$ROOT/deploy/compose.yml" "$ROOT/deploy/compose.localhost.yml" "$ROOT/deploy/.env.example" \
  "$ROOT/deploy/images.lock" "$ROOT/deploy/models.lock" "$DEST/deploy/"
cp -p "$ROOT/db/schema/95_roles.sql" "$DEST/db/"
mkdir -p "$DEST/eval/G0"
cp -p "$ROOT/eval/gold/G0/smoke.json" "$DEST/eval/G0/"
cp -p "$ROOT/scripts/airgap_import.sh" "$ROOT/scripts/fetch_models.sh" "$DEST/scripts/"
cp -p "$ROOT/docs/ANLEITUNG.md" "$DEST/docs/" 2>/dev/null || log "docs/ANLEITUNG.md not found (skipped)"
cp -p "$ROOT/README.md" "$DEST/"
cp -rp "$ROOT/LICENSES" "$DEST/"
if [ "$WITH_RERANK" = 1 ]; then
  MODEL_DIR=$(awk '$1 == "model" {print $6}' "$ROOT/deploy/models.lock" | head -n 1)
  MODEL_SRC=${LGX_MODELS_DIR:-$ROOT/data/models/hf}/$MODEL_DIR
  [ -n "$MODEL_DIR" ] && [ -d "$MODEL_SRC" ] || die "reranker model directory $MODEL_SRC not found (make fetch-models)"
  mkdir -p "$DEST/models/hf"
  cp -rp "$MODEL_SRC" "$DEST/models/hf/"
fi

GIT=$(git -C "$ROOT" rev-parse HEAD 2>/dev/null || echo unknown)
if [ "$GIT" != unknown ] && [ -n "$(git -C "$ROOT" status --porcelain --untracked-files=no 2>/dev/null)" ]; then
  GIT="$GIT+dirty"
fi
python3 -I - "$DEST" "$PLATFORM" "$RELEASE_ID" "$STEM" "$GIT" "$WITH_RERANK" <<'PY'
import datetime, json, pathlib, sys

dest, platform, release_id, stem, git, rerank = sys.argv[1:7]
d = pathlib.Path(dest)
images = {}
for line in (d / "images" / "IMAGES.tsv").read_text().splitlines():
    name, ref, iid, file = line.split("\t")
    images[name] = {"ref": ref, "id": iid, "file": file}
release = json.loads((d / "data" / f"{stem}.release.json").read_text())
manifest = {
    "format": "legilux-airgap/1",
    "platform": platform,
    "release_id": release_id,
    "data_stem": stem,
    "sync_date": release.get("sync_date"),
    "release_kind": release.get("release_kind"),
    "requires": release.get("requires"),
    "git_commit": git,
    "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
    "with_rerank": rerank == "1",
    "images": images,
}
(d / "MANIFEST.json").write_text(json.dumps(manifest, indent=2) + "\n")
emb = (release.get("requires") or {}).get("embedding") or {}
env = {  # the same facts for airgap_import.sh, which parses (never sources) this file and needs no python
    "LGX_BUNDLE_FORMAT": manifest["format"],
    "LGX_PLATFORM": platform,
    "LGX_RELEASE_ID": release_id,
    "LGX_DATA_STEM": stem,
    "LGX_WITH_RERANK": rerank,
    "LGX_RELEASE_EMBEDDING": emb.get("model_id") or "none",
    "LGX_GIT_COMMIT": git,
}
(d / "MANIFEST.env").write_text("".join(f"{k}={v}\n" for k, v in env.items()))
PY

log "writing SHA256SUMS"
(cd "$DEST" && find . -type f ! -name SHA256SUMS -printf '%P\n' | LC_ALL=C sort | xargs -d '\n' sha256sum > SHA256SUMS)
(cd "$DEST" && sha256sum -c --strict --quiet SHA256SUMS) || die "self-check of SHA256SUMS failed"
log "done: $(du -sh "$DEST" | cut -f1) in $DEST"
log "copy the whole directory to the airgap host, then: sudo ./scripts/airgap_import.sh install . (docs/ANLEITUNG.md §7)"
echo "$DEST"
