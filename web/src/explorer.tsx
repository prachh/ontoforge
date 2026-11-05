import React, {useCallback, useEffect, useMemo, useRef, useState} from 'react';
import * as Dialog from '@radix-ui/react-dialog';
import Graph from 'graphology';
import Sigma from 'sigma';
import {onContextLost} from './webgl';
import {Tabs} from './ui';
import {graphTokens, onThemeChange, useTheme} from './theme';
import {EdgeArrowProgram, EdgeLineProgram} from 'sigma/rendering';
import forceAtlas2 from 'graphology-layout-forceatlas2';
import FA2Layout from 'graphology-layout-forceatlas2/worker';
import louvain from 'graphology-communities-louvain';
import dagre from '@dagrejs/dagre';
import {ReactFlow, Background, Controls, Handle, Position, MarkerType} from '@xyflow/react';
import type {Node, Edge} from '@xyflow/react';
import {Search, X, Crosshair, Route, Pause, Play, ExternalLink, FileCode2, ChevronRight, Loader2, MapPin, Sparkles, ArrowLeft, ArrowRight, Code2, Shapes, ShieldCheck, WandSparkles, Zap, ZoomIn, ZoomOut, Scan, RotateCcw, Maximize2, Minimize2, SlidersHorizontal, PanelRight, Check, GitMerge, Info} from 'lucide-react';

type Obj = Record<string, any>;
export type Origin = {type: 'declared' | 'inferred' | 'ai'; confidence?: number; model?: string; basis?: string; meaningAI?: boolean};
export type ExplorerView = 'map' | 'structure' | 'focus' | 'diagram' | 'coverage' | 'query';
// Open Ontologies results for the current snapshot, and the action that runs an operation.
export type SemanticState = {available: boolean; reason?: string | null; sourceId: string; runs: {checks?: Obj; instances?: Obj; infer?: Obj}; busy: string; onRun: (operation: string) => void};
export type ExplorerMode = 'code' | 'domain';
export type MapEngine = 'standard' | 'cosmos' | 'vowl' | 'city' | 'floors' | 'deck' | 'force3d' | 'terrain' | 'explore';
const MAP_ENGINES: Record<ExplorerMode, MapEngine[]> = {code: ['standard', 'explore', 'cosmos', 'deck', 'city', 'force3d', 'terrain'], domain: ['standard', 'explore', 'cosmos', 'deck', 'vowl', 'floors', 'force3d', 'terrain']};
export type DiagramEngine = 'dagre' | 'elk';
// Alternative engines live in their own chunk, loaded only when chosen.
const CosmosCanvas = React.lazy(() => import('./engines').then(m => ({default: m.CosmosCanvas})));
const VowlCanvas = React.lazy(() => import('./engines').then(m => ({default: m.VowlCanvas})));
const CodeCity = React.lazy(() => import('./city3d').then(m => ({default: m.CodeCity})));
const TwoFloors = React.lazy(() => import('./city3d').then(m => ({default: m.TwoFloors})));
const Explore3D = React.lazy(() => import('./explore3d').then(m => ({default: m.Explore3D})));
const ForceGraph3D = React.lazy(() => import('./views3d').then(m => ({default: m.ForceGraph3D})));
const Terrain = React.lazy(() => import('./views3d').then(m => ({default: m.Terrain})));
const DeckLandscape = React.lazy(() => import('./deckview').then(m => ({default: m.DeckLandscape})));
const stored = <T extends string>(key: string, allowed: T[], fallback: T): T => {try {const v = localStorage.getItem(key) as T; return allowed.includes(v) ? v : fallback} catch {return fallback}};
const store = (key: string, value: string) => {try {localStorage.setItem(key, value)} catch { /* storage unavailable */ }};
// `previous` is the version this one refreshed (live analysis); it hands over node positions and the camera.
type Loaded = {graph: Graph; modules: string[]; laidOut: boolean; code?: Graph; settle?: number; camera?: Obj; previous?: Loaded};
export type LiveState = {running: boolean; paused: boolean; onToggle: () => void; stage?: string};
type Camera = {ids: string[]; nonce: number} | null;
type Filters = {hiddenKinds: string[]; hiddenEdges: string[]; hideTests: boolean; module: string; onlyMapped: boolean};

const CONCEPT = 'Concept';
// Graph colours follow the theme: muted edges per relationship kind, area/type palettes, selection and dimming.
const EDGES_DARK: Record<string, string> = {defines: '#243049', calls: '#33415F', imports: '#3A3F63', inherits: '#3B4D7E', uses: '#4A4636', references: '#2F4B5A', implements: '#2F4F4A', evidence: '#4B4380', property: '#33415F', relationship: '#3A4A70', is_a: '#5B4E8C', semantic: '#3E5578', link: '#34506B', contains: '#5A4B30', instance_of: '#4D4632'};
const EDGES_LIGHT: Record<string, string> = {defines: '#E4E1DA', calls: '#CBC8D6', imports: '#D2CBE0', inherits: '#BCC3E2', uses: '#DCD0B4', references: '#BCD3D9', implements: '#BFD8CE', evidence: '#C4B8E8', property: '#D3CFDD', relationship: '#BEC3DF', is_a: '#B8ABE2', semantic: '#AEC2DC', link: '#B8CCDB', contains: '#DFC9A0', instance_of: '#DCC895'};
let conceptColor = '', unlinkedColor = '', SELECT = '', DIM = '', FAINT = '', EDGE = '', palette: string[] = [];
let edgeColors: Record<string, string> = {};
function applyGraphTheme() {
  const k = graphTokens(); conceptColor = k.concept; unlinkedColor = k.nodeMuted; SELECT = k.select; DIM = k.dim; FAINT = k.edgeFaint; EDGE = k.edge; palette = k.nodes;
  edgeColors = k.mode === 'dark' ? EDGES_DARK : EDGES_LIGHT;
}
applyGraphTheme(); onThemeChange(applyGraphTheme);
const UNLINKED = 'Unlinked';

// Wording and filters that differ between the code graph and the domain ontology.
const MODES = {
  code: {edgeKinds: ['defines', 'calls', 'imports', 'inherits', 'uses', 'instance_of'], muted: 'Test code', allGroups: 'All modules', groupLabel: 'Module', root: 'Repository', crumbNote: 'click a folder to drill in, a file to focus it',
    search: 'Search functions, classes, files, concepts…', trigger: 'Search entities…', out: 'USES', in: 'USED BY', outTitle: 'Calls & uses', inTitle: 'Used by', noun: 'entities', labelThreshold: 7},
  domain: {edgeKinds: ['is_a', 'link', 'contains', 'semantic', 'property', 'relationship'], muted: 'Unlinked concepts', allGroups: 'All areas', groupLabel: 'Area', root: 'Ontology', crumbNote: 'click an area to drill in, a concept to focus it',
    search: 'Search concepts and aliases…', trigger: 'Search concepts…', out: 'LINKS TO', in: 'LINKED FROM', outTitle: 'Links to', inTitle: 'Linked from', noun: 'concepts', labelThreshold: 2},
};

// Laid-out graphs survive view switches and page navigation for the session.
const loadedGraphs = new Map<string, Loaded>();

const moduleOf = (path: string) => {const parts = path.split('/'); return parts.length > 2 ? parts.slice(0, 2).join('/') : parts.length === 2 ? parts[0] : '(root)'};
const isTestPath = (path: string) => /(^|\/)tests?\/|(^|\/)test_[^/]*$|\.(spec|test)\.[jt]sx?$/.test(path);
const hash = (s: string) => {let h = 2166136261; for (let i = 0; i < s.length; i++) {h ^= s.charCodeAt(i); h = Math.imul(h, 16777619)} return (h >>> 0) / 4294967295};
const seeded = (seed: number) => () => {seed = seed + 0x6D2B79F5 | 0; let t = Math.imul(seed ^ seed >>> 15, 1 | seed); t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t; return ((t ^ t >>> 14) >>> 0) / 4294967296};
const colorFor = (key: string, keys: string[]) => key === UNLINKED ? unlinkedColor : palette[Math.max(0, keys.indexOf(key)) % palette.length];
const conceptKey = (s: string) => s.toLowerCase().replace(/[^a-z0-9]/g, '');

function seedAround(graph: Graph, groupOf: (id: string) => string, groups: string[], spread: number) {
  const anchors = Object.fromEntries(groups.map((g, i) => {const a = i / groups.length * Math.PI * 2; return [g, {x: Math.cos(a) * 400, y: Math.sin(a) * 400}]}));
  graph.forEachNode((id, a) => {const anchor = anchors[groupOf(id)] || {x: 0, y: 0}, r = hash(a.path || id) * spread, t = hash((a.path || id) + '#') * Math.PI * 2; graph.mergeNodeAttributes(id, {x: anchor.x + Math.cos(t) * r + (hash(id) - .5) * 24, y: anchor.y + Math.sin(t) * r + (hash(id + 'y') - .5) * 24})});
}

// Keep known nodes where they were; place new ones beside an already placed neighbour.
function inheritLayout(graph: Graph, previous?: Graph) {
  if (!previous) return;
  graph.forEachNode(id => {if (previous.hasNode(id)) graph.mergeNodeAttributes(id, {x: previous.getNodeAttribute(id, 'x'), y: previous.getNodeAttribute(id, 'y')})});
  graph.forEachNode(id => {
    if (previous.hasNode(id)) return;
    const anchor = graph.neighbors(id).find(n => previous.hasNode(n));
    if (anchor) graph.mergeNodeAttributes(id, {x: graph.getNodeAttribute(anchor, 'x') + (hash(id) - .5) * 40, y: graph.getNodeAttribute(anchor, 'y') + (hash(id + 'y') - .5) * 40});
  });
}
const refreshedFrom = (previous?: Loaded) => {if (previous) previous.previous = undefined; return previous ? {previous, settle: previous.graph.order > 1000 ? 2500 : 1200} : {}};

function buildCodeGraph(data: Obj, previous?: Loaded): Loaded {
  const graph = new Graph({type: 'directed', multi: false, allowSelfLoops: false});
  for (const [id, name, kind, path, line] of data.nodes) graph.addNode(id, {label: name, kind, path, line, module: moduleOf(path), test: kind === 'Test' || isTestPath(path), x: 0, y: 0, size: 2});
  for (const [source, target, kind, method] of data.edges) {
    if (source === target || !graph.hasNode(source) || !graph.hasNode(target)) continue;
    const key = source + '>' + target;
    if (graph.hasEdge(key)) {const kinds: string[] = graph.getEdgeAttribute(key, 'kinds'); if (!kinds.includes(kind)) graph.setEdgeAttribute(key, 'kinds', [...kinds, kind]); continue}
    graph.addDirectedEdgeWithKey(key, source, target, {kind, kinds: [kind], method, type: 'line'});
  }
  const modules = [...new Set<string>(graph.mapNodes((_, a) => a.module))].sort();
  // Seed each module around its own anchor so the force layout starts from meaningful clusters.
  seedAround(graph, id => graph.getNodeAttribute(id, 'module'), modules, 160);
  graph.forEachNode(n => graph.setNodeAttribute(n, 'size', Math.min(15, 2 + Math.sqrt(graph.degree(n)) * 1.1)));
  inheritLayout(graph, previous?.graph);
  return {graph, modules, laidOut: false, ...refreshedFrom(previous)};
}

// Domain concepts as nodes. Links come from declared relationships and from properties that name another concept;
// areas are communities of densely linked concepts (Louvain, seeded so they are stable between visits).
function buildDomainGraph(ontology: Obj | null, mappings: Obj[], code: Graph, previous?: Loaded): Loaded {
  const graph = new Graph({type: 'directed', multi: false, allowSelfLoops: false});
  const concepts: Obj[] = (ontology?.concepts || []).filter((c: Obj) => c.layer === 'domain');
  const evidence = new Map<string, string[]>();
  for (const m of mappings) if (!(evidence.get(m.concept_id) || []).includes(m.entity_id)) evidence.set(m.concept_id, [...(evidence.get(m.concept_id) || []), m.entity_id]);
  const byKey = new Map<string, Obj>();
  for (const c of concepts) for (const n of [c.name, ...(c.aliases || [])]) if (!byKey.has(conceptKey(n))) byKey.set(conceptKey(n), c);
  // Evidence whose name matches the concept (e.g. class LeaveApplication) comes first, then the most connected.
  const ranked = (c: Obj) => (evidence.get(c.id) || []).filter(id => code.hasNode(id)).sort((x, y) =>
    Number(conceptKey(code.getNodeAttribute(y, 'label')) === conceptKey(c.name)) - Number(conceptKey(code.getNodeAttribute(x, 'label')) === conceptKey(c.name)) || code.degree(y) - code.degree(x));
  const rulesOf = new Map<string, Obj[]>(); for (const r of ontology?.rules || []) rulesOf.set(r.concept, [...(rulesOf.get(r.concept) || []), r]);
  for (const c of concepts) graph.addNode(c.id, {label: c.name, kind: CONCEPT, description: c.description, properties: c.properties || [], aliases: c.aliases || [], evidence: ranked(c),
    abstract: !!c.abstract, parentId: c.parent || null, lifecycle: c.lifecycle?.states?.length ? c.lifecycle : null, events: c.events || [], roles: c.roles || [], provenance: c.provenance || {}, rules: rulesOf.get(c.id) || [], doctype: (evidence.get(c.id) || []).some(id => code.hasNode(id) && code.getNodeAttribute(id, 'kind') === 'Model' && String(code.getNodeAttribute(id, 'path')).endsWith('.json')), x: 0, y: 0, size: 4});
  // Each relationship records where it came from: declared in code/metadata, inferred from a property name, or AI-generated.
  const link = (s: string, t: string, kind: string, via: string, meaning = '', origin: Origin = {type: 'declared'}) => {
    const key = s + '>' + t;
    if (!graph.hasEdge(key)) graph.addDirectedEdgeWithKey(key, s, t, {kind, kinds: [kind], via: [via], label: via, meanings: meaning ? {[via]: meaning} : {}, origins: {[via]: origin}, type: 'line'});
    else {const a = graph.getEdgeAttributes(key); if (!a.via.includes(via)) graph.mergeEdgeAttributes(key, {via: [...a.via, via], label: [...a.via, via].join(', '), kinds: a.kinds.includes(kind) ? a.kinds : [...a.kinds, kind], meanings: meaning ? {...a.meanings, [via]: meaning} : a.meanings, origins: {...a.origins, [via]: origin}})}
  };
  const originOf = (p?: Obj): Origin => p?.origin === 'llm' ? {type: 'ai', confidence: p.confidence, model: p.model} : {type: 'declared'};
  // The is-a hierarchy (declared in code or generated) is drawn as its own edge kind.
  for (const c of concepts) if (c.parent && graph.hasNode(c.parent) && c.parent !== c.id) link(c.id, c.parent, 'is_a', 'is a', '', originOf(c.provenance?.parent));
  // An explicit reference wins; otherwise a plain property may still be named after a concept (employee → Employee).
  // Link/Table fields are drawn from their relationship (with its meaning), so only plain properties are matched by name here.
  for (const c of concepts) for (const p of c.properties || []) {if (['Link', 'Table', 'Table MultiSelect'].includes(p.fieldtype)) continue; const t = byKey.get(conceptKey(p.reference || (p.fieldtype ? '' : p.name))); if (t && t.id !== c.id) link(c.id, t.id, 'property', p.name, '', {type: 'inferred', basis: `property “${p.name}” is named after ${t.name}`})}
  for (const r of ontology?.relationships || []) for (const s of r.source_types) for (const t of r.target_types) if (s !== t && graph.hasNode(s) && graph.hasNode(t)) link(s, t, ['link', 'contains', 'semantic'].includes(r.kind) ? r.kind : 'relationship', r.label || r.id, r.description || '', {...originOf(r.provenance), meaningAI: r.provenance?.meaning?.origin === 'llm'});

  const community = graph.size ? louvain(graph, {rng: seeded(7)}) : {};
  const members = new Map<number, string[]>();
  graph.forEachNode(id => {if (graph.degree(id)) members.set(community[id], [...(members.get(community[id]) || []), id])});
  const areaOf = new Map<string, string>(); const used = new Set<string>();
  for (const ids of [...members.values()].sort((a, b) => b.length - a.length)) {
    const hub = ids.reduce((best, id) => graph.degree(id) > graph.degree(best) ? id : best, ids[0]);
    let name = String(graph.getNodeAttribute(hub, 'label')).replace(/\//g, '∕'); if (used.has(name)) name += ' ·' + used.size; used.add(name);
    for (const id of ids) areaOf.set(id, name);
  }
  graph.forEachNode((id, a) => {
    const area = areaOf.get(id) || UNLINKED;
    graph.mergeNodeAttributes(id, {module: area, test: area === UNLINKED, path: `${area}/${String(a.label).replace(/\//g, '∕')}`, subtitle: `${a.properties.length} properties · ${area === UNLINKED ? 'not linked' : area + ' area'}`, size: Math.min(22, 4 + Math.sqrt(graph.degree(id)) * 2.4)});
  });
  const modules = [...new Set<string>(graph.mapNodes((_, a) => a.module))].sort((a, b) => a === UNLINKED ? 1 : b === UNLINKED ? -1 : a.localeCompare(b));
  // Links inside an area pull harder than links between areas, so areas form readable clusters.
  graph.forEachEdge((e, _, s, t, sa, ta) => graph.setEdgeAttribute(e, 'weight', sa.module === ta.module && sa.module !== UNLINKED ? 4 : .4));
  seedAround(graph, id => graph.getNodeAttribute(id, 'module'), modules, 120);
  inheritLayout(graph, previous?.graph);
  return {graph, modules, laidOut: false, code, ...refreshedFrom(previous)};
}

// Latest cache key per "mode:snapshot", so a live refresh can start from the version on screen and drop it afterwards.
const latestKey = new Map<string, string>();
const remember = (slot: string, key: string, built: Loaded) => {const old = latestKey.get(slot); if (old && old !== key) loadedGraphs.delete(old); latestKey.set(slot, key); loadedGraphs.set(key, built); return built};
const previousFor = (slot: string) => {const k = latestKey.get(slot); return k ? loadedGraphs.get(k) : undefined};

// Loads in progress, so the domain tree and the graph share one download and build.
const inflight = new Map<string, Promise<Loaded>>();
const shared = (key: string, load: () => Promise<Loaded>) => {const cached = loadedGraphs.get(key); if (cached) return Promise.resolve(cached); let p = inflight.get(key); if (!p) {p = load().finally(() => inflight.delete(key)); inflight.set(key, p)} return p};

function loadCode(snapshotId: string, revision = '') {return shared(`code:${snapshotId}:${revision}`, () => fetchCode(snapshotId, revision))}
async function fetchCode(snapshotId: string, revision = '') {
  const key = `code:${snapshotId}:${revision}`; const cached = loadedGraphs.get(key); if (cached) return cached;
  const r = await fetch('/api/graph/full?snapshot_id=' + snapshotId);
  if (!r.ok) throw new Error(`The graph could not be loaded (HTTP ${r.status}).`);
  const data = await r.json(); const again = loadedGraphs.get(key); if (again) return again;
  return remember('code:' + snapshotId, key, buildCodeGraph(data, previousFor('code:' + snapshotId)));
}

function neighbourhood(graph: Graph, id: string | null) {
  if (!id || !graph.hasNode(id)) return null;
  return new Set([id, ...graph.neighbors(id)]);
}

// Undirected shortest path: dependencies are followed in either direction.
function findPath(graph: Graph, from: string, to: string, visible: (id: string) => boolean) {
  const previous = new Map<string, string | null>([[from, null]]); const queue = [from];
  while (queue.length) {
    const current = queue.shift()!;
    if (current === to) {const path = [to]; let step = previous.get(to); while (step) {path.unshift(step); step = previous.get(step)} return path}
    for (const next of graph.neighbors(current)) if (!previous.has(next) && (next === to || visible(next))) {previous.set(next, current); queue.push(next)}
  }
  return null;
}

function fitCamera(renderer: Sigma, ids: string[], duration = 650) {
  const points = ids.map(id => renderer.getNodeDisplayData(id)).filter(Boolean) as {x: number; y: number}[];
  if (!points.length) return;
  const xs = points.map(p => p.x), ys = points.map(p => p.y);
  const span = Math.max(Math.max(...xs) - Math.min(...xs), Math.max(...ys) - Math.min(...ys));
  renderer.getCamera().animate({x: (Math.min(...xs) + Math.max(...xs)) / 2, y: (Math.min(...ys) + Math.max(...ys)) / 2, ratio: Math.min(1.1, Math.max(.06, span * 1.35))}, {duration});
}

// Hover label: a dark tag that matches the canvas instead of Sigma's default white box.
function drawHover(context: CanvasRenderingContext2D, data: Obj, settings: Obj) {
  const size = settings.labelSize, label = data.label; if (!label) return;
  context.font = `600 ${size}px ${settings.labelFont}`;
  const w = context.measureText(label).width + 12, h = size + 10, x = data.x + data.size + 4, y = data.y - h / 2;
  const k = graphTokens(); context.beginPath(); context.arc(data.x, data.y, data.size + 3, 0, Math.PI * 2); context.globalAlpha = .2; context.fillStyle = SELECT; context.fill(); context.globalAlpha = 1;
  context.fillStyle = k.hoverBg; context.strokeStyle = k.borderStrong; context.lineWidth = 1;
  context.beginPath(); context.roundRect(x, y, w, h, 5); context.fill(); context.stroke();
  context.fillStyle = k.text; context.fillText(label, x + 6, data.y + size / 3);
}

const themeLabels = () => ({labelColor: {color: graphTokens().label}, edgeLabelColor: {color: graphTokens().text2}});
const sigmaSettings = {
  renderEdgeLabels: true, defaultEdgeType: 'line', edgeProgramClasses: {line: EdgeLineProgram, arrow: EdgeArrowProgram},
  labelFont: "'DM Sans Variable', 'DM Sans', sans-serif", labelSize: 11, labelWeight: '500', 
  edgeLabelFont: "'DM Sans Variable', 'DM Sans', sans-serif", edgeLabelSize: 10, edgeLabelWeight: '500', 
  labelDensity: .45, labelGridCellSize: 90, labelRenderedSizeThreshold: 7, zIndex: true, minCameraRatio: .015, maxCameraRatio: 3,
  defaultDrawNodeHover: drawHover,
  // Containers can be zero-sized during responsive reflow; watchSize re-measures when they get a size.
  allowInvalidContainer: true,
};
function watchSize(sigma: Sigma, element: HTMLElement) {const observer = new ResizeObserver(() => {sigma.resize(); sigma.refresh()}); observer.observe(element); return () => observer.disconnect()}

// Convex hull (monotone chain) for drawing a soft boundary around each area.
function hull(points: [number, number][]) {
  const p = [...points].sort((a, b) => a[0] - b[0] || a[1] - b[1]); if (p.length < 3) return p;
  const cross = (o: number[], a: number[], b: number[]) => (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0]);
  const lower: [number, number][] = [], upper: [number, number][] = [];
  for (const q of p) {while (lower.length >= 2 && cross(lower[lower.length - 2], lower[lower.length - 1], q) <= 0) lower.pop(); lower.push(q)}
  for (const q of [...p].reverse()) {while (upper.length >= 2 && cross(upper[upper.length - 2], upper[upper.length - 1], q) <= 0) upper.pop(); upper.push(q)}
  return [...lower.slice(0, -1), ...upper.slice(0, -1)];
}
const rgba = (hex: string, a: number) => {const n = parseInt(hex.slice(1), 16); return `rgba(${n >> 16},${n >> 8 & 255},${n & 255},${a})`};

// Subtle area boundaries drawn under the graph, recomputed after each render (domain ontology only).
function drawAreas(sigma: Sigma, canvas: HTMLCanvasElement, graph: Graph, areaColor: (area: string) => string) {
  const {width, height} = sigma.getDimensions(); const dpr = window.devicePixelRatio || 1;
  if (canvas.width !== width * dpr || canvas.height !== height * dpr) {canvas.width = width * dpr; canvas.height = height * dpr; canvas.style.width = width + 'px'; canvas.style.height = height + 'px'}
  const ctx = canvas.getContext('2d')!; ctx.setTransform(dpr, 0, 0, dpr, 0, 0); ctx.clearRect(0, 0, width, height);
  const groups = new Map<string, [number, number][]>();
  graph.forEachNode((id, a) => {
    if (a.module === UNLINKED) return; const d = sigma.getNodeDisplayData(id); if (!d || d.hidden) return;
    const v = sigma.framedGraphToViewport(d); const list = groups.get(a.module) || []; list.push([v.x, v.y]); groups.set(a.module, list);
  });
  ctx.font = "600 10px 'DM Sans Variable', sans-serif"; ctx.lineJoin = 'round';
  for (const [area, pts] of groups) {
    if (pts.length < 3) continue;
    const h = hull(pts); const cx = h.reduce((s, q) => s + q[0], 0) / h.length, cy = h.reduce((s, q) => s + q[1], 0) / h.length;
    const pad = (q: [number, number]) => {const dx = q[0] - cx, dy = q[1] - cy, len = Math.hypot(dx, dy) || 1; return [q[0] + dx / len * 22, q[1] + dy / len * 22]};
    const color = areaColor(area); ctx.beginPath(); h.map(pad).forEach(([x, y], i) => i ? ctx.lineTo(x, y) : ctx.moveTo(x, y)); ctx.closePath();
    ctx.fillStyle = rgba(color, .045); ctx.fill(); ctx.strokeStyle = rgba(color, .22); ctx.lineWidth = 1; ctx.setLineDash([4, 4]); ctx.stroke(); ctx.setLineDash([]);
    const top = h.map(pad).reduce((a, b) => b[1] < a[1] ? b : a); ctx.fillStyle = rgba(color, .8); ctx.fillText(area.toUpperCase(), top[0] - ctx.measureText(area.toUpperCase()).width / 2, top[1] - 6);
  }
}

// Overview of the whole layout with the visible window; click or drag to move there.
// Node positions come from graph coordinates; the window is found by mapping two nodes to the screen
// (graph → viewport is a scale plus an offset on each axis), so it always matches what is on screen.
function Minimap({sigma, graph, colorOf, visible, tick}: {sigma: Sigma | null; graph: Graph; colorOf: (a: Obj) => string; visible: (id: string) => boolean; tick: number}) {
  const ref = useRef<HTMLCanvasElement>(null); const box = useRef({minX: 0, maxY: 0, scale: 1});
  const W = 168, H = 112;
  useEffect(() => {
    const canvas = ref.current; if (!canvas || !sigma) return; const dpr = window.devicePixelRatio || 1; canvas.width = W * dpr; canvas.height = H * dpr;
    const ctx = canvas.getContext('2d')!; ctx.setTransform(dpr, 0, 0, dpr, 0, 0); ctx.clearRect(0, 0, W, H);
    let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity; const pts: [number, number, string][] = []; const ids: string[] = [];
    graph.forEachNode((id, a) => {if (!visible(id) || !Number.isFinite(a.x) || !Number.isFinite(a.y)) return; ids.push(id); pts.push([a.x, a.y, colorOf(a)]); minX = Math.min(minX, a.x); maxX = Math.max(maxX, a.x); minY = Math.min(minY, a.y); maxY = Math.max(maxY, a.y)});
    if (pts.length < 2) return;
    const scale = Math.min((W - 12) / ((maxX - minX) || 1), (H - 12) / ((maxY - minY) || 1)); box.current = {minX, maxY, scale};
    const toMap = (x: number, y: number) => [6 + (x - minX) * scale, 6 + (maxY - y) * scale];
    ctx.globalAlpha = .75; for (const [x, y, c] of pts) {const [mx, my] = toMap(x, y); ctx.fillStyle = c; ctx.fillRect(mx - 1, my - 1, 2, 2)} ctx.globalAlpha = 1;
    // Fit viewport = k * graph + b per axis from the two most distant nodes, then invert it for the screen corners.
    const first = graph.getNodeAttributes(ids[0]); let far = ids[1], best = -1;
    for (const id of ids) {const a = graph.getNodeAttributes(id); const d = Math.abs(a.x - first.x) * Math.abs(a.y - first.y); if (d > best) {best = d; far = id}}
    const second = graph.getNodeAttributes(far); const v1 = sigma.graphToViewport({x: first.x, y: first.y}), v2 = sigma.graphToViewport({x: second.x, y: second.y});
    const kx = (v2.x - v1.x) / ((second.x - first.x) || 1), ky = (v2.y - v1.y) / ((second.y - first.y) || 1); if (!kx || !ky) return;
    const {width, height} = sigma.getDimensions(); const gx = (vx: number) => first.x + (vx - v1.x) / kx, gy = (vy: number) => first.y + (vy - v1.y) / ky;
    const [ax, ay] = toMap(gx(0), gy(0)), [bx, by] = toMap(gx(width), gy(height));
    const r = [Math.min(ax, bx), Math.min(ay, by), Math.abs(bx - ax), Math.abs(by - ay)] as const;
    ctx.save(); ctx.beginPath(); ctx.rect(1, 1, W - 2, H - 2); ctx.clip();
    ctx.globalAlpha = .1; ctx.fillStyle = SELECT; ctx.fillRect(...r); ctx.globalAlpha = 1; ctx.strokeStyle = SELECT; ctx.lineWidth = 1.25; ctx.strokeRect(...r); ctx.restore();
  }, [sigma, graph, colorOf, visible, tick]);
  const move = (e: React.PointerEvent) => {
    if (!sigma || (e.type === 'pointermove' && !e.buttons)) return; const r = ref.current!.getBoundingClientRect(); const {minX, maxY, scale} = box.current;
    const point = {x: minX + (e.clientX - r.left - 6) / scale, y: maxY - (e.clientY - r.top - 6) / scale};
    const target = sigma.viewportToFramedGraph(sigma.graphToViewport(point)); sigma.getCamera().animate({x: target.x, y: target.y}, {duration: e.type === 'pointermove' ? 0 : 300});
  };
  return <canvas ref={ref} className="minimap" style={{width: W, height: H}} aria-label="Minimap: click to move the view" onPointerDown={move} onPointerMove={move}/>;
}

function MapCanvas({loaded, colorOf, visible, edgeVisible = () => true, selected, path, camera, labelThreshold, onSelect, highlight = null, areaColor, onFullscreen, fullscreen = false}: {loaded: Loaded; colorOf: (a: Obj) => string; visible: (id: string) => boolean; selected: string | null; path: string[] | null; camera: Camera; labelThreshold: number; onSelect: (id: string | null) => void; edgeVisible?: (e: string) => boolean; highlight?: string[] | null; areaColor?: (area: string) => string; onFullscreen?: () => void; fullscreen?: boolean}) {
  const container = useRef<HTMLDivElement>(null); const renderer = useRef<Sigma | null>(null); const layout = useRef<FA2Layout | null>(null);
  const areas = useRef<HTMLCanvasElement>(null); const hovered = useRef<string | null>(null);
  const [arranging, setArranging] = useState(false); const [sigma, setSigma] = useState<Sigma | null>(null); const [tick, setTick] = useState(0);
  const live = useRef({colorOf, visible, edgeVisible, selected, path, highlight, areaColor});
  live.current = {colorOf, visible, edgeVisible, selected, path, highlight, areaColor};
  const {graph} = loaded;

  const [revive, setRevive] = useState(0);
  const runLayout = useCallback((ms: number) => {
    layout.current?.kill();
    const settings = {...forceAtlas2.inferSettings(graph), barnesHutOptimize: graph.order > 1000, gravity: 1, scalingRatio: graph.order > 1000 ? 8 : 14, slowDown: 6, edgeWeightInfluence: 1};
    const fa2 = new FA2Layout(graph, {settings}); layout.current = fa2; fa2.start(); setArranging(true);
    const timer = window.setTimeout(() => {fa2.stop(); loaded.laidOut = true; setArranging(false)}, ms);
    return () => window.clearTimeout(timer);
  }, [graph, loaded]);

  useEffect(() => {
    if (!container.current) return;
    // A highlight set (linked selection from another graph) wins over the hovered/selected neighbourhood.
    const focusSet = () => {const {path, selected, highlight} = live.current; return path ? new Set(path) : highlight && !hovered.current ? new Set([...highlight, ...(selected ? [selected] : [])]) : neighbourhood(graph, hovered.current || selected)};
    let cached: {key: string; set: Set<string> | null} = {key: '', set: null};
    const currentFocus = () => {const key = (live.current.path || []).join() + '|' + (hovered.current || live.current.selected || '') + '|' + (live.current.highlight || []).join(); if (cached.key !== key) cached = {key, set: focusSet()}; return cached.set};
    const sigma = new Sigma(graph, container.current, {
      ...sigmaSettings, ...themeLabels(), labelRenderedSizeThreshold: labelThreshold,
      nodeReducer: (node, attrs) => {
        const {visible, colorOf, selected} = live.current;
        if (!visible(node)) return {...attrs, hidden: true};
        const focus = currentFocus();
        const res: Obj = {...attrs, color: colorOf(attrs)};
        if (focus && !focus.has(node)) {res.color = DIM; res.label = ''; res.zIndex = 0}
        else if (focus) {res.zIndex = 2; if (focus.size <= 40) res.forceLabel = true}
        if (node === selected) {res.highlighted = true; res.forceLabel = true; res.size = attrs.size * 1.5; res.zIndex = 3}
        return res;
      },
      // Default edges stay muted and unlabelled; the selected neighbourhood turns cyan, directional and, when small, labelled.
      edgeReducer: (edge, attrs) => {
        const {visible, path} = live.current; const [s, t] = graph.extremities(edge);
        if (!visible(s) || !visible(t) || !live.current.edgeVisible(edge)) return {...attrs, hidden: true};
        const focus = currentFocus();
        const color = edgeColors[attrs.kinds.includes('calls') ? 'calls' : attrs.kind] || EDGE;
        if (!focus) return {...attrs, color, label: null};
        const centre = hovered.current || live.current.selected; const highlighted = live.current.highlight && !hovered.current;
        const on = path ? focus.has(s) && focus.has(t) && Math.abs(path.indexOf(s) - path.indexOf(t)) === 1 : highlighted ? focus.has(s) && focus.has(t) : s === centre || t === centre;
        if (!on) return {...attrs, color: FAINT, label: null, zIndex: 0};
        return {...attrs, color: SELECT, type: 'arrow', size: 1.3, zIndex: 2, label: focus.size <= 12 ? attrs.label || attrs.kinds.join(', ') : null};
      },
    });
    sigma.on('enterNode', ({node}) => {hovered.current = node; container.current!.style.cursor = 'pointer'; sigma.refresh({skipIndexation: true})});
    sigma.on('leaveNode', () => {hovered.current = null; container.current!.style.cursor = ''; sigma.refresh({skipIndexation: true})});
    sigma.on('clickNode', ({node}) => onSelect(node));
    sigma.on('clickStage', () => onSelect(null));
    // Area boundaries and the minimap follow every render (throttled to animation frames).
    let frame = 0; let lastMini = 0;
    sigma.on('afterRender', () => {
      if (frame) return; frame = requestAnimationFrame(() => {
        frame = 0; const color = live.current.areaColor; if (color && areas.current) drawAreas(sigma, areas.current, graph, color);
        const now = performance.now(); if (now - lastMini > 120) {lastMini = now; setTick(n => n + 1)}
      });
    });
    renderer.current = sigma; setSigma(sigma); const unwatch = watchSize(sigma, container.current);
    const camera = loaded.camera || loaded.previous?.camera; if (camera) sigma.getCamera().setState(camera);
    const stop = loaded.laidOut ? undefined : runLayout(loaded.settle ?? (graph.order > 1000 ? 7000 : 2500));
    const unlost = onContextLost(container.current, () => setRevive(r => r + 1));
    return () => {unlost(); cancelAnimationFrame(frame); loaded.camera = sigma.getCamera().getState(); stop?.(); unwatch(); layout.current?.kill(); layout.current = null; sigma.kill(); renderer.current = null; setSigma(null)};
  }, [graph, revive]);

  const theme = useTheme();
  useEffect(() => {const r = renderer.current; if (!r) return; const l = themeLabels(); r.setSetting('labelColor', l.labelColor); r.setSetting('edgeLabelColor', l.edgeLabelColor); r.refresh({skipIndexation: true})}, [theme]);
  useEffect(() => {renderer.current?.refresh({skipIndexation: true})}, [colorOf, visible, edgeVisible, selected, path, highlight]);
  useEffect(() => {if (camera && renderer.current) fitCamera(renderer.current, camera.ids)}, [camera]);

  const cam = () => renderer.current?.getCamera();
  const fitAll = () => {const r = renderer.current; if (r) fitCamera(r, graph.filterNodes(id => visible(id)), 500)};
  // Keyboard: arrows pan, + and - zoom, 0 fits the graph.
  const onKey = (e: React.KeyboardEvent) => {
    const c = cam(); if (!c) return; const step = .08 * c.ratio; const s = c.getState();
    const moves: Record<string, Obj> = {ArrowLeft: {x: s.x - step}, ArrowRight: {x: s.x + step}, ArrowUp: {y: s.y + step}, ArrowDown: {y: s.y - step}};
    if (moves[e.key]) {e.preventDefault(); c.animate(moves[e.key], {duration: 120})}
    else if (e.key === '+' || e.key === '=') c.animatedZoom({duration: 200}); else if (e.key === '-') c.animatedUnzoom({duration: 200}); else if (e.key === '0') fitAll();
  };
  return <div className="map-canvas graph-grid" tabIndex={0} onKeyDown={onKey} aria-label="Knowledge map. Arrow keys pan, plus and minus zoom, 0 fits the graph.">
    {areaColor && <canvas ref={areas} className="area-layer" aria-hidden/>}
    <div ref={container} className="sigma-host" role="img" aria-label="Graph drawing"/>
    {arranging && <div className="map-hud"><button className="hud-pill" onClick={() => {layout.current?.stop(); loaded.laidOut = true; setArranging(false)}}><Loader2 size={13} className="spin"/>Arranging layout<Pause size={12}/></button></div>}
    <div className="canvas-controls" role="toolbar" aria-label="View controls">
      <button aria-label="Zoom in" title="Zoom in (+)" onClick={() => cam()?.animatedZoom({duration: 200})}><ZoomIn size={15}/></button>
      <button aria-label="Zoom out" title="Zoom out (−)" onClick={() => cam()?.animatedUnzoom({duration: 200})}><ZoomOut size={15}/></button>
      <button aria-label="Fit graph to view" title="Fit to view (0)" onClick={fitAll}><Scan size={15}/></button>
      <button aria-label="Reset view" title="Reset view" onClick={() => cam()?.animatedReset({duration: 400})}><RotateCcw size={15}/></button>
      <button aria-label="Re-run layout" title="Re-run layout" disabled={arranging} onClick={() => runLayout(graph.order > 1000 ? 4000 : 2000)}><Play size={14}/></button>
      {onFullscreen && <button aria-label={fullscreen ? 'Exit full screen' : 'Full screen'} title={fullscreen ? 'Exit full screen' : 'Full screen'} onClick={onFullscreen}>{fullscreen ? <Minimize2 size={15}/> : <Maximize2 size={15}/>}</button>}
    </div>
    <Minimap sigma={sigma} graph={graph} colorOf={colorOf} visible={visible} tick={tick}/>
  </div>;
}

function StructureCanvas({loaded, prefix, onPrefix, visible, groupColor, text, onOpenLeaf}: {loaded: Loaded; prefix: string; onPrefix: (p: string) => void; visible: (id: string) => boolean; groupColor: (key: string, keys: string[]) => string; text: typeof MODES.code; onOpenLeaf: (path: string) => void}) {
  const container = useRef<HTMLDivElement>(null);
  const {graph} = loaded;
  const [revive, setRevive] = useState(0);
  // Colours are applied at draw time, so a theme change repaints without re-laying out the overview.
  const paint = useRef(groupColor); paint.current = groupColor; const sigmaRef = useRef<Sigma | null>(null); const theme = useTheme();
  useEffect(() => {const r = sigmaRef.current; if (!r) return; const l = themeLabels(); r.setSetting('labelColor', l.labelColor); r.refresh({skipIndexation: true})}, [theme, groupColor]);
  const aggregate = useMemo(() => {
    const child = (path: string) => {if (!path.startsWith(prefix)) return null; const rest = path.slice(prefix.length); const seg = rest.split('/')[0]; return rest.includes('/') ? prefix + seg + '/' : prefix + seg};
    const counts = new Map<string, number>(); const of = new Map<string, string>();
    graph.forEachNode((id, a) => {if (!a.path || !visible(id)) return; const c = child(a.path); if (!c) return; of.set(id, c); counts.set(c, (counts.get(c) || 0) + 1)});
    const links = new Map<string, number>();
    graph.forEachEdge((_, a, s, t) => {const cs = of.get(s), ct = of.get(t); if (cs && ct && cs !== ct) links.set(cs + '\n' + ct, (links.get(cs + '\n' + ct) || 0) + 1)});
    const h = new Graph({type: 'directed'}); const keys = [...counts.keys()].sort(); const groups = keys.filter(k => k.endsWith('/'));
    keys.forEach((key, i) => {
      const folder = key.endsWith('/'), name = key.slice(prefix.length).replace(/\/$/, ''), n = counts.get(key)!, angle = i / keys.length * Math.PI * 2;
      const leaf = folder ? undefined : graph.findNode((_, a) => a.path === key);
      const colorKey = folder ? key.slice(0, -1) : leaf && graph.getNodeAttribute(leaf, 'kind') === CONCEPT ? graph.getNodeAttribute(leaf, 'module') : null;
      h.addNode(key, {label: `${name}${folder && text.root === 'Repository' ? '/' : ''} · ${n}`, folder, x: Math.cos(angle) * 100, y: Math.sin(angle) * 100, size: Math.min(28, 5 + Math.sqrt(n) * (folder ? 1.6 : 3)), colorKey, colorKeys: folder ? groups.map(g => g.slice(0, -1)) : [], color: unlinkedColor});
    });
    for (const [key, n] of links) {const [s, t] = key.split('\n'); h.addDirectedEdge(s, t, {weight: n, size: Math.min(6, .6 + Math.log2(n)), color: EDGE, type: 'arrow', count: n})}
    if (h.order > 1) forceAtlas2.assign(h, {iterations: 220, settings: {...forceAtlas2.inferSettings(h), adjustSizes: true, strongGravityMode: true, gravity: .6, scalingRatio: 25, edgeWeightInfluence: .4}});
    return h;
  }, [graph, prefix, visible, text]);

  useEffect(() => {
    if (!container.current) return;
    let hovered: string | null = null;
    const sigma = new Sigma(aggregate, container.current, {
      ...sigmaSettings, ...themeLabels(), labelRenderedSizeThreshold: 0, labelDensity: 1, labelSize: 12,
      nodeReducer: (node, raw) => {const a = {...raw, color: raw.colorKey ? paint.current(raw.colorKey, raw.colorKeys) : unlinkedColor}; if (!hovered) return a; const near = node === hovered || aggregate.areNeighbors(node, hovered); return near ? {...a, forceLabel: true, zIndex: 2} : {...a, color: DIM, zIndex: 0}},
      edgeReducer: (edge, raw) => {const a = {...raw, color: EDGE}; if (!hovered) return a; const [s, t] = aggregate.extremities(edge); return s === hovered || t === hovered ? {...a, color: SELECT, zIndex: 2} : {...a, hidden: true}},
    });
    sigma.on('enterNode', ({node}) => {hovered = node; container.current!.style.cursor = 'pointer'; sigma.refresh({skipIndexation: true})});
    sigma.on('leaveNode', () => {hovered = null; container.current!.style.cursor = ''; sigma.refresh({skipIndexation: true})});
    sigma.on('clickNode', ({node}) => aggregate.getNodeAttribute(node, 'folder') ? onPrefix(node) : onOpenLeaf(node));
    sigmaRef.current = sigma; const unwatch = watchSize(sigma, container.current); const unlost = onContextLost(container.current, () => setRevive(r => r + 1));
    return () => {unlost(); unwatch(); sigma.kill(); sigmaRef.current = null};
  }, [aggregate, revive]);

  const crumbs = prefix.split('/').filter(Boolean);
  return <div className="map-canvas">
    <nav className="structure-crumbs" aria-label="Folder path">
      <button onClick={() => onPrefix('')}>{text.root}</button>
      {crumbs.map((c, i) => <React.Fragment key={i}><ChevronRight size={13}/><button onClick={() => onPrefix(crumbs.slice(0, i + 1).join('/') + '/')}>{c}</button></React.Fragment>)}
      <span className="crumb-note">{aggregate.order} items · {aggregate.size} cross-links · {text.crumbNote}</span>
    </nav>
    {aggregate.order ? <div ref={container} className="sigma-host" aria-label="Structure" role="img"/> : <div className="explorer-empty">Nothing visible here. Adjust the filters.</div>}
  </div>;
}

function FocusCard({data}: {data: Obj}) {
  const {attrs, color, centre, more} = data;
  if (more) return <div className="focus-card more"><Handle type="target" position={Position.Left}/>+{more} more<Handle type="source" position={Position.Right}/></div>;
  return <div className={'graph-node focus-card' + (centre ? ' centre' : '') + (data.selected ? ' selected' : '')} style={{'--node-color': color} as React.CSSProperties}>
    <Handle type="target" position={Position.Left}/>
    <div className="node-kind"><span/>{attrs.kind}<span className="node-method">{data.direction}</span></div>
    <strong title={attrs.label}>{attrs.label}</strong><small>{attrs.subtitle || (attrs.path ? `${attrs.path}:${attrs.line}` : 'Domain concept')}</small>
    <Handle type="source" position={Position.Right}/>
  </div>;
}
const focusNodeTypes = {focus: FocusCard};

function FocusCanvas({graph, centre, hops, visible, hiddenEdges, colorOf, text, selected, onSelect, onRecentre}: {graph: Graph; centre: string; hops: number; visible: (id: string) => boolean; hiddenEdges: string[]; colorOf: (a: Obj) => string; text: typeof MODES.code; selected: string | null; onSelect: (id: string) => void; onRecentre: (id: string) => void}) {
  const {nodes, edges, hidden, count} = useMemo(() => {
    const edgeOk = (e: string) => graph.getEdgeAttribute(e, 'kinds').some((k: string) => !hiddenEdges.includes(k));
    const layer = new Map<string, number>([[centre, 0]]);
    for (const dir of [1, -1]) {
      let frontier = [centre];
      for (let d = 1; d <= hops; d++) {
        const next: string[] = [];
        for (const id of frontier) for (const e of dir > 0 ? graph.outEdges(id) : graph.inEdges(id)) {
          const other = graph.opposite(id, e);
          if (!edgeOk(e) || !visible(other) || (layer.has(other) && Math.abs(layer.get(other)!) <= d)) continue;
          layer.set(other, d * dir); next.push(other);
        }
        frontier = next;
      }
    }
    const columns = new Map<number, string[]>();
    for (const [id, col] of layer) columns.set(col, [...(columns.get(col) || []), id]);
    // Each hop is a band of sub-columns (8 cards tall) so large neighbourhoods stay readable.
    const cap = 24, perColumn = 8, cardW = 255, rowH = 88; const ns: Node[] = []; let hidden = 0; const shown = new Set<string>();
    const offset = {[-1]: -cardW - 70, [1]: cardW + 70} as Record<number, number>;
    for (const col of [...columns.keys()].sort((a, b) => Math.abs(a) - Math.abs(b))) {
      const ids = columns.get(col)!.sort((a, b) => graph.degree(b) - graph.degree(a));
      const keep = ids.slice(0, cap); const extra = ids.length - keep.length; hidden += extra;
      const items: (string | null)[] = [...keep, ...(extra ? [null] : [])]; const side = Math.sign(col);
      const subColumns = Math.ceil(items.length / perColumn);
      items.forEach((id, i) => {
        const sub = Math.floor(i / perColumn), inSub = Math.min(perColumn, items.length - sub * perColumn), row = i % perColumn;
        const x = side === 0 ? 0 : offset[side] + side * sub * cardW, y = (row - (inSub - 1) / 2) * rowH;
        if (id === null) {ns.push({id: 'more' + col, type: 'focus', position: {x: x + 40, y}, data: {more: extra}}); return}
        const attrs = graph.getNodeAttributes(id);
        shown.add(id); ns.push({id, type: 'focus', position: {x, y}, data: {attrs, color: colorOf(attrs), centre: id === centre, selected: id === selected, direction: col === 0 ? 'FOCUS' : col < 0 ? text.in : text.out}});
      });
      if (side) offset[side] += side * (subColumns * cardW + 90);
    }
    const es: Edge[] = [];
    graph.forEachEdge((e, a, s, t) => {
      if (!shown.has(s) || !shown.has(t) || !edgeOk(e)) return;
      const on = s === centre || t === centre;
      es.push({id: e, source: s, target: t, label: a.label || a.kinds.join(' · '), markerEnd: {type: MarkerType.ArrowClosed, width: 13, height: 13, color: on ? SELECT : EDGE}, style: {stroke: on ? SELECT : EDGE, strokeWidth: on ? 1.6 : 1}, labelStyle: {fontSize: 9, fill: graphTokens().text2}, labelBgStyle: {fill: graphTokens().panel}});
    });
    return {nodes: ns, edges: es, hidden, count: shown.size};
  }, [graph, centre, hops, visible, hiddenEdges, colorOf, text, selected]);
  return <div className="map-canvas focus-canvas">
    <ReactFlow key={centre + hops} nodes={nodes} edges={edges} nodeTypes={focusNodeTypes} fitView fitViewOptions={{maxZoom: 1}} minZoom={.1} maxZoom={1.6} nodesConnectable={false}
      onNodeClick={(_, n) => {if (!n.id.startsWith('more')) onSelect(n.id)}} onNodeDoubleClick={(_, n) => {if (!n.id.startsWith('more')) onRecentre(n.id)}} proOptions={{hideAttribution: true}}>
      <Background color={graphTokens().borderStrong} gap={20} size={1}/><Controls showInteractive={false}/>
    </ReactFlow>
    <div className="map-hud"><span className="hud-pill quiet">{count} shown{hidden ? ` · ${hidden} more hidden (most connected first)` : ''} · double-click to re-centre</span></div>
  </div>;
}

function Palette({open, onOpenChange, graph, visible, colorOf, placeholder, noun, onPick, title = 'Search the knowledge graph'}: {open: boolean; onOpenChange: (v: boolean) => void; graph: Graph; visible: (id: string) => boolean; colorOf: (a: Obj) => string; placeholder: string; noun: string; onPick: (id: string) => void; title?: string}) {
  const [q, setQ] = useState(''); const [active, setActive] = useState(0);
  const results = useMemo(() => {
    const query = q.trim().toLowerCase(); if (!query) return [] as string[];
    const scored: [number, string][] = [];
    graph.forEachNode((id, a) => {
      if (!visible(id)) return;
      const name = String(a.label).toLowerCase(); const aliases = (a.aliases || []).map((x: string) => x.toLowerCase());
      const score = name === query ? 0 : name.startsWith(query) ? 1 : name.includes(query) ? 2 : aliases.some((x: string) => x.includes(query)) ? 3 : a.kind !== CONCEPT && String(a.path || '').toLowerCase().includes(query) ? 3 : -1;
      if (score >= 0) scored.push([score * 1000 - Math.min(999, graph.degree(id)), id]);
    });
    return scored.sort((a, b) => a[0] - b[0]).slice(0, 40).map(s => s[1]);
  }, [q, graph, visible]);
  useEffect(() => setActive(0), [q]);
  const pick = (id?: string) => {if (!id) return; onPick(id); onOpenChange(false); setQ('')};
  return <Dialog.Root open={open} onOpenChange={onOpenChange}><Dialog.Portal><Dialog.Overlay className="palette-overlay"/>
    <Dialog.Content className="palette" aria-describedby={undefined}>
      <Dialog.Title className="sr-only">{title}</Dialog.Title>
      <label className="palette-input"><Search size={16}/><input autoFocus placeholder={placeholder} value={q} onChange={e => setQ(e.target.value)}
        onKeyDown={e => {if (e.key === 'ArrowDown') {e.preventDefault(); setActive(a => Math.min(results.length - 1, a + 1))} else if (e.key === 'ArrowUp') {e.preventDefault(); setActive(a => Math.max(0, a - 1))} else if (e.key === 'Enter') pick(results[active])}}/><kbd>esc</kbd></label>
      <ul className="palette-results" role="listbox">
        {results.map((id, i) => {const a = graph.getNodeAttributes(id); return <li key={id} role="option" aria-selected={i === active} className={i === active ? 'active' : ''} onMouseEnter={() => setActive(i)} onClick={() => pick(id)}>
          <i style={{background: colorOf(a)}}/><strong>{a.label}</strong><span>{a.kind}</span><small>{a.kind === CONCEPT ? a.subtitle || 'Domain concept' : a.path}</small><em>{graph.degree(id)} links</em></li>})}
        {q && !results.length && <li className="palette-empty">Nothing visible matches “{q}”. Check the filters.</li>}
        {!q && <li className="palette-empty">Type to search {graph.order.toLocaleString()} {noun}. ↑ ↓ to move, Enter to open.</li>}
      </ul>
    </Dialog.Content></Dialog.Portal></Dialog.Root>;
}

// Where a relationship came from. Confidence appears only when the model reported it.
function OriginBadge({o}: {o?: Origin}) {
  if (!o) return null;
  if (o.type === 'ai') return <span className="origin-badge ai" title={`Generated by ${o.model || 'a model'}${o.confidence != null ? ` · confidence ${Math.round(o.confidence * 100)}%` : ''}`}><Sparkles size={10}/>AI{o.confidence != null ? ` ${Math.round(o.confidence * 100)}%` : ''}</span>;
  if (o.type === 'inferred') return <span className="origin-badge inferred" title={o.basis ? 'Inferred: ' + o.basis : 'Inferred'}><GitMerge size={10}/>Inferred</span>;
  return <span className="origin-badge declared" title="Declared in code or framework metadata"><Check size={10}/>Declared{o.meaningAI ? ' · AI meaning' : ''}</span>;
}

// Relationship rows: direction, the connected node, what links them and where the link came from. Click to navigate.
function RelationList({title, graph, rows, onPick, colorOf, direction}: {title: string; graph: Graph; rows: {id: string; edge: string}[]; onPick: (id: string) => void; colorOf: (a: Obj) => string; direction: 'out' | 'in'}) {
  const [all, setAll] = useState(false);
  if (!rows.length) return null;
  const shown = all ? rows : rows.slice(0, 12);
  return <section className="inspector-section"><h4>{title}<span>{rows.length}</span></h4><ul className="relation-list">
    {shown.map(({id, edge}) => {
      const a = graph.getNodeAttributes(id); const e = graph.getEdgeAttributes(edge); const vias: string[] = e.via || e.kinds || [];
      return <li key={id + edge}><button onClick={() => onPick(id)} title={`Go to ${a.label}`}>
        {direction === 'out' ? <ArrowRight size={12} className="dir"/> : <ArrowLeft size={12} className="dir"/>}
        <i style={{background: colorOf(a)}}/><span className="rel-name">{a.label}</span>
        <small className="rel-via">{vias.map(v => e.meanings?.[v] ? `${v} — ${e.meanings[v]}` : v).join(', ') || a.kind}</small>
        <span className="rel-origins">{vias.map(v => <OriginBadge key={v} o={e.origins?.[v] || (e.origins ? undefined : {type: 'declared'})}/>)}</span>
      </button></li>;
    })}
  </ul>{rows.length > 12 && <button className="text-link small" onClick={() => setAll(!all)}>{all ? 'Show fewer' : `Show all ${rows.length}`}</button>}</section>;
}

function Neighbours({title, graph, ids, onPick, colorOf, detail}: {title: string; graph: Graph; ids: string[]; onPick: (id: string) => void; colorOf: (a: Obj) => string; detail?: (id: string) => string}) {
  const [all, setAll] = useState(false);
  if (!ids.length) return null;
  const shown = all ? ids : ids.slice(0, 8);
  return <section className="inspector-section"><h4>{title}<span>{ids.length}</span></h4><ul className="neighbour-list">
    {shown.map(id => {const a = graph.getNodeAttributes(id); return <li key={id}><button onClick={() => onPick(id)}><i style={{background: colorOf(a)}}/><span>{a.label}</span><small>{detail ? detail(id) : a.kind}</small></button></li>})}
  </ul>{ids.length > 8 && <button className="text-link small" onClick={() => setAll(!all)}>{all ? 'Show fewer' : `Show all ${ids.length}`}</button>}</section>;
}

function PathSection({graph, id, visible, colorOf, placeholder, noun, path, onPath, onPick}: {graph: Graph; id: string; visible: (id: string) => boolean; colorOf: (a: Obj) => string; placeholder: string; noun: string; path: string[] | null; onPath: (p: string[] | null) => void; onPick: (id: string) => void}) {
  const [open, setOpen] = useState(false); const [error, setError] = useState('');
  return <section className="inspector-section">
    {path ? <div className="path-result"><h4>Path<span>{path.length - 1} hops</span></h4><ol>{path.map(p => <li key={p}><button onClick={() => onPick(p)}>{graph.getNodeAttribute(p, 'label')}</button></li>)}</ol><button className="text-link small" onClick={() => onPath(null)}>Clear path</button></div>
      : <button className="btn small ghost" onClick={() => setOpen(true)}><Route size={14}/>Find path to…</button>}
    {error && <p className="path-error">{error}</p>}
    <Palette open={open} onOpenChange={setOpen} graph={graph} visible={visible} colorOf={colorOf} placeholder={placeholder} noun={noun} title="Find a path to…" onPick={to => {const p = findPath(graph, id, to, visible); if (p) {onPath(p); setError('')} else setError('No connection with the current filters.')}}/>
  </section>;
}

// Relationship rows of a node, honouring the node and relationship filters.
const relationRows = (graph: Graph, id: string, dir: 'out' | 'in', defines: boolean, visible: (id: string) => boolean, edgeVisible: (e: string) => boolean) => {
  const seen = new Set<string>(); const rows: {id: string; edge: string}[] = [];
  for (const e of dir === 'out' ? graph.outEdges(id) : graph.inEdges(id)) {
    const other = dir === 'out' ? graph.target(e) : graph.source(e);
    if (graph.getEdgeAttribute(e, 'kinds').includes('defines') !== defines || !visible(other) || !edgeVisible(e) || seen.has(other)) continue;
    seen.add(other); rows.push({id: other, edge: e});
  }
  return rows.sort((x, y) => graph.degree(y.id) - graph.degree(x.id));
};

type InspectorProps = {graph: Graph; id: string; snapshotId: string; colorOf: (a: Obj) => string; text: typeof MODES.code; visible: (id: string) => boolean; edgeVisible: (e: string) => boolean; path: string[] | null; onPath: (p: string[] | null) => void; onPick: (id: string) => void; onFocus: (id: string) => void; onShowOnMap: (id: string) => void; tab: string; onTab: (t: string) => void; onImpact: () => void; onOpenEntity: (e: Obj) => void};

// Opens the source drawer (excerpt, location, provenance) for a code entity.
const viewSource = (snapshotId: string, id: string, onOpenEntity: (e: Obj) => void, onError: (m: string) => void) =>
  fetch(`/api/entities/${encodeURIComponent(id)}?snapshot_id=${snapshotId}`).then(r => r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))).then(onOpenEntity).catch(e => onError('Source could not be loaded: ' + e.message));

function InspectorHead({kind, color, title, lines, actions}: {kind: string; color: string; title: string; lines: React.ReactNode[]; actions: React.ReactNode}) {
  return <><header className="inspector-head" style={{'--node-color': color} as React.CSSProperties}>
    <span className="kind-chip"><i/>{kind}</span><h3>{title}</h3>{lines.filter(Boolean).map((l, i) => <p key={i} className="muted">{l}</p>)}
  </header><div className="inspector-actions">{actions}</div></>;
}

function CodeInspector({graph, id, snapshotId, colorOf, text, visible, edgeVisible, path, onPath, onPick, onFocus, onShowOnMap, tab, onTab, onImpact, concepts, onOpenEntity, onOpenConcept}: InspectorProps & {concepts: Obj[]; onOpenConcept: (id: string) => void}) {
  const a = graph.getNodeAttributes(id); const isConcept = a.kind === CONCEPT;
  const [detail, setDetail] = useState<Obj | null>(null); const [error, setError] = useState('');
  useEffect(() => {
    setDetail(null); setError(''); if (isConcept) return; let live = true;
    fetch(`/api/entities/${id}?snapshot_id=${snapshotId}`).then(r => r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))).then(d => {if (live) setDetail(d)}).catch(e => live && setError('Source details could not be loaded (' + e.message + ').'));
    return () => {live = false};
  }, [id, snapshotId, isConcept]);
  const out = relationRows(graph, id, 'out', false, visible, edgeVisible), inn = relationRows(graph, id, 'in', false, visible, edgeVisible);
  const defines = relationRows(graph, id, 'out', true, visible, edgeVisible), definedIn = relationRows(graph, id, 'in', true, visible, edgeVisible);
  return <div className="inspector-body">
    <InspectorHead kind={a.kind} color={colorOf(a)} title={a.label} lines={[a.path && <span className="mono">{a.path}:{a.line}</span>, a.module && `${a.module}${a.test ? ' · test code' : ''}`]} actions={<>
      <button className="btn small primary" onClick={() => onFocus(id)}><Crosshair size={14}/>Explore connections</button>
      {!isConcept && <button className="btn small" disabled={!detail} onClick={() => detail && onOpenEntity(detail)}><FileCode2 size={14}/>View source</button>}
      {isConcept && <button className="btn small" onClick={() => onOpenConcept(id.replace(/^concept:/, ''))}><Sparkles size={14}/>Open in ontology</button>}
      <button className="btn small icon-only" aria-label="Show on map" title="Show on map" onClick={() => onShowOnMap(id)}><MapPin size={14}/></button>
      <button className="btn small icon-only" aria-label="Impact of a change" title="Impact of a change" onClick={onImpact}><Zap size={14}/></button>
    </>}/>
    <Tabs label="Inspector sections" value={tab} onChange={onTab} tabs={[['overview', 'Overview'], ['relationships', 'Relationships', out.length + inn.length + defines.length + definedIn.length], ['evidence', 'Evidence']]}/>
    <div role="tabpanel" className="inspector-panel">
      {tab === 'overview' && <>
        {isConcept && a.description && <p className="concept-description">{a.description}</p>}
        <dl className="fact-list"><div><dt>Type</dt><dd>{a.kind}</dd></div>{a.module && <div><dt>Module</dt><dd>{a.module}</dd></div>}<div><dt>Connections</dt><dd>{graph.degree(id)}</dd></div>{a.test && <div><dt>Code kind</dt><dd>Test code</dd></div>}</dl>
        {!!concepts.length ? <section className="inspector-section"><h4>Domain concepts<span>{concepts.length}</span></h4><div className="concept-chips">{concepts.map(c => <button key={c.id} title="Open in the domain ontology" onClick={() => onOpenConcept(c.id)}><Sparkles size={12}/>{c.name}</button>)}</div></section>
          : !isConcept && <p className="inspector-note">Not mapped to any domain concept.</p>}
      </>}
      {tab === 'relationships' && <>
        <PathSection graph={graph} id={id} visible={visible} colorOf={colorOf} placeholder={text.search} noun={text.noun} path={path} onPath={onPath} onPick={onPick}/>
        <RelationList title={isConcept ? 'Evidence in code' : text.outTitle} graph={graph} rows={out} onPick={onPick} colorOf={colorOf} direction="out"/>
        <RelationList title={text.inTitle} graph={graph} rows={inn} onPick={onPick} colorOf={colorOf} direction="in"/>
        <RelationList title="Defines" graph={graph} rows={defines} onPick={onPick} colorOf={colorOf} direction="out"/>
        <RelationList title="Defined in" graph={graph} rows={definedIn} onPick={onPick} colorOf={colorOf} direction="in"/>
        {!(out.length + inn.length + defines.length + definedIn.length) && <p className="inspector-note">No relationships with the current filters.</p>}
      </>}
      {tab === 'evidence' && (isConcept ? <p className="inspector-note">Overlay concept; its evidence is the code it links to (Relationships).</p> : error ? <p className="inspector-note error">{error}</p> : !detail ? <p className="inspector-note"><Loader2 size={13} className="spin"/>Loading source…</p> : <>
        <dl className="fact-list"><div><dt>Location</dt><dd className="mono">{detail.path}:{detail.line}</dd></div><div><dt>Extraction</dt><dd>{detail.method}</dd></div>{detail.status && <div><dt>Status</dt><dd>{detail.status}</dd></div>}</dl>
        {detail.excerpt ? <section className="inspector-section"><h4>Source excerpt</h4><pre className="code-excerpt">{detail.excerpt}</pre></section> : <p className="inspector-note">No excerpt recorded for this entity.</p>}
        <div className="inspector-actions"><button className="btn small" onClick={() => onOpenEntity(detail)}><FileCode2 size={14}/>Full provenance</button>{detail.github_url && <a className="btn small" href={detail.github_url} target="_blank" rel="noreferrer"><ExternalLink size={14}/>Open on GitHub</a>}</div>
      </>)}
    </div>
  </div>;
}

// Where a statement came from: code/annotations/DocTypes (free) or a model (with tier, model and confidence).
function Origin({p}: {p?: Obj}) {
  if (!p) return null;
  if (p.origin === 'llm') return <small className="origin-chip ai" title={`${p.model || ''} · confidence ${p.confidence ?? '—'}${p.cached ? ' · reused from cache' : ''}`}>AI · {p.tier}{p.confidence != null ? ` · ${Math.round(p.confidence * 100)}%` : ''}</small>;
  return <small className="origin-chip code">{p.origin === 'javadoc' ? 'Javadoc' : p.origin === 'jpa' ? 'JPA' : p.origin === 'doctype' ? 'DocType' : 'code'}</small>;
}

function ConceptInspector({graph, id, snapshotId, colorOf, text, visible, edgeVisible, path, onPath, onPick, onFocus, onShowOnMap, tab, onTab, onImpact, onOpenEntity, code, codeColors, onOpenCode, findings = []}: InspectorProps & {code?: Graph; codeColors: Record<string, string>; onOpenCode: (id: string) => void; findings?: Obj[]}) {
  const a = graph.getNodeAttributes(id); const [error, setError] = useState('');
  const out = relationRows(graph, id, 'out', false, visible, edgeVisible), inn = relationRows(graph, id, 'in', false, visible, edgeVisible);
  const evidence: string[] = (a.evidence || []).filter((e: string) => code?.hasNode(e));
  const ai = Object.entries(a.provenance || {}).filter(([, p]: [string, any]) => p?.origin === 'llm') as [string, Obj][];
  return <div className="inspector-body">
    <InspectorHead kind="Domain concept" color={colorOf(a)} title={a.label} lines={[a.module === UNLINKED ? 'Not linked to other concepts' : `${a.module} area`, a.doctype && 'Frappe DocType']} actions={<>
      <button className="btn small primary" onClick={() => onFocus(id)}><Crosshair size={14}/>Explore connections</button>
      <button className="btn small" disabled={!evidence[0]} title={evidence[0] ? 'Open the source of the main evidence' : 'No code evidence recorded'} onClick={() => viewSource(snapshotId, evidence[0], onOpenEntity, setError)}><FileCode2 size={14}/>View source</button>
      <button className="btn small icon-only" aria-label="Show on map" title="Show on map" onClick={() => onShowOnMap(id)}><MapPin size={14}/></button>
      <button className="btn small icon-only" aria-label="Impact of a change" title="Impact of a change" onClick={onImpact}><Zap size={14}/></button>
    </>}/>
    {error && <p className="inspector-note error" role="alert">{error}</p>}
    <Tabs label="Inspector sections" value={tab} onChange={onTab} tabs={[['overview', 'Overview'], ['relationships', 'Relationships', out.length + inn.length], ['evidence', 'Evidence', evidence.length]]}/>
    <div role="tabpanel" className="inspector-panel">
      {tab === 'overview' && <>
        {a.description ? <p className="concept-description">{a.description}<Origin p={a.provenance?.description}/></p> : <p className="inspector-note">No description recorded. Generate with AI can add one.</p>}
        {(a.parentId && graph.hasNode(a.parentId) || a.abstract) && <p className="concept-isa">{a.abstract && <span className="alias-chip">Abstract</span>}{a.parentId && graph.hasNode(a.parentId) && <>Is a <button className="query-link" onClick={() => onPick(a.parentId)}>{graph.getNodeAttribute(a.parentId, 'label')}</button><Origin p={a.provenance?.parent}/></>}</p>}
        {!!a.properties.length && <section className="inspector-section"><h4>Properties<span>{a.properties.length}</span></h4>
          <table className="property-table"><tbody>{a.properties.map((p: Obj) => <tr key={p.name}><td>{p.name}</td><td>{p.reference ? `${p.fieldtype || 'Link'} → ${p.reference}` : p.fieldtype || p.datatype}{p.minimum != null ? ` ≥ ${p.minimum}` : ''}</td><td>{p.required ? 'required' : ''}</td></tr>)}</tbody></table></section>}
        {a.lifecycle && <section className="inspector-section"><h4>Lifecycle<span>{a.lifecycle.field}</span></h4>
          <div className="lifecycle-states">{a.lifecycle.states.map((st: string, i: number) => <React.Fragment key={st}>{i > 0 && <ChevronRight size={12}/>}<span className={'state-chip' + (st === a.lifecycle.initial ? ' initial' : '')}>{st}</span></React.Fragment>)}</div>
          {!!a.lifecycle.transitions?.length && <ul className="plain-list">{a.lifecycle.transitions.map((t: Obj, i: number) => <li key={i}><b>{t.source} → {t.target}</b>{t.trigger ? ` · ${t.trigger}` : ''}</li>)}</ul>}<Origin p={a.provenance?.lifecycle}/></section>}
        {!!a.rules.length && <section className="inspector-section"><h4>Rules<span>{a.rules.length}</span></h4><ul className="plain-list">{a.rules.map((r: Obj) => <li key={r.id}>{r.text || r.id}<small className={'origin-chip ' + (r.origin === 'llm' ? 'ai' : 'code')}>{r.origin === 'llm' ? `AI · ${r.provenance?.tier || ''}` : r.origin === 'annotation' ? 'annotation' : 'code'}</small></li>)}</ul></section>}
        {!!a.events.length && <section className="inspector-section"><h4>Events<span>{a.events.length}</span></h4><ul className="plain-list">{a.events.map((ev: Obj, i: number) => <li key={i}><b>{ev.event}</b> · {ev.effect}<small className="origin-chip ai">AI</small></li>)}</ul></section>}
        {!!a.roles.length && <section className="inspector-section"><h4>Roles<span>{a.roles.length}</span></h4><ul className="plain-list">{a.roles.map((r: Obj, i: number) => <li key={i}><b>{r.role}</b>{r.can?.length ? ` · ${r.can.join(', ')}` : ''}</li>)}</ul></section>}
        {!!findings.length && <section className="inspector-section"><h4>Quality findings<span>{findings.length}</span></h4><FindingList findings={findings} onOpen={f => f.evidence?.[0] && onOpenCode(f.evidence[0])}/></section>}
        {!!a.aliases.length && <section className="inspector-section"><h4>Also known as</h4><div className="concept-chips">{a.aliases.map((x: string) => <span key={x} className="alias-chip">{x}</span>)}</div></section>}
      </>}
      {tab === 'relationships' && <>
        <div className="origin-legend"><OriginBadge o={{type: 'declared'}}/><OriginBadge o={{type: 'inferred'}}/><OriginBadge o={{type: 'ai'}}/></div>
        <PathSection graph={graph} id={id} visible={visible} colorOf={colorOf} placeholder={text.search} noun={text.noun} path={path} onPath={onPath} onPick={onPick}/>
        <RelationList title="Outgoing" graph={graph} rows={out} onPick={onPick} colorOf={colorOf} direction="out"/>
        <RelationList title="Incoming" graph={graph} rows={inn} onPick={onPick} colorOf={colorOf} direction="in"/>
        {!(out.length + inn.length) && <p className="inspector-note">No relationships with the current filters.</p>}
      </>}
      {tab === 'evidence' && <>
        {evidence.length ? <section className="inspector-section"><h4>Source references<span>{evidence.length}</span></h4><ul className="evidence-list">
          {evidence.map(e => {const c = code!.getNodeAttributes(e); return <li key={e}><i style={{background: codeColors[c.kind] || unlinkedColor}}/><div><strong>{c.label}</strong><small className="mono">{c.path}:{c.line} · {c.kind}</small></div>
            <button className="btn small icon-only" aria-label={`View source of ${c.label}`} title="View source" onClick={() => viewSource(snapshotId, e, onOpenEntity, setError)}><FileCode2 size={13}/></button>
            <button className="btn small icon-only" aria-label={`Show ${c.label} in the code graph`} title="Show in the code graph" onClick={() => onOpenCode(e)}><Code2 size={13}/></button></li>})}
        </ul></section> : <p className="inspector-note">No code evidence is recorded for this concept.</p>}
        {!!ai.length && <section className="inspector-section"><h4>AI statements<span>{ai.length}</span></h4><ul className="plain-list">{ai.map(([facet, p]) => <li key={facet}><b>{facet}</b> · {p.model || 'model'}{p.confidence != null ? ` · confidence ${Math.round(p.confidence * 100)}%` : ''}{p.cached ? ' · from cache' : ''}</li>)}</ul></section>}
      </>}
    </div>
  </div>;
}

function Overview({graph, items, top, note, colorOf, onPick, empty}: {graph: Graph; items: [string, number][]; top: string[]; note: React.ReactNode; colorOf: (a: Obj) => string; onPick: (id: string) => void; empty?: string}) {
  return <div className="inspector-body">
    <header className="inspector-head plain"><h3>Graph summary</h3><p className="muted">Select a node to inspect it, or press <kbd>⌘K</kbd> to search.</p></header>
    <dl className="fact-list summary">{items.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value.toLocaleString()}</dd></div>)}</dl>
    {empty && <p className="inspector-note">{empty}</p>}
    <Neighbours title="Most connected" graph={graph} ids={top} onPick={onPick} colorOf={colorOf}/>
    <p className="inspector-note">{note}</p>
  </div>;
}

// ---- Diagram: entity-relationship cards, laid out left to right ----
function ConceptCard({data}: {data: Obj}) {
  const {attrs, color, selected, limit} = data; const props: Obj[] = attrs.properties || [];
  return <div className={'er-card' + (selected ? ' selected' : '')} style={{'--node-color': color} as React.CSSProperties}>
    <Handle type="target" position={Position.Left}/>
    <header><strong title={attrs.label}>{attrs.label}</strong><small>{attrs.doctype ? 'DocType' : attrs.evidence?.length ? 'Concept' : 'External'} · {attrs.module === UNLINKED ? 'not linked' : attrs.module}</small></header>
    {props.length ? <ul>{props.slice(0, limit).map(p => <li key={p.name}><span className={p.required ? 'required' : ''}>{p.name}</span><em>{p.reference ? `→ ${p.reference}` : p.fieldtype || p.datatype}</em></li>)}</ul> : <footer>No properties recorded</footer>}
    {props.length > limit && <footer>+{props.length - limit} more properties</footer>}
    <Handle type="source" position={Position.Right}/>
  </div>;
}
const diagramNodeTypes = {concept: ConceptCard};

function DiagramCanvas({graph, visible, colorOf, hiddenEdges, selected, onSelect, onFocus, engine = 'dagre'}: {graph: Graph; visible: (id: string) => boolean; colorOf: (a: Obj) => string; hiddenEdges: string[]; selected: string | null; onSelect: (id: string) => void; onFocus: (id: string) => void; engine?: DiagramEngine}) {
  const limit = 8, width = 250, cap = 24;
  const {nodes: dagreNodes, edges, note, sizes} = useMemo(() => {
    const visibleIds = graph.filterNodes(id => visible(id));
    const byDegree = (ids: string[]) => ids.sort((a, b) => graph.degree(b) - graph.degree(a));
    let ids: string[]; let note = '';
    // An ER diagram is only readable for a few dozen entities: centre it on the selected concept, else one area.
    if (selected && graph.hasNode(selected) && visible(selected)) {
      const around = byDegree(graph.neighbors(selected).filter(visible));
      ids = [selected, ...around.slice(0, cap - 1)];
      note = `${graph.getNodeAttribute(selected, 'label')} and ${around.length} directly related concepts` + (around.length > cap - 1 ? ` (${cap - 1} most connected shown)` : '');
    } else {
      const sizes = new Map<string, number>(); for (const id of visibleIds) {const m = graph.getNodeAttribute(id, 'module'); if (m !== UNLINKED) sizes.set(m, (sizes.get(m) || 0) + 1)}
      const area = [...sizes].sort((a, b) => b[1] - a[1])[0]?.[0];
      const inArea = byDegree(visibleIds.filter(id => graph.getNodeAttribute(id, 'module') === area || sizes.size === 0));
      ids = inArea.slice(0, cap);
      note = `${area || 'All'} area: ${ids.length} of ${inArea.length} concepts` + (visibleIds.length > ids.length ? ` · select a concept or an area chip to change the diagram` : '');
    }
    const shown = new Set(ids);
    const edgeOk = (e: string) => graph.getEdgeAttribute(e, 'kinds').some((k: string) => !hiddenEdges.includes(k));
    const g = new dagre.graphlib.Graph(); g.setGraph({rankdir: 'LR', nodesep: 22, ranksep: 120, marginx: 20, marginy: 20}); g.setDefaultEdgeLabel(() => ({}));
    const height = (id: string) => {const n = (graph.getNodeAttribute(id, 'properties') || []).length; return 50 + Math.max(1, Math.min(n, limit)) * 19 + (n > limit ? 20 : 0)};
    for (const id of ids) g.setNode(id, {width, height: height(id)});
    const es: Edge[] = [];
    graph.forEachEdge((e, a, s, t) => {
      if (!shown.has(s) || !shown.has(t) || !edgeOk(e)) return;
      g.setEdge(s, t);
      const on = s === selected || t === selected; const kind = a.kinds.includes('link') ? 'link' : a.kinds.includes('contains') ? 'contains' : a.kind;
      es.push({id: e, source: s, target: t, label: a.label, markerEnd: {type: MarkerType.ArrowClosed, width: 13, height: 13, color: on ? SELECT : EDGE},
        style: {stroke: on ? SELECT : edgeColors[kind] || EDGE, strokeWidth: on ? 1.8 : 1.2, strokeDasharray: kind === 'contains' ? '5 4' : kind === 'property' ? '2 4' : undefined},
        labelStyle: {fontSize: 9.5, fill: on ? graphTokens().primaryText : graphTokens().text2}, labelBgStyle: {fill: graphTokens().panel}, zIndex: on ? 1 : 0});
    });
    dagre.layout(g);
    const ns: Node[] = ids.map(id => {const p = g.node(id); const attrs = graph.getNodeAttributes(id); return {id, type: 'concept', position: {x: p.x - width / 2, y: p.y - p.height / 2}, data: {attrs, color: colorOf(attrs), selected: id === selected, limit}}});
    return {nodes: ns, edges: es, note, sizes: ids.map(id => ({id, width, height: height(id)}))};
  }, [graph, visible, colorOf, hiddenEdges, selected]);
  // ELK (Eclipse Layout Kernel) lays the same nodes out in layers with fewer crossings; it runs asynchronously and is loaded on first use.
  const layoutKey = sizes.map(s => s.id + ':' + s.height).join('|') + '#' + edges.map(e => e.id).join('|');
  const [elk, setElk] = useState<{key: string; pos: Map<string, {x: number; y: number}>} | null>(null);
  const [elkError, setElkError] = useState('');
  useEffect(() => {
    if (engine !== 'elk' || elk?.key === layoutKey) return; let cancelled = false;
    import('elkjs/lib/elk.bundled.js').then(async ({default: ELK}) => {
      const result = await new ELK().layout({id: 'root', layoutOptions: {'elk.algorithm': 'layered', 'elk.direction': 'RIGHT', 'elk.layered.spacing.nodeNodeBetweenLayers': '110', 'elk.spacing.nodeNode': '30',
        'elk.layered.crossingMinimization.strategy': 'LAYER_SWEEP', 'elk.layered.nodePlacement.strategy': 'BRANDES_KOEPF', 'elk.edgeRouting': 'ORTHOGONAL', 'elk.layered.considerModelOrder.strategy': 'NODES_AND_EDGES'},
        children: sizes.map(s => ({id: s.id, width: s.width, height: s.height})), edges: edges.map(e => ({id: e.id, sources: [e.source], targets: [e.target]}))});
      if (!cancelled) {setElk({key: layoutKey, pos: new Map((result.children || []).map(c => [c.id, {x: c.x || 0, y: c.y || 0}]))}); setElkError('')}
    }).catch(e => !cancelled && setElkError('ELK layout failed: ' + (e as Error).message));
    return () => {cancelled = true};
  }, [engine, layoutKey]);
  const useElk = engine === 'elk' && elk?.key === layoutKey;
  const nodes = useElk ? dagreNodes.map(n => ({...n, position: elk!.pos.get(n.id) || n.position})) : dagreNodes;
  const shownEdges = useElk ? edges.map(e => ({...e, type: 'smoothstep'})) : edges;
  if (!nodes.length) return <div className="map-canvas"><div className="explorer-empty">No concepts match the filters.</div></div>;
  if (engine === 'elk' && !useElk && !elkError) return <div className="map-canvas"><div className="explorer-empty"><Loader2 className="spin"/>Laying out with ELK…</div></div>;
  return <div className="map-canvas diagram-canvas">
    <ReactFlow key={(useElk ? 'elk:' : '') + nodes.map(n => n.id).join('|').length + ':' + nodes.length} nodes={nodes} edges={shownEdges} nodeTypes={diagramNodeTypes} fitView fitViewOptions={{maxZoom: 1}} minZoom={.05} maxZoom={1.6} nodesConnectable={false}
      onNodeClick={(_, n) => onSelect(n.id)} onNodeDoubleClick={(_, n) => onFocus(n.id)} proOptions={{hideAttribution: true}}>
      <Background color={graphTokens().borderStrong} gap={20} size={1}/><Controls showInteractive={false}/>
    </ReactFlow>
    <div className="map-hud">{useElk && <span className="hud-pill">Layered · ELK</span>}{elkError && <span className="hud-pill quiet">{elkError}; showing the standard layout</span>}<span className="hud-pill quiet">{note} · {edges.length} relationships · solid = link, dashed = child table · double-click to focus</span></div>
  </div>;
}

// ---- Impact analysis: what a change to the selected concept or code entity affects ----
type ImpactItem = Obj & {id: string; name: string; because: string[]};
function ImpactPanel({snapshotId, nodeId, mode, graph, onPick, onCross, onHighlight, onFlow, onClose}: {snapshotId: string; nodeId: string; mode: ExplorerMode; graph: Graph; onPick: (id: string) => void; onCross: (layer: ExplorerMode, id: string) => void; onHighlight: (ids: string[] | null) => void; onFlow: (f: {start: string; edges: [string, string, number][]} | null) => void; onClose: () => void}) {
  const [depth, setDepth] = useState(3); const [data, setData] = useState<Obj | null>(null); const [error, setError] = useState(''); const [lit, setLit] = useState(true);
  useEffect(() => {
    let live = true; setData(null); setError('');
    fetch(`/api/impact?snapshot_id=${encodeURIComponent(snapshotId)}&node_id=${encodeURIComponent(nodeId)}&depth=${depth}`).then(async r => {const d = await r.json(); if (!r.ok) throw new Error(d.detail || 'Impact analysis failed'); return d})
      .then(d => {if (live) setData(d)}).catch(e => {if (live) setError((e as Error).message)});
    return () => {live = false};
  }, [snapshotId, nodeId, depth]);
  useEffect(() => {
    if (!data || !lit) {onHighlight(null); onFlow(null); return}
    const items: Obj[] = mode === 'domain' ? data.concepts : [...data.code, ...data.tests];
    const ids = [nodeId, ...items.map(c => c.id)];
    onHighlight(ids.filter(id => graph.hasNode(id)));
    // Each item travels from the item that reached it, so 3D views can animate the impact spreading outward.
    onFlow({start: nodeId, edges: items.filter(c => c.via && graph.hasNode(c.via) && graph.hasNode(c.id)).map(c => [c.via, c.id, c.depth] as [string, string, number])});
  }, [data, lit, mode]);
  useEffect(() => () => {onHighlight(null); onFlow(null)}, []);
  const open = (layer: ExplorerMode, id: string) => layer === mode && graph.hasNode(id) ? onPick(id) : onCross(layer, id);
  const Section = ({title, items, layer, detail}: {title: string; items: ImpactItem[]; layer: ExplorerMode; detail?: (x: ImpactItem) => string}) => !items.length ? null : <details className="impact-section" open={items.length <= 25 || title === 'Concepts affected'}>
    <summary>{title} <span>{items.length}</span></summary>
    <ul>{items.slice(0, 150).map(x => <li key={x.id}><button className="text-link" onClick={() => open(layer, x.id)} title={x.because.join('\n← ')}>{x.name}</button>
      <small>{detail ? detail(x) : x.because[0] || ''}</small></li>)}</ul>{items.length > 150 && <p className="muted">…and {items.length - 150} more</p>}</details>;
  return <div className="impact-panel">
    <button className="btn small ghost" onClick={onClose}><ArrowLeft size={14}/>Back to details</button>
    <p className="eyebrow"><Zap size={13}/>Impact of a change</p>
    <h3>{data?.start?.name || graph.getNodeAttribute(nodeId, 'label')}</h3>
    <p className="muted">Everything that depends on it, directly or through others: whatever links to, calls, imports, extends or contains it, across the domain and the code. Hover an item to see why.</p>
    <div className="segmented small" role="group" aria-label="How far to follow">{[1, 2, 3, 4].map(d => <button key={d} aria-pressed={depth === d} onClick={() => setDepth(d)}>{d} step{d > 1 ? 's' : ''}</button>)}</div>
    <label className="switch"><input type="checkbox" checked={lit} onChange={e => setLit(e.target.checked)}/><span/>Highlight on the map</label>
    {error ? <div className="alert error">{error}</div> : !data ? <div className="loading"><Loader2 className="spin"/>Following dependencies…</div> : <>
      <div className="impact-tiles">{[['Concepts', data.concepts.length], ['Code', data.code.length], ['Tests', data.tests.length], ['Rules', data.rule_total], ['Lifecycles', data.lifecycles.length], ['APIs', data.apis.length]].map(([k, n]) => <div key={k as string}><strong>{(n as number).toLocaleString()}</strong><span>{k}</span></div>)}</div>
      {data.truncated && <p className="muted">Stopped after 600 items; use fewer steps for a complete list.</p>}
      <Section title="Concepts affected" items={data.concepts} layer="domain"/>
      <Section title="Code affected" items={data.code} layer="code" detail={x => `${x.type} · ${x.because[0] || ''}`}/>
      <Section title="Tests to run" items={data.tests} layer="code" detail={x => x.path}/>
      <Section title="APIs" items={data.apis} layer="code"/>
      <Section title="Lifecycles and workflows" items={data.lifecycles} layer="domain" detail={x => x.lifecycle}/>
      {data.rules.length > 0 && <details className="impact-section" open={data.rules.length <= 15}><summary>Business rules to re-check <span>{data.rule_total}</span></summary>
        <ul>{data.rules.slice(0, 150).map((r: Obj) => <li key={r.id}><span>{r.text}</span><small>{r.concept} · {r.category}</small></li>)}</ul></details>}
    </>}
  </div>;
}

// ---- Coverage: where the ontology and the code do not yet meet ----
const SEVERITIES = ['error', 'warning', 'info'];
const severityColor: Record<string, string> = {error: 'var(--danger)', warning: 'var(--warning)', info: 'var(--text-3)'};
const runFindings = (semantic?: SemanticState) => [...(semantic?.runs.checks?.findings || []), ...(semantic?.runs.instances?.findings || [])] as Obj[];

function FindingList({findings, onOpen, limit = 80}: {findings: Obj[]; onOpen: (f: Obj) => void; limit?: number}) {
  return <><ul className="quality-list">{findings.slice(0, limit).map((f, i) => <li key={i}><button title={f.code} onClick={() => onOpen(f)}><i style={{background: severityColor[f.severity] || severityColor.info}}/><span>{f.message}</span></button></li>)}</ul>
    {findings.length > limit && <p className="coverage-more">+{findings.length - limit} more</p>}</>;
}

function QualityPanel({semantic, graph, onSelect, onOpenCode}: {semantic: SemanticState; graph: Graph; onSelect: (id: string) => void; onOpenCode: (id: string) => void}) {
  const {runs} = semantic; const findings = runFindings(semantic);
  const groups = new Map<string, Obj[]>(); for (const f of findings) groups.set(f.title, [...(groups.get(f.title) || []), f]);
  const ordered = [...groups].sort((a, b) => SEVERITIES.indexOf(a[1][0].severity) - SEVERITIES.indexOf(b[1][0].severity) || b[1].length - a[1].length);
  const count = (s: string) => findings.filter(f => f.severity === s).length;
  const open = (f: Obj) => f.concept && graph.hasNode(f.concept) ? onSelect(f.concept) : f.evidence?.[0] && onOpenCode(f.evidence[0]);
  const ops: [string, string, Obj | undefined][] = [['checks', 'Model checks', runs.checks], ['instances', 'Validate records', runs.instances], ['infer', 'Run inference', runs.infer]];
  return <section className="coverage-card wide quality-card">
    <h4><span className="quality-title"><ShieldCheck size={13}/>Model checks · Open Ontologies</span><span>{findings.length}</span></h4>
    {!semantic.available ? <p>Open Ontologies is not enabled{semantic.reason ? `: ${semantic.reason}` : '.'} Run <code>scripts/setup_open_ontologies.sh --write-env</code> and restart the server.</p> : <>
      <p>Standards-based checks of the exported RDF/OWL model: SPARQL checks of DocTypes and metadata, SHACL validation of extracted records against their DocType, and RDFS inference.</p>
      <div className="quality-actions">{ops.map(([op, label, run]) => <button key={op} className="btn small" disabled={!!semantic.busy} onClick={() => semantic.onRun(op)}>
        {semantic.busy === op ? <Loader2 className="spin" size={13}/> : <Play size={13}/>}{label}
        {run && <small>{op === 'infer' ? `${run.derived_facts?.length || 0} derived` : `${run.findings?.length || 0} found`} · {new Date(run.created_at).toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'})}</small>}</button>)}</div>
      {(runs.checks || runs.instances) && <div className="quality-counts">{SEVERITIES.map(s => <span key={s}><i style={{background: severityColor[s]}}/>{count(s)} {s}</span>)}
        {runs.instances?.validation && <span>· {runs.instances.validation.records_checked} records validated</span>}
        {runs.infer && <span>· {runs.infer.derived_facts?.length || 0} facts derived by inference</span>}</div>}
      {ordered.map(([title, list]) => <details key={title} open={list[0].severity === 'error'}><summary><i style={{background: severityColor[list[0].severity]}}/><b>{title}</b><span>{list.length}</span></summary><FindingList findings={list} onOpen={open}/></details>)}
      {!!runs.infer?.derived_facts?.length && <details><summary><i style={{background: 'var(--success)'}}/><b>Derived by inference</b><span>{runs.infer.derived_facts.length}</span></summary>
        <ul className="quality-list">{runs.infer.derived_facts.slice(0, 80).map((f: Obj, i: number) => <li key={i}><button onClick={() => f.kind === 'concept superclass' && graph.hasNode(f.subject_id) ? onSelect(f.subject_id) : f.entity && onOpenCode(f.entity)}><i style={{background: 'var(--success)'}}/><span>{f.subject} is a {f.type}</span><small>{f.kind}</small></button></li>)}</ul></details>}
      {!runs.checks && !runs.instances && <p className="coverage-ok">Not run yet for this snapshot.</p>}
      {(runs.checks || runs.instances) && !findings.length && <p className="coverage-ok">No findings.</p>}
    </>}
  </section>;
}

const termText = (c: Obj, graph: Graph, code?: Graph) => {
  if (c.kind === 'concept') return graph.hasNode(c.value) ? graph.getNodeAttribute(c.value, 'label') : c.value;
  if (c.kind === 'entity') return code?.hasNode(c.value) ? code.getNodeAttribute(c.value, 'label') : c.value;
  if (c.kind === 'field') {const [concept, field] = String(c.value).split('/'); return `${graph.hasNode(concept) ? graph.getNodeAttribute(concept, 'label') : concept}.${field}`}
  if (c.kind === 'module') return `${c.value} module`;
  return String(c.value);
};

function QueryView({sourceId, semantic, graph, code, onSelect, onOpenCode}: {sourceId: string; semantic?: SemanticState; graph: Graph; code?: Graph; onSelect: (id: string) => void; onOpenCode: (id: string) => void}) {
  const [samples, setSamples] = useState<Obj[]>([]);
  const [query, setQuery] = useState(() => {try {return localStorage.getItem('onto.sparql') || ''} catch {return ''}});
  const [reasoning, setReasoning] = useState(false);
  const [result, setResult] = useState<Obj | null>(null); const [error, setError] = useState(''); const [running, setRunning] = useState(false);
  useEffect(() => {fetch('/api/semantic').then(r => r.json()).then(d => {setSamples(d.samples || []); if (d.samples?.[0]) setQuery(q => q || d.samples[0].query)}).catch(() => {})}, []);
  useEffect(() => {try {localStorage.setItem('onto.sparql', query)} catch {}}, [query]);
  const run = async () => {
    if (!query.trim() || running) return; setRunning(true); setError('');
    try {
      const r = await fetch('/api/semantic/query?source_id=' + encodeURIComponent(sourceId), {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({query, reasoning})});
      const d = await r.json(); if (!r.ok) throw new Error(d.detail || 'Query failed'); setResult(d);
    } catch (e) {setError((e as Error).message); setResult(null)} finally {setRunning(false)}
  };
  const cell = (c: Obj) => {
    const label = termText(c, graph, code);
    if (c.kind === 'concept' && graph.hasNode(c.value)) return <button className="query-link" onClick={() => onSelect(c.value)}>{label}</button>;
    if (c.kind === 'entity' && code?.hasNode(c.value)) return <button className="query-link" title={code.getNodeAttribute(c.value, 'path')} onClick={() => onOpenCode(c.value)}>{label}</button>;
    return <span className={c.kind === 'literal' ? '' : 'query-term'} title={c.kind === 'literal' ? undefined : c.kind}>{label}</span>;
  };
  if (!semantic?.available) return <div className="explorer-empty">SPARQL needs Open Ontologies{semantic?.reason ? `: ${semantic.reason}` : '.'} Run scripts/setup_open_ontologies.sh --write-env and restart the server.</div>;
  return <div className="map-canvas query-view">
    <div className="query-editor">
      <div className="query-bar">
        <select aria-label="Example queries" value="" onChange={e => {const s = samples[Number(e.target.value)]; if (s) {setQuery(s.query); setReasoning(!!s.reasoning)}}}><option value="">Example queries…</option>{samples.map((s, i) => <option key={i} value={i}>{s.title}</option>)}</select>
        <label className="switch"><input type="checkbox" checked={reasoning} onChange={e => setReasoning(e.target.checked)}/><span/>Apply RDFS inference first</label>
        <button className="btn small primary" disabled={running || !query.trim()} onClick={run}>{running ? <Loader2 className="spin" size={13}/> : <Play size={13}/>}Run<kbd>⌘↵</kbd></button>
      </div>
      <textarea spellCheck={false} aria-label="SPARQL query" value={query} onChange={e => setQuery(e.target.value)} onKeyDown={e => {if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {e.preventDefault(); run()}}}/>
      <p className="query-hint">Read-only SPARQL over the exported ontology, records and code graph, run by Open Ontologies. Prefixes <code>oe:</code> <code>rdfs:</code> <code>owl:</code> <code>sh:</code> <code>skos:</code> <code>xsd:</code> are predefined.</p>
    </div>
    {error && <div className="query-error">{error}</div>}
    {result && <div className="query-results">
      <div className="query-meta">{result.boolean != null ? `Answer: ${result.boolean}` : `${result.total.toLocaleString()} row${result.total === 1 ? '' : 's'}`}{result.truncated ? ' · first 1,000 shown' : ''} · {Math.round(result.duration_ms)} ms{result.reasoning ? ' · with RDFS inference' : ''}</div>
      {!!result.rows.length && <div className="query-table"><table><thead><tr>{result.variables.map((v: string) => <th key={v}>{v}</th>)}</tr></thead>
        <tbody>{result.rows.map((row: Obj, i: number) => <tr key={i}>{result.variables.map((v: string) => <td key={v}>{row[v] ? cell(row[v]) : ''}</td>)}</tr>)}</tbody></table></div>}
    </div>}
  </div>;
}

function CoverageView({graph, code, mapped, colorOf, codeColors, onSelect, onOpenCode, semantic}: {graph: Graph; code?: Graph; mapped: Map<string, Obj[]>; colorOf: (a: Obj) => string; codeColors: Record<string, string>; onSelect: (id: string) => void; onOpenCode: (id: string) => void; semantic?: SemanticState}) {
  const data = useMemo(() => {
    const concepts = graph.mapNodes((id, a) => ({id, a}));
    const docBacked = concepts.filter(c => c.a.doctype), withEvidence = concepts.filter(c => c.a.evidence?.length);
    const classes: Obj[] = [], doctypes: Obj[] = [];
    code?.forEachNode((id, a) => {if (a.test) return; if (a.kind === 'Class') classes.push({id, ...a}); if (a.kind === 'Model' && String(a.path).endsWith('.json')) doctypes.push({id, ...a})});
    const uncovered = classes.filter(c => !mapped.has(c.id));
    const byModule = new Map<string, Obj[]>(); for (const c of uncovered) byModule.set(c.module, [...(byModule.get(c.module) || []), c]);
    return {
      total: concepts.length,
      metrics: [
        {label: 'Code classes linked to a concept', value: classes.length - uncovered.length, of: classes.length},
        {label: 'DocTypes represented as concepts', value: doctypes.filter(d => mapped.has(d.id)).length, of: doctypes.length},
        {label: 'Concepts backed by a DocType', value: docBacked.length, of: concepts.length},
        {label: 'Concepts linked to other concepts', value: concepts.filter(c => graph.degree(c.id)).length, of: concepts.length},
      ],
      aiOnly: withEvidence.filter(c => !c.a.doctype).map(c => c.id),
      external: concepts.filter(c => !c.a.evidence?.length).map(c => c.id),
      thin: withEvidence.filter(c => c.a.evidence.length <= 1).map(c => c.id),
      unlinked: concepts.filter(c => !graph.degree(c.id)).map(c => c.id),
      uncovered: [...byModule].sort((a, b) => b[1].length - a[1].length),
    };
  }, [graph, code, mapped]);
  const ConceptList = ({title, hint, ids}: {title: string; hint: string; ids: string[]}) => <section className="coverage-card">
    <h4>{title}<span>{ids.length}</span></h4><p>{hint}</p>
    <ul className="neighbour-list">{ids.slice(0, 40).map(id => {const a = graph.getNodeAttributes(id); return <li key={id}><button onClick={() => onSelect(id)}><i style={{background: colorOf(a)}}/><span>{a.label}</span><small>{a.evidence?.length || 0} evidence</small></button></li>})}</ul>
    {ids.length > 40 && <p className="coverage-more">+{ids.length - 40} more</p>}
    {!ids.length && <p className="coverage-ok">Nothing here.</p>}
  </section>;
  return <div className="map-canvas coverage-view">
    <div className="coverage-metrics">{data.metrics.map(m => {const pct = m.of ? Math.round(m.value / m.of * 100) : 0; return <div key={m.label} className="coverage-metric">
      <span>{m.label}</span><strong>{pct}%</strong><small>{m.value.toLocaleString()} of {m.of.toLocaleString()}</small><div className="coverage-bar"><i style={{width: pct + '%'}}/></div></div>})}</div>
    <div className="coverage-grid">
      {semantic && <QualityPanel semantic={semantic} graph={graph} onSelect={onSelect} onOpenCode={onOpenCode}/>}
      <section className="coverage-card wide">
        <h4>Code with no concept<span>{data.uncovered.reduce((n, [, l]) => n + l.length, 0)}</span></h4><p>Non-test classes that no concept points to, by module. Candidates for new concepts, or helpers that need none.</p>
        {data.uncovered.slice(0, 8).map(([module, list]) => <details key={module}><summary><b>{module}</b><span>{list.length}</span></summary><ul className="neighbour-list">
          {list.slice(0, 60).map((c: Obj) => <li key={c.id}><button title={`${c.path}:${c.line}`} onClick={() => onOpenCode(c.id)}><i style={{background: codeColors[c.kind]}}/><span>{c.label}</span><small>{String(c.path).split('/').slice(-2, -1)[0]}</small></button></li>)}
        </ul></details>)}
      </section>
      <ConceptList title="Concepts without a DocType" hint="Backed only by AI interpretation of code or docs. Check that each is a real business entity." ids={data.aiOnly}/>
      <ConceptList title="Thin evidence" hint="Concepts supported by a single piece of code or documentation." ids={data.thin}/>
      <ConceptList title="No links" hint="Concepts not connected to any other concept." ids={data.unlinked}/>
      <ConceptList title="Defined outside this repository" hint="Referenced DocTypes (e.g. from Frappe or ERPNext) with no code here." ids={data.external}/>
    </div>
  </div>;
}

// ---- Side by side: domain ontology and code graph with linked selection ----
export function SplitExplorer({snapshotId, colors, mappings, ontology, onOpenEntity, onCrossLink, revision = ''}: {revision?: string; snapshotId: string; colors: Record<string, string>; mappings: Obj[]; ontology: Obj | null; onOpenEntity: (e: Obj) => void; onCrossLink: (layer: ExplorerMode, id: string) => void}) {
  const theme = useTheme();
  const [inspectorTab, setInspectorTab] = useState('overview');
  const domain = useExplorerData('domain', snapshotId, ontology, mappings, revision), code = useExplorerData('code', snapshotId, ontology, mappings, revision);
  const [selection, setSelection] = useState<{side: ExplorerMode; id: string} | null>(null);
  const [cameras, setCameras] = useState<{domain: Camera; code: Camera}>({domain: null, code: null});
  const [search, setSearch] = useState<ExplorerMode | null>(null);
  const domainGraph = domain.loaded?.graph, codeGraph = code.loaded?.graph;
  const codeVisible = useCallback((id: string) => !!codeGraph?.hasNode(id) && !codeGraph.getNodeAttribute(id, 'test') && codeGraph.getNodeAttribute(id, 'kind') !== CONCEPT, [codeGraph]);
  const domainVisible = useCallback((id: string) => !!domainGraph?.hasNode(id), [domainGraph]);
  const domainColor = useCallback((a: Obj) => colorFor(a.module, domain.loaded?.modules || []), [domain.loaded, theme]);
  const codeColor = useCallback((a: Obj) => colors[a.kind] || unlinkedColor, [colors, theme]);
  // The counterpart of the selection: a concept's code evidence, or the concepts a code entity supports.
  const counterpart = useMemo(() => {
    if (!selection || !domainGraph || !codeGraph) return [] as string[];
    return selection.side === 'domain' ? (domainGraph.hasNode(selection.id) ? (domainGraph.getNodeAttribute(selection.id, 'evidence') || []).filter(codeVisible) : [])
      : (code.mapped.get(selection.id) || []).map(c => c.id).filter(id => domainGraph.hasNode(id));
  }, [selection, domainGraph, codeGraph, code.mapped, codeVisible]);
  const select = useCallback((side: ExplorerMode, id: string | null) => {
    if (!id) {setSelection(null); return}
    setSelection({side, id});
    const other: ExplorerMode = side === 'domain' ? 'code' : 'domain';
    const linked = side === 'domain' ? (domainGraph?.getNodeAttribute(id, 'evidence') || []).filter(codeVisible) : (code.mapped.get(id) || []).map(c => c.id).filter(x => domainGraph?.hasNode(x));
    const own = side === 'domain' ? domainGraph : codeGraph; const ownVisible = side === 'domain' ? domainVisible : codeVisible;
    setCameras(c => ({...c, [side]: {ids: [id, ...(own?.hasNode(id) ? own.neighbors(id).filter(ownVisible).slice(0, 60) : [])], nonce: Date.now()}, ...(linked.length ? {[other]: {ids: linked, nonce: Date.now() + 1}} : {})}));
  }, [domainGraph, codeGraph, codeVisible, domainVisible, code.mapped]);

  if (domain.error || code.error) return <div className="explorer-empty error">{domain.error || code.error}</div>;
  if (!domain.loaded || !code.loaded || !domainGraph || !codeGraph) return <div className="explorer-empty"><Loader2 className="spin"/>Loading both graphs…</div>;
  const inspector = selection && (selection.side === 'domain' ? domainGraph : codeGraph).hasNode(selection.id) ? selection : null;
  const common = {snapshotId, path: null, onPath: () => {}, edgeVisible: () => true, tab: inspectorTab, onTab: setInspectorTab, onImpact: () => onCrossLink(inspector!.side, inspector!.id), onOpenEntity};
  const pane = (side: ExplorerMode) => {
    const loaded = side === 'domain' ? domain.loaded! : code.loaded!; const graph = loaded.graph;
    const mine = selection?.side === side ? selection.id : null;
    return <div className="split-pane">
      <header><span className="split-title">{side === 'domain' ? <Shapes size={14}/> : <Code2 size={14}/>}{side === 'domain' ? 'Domain ontology' : 'Code graph'}<small>{graph.order.toLocaleString()} {side === 'domain' ? 'concepts' : 'entities'}{side === 'code' ? ' · tests hidden' : ''}</small></span>
        <button className="palette-trigger compact" onClick={() => setSearch(side)}><Search size={13}/><span>Search…</span></button></header>
      <MapCanvas loaded={loaded} colorOf={side === 'domain' ? domainColor : codeColor} visible={side === 'domain' ? domainVisible : codeVisible} selected={mine} path={null}
        camera={cameras[side]} labelThreshold={MODES[side].labelThreshold} onSelect={id => select(side, id)} highlight={selection && selection.side !== side ? counterpart : null}/>
    </div>;
  };
  return <div className="explorer split-explorer">
    <div className="split-hint"><span>Select a concept to light up the code behind it, or select code to see which concepts it supports.{selection && <> <b>{counterpart.length}</b> linked {selection.side === 'domain' ? 'code entities' : 'concepts'}.</>}</span>
      <InfoTip label="About linked views">The left map shows concepts by area; the right map shows code (tests hidden). Selecting on either side highlights its counterpart on the other, and opens the inspector here.</InfoTip></div>
    <div className={'split-body' + (inspector ? '' : ' no-inspector')}>
      {pane('domain')}{pane('code')}
      {/* The inspector column appears only with a selection, so both maps use the full width otherwise. */}
      {inspector && <aside className="inspector" aria-label="Inspector">
        <button className="inspector-close" aria-label="Close inspector" onClick={() => select('domain', null)}><X size={16}/></button>
          {inspector.side === 'domain'
            ? <ConceptInspector key={inspector.id} {...common} graph={domainGraph} id={inspector.id} colorOf={domainColor} text={MODES.domain} visible={domainVisible} onPick={id => select('domain', id)} onFocus={id => onCrossLink('domain', id)} onShowOnMap={id => select('domain', id)} code={codeGraph} codeColors={colors} onOpenCode={id => select('code', id)}/>
            : <CodeInspector key={inspector.id} {...common} graph={codeGraph} id={inspector.id} colorOf={codeColor} text={MODES.code} visible={codeVisible} onPick={id => select('code', id)} onFocus={id => onCrossLink('code', id)} onShowOnMap={id => select('code', id)} concepts={code.mapped.get(inspector.id) || []} onOpenConcept={id => select('domain', id)}/>}
      </aside>}
    </div>
    {search && <Palette open onOpenChange={open => {if (!open) setSearch(null)}} graph={search === 'domain' ? domainGraph : codeGraph} visible={search === 'domain' ? domainVisible : codeVisible} colorOf={search === 'domain' ? domainColor : codeColor} placeholder={MODES[search].search} noun={MODES[search].noun} onPick={id => select(search, id)}/>}
  </div>;
}

// Loads the code graph (cached per snapshot) and, for the domain layer, builds the concept graph from it.
function useExplorerData(mode: ExplorerMode, snapshotId: string, ontology: Obj | null, mappings: Obj[], revision = '') {
  // The page polls state, so ontology/mappings arrive as new objects; only a change in content should rebuild.
  const dataKey = `${ontology?.id}:${ontology?.concepts?.length || 0}:${ontology?.relationships?.length || 0}:${mappings.length}`;
  const latest = useRef({ontology, mappings}); latest.current = {ontology, mappings};
  const [loaded, setLoaded] = useState<Loaded | null>(null); const [error, setError] = useState('');
  useEffect(() => {
    let live = true; setError('');
    const key = mode === 'code' ? `code:${snapshotId}:${revision}` : `domain:${snapshotId}:${revision}:${dataKey}`;
    const cached = loadedGraphs.get(key); if (cached) {setLoaded(cached); return}
    // A live refresh keeps the current picture on screen until the new version is ready.
    if (!previousFor(`${mode}:${snapshotId}`)) setLoaded(null);
    (mode === 'code' ? loadCode(snapshotId, revision) : shared(key, () => loadCode(snapshotId, revision).then(code => loadedGraphs.get(key) || remember('domain:' + snapshotId, key, buildDomainGraph(latest.current.ontology, latest.current.mappings, code.graph, previousFor('domain:' + snapshotId))))))
      .then(g => {if (live) setLoaded(g)}).catch(e => live && setError(e.message));
    return () => {live = false};
  }, [mode, snapshotId, revision, mode === 'domain' ? dataKey : '']);
  // Code entity id -> concepts it is evidence for.
  const mapped = useMemo(() => {const {ontology, mappings} = latest.current; const m = new Map<string, Obj[]>(); const byId = new Map<string, Obj>((ontology?.concepts || []).map((c: Obj) => [c.id, c])); for (const x of mappings) {const c = byId.get(x.concept_id); if (c) m.set(x.entity_id, [...(m.get(x.entity_id) || []).filter(y => y.id !== c.id), c])} return m}, [dataKey]);
  return {loaded, error, mapped};
}

export type ExplorerRequest = {kind: 'select' | 'area'; id: string; nonce: number} | null;
export default function KnowledgeExplorer({mode, snapshotId, view, onView, colors, mappings, ontology, unresolved, focusRequest, onFocusHandled, onOpenEntity, onCrossLink, semantic, revision = '', live, toolbarStart, request, onRequestHandled, fullscreen = false, onFullscreen}: {mode: ExplorerMode; snapshotId: string; view: ExplorerView; onView: (v: ExplorerView) => void; colors: Record<string, string>; mappings: Obj[]; ontology: Obj | null; unresolved: number; focusRequest: {id: string; nonce: number} | null; onFocusHandled: () => void; onOpenEntity: (e: Obj) => void; onCrossLink: (layer: ExplorerMode, id: string) => void; semantic?: SemanticState; revision?: string; live?: LiveState; toolbarStart?: React.ReactNode; request?: ExplorerRequest; onRequestHandled?: () => void; fullscreen?: boolean; onFullscreen?: () => void}) {
  const theme = useTheme();
  const text = MODES[mode];
  const defaults: Filters = {hiddenKinds: [], hiddenEdges: [], hideTests: true, module: '', onlyMapped: false};
  const [selected, setSelected] = useState<string | null>(null); const [path, setPath] = useState<string[] | null>(null);
  const [impactFor, setImpactFor] = useState<string | null>(null); const [impactHighlight, setImpactHighlight] = useState<string[] | null>(null); const [impactFlow, setImpactFlow] = useState<{start: string; edges: [string, string, number][]} | null>(null);
  const [heat, setHeat] = useState<Record<string, number[]> | null>(null);
  const [camera, setCamera] = useState<Camera>(null); const [paletteOpen, setPaletteOpen] = useState(false);
  const [filters, setFilters] = useState<Filters>(() => {try {return {...defaults, ...JSON.parse(localStorage.getItem('onto.explorerFilters.' + mode) || '{}')}} catch {return defaults}});
  const [colorBy, setColorBy] = useState<'type' | 'module'>(mode === 'domain' ? 'module' : 'type'); const [overlay, setOverlay] = useState(false);
  const [mapEngine, setMapEngine] = useState<MapEngine>(() => stored(`onto.engine.${mode}.map`, MAP_ENGINES[mode], 'standard'));
  const [diagramEngine, setDiagramEngine] = useState<DiagramEngine>(() => stored('onto.engine.diagram', ['dagre', 'elk'], 'dagre'));
  const engine: MapEngine = MAP_ENGINES[mode].includes(mapEngine) ? mapEngine : 'standard';
  // Git hotspots colour the code city and size the landscape when the history has been read (Outputs → Hotspots).
  useEffect(() => {
    if (mode !== 'code' || !['city', 'deck', 'terrain'].includes(engine) || heat) return; let live = true;
    fetch('/api/hotspots/paths?snapshot_id=' + encodeURIComponent(snapshotId)).then(r => r.ok ? r.json() : null).then(d => {if (live && d?.files && Object.keys(d.files).length) setHeat(d.files)}).catch(() => {});
    return () => {live = false};
  }, [mode, engine, snapshotId]);
  useEffect(() => {if (impactFor && impactFor !== selected) {setImpactFor(null); setImpactHighlight(null); setImpactFlow(null)}}, [selected]);
  const edgeColorOf = useCallback((a: Obj) => edgeColors[a.kinds?.includes('calls') ? 'calls' : a.kind] || EDGE, [theme]);
  const [prefix, setPrefix] = useState(''); const [centre, setCentre] = useState<string | null>(null); const [hops, setHops] = useState(1); const [trail, setTrail] = useState<string[]>([]);
  const [version, setVersion] = useState(0);
  const [inspectorTab, setInspectorTab] = useState('overview'); const [filtersOpen, setFiltersOpen] = useState(false);
  const narrow = useMedia('(max-width: 1100px)');
  const [inspectorOpen, setInspectorOpen] = useState(() => !window.matchMedia('(max-width: 1100px)').matches && localStorage.getItem('onto.inspectorOpen') !== '0');
  const [inspectorWidth, setInspectorWidth] = useState(() => Math.min(520, Math.max(300, Number(localStorage.getItem('onto.inspectorWidth')) || 340)));
  useEffect(() => {if (!narrow) try {localStorage.setItem('onto.inspectorOpen', inspectorOpen ? '1' : '0')} catch {}}, [inspectorOpen, narrow]);
  useEffect(() => {try {localStorage.setItem('onto.inspectorWidth', String(inspectorWidth))} catch {}}, [inspectorWidth]);
  const prefixChosen = useRef(false);

  useEffect(() => {try {localStorage.setItem('onto.explorerFilters.' + mode, JSON.stringify(filters))} catch {}}, [filters, mode]);
  const {loaded, error, mapped} = useExplorerData(mode, snapshotId, ontology, mappings, revision);
  const findingsFor = useMemo(() => {const m = new Map<string, Obj[]>(); for (const f of runFindings(semantic)) if (f.concept) m.set(f.concept, [...(m.get(f.concept) || []), f]); return m}, [semantic]);
  // A live refresh keeps the selection and path when their nodes still exist.
  useEffect(() => {const g = loaded?.graph; if (!g) return; setSelected(s => s && g.hasNode(s) ? s : null); setPath(p => p && p.every(n => g.hasNode(n)) ? p : null)}, [loaded]);
  const graph = loaded?.graph;

  // Code-graph domain overlay: concept nodes placed at the centre of their code evidence.
  useEffect(() => {
    if (!graph || mode !== 'code') return;
    graph.filterNodes((_, a) => a.kind === CONCEPT).forEach(n => graph.dropNode(n));
    if (overlay) {
      const evidence = new Map<string, string[]>();
      for (const [entity, concepts] of mapped) if (graph.hasNode(entity)) for (const c of concepts) evidence.set(c.id, [...(evidence.get(c.id) || []), entity]);
      for (const [conceptId, ids] of evidence) {
        const c = (ontology?.concepts || []).find((x: Obj) => x.id === conceptId); const key = 'concept:' + conceptId;
        const x = ids.reduce((s, i) => s + graph.getNodeAttribute(i, 'x'), 0) / ids.length, y = ids.reduce((s, i) => s + graph.getNodeAttribute(i, 'y'), 0) / ids.length;
        graph.addNode(key, {label: c?.name || conceptId, kind: CONCEPT, description: c?.description, x: x + 1, y: y + 1, size: Math.min(18, 8 + ids.length), module: 'Domain ontology', test: false});
        for (const i of ids) graph.addDirectedEdgeWithKey(key + '>' + i, key, i, {kind: 'evidence', kinds: ['evidence'], method: 'human-approved mapping', type: 'line'});
      }
    }
    if (selected && !graph.hasNode(selected)) setSelected(null);
    setVersion(v => v + 1);
  }, [graph, overlay, mapped, mode]);

  const visible = useCallback((id: string) => {
    if (!graph || !graph.hasNode(id)) return false;
    const a = graph.getNodeAttributes(id);
    if (mode === 'code' && a.kind === CONCEPT) return overlay;
    if (filters.hiddenKinds.includes(a.kind) || (filters.hideTests && a.test) || (filters.module && a.module !== filters.module)) return false;
    return mode !== 'code' || !filters.onlyMapped || mapped.has(id);
  }, [graph, filters, overlay, mapped, mode, version]);

  // Relationship filters: an edge stays visible while any of its kinds is shown.
  const edgeVisible = useCallback((e: string) => !!graph?.hasEdge(e) && (graph.getEdgeAttribute(e, 'kinds') as string[]).some(k => !filters.hiddenEdges.includes(k)), [graph, filters.hiddenEdges, version]);
  const groupColor = useCallback((key: string, keys: string[]) => colorFor(key, loaded?.modules.includes(key) ? loaded.modules : keys), [loaded, theme]);
  const colorOf = useCallback((a: Obj) => mode === 'domain' ? colorFor(a.module, loaded?.modules || []) : a.kind === CONCEPT ? conceptColor : colorBy === 'module' ? colorFor(a.module, loaded?.modules || []) : colors[a.kind] || unlinkedColor, [theme, mode, colorBy, colors, loaded]);

  const counts = useMemo(() => {
    const kinds: Record<string, number> = {}; const edges: Record<string, number> = {};
    if (graph) {graph.forEachNode((_, a) => {if (mode === 'domain' || a.kind !== CONCEPT) kinds[a.kind] = (kinds[a.kind] || 0) + 1}); graph.forEachEdge((_, a) => {for (const k of a.kinds) edges[k] = (edges[k] || 0) + 1})}
    return {kinds, edges};
  }, [graph, version, mode]);

  const stats = useMemo(() => {
    if (!graph) return {visible: 0, edges: 0, crossFile: 0, areas: 0, unlinked: 0, top: [] as string[]};
    let visibleCount = 0, edges = 0, crossFile = 0, unlinked = 0; const ranked: [number, string][] = []; const areas = new Set<string>();
    graph.forEachNode((id, a) => {
      if (mode === 'domain' && a.module === UNLINKED) unlinked++;
      if (!visible(id)) return; visibleCount++;
      if (mode === 'domain') {if (a.module !== UNLINKED) areas.add(a.module); ranked.push([graph.degree(id), id])}
      else if (a.kind !== 'File' && a.kind !== CONCEPT) ranked.push([graph.degree(id), id]);
    });
    graph.forEachEdge((e, a, s, t, sa, ta) => {if (visible(s) && visible(t) && edgeVisible(e)) {edges++; if (sa.path && ta.path && sa.path !== ta.path && sa.kind !== CONCEPT) crossFile++}});
    return {visible: visibleCount, edges, crossFile, areas: areas.size, unlinked, top: ranked.sort((x, y) => y[0] - x[0]).slice(0, 12).map(r => r[1])};
  }, [graph, visible, edgeVisible, mode]);

  useEffect(() => {
    if (!graph || prefixChosen.current) return; prefixChosen.current = true;
    let current = '';
    for (;;) {
      const counts = new Map<string, number>(); let total = 0;
      graph.forEachNode((_, a) => {if (!a.path?.startsWith(current)) return; total++; const rest = a.path.slice(current.length); if (rest.includes('/')) {const k = current + rest.split('/')[0] + '/'; counts.set(k, (counts.get(k) || 0) + 1)}});
      const [top, n] = [...counts].sort((a, b) => b[1] - a[1])[0] || ['', 0];
      if (!top || n < total * .6) break; current = top;
    }
    setPrefix(current);
  }, [graph]);
  const select = useCallback((id: string | null) => {setSelected(id); if (!id) setPath(null)}, []);
  // Frame a node with its visible neighbours rather than zooming onto the node alone.
  const frame = useCallback((id: string) => setCamera({ids: [id, ...(graph?.neighbors(id) || []).filter(visible).slice(0, 80)], nonce: Date.now()}), [graph, visible]);
  const showOnMap = useCallback((id: string) => {setSelected(id); onView('map'); frame(id)}, [onView, frame]);
  const focus = useCallback((id: string) => {if (centre && centre !== id) setTrail(t => [...t, centre].slice(-12)); setCentre(id); setSelected(id); onView('focus')}, [centre, onView]);
  const pick = useCallback((id: string) => {if (!graph?.hasNode(id)) return; setSelected(id); if (view === 'focus') focus(id); else if (view === 'map') frame(id); else showOnMap(id)}, [graph, view, focus, frame, showOnMap]);

  useEffect(() => {
    if (!focusRequest || !graph) return;
    if (graph.hasNode(focusRequest.id)) {
      // Make sure filters do not hide the requested node.
      const a = graph.getNodeAttributes(focusRequest.id);
      if ((filters.hideTests && a.test) || filters.hiddenKinds.includes(a.kind) || (filters.module && filters.module !== a.module) || (mode === 'code' && filters.onlyMapped && !mapped.has(focusRequest.id))) setFilters({...filters, hideTests: filters.hideTests && !a.test, hiddenKinds: filters.hiddenKinds.filter(k => k !== a.kind), module: '', onlyMapped: false});
      focus(focusRequest.id);
    }
    onFocusHandled();
  }, [focusRequest, graph]);
  useEffect(() => {if (path) setCamera({ids: path, nonce: Date.now()})}, [path]);
  // Progressive disclosure: a very large code graph opens on files, classes and APIs; functions and columns stay one click away in Filters.
  const [disclosed, setDisclosed] = useState(false);
  useEffect(() => {
    if (!graph || mode !== 'code' || disclosed || localStorage.getItem('onto.codeShowAll') === '1' || localStorage.getItem('onto.explorerFilters.code')?.includes('"hiddenKinds":["')) return;
    if (graph.order > 2500) {setFilters(f => ({...f, hiddenKinds: [...new Set([...f.hiddenKinds, 'Function', 'Column'])]})); setDisclosed(true)}
  }, [graph, mode]);
  // Requests from the domain tree: select and centre a concept, or show one area.
  useEffect(() => {
    if (!request || !graph) return;
    if (request.kind === 'area') setFilters(f => ({...f, module: request.id}));
    else if (graph.hasNode(request.id)) {const a = graph.getNodeAttributes(request.id); if (filters.module && filters.module !== a.module) setFilters(f => ({...f, module: ''})); if (a.test && filters.hideTests) setFilters(f => ({...f, hideTests: false})); setSelected(request.id); if (view === 'focus') focus(request.id); else frame(request.id); setInspectorOpen(true)}
    onRequestHandled?.();
  }, [request, graph]);
  // On narrow screens the inspector is a drawer that opens with a selection.
  useEffect(() => {if (narrow && selected) setInspectorOpen(true)}, [selected, narrow]);
  // The drawer behaves like a dialog: focus moves into it and Escape closes it.
  const drawerRef = useRef<HTMLElement>(null);
  useEffect(() => {
    if (!narrow || !inspectorOpen) return;
    // Focus the drawer itself, not its close button: the Enter that picked a search result must not also close it.
    drawerRef.current?.focus();
    const key = (e: KeyboardEvent) => {if (e.key === 'Escape' && !paletteOpen) {e.stopPropagation(); setInspectorOpen(false)}};
    window.addEventListener('keydown', key, true); return () => window.removeEventListener('keydown', key, true);
  }, [narrow, inspectorOpen, paletteOpen]);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {e.preventDefault(); setPaletteOpen(true)} else if (e.key === 'Escape' && !paletteOpen) {setSelected(null); setPath(null)}};
    window.addEventListener('keydown', onKey); return () => window.removeEventListener('keydown', onKey);
  }, [paletteOpen]);
  useEffect(() => {
    // A pending cross-link request decides the centre; only fall back to the most connected node without one.
    if (view !== 'focus' || !graph || focusRequest || (centre && graph.hasNode(centre))) return;
    const start = selected && graph.hasNode(selected) ? selected : stats.top[0]; if (start) setCentre(start);
  }, [view, graph, centre, selected, stats, focusRequest]);

  const areaCounts = useMemoCounts(graph, version);
  if (error) return <div className="explorer-empty error">{error}<button className="btn small" onClick={() => {loadedGraphs.clear(); window.location.reload()}}>Retry</button></div>;
  if (!loaded || !graph) return <div className="explorer-empty"><Loader2 className="spin"/>Loading the {mode === 'domain' ? 'domain ontology' : 'knowledge graph'}…</div>;
  if (mode === 'code' && !graph.order && live?.running) return <div className="explorer-empty"><Loader2 className="spin"/>Analysis is running ({live.stage}). Entities appear here as files are parsed, refreshing about every 15 seconds.</div>;
  if (mode === 'domain' && !graph.order) return <div className="explorer-empty">{live?.running ? <><Loader2 className="spin"/>Analysis is running ({live.stage}). Domain concepts appear here as soon as graph construction finishes, then grow with each AI batch.</> : 'No domain concepts yet. Analyse a source with Frappe DocTypes, or use sample or live AI mode, to build the domain ontology.'}</div>;

  const toggle = (list: string[], value: string) => list.includes(value) ? list.filter(x => x !== value) : [...list, value];
  const kindOrder = mode === 'domain' ? [] : ['File', 'Component', 'Class', 'Function', 'Test', 'API', 'Model', 'Metadata', 'Instance', 'Table', 'Column', 'Dependency', 'Configuration', 'Documentation'].filter(k => counts.kinds[k]);
  const overviewItems: [string, number][] = mode === 'domain'
    ? [['Visible concepts', stats.visible], ['Links', stats.edges], ['Areas', stats.areas], ['Unlinked concepts', stats.unlinked]]
    : [['Visible entities', stats.visible], ['Connections', stats.edges], ['Cross-file links', stats.crossFile], ['Unresolved refs', unresolved]];
  const note = mode === 'domain'
    ? <>Links are declared relationships (DocType links and tables, code), relationships inferred where a property names another concept, and AI-generated relationships, each labelled in the inspector. Areas are groups of densely linked concepts, named after their most connected concept. Ontology status: <strong>{ontology?.status || 'none'}</strong>.</>
    : <>Links are resolved statically: same-file names, <code>self.</code> methods, Python imports, Frappe DocType names and project-unique names. Ambiguous and external references stay unresolved rather than guessed.</>;
  const activeFilters = filters.hiddenKinds.length + filters.hiddenEdges.length + (filters.module ? 1 : 0) + (mode === 'code' && filters.onlyMapped ? 1 : 0) + (mode === 'code' && overlay ? 1 : 0);
  const inspectorProps = selected && graph.hasNode(selected) ? {graph, id: selected, snapshotId, colorOf, text, visible, edgeVisible, path, onPath: setPath, onPick: pick, onFocus: focus, onShowOnMap: showOnMap, tab: inspectorTab, onTab: setInspectorTab, onImpact: () => setImpactFor(selected), onOpenEntity} : null;
  const viewValue = view === 'focus' ? 'focus' : engine;
  const setViewValue = (v: string) => {if (v === 'focus') {onView('focus'); return} onView('map'); setMapEngine(v as MapEngine); store(`onto.engine.${mode}.map`, v)};
  const startResize = (e: React.PointerEvent) => {
    e.preventDefault(); const x0 = e.clientX, w0 = inspectorWidth;
    const move = (ev: PointerEvent) => setInspectorWidth(Math.min(520, Math.max(300, w0 + x0 - ev.clientX)));
    const up = () => {window.removeEventListener('pointermove', move); window.removeEventListener('pointerup', up)};
    window.addEventListener('pointermove', move); window.addEventListener('pointerup', up);
  };
  const resizeKey = (e: React.KeyboardEvent) => {if (e.key === 'ArrowLeft') setInspectorWidth(w => Math.min(520, w + 16)); else if (e.key === 'ArrowRight') setInspectorWidth(w => Math.max(300, w - 16))};
  return <div className="explorer">
    <div className="explorer-toolbar" role="toolbar" aria-label="Graph tools">
      <button className="palette-trigger" onClick={() => setPaletteOpen(true)} aria-label={text.search + ' (⌘K)'}><Search size={15}/><span>{text.trigger}</span><kbd>⌘K</kbd></button>
      {toolbarStart}
      <select aria-label={text.groupLabel} value={filters.module} onChange={e => setFilters({...filters, module: e.target.value})}><option value="">{text.allGroups}</option>{loaded.modules.map(m => <option key={m} value={m}>{m} ({areaCounts.get(m) || 0})</option>)}</select>
      {(view === 'map' || view === 'focus') && <select className="engine-select" aria-label="View" title="Choose how the graph is drawn" value={viewValue} onChange={e => setViewValue(e.target.value)}>
        <optgroup label="2D"><option value="standard">2D map</option><option value="focus">Focus: neighbourhood</option><option value="cosmos">GPU, large graphs (cosmos.gl)</option>{mode === 'domain' && <option value="vowl">OWL notation (WebVOWL)</option>}</optgroup>
        <optgroup label="3D and 2.5D"><option value="explore">✦ Explore: interactive 3D</option><option value="deck">{mode === 'domain' ? '2.5D rule landscape (deck.gl)' : '2.5D landscape (deck.gl)'}</option>
          {mode === 'code' ? <option value="city">3D code city</option> : <option value="floors">3D two floors: code ↔ domain</option>}
          <option value="force3d">3D force graph</option><option value="terrain">{mode === 'domain' ? '3D rule terrain' : '3D code terrain'}</option></optgroup>
      </select>}
      {view === 'diagram' && <select className="engine-select" aria-label="Diagram layout" value={diagramEngine} onChange={e => {setDiagramEngine(e.target.value as DiagramEngine); store('onto.engine.diagram', e.target.value)}}><option value="dagre">Standard layout (dagre)</option><option value="elk">Layered layout (ELK)</option></select>}
      <div className="popover-anchor">
        <button className={'btn small' + (activeFilters ? ' active' : '')} aria-expanded={filtersOpen} aria-haspopup="dialog" onClick={() => setFiltersOpen(!filtersOpen)}><SlidersHorizontal size={14}/>Filters{activeFilters > 0 && <span className="tab-count">{activeFilters}</span>}</button>
        {filtersOpen && <FilterPopover onClose={() => setFiltersOpen(false)} onReset={() => {setFilters(defaults); setOverlay(false)}}>
          {mode === 'code' && <fieldset><legend>Entity types</legend><div className="chip-wrap">{kindOrder.map(k => <button key={k} className="chip" aria-pressed={!filters.hiddenKinds.includes(k)} onClick={() => setFilters({...filters, hiddenKinds: toggle(filters.hiddenKinds, k)})}><i style={{background: colors[k]}}/>{k}<span>{counts.kinds[k].toLocaleString()}</span></button>)}</div></fieldset>}
          <fieldset><legend>Relationships</legend><div className="chip-wrap">{text.edgeKinds.filter(k => counts.edges[k]).map(k => <button key={k} className="chip edge" aria-pressed={!filters.hiddenEdges.includes(k)} onClick={() => setFilters({...filters, hiddenEdges: toggle(filters.hiddenEdges, k)})}><i style={{background: edgeColors[k]}}/>{k === 'property' ? 'property links' : k === 'is_a' ? 'is a' : k}<span>{counts.edges[k].toLocaleString()}</span></button>)}</div></fieldset>
          <fieldset><legend>Show</legend><label className="switch"><input type="checkbox" checked={!filters.hideTests} onChange={() => setFilters({...filters, hideTests: !filters.hideTests})}/><span/>{text.muted}{mode === 'domain' ? ` (${stats.unlinked})` : ''}</label>
            {mode === 'code' && <><label className="switch"><input type="checkbox" checked={overlay} onChange={e => setOverlay(e.target.checked)}/><span/>Domain overlay</label>
              <label className="switch"><input type="checkbox" checked={filters.onlyMapped} onChange={e => setFilters({...filters, onlyMapped: e.target.checked})}/><span/>Only mapped to concepts</label>
              <div className="segmented small" role="group" aria-label="Colour nodes by"><button aria-pressed={colorBy === 'type'} onClick={() => setColorBy('type')}>Colour: type</button><button aria-pressed={colorBy === 'module'} onClick={() => setColorBy('module')}>module</button></div></>}
          </fieldset>
        </FilterPopover>}
      </div>
      {live?.running && <button className={'live-pill' + (live.paused ? ' paused' : '')} onClick={live.onToggle} title={live.paused ? 'Resume live updates' : 'Pause live updates while you explore'}>
        {live.paused ? <Play size={12}/> : <i/>}{live.paused ? 'Live updates paused' : `Live · ${live.stage}`}</button>}
      <span className="toolbar-counts" aria-live="polite" title={`${stats.visible} of ${graph.order} ${text.noun} visible, ${stats.edges} links`}><strong>{stats.visible.toLocaleString()}</strong>/{graph.order.toLocaleString()} {text.noun} · <strong>{stats.edges.toLocaleString()}</strong> links</span>
      <button className="btn small icon-only" aria-pressed={inspectorOpen} aria-label={inspectorOpen ? 'Hide inspector' : 'Show inspector'} title={inspectorOpen ? 'Hide inspector' : 'Show inspector'} onClick={() => setInspectorOpen(!inspectorOpen)}><PanelRight size={15}/></button>
    </div>
    {disclosed && filters.hiddenKinds.includes('Function') && <div className="disclosure-note" role="status"><Info size={13}/><span>Large graph ({graph.order.toLocaleString()} entities): functions and columns are hidden to keep it readable. Select a node to explore its neighbourhood, or</span><button className="text-link small" onClick={() => {setFilters(f => ({...f, hiddenKinds: f.hiddenKinds.filter(k => k !== 'Function' && k !== 'Column')})); setDisclosed(false); try {localStorage.setItem('onto.codeShowAll', '1')} catch {}}}>show everything</button></div>}
    <div className="explorer-body">
      <div className="explorer-canvas">
        {view === 'map' && engine === 'standard' && <MapCanvas loaded={loaded} colorOf={colorOf} visible={visible} edgeVisible={edgeVisible} selected={selected} path={path} camera={camera} labelThreshold={text.labelThreshold} onSelect={select} highlight={impactHighlight}
          areaColor={mode === 'domain' ? (m: string) => colorFor(m, loaded.modules) : undefined} onFullscreen={onFullscreen} fullscreen={fullscreen}/>}
        {view === 'map' && engine !== 'standard' && <React.Suspense fallback={<div className="map-canvas"><div className="explorer-empty"><Loader2 className="spin"/>Loading the graph engine…</div></div>}>
          {engine === 'cosmos' && <CosmosCanvas graph={graph} visible={visible} colorOf={colorOf} edgeColorOf={edgeColorOf} selected={selected} path={path} camera={camera} onSelect={select}/>}
          {engine === 'vowl' && <VowlCanvas graph={graph} ontology={ontology} visible={visible} selected={selected} onSelect={select}/>}
          {engine === 'deck' && <DeckLandscape graph={graph} mode={mode} visible={visible} colorOf={colorOf} selected={selected} camera={camera} onSelect={select} heat={heat}/>}
          {engine === 'city' && <CodeCity graph={graph} visible={visible} colors={colors} mapped={mapped} selected={selected} camera={camera} onSelect={select} heat={heat} flow={impactFlow}/>}
          {engine === 'explore' && <Explore3D graph={graph} visible={visible} colorOf={colorOf} selected={selected} camera={camera} onSelect={select} highlight={impactHighlight} flow={impactFlow}/>}
          {engine === 'force3d' && <ForceGraph3D graph={graph} visible={visible} colorOf={colorOf} selected={selected} camera={camera} onSelect={select} highlight={impactHighlight} flow={impactFlow}/>}
          {engine === 'terrain' && <Terrain graph={graph} mode={mode} visible={visible} colorOf={colorOf} selected={selected} camera={camera} onSelect={select} heat={heat}/>}
          {engine === 'floors' && <TwoFloors graph={graph} code={loaded.code} colorOf={colorOf} codeColors={colors} visible={visible} selected={selected} onSelect={select} onOpenCode={id => onCrossLink('code', id)}/>}
        </React.Suspense>}
        {view === 'structure' && <StructureCanvas loaded={loaded} prefix={prefix} onPrefix={setPrefix} visible={visible} groupColor={groupColor} text={text}
          onOpenLeaf={p => {const leaf = graph.findNode((_, a) => a.path === p && (mode === 'domain' || a.kind === 'File')); if (leaf) focus(leaf)}}/>}
        {view === 'diagram' && <DiagramCanvas graph={graph} visible={visible} colorOf={colorOf} hiddenEdges={filters.hiddenEdges} selected={selected} onSelect={setSelected} onFocus={focus} engine={diagramEngine}/>}
        {view === 'coverage' && <CoverageView graph={graph} code={loaded.code} mapped={mapped} colorOf={colorOf} codeColors={colors} onSelect={setSelected} onOpenCode={id => onCrossLink('code', id)} semantic={semantic}/>}
        {view === 'query' && <QueryView sourceId={semantic?.sourceId || ''} semantic={semantic} graph={graph} code={loaded.code} onSelect={setSelected} onOpenCode={id => onCrossLink('code', id)}/>}
        {view === 'focus' && centre && graph.hasNode(centre) && <>
          <div className="focus-bar">
            {trail.length > 0 && <button className="btn small ghost" onClick={() => {const prev = trail[trail.length - 1]; setTrail(trail.slice(0, -1)); setCentre(prev); setSelected(prev)}}><ArrowLeft size={14}/>Back</button>}
            <span>Focused on <strong>{graph.getNodeAttribute(centre, 'label')}</strong></span>
            <div className="segmented small" role="group" aria-label="Hops">{[1, 2, 3].map(h => <button key={h} aria-pressed={hops === h} onClick={() => setHops(h)}>{h} hop{h > 1 ? 's' : ''}</button>)}</div>
          </div>
          <FocusCanvas graph={graph} centre={centre} hops={hops} visible={visible} hiddenEdges={filters.hiddenEdges} colorOf={colorOf} text={text} selected={selected} onSelect={setSelected} onRecentre={focus}/>
        </>}
      </div>
      {inspectorOpen && narrow && <div className="drawer-backdrop" onClick={() => setInspectorOpen(false)} aria-hidden/>}
      {inspectorOpen && <aside ref={drawerRef} tabIndex={narrow ? -1 : undefined} role={narrow ? 'dialog' : undefined} aria-modal={narrow || undefined} className={'inspector' + (narrow ? ' drawer' : '')} style={narrow ? undefined : {width: inspectorWidth}} aria-label="Inspector">
        {!narrow && <div className="resize-handle" role="separator" aria-orientation="vertical" aria-label="Resize inspector" aria-valuemin={300} aria-valuemax={520} aria-valuenow={inspectorWidth} tabIndex={0} onPointerDown={startResize} onKeyDown={resizeKey}/>}
        <button className="inspector-close" aria-label={selected ? 'Clear selection' : 'Hide inspector'} title={selected ? 'Clear selection' : 'Hide inspector'} onClick={() => selected ? select(null) : setInspectorOpen(false)}><X size={16}/></button>
        {inspectorProps && impactFor === selected
          ? <ImpactPanel snapshotId={snapshotId} nodeId={selected!} mode={mode} graph={graph} onPick={pick} onCross={onCrossLink} onHighlight={setImpactHighlight} onFlow={setImpactFlow} onClose={() => setImpactFor(null)}/>
          : inspectorProps
          ? (mode === 'domain'
              ? <ConceptInspector key={selected} {...inspectorProps} code={loaded.code} codeColors={colors} onOpenCode={id => onCrossLink('code', id)} findings={findingsFor.get(selected!) || []}/>
              : <CodeInspector key={selected} {...inspectorProps} concepts={mapped.get(selected!) || []} onOpenConcept={id => onCrossLink('domain', id)}/>)
          : <Overview graph={graph} items={overviewItems} top={stats.top} note={note} colorOf={colorOf} onPick={pick}/>}
      </aside>}
    </div>
    <Palette open={paletteOpen} onOpenChange={setPaletteOpen} graph={graph} visible={visible} colorOf={colorOf} placeholder={text.search} noun={text.noun} onPick={pick}/>
  </div>;
}

// An info button that reveals a short explanation on hover, focus or click (Escape closes it).
function InfoTip({label, children}: {label: string; children: React.ReactNode}) {
  const [open, setOpen] = useState(false); const id = useRef('tip-' + Math.random().toString(36).slice(2)).current;
  return <span className="info-tip" onMouseEnter={() => setOpen(true)} onMouseLeave={() => setOpen(false)}>
    <button className="icon-btn" aria-label={label} aria-expanded={open} aria-describedby={open ? id : undefined} onClick={() => setOpen(!open)} onFocus={() => setOpen(true)} onBlur={() => setOpen(false)} onKeyDown={e => {if (e.key === 'Escape') setOpen(false)}}><Info size={15}/></button>
    {open && <span role="tooltip" id={id} className="info-tip-panel">{children}</span>}
  </span>;
}

// Concepts (or entities) per area/module, for the area selector and the domain tree.
function useMemoCounts(graph: Graph | undefined, version: number) {
  return useMemo(() => {const m = new Map<string, number>(); graph?.forEachNode((_, a) => m.set(a.module, (m.get(a.module) || 0) + 1)); return m}, [graph, version]);
}

function useMedia(query: string) {
  const [match, setMatch] = useState(() => window.matchMedia(query).matches);
  useEffect(() => {const m = window.matchMedia(query); const on = () => setMatch(m.matches); m.addEventListener('change', on); return () => m.removeEventListener('change', on)}, [query]);
  return match;
}

// A small dialog anchored under its button; Escape or a click outside closes it.
function FilterPopover({children, onClose, onReset}: {children: React.ReactNode; onClose: () => void; onReset: () => void}) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const key = (e: KeyboardEvent) => {if (e.key === 'Escape') {e.stopPropagation(); onClose()}};
    const click = (e: MouseEvent) => {if (ref.current && !ref.current.parentElement!.contains(e.target as HTMLElement)) onClose()};
    window.addEventListener('keydown', key, true); window.addEventListener('mousedown', click); ref.current?.querySelector<HTMLElement>('button, input')?.focus();
    return () => {window.removeEventListener('keydown', key, true); window.removeEventListener('mousedown', click)};
  }, [onClose]);
  return <div ref={ref} className="popover" role="dialog" aria-label="Filters">{children}<footer><button className="btn small ghost" onClick={onReset}>Reset filters</button><button className="btn small" onClick={onClose}>Done</button></footer></div>;
}

// ---- Domain tree for the explorer panel: areas with their concepts, from the same data as the graph ----
export function DomainTree({snapshotId, ontology, mappings, revision = '', selected, onConcept, onArea}: {snapshotId: string; ontology: Obj | null; mappings: Obj[]; revision?: string; selected?: string | null; onConcept: (id: string) => void; onArea: (area: string) => void}) {
  const {loaded, error} = useExplorerData('domain', snapshotId, ontology, mappings, revision);
  const [query, setQuery] = useState(''); const [open, setOpen] = useState<Record<string, boolean>>({});
  const groups = useMemo(() => {
    if (!loaded) return [];
    const q = query.trim().toLowerCase(); const m = new Map<string, {id: string; label: string}[]>();
    loaded.graph.forEachNode((id, a) => {if (q && !String(a.label).toLowerCase().includes(q) && !(a.aliases || []).some((x: string) => x.toLowerCase().includes(q))) return; m.set(a.module, [...(m.get(a.module) || []), {id, label: a.label}])});
    return loaded.modules.filter(x => m.has(x)).map(area => ({area, color: colorFor(area, loaded.modules), items: m.get(area)!.sort((x, y) => x.label.localeCompare(y.label))}));
  }, [loaded, query]);
  if (error) return <p className="tree-note error" role="alert">{error}</p>;
  if (!loaded) return <p className="tree-note"><Loader2 size={13} className="spin"/>Loading concepts…</p>;
  if (!loaded.graph.order) return <p className="tree-note">No domain concepts yet.</p>;
  return <div className="domain-tree">
    <label className="tree-search"><Search size={13}/><input value={query} onChange={e => setQuery(e.target.value)} placeholder="Filter concepts" aria-label="Filter domain concepts"/></label>
    {!groups.length && <p className="tree-note">No concepts match “{query}”.</p>}
    <ul role="tree" aria-label="Domain areas and concepts">{groups.map(({area, color, items}) => {
      const expanded = !!query || !!open[area];
      return <li key={area} role="treeitem" aria-expanded={expanded}>
        <div className="tree-row area"><button className="tree-twist" aria-label={(expanded ? 'Collapse ' : 'Expand ') + area} onClick={() => setOpen({...open, [area]: !expanded})}><ChevronRight size={13} className={expanded ? 'open' : ''}/></button>
          <button className="tree-label" onClick={() => onArea(area)} title={`Show the ${area} area`}><i style={{background: color}}/><span>{area}</span><small>{items.length}</small></button></div>
        {expanded && <ul role="group">{items.slice(0, 300).map(c => <li key={c.id} role="treeitem"><button className={'tree-row concept' + (selected === c.id ? ' selected' : '')} aria-current={selected === c.id || undefined} onClick={() => onConcept(c.id)}>{c.label}</button></li>)}</ul>}
      </li>})}</ul>
  </div>;
}
