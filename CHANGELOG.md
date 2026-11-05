# Changelog

All notable changes to OntoForge are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses [Semantic Versioning](https://semver.org/).

## [Unreleased]

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
