#!/usr/bin/env bash
# Apply db/schema/*.sql (PLAN Appendix B) to a database, in file-name order.
#
#   scripts/db_apply.sh [--dbname NAME] [--embed-dim N] [--recreate] [--container NAME] [--postload-only]
#
# Connection: with --container (or LGX_DB_CONTAINER) psql runs inside that container as PGUSER
# (default postgres); otherwise the local psql uses the usual libpq variables (PGHOST, PGPORT, PGUSER,
# PGPASSWORD). --recreate drops and recreates the database first ("rebuild, don't migrate").
# The server must have db/tsearch/french_unaccent.stop in $SHAREDIR/tsearch_data/ (the lgx-db image does).
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
schema_dir="${LGX_SCHEMA_DIR:-$here/db/schema}"
dbname="${LGX_DB_NAME:-legilux_dev}"
embed_dim=1024
recreate=0
postload_only=0
container="${LGX_DB_CONTAINER:-}"
pguser="${PGUSER:-postgres}"

usage() { sed -n '2,9p' "$0" | sed 's/^# \{0,1\}//'; exit "${1:-0}"; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dbname) dbname="$2"; shift 2 ;;
    --embed-dim) embed_dim="$2"; shift 2 ;;
    --recreate) recreate=1; shift ;;
    --container) container="$2"; shift 2 ;;
    --postload-only) postload_only=1; shift ;;
    -h|--help) usage 0 ;;
    *) echo "unknown argument: $1" >&2; usage 2 ;;
  esac
done

[[ "$dbname" =~ ^[a-z_][a-z0-9_]{0,62}$ ]] || { echo "invalid database name: $dbname" >&2; exit 2; }
[[ "$embed_dim" =~ ^[1-9][0-9]{0,4}$ ]] || { echo "invalid --embed-dim: $embed_dim" >&2; exit 2; }

run_psql() {
  if [[ -n "$container" ]]; then
    docker exec -i "$container" psql -X -q -U "$pguser" "$@"
  else
    psql -X -q "$@"
  fi
}

if [[ $recreate -eq 1 ]]; then
  echo "== recreate database $dbname"
  run_psql -d postgres -v ON_ERROR_STOP=1 \
    -c "DROP DATABASE IF EXISTS \"$dbname\" WITH (FORCE)" \
    -c "CREATE DATABASE \"$dbname\""
fi

shopt -s nullglob
files=("$schema_dir"/[0-9][0-9]_*.sql)
[[ ${#files[@]} -gt 0 ]] || { echo "no schema files in $schema_dir" >&2; exit 1; }
for f in "${files[@]}"; do
  name="$(basename "$f")"
  if [[ $postload_only -eq 1 && "$name" != 90_* ]]; then continue; fi
  echo "== $name -> $dbname"
  run_psql -d "$dbname" -v ON_ERROR_STOP=1 -v embed_dim="$embed_dim" -f - < "$f"
done
echo "== schema applied to $dbname (embed_dim=$embed_dim)"
