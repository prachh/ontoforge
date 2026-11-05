---
description: Explore a codebase as a knowledge graph and business ontology in OntoForge - 2D maps, GPU graphs, OWL notation, 3D code city, interactive 3D, VR/AR, impact analysis and exports to Neo4j, Gephi and GraphXR.
---

# Knowledge Graph

The Knowledge Graph page shows two layers of the same source:

- **Code graph**: files, classes, functions, APIs, tables, tests and their calls, imports and references.
- **Domain ontology**: business concepts, their fields, relationships, rules and lifecycles.

**Side by side** shows both, and selecting something in one highlights its counterpart in the other through the concept→code mappings. **Full screen** gives the graph the whole window.

![Knowledge Graph map of the Frappe HRMS domain ontology](../images/knowledge-graph.png)

## Views

| View | What it shows |
|---|---|
| **Map** | The whole graph with a force layout, grouped into areas (Louvain communities or modules). Search with ⌘K / Ctrl+K. |
| **Structure** | The is-a hierarchy and containment as a tree. |
| **Focus** | One node and its neighbours, step by step. |
| **Diagram** | A UML-style class diagram (React Flow) with *Standard (dagre)* or *Layered (ELK)* layout. |
| **Coverage** | How much of the code is explained by concepts, plus model-check findings. |
| **SPARQL** | Read-only SPARQL over the exported RDF (with the optional Open Ontologies engine). |

A searchable table alternative is available for every graph.

## Graph engines (Map)

The engine dropdown changes how the graph is drawn, not the data. Your choice is remembered, and each engine loads only when chosen.

| Engine | Layers | Best for |
|---|---|---|
| **Standard (Sigma)** | both | Everyday exploration, fast for thousands of nodes |
| **✦ Explore: interactive 3D** | both | Playing with the graph: living physics, drag and pin, flowing particles |
| **GPU, large graphs (cosmos.gl)** | both | Very large graphs; the force layout runs on the GPU |
| **2.5D landscape (deck.gl)** | both | Columns whose height is rules, links, properties, members or git changes |
| **OWL notation (WebVOWL style)** | domain | Ontologists: classes, object and datatype properties, subclass and disjoint links |
| **3D code city** | code | Folders as districts, classes as buildings (height = methods, footprint = usage) |
| **3D two floors: code ↔ domain** | domain | Concepts floating above the code city with beams to the code that implements them |
| **3D force graph** | both | The graph laid out in three dimensions |
| **3D rule / code terrain** | both | A landscape whose height is the density of rules, links or git changes |

### ✦ Explore

![Explore: the interactive 3D graph with Salary Slip selected](../images/explore-3d.png)

- The graph assembles from the centre, then keeps "breathing" (physics runs gently; large graphs settle).
- **Drag** a node to move it, drop to pin it; double-click to unpin, or **Unpin all**.
- **Click** a node to fly the camera to it. A breadcrumb trail records where you have been (Esc steps back).
- Particles flow along links in the dependency direction: all links, the selection only, or off.
- **Pause/Resume** (Space), **Shake**, **Focus** (F, 1–3 steps around the selection), **Reset** (R).
- A minimap shows where you are; click it to fly there. Idle for a few seconds and the camera orbits.
- Themes: **Space** (dark, glow, stars) and **Studio** (light). Subtle synthesised sounds with a mute button.
- With *reduced motion* set in your OS, the intro, orbit, flights and particles are turned off.

### 3D views, quality and VR

![3D two floors: domain concepts above the code city](../images/two-floors.png)

Every 3D view has a **High quality / Fast** switch (high adds soft shadows, ambient occlusion and glow). On a device with WebXR, **Enter VR** and **Enter AR** place the scene in front of you at table scale. Add `?xr-emulate` to the URL to test the buttons without a headset.

## Impact of a change

Select any concept or code entity and choose **Impact of a change** in the inspector. OntoForge follows dependencies backwards (who calls, imports, extends, uses or links to it) across both layers and lists:

- affected concepts and code, each with the chain that explains why
- tests to run, APIs, business rules to re-check and lifecycles involved

The affected nodes are highlighted on the map; Explore and the 3D views animate pulses along the chain.

![Impact of a change on Salary Slip](../images/impact-analysis.png)

## Exports: Open in…

| Target | Format |
|---|---|
| **Kineviz GraphXR** | ZIP with `nodes.csv`, `edges.csv`, `graph.json` and import steps |
| **Graphistry** | Uploads to a Graphistry server after you confirm (optional, see [Configuration](../configuration.md#graphistry-gpu-graph-hosting)) |
| **GraphML** | yEd, Gephi, Cytoscape, NetworkX |
| **GEXF** | Gephi, Gephi Lite |
| **Neo4j** | CSV files with an `import.cypher` for `LOAD CSV`, plus `neo4j-admin` import files |
| **Mermaid** | Class diagram of the 60 most connected concepts, for docs and wikis |
| **Download RDF…** | OWL/Turtle (model, data or full) and SHACL shapes for Protégé and other RDF tools |

Each export can contain the domain ontology, the code graph, or both joined by "implemented by" links.

## Generate with AI

On the domain ontology, **Generate with AI** runs the generative passes (definitions, hierarchy, relationships, logic, rules, lifecycles, events) with a cost plan and budget cap. See [Generative ontology](../concepts/generative-ontology.md).
