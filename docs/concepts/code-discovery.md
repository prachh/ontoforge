---
description: How OntoForge discovers code structure statically with Python AST, Tree-sitter and SQLGlot, resolves references conservatively and records provenance.
---

# Code discovery

Python uses `ast` without imports or execution. It extracts classes, functions, test functions, parameters, annotations and supported router decorators. Calls and imports are reference candidates. Tree-sitter parses JS/JSX/TS/TSX classes, named functions, methods, interfaces, type aliases and calls. Tree-sitter also parses Java types, methods, JUnit tests, extends, imports, calls and Spring MVC mapping annotations; imports resolve to the unique file matching their package path. SQLGlot parses DDL tables, columns and foreign references. Structured YAML/JSON parses OpenAPI paths, HTTP operations, schemas and local schema references. Markdown headings and text provide documentation evidence.

Reference resolution is deliberately conservative: a bare target name must be unique **in the same file**. Attribute calls, imports, cross-file names and ambiguous targets remain unresolved. It does not manufacture call edges or claim complete interprocedural/runtime analysis. A TypeScript function assigned to an arrow expression is not yet represented as a named callable. Nested lexical scope resolution is limited. SQL dialect coverage follows SQLGlot's default dialect.

Each entity contains stable path/symbol/line-derived ID, source ID, snapshot ID, content hash, symbol location, method, status, excerpt, optional commit and pinned GitHub URL. Edges preserve extraction/reference evidence. Structured OpenAPI and SQL currently use file-level line 1 locations; symbols are precise but individual YAML/SQL source lines are not. Stable IDs change if symbol location or name changes; mappings are flagged stale at file granularity.

Inventory never follows symlinks. Default exclusions identify dependency directories, generated artifacts, credentials and `.env` patterns; additional globs are configurable in scope. Root `.gitignore` is applied; nested `.gitignore` and global Git ignore configuration are not yet implemented. Potential secrets with ordinary filenames are not comprehensively detected: inspect sharing scope before live AI. The application datastore is excluded to avoid recursive self-analysis.

Hashes and file manifests preserve failed, excluded, unsupported and pending states. Parsing errors do not suppress successful files. Unsupported JSON/YAML is inventoried rather than treated as OpenAPI. Per-file budgets bound parser input; an oversized file is a visible failure. Physical byte budgets are configurable rather than a product file-count limit.
