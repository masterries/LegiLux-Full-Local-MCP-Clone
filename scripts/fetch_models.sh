#!/usr/bin/env bash
# Pinned Hugging Face models for the runtime (PLAN §9.2, §10.1; Appendix C service `rerank`) -> ONE shared dir.
#
# Run once on the CONNECTED dev/build host. Every file is downloaded from the pinned revision
# (https://huggingface.co/<repo>/resolve/<revision>/<file>), verified against the Hub's own metadata (LFS files:
# sha256; small files: git blob sha1) and then recorded with its byte size and sha256 in deploy/models.lock.
# Later runs (and the airgap side, --verify-only) never touch the network: they check every file against the
# lock and fail on any difference. A pin bump is deliberate: change the revision below, delete the old directory,
# re-run, review the new lock lines.
#
# Layout: $DEST/<name>@<revision>/<file>  (TEI: --model-id /models/hf/<name>@<revision>, HF_HUB_OFFLINE=1).
# Only the files a runtime needs are fetched (weights as safetensors, tokenizer, configs, the README with the
# licence header); never pytorch_model.bin / ONNX duplicates, never the README images.
#
# Usage: scripts/fetch_models.sh [--dest DIR] [--lock FILE] [--verify-only] [--list] [NAME ...]
#   default DEST = data/models/hf (gitignored; LGX_MODELS_DIR overrides), default LOCK = deploy/models.lock
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
DEST=${LGX_MODELS_DIR:-"$ROOT/data/models/hf"}
LOCK="$ROOT/deploy/models.lock"
VERIFY_ONLY=0
LIST=0
NAMES=()
while [ $# -gt 0 ]; do
  case "$1" in
    --dest) DEST=$2; shift ;;
    --lock) LOCK=$2; shift ;;
    --verify-only|--offline) VERIFY_ONLY=1 ;;
    --list) LIST=1 ;;
    -h|--help) sed -n '2,17p' "$0"; exit 0 ;;
    *) NAMES+=("$1") ;;
  esac
  shift
done

# name | repo | revision | licence | files (space separated)
MODELS=(
  "bge-reranker-v2-m3|BAAI/bge-reranker-v2-m3|953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e|Apache-2.0|config.json model.safetensors tokenizer.json tokenizer_config.json special_tokens_map.json sentencepiece.bpe.model README.md"
)

sha256() { sha256sum "$1" | cut -d' ' -f1; }
git_blob_sha1() { python3 -I -c 'import hashlib,sys; d=open(sys.argv[1],"rb").read(); print(hashlib.sha1(b"blob %d\0" % len(d) + d).hexdigest())' "$1"; }
fsize() { stat -c %s "$1"; }

wanted() {
  [ ${#NAMES[@]} -eq 0 ] && return 0
  local n
  for n in "${NAMES[@]}"; do [ "$n" = "$1" ] && return 0; done
  return 1
}

# lock lines: "file <name> <path> <bytes> <sha256>"
lock_entry() { [ -f "$LOCK" ] && awk -v n="$1" -v p="$2" '$1 == "file" && $2 == n && $3 == p { print $4, $5 }' "$LOCK"; }

verify_model() {  # name revision files -> 0 when every file matches the lock
  local name=$1 rev=$2 files=$3 dir="$DEST/$1@$2" f entry bytes sum bad=0
  for f in $files; do
    entry=$(lock_entry "$name" "$f" || true)
    if [ -z "$entry" ]; then echo "  $name/$f: not in $LOCK" >&2; bad=1; continue; fi
    if [ ! -f "$dir/$f" ]; then echo "  $name/$f: missing in $dir" >&2; bad=1; continue; fi
    read -r bytes sum <<<"$entry"
    if [ "$(fsize "$dir/$f")" != "$bytes" ] || [ "$(sha256 "$dir/$f")" != "$sum" ]; then
      echo "  $name/$f: size or sha256 differs from $LOCK" >&2; bad=1
    fi
  done
  if [ -f "$LOCK" ] && ! grep -q "^model $name .* $rev " "$LOCK"; then echo "  $name: revision $rev not in $LOCK" >&2; bad=1; fi
  return $bad
}

write_lock() {  # name repo rev licence files: rewrite this model's lines, keep the others
  local name=$1 repo=$2 rev=$3 lic=$4 files=$5 dir="$DEST/$1@$3" tmp f
  tmp=$(mktemp "$LOCK.XXXXXX")
  if [ -f "$LOCK" ]; then
    awk -v n="$name" '!(($1 == "model" || $1 == "file") && $2 == n)' "$LOCK" >"$tmp"
  else
    cat >"$tmp" <<'EOF'
# deploy/models.lock — pinned model files (PLAN §9.2, §10.1). Written by scripts/fetch_models.sh after verifying
# every download against the Hub metadata of the pinned revision; checked by `scripts/fetch_models.sh --verify-only`.
# model <name> <hf repo> <revision> <licence> <directory under models/hf>
# file  <name> <path> <bytes> <sha256>
EOF
  fi
  echo "model $name $repo $rev $lic $name@$rev" >>"$tmp"
  for f in $files; do echo "file $name $f $(fsize "$dir/$f") $(sha256 "$dir/$f")" >>"$tmp"; done
  mv "$tmp" "$LOCK"
}

fetch_model() {
  local name=$1 repo=$2 rev=$3 files=$4 dir="$DEST/$1@$3" meta f expected kind got part
  mkdir -p "$dir"
  meta=$(curl -fsS --retry 3 --max-time 60 "https://huggingface.co/api/models/$repo/tree/$rev?recursive=true")
  for f in $files; do
    # "<sha256|sha1> <size> <lfs|git>" from the Hub tree of the pinned revision
    read -r expected size kind < <(python3 -I -c '
import json, sys
for e in json.loads(sys.argv[1]):
    if e.get("type") == "file" and e["path"] == sys.argv[2]:
        lfs = e.get("lfs")
        print(lfs["oid"], e["size"], "lfs") if lfs else print(e["oid"], e["size"], "git")
        break
else:
    sys.exit(f"{sys.argv[2]} is not in the revision tree")
' "$meta" "$f")
    if [ -f "$dir/$f" ]; then
      got=$([ "$kind" = lfs ] && sha256 "$dir/$f" || git_blob_sha1 "$dir/$f")
      if [ "$got" = "$expected" ]; then echo "  $f: present, verified"; continue; fi
      echo "  $f: present but differs from the Hub metadata, downloading again"
    fi
    part="$dir/.$f.part"
    echo "  $f: downloading $size bytes"
    curl -fL --retry 5 --retry-delay 5 -C - --max-time 3600 -sS -o "$part" "https://huggingface.co/$repo/resolve/$rev/$f"
    got=$([ "$kind" = lfs ] && sha256 "$part" || git_blob_sha1 "$part")
    if [ "$got" != "$expected" ] || [ "$(fsize "$part")" != "$size" ]; then
      rm -f "$part"
      echo "error: $f does not match the Hub metadata of $repo@$rev ($kind $expected, got $got)" >&2
      return 1
    fi
    mv "$part" "$dir/$f"
  done
  chmod -R a+rX "$dir"   # TEI runs as an unprivileged user and mounts the directory read-only
}

status=0
for spec in "${MODELS[@]}"; do
  IFS='|' read -r name repo rev lic files <<<"$spec"
  wanted "$name" || continue
  if [ "$LIST" = 1 ]; then echo "$name $repo@$rev ($lic) -> $DEST/$name@$rev"; continue; fi
  echo "$name ($repo@$rev)"
  if verify_model "$name" "$rev" "$files" 2>/dev/null; then echo "  ok: every file matches $LOCK"; continue; fi
  if [ "$VERIFY_ONLY" = 1 ]; then
    verify_model "$name" "$rev" "$files" || true
    echo "error: $name does not match $LOCK (--verify-only: no download)" >&2
    status=1
    continue
  fi
  fetch_model "$name" "$repo" "$rev" "$files" || { status=1; continue; }
  write_lock "$name" "$repo" "$rev" "$lic" "$files"
  verify_model "$name" "$rev" "$files" && echo "  recorded in $LOCK" || status=1
done
exit $status
