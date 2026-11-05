// More 3D views on the shared scene (shadows, ambient occlusion, glow, VR/AR): a 3D force graph with animated impact
// pulses, a terrain whose height is rule (or code) density, and a stack of ontology versions.
import React, {useEffect, useLayoutEffect, useMemo, useRef, useState} from 'react';
import Graph from 'graphology';
import * as THREE from 'three';
import {useFrame} from '@react-three/fiber';
import {forceSimulation, forceLink, forceManyBody, forceCenter} from 'd3-force-3d';
import {forceSimulation as force2d, forceLink as link2d, forceManyBody as charge2d, forceCenter as center2d, forceX as x2d, forceY as y2d} from 'd3-force';
import {Scene, Pulses, Framer, webglAvailable, uniqueTags} from './city3d';
import {graphTokens, onThemeChange, useTheme} from './theme';
import type {Camera, Flow, Tag} from './city3d';
import {Loader2} from 'lucide-react';

type Obj = Record<string, any>;
const DIM = new THREE.Color(); const LOW = new THREE.Color(), MID = new THREE.Color(), HIGH = new THREE.Color(); let SELECTED = '';
// Theme colours, refreshed in place; terrain runs from the canvas colour (low) to the accent (high).
function apply3DTheme() {const k = graphTokens(); DIM.set(k.dim); LOW.set(k.canvas).lerp(new THREE.Color(k.text), .06); MID.set(k.nodes[0]); HIGH.set(k.primary).lerp(new THREE.Color(k.mode === 'dark' ? '#FFFFFF' : '#000000'), .2); SELECTED = k.select}
apply3DTheme(); onThemeChange(apply3DTheme);
const NoWebGL = () => <div className="map-canvas"><div className="explorer-empty error">3D needs WebGL, which is not available in this browser.</div></div>;

// ---- 3D force graph ----
type FNode = {id: string; i: number; x: number; y: number; z: number; vx?: number; vy?: number; vz?: number};
type ForceProps = {graph: Graph; visible: (id: string) => boolean; colorOf: (a: Obj) => string; selected: string | null; camera: Camera; onSelect: (id: string | null) => void; highlight?: string[] | null; flow?: Flow};

function ForceBody({data, colors, focus, selected, onPick, onHover, flowEdges, done}: {data: {nodes: FNode[]; links: {source: any; target: any}[]; sizes: number[]}; colors: THREE.Color[]; focus: Set<string> | null; selected: string | null; onPick: (i: number | null) => void; onHover: (i: number | null) => void; flowEdges: [number, number, number][]; done: () => void}) {
  const mesh = useRef<THREE.InstancedMesh>(null); const lines = useRef<THREE.BufferGeometry>(null); const lit = useRef<THREE.BufferGeometry>(null);
  const o = useMemo(() => new THREE.Object3D(), []);
  const sim = useMemo(() => forceSimulation(data.nodes, 3)
    .force('link', forceLink(data.links).id((d: FNode) => d.id).distance(22).strength(.35))
    .force('charge', forceManyBody().strength(-38).theta(.9).distanceMax(400)).force('center', forceCenter(0, 0, 0)).alphaDecay(.025).stop(), [data]);
  const positions = useMemo(() => new Float32Array(data.links.length * 6), [data]);
  const focusLinks = useMemo(() => data.links.filter(l => focus && focus.has((l.source as FNode).id ?? l.source) && focus.has((l.target as FNode).id ?? l.target) && (!selected || (l.source as FNode).id === selected || (l.target as FNode).id === selected || !focus.has(selected))), [data, focus, selected]);
  const litPositions = useMemo(() => new Float32Array(Math.max(1, focusLinks.length) * 6), [focusLinks]);
  const settled = useRef(false);
  useEffect(() => {settled.current = false}, [data]);
  const draw = () => {
    const m = mesh.current; if (!m) return;
    data.nodes.forEach((n, i) => {o.position.set(n.x, n.y, n.z); o.scale.setScalar(data.sizes[i]); o.updateMatrix(); m.setMatrixAt(i, o.matrix)});
    m.instanceMatrix.needsUpdate = true; m.computeBoundingSphere();
    data.links.forEach((l, k) => {const a = l.source as FNode, b = l.target as FNode; positions.set([a.x, a.y, a.z, b.x, b.y, b.z], k * 6)});
    if (lines.current) {lines.current.attributes.position.needsUpdate = true; lines.current.computeBoundingSphere()}
    focusLinks.forEach((l, k) => {const a = l.source as FNode, b = l.target as FNode; litPositions.set([a.x, a.y, a.z, b.x, b.y, b.z], k * 6)});
    if (lit.current) {lit.current.setDrawRange(0, focusLinks.length * 2); lit.current.attributes.position.needsUpdate = true; lit.current.computeBoundingSphere()}
  };
  useFrame(() => {
    if (!settled.current) {
      const start = performance.now(); while (performance.now() - start < 10 && sim.alpha() > sim.alphaMin()) sim.tick();
      if (sim.alpha() <= sim.alphaMin()) {settled.current = true; done()}
      draw();
    }
  });
  useLayoutEffect(() => {draw()}, [focusLinks]);
  useLayoutEffect(() => {const m = mesh.current; if (!m) return; colors.forEach((c, i) => m.setColorAt(i, c)); if (m.instanceColor) m.instanceColor.needsUpdate = true}, [colors]);
  const pulses = useMemo(() => flowEdges.map(([a, b, depth]) => ({from: () => [data.nodes[a].x, data.nodes[a].y, data.nodes[a].z] as [number, number, number], to: () => [data.nodes[b].x, data.nodes[b].y, data.nodes[b].z] as [number, number, number], depth})), [flowEdges, data]);
  return <>
    <lineSegments raycast={() => null}><bufferGeometry ref={lines}><bufferAttribute attach="attributes-position" args={[positions, 3]}/></bufferGeometry>
      <lineBasicMaterial color={graphTokens().edge} transparent opacity={focus ? .05 : .22}/></lineSegments>
    <lineSegments raycast={() => null}><bufferGeometry ref={lit}><bufferAttribute attach="attributes-position" args={[litPositions, 3]}/></bufferGeometry>
      <lineBasicMaterial color={SELECTED} transparent opacity={.85}/></lineSegments>
    <instancedMesh key={data.nodes.length} ref={mesh} args={[undefined, undefined, data.nodes.length]} castShadow
      onClick={e => {e.stopPropagation(); onPick(e.instanceId ?? null)}} onPointerMove={e => {e.stopPropagation(); onHover(e.instanceId ?? null)}} onPointerOut={() => onHover(null)}>
      <sphereGeometry args={[1, 16, 12]}/><meshStandardMaterial roughness={.45} toneMapped={false}/>
    </instancedMesh>
    <Pulses edges={pulses}/>
  </>;
}

export function ForceGraph3D({graph, visible, colorOf, selected, camera, onSelect, highlight, flow}: ForceProps) {
  const theme = useTheme();
  const [settled, setSettled] = useState(false); const [hover, setHover] = useState<number | null>(null);
  const data = useMemo(() => {
    const ids = graph.filterNodes(id => visible(id)); const index = new Map(ids.map((id, i) => [id, i]));
    const xs = ids.map(id => graph.getNodeAttribute(id, 'x') || 0), ys = ids.map(id => graph.getNodeAttribute(id, 'y') || 0);
    const span = Math.max(1e-6, Math.max(...xs) - Math.min(...xs), Math.max(...ys) - Math.min(...ys)); const scale = Math.sqrt(ids.length) * 9 / span;
    const nodes: FNode[] = ids.map((id, i) => ({id, i, x: (xs[i] - (Math.max(...xs) + Math.min(...xs)) / 2) * scale, y: (ys[i] - (Math.max(...ys) + Math.min(...ys)) / 2) * scale, z: (Math.random() - .5) * 40}));
    const links: {source: any; target: any}[] = []; graph.forEachEdge((_, __, s, t) => {if (index.has(s) && index.has(t)) links.push({source: s, target: t})});
    const sizes = ids.map(id => Math.min(6, 1.1 + Math.sqrt(graph.degree(id)) * .45));
    return {nodes, links, sizes, index, ids};
  }, [graph, visible]);
  useEffect(() => setSettled(false), [data]);
  const focus = useMemo(() => {
    if (highlight?.length) return new Set(highlight);
    if (selected && data.index.has(selected)) return new Set([selected, ...graph.neighbors(selected)]);
    return null;
  }, [highlight, selected, data, graph]);
  const colors = useMemo(() => data.ids.map(id => {
    const c = new THREE.Color(colorOf(graph.getNodeAttributes(id)));
    if (id === selected) return new THREE.Color(SELECTED).multiplyScalar(1.8);
    return focus && !focus.has(id) ? c.lerp(DIM, .85) : c;
  }), [data, colorOf, selected, focus, graph]);
  const flowEdges = useMemo<[number, number, number][]>(() => (flow?.edges || []).map(([a, b, d]) => [data.index.get(a), data.index.get(b), d] as [number | undefined, number | undefined, number])
    .filter((e): e is [number, number, number] => e[0] !== undefined && e[1] !== undefined).slice(0, 800), [flow, data]);
  const [frame, setFrame] = useState<[number, number, number] | null>(null);
  useEffect(() => {if (!camera) return; const i = camera.ids.map(id => data.index.get(id)).find(i => i !== undefined); if (i !== undefined) {const n = data.nodes[i]; setFrame([n.x, n.y, n.z])}}, [camera]);
  const top = useMemo(() => data.ids.map((id, i) => [graph.degree(id), i]).sort((a, b) => b[0] - a[0]).slice(0, 16).map(x => x[1]), [data, graph]);
  const tagFor = (i: number, cls = ''): Tag => {const n = data.nodes[i]; return {key: 'n' + n.id, pos: [0, 0, 0], at: () => [n.x, n.y + data.sizes[i] + .8, n.z], text: String(graph.getNodeAttribute(n.id, 'label')), cls}};
  const sel = selected ? data.index.get(selected) : undefined;
  const tags = uniqueTags([...(sel !== undefined ? [tagFor(sel, 'on'), ...graph.neighbors(selected!).map(n => data.index.get(n)).filter((i): i is number => i !== undefined).slice(0, 14).map(i => tagFor(i))] : top.map(i => tagFor(i))),
    ...(hover !== null ? [tagFor(hover)] : [])]);
  if (!webglAvailable()) return <NoWebGL/>;
  if (!data.nodes.length) return <div className="map-canvas"><div className="explorer-empty">Nothing matches the filters.</div></div>;
  return <div className="map-canvas city-canvas">
    <Scene size={Math.sqrt(data.nodes.length) * 14} onMiss={() => onSelect(null)} tags={tags}>
      <ForceBody data={data} colors={colors} focus={focus} selected={selected} onPick={i => onSelect(i === null ? null : data.ids[i])} onHover={setHover} flowEdges={flowEdges} done={() => setSettled(true)}/>
      <Framer target={frame}/>
    </Scene>
    <div className="map-hud">
      <span className="hud-pill">3D force graph · {data.nodes.length.toLocaleString()} nodes · {data.links.length.toLocaleString()} links</span>
      {!settled && <span className="hud-pill quiet"><Loader2 size={13} className="spin"/>Arranging in 3D</span>}
      {flowEdges.length > 0 && <span className="hud-pill impact">Impact: {flowEdges.length} paths animating</span>}
    </div>
    <div className="city-legend">Drag to pan, right-drag to rotate, scroll to zoom · click a node to see its neighbours · open "Impact of a change" in the inspector to watch the impact travel outward</div>
  </div>;
}

// ---- Terrain: height = density of business rules (or links, properties, members, commits) ----
type TerrainMetric = 'rules' | 'links' | 'properties' | 'members' | 'changes';
const TERRAIN_METRICS: Record<'code' | 'domain', [TerrainMetric, string][]> = {domain: [['rules', 'business rules'], ['links', 'links'], ['properties', 'properties']], code: [['links', 'links'], ['members', 'members']]};  // 'changes' (file commits) is added when git history has been read
const TERRAIN_SIZE = 400, GRID = 140, PEAK = 70;

type TerrainProps = {graph: Graph; mode: 'code' | 'domain'; visible: (id: string) => boolean; colorOf: (a: Obj) => string; selected: string | null; camera: Camera; onSelect: (id: string | null) => void; heat?: Record<string, number[]> | null};

export function Terrain({graph, mode, visible, colorOf, selected, camera, onSelect, heat}: TerrainProps) {
  const theme = useTheme();
  const metrics = TERRAIN_METRICS[mode].concat(mode === 'code' && heat ? [['changes', 'file changes (git)']] : []);
  const [metric, setMetric] = useState<TerrainMetric>(mode === 'domain' ? 'rules' : 'links'); const [hover, setHover] = useState<number | null>(null);
  const land = useMemo(() => {
    const ids = graph.filterNodes(id => visible(id));
    const xs = ids.map(id => graph.getNodeAttribute(id, 'x') || 0), ys = ids.map(id => graph.getNodeAttribute(id, 'y') || 0);
    const minX = Math.min(...xs), maxX = Math.max(...xs), minY = Math.min(...ys), maxY = Math.max(...ys); const span = Math.max(1e-6, maxX - minX, maxY - minY);
    const members = (id: string) => graph.outEdges(id).filter(e => graph.getEdgeAttribute(e, 'kinds').includes('defines')).length;
    const value = (id: string) => {const a = graph.getNodeAttributes(id); return metric === 'rules' ? (a.rules || []).length : metric === 'properties' ? (a.properties || []).length : metric === 'members' ? members(id) : metric === 'changes' ? (a.kind === 'File' ? (heat?.[a.path] || [0])[0] : 0) : graph.degree(id)};  // a file's commits count once, on the file
    const pts = ids.map((id, i) => ({id, x: ((xs[i] - minX) / span - .5) * TERRAIN_SIZE * .9, z: ((ys[i] - minY) / span - .5) * TERRAIN_SIZE * .9, v: value(id)}));
    // Sum a Gaussian bump per node onto the grid; the peak is scaled to a fixed height.
    const h = new Float32Array(GRID * GRID); const cell = TERRAIN_SIZE / (GRID - 1); const sigma = TERRAIN_SIZE / 38; const reach = Math.ceil(3 * sigma / cell);
    for (const p of pts) {
      if (!p.v) continue; const gx = Math.round((p.x + TERRAIN_SIZE / 2) / cell), gz = Math.round((p.z + TERRAIN_SIZE / 2) / cell);
      for (let dz = -reach; dz <= reach; dz++) for (let dx = -reach; dx <= reach; dx++) {
        const x = gx + dx, z = gz + dz; if (x < 0 || z < 0 || x >= GRID || z >= GRID) continue;
        const d2 = (dx * dx + dz * dz) * cell * cell; h[z * GRID + x] += p.v * Math.exp(-d2 / (2 * sigma * sigma));
      }
    }
    let top = 0; for (const v of h) top = Math.max(top, v); const k = top ? PEAK / top : 0; for (let i = 0; i < h.length; i++) h[i] *= k;
    const heightAt = (x: number, z: number) => {const gx = Math.min(GRID - 1, Math.max(0, Math.round((x + TERRAIN_SIZE / 2) / cell))), gz = Math.min(GRID - 1, Math.max(0, Math.round((z + TERRAIN_SIZE / 2) / cell))); return h[gz * GRID + gx]};
    const geometry = new THREE.PlaneGeometry(TERRAIN_SIZE, TERRAIN_SIZE, GRID - 1, GRID - 1); geometry.rotateX(-Math.PI / 2);
    const pos = geometry.attributes.position as THREE.BufferAttribute; const colors = new Float32Array(pos.count * 3); const c = new THREE.Color();
    for (let i = 0; i < pos.count; i++) {
      const gx = i % GRID, gz = Math.floor(i / GRID); const y = h[gz * GRID + gx]; pos.setY(i, y);
      const r = PEAK ? y / PEAK : 0; c.copy(r < .5 ? LOW : MID).lerp(r < .5 ? MID : HIGH, r < .5 ? r * 2 : (r - .5) * 2); colors.set([c.r, c.g, c.b], i * 3);
    }
    geometry.setAttribute('color', new THREE.BufferAttribute(colors, 3)); geometry.computeVertexNormals();
    const ranked = [...pts.keys()].sort((a, b) => pts[b].v - pts[a].v);
    const pins = ranked.filter(i => pts[i].v > 0).slice(0, 400).map(i => ({...pts[i], y: heightAt(pts[i].x, pts[i].z)}));
    const areas = new Map<string, {x: number; z: number; n: number}>();
    for (const p of pts) {const m = graph.getNodeAttribute(p.id, 'module'); if (!m || m === 'Unlinked') continue; const a = areas.get(m) || {x: 0, z: 0, n: 0}; a.x += p.x; a.z += p.z; a.n++; areas.set(m, a)}
    const areaTags: Tag[] = [...areas].filter(([, a]) => a.n >= 3).sort((a, b) => b[1].n - a[1].n).slice(0, 12).map(([m, a]) => ({key: 'a' + m, pos: [a.x / a.n, heightAt(a.x / a.n, a.z / a.n) + 4, a.z / a.n], text: m.split('/').slice(-2).join('/'), cls: 'district'}));
    return {geometry, pins, areaTags, total: pts.reduce((s, p) => s + p.v, 0)};
  }, [graph, visible, metric, heat, theme]);
  useEffect(() => () => land.geometry.dispose(), [land]);
  const pinMesh = useRef<THREE.InstancedMesh>(null);
  useLayoutEffect(() => {
    const m = pinMesh.current; if (!m) return; const o = new THREE.Object3D(); const c = new THREE.Color();
    land.pins.forEach((p, i) => {
      o.position.set(p.x, p.y + 1.2, p.z); o.scale.setScalar(p.id === selected ? 2.4 : 1.2); o.updateMatrix(); m.setMatrixAt(i, o.matrix);
      m.setColorAt(i, p.id === selected ? c.set(SELECTED).multiplyScalar(1.8) : c.set(colorOf(graph.getNodeAttributes(p.id))));
    });
    m.instanceMatrix.needsUpdate = true; if (m.instanceColor) m.instanceColor.needsUpdate = true; m.computeBoundingSphere();
  }, [land, selected, colorOf, graph, theme]);
  const [frame, setFrame] = useState<[number, number, number] | null>(null);
  useEffect(() => {if (!camera) return; const p = land.pins.find(p => camera.ids.includes(p.id)); if (p) setFrame([p.x, p.y, p.z])}, [camera]);
  const label = (p: {id: string; x: number; y: number; z: number; v: number}, cls = ''): Tag => ({key: 'p' + p.id, pos: [p.x, p.y + 3.5, p.z], text: String(graph.getNodeAttribute(p.id, 'label')), sub: `${p.v} ${metrics.find(m => m[0] === metric)?.[1] || metric}`, cls});
  const selPin = land.pins.find(p => p.id === selected);
  const tags = uniqueTags([...land.areaTags, ...land.pins.slice(0, 10).map(p => label(p)), ...(selPin ? [label(selPin, 'on')] : []), ...(hover !== null && land.pins[hover] ? [label(land.pins[hover])] : [])]);
  if (!webglAvailable()) return <NoWebGL/>;
  return <div className="map-canvas city-canvas">
    <Scene size={TERRAIN_SIZE * .9} onMiss={() => onSelect(null)} tags={tags}>
      <mesh geometry={land.geometry} receiveShadow castShadow raycast={() => null}><meshStandardMaterial vertexColors roughness={.9} flatShading={false}/></mesh>
      <mesh geometry={land.geometry} position={[0, .05, 0]} raycast={() => null}><meshBasicMaterial wireframe color={graphTokens().primary} transparent opacity={.06}/></mesh>
      {land.pins.length > 0 && <instancedMesh key={land.pins.length} ref={pinMesh} args={[undefined, undefined, land.pins.length]} castShadow
        onClick={e => {e.stopPropagation(); if (e.instanceId !== undefined) onSelect(land.pins[e.instanceId].id)}} onPointerMove={e => {e.stopPropagation(); setHover(e.instanceId ?? null)}} onPointerOut={() => setHover(null)}>
        <sphereGeometry args={[1, 12, 10]}/><meshStandardMaterial toneMapped={false} roughness={.4}/></instancedMesh>}
      <Framer target={frame}/>
    </Scene>
    <div className="map-hud">
      <span className="hud-pill">{mode === 'domain' ? 'Rule terrain' : 'Code terrain'} · {land.total.toLocaleString()} {metrics.find(m => m[0] === metric)?.[1]}</span>
      <div className="segmented small hud-seg" role="group" aria-label="Terrain height">{metrics.map(([m, l]) => <button key={m} aria-pressed={metric === m} onClick={() => setMetric(m as TerrainMetric)}>Height: {l}</button>)}</div>
    </div>
    <div className="city-legend"><span className="terrain-ramp"/>low → high density · mountains are where {metrics.find(m => m[0] === metric)?.[1]} concentrate on the map layout · pins mark the {land.pins.length} {mode === 'domain' ? 'concepts' : 'entities'} that contribute · click a pin to inspect it</div>
  </div>;
}

// ---- Version stack: each ontology version is a layer; concepts keep their place from layer to layer ----
type StackData = {names: string[]; edges: [number, number][]; versions: {id: string; time: string; status: string; members: number[]; added: number[]; removed: number[]}[]};
const STACK_GAP = 10, STACK_SPAN = 220;

function StackBody({data, pos, selected, onSelect, picked, onPick, onHover}: {data: StackData; pos: [number, number][]; selected: string | null; onSelect: (id: string) => void; picked: number | null; onPick: (i: number | null) => void; onHover: (h: {name: number; layer: number} | null) => void}) {
  const theme = useTheme();
  const mesh = useRef<THREE.InstancedMesh>(null);
  const points = useMemo(() => {
    const out: {name: number; layer: number; kind: 'kept' | 'added' | 'removed'}[] = [];
    data.versions.forEach((v, l) => {const added = new Set(v.added); for (const n of v.members) out.push({name: n, layer: l, kind: added.has(n) ? 'added' : 'kept'}); for (const n of v.removed) out.push({name: n, layer: l, kind: 'removed'})});
    return out;
  }, [data]);
  useLayoutEffect(() => {
    const m = mesh.current; if (!m) return; const o = new THREE.Object3D(); const c = new THREE.Color();
    points.forEach((p, i) => {
      const [x, z] = pos[p.name]; const on = data.versions[p.layer].id === selected; const isPicked = p.name === picked;
      o.position.set(x, p.layer * STACK_GAP + 1, z); o.scale.setScalar(isPicked ? 2.2 : p.kind === 'kept' ? (on ? 1.2 : .8) : 1.6); o.updateMatrix(); m.setMatrixAt(i, o.matrix);
      m.setColorAt(i, isPicked ? c.set(SELECTED).multiplyScalar(1.8) : p.kind === 'added' ? c.set(graphTokens().success).multiplyScalar(on ? 2.2 : 1.4) : p.kind === 'removed' ? c.set(graphTokens().danger).multiplyScalar(on ? 2 : 1.2) : c.set(on ? '#3d5a73' : '#a9b7c2'));
    });
    m.instanceMatrix.needsUpdate = true; if (m.instanceColor) m.instanceColor.needsUpdate = true; m.computeBoundingSphere();
  }, [points, pos, selected, picked, data, theme]);
  const lineage = useMemo(() => {
    if (picked === null) return null; const pts: number[] = []; let prev: number | null = null;
    data.versions.forEach((v, l) => {if (v.members.includes(picked)) {if (prev !== null && prev === l - 1) pts.push(pos[picked][0], (l - 1) * STACK_GAP + 1, pos[picked][1], pos[picked][0], l * STACK_GAP + 1, pos[picked][1]); prev = l}});
    return new Float32Array(pts);
  }, [picked, data, pos]);
  return <>
    {data.versions.map((v, l) => <mesh key={v.id} position={[0, l * STACK_GAP, 0]} rotation={[-Math.PI / 2, 0, 0]} onClick={e => {e.stopPropagation(); onSelect(v.id)}} receiveShadow>
      <planeGeometry args={[STACK_SPAN * 1.15, STACK_SPAN * 1.15]}/>
      <meshStandardMaterial color={v.id === selected ? graphTokens().primary : v.status === 'draft' ? graphTokens().warning : graphTokens().border} transparent opacity={v.id === selected ? .55 : .18} side={THREE.DoubleSide} depthWrite={false}/>
    </mesh>)}
    {lineage && lineage.length > 0 && <lineSegments raycast={() => null}><bufferGeometry><bufferAttribute attach="attributes-position" args={[lineage, 3]}/></bufferGeometry><lineBasicMaterial color={[2.4, 1.3, .2]} toneMapped={false}/></lineSegments>}
    <instancedMesh key={points.length} ref={mesh} args={[undefined, undefined, points.length]} castShadow
      onClick={e => {e.stopPropagation(); if (e.instanceId !== undefined) onPick(points[e.instanceId].name)}} onPointerMove={e => {e.stopPropagation(); if (e.instanceId !== undefined) onHover(points[e.instanceId])}} onPointerOut={() => onHover(null)}>
      <sphereGeometry args={[1, 10, 8]}/><meshStandardMaterial toneMapped={false} roughness={.5}/>
    </instancedMesh>
  </>;
}

export function VersionStack({sourceId, selected, onSelect}: {sourceId: string; selected: string | null; onSelect: (id: string) => void}) {
  const [data, setData] = useState<StackData | null>(null); const [error, setError] = useState(''); const [picked, setPicked] = useState<number | null>(null); const [hover, setHover] = useState<{name: number; layer: number} | null>(null);
  useEffect(() => {let live = true; fetch('/api/timeline/stack?source_id=' + encodeURIComponent(sourceId)).then(r => r.ok ? r.json() : Promise.reject(new Error('The version stack could not be loaded'))).then(d => live && setData(d)).catch(e => live && setError(e.message)); return () => {live = false}}, [sourceId]);
  // One layout for every layer, so a concept stays in place from version to version.
  const pos = useMemo<[number, number][]>(() => {
    if (!data) return []; const nodes = data.names.map((_, i) => ({i, x: Math.cos(i) * 50, y: Math.sin(i) * 50})) as {i: number; x: number; y: number}[];
    const sim = force2d(nodes as any).force('link', link2d(data.edges.map(([a, b]) => ({source: a, target: b}))).id((d: any) => d.i).distance(12).strength(.4))
      .force('charge', charge2d().strength(-22)).force('center', center2d(0, 0)).force('x', x2d(0).strength(.09)).force('y', y2d(0).strength(.09)).stop();
    for (let k = 0; k < 260; k++) sim.tick();
    const xs = nodes.map(n => n.x), ys = nodes.map(n => n.y); const span = Math.max(1e-6, Math.max(...xs) - Math.min(...xs), Math.max(...ys) - Math.min(...ys));
    const cx = (Math.max(...xs) + Math.min(...xs)) / 2, cy = (Math.max(...ys) + Math.min(...ys)) / 2;
    return nodes.map(n => [(n.x - cx) / span * STACK_SPAN, (n.y - cy) / span * STACK_SPAN]);
  }, [data]);
  if (error) return <div className="alert error">{error}</div>;
  if (!data) return <div className="loading"><Loader2 className="spin"/>Loading versions…</div>;
  if (!webglAvailable()) return <NoWebGL/>;
  const height = data.versions.length * STACK_GAP;
  const layerTags: Tag[] = data.versions.map((v, l) => ({key: 'v' + v.id, pos: [STACK_SPAN * .6, l * STACK_GAP + 1, STACK_SPAN * .6], text: `v${l + 1} · ${new Date(v.time).toLocaleDateString(undefined, {month: 'short', day: 'numeric'})}`,
    sub: l ? `+${v.added.length} −${v.removed.length} · ${v.members.length} concepts` : `${v.members.length} concepts`, cls: v.id === selected ? 'on' : ''}));
  const present = picked === null ? [] : data.versions.map((v, l) => v.members.includes(picked) ? l + 1 : 0).filter(Boolean);
  const tags = uniqueTags([...layerTags, ...(picked !== null ? [{key: 'pick', pos: [pos[picked][0], height, pos[picked][1]] as [number, number, number], text: data.names[picked], sub: `in versions ${present[0]}–${present[present.length - 1]} (${present.length} of ${data.versions.length})`, cls: 'on'}] : []),
    ...(hover ? [{key: 'hover', pos: [pos[hover.name][0], hover.layer * STACK_GAP + 3, pos[hover.name][1]] as [number, number, number], text: data.names[hover.name]}] : [])]);
  return <div className="map-canvas city-canvas stack-canvas">
    <Scene size={Math.max(STACK_SPAN, height) * 1.1} onMiss={() => setPicked(null)} tags={tags}>
      <group position={[0, -height / 2, 0]}><StackBody data={data} pos={pos} selected={selected} onSelect={onSelect} picked={picked} onPick={setPicked} onHover={setHover}/></group>
    </Scene>
    <div className="map-hud"><span className="hud-pill">{data.versions.length} versions · {data.names.length} concepts ever</span></div>
    <div className="city-legend"><b className="dot add"/>added in that version <b className="dot del"/>removed in that version <b className="dot keep"/>unchanged · click a layer to open that version below · click a concept to trace it through the versions</div>
  </div>;
}
