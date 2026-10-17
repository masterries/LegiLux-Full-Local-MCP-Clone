# LegiLux Local MCP Clone – Kurzfassung des Plans

> Stand 16.10.2026. Der ausführliche Plan steht in [`PLAN.md`](PLAN.md), die Anhänge in [`plan/`](plan/).
> Status: **geprüft.** Zwei Gegenprüfungen (Fakten; Anforderungen, Airgap und Umsetzbarkeit) mit 22 Befunden
> (9 schwer, 13 leicht) sind eingearbeitet. Die DB-Größe ist neu geschätzt: ohne Vektoren ca. 0,9–1,3 GB.

## Was gebaut wird

Eine lokale, per Docker installierbare Kopie des **aktuell geltenden und relevanten** Luxemburger Rechts
aus Legilux. Sie läuft in einer **Airgap-Umgebung**, also ohne Internet. Ein lokales LLM greift über einen
**MCP-Server** darauf zu und beantwortet Rechtsfragen ausführlich, mit Zitaten auf Artikelebene.

```
 BUILD-SEITE (mit Internet)                       AIRGAP (ohne Internet)
 Legilux SPARQL ─► Auswahl „aktuell“              PostgreSQL 18 + pgvector (fertige DB)
 legilux.public.lu/filestore ─► Download              ▲
 Parser XML/HTML/PDF ─► Artikel                    MCP-Server (5 Tools, nur lesend)
 (optional) Mistral-API ─► Embeddings                 ▲
 Gates + Tests ─► signierte Pakete ──USB/Datei──►  LLM (Ollama / vLLM / Mistral On-Prem)
                                                      ▲
                                                   Streamlit-Test-UI (+ optional Open WebUI)
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
| Suche | Zitat-Erkennung („Art. L. 121-1 C. trav.“) → Volltext → Reranker (bge-reranker-v2-m3) → später zusätzlich Vektoren |
| Embeddings | **Austauschbar, standardmäßig aus.** Build-Seite: Mistral Embed (`mistral-embed-2312`, 1024-d) über die API. Airgap: dein lokaler Mistral-Endpunkt mit Abgleichtest beim Start; schlägt der fehl, wird automatisch nur mit Volltext gesucht |
| MCP-Server | Python (offizielles SDK), Streamable HTTP + stdio; Tools: `search_legislation`, `get_article`, `get_act`, `resolve_citation`, `about_corpus` |
| Test-UI | **Streamlit** mit jeder OpenAI-kompatiblen API und Tool-Calling über MCP; zeigt Tool-Aufrufe, Quellen und Warnhinweise; Tab für direkte Suche ohne LLM; Tab für Testfragen |
| LLM | Profile `cpu` (zuerst testen), `gpu24` (empfohlen), `gpu48+`; Mistral On-Prem lässt sich als OpenAI-kompatible URL einbinden |
| Airgap | Internes Docker-Netz, dauerhafte DOCKER-USER-DROP-Regel und Netzwerk-Mitschnitt als Release-Gate; keine Telemetrie, keine CDN-Zugriffe |
| Updates | Signierte Pakete (`platform`, `models-<profil>`, `data-<datum>`); Datenwechsel per DB-Umbenennung mit Rollback in ≤ 2 min |
| Entwicklung | Kleine Stichprobe (~150–250 Dateien); der vollständige Download (~7 h) ist der Befehl `lgx ingest --full` |

## Fahrplan

| Meilenstein | Inhalt |
|---|---|
| M0 | Grundgerüst, CI, Lock-Dateien, kleine technische Machbarkeitstests (Spikes) |
| M1 | SPARQL-Metadaten, Auswahlregeln, Stichproben-Ingest, AKN-Parser, DB-Schema, MCP-Server mit Volltextsuche |
| M1b | Streamlit-Test-UI |
| M2 | Alle Textquellen (HTML, PDF-Codes), Reranker, Qualitätsmessung |
| M3 | Embedding-Stufe (Mistral), standardmäßig aus |
| M4 | Evaluation, nächtliche Synchronisierung, Betrieb |
| M5 | Härtung, signierte Pakete, Installation im Airgap |

## Schnellstart (Entwicklung)

Linux-Rechner mit Docker und [uv](https://docs.astral.sh/uv/); alle Dienste lauschen nur auf `127.0.0.1`.

```sh
uv sync --all-extras          # Python-3.13-Abhängigkeiten aus uv.lock (+ Entwicklungswerkzeuge)
make ingest-sample            # Stichproben-DB + Paket data/bundles/data-2026-10-16-sample.dump (offline aus data/;
                              #   frischer Checkout: make ingest-sample INGEST_FLAGS=  → höflicher Stichproben-Abruf, nie Vollabzug)
make dev-up                   # DB + Wiederherstellung + MCP + Streamlit + Ollama (cpu), wartet bis alles gesund ist
make ollama-pull              # einmalig: qwen3:4b-instruct-2507-q4_K_M (2,5 GB) ins Ollama-Volume; MODEL=<tag> für ein anderes
open http://127.0.0.1:8501    # Test-UI (Chat, Recherche directe, Évaluation, Corpus); MCP: http://127.0.0.1:8000/mcp
make e2e                      # optional: 5 Testfragen (FR/DE) über den Code-Pfad der UI, JSON in data/reports/e2e/
make dev-down                 # stoppen (Volumes bleiben); make dev-logs SERVICE=mcp zeigt die Logs eines Dienstes
```

Anderer OpenAI-kompatibler Endpunkt (vLLM, Mistral On-Prem …): in der Seitenleiste « Autre endpoint (saisie libre) »
wählen und Basis-URL und Modell eintragen (an eine eingetippte URL wird kein API-Schlüssel gesendet), oder den Stack
mit dem Endpunkt als Voreinstellung starten, die auch den Schlüssel erhält:
`LGX_MISTRAL_ONPREM_URL=https://mistral.example.internal/v1 LGX_MISTRAL_ONPREM_MODEL=<modell> LGX_DEV_LLM_API_KEY=<schlüssel> make dev-up`
(Voreinstellung « Mistral on-prem »), oder als Standard: `LGX_LLM_BASE_URL=https://…/v1 MODEL=<modell> make dev-up`.
Embeddings bleiben aus (`provider: none`, nur Volltextsuche). Auf 4 vCPUs dauert eine Antwort 4–10 Minuten; Ergebnisse,
Zeiten und bekannte Probleme: [`dev/e2e-2026-10-17.md`](dev/e2e-2026-10-17.md). Airgap-Prüfung: `make egress-check`.

## Offene Entscheidungen (Vorgabe in Klammern)

1. Historische Fassungen für Fragen zu einem früheren Stichtag (**keine**, später N = 1 für Codes möglich)
2. Details zum Mistral-On-Prem-Endpunkt: API, **gleiche Modellversion wie die API**, Hardware (**von dir benötigt**)
3. Wann die Embeddings über die Mistral-API berechnet werden (**nachdem der lokale Endpunkt den Abgleichtest besteht**)
4. Hardware (**zuerst CPU testen, `gpu24` empfohlen**)
5. Lizenz (**nur CC-BY, nur interne Nutzung**, bis SCL und Rechtsberatung antworten)
6. Open WebUI zusätzlich zu Streamlit (**optional**)
