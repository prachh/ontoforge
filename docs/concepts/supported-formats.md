---
description: Languages, frameworks and file formats OntoForge reads - Python, Java, JavaScript, TypeScript, Vue, SQL, OpenAPI, Maven, Gradle, Spring, Frappe and JPA.
---

# Supported languages and formats

OntoForge reads code statically: nothing is imported, executed, installed or built. Frameworks that declare their business model (Frappe, JPA, Pydantic) give the most complete ontology without any AI.

| Format | Mechanism | Extracted facts | Principal limits |
|---|---|---|---|
| Python `.py` | Python AST | Files, classes, functions, tests, annotations, HTTP decorators, candidate calls; validation checks (`if …: raise`, `frappe.throw`) and Pydantic/dataclass field constraints as business rules | Static only; no runtime/import execution; conservative local references |
| Vue `.vue` single-file components | Tree-sitter on each `<script>` block | One Component per file with its functions and calls; `doctype="…"` template attributes as DocType usage | Templates are not otherwise parsed |
| JS/JSX/TS/TSX | Tree-sitter | Classes, named functions/methods, interfaces/type aliases, candidate calls | No type-checker, bundler resolution or arrow-function naming |
| Java `.java` | Tree-sitter | Classes, records, enums, interfaces, methods/constructors, JUnit `@Test` methods, extends, imports, calls, object creation, Spring `@*Mapping` routes; JPA entities (fields, relations, inheritance, Bean Validation, unique columns, Javadoc) | No type-checker, classpath or overload resolution; wildcard imports and annotation-driven wiring (DI, JPA) not followed |
| SQL `.sql` | SQLGlot AST | DDL tables, columns, foreign-reference candidates | Default dialect; file-level locations |
| OpenAPI `.json/.yaml/.yml` (multi-document YAML read in full) | JSON / safe YAML parser | HTTP operations, schemas, local schema refs | General structured data unsupported; no remote `$ref` fetching; file-level locations |
| Maven `pom.xml` | XML parser (no entity expansion or network) | Project coordinates and modules; parent, dependencies with scope and `${property}` versions, build plugins | Other XML files unsupported; `dependencyManagement` and profiles not modelled; parent POMs not fetched |
| Gradle `.gradle`/`.gradle.kts` | Text patterns, never evaluated | Group/version/root project and includes; plugins; string-literal dependency declarations with simple variable versions | Version catalogs (`libs.x`), computed and multi-line declarations not resolved |
| `.properties` | Properties parser | One Configuration per file with its settings (`application-<profile>` sets the profile) | Placeholders not resolved; secret-looking keys (password, secret, token, credential, API/private key) are masked |
| Spring `application*/bootstrap*.yml/.yaml` | Safe YAML parser | One Configuration with flattened settings; profile documents prefixed `[profile]` | Same masking as properties |
| Frappe DocType JSON | JSON parser | DocType models with fields, Link/Table references, Select options, permissions, naming, submittable/tree/child-table flags and field rules (`mandatory_depends_on`, `unique`, `set_only_once`, …) | Custom fields created in code (`setup.py`) are not seen |
| Frappe metadata JSON (Workflow, Report, Notification, Workspace, Number Card, …) | JSON parser | Metadata entities linked to the DocTypes they name; workflow states and transitions become lifecycles and transition rules | Conditions are kept as text, not executed |
| Frappe fixtures and test records | JSON parser | Up to 200 typed records per file as instances of their DocType | Child-table rows inside records are not extracted |
| Markdown `.md` | Heading parser | Documentation sections and excerpts | Supporting evidence, not authoritative business policy |
| Plain text `.txt` | Section evidence | File-level document excerpt | No semantic fact extraction |

Other formats remain inventoried as unsupported. Binaries are never executed. Syntax errors are visible file failures. Git LFS pointers and symlinks are excluded. Default parse budget is 2 MiB per file, configurable with ONTO_MAX_FILE_BYTES. Oversized files are reported; analysis never claims they were parsed.
