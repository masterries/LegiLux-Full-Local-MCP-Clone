#!/bin/bash
# Run one catalog query against the Legilux SPARQL endpoint (politely: 1 s delay).
# usage: ./run_query.sh <output-name> [accept-mime] < query.rq
# Results are written to $OUT (default: ./out).
OUT=${OUT:-./out}
mkdir -p "$OUT"
NAME=$1
ACCEPT=${2:-application/sparql-results+json}
Q=$(cat)
sleep 1
curl -sS -G --max-time 180 -A "LegiLux-Local-Clone-Research/0.1 (polite sampling)" \
  -H "Accept: $ACCEPT" --data-urlencode "query=$Q" \
  -o "$OUT/$NAME" -w "HTTP %{http_code} size %{size_download} time %{time_total}s ctype %{content_type}\n" \
  "https://data.legilux.public.lu/sparqlendpoint"
