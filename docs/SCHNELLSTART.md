# Schnellstart: LegiLux auf einem Rechner zum Laufen bringen

> Ziel: auf **einem** Linux-Rechner mit Internet die Test-UI (Streamlit), den MCP-Server und die Stichprobe (≈ 50
> Werke, u. a. der Code du travail) laufen sehen; Chat über Mistral SaaS, wenn ein Schlüssel da ist. Kein Airgap, kein
> Vollabzug, keine Embeddings, kein Reranker – all das steht in der ausführlichen [ANLEITUNG.md](ANLEITUNG.md).

## Voraussetzungen

| | |
|---|---|
| Rechner | Linux x86_64 (z. B. Ubuntu 24.04, Debian 12/13); empfohlen 4 Kerne, 8 GB RAM, ≈ 10 GB frei (belegt werden ≈ 5–6 GB) |
| Software | Docker Engine mit Compose-Plugin ≥ 2.24 (<https://docs.docker.com/engine/install/>, schnell: `curl -fsSL https://get.docker.com \| sudo sh`), `git`, `make`, `curl` (Debian/Ubuntu: `sudo apt-get install -y git make curl`). [uv](https://docs.astral.sh/uv/) installiert das Skript auf Nachfrage selbst |
| Rechte | ein normaler Benutzer in der Gruppe `docker`: einmalig `sudo usermod -aG docker $USER`, danach ab- und wieder anmelden. Das Skript selbst braucht kein `sudo` |
| Netz | ausgehendes HTTPS zu `legilux.public.lu` und `data.legilux.public.lu` (Stichprobe), `mirror.gcr.io`, `ghcr.io`, PyPI und GitHub (Images, Python, uv), `astral.sh` (uv-Installer) und `api.mistral.ai` (Chat) |
| optional | ein Mistral-API-Schlüssel (<https://console.mistral.ai>) für den Chat |

## Installieren und starten

```sh
git clone -b claude/great-hawking-nj1tlj https://github.com/masterries/LegiLux-Full-Local-MCP-Clone.git
cd LegiLux-Full-Local-MCP-Clone
export MISTRAL_API_KEY=…     # optional; ohne fragt das Skript verdeckt danach (nur Enter = ohne Chat)
./scripts/quickstart.sh      # = make quickstart
```

Das Skript prüft die Voraussetzungen (Docker, Compose, Platz, RAM, freie Ports 8501/8000), richtet Python 3.13 mit
`uv sync` ein und baut **nur beim ersten Mal** die Stichprobe mit heutigem Stichtag: ein höflicher Abruf von Legilux
(robots.txt, ≈ 12 SPARQL-Abfragen mit ≈ 140 MB Metadaten, 49 Dateien, höchstens 1 Anfrage/s), Parser, Datenbank,
Prüfungen und Paket in `data/bundles/` (≈ 10–20 min; gemessen 7 min auf 4 Kernen). Dann legt es den Schlüssel in
`secrets/llm_api_key` ab (gitignored, das Verzeichnis ist nur für dich lesbar), baut die Docker-Images, startet
PostgreSQL, MCP-Server und Test-UI, wartet, bis alles gesund ist, und zeigt die Adressen. Der erste Lauf dauert ≈ 15–30
min (gemessen ≈ 10 min, Basis-Images schon im Cache), jeder weitere Sekunden; das Skript darf beliebig oft laufen.
Bricht etwas ab (Netz, Legilux nicht erreichbar, Image-Build): einfach neu starten, schon Geholtes bleibt in `data/`,
eine fertige Stichprobe wird nicht neu gebaut.

## Öffnen

- Auf dem Rechner selbst: <http://127.0.0.1:8501>.
- Von deinem Arbeitsplatz aus: alle Ports lauschen nur auf `127.0.0.1`, von außen ist nichts erreichbar. Deshalb ein
  SSH-Tunnel (das Skript druckt die passende Zeile), dann lokal <http://127.0.0.1:8501> öffnen:
  ```sh
  ssh -L 8501:127.0.0.1:8501 <benutzer>@<rechner>      # MCP-Server zusätzlich: -L 8000:127.0.0.1:8000
  ```
- MCP-Server für eigene Clients: `http://127.0.0.1:8000/mcp` mit `Authorization: Bearer lgx-dev-mcp-token` (nur
  Dev-Token); Zustand: `curl -s http://127.0.0.1:8000/readyz` (`"status":"ready"`).

## Ausprobieren

1. Seitenleiste: « prêt · data-…-sample » heißt: MCP-Server bereit.
2. Tab **Recherche directe** (ohne LLM): Tool `search_legislation`, query « art. L. 121-1 C. trav. », « Appeler
   l'outil » → Art. L. 121-1 des Code du travail an erster Stelle, mit Fassung, ELI und Warnhinweisen.
3. Tab **Corpus**: Datenstand, Stichtag, bekannte Lücken.
4. Mit Schlüssel: in der Seitenleiste « Tester l'appel d'outils », dann im Tab **Chat** « Que dit l'article L. 121-1 du
   Code du travail ? » → Antwort mit Quellen und Tool-Protokoll. Frage und gefundene (öffentliche) Texte gehen dabei an
   `api.mistral.ai`. Ohne Schlüssel den Chat nicht benutzen: Mistral antwortet nur mit `LLM HTTP 401`.
5. Tab **Évaluation (G0)**: 20 Rauchtests. Mit einer frisch gebauten Stichprobe ist **19/20, nur G0-01 rot,** normal
   (G0-01 prüft das Datum der Referenz-Stichprobe 2026-10-16).

## Stoppen, neu starten, Schlüssel ändern

```sh
./scripts/quickstart.sh status                      # Container, /readyz, Adressen
./scripts/quickstart.sh stop                        # anhalten (= make dev-down); Datenbank und Stichprobe bleiben
./scripts/quickstart.sh                             # wieder starten, baut nichts neu
make dev-logs SERVICE=mcp                           # Logs (mcp | db | db-load; LLM-Fehler zeigt nur die UI)
MISTRAL_API_KEY=… ./scripts/quickstart.sh           # Schlüssel setzen oder ersetzen (die Test-UI startet neu)
rm secrets/llm_api_key && ./scripts/quickstart.sh   # Schlüssel entfernen
```

Nach einem Neustart des Rechners starten die Container von selbst (außer nach `stop`). Platz sparen (≈ 1,8 GB): die
Build-Datenbank braucht nach dem ersten Lauf niemand mehr, `docker rm -f lgx-dev-db && docker volume rm lgx-dev-pgdata`
(ein späterer Bau legt sie neu an). Alles wieder entfernen: `make dev-down DEV_DOWN_FLAGS=-v`, die Build-Datenbank wie
eben, dann das Verzeichnis löschen.

## Häufige Probleme

| Meldung | Lösung |
|---|---|
| `permission denied … docker.sock` | `sudo usermod -aG docker $USER`, ab- und wieder anmelden (oder `newgrp docker`) |
| « Port 127.0.0.1:8501 ist belegt » / `port is already allocated` | ein anderes Programm nutzt den Port: `LGX_DEV_UI_PORT=8502 LGX_DEV_MCP_PORT=8002 ./scripts/quickstart.sh` (bei jedem Start mitgeben) |
| Chat: `LLM HTTP 401 … Invalid API Key` | Schlüssel falsch oder keiner gesetzt: `MISTRAL_API_KEY=… ./scripts/quickstart.sh` |
| Chat: `LLM injoignable` oder `délai dépassé` | der Rechner erreicht `api.mistral.ai:443` nicht (Firewall). Nur über einen HTTP-Proxy: `LGX_LLM_TRUST_ENV=1 LGX_LLM_HTTPS_PROXY=http://<proxy>:<port> ./scripts/quickstart.sh` |
| Abbruch beim Bau der Stichprobe (Netzfehler, HTTP 429, Legilux nicht erreichbar) | später erneut starten; schon Geholtes wird nicht noch einmal geladen |
| `invalid peer certificate: UnknownIssuer` oder `certificate verify failed` bei `uv sync` oder beim Image-Build (Schritt 5, erst nach der Stichprobe) | TLS-abfangender Firmen-Proxy: `export UV_SYSTEM_CERTS=1` und `LGX_DEV_CA_BUNDLE=/pfad/ca.crt ./scripts/quickstart.sh`; die Stichprobe bleibt, nur die Images werden gebaut ([ANLEITUNG.md](ANLEITUNG.md) § 1.5) |
| « der Stack zeigt data-…, nicht data-… » | die Dev-Datenbank wird nur einmal geladen: `make dev-down DEV_DOWN_FLAGS=-v && ./scripts/quickstart.sh` |

## Weiter

In [ANLEITUNG.md](ANLEITUNG.md): vollständige Daten (Vollabzug, ≈ 25.500 Dateien in mehreren Nachtfenstern, § 3.2),
Mistral-Einstellungen, anderes Modell oder On-Prem-Endpunkt (§ 2), Embeddings (§ 4), Reranker (§ 5.5), Airgap-Paket
und Installation ohne Internet (§ 6–§ 7). Für Entwickler: [dev/DEVELOPMENT.md](dev/DEVELOPMENT.md).

Daten: Legilux, © Ministère d'État – Service central de législation, CC BY 4.0. Inoffizielle Kopie: nur das
veröffentlichte Amtsblatt ist maßgeblich (loi du 23 décembre 2016, art. 5); keine Rechtsberatung.
