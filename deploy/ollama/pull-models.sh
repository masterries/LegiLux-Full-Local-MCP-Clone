#!/bin/sh
# One-shot model fetch for the CONNECTED dev host (compose.dev.yml service ollama-pull, profile ollama-pull).
# Starts a private Ollama server on 127.0.0.1 inside the job container, pulls $LGX_OLLAMA_PULL (space-separated
# tags) into the shared ollama-models volume, optionally creates the alias $LGX_OLLAMA_ALIAS from
# /opt/lgx/Modelfile.legilux-cpu, and stops. The runtime `ollama` service (internal network) never pulls.
# In the airgap the models come from the signed models bundle instead (PLAN §9.2).
set -eu
: "${LGX_OLLAMA_PULL:?set LGX_OLLAMA_PULL (e.g. qwen3:4b-instruct-2507-q4_K_M)}"
# Behind a TLS-intercepting proxy (connected dev host): LGX_DEV_CA_BUNDLE is mounted here; empty = system roots.
if [ -s /run/lgx/ca-bundle.crt ]; then export SSL_CERT_FILE=/run/lgx/ca-bundle.crt; fi
ollama serve >/tmp/ollama-serve.log 2>&1 &
pid=$!
trap 'kill "$pid" 2>/dev/null || true' EXIT INT TERM
i=0
until ollama list >/dev/null 2>&1; do
  i=$((i + 1))
  if [ "$i" -ge 60 ]; then echo "ollama-pull: server did not start" >&2; cat /tmp/ollama-serve.log >&2; exit 1; fi
  sleep 1
done
for tag in $LGX_OLLAMA_PULL; do
  echo "ollama-pull: pulling $tag"
  ollama pull "$tag"
done
if [ -n "${LGX_OLLAMA_ALIAS:-}" ]; then
  first=$(echo "$LGX_OLLAMA_PULL" | awk '{print $1}')
  sed "s|^FROM .*|FROM $first|" /opt/lgx/Modelfile.legilux-cpu > /tmp/Modelfile
  ollama create "$LGX_OLLAMA_ALIAS" -f /tmp/Modelfile
fi
ollama list
