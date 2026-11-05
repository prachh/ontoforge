---
description: Measured performance and current limits of OntoForge - parsing, AI generation, graph sizes, storage and security boundaries.
---

# Scale and limitations

OntoForge is a local, single-user tool. This page lists what has been measured and where the current boundaries are, so you can decide whether it fits your codebase.

## Measured benchmark

Reproduce with `.venv/bin/python scripts/scale_test.py 1000`; it prints the timings and memory as JSON.

Environment: macOS 26.7.1, arm64 Apple Silicon, Python 3.13.5, 12 reported CPUs, local filesystem. Corpus: **1,000 generated small Python files**, one class, one method and one test per file, **4,000 graph entities**. Cold run **1.714 seconds**, cached re-analysis **1.401 seconds**, **1,000/1,000 files parsed**, **zero failures/pending**, **73.03 MiB** process peak RSS across both runs. The database after both snapshots measured **13,795,328 bytes**. This is not a production or distributed capacity benchmark. Browser graphs, live model latency and GitHub download rate are not included in those timings.

## Boundaries

### Acquisition and parsing
- There is no product limit on folder or file count. Inventory streams `os.walk` and parses one file at a time.
- Default budgets (all configurable; see [Configuration](../configuration.md)): 2 MiB per file, 256 MiB archive transfer, 1 GiB expanded extraction, 100,000 archive entries, two concurrent analysis jobs. Files over budget are reported as failed, never silently skipped.
- ZIP uploads are validated against traversal, symlinks and size budgets, but uploads are not streamed to disk.
- Root `.gitignore` and configured globs are applied; nested and global Git ignore rules are not.
- Sensitive-name exclusion (`.env`, keys, credentials) is not full secret scanning. Check the sharing scope before using a cloud AI provider.
- Git LFS contents and submodules are not acquired.
- Static references resolve conservatively (same-file or unique names). Dynamic calls, reflection, dependency-injection wiring and full cross-module call graphs are not resolved.
- Django ORM models are not read deterministically yet; Django projects rely on AI concept discovery.

### Jobs and storage
- File and AI-batch checkpoints persist. A server restart pauses interrupted jobs; you choose whether to resume.
- Interrupted downloads restart the pinned archive (no HTTP range resume). A local-folder capture is sequential, not an atomic filesystem snapshot.
- Re-analysis tracks added, modified and deleted files and marks affected mappings stale. Rename tracking and automatic mapping migration are not implemented.
- SQLite (WAL) serialises writes. There are no distributed workers. Snapshots accumulate on disk until you delete them.

### AI
- AI concept discovery sends compact cards in batches of 12 with short code excerpts (700 characters). Batches that time out, hit the output limit or return broken JSON are split and retried.
- Provider context limits, quotas and local model speed still bound generation. Small local models (7B) can be slow or truncate long answers.
- Ollama, OpenAI-compatible servers and Amazon Bedrock are supported. Embeddings and automatic model routing are not.

### Graph views
- The table and API pages return at most 150 entities per request.
- The 2D map (Sigma) and cosmos.gl views handle thousands of nodes. Code graphs with more than 2,500 entities open with functions and columns hidden (one click shows everything), and edge labels appear only for small selected neighbourhoods. The 3D views draw every node as a separate object; on very large code graphs (thousands of nodes) use the area filter first. Explore stops "breathing" and shows particles only for the selection on large graphs.
- Browsers allow about 16 live WebGL contexts per page. Views rebuild themselves if the browser drops one.

### Security and deployment
- No authentication, multi-tenant access control, SSO, encrypted credential vault or GitHub App flow. Keep the HTTP listener on loopback (the default and the Docker Compose binding).
- Session API keys and GitHub tokens stay in server memory and are lost on restart.
- Accessibility work targets WCAG 2.2 AA (labelled controls, visible focus, keyboard tabs, menus and canvas controls, reduced motion, a table alternative to graphs, contrast checked for every colour theme), but no formal audit has been done.
- ✦ Explore keeps its own Space/Studio scene themes and the OWL notation view keeps the standard WebVOWL colours; they do not follow the app colour theme.

### Not verified yet
- Private GitHub repositories end to end, Linux and Windows builds of the optional Open Ontologies engine, and running the Docker image against a host Ollama server.
