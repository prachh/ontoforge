---
description: The difference between an ontology and a knowledge graph, and how OntoForge links a business ontology to the code knowledge graph that implements it.
---

# Ontology vs knowledge graph

An ontology defines types, meanings, properties and permitted relationships. The knowledge graph records particular source entities and their connections. Generative ontology is the AI-assisted process of proposing and refining definitions. An ontology engine checks or reasons over representations within its declared capabilities. These are complementary, not competing technologies.

In the bundled Meridian sample, `API` is a technical definition and `POST /refunds` is a particular API declaration. `Refund` is an interpreted domain concept with supporting implementation and policy evidence. Neither the code nor the generated graph creates an actual refund transaction. Python `RefundService` and an OpenAPI operation are separate source entities even if they refer to a similar feature.

In OntoForge the two are shown together. **Knowledge Graph → Side by side** puts the code graph next to the domain ontology, and selecting a concept highlights the code it was found in (and the other way round). The *3D two floors* view stacks them: the code city below, concepts above, with beams to their code. **Impact of a change** follows dependencies across both layers through the concept→code mappings.

The engines work on the ontology, not on running systems. The built-in engine checks definitions, required properties and subclass inference; the optional [Open Ontologies](../reference/open-ontologies.md) engine adds RDFS inference, SHACL validation and SPARQL. Neither touches your source facts or invents operational records.

The built-in engine evaluates required properties and subclass traversal. The optional external path uses RDFS for inference and SHACL for validation. RDFS domain/range infer types; they do not impose closed-world validity requirements. No certificate establishes business policy truth or application runtime correctness.
