---
description: Configure OntoForge - AI providers (Ollama, OpenAI-compatible, Amazon Bedrock), local folders, resource limits, GitHub tokens and optional integrations.
---

# Configuration

Copy `.env.example` to `.env`. `./scripts/start.sh` loads it automatically; restart after changes. If you run uvicorn directly, export it first with `set -a; source .env; set +a`. `.env` is trusted local configuration: OntoForge never sources files from analysed repositories, and `.env` is ignored by git.

## AI providers

AI is optional. Without it you still get the code graph, the framework ontology (Frappe, JPA), rules read from code, the reasoner and every export. You can also choose and test the provider in **Settings → AI provider**; values set there override `.env`, and API keys entered there stay in server memory only.

### Ollama (default, local)

```dotenv
ONTO_AI_PROVIDER=ollama
ONTO_AI_BASE_URL=http://127.0.0.1:11434
ONTO_AI_MODEL=qwen2.5-coder:14b   # any name from `ollama list`
ONTO_AI_TIMEOUT_SECONDS=180
```

Start the Ollama app (or `ollama serve`) and pull a model. 7B models work but are slow on large passes and may cut long answers short; 14B-class models give noticeably better definitions and rules if your machine has the memory. Use **Test connection** in **Settings → AI provider** to verify it and list installed models. Ollama calls use the native `/api/chat` endpoint with a JSON schema, bypass HTTP proxies, and count as $0 in AI Usage.

### OpenAI or any OpenAI-compatible server

```dotenv
ONTO_AI_PROVIDER=openai
ONTO_AI_BASE_URL=https://api.openai.com/v1
ONTO_AI_MODEL=<model>
ONTO_AI_API_KEY=<key>        # or OPENAI_API_KEY
```

### Amazon Bedrock

```dotenv
ONTO_AI_PROVIDER=bedrock
ONTO_AI_REGION=us-east-1
ONTO_AI_MODEL=<model ID or inference profile ID>
# ONTO_AI_AWS_PROFILE=<profile>
```

Credentials come from the standard AWS chain (profile, SSO, environment, role) or a Bedrock API key in `AWS_BEARER_TOKEN_BEDROCK`. Bedrock calls use the Converse API with prompt caching.

### Cheap and strong tiers

The generative passes use a cheap model for most work and a strong model for rules and the quality judge. Set both in **Settings → AI provider** (they default to `ONTO_AI_MODEL`). Enter prices per model in **AI Usage** (USD per million tokens) to get cost estimates, projections and budget caps.

| Variable | Default | Meaning |
|---|---|---|
| `ONTO_AI_TIMEOUT_SECONDS` | 180 | Timeout for one model call |
| `ONTO_JUDGE_SAMPLE` | 8 | AI statements the strong model reviews after each generative run (0 disables) |

## Sources and limits

| Variable | Default | Meaning |
|---|---|---|
| `ONTO_LOCAL_ROOTS` | the OntoForge folder | Folders the backend may read, separated by `:` (`;` on Windows) |
| `ONTO_DATA` | `./data` | Database (`ontoforge.db`) and snapshots |
| `ONTO_MAX_FILE_BYTES` | 2 MiB | Largest file parsed |
| `ONTO_MAX_ARCHIVE_BYTES` | 256 MiB | Largest ZIP or GitHub archive downloaded |
| `ONTO_MAX_EXTRACT_BYTES` | 1 GiB | Largest expanded archive |
| `ONTO_MAX_ARCHIVE_ENTRIES` | 100,000 | Most entries in an archive |
| `ONTO_CONCURRENCY` | 2 | Analysis jobs running at once |
| `GITHUB_TOKEN` | – | Optional fallback token for GitHub sources (prefer entering a token in the UI). See [GitHub sources](user-guide/github-sources.md). |
| `PORT` | 8017 | Port used by `scripts/start.sh` |

## Optional integrations

### Open Ontologies (RDF/OWL checks, SHACL, RDFS inference, SPARQL)

```sh
scripts/setup_open_ontologies.sh --write-env
```

downloads the pinned v2.1.0 release, verifies its SHA-256 and sets `ONTO_OPEN_ONTOLOGIES_BIN` and `ONTO_OPEN_ONTOLOGIES_SHA256`. Optional: `ONTO_OPEN_ONTOLOGIES_TIMEOUT_SECONDS` (90) and `ONTO_OPEN_ONTOLOGIES_OUTPUT_BYTES` (32 MiB). See [Open Ontologies](reference/open-ontologies.md).

### Graphistry (GPU graph hosting)

```sh
.venv/bin/pip install -r requirements-graphistry.txt
```

then set `GRAPHISTRY_SERVER` (default `hub.graphistry.com`) and either `GRAPHISTRY_PERSONAL_KEY_ID` + `GRAPHISTRY_PERSONAL_KEY_SECRET` (preferred) or `GRAPHISTRY_USERNAME` + `GRAPHISTRY_PASSWORD`. Graphs are uploaded only after you confirm in **Open in… → Graphistry**.

## Browser settings

These are remembered per browser in local storage, not on the server:

- the **colour theme** (palette button in the header; Sand & Indigo by default, applied before the page first paints)
- page, source, filters per layer, graph layer, view and diagram layout
- explorer panel and inspector open or closed, inspector width
- Explore's Space/Studio scene and sound

Clearing site data resets them to the defaults.
