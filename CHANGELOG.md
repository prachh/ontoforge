# Changelog

All notable changes to OntoForge are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- New workspace layout: navigation rail, collapsible explorer panel (source, analysis status, Review scope, searchable domain tree with concept counts), header with breadcrumb source switcher, Export menu and Generate with AI.
- Knowledge Graph tabs (Graph, Ontology, Compare, Coverage) with one toolbar for search, layer, area, view and a Filters panel; compact counts.
- Inspector with Overview, Relationships and Evidence tabs; relationships are labelled Declared, Inferred or AI (with confidence when the model reported it); resizable, collapsible, and a drawer on narrow screens.
- 2D map: area boundaries, a highlighted selected neighbourhood with directional connectors, selective edge labels, minimap, zoom/fit/reset controls and keyboard panning.
- Six colour themes (Sand & Indigo default, Arctic Blue, Obsidian & Copper, Graphite & Violet, Ivory & Ember, Midnight Navy & Electric Blue), applied to every view including canvas, WebGL and 3D renderers without reloading; saved per browser.
- Large code graphs (over 2,500 entities) open with functions and columns hidden, one click away.

### Changed
- The Knowledge Graph fills the window; Compare uses the full width until something is selected (its explanation moved behind an info button).

## [0.1.0] - 2026-11-05

First public release.

### Added
- Source acquisition from local folders, ZIP uploads and GitHub (pinned to a commit, no code execution).
- Static parsers for Python, Java, JavaScript, TypeScript, Vue, SQL, OpenAPI, Maven, Gradle, Spring configuration, Markdown, Frappe DocTypes and metadata.
- Business ontology from framework metadata (Frappe, JPA, Pydantic) and business rules read from code, including Python `raise` checks.
- Generative ontology passes with cheap and strong model tiers, cost plans, budget caps, caching, claim validation and an AI judge; providers Ollama, OpenAI-compatible and Amazon Bedrock.
- Built-in OWL 2 RL reasoner; optional Open Ontologies engine for SHACL, RDFS inference and SPARQL.
- Knowledge Graph views: Sigma map, Structure, Focus, Diagram (dagre, ELK), Coverage, SPARQL; cosmos.gl, deck.gl, WebVOWL-style notation, 3D code city, two-floor code ↔ domain view, 3D force graph, terrain and the interactive ✦ Explore mode, with WebXR VR/AR.
- Impact of a change across code and domain.
- Outputs: documentation, domain guide, rule catalogue with expert review, reasoning, timeline, git hotspots, Q&A, sample data, contracts, changes and quality.
- Grounded chat per source with saved conversations.
- Exports: OWL/Turtle, SHACL, SKOS, GraphML, GEXF, Neo4j, Mermaid, Kineviz GraphXR and Graphistry.

[Unreleased]: https://github.com/prachh/ontoforge/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/prachh/ontoforge/releases/tag/v0.1.0
