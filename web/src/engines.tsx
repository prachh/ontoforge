// Alternative graph engines, loaded on demand so the standard views stay as they are:
// cosmos.gl (GPU force layout for large graphs) and an OWL notation view in the style of WebVOWL.
import React, {useEffect, useMemo, useRef, useState} from 'react';
import Graph from 'graphology';
import {Graph as Cosmos} from '@cosmos.gl/graph';
import {onContextLost} from './webgl';
import {forceSimulation, forceLink, forceManyBody, forceCollide, forceX, forceY} from 'd3-force';
import type {SimulationNodeDatum, SimulationLinkDatum} from 'd3-force';
import {Loader2, Pause, Play} from 'lucide-react';

type Obj = Record<string, any>;
type Camera = {ids: string[]; nonce: number} | null;

function rgba(hex: string, alpha = 1): number[] {
  const h = hex.replace('#', ''); const full = h.length === 3 ? h.split('').map(c => c + c).join('') : h.slice(0, 6);
  const n = parseInt(full, 16); return Number.isNaN(n) ? [.55, .6, .65, alpha] : [(n >> 16 & 255) / 255, (n >> 8 & 255) / 255, (n & 255) / 255, alpha];
}

// ---- cosmos.gl: the whole graph simulated and drawn on the GPU ----
type CosmosProps = {graph: Graph; visible: (id: string) => boolean; colorOf: (a: Obj) => string; edgeColorOf: (a: Obj) => string;
  selected: string | null; path: string[] | null; camera: Camera; onSelect: (id: string | null) => void};

const MAX_LABELS = 40;

export function CosmosCanvas({graph, visible, colorOf, edgeColorOf, selected, path, camera, onSelect}: CosmosProps) {
  const host = useRef<HTMLDivElement>(null); const labelLayer = useRef<HTMLDivElement>(null);
  const cosmos = useRef<Cosmos | null>(null); const ready = useRef(false);
  const [revive, setRevive] = useState(0);
  const [error, setError] = useState(''); const [running, setRunning] = useState(true); const [hover, setHover] = useState<{label: string; x: number; y: number} | null>(null);
  const onSelectRef = useRef(onSelect); onSelectRef.current = onSelect;

  const data = useMemo(() => {
    const ids = graph.filterNodes(id => visible(id)); const index = new Map(ids.map((id, i) => [id, i]));
    const positions = new Float32Array(ids.length * 2), colors = new Float32Array(ids.length * 4), sizes = new Float32Array(ids.length);
    ids.forEach((id, i) => {
      const a = graph.getNodeAttributes(id);
      positions[2 * i] = Number.isFinite(a.x) ? a.x : Math.random() * 100; positions[2 * i + 1] = Number.isFinite(a.y) ? a.y : Math.random() * 100;
      colors.set(rgba(colorOf(a)), 4 * i); sizes[i] = Math.min(14, 3 + Math.sqrt(graph.degree(id)) * 1.3);
    });
    const links: number[] = [], linkColors: number[] = [];
    graph.forEachEdge((_, a, s, t) => {const i = index.get(s), j = index.get(t); if (i === undefined || j === undefined) return; links.push(i, j); linkColors.push(...rgba(edgeColorOf(a), .75))});
    const top = ids.map((id, i) => [graph.degree(id), i]).sort((x, y) => y[0] - x[0]).slice(0, 25).map(x => x[1]);
    return {ids, index, positions, colors, sizes, links: new Float32Array(links), linkColors: new Float32Array(linkColors), top};
  }, [graph, visible, colorOf, edgeColorOf]);

  // Labels: the most connected points plus the selection and its neighbours, placed over the canvas every frame.
  const labelled = useMemo(() => {
    const out = new Set(data.top); const sel = selected ? data.index.get(selected) : undefined;
    for (const id of path || []) {const i = data.index.get(id); if (i !== undefined) out.add(i)}
    if (sel !== undefined) {out.add(sel); for (const n of graph.neighbors(selected!)) {const i = data.index.get(n); if (i !== undefined && out.size < MAX_LABELS + 25) out.add(i)}}
    return [...out];
  }, [data, selected, path, graph]);

  useEffect(() => {
    if (!host.current) return;
    setError(''); ready.current = false; let cancelled = false; let g: Cosmos;
    try {
      g = new Cosmos(host.current, {
        backgroundColor: '#fafcf8', spaceSize: 4096, attribution: '', rescalePositions: true, fitViewOnInit: true, fitViewDelay: 1200, fitViewPadding: .12,
        pointGreyoutOpacity: .12, linkGreyoutOpacity: .04, linkOpacity: .7, linkDefaultWidth: .9, curvedLinks: true, scalePointsOnZoom: false,
        renderHoveredPointRing: true, hoveredPointRingColor: '#2f8a73', focusedPointRingColor: '#1f6f5c', hoveredPointCursor: 'pointer', enableDrag: true,
        simulationGravity: .2, simulationRepulsion: 1.2, simulationLinkSpring: 1, simulationLinkDistance: 10, simulationFriction: .85, simulationDecay: 6000,
        onClick: i => onSelectRef.current(i === undefined ? null : data.ids[i] ?? null),
        onPointMouseOver: (i, pos) => {const [x, y] = g.spaceToScreenPosition(pos); setHover({label: String(graph.getNodeAttribute(data.ids[i], 'label')), x, y})},
        onPointMouseOut: () => setHover(null),
        onSimulationStart: () => setRunning(true), onSimulationEnd: () => setRunning(false), onSimulationPause: () => setRunning(false), onSimulationUnpause: () => setRunning(true),
      });
    } catch (e) {setError('GPU rendering is not available in this browser (WebGL could not start).'); return}
    cosmos.current = g;
    g.ready.then(() => {
      if (cancelled) return;
      g.setPointPositions(data.positions); g.setPointColors(data.colors); g.setPointSizes(data.sizes);
      g.setLinks(data.links); g.setLinkColors(data.linkColors); g.render(); ready.current = true;
    }).catch(() => !cancelled && setError('GPU rendering is not available in this browser (WebGL could not start).'));
    const unlost = onContextLost(host.current, () => setRevive(r => r + 1));
    return () => {unlost(); cancelled = true; ready.current = false; cosmos.current = null; g.destroy()};
  }, [data, revive]);

  // Track labelled points: read their positions from the GPU a few times a second, convert to screen space every frame.
  useEffect(() => {
    let frame = 0, last = 0; let space = new Map<number, [number, number]>();
    const loop = (t: number) => {
      const g = cosmos.current; const layer = labelLayer.current;
      if (g && ready.current && layer) {
        if (t - last > 180) {try {g.trackPointPositionsByIndices(labelled); space = new Map(g.getTrackedPointPositionsMap())} catch { /* not ready yet */ } last = t}
        for (const el of Array.from(layer.children) as HTMLElement[]) {
          const p = space.get(Number(el.dataset.i));
          if (!p) {el.style.display = 'none'; continue}
          const [x, y] = g.spaceToScreenPosition(p); el.style.display = ''; el.style.transform = `translate(${x}px, ${y}px)`;
        }
      }
      frame = requestAnimationFrame(loop);
    };
    frame = requestAnimationFrame(loop); return () => cancelAnimationFrame(frame);
  }, [labelled]);

  useEffect(() => {
    const g = cosmos.current; if (!g || !ready.current) return;
    const sel = selected ? data.index.get(selected) : undefined;
    if (path?.length) {const idx = path.map(id => data.index.get(id)).filter((i): i is number => i !== undefined); g.setConfigPartial({highlightedPointIndices: idx, focusedPointIndex: idx[0]})}
    else if (sel !== undefined) g.setConfigPartial({highlightedPointIndices: [sel, ...g.getNeighboringPointIndices(sel)], highlightedLinkIndices: g.getConnectedLinkIndices(sel), focusedPointIndex: sel});
    else g.setConfigPartial({highlightedPointIndices: undefined, highlightedLinkIndices: undefined, focusedPointIndex: undefined});
  }, [selected, path, data, running]);

  useEffect(() => {
    const g = cosmos.current; if (!g || !ready.current || !camera) return;
    const idx = camera.ids.map(id => data.index.get(id)).filter((i): i is number => i !== undefined);
    if (idx.length === 1) g.zoomToPointByIndex(idx[0], 600, 6); else if (idx.length) g.fitViewByPointIndices(idx, 600, .2);
  }, [camera]);

  if (error) return <div className="map-canvas"><div className="explorer-empty error">{error}</div></div>;
  const sel = selected ? data.index.get(selected) : undefined;
  return <div className="map-canvas cosmos-canvas">
    <div ref={host} className="sigma-host" aria-label="Knowledge map (GPU)" role="img"/>
    <div ref={labelLayer} className="cosmos-labels" aria-hidden>
      {labelled.map(i => <span key={i} data-i={i} className={i === sel ? 'on' : ''}>{String(graph.getNodeAttribute(data.ids[i], 'label'))}</span>)}
    </div>
    {hover && <div className="cosmos-tip" style={{left: hover.x, top: hover.y}}>{hover.label}</div>}
    <div className="map-hud">
      <span className="hud-pill">GPU · cosmos.gl · {data.ids.length.toLocaleString()} nodes · {(data.links.length / 2).toLocaleString()} links</span>
      {running ? <button className="hud-pill quiet" onClick={() => cosmos.current?.pause()}><Loader2 size={13} className="spin"/>Simulating<Pause size={12}/></button>
        : <button className="hud-pill quiet" onClick={() => {cosmos.current?.start(.6)}}><Play size={12}/>Re-run layout</button>}
      <button className="hud-pill quiet" onClick={() => cosmos.current?.fitView(500)}>Fit view</button>
    </div>
  </div>;
}

// ---- WebVOWL-style OWL notation ----
// Classes are circles; object properties are labelled boxes on their links; subclass links are dashed with a hollow arrow;
// datatype properties (green) point to datatypes (yellow); property characteristics and disjointness are shown as in VOWL.
const VOWL = {cls: '#aaccff', clsText: '#123', external: '#3366cc', abstract: '#ddeeff', objProp: '#aaccff', dataProp: '#99cc66', datatype: '#ffcc33', line: '#5a6b78', disjoint: '#b05050'};
const MAX_CLASSES = 350;

type VNode = SimulationNodeDatum & {id: string; label: string; r: number; abstract: boolean; external: boolean};
type VLink = SimulationLinkDatum<VNode> & {id: string; kind: 'object' | 'subclass' | 'disjoint'; label: string; tags: string[]; inverse?: string; curve: number};

type VowlProps = {graph: Graph; ontology: Obj | null; visible: (id: string) => boolean; selected: string | null; onSelect: (id: string | null) => void};

function characteristics(ontology: Obj | null) {
  // Property characteristics by relationship id: Link fields are single-valued, child tables read back as "row of",
  // and stored axioms (from code or the AI logic pass) add inverse, symmetric, transitive and functional.
  const tags = new Map<string, Set<string>>(); const inverse = new Map<string, string>();
  const add = (id: string, t: string) => tags.set(id, new Set([...(tags.get(id) || []), t]));
  for (const r of ontology?.relationships || []) {
    if (r.kind === 'link') add(r.id, 'functional');
    if (r.kind === 'contains') inverse.set(r.id, 'is row of');
    if (r.source_types?.[0] === r.target_types?.[0] && /^parent/i.test(r.label || '')) add(r.id, 'transitive');
  }
  for (const a of ontology?.axioms || []) {
    if (!a.property) continue;
    if (a.kind === 'inverse') inverse.set(a.property, a.label || String(a.inverse || '').replace(/~inverse$/, ''));
    if (['symmetric', 'transitive', 'functional'].includes(a.kind)) add(a.property.replace(/~ancestor$/, ''), a.kind);
  }
  return {tags, inverse};
}

export function VowlCanvas({graph, ontology, visible, selected, onSelect}: VowlProps) {
  const svg = useRef<SVGSVGElement>(null);
  const [view, setView] = useState({x: 0, y: 0, k: 1}); const [tick, setTick] = useState(0); const [hover, setHover] = useState<string | null>(null);
  const [showData, setShowData] = useState(true);
  const drag = useRef<{node?: VNode; pan?: {x: number; y: number; vx: number; vy: number}; moved: boolean} | null>(null);
  const simRef = useRef<ReturnType<typeof forceSimulation<VNode>> | null>(null);

  const model = useMemo(() => {
    const ids = graph.filterNodes(id => visible(id)).sort((a, b) => graph.degree(b) - graph.degree(a));
    const shown = ids.slice(0, MAX_CLASSES); const set = new Set(shown);
    const evidence = (id: string) => (graph.getNodeAttribute(id, 'evidence') || []).length;
    const nodes: VNode[] = shown.map(id => {const a = graph.getNodeAttributes(id); return {id, label: String(a.label), r: Math.min(46, 22 + Math.sqrt(graph.degree(id)) * 3.2), abstract: !!a.abstract, external: !evidence(id)}});
    const {tags, inverse} = characteristics(ontology);
    const links: VLink[] = []; const pairs = new Map<string, number>();
    const push = (l: Omit<VLink, 'curve'>) => {
      const k = [l.source, l.target].sort().join('|'); const n = pairs.get(k) || 0; pairs.set(k, n + 1);
      links.push({...l, curve: n === 0 ? 0 : (n % 2 ? 1 : -1) * Math.ceil(n / 2) * 38});
    };
    for (const c of ontology?.concepts || []) if (c.parent && set.has(c.id) && set.has(c.parent) && c.parent !== c.id) push({id: 'sub:' + c.id, source: c.id, target: c.parent, kind: 'subclass', label: 'Subclass of', tags: []});
    for (const r of ontology?.relationships || []) for (const s of r.source_types || []) for (const t of r.target_types || []) {
      if (s === t || !set.has(s) || !set.has(t)) continue;
      push({id: r.id + ':' + s + ':' + t, source: s, target: t, kind: 'object', label: r.label || r.id, tags: [...(tags.get(r.id) || [])], inverse: inverse.get(r.id)});
    }
    let largeDisjoint = 0;
    for (const a of ontology?.axioms || []) {
      if (a.kind !== 'disjoint') continue;
      const members = (a.concepts || []).filter((x: string) => set.has(x));
      if ((a.concepts || []).length > 8) {largeDisjoint++; continue}
      for (let i = 0; i < members.length; i++) for (let j = i + 1; j < members.length; j++) push({id: `dis:${a.id}:${i}:${j}`, source: members[i], target: members[j], kind: 'disjoint', label: 'Disjoint', tags: []});
    }
    return {nodes, links, hidden: ids.length - shown.length, largeDisjoint};
  }, [graph, ontology, visible]);

  // Fit the laid-out graph into the view.
  const fit = () => {
    const box = svg.current?.getBoundingClientRect(); const xs = model.nodes.map(n => n.x || 0), ys = model.nodes.map(n => n.y || 0);
    if (!box || !xs.length) return;
    const w = Math.max(...xs) - Math.min(...xs) + 200, h = Math.max(...ys) - Math.min(...ys) + 200; const k = Math.min(1.2, box.width / w, box.height / h);
    setView({k, x: box.width / 2 - k * (Math.max(...xs) + Math.min(...xs)) / 2, y: box.height / 2 - k * (Math.max(...ys) + Math.min(...ys)) / 2});
  };
  useEffect(() => {
    const sim = forceSimulation<VNode>(model.nodes)
      .force('link', forceLink<VNode, VLink>(model.links).id(d => d.id).distance(l => l.kind === 'disjoint' ? 260 : 210).strength(l => l.kind === 'disjoint' ? .05 : .5))
      .force('charge', forceManyBody().strength(-1300)).force('collide', forceCollide<VNode>(d => d.r + 26)).force('x', forceX(0).strength(.04)).force('y', forceY(0).strength(.04));
    sim.stop(); for (let i = 0; i < Math.min(300, 80 + model.nodes.length); i++) sim.tick();
    let frame = 0; sim.on('tick', () => {cancelAnimationFrame(frame); frame = requestAnimationFrame(() => setTick(t => t + 1))});
    sim.alpha(.08).restart(); simRef.current = sim;
    fit(); setTick(t => t + 1);
    return () => {sim.stop(); cancelAnimationFrame(frame)};
  }, [model]);

  const point = (e: React.PointerEvent | React.WheelEvent) => {const b = svg.current!.getBoundingClientRect(); return {x: (e.clientX - b.left - view.x) / view.k, y: (e.clientY - b.top - view.y) / view.k}};
  const onWheel = (e: React.WheelEvent) => {
    const b = svg.current!.getBoundingClientRect(); const mx = e.clientX - b.left, my = e.clientY - b.top;
    const k = Math.max(.08, Math.min(3, view.k * Math.exp(-e.deltaY * .0015)));
    setView({k, x: mx - (mx - view.x) * k / view.k, y: my - (my - view.y) * k / view.k});
  };
  useEffect(() => {const el = svg.current; if (!el) return; const stop = (e: WheelEvent) => e.preventDefault(); el.addEventListener('wheel', stop, {passive: false}); return () => el.removeEventListener('wheel', stop)}, []);
  const onDown = (e: React.PointerEvent, node?: VNode) => {
    e.stopPropagation(); (e.target as Element).setPointerCapture?.(e.pointerId);
    drag.current = node ? {node, moved: false} : {pan: {x: e.clientX, y: e.clientY, vx: view.x, vy: view.y}, moved: false};
    if (node) {node.fx = node.x; node.fy = node.y; simRef.current?.alphaTarget(.15).restart()}
  };
  const onMove = (e: React.PointerEvent) => {
    const d = drag.current; if (!d) return; d.moved = true;
    if (d.node) {const p = point(e); d.node.fx = p.x; d.node.fy = p.y}
    else if (d.pan) setView(v => ({...v, x: d.pan!.vx + e.clientX - d.pan!.x, y: d.pan!.vy + e.clientY - d.pan!.y}));
  };
  const onUp = () => {
    const d = drag.current; drag.current = null;
    if (d?.node) {d.node.fx = null; d.node.fy = null; simRef.current?.alphaTarget(0); if (!d.moved) onSelect(d.node.id)}
    else if (d && !d.moved) onSelect(null);
  };

  const inModel = (id: string | null) => !!id && model.nodes.some(n => n.id === id);
  const focus = inModel(hover) ? hover : inModel(selected) ? selected : null;
  const near = useMemo(() => {
    if (!focus) return null; const s = new Set([focus]);
    for (const l of model.links) {const a = (l.source as VNode).id, b = (l.target as VNode).id; if (a === focus) s.add(b); if (b === focus) s.add(a)}
    return s;
  }, [focus, model, tick]);

  const sel = selected ? model.nodes.find(n => n.id === selected) : undefined;
  const dataProps = sel && sel.x != null && showData ? ((graph.getNodeAttribute(sel.id, 'properties') || []) as Obj[]).filter(p => !['Link', 'Table', 'Table MultiSelect'].includes(p.fieldtype)).slice(0, 14) : [];

  if (!model.nodes.length) return <div className="map-canvas"><div className="explorer-empty">No classes match the filters.</div></div>;
  return <div className="map-canvas vowl-canvas">
    <svg ref={svg} className="vowl-svg" onWheel={onWheel} onPointerDown={e => onDown(e)} onPointerMove={onMove} onPointerUp={onUp} role="img" aria-label="Ontology in OWL notation">
      <defs>
        <marker id="vowl-arrow" viewBox="0 0 10 10" refX="10" refY="5" markerWidth="9" markerHeight="9" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill={VOWL.line}/></marker>
        <marker id="vowl-hollow" viewBox="0 0 10 10" refX="10" refY="5" markerWidth="11" markerHeight="11" orient="auto"><path d="M0,0 L10,5 L0,10 z" fill="#fff" stroke={VOWL.line}/></marker>
      </defs>
      <g transform={`translate(${view.x},${view.y}) scale(${view.k})`}>
        {model.links.map(l => {
          const s = l.source as VNode, t = l.target as VNode; if (s.x == null || t.x == null) return null;
          const dx = t.x - s.x!, dy = t.y! - s.y!, len = Math.hypot(dx, dy) || 1; const nx = -dy / len, ny = dx / len;
          const mx = (s.x! + t.x) / 2 + nx * l.curve, my = (s.y! + t.y!) / 2 + ny * l.curve;
          // End the line at the target circle's edge so the arrow head is visible.
          const ex = t.x - (t.x - mx) / Math.hypot(t.x - mx, t.y! - my) * t.r, ey = t.y! - (t.y! - my) / Math.hypot(t.x - mx, t.y! - my) * t.r;
          const sx = s.x! + (mx - s.x!) / Math.hypot(mx - s.x!, my - s.y!) * s.r, sy = s.y! + (my - s.y!) / Math.hypot(mx - s.x!, my - s.y!) * s.r;
          const dim = near && !(near.has(s.id) && near.has(t.id)); const on = !!near && !dim;
          const text = l.inverse ? `${l.label} / ${l.inverse}` : l.label; const sub = l.tags.length ? `(${l.tags.join(', ')})` : '';
          const w = Math.max(text.length, sub.length) * 6.1 + 14, h = sub ? 30 : 18;
          const fill = l.kind === 'object' ? VOWL.objProp : l.kind === 'disjoint' ? '#fff' : '#fff';
          return <g key={l.id} className={'vowl-link ' + l.kind + (dim ? ' dim' : '') + (on ? ' on' : '')}>
            <path d={`M${sx},${sy} Q${2 * mx - (s.x! + t.x) / 2},${2 * my - (s.y! + t.y!) / 2} ${ex},${ey}`} fill="none" stroke={l.kind === 'disjoint' ? VOWL.disjoint : VOWL.line}
              strokeDasharray={l.kind === 'object' ? undefined : '6 4'} markerEnd={l.kind === 'subclass' ? 'url(#vowl-hollow)' : l.kind === 'object' ? 'url(#vowl-arrow)' : undefined}
              markerStart={l.kind === 'object' && l.inverse ? 'url(#vowl-arrow)' : undefined}/>
            {(view.k > .35 || on) && <g transform={`translate(${mx},${my})`}>
              <rect x={-w / 2} y={-h / 2} width={w} height={h} rx={l.kind === 'object' ? 0 : 3} fill={fill} stroke={l.kind === 'disjoint' ? VOWL.disjoint : l.kind === 'subclass' ? 'none' : '#7a95b8'}/>
              <text y={sub ? -3 : 4} textAnchor="middle" className="vowl-prop">{l.kind === 'disjoint' ? '⊥ Disjoint' : text}</text>
              {sub && <text y={11} textAnchor="middle" className="vowl-tag">{sub}</text>}
            </g>}
          </g>;
        })}
        {sel && dataProps.map((p, i) => {
          const angle = (i / Math.max(1, dataProps.length)) * Math.PI * 2 - Math.PI / 2; const d1 = sel.r + 70, d2 = sel.r + 150;
          const px = sel.x! + Math.cos(angle) * d1, py = sel.y! + Math.sin(angle) * d1, tx = sel.x! + Math.cos(angle) * d2, ty = sel.y! + Math.sin(angle) * d2;
          const type = p.fieldtype || p.datatype || 'string'; const w = String(p.name).length * 6.1 + 12, tw = String(type).length * 6 + 12;
          return <g key={p.name} className="vowl-data">
            <line x1={sel.x! + Math.cos(angle) * sel.r} y1={sel.y! + Math.sin(angle) * sel.r} x2={tx} y2={ty} stroke={VOWL.line} markerEnd="url(#vowl-arrow)"/>
            <rect x={px - w / 2} y={py - 9} width={w} height={18} fill={VOWL.dataProp} stroke="#6f9a45"/><text x={px} y={py + 4} textAnchor="middle" className="vowl-prop">{p.name}{p.required ? ' *' : ''}</text>
            <rect x={tx - tw / 2} y={ty - 10} width={tw} height={20} fill={VOWL.datatype} stroke="#c9a024"/><text x={tx} y={ty + 4} textAnchor="middle" className="vowl-prop">{type}</text>
          </g>;
        })}
        {model.nodes.map(n => {
          if (n.x == null) return null; const dim = near && !near.has(n.id);
          const fill = n.external ? VOWL.external : n.abstract ? VOWL.abstract : VOWL.cls; const label = n.label.length > 18 ? n.label.slice(0, 16) + '…' : n.label;
          return <g key={n.id} transform={`translate(${n.x},${n.y})`} className={'vowl-class' + (dim ? ' dim' : '') + (n.id === selected ? ' selected' : '')}
            onPointerDown={e => onDown(e, n)} onPointerEnter={() => setHover(n.id)} onPointerLeave={() => setHover(null)}>
            <circle r={n.r} fill={fill} stroke={n.id === selected ? '#1f6f5c' : '#33475b'} strokeWidth={n.id === selected ? 3 : 1.2} strokeDasharray={n.abstract ? '5 3' : undefined}/>
            <text textAnchor="middle" y={4} fill={n.external ? '#fff' : VOWL.clsText} className="vowl-label"><title>{n.label}</title>{label}</text>
          </g>;
        })}
      </g>
    </svg>
    <div className="map-hud">
      <span className="hud-pill">OWL notation (WebVOWL style) · {model.nodes.length} classes{model.hidden ? ` · ${model.hidden} more hidden: choose an area` : ''}</span>
      <button className="hud-pill quiet" onClick={fit}>Fit view</button>
      <button className="hud-pill quiet" onClick={() => setShowData(!showData)}>{showData ? 'Hide' : 'Show'} datatype properties of the selected class</button>
      {model.largeDisjoint > 0 && <span className="hud-pill quiet" title="Large disjoint groups (for example all DocTypes) are not drawn pairwise">⊥ {model.largeDisjoint} large disjoint group{model.largeDisjoint > 1 ? 's' : ''} not drawn</span>}
    </div>
    <div className="vowl-legend" aria-label="Legend">
      <span><i className="c" style={{background: VOWL.cls}}/>Class</span><span><i className="c" style={{background: VOWL.external}}/>No code evidence</span><span><i className="c dashed" style={{background: VOWL.abstract}}/>Abstract</span>
      <span><i style={{background: VOWL.objProp}}/>Object property</span><span><i style={{background: VOWL.dataProp}}/>Datatype property</span><span><i style={{background: VOWL.datatype}}/>Datatype</span>
      <span><b className="dash"/>Subclass of</span><span><b className="dash red"/>Disjoint</span>
    </div>
  </div>;
}
