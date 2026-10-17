# LegiLux Local MCP Clone – Kurzfassung des Plans

> Stand 16.10.2026, Änderungen vom 17.10.2026 eingearbeitet. Der ausführliche Plan steht in [`PLAN.md`](PLAN.md), die Anhänge in [`plan/`](plan/).
> Status: **geprüft.** Zwei Gegenprüfungen (Fakten; Anforderungen, Airgap und Umsetzbarkeit) mit 22 Befunden
> (9 schwer, 13 leicht) sind eingearbeitet. Die DB-Größe ist neu geschätzt: ohne Vektoren ca. 0,9–1,3 GB.

> **Änderungen 17.10.2026 (Entscheidung des Nutzers, verbindlich).** Es wird **keine LLM-Laufzeit mehr mitgeliefert**
> (keine lokalen LLM-Server-Images, keine Profile `cpu`/`gpu24`/`gpu48`, keine `models-<profil>`-Pakete). Das Chat-LLM
> ist **ein externer OpenAI-kompatibler Endpunkt** – standardmäßig dein **Mistral**-Endpunkt (`OPENAI_BASE_URL`,
> `OPENAI_API_KEY_FILE`, `LGX_LLM_MODEL`; mit Internet die Mistral-SaaS-API, im Airgap dieselbe API unter deiner
> On-Prem-Domain). Es gibt **einen Embedding-Anbieter**, `mistral` (Mistral-Embeddings-API, `mistral-embed`, 1024-d),
> gleich für Dokumente auf der Build-Seite und Fragen im Airgap – nur die Basis-URL unterscheidet sich; standardmäßig
> **aus**. Der Durchsatz wird per Konfiguration begrenzt (`config/embedding.yaml`, per `LGX_EMBED_*` überschreibbar):
> **höchstens 5 Anfragen gleichzeitig, danach 20 s Wartezeit pro Anfrage-Slot**, dazu `batch_size`, `timeout_s` und
> Wiederholungen mit `Retry-After`. Statt des Golden-Vector-Abgleichs gibt es **einen Starttest** (Modell-ID und
> Dimension müssen zur Datenversion passen, Kosinus des Testvektors ≥ 0,99); schlägt er fehl oder ist der Endpunkt
> nicht erreichbar, wird mit Begründung nur im Volltext gesucht.

## Was gebaut wird

Eine lokale, per Docker installierbare Kopie des **aktuell geltenden und relevanten** Luxemburger Rechts
aus Legilux. Sie läuft in einer **Airgap-Umgebung**, also ohne Internet. Ein LLM – standardmäßig dein
Mistral-Endpunkt (im Airgap On-Prem) – greift über einen **MCP-Server** darauf zu und beantwortet Rechtsfragen
ausführlich, mit Zitaten auf Artikelebene.

```
 BUILD-SEITE (mit Internet)                       AIRGAP (ohne Internet)
 Legilux SPARQL ─► Auswahl „aktuell“              PostgreSQL 18 + pgvector (fertige DB)
 legilux.public.lu/filestore ─► Download              ▲
 Parser XML/HTML/PDF ─► Artikel                    MCP-Server (5 Tools, nur lesend) ──► Mistral On-Prem
 (optional) Mistral-API ─► Embeddings                 ▲                                 (Frage-Embeddings,
   (≤ 5 parallel, 20 s Pause pro Slot)                │                                  optional)
 Gates + Tests ─► signierte Pakete ──USB/Datei──►  Streamlit-Test-UI (+ optional Open WebUI)
                                                      │ OpenAI-kompatible API
                                                      ▼
                                                   Mistral On-Prem (Chat-LLM, extern; gleiche API wie SaaS)
```

## Was „aktuell“ und „relevant“ heißt

- **Konsolidierte Texte:** pro Werk die Fassung mit `dateApplicability ≤ T < dateEndApplicability`
  (heute 1.213 Werke) plus höchstens die nächste schon veröffentlichte Fassung.
- **Nie konsolidierte, geltende Rechtsakte** (Gesetze, Verordnungen usw.) aus dem Mémorial A: Es wird der
  Originaltext verwendet. Wurde der Akt geändert, kommt der Warnhinweis „texte non consolidé“ mit der
  Liste der Änderungsgesetze dazu.
- **Ob etwas in Kraft ist, wird bei jeder Abfrage aus den Datumsfeldern berechnet.** Die Statusflags von
  Legilux sind unzuverlässig.
- **Nur Metadaten** (Titel, Daten, Beziehungen „ändert“/„hebt auf“): alte Fassungen und aufgehobene Akte.
- **Gar nicht:** Mémorial B (per robots.txt gesperrt), Gemeinderecht, Gesetzesentwürfe,
  Einbürgerungsgesetze (personenbezogene Daten) sowie Scans, wenn XML oder HTML vorhanden ist.
- **Größe:** ca. 24.000 Texte, ca. 300 MB Text, ca. 400.000 Abschnitte. Die Datenbank hat ohne Vektoren
  ca. 0,9–1,3 GB, mit Mistral-Vektoren ca. 2,5–3,1 GB.

## Wichtigste Entscheidungen

| Thema | Entscheidung |
|---|---|
| Quelle | SPARQL-Endpunkt für alle Metadaten; Dateien nur von `legilux.public.lu/filestore` (erlaubt, 1 Anfrage/s) |
| Textformat | Akoma-Ntoso-XML; HTML für Originaltexte, deren XML nicht unter CC-BY steht; PDF nur für die Codes, die es nur als PDF gibt |
| Lizenz | Standardmäßig nur CC-BY-4.0-Texte, nur interne Nutzung; jede Antwort enthält Quelle, ELI und Fassungsdatum; Anfrage an das SCL wegen der „Licence SCL“ |
| Datenbank | PostgreSQL 18 + pgvector 0.8.7 mit französischer Volltextsuche; jede Datenversion wird neu gebaut, nicht migriert |
| Suche | Zitat-Erkennung („Art. L. 121-1 C. trav.“) → Volltext → Reranker (bge-reranker-v2-m3) → mit Vektoren zusätzlich dichte Suche, per RRF zusammengeführt |
| Embeddings | **Ein Anbieter (`mistral`), standardmäßig aus.** Build-Seite: Mistral Embed (`mistral-embed`, 1024-d) über die SaaS-API; Airgap: dieselbe API unter deiner On-Prem-Domain. Durchsatzgrenze per Konfiguration (5 parallel, 20 s Pause pro Slot); Starttest beim MCP-Start; schlägt er fehl, wird automatisch nur mit Volltext gesucht |
| MCP-Server | Python (offizielles SDK), Streamable HTTP + stdio; Tools: `search_legislation`, `get_article`, `get_act`, `resolve_citation`, `about_corpus` |
| Test-UI | **Streamlit** mit jeder OpenAI-kompatiblen API (Standard: dein Mistral-Endpunkt) und Tool-Calling über MCP; zeigt Tool-Aufrufe, Quellen und Warnhinweise; Tab für direkte Suche ohne LLM; Tab für Testfragen |
| LLM | **Keine mitgelieferte LLM-Laufzeit.** Ein externer OpenAI-kompatibler Endpunkt (`OPENAI_BASE_URL`), standardmäßig Mistral; Streamlit und Open WebUI zeigen auf dieselbe URL. Kein GPU nötig |
| Airgap | Internes Docker-Netz, dauerhafte DOCKER-USER-DROP-Regel (einzige Ausnahme: das Netz `enclave` zur IP deines Mistral-Endpunkts) und Netzwerk-Mitschnitt als Release-Gate; keine Telemetrie, keine CDN-Zugriffe |
| Updates | Signierte Pakete (`platform` inkl. Reranker-Modell, `data-<datum>`); Datenwechsel per DB-Umbenennung mit Rollback in ≤ 2 min |
| Entwicklung | Kleine Stichprobe (~150–250 Dateien); der vollständige Download (~7 h) ist der Befehl `lgx ingest --full` |

## Fahrplan

| Meilenstein | Inhalt |
|---|---|
| M0 | Grundgerüst, CI, Lock-Dateien, kleine technische Machbarkeitstests (Spikes) |
| M1 | SPARQL-Metadaten, Auswahlregeln, Stichproben-Ingest, AKN-Parser, DB-Schema, MCP-Server mit Volltextsuche |
| M1b | Streamlit-Test-UI |
| M2 | Alle Textquellen (HTML, PDF-Codes), Reranker, Qualitätsmessung |
| M3 | Embedding-Stufe (Mistral), standardmäßig aus; Code seit 17.10.2026 fertig (mit Fake-Endpunkten getestet), offen ist der Lauf auf der Stichprobe, sobald ein Schlüssel existiert |
| M4 | Evaluation, nächtliche Synchronisierung, Betrieb |
| M5 | Härtung, signierte Pakete, Installation im Airgap (inkl. der Freigabe nur zum Mistral-Endpunkt) |

## Stand M2 (17.10.2026)

M0, M1, M1b und M2 sind auf der Stichprobe fertig (Embeddings aus, kein Vollabzug). Neu in M2: die nur als PDF
vorliegenden Codes (Code de la route, Code de la sécurité sociale, Code communal, Code de l'éducation nationale) werden
mit Seitenangaben und Mitgliedsakten gelesen (Stichproben-Prüfung gegen pdftotext: CSS 99,95 % Ähnlichkeit); doppelte
Artikel in XML-Recueils werden entfernt; Mitgliedsakte ohne eigenen Text (z. B. das AGD vom 23.11.1955) werden auf den
Code-Text umgeleitet und so zitiert: « Art. 12 de l'arrêté grand-ducal du 23 novembre 1955 … (Code de la route, version
consolidée applicable au 12/06/2026) ». Die Volltextsuche gewichtet jetzt nach Seltenheit der Wörter (IDF); der
Reranker (bge-reranker-v2-m3 in TEI) ist optional, weil er auf 4 CPUs 17–25 s pro Suche braucht. Messwerte auf der
Stichprobe (20.529 durchsuchbare Abschnitte): Treffer unter den ersten 10 bei 84 % der 205 Testfragen (mit Reranker auf
einer festen Teilmenge von 40 Fragen gleich gut wie vor den PDF-Daten), Pflicht-Warnhinweise 100 %, Zitat-Auflösung
99,6 %. Offen: Prüfung der Mitgliederliste und der Testfragen durch eine Juristin oder einen Juristen, der Vollabzug
(`lgx ingest --full`, ≈ 25.600 Dateien, 4 Nachtfenster à 4 h; `--dry-run` zeigt den Plan ohne Abruf). Details:
[`../eval/reports/m2-acceptance.md`](../eval/reports/m2-acceptance.md).

## Schnellstart (Entwicklung)

Linux-Rechner mit Docker und [uv](https://docs.astral.sh/uv/); alle Dienste lauschen nur auf `127.0.0.1`.

```sh
uv sync --all-extras          # Python-3.13-Abhängigkeiten aus uv.lock (+ Entwicklungswerkzeuge)
make ingest-sample            # Stichproben-DB + Paket data/bundles/data-2026-10-16-sample.dump (offline aus data/;
                              #   frischer Checkout: make ingest-sample INGEST_FLAGS=  → höflicher Stichproben-Abruf, nie Vollabzug)
make dev-up                   # DB + Wiederherstellung + MCP + Streamlit, wartet bis alles gesund ist
                              #   DEV_PROFILES=rerank: + TEI-Reranker (einmalig `make fetch-models`, 2,2 GB)
open http://127.0.0.1:8501    # Test-UI (Chat, Recherche directe, Évaluation, Corpus); MCP: http://127.0.0.1:8000/mcp
make e2e                      # optional: 5 Testfragen (FR/DE) über den Code-Pfad der UI, JSON in data/reports/e2e/
                              #   E2E_STUB=1: gegen den schlüssellosen OpenAI-kompatiblen Stub scripts/openai_stub.py
make embed-plan               # optional: was `lgx embed` senden würde (Abschnitte, Anfragen, Dauer mit Drossel)
make eval                     # optional: Testsätze G0–G3 ohne Reranker (mit Reranker nur auf einer festen Teilmenge)
make dev-down                 # stoppen (Volumes bleiben); make dev-logs SERVICE=mcp zeigt die Logs eines Dienstes
```

Das Chat-LLM ist ein externer OpenAI-kompatibler Endpunkt: standardmäßig Mistral (`https://api.mistral.ai/v1`, Modell
`mistral-small-latest`; der Schlüssel liegt in der 0600-Datei `secrets/llm_api_key`, `make dev-up` nimmt ihn mit).
Ein anderer Endpunkt (deine On-Prem-Domain, jeder OpenAI-kompatible Server):
`LGX_LLM_BASE_URL=https://…/v1 LGX_LLM_MODEL=<modell> make dev-up`, oder Basis-URL und Modell in der Seitenleiste
eintragen (an eine eingetippte URL wird kein API-Schlüssel gesendet). Embeddings bleiben aus (`provider: none`, nur
Volltextsuche). Ergebnisse und bekannte Probleme des ersten Durchlaufs: [`dev/e2e-2026-10-17.md`](dev/e2e-2026-10-17.md).
Airgap-Prüfung: `make egress-check`.

## Offene Entscheidungen (Vorgabe in Klammern)

1. Historische Fassungen für Fragen zu einem früheren Stichtag (**keine**, später N = 1 für Codes möglich)
2. Details zum Mistral-On-Prem-Endpunkt: Domain und IP, Chat-Modell (Tool-Calling), die gemeldete Embedding-Modell-ID
   (**muss der der SaaS-API entsprechen**), API-Schlüssel, Ratenlimits (**von dir benötigt**)
3. Wann die Embeddings über die Mistral-API berechnet werden (**nachdem der On-Prem-Endpunkt den Starttest besteht**;
   Vollbestand ≈ 11,7 h mit der Standard-Drossel)
4. Hardware (**kein GPU nötig**, das LLM ist extern; 8–16 Kerne, 32 GB RAM)
5. Lizenz (**nur CC-BY, nur interne Nutzung**, bis SCL und Rechtsberatung antworten)
6. Open WebUI zusätzlich zu Streamlit (**optional**)
