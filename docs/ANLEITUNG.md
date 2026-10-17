# LegiLux lokal – Schritt-für-Schritt-Anleitung (Mistral-Stack, Build-Rechner und Airgap)

> Stand 17.10.2026 (Meilensteine M0–M2, Airgap-Skripte `scripts/airgap_export.sh` / `scripts/airgap_import.sh`).
> Für den Projekteigentümer: technisch, setzt aber keine Kenntnis des Codes voraus. Die Befehle wurden in genau dieser
> Form ausprobiert; was in dieser Umgebung nicht ging (kein Mistral-Schlüssel, kein Vollabzug), steht in
> [Anhang A](#anhang-a-was-am-17102026-getestet-wurde).

**Verbindliche Vorgaben.** Das System läuft vollständig auf dem **Mistral-Stack**: Chat über die Mistral-Chat-API
(OpenAI-kompatibel), Embeddings über Mistral Embed – heute Mistral SaaS (`https://api.mistral.ai`), später euer
Mistral On-Prem mit derselben API. Der Wechsel ist **nur** eine Änderung von Basis-URL(s) und Schlüssel(n). Es gibt
keine andere LLM-Laufzeit. Einziges Modell außerhalb von Mistral ist der optionale Reranker (bge-reranker-v2-m3 in
TEI); er ist **überall standardmäßig aus**.

**Schreibweisen.** `make …`, `uv …`, `docker …` laufen als normaler Benutzer (Mitglied der Gruppe `docker`) im
Repository-Verzeichnis `LegiLux-Full-Local-MCP-Clone/`; `sudo` steht dort, wo root nötig ist. `<…>` ist ein
Platzhalter. [E] = Schätzung. „zu prüfen“ = vor dem Produktivbetrieb bestätigen.

---

## 0. Überblick

### 0.1 Zwei Rechner, zwei Rollen

```
 BUILD-RECHNER (mit Internet)                                AIRGAP-RECHNER (ohne Internet)
 ─────────────────────────────                               ──────────────────────────────
 Legilux SPARQL + filestore (höflich, ≤ 1 Anfrage/s)          PostgreSQL 18 + pgvector  (fertige Datenbank)
   │ lgx ingest: Auswahl, Download, Parser, DB, Gates             ▲
   ▼                                                          MCP-Server (5 Tools, nur lesend)
 PostgreSQL (Build) ──► Daten-Paket (pg_dump)                     ▲                │ Frage-Embeddings (optional)
   │ optional: lgx embed ──► Mistral Embed (SaaS)             Test-UI (Streamlit) ─┤
   ▼                                                              │ Chat            ▼
 Dev-Stack zum Testen (Streamlit, MCP, Eval)                      └────────► Mistral On-Prem  (einzige erlaubte
   │                                                                          Verbindung, Firewall DOCKER-USER)
   ▼
 make airgap-export ──► ein Verzeichnis (USB) ──────────────► sudo ./scripts/airgap_import.sh install .
```

- **Build-Rechner:** holt die Daten von Legilux, baut die Datenbank, berechnet optional Embeddings über die
  Mistral-API, testet alles (Streamlit, Evaluation) und schnürt das Airgap-Paket.
- **Airgap-Rechner:** bekommt das Paket per Datenträger, prüft die Prüfsummen, lädt die Docker-Images, stellt die
  Datenbank wieder her und startet die Dienste. Die einzige Verbindung nach außen ist euer Mistral On-Prem (Chat und,
  wenn eingeschaltet, Frage-Embeddings); alles andere blockiert die Firewall-Regel (DOCKER-USER).

### 0.2 Was das Airgap-Paket enthält

`make airgap-export` schreibt **ein Verzeichnis** `data/airgap/legilux-airgap-<plattform>-<release>/`:

| Inhalt | Datei(en) | Bemerkung |
|---|---|---|
| Docker-Images | `images/lgx-db.tar`, `images/lgx-mcp.tar`, `images/lgx-streamlit.tar` (+ `images/tei.tar` nur mit Reranker) | `docker save`; `images/IMAGES.tsv` nennt Name, Tag und Image-ID (wird nach dem Laden geprüft) |
| Daten-Paket | `data/data-<T>[-sample].dump`, `.release.json`, `.sha256` | `pg_dump` der fertigen Datenbank, Beschreibung (Zählungen, Gates, ob Vektoren drin sind), Prüfsummen |
| Konfiguration | `deploy/compose.yml`, `deploy/compose.localhost.yml`, `deploy/.env.example`, `deploy/images.lock`, `deploy/models.lock` | Compose-Dateien des Airgaps; die `.env` mit euren Werten entsteht erst auf dem Airgap-Rechner |
| Datenbank-Rechte | `db/95_roles.sql` | Rolle `lgx_ro` (nur lesen) und GRANTs, nach jeder Wiederherstellung angewendet |
| Testfragen | `eval/G0/smoke.json` | 20 Rauchtests für den Tab « Évaluation (G0) » |
| Werkzeuge | `scripts/airgap_import.sh`, `scripts/fetch_models.sh` | Installation, Update, Rollback, Firewall, Prüfung; Modell-Prüfung |
| Reranker-Modell (optional) | `models/hf/bge-reranker-v2-m3@<revision>/` | nur mit `--with-rerank`, 2,2 GB |
| Dokumentation, Lizenz | `docs/ANLEITUNG.md`, `README.md`, `LICENSES/CC-BY-4.0.txt` | |
| Prüfsummen | `SHA256SUMS`, `MANIFEST.json`, `MANIFEST.env` | jede Datei steht in `SHA256SUMS`; das Manifest nennt Plattform, Datenversion, Git-Stand |

**Nicht** im Paket: API-Schlüssel und Passwörter (werden auf dem Airgap-Rechner erzeugt bzw. eingetragen), die
Build-Caches (`data/raw`, `data/parsed`, der Embedding-Cache `data/emb/…`) und kein LLM (Mistral ist extern).

### 0.3 Was von Mistral gebraucht wird

| Was | Wofür | Mistral SaaS | Mistral On-Prem |
|---|---|---|---|
| **Chat-Endpunkt** (OpenAI-kompatibel, `POST <basis>/chat/completions` mit `tools`) | Test-UI: Chat mit Tool-Aufrufen | `https://api.mistral.ai/v1` | `https://<eure-domain>/v1` |
| **Chat-Modell mit Tool-Calling** | dito | z. B. `mistral-small-latest` (§ 2.5) | Modell-ID eurer Installation |
| **Embeddings-Endpunkt** (`POST <basis>/v1/embeddings`) | optional: Vektoren für Dokumente (Build) und Fragen (Airgap) | `https://api.mistral.ai` (ohne `/v1`) | `https://<eure-domain>` (ohne `/v1`) |
| **Embedding-Modell** | dito | `mistral-embed` (1024 Dimensionen) | **dasselbe** Modell: die vom Endpunkt gemeldete Modell-ID muss gleich sein, sonst wird nur im Volltext gesucht (§ 4.6) |
| **API-Schlüssel** | Chat und Embeddings (darf derselbe sein) | aus der Mistral-Konsole | von euren Mistral-Administratoren |
| nur On-Prem: **IP-Adresse und Port** | Namensauflösung ohne DNS (`extra_hosts`) und die einzige Firewall-Ausnahme | – | z. B. `10.20.0.40`, Port 443 |
| nur On-Prem: **TLS-Zertifikat** | HTTPS | – | von einer öffentlich vertrauten CA; eine **interne CA wird noch nicht unterstützt** (§ 9) |
| **Ratenlimits** | Drossel einstellen (§ 2.4) | je nach Vertrag | je nach Installation |

### 0.4 Voraussetzungen

| | Build-Rechner | Airgap-Rechner |
|---|---|---|
| Betriebssystem | Linux x86_64 (getestet: Linux 6.18) | Linux x86_64 (Kandidaten laut Plan: Ubuntu 24.04, Debian 13, RHEL 9) |
| Docker | Docker Engine + Compose-Plugin **≥ 2.24** (getestet: Docker 29.8.2, Compose v5.6.0) | dito, offline installiert (Aufgabe des Betriebs) |
| Weitere Software | git, [uv](https://docs.astral.sh/uv/) (getestet 0.11.32; bringt Python 3.13 mit); optional Node.js + Playwright (nur für `make egress-check` und Screenshots) | bash, coreutils (`sha256sum`, `install`, `od`), `iptables`; **kein** Python, **kein** Internet |
| CPU / RAM | 4 vCPU reichen für die Stichprobe (so entwickelt); RAM 8 GB [E] | Plan: 8–16 Kerne, 32 GB RAM, **keine GPU** (das LLM ist extern); der Reranker braucht zusätzlich Kerne |
| Rechte | Benutzer in der Gruppe `docker`; `sudo` für Schlüsseldateien und `make egress-check` | root (`sudo`) für Installation, Firewall und Prüfung |
| Netz | Internet (Legilux, Mistral SaaS, Image-Registries); hinter einem TLS-Proxy dessen CA-Datei (§ 1.5) | nur der Mistral-On-Prem-Endpunkt |

### 0.5 Zeit- und Platzbedarf

| Schritt | Dauer | Platz |
|---|---|---|
| Build-Rechner einrichten (`uv sync`, erste Image-Builds) | 5–15 min [E] | `.venv` 0,5 GB; Images ≈ 1,8 GB (DB 665 MB, MCP 287 MB, UI 868 MB) |
| Stichprobe bauen, Caches vorhanden (`make ingest-sample`) | gemessen 2 min 44 s | `data/` ≈ 0,4 GB, Datenbank 315 MB, Dump 15 MB |
| Stichprobe frisch (leeres `data/`) | + SPARQL-Metadaten (≈ 12 Abfragen, ≈ 140 MB), 49 Dateien, PDF-Parsing ≈ 7 min (4 CPU) | dito |
| **Vollabzug** `lgx ingest --full` | 25.520 Datei-Abrufe: 7,1 h bei genau 1 Anfrage/s, 8,5 h beim Standardabstand 1,2 s; mit den Obergrenzen (2.000/h) 12,8 h = **4 Nachtfenster** 01:00–05:00 | **≥ 8 GB frei**: Rohdateien ≈ 2,9 GB [E], Caches 1–1,5 × davon, Build-DB 0,9–1,3 GB, Dump 0,2–0,5 GB |
| Embeddings Stichprobe (Standard-Drossel) | ≈ 0,56 h (459 Anfragen) | Cache < 0,1 GB [E] |
| Embeddings Vollbestand (Standard-Drossel) | ≈ 11,4 h [E] (≈ 9.350 Anfragen) | DB mit Vektoren 2,5–3,1 GB |
| Airgap-Paket exportieren | < 1 min (Stichprobe, Images im Cache) | 425 MB (Stichprobe); mit Reranker 2,8 GB; Vollbestand + 0,2–0,5 GB Dump |
| Airgap-Installation (erstmalig) | gemessen ≈ 2 min (Stichprobe); Vollbestand: Wiederherstellung 5–20 min [E] | Images ≈ 1,8 GB (+ 0,9 GB TEI), DB wie oben, Pakete |
| Daten-Update / Rollback | gemessen ≈ 1 min 10 s / ≈ 40 s (Stichprobe) | eine Vorversion der DB bleibt liegen (`legilux_prev`) |

---

## 1. Build-Rechner vorbereiten

### 1.1 Docker und Compose

Docker Engine mit Compose-Plugin nach <https://docs.docker.com/engine/install/> installieren, dann prüfen:

```sh
docker version --format 'Docker {{.Server.Version}}'
docker compose version                      # muss ≥ 2.24 sein (getestet: v5.6.0)
sudo usermod -aG docker "$USER"             # einmalig; danach ab- und wieder anmelden
```

### 1.2 uv (Python-Werkzeug)

```sh
curl -LsSf https://astral.sh/uv/install.sh | sh     # offizieller Installer, https://docs.astral.sh/uv/
uv --version
```

### 1.3 Repository holen

```sh
git clone https://github.com/masterries/LegiLux-Full-Local-MCP-Clone.git
cd LegiLux-Full-Local-MCP-Clone
git checkout <branch>        # nur falls der Stand noch nicht im Hauptzweig ist (Entwicklung: claude/great-hawking-nj1tlj)
```

### 1.4 Python-Umgebung

```sh
uv sync --all-extras         # Python 3.13 und alle Abhängigkeiten exakt aus uv.lock (= make sync)
uv run lgx config-check      # prüft config/*.yaml; zeigt u. a. "embedding_provider": "none"
make test                    # optional: alle Tests (die DB-Tests starten einen Wegwerf-PostgreSQL-Container)
```

### 1.5 Hinter einem TLS-Proxy

Fängt ein Firmen-Proxy TLS ab, braucht der Docker-Build dessen CA-Datei als Build-Secret (landet nie im Image):
`make dev-up LGX_DEV_CA_BUNDLE=/pfad/ca-bundle.crt` und `make airgap-export LGX_DEV_CA_BUNDLE=/pfad/ca-bundle.crt`.
uv nimmt mit `export UV_SYSTEM_CERTS=1` die Zertifikate des Systems. Erreicht die Test-UI Mistral SaaS nur über den
Proxy: `LGX_LLM_TRUST_ENV=1 LGX_LLM_HTTPS_PROXY=http://<proxy>:<port> make dev-up`.

---

## 2. Mistral konfigurieren

### 2.1 Alle Einstellungen auf einen Blick

Der Code liest **keine** Variable `MISTRAL_API_KEY`; in den Containern stehen Schlüssel immer in Dateien
(Docker-Secrets). Nur für die Entwicklung auf dem Host nehmen `lgx embed` auch `LGX_EMBED_API_KEY` und `make ui` auch
`OPENAI_API_KEY` aus der Umgebung (eine Schlüsseldatei hat Vorrang); diese Anleitung benutzt immer Dateien. Unten wird
`MISTRAL_API_KEY` nur als Shell-Variable benutzt, um diese Dateien zu schreiben. Auf dem Airgap liest
`airgap_import.sh keys` sie, aber nur mit `sudo --preserve-env=MISTRAL_API_KEY,MISTRAL_EMBED_API_KEY …`, weil `sudo`
exportierte Variablen sonst verwirft (§ 7.4).

| Einstellung | Build-/Testrechner (Dev-Stack, `lgx embed`) | Airgap (`/opt/legilux/deploy/.env`, Secrets) |
|---|---|---|
| Chat-URL | `LGX_LLM_BASE_URL` (Make-Variable oder Umgebung; Standard `https://api.mistral.ai/v1`) | `LGX_LLM_BASE_URL=https://<domain>/v1` |
| Chat-Modell | `LGX_LLM_MODEL` (Standard `mistral-small-latest`) | `LGX_LLM_MODEL=<modell-id>` |
| Chat-Schlüssel | Datei `secrets/llm_api_key`, Eigentümer **10001**, Modus 0400; `make dev-up` nimmt sie automatisch mit | `/opt/legilux/secrets/llm_api_key` (10001:10001, 0400) |
| Embeddings an/aus | `LGX_EMBED_PROVIDER` = `none` (Standard, `config/embedding.yaml`) oder `mistral` | `LGX_EMBED_PROVIDER=none` / `mistral` |
| Embedding-URL (**ohne** `/v1`) | `LGX_EMBED_BASE_URL` (Standard `https://api.mistral.ai`) | `LGX_EMBED_BASE_URL=https://<domain>` |
| Embedding-Modell | `LGX_EMBED_MODEL` (Standard `mistral-embed`, `dim: 1024` in `config/embedding.yaml`) | `LGX_EMBED_MODEL=mistral-embed` |
| Embedding-Schlüssel | für `lgx embed`: `LGX_EMBED_API_KEY_FILE=secrets/mistral_api_key` (Modus 0600, eigener Benutzer) | `/opt/legilux/secrets/embed_api_key` (10001:10001, 0400) |
| Drossel | `config/embedding.yaml` bzw. `LGX_EMBED_MAX_CONCURRENCY` (5), `LGX_EMBED_COOLDOWN_S` (20), `LGX_EMBED_BATCH_SIZE` (64), `LGX_EMBED_TIMEOUT_S`, `LGX_EMBED_RETRIES` | `LGX_EMBED_MAX_CONCURRENCY`, `LGX_EMBED_COOLDOWN_S` in der `.env` |
| nur Airgap | – | `LGX_MISTRAL_HOST` (Domain), `LGX_MISTRAL_IP`, `LGX_MISTRAL_PORT` (Firewall-Ausnahme und Namensauflösung) |

Warum Eigentümer 10001? MCP-Server und Test-UI laufen im Container als Benutzer 10001 ohne Zusatzrechte und lesen die
Schlüsseldatei direkt (Docker bindet sie mit Eigentümer und Modus des Hosts ein). Eine Datei, die 10001 nicht lesen
kann, stoppt den Dienst beim Start mit `UiSettingsError … cannot read secret file` – absichtlich, statt ohne
Schlüssel weiterzulaufen.

### 2.2 Variante A – Mistral SaaS (Build-Rechner, heute)

Schlüssel einmal eingeben (keine Anzeige; bash oder zsh) und in die beiden Dateien schreiben:

```sh
printf 'Mistral-API-Schlüssel: '; read -rs MISTRAL_API_KEY; echo
mkdir -p secrets                                                                   # gitignored
sudo install -o 10001 -g 10001 -m 0400 /dev/stdin secrets/llm_api_key <<< "$MISTRAL_API_KEY"   # Chat (Container)
install -m 0600 /dev/stdin secrets/mistral_api_key <<< "$MISTRAL_API_KEY"                    # lgx embed (Host)
unset MISTRAL_API_KEY
```

Dann den Dev-Stack starten (§ 5); die Standardwerte zeigen schon auf SaaS. Ein anderes Modell:

```sh
make dev-up LGX_LLM_MODEL=mistral-medium-latest     # oder dauerhaft: export LGX_LLM_MODEL=mistral-medium-latest
```

Ob der Schlüssel funktioniert und welche Modelle euer Konto hat (echter Aufruf, nicht hier getestet):
`curl -s -H "Authorization: Bearer $(cat secrets/mistral_api_key)" https://api.mistral.ai/v1/models | head -c 600`.

### 2.3 Variante B – Mistral On-Prem (eigene Domain, später)

Dieselbe API, nur andere URLs und Schlüssel. Beispiel-Domain `mistral.example.intra`, IP `10.20.0.40`:

- **Build-/Testrechner** (nur falls er euren On-Prem erreicht):
  ```sh
  make dev-up LGX_LLM_BASE_URL=https://mistral.example.intra/v1 LGX_LLM_MODEL=<modell-id>
  ```
  Embeddings gegen On-Prem: `LGX_EMBED_BASE_URL=https://mistral.example.intra` vor `uv run lgx … embed` (§ 4.3).
- **Airgap** – in `/opt/legilux/deploy/.env` (wird bei der Installation angelegt, § 7.4):
  ```
  LGX_LLM_BASE_URL=https://mistral.example.intra/v1
  LGX_LLM_MODEL=<modell-id>
  LGX_EMBED_PROVIDER=none                       # mistral, sobald das Daten-Paket Vektoren trägt (§ 4)
  LGX_EMBED_BASE_URL=https://mistral.example.intra
  LGX_EMBED_MODEL=mistral-embed
  LGX_MISTRAL_HOST=mistral.example.intra
  LGX_MISTRAL_IP=10.20.0.40
  LGX_MISTRAL_PORT=443
  ```
  Die Schlüssel kommen mit `sudo /opt/legilux/scripts/airgap_import.sh keys` in die Secret-Dateien.

**Wichtig für Embeddings:** Dokumente (Build-Seite, SaaS) und Fragen (Airgap, On-Prem) müssen mit **demselben**
Modell eingebettet werden. Der MCP-Server prüft das beim Start (§ 4.6); fragt eure Mistral-Administratoren, ob On-Prem
`mistral-embed` mit derselben gemeldeten Modell-ID und 1024 Dimensionen anbietet (zu prüfen).

### 2.4 Drosselung der Embeddings: Formel und Dauer

Ein gemeinsamer Begrenzer gilt für `lgx embed` (Build) und für die Frage-Embeddings des MCP-Servers
(`config/embedding.yaml`, jede Zeile per `LGX_EMBED_*` überschreibbar):

- höchstens `max_concurrency` = **5** Anfragen gleichzeitig;
- jeder der 5 „Slots“ wartet nach seiner Anfrage `cooldown_s` = **20 s**, bevor er wieder sendet;
- `batch_size` 64 Texte pro Anfrage, früher geteilt bei `max_tokens_per_request` = 16.000 geschätzten Tokens;
- bei 429 / 5xx / Netzfehlern bis zu 5 Wiederholungen (2, 4, 8, 16, 32 s bzw. `Retry-After`, der längere Wert gilt).

**Durchsatz ≈ max_concurrency × Texte pro Anfrage / (Latenz + cooldown_s).** Auf der Stichprobe passen wegen des
Token-Budgets im Mittel 42,8 Texte in eine Anfrage; bei 2 s angenommener Latenz [E]:
5 × 42,8 / (2 + 20) ≈ **9,7 Texte/s** ≈ 13,6 Anfragen/min.

| Bestand | Abschnitte | Anfragen | Dauer bei 5 / 20 s | ohne Cooldown (`LGX_EMBED_COOLDOWN_S=0`) |
|---|---|---|---|---|
| Stichprobe (`make embed-plan`, 17.10.2026) | 19.682 durchsuchbar (19.634 verschiedene Texte) | 459 | ≈ 0,56 h | wenige Minuten [E] |
| Vollbestand [E] | ≈ 400.000 | ≈ 9.350 | ≈ 11,4 h | ≈ 1,1 h [E] |

Die Drossel nur lockern, wenn eure Ratenlimits es erlauben, z. B. `LGX_EMBED_MAX_CONCURRENCY=2 LGX_EMBED_COOLDOWN_S=60`
(vorsichtiger) oder `LGX_EMBED_COOLDOWN_S=5` (schneller), jeweils vor den Befehl in § 4.3 geschrieben.

### 2.5 Welche Mistral-Modelle können Tool-Calling? (zu prüfen)

Die Test-UI braucht ein Chat-Modell, das `tools` versteht. Stand der öffentlichen Mistral-Dokumentation am 17.10.2026
(<https://docs.mistral.ai/getting-started/models/> und <https://docs.mistral.ai/capabilities/function_calling/>, ohne
Schlüssel abgerufen) – **alles zu prüfen**, die Aliase `…-latest` wandern mit neuen Versionen:

| Modell (API-Name) | Laut Dokumentation | Einschätzung für LegiLux |
|---|---|---|
| `mistral-small-latest` | Function Calling; Modellseite: Mistral Small 4 (`mistral-small-2603`, v26.03, 256k Kontext, Apache 2.0). Die Function-Calling-Seite nennt dafür noch Mistral Small 3.2 (`mistral-small-2506`), dessen Abschaltung die Modellübersicht für den 31.07.2026 angibt | **Standard der Konfiguration**; zuerst damit testen |
| `mistral-medium-latest` | Mistral Medium 3.5 (v26.04, 256k Kontext, Function Calling, offene Gewichte unter „Modified MIT“) | Kandidat, wenn Small zu ungenau ist |
| `mistral-large-latest` | Function Calling; Function-Calling-Seite: Mistral Large 3, Modellübersicht: Mistral Large 4 (v26.10, 1M Kontext) | Kandidat; teurer/langsamer [E] |
| `ministral-14b-latest`, `ministral-8b-latest` | Ministral 3, Function Calling | kleine On-Prem-Varianten; Qualität im Chat und mit `make e2e` prüfen |
| `magistral-medium-latest`, `magistral-small-latest` | Reasoning-Modelle mit Function Calling | eher langsam für den Tool-Loop [E] |
| `mistral-embed` | Embeddings, `mistral-embed-2312`, `/v1/embeddings`, 8k Kontext; die Dimension nennt die Seite nicht | vom Code erwartet: 1024 Dimensionen (`lgx embed` bricht sonst mit `dim_mismatch` ab) |

Welche dieser Modelle euer On-Prem bereitstellt, hängt von eurem Mistral-Vertrag ab (zu prüfen). In der Test-UI
prüft der Knopf **« Tester l'appel d'outils »** in der Seitenleiste, ob das eingestellte Modell Tool-Calling kann.

---

## 3. Daten holen und Datenbank bauen

Das Datum **T** ist der Stichtag eines Datenstands; alle Befehle eines Laufs brauchen dasselbe T. Die mitgelieferte
Stichprobe hat T = 2026-10-16. `make` liest T aus `LGX_T`.

### 3.1 Erst die Stichprobe (≈ 50 Werke, Entwicklung und Tests)

**Mit vorhandenen Caches** (dieses Repository nach einem früheren Lauf; offline, ohne jeden Abruf):

```sh
export LGX_T=2026-10-16
make ingest-sample
```

**Frischer Checkout** (`data/` leer) – ein höflicher Abruf der Stichprobe (robots.txt, ≈ 12 SPARQL-Abfragen für die
vollständigen Metadaten ≈ 140 MB, 49 Dateien inkl. der 5 PDF-Codes), nie der Vollabzug:

```sh
export LGX_T=$(TZ=Europe/Luxembourg date +%F)      # heutiger Stichtag (die Stichprobe von 2026-10-16 ist eingefroren)
make ingest-sample INGEST_FLAGS=--include-m2
```

Was passiert: `make dev-db` startet (falls nötig) den Build-PostgreSQL `lgx-dev-db` auf `127.0.0.1:55432`; dann
robots → Metadaten → Auswahl → Abruf (Cache zuerst) → Parser → Abschnitte → Laden in `legilux_build_<T>_sample` →
**Gates** (Plausibilitätsprüfungen) → Paket `data/bundles/data-<T>-sample.{dump,release.json,sha256}` → Wiederherstellung
als `legilux`. Am Ende steht z. B. `▶ gates ok {"pass": 28, "skip": 6, "warn": 1}` und `▶ package ok`. Kommen alle
Dateien aus dem Cache und nennt `data/logs/fetch_log.jsonl` keinen Abruf ab dem Vortag von T, wird `fetch_errors` übersprungen
(« no fetch attempts for T (cache reused) ») und es steht `{"pass": 27, "skip": 7, "warn": 1}` – ebenfalls bestanden. Nur
wenn die Gates bestehen, entsteht ein Paket. Details: `data/reports/<T>/gates.json`, `sizing.json`.

Danach den Dev-Stack starten (§ 5). Lief er schon mit einer älteren Stichprobe: `make dev-down DEV_DOWN_FLAGS=-v`
und `make dev-up` (der Dev-Stack lädt das Paket nur in eine leere Datenbank).

### 3.2 Dann vollständig (Betreiber-Schritt, ≈ 7 h reine Abrufzeit)

**1. Plan ohne einen einzigen Datei-Abruf.** Für einen neuen Stichtag zuerst robots.txt und die Metadaten holen
(2 + ≈ 12 Anfragen), dann den Plan rechnen:

```sh
export LGX_T=$(TZ=Europe/Luxembourg date +%F)      # T für den ganzen Vollabzug festhalten (auch über mehrere Nächte)
uv run lgx --t $LGX_T robots
uv run lgx --t $LGX_T discover                    # SPARQL-Metadaten ≈ 140 MB (siehe unten: oft unnötig)
uv run lgx --t $LGX_T ingest --full --dry-run     # Plan → data/reports/<T>/fetch_plan_full.json
```

`discover` holt die Metadaten **jedes Mal** neu (≈ 140 MB). Die Zeile entfällt, wenn für T schon ein Snapshot
existiert (`data/meta/<T>/`, z. B. nach `make ingest-sample` am selben Tag); `ingest` benutzt ihn dann.

Ergebnis auf dem Stand 2026-10-16 (29 s, 0 Abrufe): 25.569 Fassungen mit Datei, davon 49 im Cache; **25.520 Abrufe** +
52 SPARQL-Abfragen; 7,1 h bei 1 Anfrage/s, 8,5 h beim Standardabstand 1,2 s, **12,8 h unter der Obergrenze 2.000/h
= 4 Nachtfenster** (01:00–05:00 Uhr Luxemburger Zeit, ≤ 8.000 Abrufe pro Nacht, ≤ 30.000 pro Tag).

**2. Lauf** (Platz: ≥ 8 GB frei in `data/` und im PostgreSQL-Volume):

```sh
make dev-db
export LGX_PG_DSN=postgresql://postgres:lgx-dev-only@127.0.0.1:55432/postgres
uv run lgx --t $LGX_T ingest --full --confirm-full-crawl --package --container lgx-dev-db --restore-to legilux
```

- **Nachtfenster:** außerhalb 01:00–05:00 Uhr (Europe/Luxembourg) hält der Abruf vor der nächsten Anfrage an
  (« outside the nightly window », Exit-Code 1). Denselben Befehl mit **demselben T** in der nächsten Nacht erneut
  starten (z. B. in `tmux`). `--ignore-window` erlaubt einen durchgehenden Lauf (≈ 13 h); alle Ratenlimits bleiben.
- **Fortsetzen nach Abbruch:** einfach denselben Befehl wiederholen. Erledigte Adressen stehen in
  `data/logs/fetch_progress_<T>.txt`, vorhandene Dateien in `data/raw/` werden nicht noch einmal geholt; Parser- und
  Abschnitt-Caches hängen am Datei-Hash. Die Datenbank wird jedes Mal neu gebaut („rebuild, don't migrate“).
- **robots.txt geändert:** der Lauf stoppt mit einer Meldung samt sha256; nach Prüfung `uv run lgx --t $LGX_T robots-ack <sha256>`.
- **Ergebnis:** `data/bundles/data-<T>.dump` (+ `.release.json`, `.sha256`) und die Datenbank `legilux` in
  `lgx-dev-db`. Testen: `make mcp` (MCP auf `127.0.0.1:8001`) und in einem zweiten Terminal `make ui`
  (`http://127.0.0.1:8511`), oder die Generalprobe (§ 6.4).

**Was nie geladen wird:** Adressen außerhalb `https://legilux.public.lu/filestore/eli/etat/leg/…`; von robots.txt
gesperrte Pfade und gesperrte ELIs (Denylist, DSGVO-Liste); Mémorial B, Gemeindereglemente (RC),
Einbürgerungsgesetze, EU-Texte, Gesetzentwürfe, Texte geschlossener Fassungen; DOCX- und PDF-A-Dubletten;
`code/environnement_annexes` (239,9 MB, nur Metadaten); Dateien über 64 MB ohne Freigabe; SPARQL und robots.txt nur
mit `--refresh` bzw. ohne vorhandenen Stand für T erneut.

---

## 4. Embeddings (optional, Mistral)

Ohne Embeddings sucht das System im **Volltext** (französische Volltextsuche mit Gewichtung nach Seltenheit der Wörter;
auf der Stichprobe 82 % der Testfragen mit Treffer unter den ersten 10). Embeddings fügen eine semantische Suche hinzu
(„hybrid“). Sie bleiben aus, bis ein Schlüssel vorliegt; ob „hybrid ohne Reranker“ besser ist, wird dann gemessen.

### 4.1 Plan ohne Abruf

```sh
make embed-plan                    # = uv run lgx --t $LGX_T embed --dry-run
```

Zeigt Abschnitte, Anfragen, geschätzte Tokens und die Dauer unter der Drossel – sendet nie etwas.

### 4.2 Kosten- und Zeitabschätzung [E]

- **Zeit:** Tabelle in § 2.4 (Stichprobe ≈ 0,56 h, Vollbestand ≈ 11,4 h mit 5 / 20 s).
- **Kosten:** `mistral-embed` kostet laut Modellseite 0,1 (USD/EUR) pro Million Tokens (Stand 17.10.2026, zu prüfen).
  Stichprobe ≈ 7,2 Mio. geschätzte Tokens ≈ **0,7**; Vollbestand ≈ 400.000 × ≈ 366 Tokens ≈ 146 Mio. ≈ **15**.
  Die Schätzung zählt Zeichen / 3,5; Mistrals Tokenizer zählt anders. Chat-Kosten hängen vom Modell und der Nutzung ab.

### 4.3 Echter Lauf (Build-Rechner)

```sh
# Mistral SaaS (Schlüsseldatei aus § 2.2)
LGX_EMBED_PROVIDER=mistral LGX_EMBED_API_KEY_FILE=secrets/mistral_api_key uv run lgx --t $LGX_T embed
# On-Prem statt SaaS: zusätzlich LGX_EMBED_BASE_URL=https://mistral.example.intra davor schreiben
```

Ausgabe: Fortschritt alle 10 Anfragen, am Ende JSON (`embedded`, `failed_batches`, `missing`) und
`data/reports/<T>/embed.json`. Exit-Code 0 nur, wenn **jeder** durchsuchbare Abschnitt einen Vektor hat.

### 4.4 Wiederaufnahme

Den Befehl einfach wiederholen. Jede fertige Anfrage liegt sofort im Cache `data/emb/mistral-embed-1024.sqlite`
(nur Build-Seite, wird nie ausgeliefert); ein neuer Lauf schickt nur fehlende oder geänderte Abschnitte. Meldet die API
plötzlich eine andere Modell-ID, bricht der Lauf ab (`model_changed`) – dann das Modell festlegen oder einen neuen
Cache beginnen. Ein falscher Schlüssel bricht sofort ab (`unauthorized`).

### 4.5 Vektoren in die Datenbank bringen

Die Vektoren kommen beim nächsten Laden dazu – nur wenn der Cache **alle** durchsuchbaren Abschnitte abdeckt (sonst
bleibt das Paket „nur Volltext“, mit Meldung):

```sh
LGX_EMBED_PROVIDER=mistral make ingest-sample                         # Stichprobe: neu laden, Gates, Paket
LGX_EMBED_PROVIDER=mistral uv run lgx --t $LGX_T ingest --full --confirm-full-crawl --offline \
  --package --container lgx-dev-db --restore-to legilux               # Vollbestand: ohne neuen Abruf
```

Das Gate `embedding_vectors` prüft „alle oder keiner“; `.release.json` nennt unter `requires.embedding` Modell-ID und
Dimension, und Export wie Import melden dann `embedding: <modell-id>` statt `embedding: none`. Die Datenbank wächst von 0,9–1,3 GB auf 2,5–3,1 GB (Vollbestand).

### 4.6 Starttest (Probe) beim Start des MCP-Servers – und der Volltext-Fallback

Mit `LGX_EMBED_PROVIDER=mistral` bettet der MCP-Server beim Start (und stündlich, nach Fehlern jede Minute) einen
festen Probetext über **seinen** Endpunkt ein und vergleicht:

1. die vom Endpunkt **gemeldete Modell-ID** mit der beim Bau gespeicherten (`lgx.embedding_model`),
2. die **Dimension** (1024),
3. den **Kosinus** des Probevektors mit dem beim Bau gespeicherten: mindestens 0,99 (`probe_min_cosine`).

Nur wenn alles passt **und** das Paket Vektoren trägt, wird hybrid gesucht (`vector_status: ok`). Sonst sucht der
Server **ohne Fehlermeldung an den Nutzer** im Volltext weiter und nennt den Grund in `/readyz` (`vector_status`,
`vector_reason`), in jedem Suchergebnis (`retrieval.reason`) und in der Seitenleiste der Test-UI („hybride
indisponible …“). Die möglichen Gründe und ihre Lösung stehen in § 9.

Hybrid wird dort gesucht, wo der MCP-Server den Embeddings-Endpunkt erreicht: im Airgap über das Netz `enclave` zu
eurem On-Prem. Der Dev-Stack lässt den MCP-Server absichtlich nicht ins Internet; mit SaaS testet ihr hybrid in der
Generalprobe (§ 6.4, mit `--no-firewall` und `LGX_EMBED_PROVIDER=mistral`, `LGX_EMBED_BASE_URL=https://api.mistral.ai`).

---

## 5. Lokal testen (Dev-Stack auf dem Build-Rechner)

### 5.1 Starten

```sh
make dev-up                       # baut die Images (Cache), startet db, Wiederherstellung, mcp, Streamlit; wartet bis gesund
make dev-ps                       # Status
make dev-logs SERVICE=mcp         # Logs eines Dienstes (mcp | lgx-streamlit | db | db-load | rerank)
```

Alle Ports lauschen nur auf `127.0.0.1`. Von einem anderen Rechner aus per SSH-Tunnel:
`ssh -L 8501:127.0.0.1:8501 -L 8000:127.0.0.1:8000 <benutzer>@<build-rechner>`.

### 5.2 Streamlit öffnen: <http://127.0.0.1:8501>

Seitenleiste: Status des MCP-Servers (« prêt · data-20261016-sample »), die LLM-Einstellungen mit den
**Mistral-Standardwerten** (Base URL `https://api.mistral.ai/v1`, Modell `mistral-small-latest`) und ein Hinweis, an
welchen externen Host Fragen und gefundene Texte gehen.

| Tab | Wozu | Braucht Mistral-Schlüssel? |
|---|---|---|
| **Chat** | Frage stellen; das Modell ruft die MCP-Tools auf; Antwort mit Quellen (ELI, Fassung, Textbasis), Warnhinweisen und Tool-Protokoll. Modus « outils, repli retrieve-first » = Tools, bei Bedarf vorher suchen | ja |
| **Recherche directe** | jedes MCP-Tool direkt aufrufen (ohne LLM), z. B. `search_legislation` mit « art. L. 121-1 C. trav. » | nein |
| **Évaluation (G0)** | 20 Rauchtests (feste Tool-Aufrufe), Ergebnis als JSON | nein |
| **Corpus** | Datenstand, Alter, Uhr, Vektoren-Status, Texte je Textbasis, bekannte Lücken | nein |

Screenshots (Stand 17.10.2026, ohne Schlüssel): [Recherche directe](screenshots/direct-search.png),
[Corpus](screenshots/corpus.png). Ein Chat-Screenshot folgt nach dem ersten Lauf mit Mistral.

**Ohne Schlüssel** den Chat und « Tester l'appel d'outils » nicht benutzen: die UI schickt die Anfrage trotzdem an
`api.mistral.ai`, nur mit dem Platzhalter-Schlüssel `sk-no-key` (die Frage verlässt das Haus, die Antwort ist ein
Fehler). Die anderen drei Tabs brauchen kein LLM. Der MCP-Server erlaubt 30 Aufrufe pro Minute und Client, und die
ganze UI zählt als ein Client: zwei G0-Läufe innerhalb einer Minute ergeben zusätzliche rote Fälle; eine Minute warten.

Erster Chat-Test mit Schlüssel: in der Seitenleiste **« Tester l'appel d'outils »**, dann im Chat
« Que dit l'article L. 121-1 du Code du travail ? ». Eine in der Seitenleiste **eingetippte** andere Base-URL bekommt
nie den konfigurierten Schlüssel (Schutz vor Fehlleitung).

### 5.3 MCP-Endpunkt prüfen

```sh
curl -s http://127.0.0.1:8000/readyz        # 200 = bereit; JSON mit Datenstand und Zustand der optionalen Teile
curl -s http://127.0.0.1:8000/livez
```

Wichtige Felder von `/readyz`: `status` (`ready`), `release_id` (z. B. `data-20261016-sample`), `vector_status`
(`disabled:provider_none` = Embeddings aus, Standard), `rerank_status` (`off` = Reranker aus, Standard),
`clock_ok`, `canned_query_ok`. Für eigene MCP-Clients: `http://127.0.0.1:8000/mcp` mit
`Authorization: Bearer lgx-dev-mcp-token` (nur Dev).

### 5.4 Testfragen und Evaluation

```sh
make e2e E2E_STUB=1     # 5 Fragen über den Code-Pfad der UI gegen einen schlüssellosen Test-Stub (Sekunden)
make e2e                # dieselben 5 Fragen gegen den echten Mistral-Endpunkt (braucht den Schlüssel)
make eval               # Testsätze G0–G3, Volltext, ≈ 1,5 min → data/reports/eval/
```

Ergebnis von `make eval` am 17.10.2026 (Stichprobe): G0 **20/20**; G1 Zitat-Auflösung 0,997; G2 Treffer unter den
ersten 10 bei **82 %** der 218 Fragen (MRR 0,64); G3 Pflicht-Warnhinweise **100 %**. Die Testsätze sind
Entwürfe (§ 10).

**G0-01 prüft Datum/Release der Stichprobe 2026-10-16** (`sync_date` 2026-10-16, `release_id`
`data-20261016-sample`, fest eingetragen in `eval/gold/G0/smoke.json` und `ui/lgx_ui/gold/g0_dev_sample.json`). Bei
anderem Datenstand – eine frisch geholte Stichprobe mit heutigem T, jeder Vollabzug – ist **19/20 mit nur G0-01 rot
erwartet**, in `make eval` (die übrigen Sätze laufen trotzdem), im Tab « Évaluation (G0) » des Dev-Stacks und im
Airgap (§ 7.6).

### 5.5 Optional: Reranker (standardmäßig aus)

Der Reranker (bge-reranker-v2-m3 in TEI) ordnet die ersten 16 Treffer neu; auf 4 CPUs kostet das 17–25 s pro Suche.

```sh
make fetch-models       # einmalig: Modell 2,2 GB nach data/models/hf, sha256-geprüft
make rerank-up          # starten (bereit nach 1,5–6 min) und den Dev-MCP auf LGX_RERANK=auto stellen
make rerank-down        # wieder aus
```

### 5.6 Stoppen

```sh
make dev-down                        # stoppt alles (Volumes bleiben)
make dev-down DEV_DOWN_FLAGS=-v      # zusätzlich die Dev-Datenbank löschen (wird beim nächsten dev-up neu geladen)
```

---

## 6. Paket für den Airgap bauen

### 6.1 Vorher

1. Die Gates des Datenstands sind bestanden (sonst gibt es keinen Dump, § 3).
2. `make eval` zeigt die erwarteten Werte (§ 5.4).
3. **Egress-Prüfung** (Freigabe-Gate; root, Node.js mit dem npm-Paket `playwright` und dessen Chromium, das gepinnte
   CoreDNS-Image): `sudo make egress-check LGX_T=$LGX_T` (`sudo` verwirft das exportierte `LGX_T`, deshalb ausdrücklich;
   geprüft wird das Stichproben-Paket `data/bundles/data-<T>-sample.dump`). Liegen die Playwright-Browser nicht am
   Standardort, den Pfad mitgeben: `sudo make egress-check LGX_T=$LGX_T PLAYWRIGHT_BROWSERS_PATH=/pfad/zu/den/browsern`.
   Sie startet die Dienste in einem eigenen Compose-Projekt ohne Internetweg und zählt jeden Versuch nach außen (DNS,
   Pakete, Browser-Anfragen): Ergebnis **0 / 0 / 0** = bestanden
   (Bericht in `data/reports/egress/`). Das CoreDNS-Image einmalig holen:
   `docker pull mirror.gcr.io/coredns/coredns:1.14.7@sha256:7efd3c635b03efd68c4e8398fc45f0d993d0e9ab016f72c1cefb0fd6d01aa286`.

### 6.2 Exportieren

```sh
make airgap-export                                                   # Stichprobe: neuestes Paket in data/bundles/
make airgap-export AIRGAP_DATA=data/bundles/data-<T>.dump            # Vollbestand
make airgap-export AIRGAP_DATA=data/bundles/data-<T>.dump AIRGAP_EXPORT_ARGS=--with-rerank   # + Reranker (2,4 GB mehr)
```

Hinter einem TLS-Proxy `LGX_DEV_CA_BUNDLE=/pfad/ca-bundle.crt` anhängen. Das Skript prüft die Prüfsummen des
Daten-Pakets, baut die drei Images aus dem aktuellen Stand (`lgx.local/lgx-db:18-0.8.7-2026.10.0`,
`lgx.local/lgx-mcp:2026.10.0`, `lgx.local/lgx-streamlit:2026.10.0`), speichert sie mit `docker save`, kopiert
Daten, Compose-Dateien und Skripte und schreibt `SHA256SUMS`. Die letzte Zeile nennt das Verzeichnis, z. B.
`data/airgap/legilux-airgap-2026.10.0-data-20261016-sample` (Stichprobe: 425 MB). Weitere Optionen:
`scripts/airgap_export.sh --help` (`--platform`, `--out`, `--no-build`, `--force`).

### 6.3 Prüfsummen und Transport

```sh
cd data/airgap/legilux-airgap-2026.10.0-data-20261016-sample
sha256sum -c --strict --quiet SHA256SUMS && echo OK
sha256sum SHA256SUMS            # diesen Wert getrennt notieren (Papier, zweiter Kanal) und im Airgap vergleichen
cd -
cp -a data/airgap/legilux-airgap-2026.10.0-data-20261016-sample /media/usb/
```

Eine Signatur (Ed25519/cosign) folgt mit M5; bis dahin schützt der getrennt übermittelte Hash von `SHA256SUMS`.

### 6.4 Generalprobe auf dem Build-Rechner (empfohlen)

Das Paket genau so installieren wie im Airgap, aber in ein eigenes Verzeichnis und Compose-Projekt. Der Dev-Stack
belegt die Ports 8000/8501; deshalb vorher anhalten, oder ohne `--localhost` installieren:

```sh
make dev-down
sudo scripts/airgap_import.sh install data/airgap/legilux-airgap-2026.10.0-data-20261016-sample \
  --home /tmp/lgx-probe --project legilux-probe --localhost --no-firewall
sudo scripts/airgap_import.sh check --home /tmp/lgx-probe       # Status, /readyz, UI, Egress-Probe
```

`check` endet in der Generalprobe mit Exit-Code 1: ohne Firewall meldet es `firewall NOT INSTALLED`,
`FAIL: mcp reached 1.1.1.1:443` (der Build-Rechner hat Internet) und den Platzhalter-Endpunkt `mistral.invalid` als
`NOT reachable`. In der Generalprobe ist das erwartet; maßgeblich sind hier Dienste, `/readyz` und `health: ok`.

(`sudo make airgap-import AIRGAP_BUNDLE=<verzeichnis> AIRGAP_IMPORT_ARGS="--home /tmp/lgx-probe --project
legilux-probe --localhost --no-firewall"` ist dasselbe.) Mit Mistral SaaS: in `/tmp/lgx-probe/deploy/.env`
`LGX_LLM_BASE_URL=https://api.mistral.ai/v1`, für hybrid auch `LGX_EMBED_PROVIDER=mistral` und
`LGX_EMBED_BASE_URL=https://api.mistral.ai` (nur mit einem Paket mit Vektoren sinnvoll), dann
`sudo scripts/airgap_import.sh keys --home /tmp/lgx-probe` und
`cd /tmp/lgx-probe/deploy && sudo docker compose up -d --wait`. `--no-firewall` ist hier nötig, weil die Firewall nur
eine einzige Mistral-IP zulässt. Aufräumen:

```sh
cd /tmp/lgx-probe/deploy && sudo docker compose down -v && cd - && sudo rm -rf /tmp/lgx-probe
make dev-up
```

Nur ein Projekt im Airgap-Stil kann gleichzeitig laufen (feste Bridge-Namen `br-lgx-*`).

---

## 7. Installation im Airgap

### 7.1 Rechner vorbereiten

Docker Engine + Compose-Plugin (≥ 2.24) offline installieren (Aufgabe des Betriebs), dann:

```sh
docker compose version
sudo iptables -S DOCKER-USER            # die Kette muss existieren (legt Docker beim Start an)
```

Uhrzeit per interner NTP-Quelle stellen: weicht die Uhr vom Datenstand ab (vor T oder mehr als ein Jahr danach),
verweigern die Status-Tools Antworten ohne ausdrückliches Datum.

### 7.2 Dateien kopieren und prüfen

```sh
sudo mkdir -p /srv/legilux-import
sudo cp -a /media/usb/legilux-airgap-2026.10.0-data-20261016-sample /srv/legilux-import/
cd /srv/legilux-import/legilux-airgap-2026.10.0-data-20261016-sample
sha256sum SHA256SUMS                    # = der getrennt notierte Wert?
sudo ./scripts/airgap_import.sh verify .
```

`verify` prüft jede Datei gegen `SHA256SUMS`, lehnt Dateien ab, die dort nicht stehen, und ändert nichts. Eine
beschädigte Kopie meldet « checksum mismatch: the bundle is damaged, copy it again ».

### 7.3 Installieren

```sh
sudo ./scripts/airgap_import.sh install . \
  --llm-url https://mistral.example.intra/v1 --llm-model <modell-id> --mistral-ip 10.20.0.40 --localhost
```

Die Mistral-Optionen dürfen fehlen (dann Platzhalter `mistral.invalid`, § 7.4). Das Skript (idempotent, darf
wiederholt werden):

1. prüft die Prüfsummen (wie `verify`);
2. lädt die Images (`docker load`) und vergleicht jede Image-ID mit dem Manifest;
3. installiert nach `/opt/legilux` (`--home` ändert das): `deploy/`, `db/`, `scripts/`, `docs/`,
   `bundles/<release>/` mit dem Dump und den G0-Testfragen, Symlink `bundles/current`;
4. erzeugt fehlende Passwörter/Token (zufällig, 32 Byte) und leere Schlüsseldateien mit den richtigen Rechten:

   | Datei in `/opt/legilux/secrets/` | Eigentümer | Modus | Inhalt |
   |---|---|---|---|
   | `pg_superuser_pw` | root:root | 0600 | PostgreSQL-Superuser (erzeugt) |
   | `pg_ro_pw` | 10001:10001 | 0400 | Rolle `lgx_ro` des MCP-Servers (erzeugt) |
   | `mcp_token` | 10001:10001 | 0400 | Bearer-Token zwischen UI und MCP (erzeugt) |
   | `llm_api_key` | 10001:10001 | 0400 | Mistral-Chat-Schlüssel (leer, § 7.4) |
   | `embed_api_key` | 10001:10001 | 0400 | Mistral-Embeddings-Schlüssel (leer, § 7.4) |

5. schreibt `/opt/legilux/deploy/.env` (nur beim ersten Mal; danach werden nur die per Option gesetzten Zeilen geändert);
6. setzt die Firewall-Regeln (`--no-firewall` lässt sie weg, § 7.6);
7. startet PostgreSQL, stellt das Daten-Paket in `legilux_next` wieder her (`pg_restore -j 4`), wendet die Rechte an,
   prüft Release-ID und Invarianten, schaltet auf nur-lesen und benennt in `legilux` um;
8. startet MCP und Test-UI (`--no-testui` ohne UI; `--with-rerank` mit Reranker) und prüft `/readyz`.

Ende der Ausgabe: das `/readyz`-JSON und
`[import] installed: data data-20261016-sample, platform 2026.10.0, project legilux`.

### 7.4 Mistral-Endpunkt eintragen (Hostnamen, Schlüssel)

1. `/opt/legilux/deploy/.env` bearbeiten (`sudo nano /opt/legilux/deploy/.env`); die Zeilen aus § 2.3 setzen.
   `LGX_MISTRAL_HOST` → `LGX_MISTRAL_IP` landet als `extra_hosts`-Eintrag in MCP und UI (das Airgap hat kein
   Upstream-DNS). `LGX_HOSTNAME` ist der Name, unter dem Nutzer die UI später erreichen (Standard `legilux.intra`).
2. Schlüssel schreiben – das Skript fragt zweimal ohne Anzeige: Chat-Schlüssel, dann Embeddings-Schlüssel
   (nur Enter = derselbe Schlüssel):
   ```sh
   sudo /opt/legilux/scripts/airgap_import.sh keys
   ```
   Es schreibt `llm_api_key` und `embed_api_key` (10001:10001, 0400) und startet MCP und UI neu, weil Schlüssel nur
   beim Start gelesen werden. Für Automatisierung liest es stattdessen die Variablen `MISTRAL_API_KEY` und
   `MISTRAL_EMBED_API_KEY`, falls gesetzt. `sudo` verwirft exportierte Variablen; sie müssen ausdrücklich durchgereicht
   werden:
   ```sh
   sudo --preserve-env=MISTRAL_API_KEY,MISTRAL_EMBED_API_KEY /opt/legilux/scripts/airgap_import.sh keys
   ```
   Ohne Schlüssel und ohne Terminal endet `keys` mit « empty key » und ändert nichts.
3. Nach einer IP- oder Port-Änderung die Firewall neu setzen und die Dienste mit der neuen `.env` starten:
   ```sh
   sudo /opt/legilux/scripts/airgap_import.sh firewall
   cd /opt/legilux/deploy && sudo docker compose up -d --wait
   ```

### 7.5 Starten, Stoppen, Logs

`/opt/legilux/deploy/.env` enthält `COMPOSE_PROJECT_NAME`, `COMPOSE_FILE` und `COMPOSE_PROFILES`; im Verzeichnis
`deploy/` genügt deshalb `docker compose …`:

```sh
cd /opt/legilux/deploy
sudo docker compose up -d --wait          # starten bzw. Änderungen der .env anwenden
sudo docker compose ps
sudo docker compose logs -f mcp           # Logs (mcp | lgx-streamlit | db | rerank)
sudo docker compose down                  # stoppen (Datenbank-Volume bleibt)
```

Gleichwertig von überall, mit ausdrücklichen Dateien (die Profile kommen aus der `.env`):
`sudo docker compose -f /opt/legilux/deploy/compose.yml -f /opt/legilux/deploy/compose.localhost.yml up -d --wait`.
Ohne `--localhost` bei der Installation entfällt die zweite Datei; dann ist nichts auf dem Host veröffentlicht (der
Caddy-Proxy mit Anmeldung folgt mit M5). `compose.localhost.yml` veröffentlicht UI und MCP nur auf `127.0.0.1`
(Ports per `LGX_LOCAL_UI_PORT` / `LGX_LOCAL_MCP_PORT` in der `.env` änderbar).

### 7.6 Prüfen

```sh
sudo /opt/legilux/scripts/airgap_import.sh check
```

| Abschnitt der Ausgabe | Erwartet |
|---|---|
| Dienste | `db`, `mcp`, `lgx-streamlit` (und ggf. `rerank`) « healthy » |
| `/readyz (mcp)` | `"status": "ready"`, richtige `release_id`, `vector_status` (`disabled:provider_none` ohne Embeddings; `ok` = Probe bestanden), `rerank_status` (`off` Standard) |
| tester UI | `health: ok` |
| firewall | fünf Regeln mit `lgx-airgap` (vier DROP, eine ACCEPT zur Mistral-IP) |
| egress probe | `blocked (DROP counter br-lgx-encl: n -> n+k packets)` – der Versuch nach 1.1.1.1:443 wurde gezählt und verworfen |
| Mistral endpoint | `… /models -> 401 (reachable)` ohne Schlüssel = erreichbar; `NOT reachable` = Domain/IP/Port/Firewall prüfen (§ 9) |

Exit-Code 0 nur, wenn alles passt. Die **Test-UI** im Browser: vom Arbeitsplatz aus
`ssh -L 8501:127.0.0.1:8501 <admin>@<airgap-rechner>`, dann <http://127.0.0.1:8501>: Seitenleiste « prêt · data-… »,
**« Tester l'appel d'outils »**, eine Chat-Frage, Tab « Évaluation (G0) » → 20/20 mit der Stichprobe 2026-10-16, mit
jedem anderen Datenstand 19/20 mit nur G0-01 rot (erwartet, § 5.4), Tab « Corpus ».

Die vollständige Egress-Prüfung mit DNS-Sentinel und Browser-Mitschnitt (`make egress-check`, § 6.1) läuft auf dem
Build-Rechner (sie braucht Node.js, Playwright und das CoreDNS-Image); im Airgap prüft `check` die Firewall-Regeln mit
einem echten Verbindungsversuch.

### 7.7 Nach einem Neustart des Rechners

Die Container starten von selbst (`restart: unless-stopped`); die **Firewall-Regeln sind nicht dauerhaft** und müssen
nach jedem Neustart erneut gesetzt werden:

```sh
sudo /opt/legilux/scripts/airgap_import.sh firewall
```

---

## 8. Updates, Rollback und Backups

### 8.1 Neues Daten-Paket einspielen

Build-Rechner: neuer Vollabzug mit neuem T (§ 3.2), Gates, `make airgap-export AIRGAP_DATA=data/bundles/data-<T>.dump`,
Transport und Prüfung wie § 6.3 / § 7.2. Im Airgap:

```sh
sudo /opt/legilux/scripts/airgap_import.sh update-data /srv/legilux-import/legilux-airgap-<plattform>-<release>
```

Ablauf: Prüfsummen → Wiederherstellung in `legilux_next` → Rechte, Release-ID, Invarianten, nur-lesen → MCP kurz
anhalten → `legilux` wird `legilux_prev` (eine ältere Vorversion wird gelöscht) → `legilux_next` wird `legilux` → MCP
startet, `/readyz` muss die neue Release-ID melden. Die UI ist dabei einige Sekunden ohne MCP. Neue Images im Paket
spielt `install` statt `update-data` ein (gleicher Ablauf, plus `docker load`; die Daten werden übersprungen, wenn
`legilux` schon diese Release-ID hat).

Dasselbe Paket (gleiche Release-ID) noch einmal einspielen ändert nichts: `update-data` meldet « database legilux
already serves <release>: nothing to restore », endet mit Exit-Code 0 und legt **keine** `legilux_prev` an.

### 8.2 Rollback

```sh
sudo /opt/legilux/scripts/airgap_import.sh rollback      # legilux ↔ legilux_prev tauschen, ≈ 40 s
```

Ein zweites `rollback` schaltet wieder vor. Der Symlink `bundles/current` folgt. Gibt es noch keine Vorversion (nach
der Erstinstallation, auch wenn danach nur dasselbe Paket erneut eingespielt wurde), verweigert `rollback` mit
« no legilux_prev: nothing to roll back to » (Exit-Code 1, nichts geändert).

### 8.3 Backups

Die Datenbank muss nicht gesichert werden: sie entsteht jederzeit neu aus dem Daten-Paket („rebuild, don't migrate“).
Aufbewahren:

- die letzten **zwei Pakete** (Datenträger oder `/srv/legilux-import/`),
- Konfiguration und Secrets (die Secrets enthalten Schlüssel: Sicherung entsprechend schützen):
  ```sh
  sudo tar -C /opt/legilux -czf /root/legilux-konfig-$(date +%F).tar.gz deploy secrets db scripts
  ```
- auf dem Build-Rechner `data/bundles/` und, wenn Embeddings berechnet wurden, `data/emb/` (spart Zeit und Kosten
  beim nächsten Lauf).

Wiederherstellen auf einem neuen Rechner: zuerst
`sudo mkdir -p /opt/legilux && sudo tar -C /opt/legilux -xzf legilux-konfig-<datum>.tar.gz deploy/.env secrets`, dann
das Paket installieren (§ 7.3; `install` behält vorhandene Secrets und `.env`). Die umgekehrte Reihenfolge geht
**nicht**: `install` setzt das Passwort der Rolle `lgx_ro` aus einem neu erzeugten `pg_ro_pw`; ein danach
zurückgespieltes `pg_ro_pw` passt nicht mehr, und der MCP-Server startet nicht (`password authentication failed for
user "lgx_ro"`), auch nicht nach `docker compose down` / `up`.

---

## 9. Fehlerbehebung / FAQ

| Symptom | Ursache | Lösung |
|---|---|---|
| `/readyz`: `vector_status` = `disabled:no_api_key` | Embeddings an, aber `embed_api_key` leer | `sudo /opt/legilux/scripts/airgap_import.sh keys` |
| `disabled:endpoint_unreachable` / `disabled:endpoint_timeout` | Domain, IP, Port, Firewall oder `extra_hosts` falsch | `.env` (§ 7.4), `… firewall`, `… check` |
| `disabled:unauthorized` | Schlüssel falsch oder ohne Embeddings-Recht | Schlüssel erneuern, `… keys` |
| `disabled:model_mismatch` | On-Prem meldet eine andere Modell-ID als beim Bau der Vektoren | denselben Embedding-Stand bereitstellen lassen, oder die Vektoren gegen genau diesen Endpunkt neu berechnen (§ 4.3, `LGX_EMBED_BASE_URL`) |
| `disabled:probe_mismatch` | gleiche ID, aber andere Vektoren (Kosinus < 0,99) | wie oben |
| `disabled:dim_mismatch` | Modell mit anderer Dimension als 1024 | Embedding-Modell prüfen (§ 2.5) |
| `disabled:insecure_url` | Embedding-URL mit `http://` | `https://` verwenden (sonst ginge der Schlüssel im Klartext) |
| `disabled:no_vectors` | das Paket hat keine Vektoren | normal für ein Volltext-Paket; sonst § 4.5 |
| In allen diesen Fällen | – | die Suche läuft im Volltext weiter; erneuter Test nach 1 min (bei Erfolg stündlich) |
| `lgx embed`: viele `failed_batches`, Meldung `… still without a vector: re-run` | Mistral 429 (Ratenlimit) oder Netzfehler nach 5 Wiederholungen | denselben Befehl wiederholen (Cache); Drossel verschärfen, z. B. `LGX_EMBED_MAX_CONCURRENCY=2 LGX_EMBED_COOLDOWN_S=60` |
| Chat zeigt `LLM HTTP 429: …` | Ratenlimit des Chat-Modells | kurz warten und erneut fragen; Limits beim Anbieter/Admin erhöhen; kleineres Modell |
| Chat zeigt `LLM injoignable (…)` | Endpunkt nicht erreichbar | URL; Dev hinter Proxy: `LGX_LLM_TRUST_ENV=1 LGX_LLM_HTTPS_PROXY=…`; Airgap: `… check` |
| « Tester l'appel d'outils » schlägt fehl | Modell ohne Tool-Calling | anderes Modell (§ 2.5); Modus « retrieve-first » geht auch ohne Tools |
| `keys`: « empty key » | kein Terminal für die Eingabe, und `sudo` hat `MISTRAL_API_KEY` verworfen | `sudo --preserve-env=MISTRAL_API_KEY,MISTRAL_EMBED_API_KEY … keys` (§ 7.4) |
| MCP startet nicht: `password authentication failed for user "lgx_ro"` | `secrets/pg_ro_pw` nach `install` aus einer Sicherung überschrieben | in der Reihenfolge aus § 8.3 neu aufsetzen: erst die Sicherung zurückspielen, dann `install` |
| G0 19/20, nur G0-01 rot | G0-01 prüft Datum/Release der Stichprobe 2026-10-16 | erwartet bei jedem anderen Datenstand (§ 5.4) |
| UI oder MCP startet nicht: `UiSettingsError … cannot read secret file` / `SettingsError` | Schlüsseldatei für uid 10001 nicht lesbar | `sudo chown 10001:10001 <datei> && sudo chmod 0400 <datei>` (Dev: `secrets/llm_api_key`) |
| `Bind for 127.0.0.1:8501 failed: port is already allocated` | Port belegt (z. B. zweiter Stack) | `ss -ltnp \| grep 8501`; Dev: `LGX_DEV_UI_PORT=8502 LGX_DEV_MCP_PORT=8002 make dev-up`; Airgap: `LGX_LOCAL_UI_PORT` / `LGX_LOCAL_MCP_PORT` in der `.env` |
| `make db-up` scheitert an Port 55432 | `lgx-dev-db` läuft schon (gleicher Port) | `docker stop lgx-dev-db` oder `make db-up LGX_DB_PORT=55433` |
| `db-load: /bundles/… not found` | kein Stichproben-Paket | `make ingest-sample` |
| `db-load` scheitert mit `Permission denied` | Paketdateien nicht für alle lesbar (der Lader läuft ohne Zusatzrechte) | `chmod 0644 data/bundles/*` |
| Dev-Stack zeigt den alten Datenstand | `db-load` lädt nur in eine leere Datenbank | `make dev-down DEV_DOWN_FLAGS=-v && make dev-up` |
| Reranker sehr langsam / `rerank_reason` « too slow » | CPU-Reranking (17–25 s pro Suche auf 4 CPU) | Standard ist aus; sonst mehr Kerne (`LGX_RERANK_THREADS`), oder `make rerank-down` / `install --without-rerank` |
| `rerank_status: unavailable` direkt nach dem Start | TEI lädt noch (1,5–6 min); MCP prüft nach 1 min erneut | warten; `docker compose logs rerank` |
| Vollabzug stoppt: « outside the nightly window » | Fenster 01:00–05:00 Uhr | in der nächsten Nacht denselben Befehl, oder `--ignore-window` |
| Vollabzug stoppt wegen robots.txt | robots.txt hat sich geändert | prüfen, dann `uv run lgx --t $LGX_T robots-ack <sha256>` |
| Image-Build scheitert mit TLS-Fehler | TLS-abfangender Proxy | `LGX_DEV_CA_BUNDLE=/pfad/ca-bundle.crt` (§ 1.5) |
| `install`: « image …: ID …, expected … » | Image-Datei passt nicht zum Manifest | Paket neu kopieren; nie Images aus anderer Quelle nachladen |
| `check`: `NOT reachable: … No route to host` | Mistral-IP/-Port falsch oder Firewall-Ausnahme fehlt | `.env` (`LGX_MISTRAL_IP`, `LGX_MISTRAL_PORT`), dann `… firewall` |
| On-Prem-Mistral mit **interner CA** | die Container vertrauen nur öffentlichen CAs; ein eigenes CA-Bundle ist noch nicht vorgesehen | offen (M5); bis dahin ein öffentlich vertrautes Zertifikat verwenden |
| Status-Tools melden `AS_OF_OUT_OF_RANGE` | Uhr des Rechners außerhalb [T, T + 1 Jahr] | Uhr per NTP korrigieren; neuer Datenstand |

---

## 10. Rechtliches und Grenzen

- **Quelle und Lizenz.** Die Daten stammen von Legilux, © Ministère d'État – Service central de législation (SCL),
  unter **CC BY 4.0**. Jede Antwort trägt Quelle, ELI und Fassungsdatum; die Ressource `legilux://legal/attribution`
  enthält den Hinweis « Source : Legilux (https://legilux.public.lu) – © Ministère d'État du Grand-Duché de
  Luxembourg, Service central de législation – licence CC BY 4.0 … copie locale non officielle arrêtée au <T>. Aucune
  approbation du SCL n'est impliquée. » Lizenztext: `LICENSES/CC-BY-4.0.txt`. Es werden nur CC-BY-Texte geladen, nur
  zur internen Nutzung; Texte unter der « Licence de réutilisation SCL » sind ausgeschlossen, bis SCL und
  Rechtsberatung antworten.
- **« fait seul foi ».** Nur das veröffentlichte Amtsblatt ist maßgeblich: « Le Journal officiel publié fait seul foi et
  produit des effets juridiques » (loi du 23 décembre 2016, art. 5). Diese Kopie und die konsolidierten Fassungen
  haben nur informativen Wert; die Test-UI zeigt das auf jeder Seite. Keine Rechtsberatung.
- **Juristisch noch nicht geprüft** (Stand 17.10.2026):
  - `config/recueil_members.yaml` – 756 Mitgliedsakte der Codes/Recueils, davon 351 auf den Code-Text umgeleitet;
    alle `reviewed: false`;
  - die Testsätze G1–G3 (`eval/gold/`) – vom Entwickler geschrieben bzw. erzeugt, `draft_unvalidated`;
  - die Aliase `config/aliases.yaml` (z. B. « C. trav. », « NCPC ») – `reviewed: false`;
  - die Einbürgerungs-Regel und die DSGVO-Liste (`data/manifest/<T>/gdpr_review.json` listet Grenzfälle).
- **Grenzen.** Nur geltendes Recht (keine historischen Fassungen); kein Mémorial B, kein Gemeinderecht, keine
  EU-Texte; nicht konsolidierte Akte tragen den Warnhinweis « texte non consolidé »; die vier PDF-Codes sind aus PDF
  extrahiert (Seitenangaben, Warnhinweis); die Suche ist auf der Stichprobe gemessen, nicht auf dem Vollbestand.
- **Datenfluss zu Mistral.** Der Chat schickt die Frage und die gefundenen (öffentlichen) Texte an den eingestellten
  Mistral-Endpunkt: mit SaaS verlassen sie das Haus, mit On-Prem nicht. Die Test-UI nennt den Empfänger-Host.

---

## Anhang A: Was am 17.10.2026 getestet wurde

| Befehl / Ablauf | Ergebnis |
|---|---|
| `make ingest-sample` (Caches vorhanden) | ok, 2 min 44 s; Gates 28 pass / 6 skip / 1 warn; Paket 14,7 MB |
| `uv run lgx --t 2026-10-16 ingest --full --dry-run` | ok, 29 s, 0 Abrufe (Zahlen § 3.2) |
| `make dev-up` / `make dev-down`, Screenshots mit Playwright | ok; Reranker und Embeddings aus; Seitenleiste mit Mistral-Standardwerten |
| `make embed-plan` | ok (Zahlen § 2.4) |
| `lgx embed` gegen einen **nicht erreichbaren** Endpunkt mit Schein-Schlüssel | wie erwartet: 0 Vektoren, Exit 1, « re-run »; Cache danach gelöscht |
| Schlüsseldatei für den Dev-Stack (`sudo install -o 10001 …`, Schein-Schlüssel, unerreichbare URL) | UI liest den Schlüssel; eine root-0600-Datei bricht wie beschrieben mit `UiSettingsError` ab |
| `make eval` | ok, ≈ 1,5 min (Zahlen § 5.4) |
| `make airgap-export` (ohne und mit `--with-rerank`, mit und ohne `--no-build`) | ok; 425 MB bzw. 2,8 GB |
| `airgap_import.sh verify` inkl. verändertem Byte und fremder Datei | ok bzw. korrekt abgelehnt |
| `airgap_import.sh install` in ein eigenes Verzeichnis und Projekt (`--home …/home --project legilux-airgaptest`), mit `--no-start`, `--localhost`, `--with-rerank`, `--without-rerank` | ok; Images per `docker load` und ID-Prüfung, Restore, Start, `/readyz` ready; Reranker nach ≤ 1 min `ok` |
| `keys` (Schein-Schlüssel), `firewall`, `check` | ok; Egress-Probe verworfen und gezählt; Mistral-Platzhalter `NOT reachable` (erwartet, Exit 1) |
| `update-data` (simulierter älterer Datenstand) und zweimal `rollback` | ok; ≈ 1 min 10 s bzw. ≈ 40 s |
| UI der Generalprobe über `compose.localhost.yml` (Playwright) | ok |
| `make airgap-import AIRGAP_BUNDLE=… AIRGAP_IMPORT_ARGS="--home … --project … --localhost"`, danach `check` | ok |
| `make e2e E2E_STUB=1` | ok (5 Fragen gegen den Stub, Sekunden) |
| `make egress-check` | erster Lauf scheiterte am Start (der Test-Stub lief mit dem Gesundheitstest des UI-Images); Override korrigiert, zweiter Lauf **PASS** 0 / 0 / 0, 0 fremde Browser-Anfragen |
| Durchgang § 1–§ 8 nach dieser Anleitung in einem frischen Klon (eigene Build-DB, Test-IP `192.0.2.40`, Homes im Scratch-Verzeichnis) | ok; korrigiert danach: Reihenfolge der Wiederherstellung (§ 8.3), `sudo --preserve-env` für `keys` (§ 7.4), `make egress-check LGX_T=…` (§ 6.1), G0-01 bei anderem Datenstand (§ 5.4), erwarteter Exit 1 von `check` in der Generalprobe (§ 6.4) |
| `uv run ruff check .`, `uv run ruff format --check .`, `uv run pytest -q` | ok; 839 Tests bestanden, 14 übersprungen (Live-/Large-Tests), ≈ 2 min |

**Nicht** getestet (in dieser Umgebung nicht möglich oder nicht erlaubt): jeder echte Mistral-Aufruf (kein Schlüssel),
ein Lauf mit Vektoren und bestandener Probe (dafür Vertragstests mit Schein-Endpunkten: `tests/contract/test_mcp_vectors.py`),
der Vollabzug, ein frischer Stichproben-Abruf aus ganz leerem `data/` (getestet nur mit vorhandenem `data/raw`:
Metadaten neu geholt, die 49 Dateien aus dem Cache, 7 min 28 s), der Installer von uv, SSH-Tunnel, ein echter
Airgap-Rechner, Neustart-Verhalten der Firewall-Regeln.

## Anhang B: Wo liegt was?

| Ort | Inhalt |
|---|---|
| `config/embedding.yaml` | Embedding-Anbieter (Standard `none`), Modell, Drossel, Probe-Schwelle |
| `deploy/compose.dev.yml`, `Makefile` | Dev-Stack (Build-Rechner) |
| `deploy/compose.yml`, `deploy/compose.localhost.yml` | Airgap-Stack, lokale Ports für die Testphase |
| `secrets/` (Build-Rechner, gitignored) | `llm_api_key` (10001, 0400), `mistral_api_key` (eigener Benutzer, 0600) |
| `data/bundles/` | Daten-Pakete (`.dump`, `.release.json`, `.sha256`) |
| `data/airgap/` | exportierte Airgap-Verzeichnisse |
| `/opt/legilux/` (Airgap) | `deploy/.env`, `secrets/`, `bundles/` (+ `current`, `previous`), `scripts/`, `models/hf/` (nur Reranker) |
| `docs/dev/DEVELOPMENT.md`, `docs/PLAN_KURZFASSUNG.md`, `docs/PLAN.md` | Entwickler-Details, Plan |
