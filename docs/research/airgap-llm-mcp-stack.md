# airgap-llm-mcp-stack

## Recommendations
- Build the MCP server in Python on the official SDK, pinned mcp>=2.3,<3. Use MCPServer with transport='streamable-http', stateless_http=True and json_response=True, plus a stdio entry point. It must serve both protocol eras, because Open WebUI 0.12.3 still embeds the v1 client (mcp==1.27.2).
- Expose 5-7 read-only tools: search_legislation (hybrid of French FTS/BM25 and pgvector, merged with RRF, then bge-reranker-v2-m3), get_article, get_act, list_versions, resolve_citation, and optionally get_related. Each returns structuredContent plus a compact text rendering with ELI, article, consolidation date and source_url, and stays within about 2-4k tokens per call.
- Use a custom resource URI scheme (legilux://eli/{+path}[/art/{n}]) rather than https://, since airgapped clients cannot fetch legilux.public.lu. Provide a legal_answer prompt in FR/DE/EN that forces citations.
- Secure the MCP server: internal Docker network only, a Bearer token from a Docker secret, Origin and Host allowlists (TransportSecuritySettings), a read-only Postgres role with statement_timeout, and audit logging of Open WebUI's {{USER_ID}}/{{CHAT_ID}}/{{MODEL_ID}} headers.
- Ship two runtime profiles behind the same OpenAI-compatible API: Ollama 0.40.5 for CPU and 24 GB tiers (set OLLAMA_CONTEXT_LENGTH>=32768 and OLLAMA_NO_CLOUD=1), and vLLM v0.31.0 for 48 GB+ tiers (use the -cu129 tag on hosts with drivers older than CUDA 13). Keep llama.cpp server-cuda-b11681 as a lean fallback.
- Default chat model on a 24 GB GPU: qwen3.8:27b-q4_K_M (Apache-2.0, 18 GB). On CPU: gemma4:26b-a4b QAT or qwen3.6:35b-a3b. On 48 GB: Qwen3.8-27B-FP8 on vLLM. On 80-96 GB: Qwen3.8-27B BF16, gpt-oss-120b or Mistral-Small-4-119B-NVFP4. Decide finally with a 50-100 question Luxembourg-law gold set scored on citation precision and tool-call validity.
- Embeddings: Qwen3-Embedding-0.6B on TEI cpu-1.9.4 (or 89-/hopper-1.9.4 on GPU). Reranker: bge-reranker-v2-m3 on TEI. Pin HF commit revisions, store embedder id/revision/dim in a DB metadata table, and fail fast on mismatch.
- Avoid non-commercial or restricted licences in the default bundle: jina-* (CC-BY-NC), Mistral Medium 3.5 (revenue cap), Qwen3.8-Flash-Next (Qwen Community License), and ParadeDB (AGPL), unless legal signs off.
- Use Open WebUI v0.12.3 as the default UI. Pre-provision the MCP connection via TOOL_SERVER_CONNECTIONS (type mcp, info.id, config.enable=true). Set OFFLINE_MODE=true and WEBUI_SECRET_KEY, and enforce isolation with internal: true networks. Offer LibreChat v0.8.8 (MIT) as the alternative if the Open WebUI branding clause is a problem above 50 users.
- Airgap bundle: pin images by digest, docker save and zstd-compress them, hf download --revision for model weights, use Ollama GGUF plus Modelfile (or the model store), and export the DB with pg_dump -Fd -j8 --compress=zstd, optionally wrapped in a legilux-db:<date> image with initdb restore. Add syft SBOMs and a grype scan online, then sign SHA256SUMS and release.json with cosign or minisign.
- Data updates by sneakernet: ship only the signed DB bundle, restore into legilux_<date>, run the golden-question smoke test through MCP, then switch DATABASE_URL (the server is stateless) and keep the previous DB for rollback. Re-embed the whole corpus online whenever the embedder changes.
- Hardware targets: CPU-only node with 16-32 cores and 64 GB RAM; default node with one 24 GB GPU, 16 cores, 64 GB RAM and 500 GB NVMe; multi-user node with 48 GB (L40S) or 80-96 GB GPUs. Also stage the NVIDIA driver and Container Toolkit packages offline.

## Risks / open questions
- No public French or Luxembourg legal QA benchmark exists, so model ranking is unproven. A project-specific evaluation set with gold ELI/article citations is required before the default model is fixed.
- The Qwen3.8 vLLM tool and reasoning parser names (qwen3_coder / qwen3) are inferred from the Qwen3.6 card; the Qwen3.8 card has no serve command. Verify with vLLM v0.31.0 before release.
- Mistral-Small-4 NVFP4 on Hopper or Ampere (non-Blackwell) and FP8 on Ampere (A100/A6000) may fall back to slower kernels or fail. Verify before buying hardware.
- TEI loading BAAI/bge-m3 offline is unconfirmed: the repo has only pytorch_model.bin and ONNX, no safetensors. TEI support for embeddinggemma-2 is also unconfirmed.
- Copying Ollama's model store between machines is common practice but not documented. The documented offline path is GGUF plus Modelfile and 'ollama create'.
- Open WebUI pins MCP SDK v1 (handshake era). When it moves to the 2026-07-28 stateless protocol, retest. The server should keep supporting both eras for at least the 12-month deprecation window.
- Open WebUI TOOL_SERVER_CONNECTIONS access-control fields and the precedence of ENABLE_PERSISTENT_CONFIG over env after first start need testing. Without that, provisioning may silently keep stale configs.
- Unverified by me: the CUDA 13 driver minimum (believed R580+) for the vLLM default image; the Postgres 18 Docker image PGDATA path change (/var/lib/postgresql/18/docker); pg_dump zstd syntax; and offline grype DB import and cosign offline-verify flags in v3.
- CPU-only latency: prefill of 10-20k tokens of retrieved legal text on CPU may take minutes per answer. Retrieval must stay compact (reranked top-k, article-level chunks).
- Licence review is still needed for Open WebUI (branding clause above 50 users), ParadeDB (AGPL) and any model with revenue caps. The Llama 4 EU restriction could not be confirmed because the model card is gated.
- The 'current data only' policy limits list_versions and as_of queries. Decide whether to keep at least the amending-act metadata so historical questions can be answered with an honest 'not stored'.
- Embedder and corpus coupling: changing the embedding model or revision requires re-embedding online and a full DB re-ship. Lock this down before the first production bundle.

## Verified facts
- Latest MCP specification revision is 2026-07-28 (stateless; initialize handshake and Mcp-Session-Id removed; server/discover added; Roots/Sampling/Logging and HTTP+SSE deprecated).  
  _evidence:_ WebFetch https://modelcontextprotocol.io/specification/latest -> 'based on the TypeScript schema in .../schema/2026-07-28/schema.ts'; https://modelcontextprotocol.io/specification/2026-07-28/changelog -> 'Remove protocol-level sessions and the Mcp-Session-Id header...', 'Make MCP stateless: remove the initialize/notifications/initialized handshake', 'Deprecate the Roots, Sampling, and Logging features (SEP-2577)'
- Standard transports are stdio and Streamable HTTP. Servers MUST validate Origin, SHOULD bind localhost when local, SHOULD authenticate. POSTs must carry MCP-Protocol-Version, Mcp-Method and Mcp-Name headers.  
  _evidence:_ WebFetch https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/streamable-http -> 'Servers MUST validate the Origin header on all incoming connections to prevent DNS rebinding attacks'; 'Every POST request to the MCP endpoint MUST include an MCP-Protocol-Version header'; table 'Mcp-Method | method | All requests; Mcp-Name | ... tools/call, resources/read, prompts/get'
- Official MCP Python SDK mcp 2.3.0 (2026-10-02) supports handshake versions 2024-11-05..2025-11-25 AND modern 2026-07-28. FastMCP class renamed MCPServer; mcp.server.fastmcp removed.  
  _evidence:_ curl https://pypi.org/pypi/mcp/json -> '2.3.0 2026-10-02'; pip download mcp-types==2.3.0, mcp_types/version.py: 'HANDSHAKE_PROTOCOL_VERSIONS = ("2024-11-05","2025-03-26","2025-06-18","2025-11-25")', 'MODERN_PROTOCOL_VERSIONS = ("2026-07-28",)'; mcp/server/__init__.py: 'from .mcpserver import MCPServer'; mcp/server/fastmcp.py: "Removed in mcp 2: FastMCP is now mcp.server.mcpserver.MCPServer"
- Open WebUI 0.12.3 pins the v1 MCP SDK (mcp==1.27.2), i.e. a handshake-era client; MCP server must support 2025-era protocol.  
  _evidence:_ curl https://pypi.org/pypi/open-webui/0.12.3/json requires_dist -> ['chromadb==1.5.9', 'mcp==1.27.2', ...]; docs.openwebui.com/features/extensibility/mcp.md line 267: 'the session.initialize() handshake can exceed its timeout. Raise MCP_INITIALIZE_TIMEOUT (default 10 seconds)'
- Open WebUI native MCP support (since v0.6.31) is Streamable HTTP only, admin-only. Auth options are None/Bearer/Session/OAuth 2.1. Custom headers support {{USER_ID}}, {{CHAT_ID}}, {{MODEL_ID}} templating.  
  _evidence:_ WebFetch https://docs.openwebui.com/features/extensibility/mcp.txt -> 'Open WebUI natively supports MCP ... starting in v0.6.31', 'Native MCP support in Open WebUI is Streamable HTTP only'; curl docs.openwebui.com/features/extensibility/mcp.md lines 199-215 token table
- Open WebUI v0.12.3 reads TOOL_SERVER_CONNECTIONS (JSON) from env. MCP connections are matched by info.id and require config.enable.  
  _evidence:_ curl raw.githubusercontent.com/open-webui/open-webui/v0.12.3/backend/open_webui/config.py line 388: "JSONCodec.loads(os.getenv('TOOL_SERVER_CONNECTIONS', '[]'))"; utils/tools.py line 215: "server_connection.get('type', '') == 'mcp' and (server_connection.get('info') or {}).get('id') == server_id"; line 219: "(mcp_server_connection.get('config') or {}).get('enable')"
- Open WebUI OFFLINE_MODE disables the version check and auto-update of embedding/reranker/whisper models, but does not block outbound connections.  
  _evidence:_ WebFetch https://docs.openwebui.com/tutorials/maintenance/offline-mode.md -> env 'OFFLINE_MODE=true HF_HUB_OFFLINE=1 ...', 'These variables do not block outbound connections'; env.py line 1213-1217 'OFFLINE_MODE = ... if OFFLINE_MODE: ENABLE_VERSION_UPDATE_CHECK = False'
- Open WebUI license since v0.6.6 adds a branding clause (exempt at 50 or fewer users per 30 days); not OSI-approved.  
  _evidence:_ WebFetch https://docs.openwebui.com/license -> 'You may NOT alter, remove, or obscure any "Open WebUI" branding', 'You have 50 or fewer users in a 30-day period;', v0.6.6+ 'is not an OSI-approved "open source" license'
- Pinned image tags and digests exist: ollama/ollama:0.40.5, vllm/vllm-openai:v0.31.0, open-webui:v0.12.3, TEI cpu-1.9.4, llama.cpp server-cuda-b11681, pgvector 0.8.7-pg18-trixie, sglang v0.5.22, registry 3.1.2.  
  _evidence:_ Registry HEAD/Hub API: 'ollama/ollama:0.40.5@sha256:5d3a2f25f982...', 'vllm/vllm-openai:v0.31.0@sha256:c1c9f6fd5c10...', 'ghcr.io/open-webui/open-webui:v0.12.3@sha256:a537bbe1e6b4...', 'ghcr.io/huggingface/text-embeddings-inference:cpu-1.9.4@sha256:2538ea1c9640...', 'ghcr.io/ggml-org/llama.cpp:server-cuda-b11681@sha256:53bec65a6120...', 'pgvector/pgvector:0.8.7-pg18-trixie sha256:9d9c930220cb...', 'lmsysorg/sglang:v0.5.22@sha256:e3a4f06001a2...', 'library/registry:3.1.2 sha256:ddf754342cfc...'
- Compressed amd64 image sizes: vLLM v0.31.0 9.04 GB, vLLM CPU 1.57 GB, Ollama 0.40.5 3.79 GB (ROCm 1.45 GB), Open WebUI v0.12.3 1.62 GB (cuda 5.53 GB), TEI cpu-1.9.4 0.24 GB (GPU 3.14-3.15 GB), llama.cpp server-cuda 2.59 GB (CPU 0.31 GB), SGLang v0.5.22 15.62 GB, pgvector 0.17 GB, ParadeDB 0.37 GB.  
  _evidence:_ Docker Hub tags API e.g. 'v0.31.0 2026-10-04 amd64=9.04GB'; GHCR manifest layer sums 'ghcr.io/open-webui/open-webui:v0.12.3: amd64 compressed 1.62 GB, 19 layers', 'text-embeddings-inference:cpu-1.9.4: amd64 compressed 0.24 GB'
- vLLM v0.31.0 default image is built on CUDA 13.0.2 with TORCH_CUDA_ARCH_LIST 7.5-12.0. A -cu129 variant exists. Entrypoint is 'vllm serve'.  
  _evidence:_ Docker registry config blob for vllm/vllm-openai:v0.31.0 amd64: 'CUDA_VERSION=13.0.2', 'NVIDIA_REQUIRE_CUDA=cuda>=13.0 ...', 'TORCH_CUDA_ARCH_LIST=7.5 8.0 8.6 8.9 9.0 10.0 12.0', entrypoint ['vllm','serve']; Hub tag 'v0.31.0-cu129 2026-10-04'
- Text Generation Inference is in maintenance mode; HF recommends vLLM/SGLang. Latest TGI tag is 3.3.7.  
  _evidence:_ WebSearch -> TGI docs: 'text-generation-inference is now in maintenance mode... recommends vllm and SGLang'; GHCR tags huggingface/text-generation-inference -> ['3.3.3','3.3.4','3.3.6','3.3.7']
- TEI 1.9 supports Qwen3-Embedding (0.6B/4B/8B), multilingual-e5-large-instruct, arctic-embed-l-v2.0 and XLM-RoBERTa rerankers. Qwen3-Reranker is not listed. Images are cpu-1.9 / 1.9 / 86-1.9 / 89-1.9 / hopper-1.9 / 100-1.9 / 120-1.9.  
  _evidence:_ WebFetch https://huggingface.co/docs/text-embeddings-inference/supported_models -> table '4 | 509M | Qwen3 | Qwen/Qwen3-Embedding-0.6B', 'Re-Ranking | XLM-RoBERTa | BAAI/bge-reranker-large', image table 'CPU x86_64 ghcr.io/huggingface/text-embeddings-inference:cpu-1.9'
- Ollama default context window is 4096 tokens. Cloud features are disabled via OLLAMA_NO_CLOUD=1. Models are stored in OLLAMA_MODELS. GGUF is imported via Modelfile FROM + ollama create, with no quantization on import.  
  _evidence:_ WebFetch https://docs.ollama.com/faq -> 'By default, Ollama uses a context window size of 4096 tokens', 'disable_ollama_cloud ... or by setting OLLAMA_NO_CLOUD=1'; https://docs.ollama.com/import -> 'FROM /path/to/file.gguf', 'Ollama does not quantize GGUF models during import'
- Ollama library tags and sizes: qwen3.8:27b-q4_K_M 18GB (q8 30GB, bf16 56GB); qwen3.6:35b-a3b-q4_K_M 24GB; gemma4:26b-a4b 16GB, 31b 19GB, 12b 7.7GB; ministral-3 3b/8b/14b 3.0/6.0/9.1GB; gpt-oss 20b 14GB, 120b 65GB; bge-m3 1.2GB; qwen3-embedding:0.6b 639MB.  
  _evidence:_ curl https://ollama.com/library/<model>/tags parsed: "qwen3.8: 27b-q4_K_M ('18GB','256K')", "gemma4: 26b-a4b ('16GB','256K')", "ministral-3: 14b ('9.1GB','256K')", "gpt-oss: 120b ('65GB','128K')"
- Qwen3.8-27B (2026-08-05) is Apache-2.0, 27.8B params, 262144 native ctx, a hybrid of 48 Gated DeltaNet and 16 full-attention layers with 4 KV heads x 256 head_dim (about 64 KiB/token KV at BF16, computed).  
  _evidence:_ HF API /api/models/Qwen/Qwen3.8-27B -> 'license=apache-2.0 | params=27.8B | ctx=262144'; config.json text_config -> {'num_hidden_layers': 64, 'num_key_value_heads': 4, 'head_dim': 256, 'layer_types_counts': {'linear_attention': 48, 'full_attention': 16}}
- Mistral Small 4 (mistralai/Mistral-Small-4-119B-2603) is Apache-2.0, 119B MoE with 6.5B active, 256k ctx, recommended on vLLM with mistral tool/reasoning parsers. The NVFP4 checkpoint is 70.8 GB.  
  _evidence:_ HF README: '119B parameters, with 6.5B activated per token', '256k context length', 'vllm serve mistralai/Mistral-Small-4-119B-2603 ... --tool-call-parser mistral --enable-auto-tool-choice --reasoning-parser mistral'; HF tree of -NVFP4 'total 70.8 GB'
- Mistral Medium 3.5 128B uses a Modified MIT license that excludes companies with monthly revenue over $20M.  
  _evidence:_ curl https://huggingface.co/mistralai/Mistral-Medium-3.5-128B/resolve/main/LICENSE -> 'You are not authorized to exercise any rights under this license if the global consolidated monthly revenue of your company ... exceeds $20 million'
- Gemma 4 (E2B/E4B/12B/26B-A4B/31B) is Apache-2.0. 26B-A4B has 25.2B total / 3.8B active. Context is 256K (128K for small models). Native function calling. Pretrained on 140+ languages.  
  _evidence:_ HF API google/gemma-4-26B-A4B-it 'license=apache-2.0'; README: '| **Active Parameters** | 3.8B |', 'Function Calling – Native support for structured tool use', 'Out-of-the-box support for 35+ languages, pre-trained on 140+ languages'
- Qwen3.6-35B-A3B is Apache-2.0, 35B total / 3B active, and recommends vLLM >=0.19.0 with '--reasoning-parser qwen3 --enable-auto-tool-choice --tool-call-parser qwen3_coder'. The FP8 checkpoint is 37.5 GB.  
  _evidence:_ HF README: 'Number of Parameters: 35B in total and 3B activated', 'vllm>=0.19.0 is recommended', 'vllm serve Qwen/Qwen3.6-35B-A3B ... --enable-auto-tool-choice --tool-call-parser qwen3_coder'; HF tree Qwen3.6-35B-A3B-FP8 'safetensors=37.5GB'
- gpt-oss-120b and gpt-oss-20b are Apache-2.0 with 131072 ctx; weights are 65.2 GB and 13.8 GB.  
  _evidence:_ HF API openai/gpt-oss-120b 'license=apache-2.0 | params=116.8B | ctx=131072'; tree 'safetensors=65.2GB'; gpt-oss-20b 'safetensors=13.8GB'
- Qwen3.8-Flash-Next uses the Qwen Community License 1.0 (Model-as-a-Service and AI Work Assistant restrictions; display obligation above 100M MAU or $20M monthly revenue). It has 125B params with 6B activated.  
  _evidence:_ curl https://huggingface.co/Qwen/Qwen3.8-Flash-Next/resolve/main/LICENSE -> 'If the licensee ... conducts a Model as a Service or AI Work Assistant business, the licensee shall obtain a separate license'; README 'Number of Parameters: 125B with 6B activated'
- Embedding/reranker licenses and specs: Qwen3-Embedding-0.6B Apache-2.0, 32k ctx, up to 1024 dims with MRL, 100+ languages. bge-m3 MIT, 8192 ctx, dense+sparse+multi-vector. bge-reranker-v2-m3 Apache-2.0. jina-embeddings-v3/v5 and jina-reranker-v3.5 are cc-by-nc-4.0. embeddinggemma-2 is Apache-2.0, 768-d, 8K ctx.  
  _evidence:_ HF API: 'Qwen/Qwen3-Embedding-0.6B | license=apache-2.0 | ctx=32768', 'BAAI/bge-m3 | license=mit', 'jinaai/jina-embeddings-v3 | license=cc-by-nc-4.0', 'jinaai/jina-reranker-v3.5 | license=cc-by-nc-4.0'; Qwen card 'Embedding Dimension: Up to 1024, supports user-defined output dimensions ranging from 32 to 1024'; embeddinggemma-2 card 'single, unified 768-dimensional vector space', 'Context Window | 8,192 tokens'
- Infinity embedding server is stale: last release 0.0.77 on 2025-08-22.  
  _evidence:_ Docker Hub michaelf34/infinity tags -> '0.0.77 2025-08-22'; PyPI infinity-emb '0.0.77 2025-08-22'
- llama.cpp server (master docs) supports function calling via --jinja (default on), --embedding, --rerank, --api-key, and a built-in stdio-only MCP client via --mcp-servers-config.  
  _evidence:_ curl raw.githubusercontent.com/ggml-org/llama.cpp/master/tools/server/README.md -> '| --jinja, --no-jinja | ... (default: enabled)', '| --rerank, --reranking | enable reranking endpoint', 'Only the stdio transport is supported', 'llama-server -m model.gguf --mcp-servers-config mcp.json'
- FastMCP 4.1.0 (Apache-2.0, 2026-10-08) serves both protocol eras and offers JWTVerifier with HS256 shared secrets. StaticTokenVerifier is dev-only.  
  _evidence:_ PyPI fastmcp '4.1.0 2026-10-08 license=Apache-2.0'; gofastmcp.com/getting-started/whats-new.md 'FastMCP 4 serves both eras from one deployment'; token-verification.md 'Static token verification stores tokens as plain text and should never be used in production'
- HF_HUB_OFFLINE=1 makes huggingface_hub perform no HTTP calls. Models are downloaded with 'hf download <repo> --revision <rev> --local-dir <path>'.  
  _evidence:_ WebFetch huggingface.co/docs/huggingface_hub/package_reference/environment_variables -> 'HF_HUB_OFFLINE: If set, no HTTP calls will be made to the Hugging Face Hub'; CLI guide 'hf download <repo_id> --local-dir <path>', 'hf download <repo_id> --revision <commit_hash_or_branch_or_tag>'
- vLLM usage stats are disabled with VLLM_NO_USAGE_STATS or DO_NOT_TRACK, or the file ~/.config/vllm/do_not_track.  
  _evidence:_ WebFetch https://docs.vllm.ai/en/latest/usage/usage_stats.html -> 'setting the VLLM_NO_USAGE_STATS or DO_NOT_TRACK environment variable'
- LibreChat v0.8.8 (Docker Hub librechat/librechat, 2026-10-01) supports MCP via stdio, SSE and streamable-http, configured in librechat.yaml mcpServers.  
  _evidence:_ Docker Hub librechat/librechat tags -> ('v0.8.8','2026-10-01'); WebFetch https://www.librechat.ai/docs/features/mcp -> 'mcpServers: clickhouse-cloud: type: streamable-http'
- AnythingLLM 1.18.0 image exists. MCP is configured via anythingllm_mcp_servers.json with stdio/sse/streamable transports, for agents.  
  _evidence:_ Docker Hub mintplexlabs/anythingllm '1.18.0 2026-10-13' digest sha256:5461782e7db4...; WebFetch docs.anythingllm.com/mcp-compatibility/overview -> 'The stdio transport type is the default', 'both sse and streamable require the url field'
- ParadeDB Community (pg_search BM25) is AGPL-3.0. Latest image is 0.26.2-pg18.  
  _evidence:_ Docker Hub API paradedb/paradedb full_description: 'ParadeDB Community is licensed under the GNU Affero General Public License v3.0'; tag '0.26.2-pg18 2026-10-16'
- Legilux ELI URI shapes for 2026 acts (for tool and citation design).  
  _evidence:_ GET https://data.legilux.public.lu/sparqlendpoint (Accept: application/sparql-results+json) with query 'SELECT ?act ?date ?title WHERE { ?act a jolux:Act ; jolux:dateDocument ?date ; jolux:isRealizedBy ?expr . ?expr jolux:title ?title . FILTER(?date >= "2026-06-01"^^xsd:date && STRSTARTS(STR(?act), "http://data.legilux.public.lu/eli/etat/leg/loi/")) } LIMIT 3' -> 'http://data.legilux.public.lu/eli/etat/leg/loi/2026/07/06/a350/jo 2026-07-06 Loi du 6 juillet 2026 concernant l'aide financière de l'État pour études supérieures.'
- Supporting tool versions: syft v1.54.1, grype v0.120.1, cosign v3.1.3, caddy 2.11.7-alpine, nginx 1.31.7-alpine, python 3.13.16-slim-trixie, MCP Inspector 2.11.0, TS SDK 1.32.1.  
  _evidence:_ Docker Hub tag listings ('v1.54.1 2026-10-06', 'v0.120.1 2026-10-06', '2.11.7-alpine 2026-10-06'); GHCR sigstore/cosign/cosign tags ['v3.1.2','v3.1.3']; npm registry '@modelcontextprotocol/inspector 2.11.0 2026-10-14', '@modelcontextprotocol/sdk 1.32.1'

## Report

# Airgapped LLM + MCP serving stack for the Legilux clone (status as of 2026-10-16)

All checks were run live from this sandbox on 2026-10-16 against Docker Hub, GHCR, PyPI, npm, the Hugging Face API, the MCP spec site and vendor docs. Anything marked **VERIFIED** has its evidence in `verified_facts`. Anything marked **UNVERIFIED** comes from memory or inference and needs a check before you rely on it. Scratch files are in `<research-scratch>/airgap-llm-mcp-stack/`.

---

## 1. Recommended default stack (one `docker compose` project with profiles)

| Layer | Default choice | Pinned image (VERIFIED tag + digest) | Notes |
|---|---|---|---|
| Database | PostgreSQL 18 + pgvector | `pgvector/pgvector:0.8.7-pg18-trixie@sha256:9d9c930220cb9bf2f956d10a8f909cf9973d9672ca0278aef2c1e6facccad2e0` (0.17 GB compressed) | Uses built-in French FTS (`french` config + `unaccent`) for lexical search and pgvector for dense search. ParadeDB `paradedb/paradedb:0.26.2-pg18@sha256:c552094b…` is the BM25 alternative, but it is **AGPL-3.0** (VERIFIED from its Docker Hub description). |
| MCP server | Custom Python server on the official SDK `mcp==2.3.0` (`MCPServer`), Streamable HTTP at `/mcp`, with stdio as a second entry point | `legilux-mcp:<date>`, built FROM `python:3.13.16-slim-trixie` | Must serve **both** protocol eras (see §2). |
| Embeddings | `Qwen/Qwen3-Embedding-0.6B` (Apache-2.0, 1024-d with MRL, 32k ctx) | `ghcr.io/huggingface/text-embeddings-inference:cpu-1.9.4@sha256:2538ea1c9640d3763b15af668039d24172d063b42337b0c27796fc2be180c78d` (0.24 GB). GPU: `:89-1.9.4@sha256:1a284d9c…` (Ada), `:1.9.4` (A100), `:hopper-1.9.4` (~3.1 GB each) | The same model **and HF revision** must be used offline at build time and at query time. |
| Reranker | `BAAI/bge-reranker-v2-m3` (Apache-2.0, 0.6B) | Same TEI image, second container | XLM-RoBERTa sequence-classification, which TEI supports. |
| LLM runtime, small/CPU/24 GB tiers | Ollama | `ollama/ollama:0.40.5@sha256:5d3a2f25f9825628daba94dc0b1d421c7d57d6c03ae5faa9ba050e058bab8241` (3.79 GB amd64, includes CUDA; `-rocm` variant 1.45 GB) | Set `OLLAMA_NO_CLOUD=1` and `OLLAMA_CONTEXT_LENGTH≥32768`, because the **default context is 4096** (VERIFIED). |
| LLM runtime, ≥48 GB GPU tiers | vLLM | `vllm/vllm-openai:v0.31.0@sha256:c1c9f6fd5c109ba7f0546a59f5b2f15fb87f64c77782e90a27b648b42a8e67c3` (9.04 GB). It is built on **CUDA 13.0.2**. Use `v0.31.0-cu129` on hosts with older drivers. CPU image: `vllm/vllm-openai-cpu:v0.31.0` | `VLLM_NO_USAGE_STATS=1` and `HF_HUB_OFFLINE=1`. Serve from a local path. |
| Default chat model (24 GB GPU) | `Qwen3.8-27B` (Apache-2.0, 256K ctx), served as Ollama `qwen3.8:27b-q4_K_M` (18 GB) | – | Alternatives are in §4. Choose with your own Luxembourg-law evaluation set. |
| Chat UI / MCP client | Open WebUI | `ghcr.io/open-webui/open-webui:v0.12.3@sha256:a537bbe1e6b428820f52ed4a029d7fff97062e24d5cf32c365fba36fd0542def` (1.62 GB; `-cuda` is 5.53 GB and not needed) | Has native MCP over Streamable HTTP. Set `OFFLINE_MODE=true`. Its licence has a branding clause above 50 users. |
| TLS / reverse proxy | Caddy with `tls internal` | `caddy:2.11.7-alpine` (or `nginx:1.31.7-alpine`) | Only the UI is published. Everything else stays on an `internal: true` Docker network. |
| Optional local registry | Distribution | `registry:3.1.2@sha256:ddf754342cfc8acc51a56d5d0ab6af06826461864460636d8bd5c546dab2a7b8` | Only needed for multi-host or Kubernetes setups. |
| Supply chain | syft / grype / cosign | `anchore/syft:v1.54.1`, `anchore/grype:v0.120.1`, `ghcr.io/sigstore/cosign/cosign:v3.1.3` | Generate the SBOM and run the vulnerability scan on the online build host, and sign the bundle there. |

---

## 2. MCP layer

### Spec status (VERIFIED)
- `modelcontextprotocol.io/specification/latest` now points at **2026-07-28**. The changelog lists what changed since 2025-11-25:
  - MCP is now **stateless**: the `initialize` handshake and the `Mcp-Session-Id` header are gone. Each request carries `_meta.io.modelcontextprotocol/protocolVersion` and the client capabilities.
  - Servers must implement a new `server/discover` method.
  - `subscriptions/listen` replaces the GET stream.
  - Server-initiated requests are replaced by Multi Round-Trip Requests (MRTR, `InputRequiredResult`).
  - Tasks moved into an extension.
  - Results gain `ttlMs`/`cacheScope` caching fields.
  - Roots, Sampling and Logging are **deprecated**, and so is HTTP+SSE.
- **Transports:** stdio and Streamable HTTP. Every request is a POST to a single endpoint. POSTs **must** carry the `MCP-Protocol-Version`, `Mcp-Method` and `Mcp-Name` headers.
- **Security rules quoted from the spec:**
  - "Servers MUST validate the Origin header on all incoming connections to prevent DNS rebinding attacks"
  - "When running locally, servers SHOULD bind only to localhost"
  - "Servers SHOULD implement proper authentication"

### Client and SDK compatibility — the key finding (VERIFIED)
- **Open WebUI 0.12.3 pins `mcp==1.27.2`.** That is the v1 SDK, and it uses the handshake protocol. Open WebUI's docs also mention a `session.initialize()` timeout (`MCP_INITIALIZE_TIMEOUT`, default 10 s). **Our server therefore must speak the 2025 handshake era as well as 2026-07-28.**
- **`mcp` 2.3.0** (PyPI, released 2026-10-02; 2.0.0 shipped 2026-07-28) does exactly that. Inspecting the `mcp_types` wheel shows:
  - `HANDSHAKE_PROTOCOL_VERSIONS = ("2024-11-05","2025-03-26","2025-06-18","2025-11-25")`
  - `MODERN_PROTOCOL_VERSIONS = ("2026-07-28",)`

  The high-level class is now `MCPServer`. `from mcp.server import MCPServer` and `mcp.server.mcpserver.MCPServer` both work. `mcp.server.fastmcp` is removed and raises an error that points to the migration guide.
- **Running it:** host and port moved from the constructor to `run()`:
  ```python
  mcp.run(transport="streamable-http", host="0.0.0.0", port=8000, stateless_http=True, json_response=True)
  ```
  DNS-rebinding protection switches on automatically only when the host is 127.0.0.1. Inside Docker, pass `TransportSecuritySettings(allowed_hosts=["legilux-mcp:8000"], allowed_origins=[...])` explicitly.
- **Other SDKs:**
  - FastMCP 4.1.0 (Apache-2.0, 2026-10-08) also "serves both eras from one deployment". It adds `JWTVerifier` (HS256 shared secret, no IdP needed). Its `StaticTokenVerifier` is documented as "dev only".
  - TypeScript SDK `@modelcontextprotocol/sdk` 1.32.1. I did not check its 2026-07-28 support.
  - `@modelcontextprotocol/inspector` 2.11.0 is useful for testing.
- **Recommendation:** use official `mcp>=2.3,<3`, `stateless_http=True` and `json_response=True`. The server is read-only and needs no server-to-client channel. Add a stdio entry point (`python -m legilux_mcp --stdio`) for desktop clients and for llama.cpp's built-in MCP support.

### Tool design for legal retrieval
Keep the tool set small (5–7 tools), in a deterministic order (the spec says SHOULD), named in snake_case. Each tool gets an `outputSchema`, and returns `structuredContent` plus a compact text rendering; the spec says a structured result SHOULD also be serialized in a TextContent block. Mark every tool read-only with `readOnlyHint: true` and `openWorldHint: false`. (These annotation names come from the 2025 spec; UNVERIFIED that 2026 is unchanged.)

1. **`search_legislation(query, lang="fr", filters{act_type, in_force_only=true, date_from, date_to, eli_prefix}, top_k=8, cursor)`**
   - Hybrid search: French FTS/BM25 plus pgvector, merged with RRF, then reranked by bge-reranker-v2-m3.
   - Returns hits with `eli`, `article`, `title`, `snippet` (≤600 chars), `version_date`, `in_force`, `score` and `source_url`.
   - The description tells the model to write queries in French. Luxembourg legislation is in French, and cross-lingual embeddings help questions asked in German or English.
2. **`get_article(eli, article, as_of=null)`** returns the full text of the current consolidated article, plus a citation string, validity dates and `source_url`.
3. **`get_act(eli, include_toc=true, include_text=false)`** returns metadata: type, date, Mémorial A reference, in-force status, amending and amended acts, consolidation date, and the table of contents.
4. **`list_versions(eli)`** returns the consolidation dates that are stored. Under the "current only" policy that is the current version plus metadata on amending acts.
5. **`resolve_citation(text)`** turns strings like «art. 5 de la loi modifiée du 10 août 1915 concernant les sociétés commerciales» or «règlement grand-ducal du …» into ELI and article.
6. *(optional)* **`get_related(eli, relation=cites|cited_by|transposes|amends)`**.

Real ELI shapes returned by a live SPARQL sample:
- `http://data.legilux.public.lu/eli/etat/leg/loi/2026/07/06/a350/jo` («Loi du 6 juillet 2026 concernant l'aide financière de l'État pour études supérieures.»)
- `…/eli/etat/adm/amin/2026/01/05/b1799/jo`

Other design rules:
- **Resource URIs.** Expose resources and resource templates under a custom scheme such as `legilux://eli/{+path}` and `legilux://eli/{+path}/art/{n}`. The spec says `https://` should only be used when the client can fetch the resource directly, which an airgapped client cannot. Keep `https://legilux.public.lu/eli/...` only as display and citation text.
- **Answer prompt.** Add a `prompts/` entry, `legal_answer`, in FR, DE and EN. It should require: cite ELI, article and consolidation date for every claim; say when nothing relevant was found; add a "not legal advice" notice.
- **Result size.** Cap each tool result at roughly 2–4k tokens and paginate with cursors. Use `isError: true` with an actionable message for bad ELIs.
- **Database access.** Use a dedicated read-only Postgres role with `default_transaction_read_only=on` and a `statement_timeout`, and rate-limit tool calls.

### Auth on the local network
- Open WebUI supports None, Bearer, Session and OAuth 2.1 for MCP connections. Use **Bearer** with a random token from a Docker secret.
- Open WebUI can send audit headers using template tokens such as `{{USER_ID}}`, `{{USER_ROLE}}`, `{{CHAT_ID}}` and `{{MODEL_ID}}` (VERIFIED). Log these in the MCP server.
- Keep the MCP server on the internal network only and validate `Origin`.
- OAuth is not worth the complexity in an airgap.
- Connections can be pre-provisioned through the `TOOL_SERVER_CONNECTIONS` env JSON (VERIFIED in `config.py`). An MCP entry is matched by `info.id` and is active only if `config.enable` is true (VERIFIED in `utils/tools.py`). Example:
  ```json
  [{"type":"mcp","url":"http://legilux-mcp:8000/mcp","path":"","auth_type":"bearer","key":"<token>","config":{"enable":true},"info":{"id":"legilux","name":"Legilux"}}]
  ```
  Access-control fields are UNVERIFIED. Note that once the database holds config, `ENABLE_PERSISTENT_CONFIG` (default True) makes the stored config take precedence over env.

---

## 3. Local inference servers compared

| Server | Version (VERIFIED) | Hardware | OpenAI API / tools | Offline model handling | Verdict |
|---|---|---|---|---|---|
| **vLLM** | v0.31.0 (2026-10-04). Images `vllm-openai` (CUDA 13.0.2, `TORCH_CUDA_ARCH_LIST=7.5…12.0`), `-cu129`, `vllm-openai-rocm`, `vllm-openai-cpu` (1.57 GB) | NVIDIA, AMD, CPU | Yes. Needs `--enable-auto-tool-choice --tool-call-parser …`. Parsers include `mistral`, `openai` (gpt-oss), `granite4`, `functiongemma`, `apertus`. The Qwen3.6 card uses `--tool-call-parser qwen3_coder --reasoning-parser qwen3`. Also serves `/v1/embeddings` and `/rerank` for pooling models. | Mount the `hf download` dir and serve the local path. `HF_HUB_OFFLINE=1`. Opt out of telemetry with `VLLM_NO_USAGE_STATS` / `DO_NOT_TRACK`. | Best for ≥48 GB multi-user. |
| **Ollama** | 0.40.5 (2026-10-15) | NVIDIA (bundled), ROCm tag, CPU, Apple | `/api/chat` tools with streaming and parallel calls. `/v1` OpenAI-compatible (well known). Also embeddings. | `OLLAMA_MODELS` dir. GGUF import via a `Modelfile` with `FROM /path/model.gguf` then `ollama create`. Ollama "does not quantize GGUF models during import". | Simplest for CPU and 24 GB. |
| **llama.cpp server** | build b11681. `ghcr.io/ggml-org/llama.cpp:server-cuda-b11681` (2.59 GB), `server-b11681` CPU (0.31 GB), plus `-vulkan`, `-rocm`, `-intel` | Everything | `--jinja` is on by default for tool calling. `--embedding`, `--rerank`, `--api-key`. Master docs now describe **built-in MCP client (stdio only)** via `--mcp-servers-config` (I did not confirm it is in b11681). | Plain GGUF files. | Leanest. A good alternative to Ollama. |
| **SGLang** | v0.5.22 (15.62 GB image) | NVIDIA, AMD, Ascend | Yes (`--tool-call-parser`) | Same as vLLM | Large image. Only needed for very high throughput. |
| **TGI** | 3.3.7 | – | – | – | **Maintenance mode**. HF docs recommend vLLM or SGLang. Do not use. |

---

## 4. Chat models (all VERIFIED on the HF API: license, parameters, context; sizes from Ollama or HF)

| Tier | Model | License | Params / active | Ctx | Size to ship | Why |
|---|---|---|---|---|---|---|
| CPU / small | `gemma4:26b-a4b-it-qat` (google/gemma-4-26B-A4B-it) | Apache-2.0 | 25.2B / 3.8B | 256K | 16 GB (Ollama) | MoE with small active params runs acceptably on CPU. 140+ pretraining languages, native function calling. |
| CPU / small | `qwen3.6:35b-a3b-q4_K_M` | Apache-2.0 | 36B / 3B | 256K | 24 GB | Strong agentic and tool use. Card recommends ≥128K ctx for thinking. |
| CPU / tiny | `ministral-3:8b` / `:14b` / `:3b` | Apache-2.0 | 8.9B / 13.9B / 3.8B dense | 256K | 6.0 / 9.1 / 3.0 GB | French-native vendor, explicit `fr`. |
| **24 GB GPU (default)** | `qwen3.8:27b-q4_K_M` (Qwen/Qwen3.8-27B, 2026-08-05) | Apache-2.0 | 27.8B dense hybrid (48 Gated-DeltaNet + 16 attention layers) | 256K | 18 GB | Most recent mid-size Apache model. KV cache only ≈64 KiB/token (computed), so 32K ctx ≈ 2 GiB. |
| 24 GB alternatives | `gemma4:31b-it-qat` (19 GB), `mistralai/Mistral-Small-3.2-24B-Instruct-2506` (Apache, 131K ctx) | Apache-2.0 | – | – | – | Worth benchmarking for French legal style. |
| 48 GB | `Qwen/Qwen3.8-27B-FP8` (30.9 GB) or `Qwen/Qwen3.6-35B-A3B-FP8` (37.5 GB) on vLLM | Apache-2.0 | – | 256K | – | FP8 needs Ada or Hopper (sm89+). |
| 80–96 GB | `Qwen3.8-27B` BF16 (55.6 GB) · `openai/gpt-oss-120b` (65.2 GB MXFP4, 117B/≈5B active, 131K) · `mistralai/Mistral-Small-4-119B-2603-NVFP4` (70.8 GB; 119B / **6.5B active**, 256K ctx, `--tool-call-parser mistral --reasoning-parser mistral`) | Apache-2.0 (all three) | – | – | – | Mistral Small 4 is the strongest French-vendor candidate. NVFP4 natively needs Blackwell (UNVERIFIED on Hopper). |
| Avoid or caution | `Mistral-Medium-3.5-128B` | **Modified MIT: no rights if monthly revenue > $20M** | – | – | – | – |
| Avoid or caution | `Qwen3.8-Flash-Next` (125B / 6B active) | Qwen Community License 1.0: MaaS restrictions, display duties above $20M per month | – | – | – | – |
| Avoid or caution | `jina-*` models | CC-BY-NC | – | – | – | Non-commercial. |
| Avoid or caution | Llama 4 | Llama licence with EU restriction on multimodal models (UNVERIFIED, page is gated); also stale | – | – | – | – |
| Also open | EuroLLM-22B-Instruct-2512, Apertus-v1.5-8B/70B, granite-4.2-8b/30b | Apache-2.0 | – | EuroLLM is only 32K ctx | – | Apertus is gated (`auto`). |

There is no public benchmark for French or Luxembourg legal QA; a search found only general and Swiss studies. **Build a 50–100 question gold set with ELI and article citations and run each candidate through the real MCP tools.** Score citation precision, tool-call validity and faithfulness.

---

## 5. Embeddings and rerankers

| Model | License | Dim | Ctx | Serving | Notes |
|---|---|---|---|---|---|
| **Qwen/Qwen3-Embedding-0.6B** (default) | Apache-2.0 | ≤1024 (MRL, 32–1024) | 32k | TEI ✔ (listed, MTEB rank 4 in the TEI table), Ollama `qwen3-embedding:0.6b` 639 MB | Instruction-aware; write instructions in English. |
| Qwen3-Embedding-4B / 8B | Apache-2.0 | 2560 / 4096 | 40k | TEI ✔ | Better quality on the 48 GB+ tiers. |
| BAAI/bge-m3 | MIT | 1024 | 8192 | TEI (XLM-R); Ollama `bge-m3` 1.2 GB | Dense + sparse + ColBERT. The repo has only `pytorch_model.bin` and ONNX, no safetensors, so test TEI loading offline. |
| google/embeddinggemma-2 (2026-09-14) | Apache-2.0 | 768 (MRL to 512/256/128) | 8192 | sentence-transformers; TEI support UNVERIFIED | MTEB multilingual v2 = 61.36 (card). Multimodal. |
| intfloat/multilingual-e5-large-instruct | MIT | 1024 | **512** | TEI ✔ | Context too short for long articles. |
| Snowflake/snowflake-arctic-embed-l-v2.0 | Apache-2.0 | 1024 | 8192 | TEI ✔ | – |
| **BAAI/bge-reranker-v2-m3** (default) | Apache-2.0 | – | 8192 | TEI ✔ | – |
| Qwen/Qwen3-Reranker-0.6B / 4B | Apache-2.0 | – | 40k | vLLM pooling (score); **not in TEI's list** | – |
| Alibaba-NLP/gte-multilingual-reranker-base | Apache-2.0 | 0.3B | 8192 | TEI ✔ | CPU-friendly. |

Infinity (`michaelf34/infinity`) has had no release since 2025-08-22 (0.0.77), so prefer TEI 1.9.4. **Critical:** store `embedding_model`, `hf_revision` and `dim` in a DB metadata table, and have the MCP server refuse to start if the embedder it connects to differs.

---

## 6. Chat front-ends that act as offline MCP clients

- **Open WebUI v0.12.3 (default).**
  - Native MCP since v0.6.31, **Streamable HTTP only**. stdio and SSE need `mcpo` (`ghcr.io/open-webui/mcpo:main`; latest PyPI release 0.0.20 from Feb 2026, so stagnant). Only admins can add MCP servers.
  - Offline settings: `OFFLINE_MODE=true` also forces `HF_HUB_OFFLINE`, turns off the version check and the `*_AUTO_UPDATE` flags. The docs warn that these settings "do not block outbound connections", so use an `internal: true` network.
  - License: BSD-3 up to v0.6.5. From v0.6.6 a branding clause applies to deployments with more than 50 users in 30 days. It is not OSI-approved.
- **LibreChat** (`librechat/librechat:v0.8.8`, MIT, 2026-10-01). Supports stdio, SSE and Streamable HTTP MCP, gives each user an isolated connection, and has an Agent Builder MCP picker. It needs MongoDB and Meilisearch, so the footprint is heavier.
- **AnythingLLM** (`mintplexlabs/anythingllm:1.18.0@sha256:5461782e…`, MIT). MCP tools are for agents; config in `anythingllm_mcp_servers.json`; stdio, SSE and streamable transports.
- **Headless / batch.** A small Python agent using the `mcp` client SDK and the OpenAI-compatible LLM API. It doubles as the evaluation harness. llama-server's built-in stdio MCP is another option.

---

## 7. Airgap packaging and sneakernet updates

**Build side (online staging host, one script, `make bundle`):**
1. Resolve every image to a digest (table above). Then `docker pull` and `docker save` everything into `images.tar`, compress with zstd. Alternatively `skopeo copy` to `oci-archive`.
2. Models:
   - vLLM and TEI: `hf download <repo> --revision <commit> --local-dir models/<name>`. Get the commit SHA from `/api/models/<repo>`.
   - Ollama: `ollama pull qwen3.8:27b-q4_K_M` and archive `$OLLAMA_MODELS`. Copying the store is common practice but not documented, so UNVERIFIED. The documented path is to ship the GGUF plus a `Modelfile` and run `ollama create` offline.
3. Database:
   - Build and embed online with the **same** embedder image and revision.
   - Export with `pg_dump -Fd -j8 --compress=zstd:9` (zstd needs PG16+; recalled).
   - Optionally wrap it as a `legilux-db:<date>` image whose `/docker-entrypoint-initdb.d` restores the dump on first start. Then shipping data is just another `docker load`.
4. SBOM and scan: `syft <img> -o spdx-json`, and `grype` online. For scanning inside the airgap, export the grype DB as well (UNVERIFIED commands).
5. Integrity:
   - Write a `SHA256SUMS` manifest and sign it with `cosign sign-blob --key` or minisign.
   - Record image digests, model revisions, the DB snapshot date, row counts and the embedder ID in `release.json`.
6. Python dependencies: images are built online, so no wheelhouse is needed. If you must rebuild inside the airgap:
   ```
   uv pip compile --generate-hashes
   pip download --only-binary=:all: -d wheelhouse
   pip install --no-index --find-links wheelhouse --require-hashes
   ```

**Airgap side:**
- Verify the signature and checksums, then `docker load -i images.tar`. Optionally `docker push` into `registry:3.1.2` for multi-host setups.
- `docker compose --profile gpu24 up -d` with `pull_policy: never`.
- Offline env vars everywhere: `HF_HUB_OFFLINE=1`, `HF_HUB_DISABLE_TELEMETRY=1`, `DO_NOT_TRACK=1`, `VLLM_NO_USAGE_STATS=1`, `OLLAMA_NO_CLOUD=1`, `OFFLINE_MODE=true`.
- Backend networks are `internal: true`.

**Update procedure (data, e.g. weekly or monthly):**
1. Ship only the new DB bundle. It is small compared with the models.
2. Restore it into `legilux_<date>`.
3. Run the golden-question smoke test through MCP (counts, a list of ELIs, 20 Q/A with expected citations).
4. Switch the MCP `DATABASE_URL` and restart; the server is stateless, so this is quick.
5. Keep the previous DB for rollback.

Image and model updates go through the same signed-bundle flow. If the embedder changes, the whole corpus must be re-embedded online.

**Bundle size, 24 GB-tier default (compressed amd64 images, VERIFIED):**
- Images: Ollama 3.79 + Open WebUI 1.62 + TEI cpu 0.24 + pgvector 0.17 + MCP ≈0.15 + Caddy ≈0.02 ≈ **6 GB**.
- Models: qwen3.8 Q4 18 GB + Qwen3-Embedding-0.6B 1.2 GB + bge-reranker-v2-m3 2.3 GB ≈ **21.5 GB**.
- Plus the DB dump. Fits on a 64 GB stick.
- The 48–80 GB tiers add vLLM (9.04 GB compressed) and 31–71 GB of weights.

---

## 8. Hardware sizing (computed and estimated; KV cache derived from HF `config.json`)

| Tier | GPU | CPU / RAM | NVMe | LLM | Embed + rerank | Users (estimate, UNVERIFIED) |
|---|---|---|---|---|---|---|
| S: CPU-only | – | 16–32 cores (AVX-512/AMX), 64 GB (32 GB minimum) | 250 GB | gemma4:26b-a4b QAT (16 GB) or qwen3.6:35b-a3b Q4 (24 GB) via Ollama or llama.cpp | TEI cpu-1.9.4 (≈2–4 GB RAM) | 1–2. Prefill of 10–20k retrieved tokens takes minutes. |
| **M: 1×24 GB** (RTX 4090 / L4 / A10) | 24 GB | 16 cores, 64 GB | 500 GB | qwen3.8:27b Q4_K_M 18 GB + 32K ctx (KV ≈2 GiB) | TEI on CPU, or the GPU image (~2–3 GB VRAM) | 2–5 |
| L: 1×48 GB (L40S / RTX 6000 Ada) | 48 GB | 24 cores, 128 GB | 1 TB | vLLM + Qwen3.8-27B-FP8 30.9 GB, `--max-model-len 131072` (KV ≈8 GiB per 128K sequence) | TEI `89-1.9.4`; Qwen3-Embedding-4B optional | 10–30 |
| XL: 1×80–96 GB (H100 / A100-80 / RTX PRO 6000) | 80–96 GB | 32 cores, 256 GB | 2 TB | Qwen3.8-27B BF16, gpt-oss-120b (65 GB) or Mistral-Small-4 NVFP4 (70.8 GB, Blackwell) | TEI `hopper-1.9.4` + Qwen3-Reranker-4B via vLLM | 30–100 |

KV cache per token at BF16 (computed from config):

| Model | KV per token |
|---|---|
| Qwen3.8-27B | 64 KiB |
| Qwen3.6-35B-A3B | 20 KiB |
| Ministral-3-14B | 160 KiB |
| Mistral-Small-4 (MLA) | ≈22.5 KiB |
| gpt-oss-120b | ≈36 KiB (full-attention layers only) |
| Gemma-4-26B-A4B | small; mostly 1024-token sliding windows |

**Postgres host:** 4–8 vCPU and 16–32 GB RAM. Storage ≈ text (a few GB) + chunks × dim × 2 bytes (halfvec) × ~3 to cover HNSW. Example: 300k chunks × 1024-d ≈ 0.6 GB of vectors and about 2 GB with the index.

**NVIDIA hosts:** the driver and Container Toolkit must also be installed offline (.deb/.rpm). vLLM's default image wants CUDA 13 (driver ≥580, recalled); use the `-cu129` tag otherwise. Ampere cards have no FP8 hardware, so use BF16 or AWQ/GPTQ there.

---

## 9. Compose sketch (profiles)
```yaml
networks: { backend: { internal: true }, edge: {} }
services:
  db:      { image: pgvector/pgvector:0.8.7-pg18-trixie@sha256:9d9c93…, networks: [backend] }
  embed:   { image: ghcr.io/huggingface/text-embeddings-inference:cpu-1.9.4@sha256:2538ea…,
             command: ["--model-id","/models/Qwen3-Embedding-0.6B"],
             environment: {HF_HUB_OFFLINE: "1"}, volumes: ["./models:/models:ro"], networks: [backend] }
  rerank:  { same image, command: ["--model-id","/models/bge-reranker-v2-m3"] }
  llm:     { profiles: [cpu, gpu24], image: ollama/ollama:0.40.5@sha256:5d3a2f…,
             environment: {OLLAMA_NO_CLOUD: "1", OLLAMA_CONTEXT_LENGTH: "32768", OLLAMA_KEEP_ALIVE: "-1"} }
  llm-vllm: { profiles: [gpu48, gpu80], image: vllm/vllm-openai:v0.31.0@sha256:c1c9f6…,
             command: ["/models/Qwen3.8-27B-FP8","--served-model-name","legal-llm","--max-model-len","131072",
                       "--enable-auto-tool-choice","--tool-call-parser","qwen3_coder","--reasoning-parser","qwen3"],
             environment: {HF_HUB_OFFLINE: "1", VLLM_NO_USAGE_STATS: "1"} }
  mcp:     { image: legilux-mcp:2026.10.16, environment: {DATABASE_URL: postgres://legilux_ro@db/legilux}, networks: [backend] }
  webui:   { image: ghcr.io/open-webui/open-webui:v0.12.3@sha256:a537bb…, networks: [backend, edge],
             environment: {OFFLINE_MODE: "true", TOOL_SERVER_CONNECTIONS: '[{"type":"mcp",…}]', WEBUI_SECRET_KEY: …} }
  proxy:   { image: caddy:2.11.7-alpine, ports: ["443:443"], networks: [edge] }
```
The tool and reasoning parser names for Qwen3.8 are inferred from the Qwen3.6 card (UNVERIFIED for 3.8). The Postgres 18 image's PGDATA layout change (`/var/lib/postgresql/18/docker`) is recalled and needs checking.

---

## 10. Live queries run (excerpt)
- Docker Hub tags API, e.g.:
  ```
  https://hub.docker.com/v2/repositories/vllm/vllm-openai/tags?page_size=100&ordering=last_updated
  ```
  → `v0.31.0 2026-10-04 amd64=9.04GB archs=['amd64','arm64']`
- GHCR anonymous token plus `/v2/<repo>/tags/list` and manifest HEAD, for digests and sizes.
- PyPI JSON (`mcp`, `fastmcp`, `open-webui`), npm registry, HF `/api/models/<id>` and `/tree/main`.
- `ollama.com/library/<m>/tags`.
- Two small SPARQL `LIMIT 3` queries against `https://data.legilux.public.lu/sparqlendpoint` with a 2 s gap (shown in `verified_facts`).
- `pip download mcp==2.3.0` and `mcp-types==2.3.0` wheels, inspected without executing them.
