// Explore: an always-alive 3D graph to play with. Built on three-forcegraph (via r3f-forcegraph) inside the shared
// scene, so glow, shadows, quality and VR/AR keep working. Physics runs continuously and gently; nodes can be dragged
// and pinned; links carry particles in the dependency direction; the camera flies to what you pick; focus mode keeps
// only the neighbourhood; impact analysis sends a shockwave and particles down the chain. Space (dark) is the default
// theme; subtle sounds accompany interactions and can be muted. Reduced-motion preferences turn the motion off.
import React, {useCallback, useEffect, useMemo, useRef, useState} from 'react';
import Graph from 'graphology';
import * as THREE from 'three';
import {useFrame, useThree} from '@react-three/fiber';
import {Stars} from '@react-three/drei';
import ForceGraph from 'r3f-forcegraph';
import {Scene, uniqueTags, webglAvailable} from './city3d';
import type {Camera, Flow, Tag} from './city3d';
import {Pause, Play, Crosshair, RotateCcw, Volume2, VolumeX, Sparkles, Waves} from 'lucide-react';

type Obj = Record<string, any>;
type ENode = {id: string; label: string; color: string; degree: number; r: number; x?: number; y?: number; z?: number; vx?: number; vy?: number; vz?: number; fx?: number; fy?: number; fz?: number};
type ELink = {source: string | ENode; target: string | ENode; kind: string};
type Theme = 'space' | 'studio';
type Particles = 'all' | 'selection' | 'off';

const BIG = 1500;  // above this many nodes the graph settles instead of breathing forever, and particles start on the selection only
const reducedMotion = () => {try {return window.matchMedia('(prefers-reduced-motion: reduce)').matches} catch {return false}};
const stored = (k: string, d: string) => {try {return localStorage.getItem(k) ?? d} catch {return d}};
const save = (k: string, v: string) => {try {localStorage.setItem(k, v)} catch { /* storage unavailable */ }};
const idOf = (end: string | ENode) => typeof end === 'string' ? end : end.id;

// ---- Subtle interaction sounds, synthesised with Web Audio (no files) ----
class Sound {
  ctx: AudioContext | null = null; on = true; last = 0;
  private audio() {
    if (!this.on) return null;
    try {this.ctx = this.ctx || new (window.AudioContext || (window as unknown as {webkitAudioContext: typeof AudioContext}).webkitAudioContext)(); if (this.ctx.state === 'suspended') this.ctx.resume()} catch {return null}
    return this.ctx;
  }
  private tone(freq: number, dur: number, vol: number, type: OscillatorType = 'sine', to?: number, delay = 0) {
    const ctx = this.audio(); if (!ctx) return; const t = ctx.currentTime + delay;
    const osc = ctx.createOscillator(); const gain = ctx.createGain(); osc.type = type; osc.frequency.setValueAtTime(freq, t); if (to) osc.frequency.exponentialRampToValueAtTime(to, t + dur);
    gain.gain.setValueAtTime(0, t); gain.gain.linearRampToValueAtTime(vol, t + .01); gain.gain.exponentialRampToValueAtTime(.0001, t + dur);
    osc.connect(gain).connect(ctx.destination); osc.start(t); osc.stop(t + dur + .02);
  }
  hover() {const now = performance.now(); if (now - this.last < 90) return; this.last = now; this.tone(1320, .05, .018)}
  select() {this.tone(660, .12, .045); this.tone(990, .16, .035, 'sine', undefined, .07)}
  grab() {this.tone(220, .09, .05, 'triangle', 300)}
  drop() {this.tone(300, .12, .05, 'triangle', 180)}
  toggle(on: boolean) {this.tone(on ? 440 : 520, .14, .04, 'sine', on ? 620 : 360)}
  whoosh() {this.tone(180, .5, .04, 'sawtooth', 900)}
  impact() {this.tone(110, .7, .07, 'sine', 55); this.tone(880, .25, .02, 'sine', 1760, .1)}
}
const sound = new Sound();

// ---- Camera: eased fly-to, idle orbit, fit ----
type Flight = {pos: THREE.Vector3; look: THREE.Vector3; from?: THREE.Vector3; fromLook?: THREE.Vector3; t: number} | null;
function CameraRig({flight, done, idleOrbit}: {flight: React.MutableRefObject<Flight>; done: () => void; idleOrbit: React.MutableRefObject<number>}) {
  const {camera, controls} = useThree() as unknown as {camera: THREE.PerspectiveCamera; controls: {target: THREE.Vector3; update: () => void; autoRotate: boolean; autoRotateSpeed: number; enabled: boolean} | null};
  useFrame((_, dt) => {
    if (!controls) return;
    const f = flight.current;
    if (f) {
      if (!f.from) {f.from = camera.position.clone(); f.fromLook = controls.target.clone(); f.t = 0}
      f.t = Math.min(1, f.t + dt / .95); const k = f.t < .5 ? 4 * f.t ** 3 : 1 - (-2 * f.t + 2) ** 3 / 2;
      camera.position.lerpVectors(f.from, f.pos, k); controls.target.lerpVectors(f.fromLook!, f.look, k); controls.update();
      if (f.t >= 1) {flight.current = null; done()}
    }
    const idle = !reducedMotion() && performance.now() - idleOrbit.current > 9000;
    if (controls.autoRotate !== idle) {controls.autoRotate = idle; controls.autoRotateSpeed = .35}
  });
  return null;
}

// ---- Dragging: grab a node, move it on a plane facing the camera, drop to pin it ----
function useDrag(fg: React.MutableRefObject<any>, onDrag: (on: boolean) => void, onPinned: () => void) {
  const {camera, gl, controls} = useThree() as unknown as {camera: THREE.Camera; gl: THREE.WebGLRenderer; controls: {enabled: boolean} | null};
  const moved = useRef(false);
  const start = useCallback((e: Obj) => {
    const hit = (e.intersections as Obj[]).map(i => {let o = i.object; while (o && !Object.prototype.hasOwnProperty.call(o, '__graphObjType')) o = o.parent; return o}).find(o => o?.__graphObjType === 'node');
    if (!hit) return; const node: ENode = hit.__data; e.stopPropagation(); moved.current = false;
    const plane = new THREE.Plane(); const normal = new THREE.Vector3(); camera.getWorldDirection(normal);
    plane.setFromNormalAndCoplanarPoint(normal, new THREE.Vector3(node.x, node.y, node.z));
    const ray = new THREE.Raycaster(); const point = new THREE.Vector3(); const rect = gl.domElement.getBoundingClientRect(); const x0 = e.clientX, y0 = e.clientY;
    if (controls) controls.enabled = false; onDrag(true); sound.grab();
    const move = (ev: PointerEvent) => {
      if (Math.hypot(ev.clientX - x0, ev.clientY - y0) > 4) moved.current = true; if (!moved.current) return;
      ray.setFromCamera(new THREE.Vector2((ev.clientX - rect.left) / rect.width * 2 - 1, -((ev.clientY - rect.top) / rect.height) * 2 + 1), camera);
      if (ray.ray.intersectPlane(plane, point)) {node.fx = point.x; node.fy = point.y; node.fz = point.z; fg.current?.resetCountdown()}
    };
    const up = () => {
      window.removeEventListener('pointermove', move); window.removeEventListener('pointerup', up);
      if (controls) controls.enabled = true; onDrag(false);
      if (moved.current) {sound.drop(); onPinned()}  // dropped where released: pinned until double-clicked
    };
    window.addEventListener('pointermove', move); window.addEventListener('pointerup', up);
  }, [camera, gl, controls, fg, onDrag, onPinned]);
  return {start, wasDrag: () => moved.current};
}

// ---- The graph inside the scene ----
type BodyProps = {data: {nodes: ENode[]; links: ELink[]}; theme: Theme; paused: boolean; particles: Particles; focus: Set<string> | null; selected: string | null; hover: string | null;
  fgRef: React.MutableRefObject<any>; meshes: React.MutableRefObject<Map<string, THREE.Mesh>>; onHover: (id: string | null) => void; onPick: (id: string) => void; onUnpin: (id: string) => void;
  onDrag: (on: boolean) => void; onPinned: () => void; breathe: boolean; impact: {start: string; links: Set<string>; nonce: number} | null};

const SPHERE = new THREE.SphereGeometry(1, 18, 14);
function GraphBody({data, theme, paused, particles, focus, selected, hover, fgRef, meshes, onHover, onPick, onUnpin, onDrag, onPinned, breathe, impact}: BodyProps) {
  const mats = useRef(new Map<string, THREE.Material>());
  const material = useCallback((color: string, state: 'normal' | 'dim' | 'hot') => {
    const key = color + state; let m = mats.current.get(key);
    if (!m) {
      const c = new THREE.Color(color);
      if (state === 'hot') c.set('#ffb347').multiplyScalar(1.7); else if (state === 'dim') c.lerp(new THREE.Color(theme === 'space' ? '#1b2233' : '#dfe5e1'), .82); else if (theme === 'space') c.multiplyScalar(1.25);
      m = theme === 'space' || state === 'hot' ? new THREE.MeshBasicMaterial({color: c, toneMapped: false, transparent: state === 'dim', opacity: state === 'dim' ? .45 : 1}) : new THREE.MeshStandardMaterial({color: c, roughness: .4, toneMapped: false});
      mats.current.set(key, m);
    }
    return m;
  }, [theme]);
  useEffect(() => {mats.current.clear(); meshes.current.clear()}, [theme]);
  const nodeObject = useCallback((n: ENode) => {
    const mesh = new THREE.Mesh(SPHERE, material(n.color, 'normal')); mesh.scale.setScalar(n.r); meshes.current.set(n.id, mesh); return mesh;
  }, [material]);
  // Hover, selection and focus restyle the existing meshes: no rebuild, so they stay instant on large graphs.
  useEffect(() => {
    for (const n of data.nodes) {
      const m = meshes.current.get(n.id); if (!m) continue;
      const hot = n.id === selected || n.id === hover; const dim = !!focus && !focus.has(n.id);
      m.material = material(n.color, hot ? 'hot' : dim ? 'dim' : 'normal'); m.scale.setScalar(n.r * (n.id === selected ? 1.35 : n.id === hover ? 1.25 : 1));
    }
  }, [data, selected, hover, focus, material, meshes]);
  const lit = useCallback((l: ELink) => {const s = idOf(l.source), t = idOf(l.target); return !!selected && (s === selected || t === selected) || !!impact && impact.links.has(s + '>' + t)}, [selected, impact]);
  const linkColor = useCallback((l: ELink) => lit(l) ? (theme === 'space' ? '#ffd27a' : '#e8590c') : focus && !(focus.has(idOf(l.source)) && focus.has(idOf(l.target))) ? (theme === 'space' ? 'rgba(80,100,140,0.08)' : 'rgba(150,165,160,0.1)') : (theme === 'space' ? '#6f8fd8' : '#9fb3ab'), [lit, focus, theme]);
  const particleCount = useCallback((l: ELink) => particles === 'off' ? 0 : lit(l) ? 4 : particles === 'all' ? 1 : 0, [particles, lit]);
  const visible = useCallback((n: ENode) => !focus || focus.has(n.id), [focus]);
  const linkVisible = useCallback((l: ELink) => !focus || (focus.has(idOf(l.source)) && focus.has(idOf(l.target))), [focus]);
  useFrame(() => {if (!paused) fgRef.current?.tickFrame()});
  // Impact: a burst of particles down every link of the chain, outward from the changed node.
  useEffect(() => {
    if (!impact || !fgRef.current) return; sound.impact();
    const chain = data.links.filter(l => impact.links.has(idOf(l.source) + '>' + idOf(l.target)));
    const timers = chain.map((l, i) => window.setTimeout(() => fgRef.current?.emitParticle(l), (i % 40) * 35));
    return () => timers.forEach(t => window.clearTimeout(t));
  }, [impact]);
  const drag = useDrag(fgRef, onDrag, onPinned);
  const group = useRef<THREE.Group>(null);
  const quiet = useCallback(() => {group.current?.traverse(o => {if ((o as Obj).__graphObjType === 'link' || (o as Obj).__graphObjType === 'particle') o.raycast = () => {}})}, []);
  return <group ref={group} onPointerDown={drag.start} onDoubleClick={e => {const o = e.intersections.map(i => {let x: Obj | null = i.object; while (x && !x.__graphObjType) x = x.parent; return x}).find(x => x?.__graphObjType === 'node'); if (o) {e.stopPropagation(); onUnpin(o.__data.id)}}}>
    <ForceGraph ref={fgRef} graphData={data as never} nodeId="id" nodeThreeObject={nodeObject as never} nodeVisibility={visible as never} linkVisibility={linkVisible as never}
      linkColor={linkColor as never} linkOpacity={theme === 'space' ? .32 : .5} linkWidth={((l: ELink) => lit(l) ? .45 : 0) as never} linkCurvature={.12}
      linkDirectionalParticles={particleCount as never} linkDirectionalParticleWidth={((l: ELink) => lit(l) ? 1.8 : 1.2) as never} linkDirectionalParticleSpeed={.006}
      linkDirectionalParticleColor={(() => theme === 'space' ? '#ffe3a3' : '#e8590c') as never} linkDirectionalParticleResolution={6}
      d3AlphaDecay={.018} d3VelocityDecay={.28} d3AlphaTarget={breathe ? .004 : 0} cooldownTicks={breathe ? Infinity : 900} cooldownTime={breathe ? Infinity : 20000} warmupTicks={reducedMotion() ? 140 : 0}
      onFinishUpdate={quiet}
      onNodeHover={((n: ENode | null) => {onHover(n ? n.id : null); if (n) sound.hover(); document.body.style.cursor = n ? 'grab' : ''}) as never}
      onNodeClick={((n: ENode) => {if (drag.wasDrag()) return; onPick(n.id)}) as never}/>
  </group>;
}

// ---- Minimap: a top-down sketch of the layout with the camera's position; click to fly there ----
function Minimap({nodes, onFly, camPos}: {nodes: ENode[]; onFly: (n: ENode) => void; camPos: React.MutableRefObject<THREE.Vector3>}) {
  const [, setTick] = useState(0); useEffect(() => {const id = window.setInterval(() => setTick(t => t + 1), 500); return () => window.clearInterval(id)}, []);
  const sample = nodes.length > 1500 ? nodes.filter((_, i) => i % Math.ceil(nodes.length / 1500) === 0) : nodes;
  const xs = sample.map(n => n.x || 0), zs = sample.map(n => n.z || 0); const minX = Math.min(...xs, -1), maxX = Math.max(...xs, 1), minZ = Math.min(...zs, -1), maxZ = Math.max(...zs, 1);
  const s = 120 / Math.max(maxX - minX, maxZ - minZ); const px = (x: number) => 4 + (x - minX) * s, pz = (z: number) => 4 + (z - minZ) * s;
  const cam = camPos.current;
  return <svg className="explore-minimap" viewBox="0 0 128 128" role="img" aria-label="Overview map; click to fly there"
    onClick={e => {const b = (e.currentTarget as SVGSVGElement).getBoundingClientRect(); const x = (e.clientX - b.left) / b.width * 128, z = (e.clientY - b.top) / b.height * 128;
      let best: ENode | null = null, d = Infinity; for (const n of sample) {const dd = (px(n.x || 0) - x) ** 2 + (pz(n.z || 0) - z) ** 2; if (dd < d) {d = dd; best = n}} if (best) onFly(best)}}>
    {sample.map(n => <circle key={n.id} cx={px(n.x || 0)} cy={pz(n.z || 0)} r={.9} fill={n.color} opacity={.8}/>)}
    <circle cx={Math.min(124, Math.max(4, px(cam.x)))} cy={Math.min(124, Math.max(4, pz(cam.z)))} r={3.2} className="explore-minimap-cam"/>
  </svg>;
}
function CamTracker({camPos}: {camPos: React.MutableRefObject<THREE.Vector3>}) {useFrame(({camera}) => {camPos.current.copy(camera.position)}); return null}

// ---- The view ----
type Props = {graph: Graph; visible: (id: string) => boolean; colorOf: (a: Obj) => string; selected: string | null; camera: Camera; onSelect: (id: string | null) => void; highlight?: string[] | null; flow?: Flow};

export function Explore3D({graph, visible, colorOf, selected, camera, onSelect, flow}: Props) {
  const [theme, setTheme] = useState<Theme>(() => stored('onto.explore.theme', 'space') === 'studio' ? 'studio' : 'space');
  const [muted, setMuted] = useState(() => stored('onto.explore.sound', 'on') === 'off'); sound.on = !muted;
  const [paused, setPaused] = useState(false); const [hover, setHover] = useState<string | null>(null); const [dragging, setDragging] = useState(false);
  const [focusOn, setFocusOn] = useState(false); const [hops, setHops] = useState(1); const [trail, setTrail] = useState<string[]>([]); const [pins, setPins] = useState(0);
  const [intro, setIntro] = useState(() => !reducedMotion()); const [progress, setProgress] = useState(0);
  const fg = useRef<any>(null); const meshes = useRef(new Map<string, THREE.Mesh>()); const flight = useRef<Flight>(null); const idle = useRef(performance.now()); const camPos = useRef(new THREE.Vector3(0, 0, 400));

  const data = useMemo(() => {
    const ids = graph.filterNodes(id => visible(id)); const keep = new Set(ids); const motion = !reducedMotion();
    // Every node starts at the centre, so the graph bursts outward and assembles (unless motion is reduced).
    const nodes: ENode[] = ids.map(id => {const a = graph.getNodeAttributes(id); const d = graph.degree(id); const j = () => (Math.random() - .5) * (motion ? 4 : 300);
      return {id, label: String(a.label), color: colorOf(a), degree: d, r: Math.min(9, 1.6 + Math.sqrt(d) * .75), x: j(), y: j(), z: j()}});
    const links: ELink[] = []; graph.forEachEdge((_, a, s, t) => {if (keep.has(s) && keep.has(t)) links.push({source: s, target: t, kind: a.kind})});
    return {nodes, links};
  }, [graph, visible, colorOf]);
  const byId = useMemo(() => new Map(data.nodes.map(n => [n.id, n])), [data]);
  const big = data.nodes.length > BIG;
  const [particles, setParticles] = useState<Particles>(() => big || reducedMotion() ? 'selection' : 'all');
  useEffect(() => {setParticles(big || reducedMotion() ? 'selection' : 'all')}, [big]);
  useEffect(() => {
    if (!intro) return; const start = performance.now(); let raf = 0;
    const step = () => {const p = Math.min(1, (performance.now() - start) / 1600); setProgress(p); if (p < 1) raf = requestAnimationFrame(step); else window.setTimeout(() => setIntro(false), 250)};
    raf = requestAnimationFrame(step); return () => cancelAnimationFrame(raf);
  }, [data]);

  const fly = useCallback((n: ENode | undefined, distance?: number) => {
    if (!n || n.x === undefined) return; const look = new THREE.Vector3(n.x, n.y, n.z);
    const dir = camPos.current.clone().sub(look); if (dir.lengthSq() < 1) dir.set(0, .3, 1); dir.normalize();
    const pos = look.clone().add(dir.multiplyScalar(distance ?? 110 + n.r * 12));
    if (reducedMotion()) flight.current = {pos, look, from: pos, fromLook: look, t: 1}; else {flight.current = {pos, look, t: 0}; sound.whoosh()}
  }, []);
  const pick = useCallback((id: string) => {idle.current = performance.now(); sound.select(); onSelect(id); setTrail(t => [...t.filter(x => x !== id), id].slice(-7)); fly(byId.get(id))}, [onSelect, fly, byId]);
  useEffect(() => {if (!camera) return; const n = camera.ids.map(id => byId.get(id)).find(Boolean); if (n) {fly(n); setTrail(t => [...t.filter(x => x !== n.id), n.id].slice(-7))}}, [camera]);
  const fit = useCallback(() => {
    const box = fg.current?.getGraphBbox?.(); if (!box) return; const c = new THREE.Vector3((box.x[0] + box.x[1]) / 2, (box.y[0] + box.y[1]) / 2, (box.z[0] + box.z[1]) / 2);
    const r = Math.max(box.x[1] - box.x[0], box.y[1] - box.y[0], box.z[1] - box.z[0]) || 100;
    flight.current = {pos: c.clone().add(new THREE.Vector3(.35, .45, 1).normalize().multiplyScalar(r * 1.15)), look: c, t: 0}; sound.whoosh();
  }, []);
  // Focus mode keeps the selection and its neighbourhood (1-3 steps); without a selection it keeps the hovered node's.
  const focus = useMemo(() => {
    const centre = selected && byId.has(selected) ? selected : null; if (!focusOn || !centre) return null;
    const out = new Set([centre]); let frontier = [centre];
    for (let h = 0; h < hops; h++) {const next: string[] = []; for (const id of frontier) for (const n of graph.neighbors(id)) if (byId.has(n) && !out.has(n)) {out.add(n); next.push(n)} frontier = next}
    return out;
  }, [focusOn, hops, selected, graph, byId]);
  const impact = useMemo(() => flow?.edges.length ? {start: flow.start, links: new Set(flow.edges.flatMap(([a, b]) => [a + '>' + b, b + '>' + a])), nonce: Date.now()} : null, [flow]);
  const togglePause = useCallback(() => {setPaused(p => {sound.toggle(p); return !p})}, []);
  const shake = useCallback(() => {for (const n of data.nodes) if (n.fx === undefined) {n.vx = (n.vx || 0) + (Math.random() - .5) * 8; n.vy = (n.vy || 0) + (Math.random() - .5) * 8; n.vz = (n.vz || 0) + (Math.random() - .5) * 8}
    fg.current?.d3ReheatSimulation(); setPaused(false); sound.whoosh()}, [data]);
  const unpin = useCallback((id: string) => {const n = byId.get(id); if (!n) return; delete n.fx; delete n.fy; delete n.fz; fg.current?.resetCountdown(); setPins(data.nodes.filter(x => x.fx !== undefined).length); sound.drop()}, [byId, data]);
  const unpinAll = useCallback(() => {for (const n of data.nodes) {delete n.fx; delete n.fy; delete n.fz} fg.current?.d3ReheatSimulation(); setPins(0)}, [data]);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement; if (el && (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' || el.isContentEditable)) return;
      if (e.code === 'Space') {e.preventDefault(); togglePause()} else if (e.key === 'f' || e.key === 'F') {setFocusOn(v => !v); sound.toggle(true)} else if (e.key === 'r' || e.key === 'R') fit();
      else if (e.key === 'Escape' && trail.length > 1) {const prev = trail[trail.length - 2]; setTrail(trail.slice(0, -1)); onSelect(prev); fly(byId.get(prev))}
      idle.current = performance.now();
    };
    window.addEventListener('keydown', onKey); return () => window.removeEventListener('keydown', onKey);
  }, [togglePause, fit, trail, onSelect, fly, byId]);

  const top = useMemo(() => [...data.nodes].sort((a, b) => b.degree - a.degree).slice(0, 14), [data]);
  const tag = (n: ENode, cls = ''): Tag => ({key: 'n' + n.id, pos: [0, 0, 0], at: () => [n.x || 0, (n.y || 0) + n.r * 1.8 + 1.5, n.z || 0], text: n.label, cls});
  const sel = selected ? byId.get(selected) : undefined; const hov = hover ? byId.get(hover) : undefined;
  const tags = intro ? [] : uniqueTags([...(sel ? [tag(sel, 'on'), ...graph.neighbors(sel.id).map(id => byId.get(id)).filter((n): n is ENode => !!n).sort((a, b) => b.degree - a.degree).slice(0, 12).map(n => tag(n))] : top.map(n => tag(n))), ...(hov ? [tag(hov)] : [])]);

  if (!webglAvailable()) return <div className="map-canvas"><div className="explorer-empty error">3D needs WebGL, which is not available in this browser.</div></div>;
  if (!data.nodes.length) return <div className="map-canvas"><div className="explorer-empty">Nothing matches the filters.</div></div>;
  const space = theme === 'space'; const size = Math.max(160, Math.sqrt(data.nodes.length) * 22);
  return <div className={'map-canvas city-canvas explore-canvas ' + theme} onPointerDown={() => {idle.current = performance.now()}} onWheel={() => {idle.current = performance.now()}}>
    <Scene size={size} onMiss={() => {onSelect(null); idle.current = performance.now()}} tags={tags} background={space ? '#05070f' : '#f6f7f9'} bloom={space ? 1.35 : .9}>
      {space && <><Stars radius={size * 2.2} depth={size} count={3500} factor={4} saturation={0} fade speed={reducedMotion() ? 0 : .6}/><fog attach="fog" args={['#05070f', size * 1.4, size * 5]}/></>}
      <GraphBody data={data} theme={theme} paused={paused} particles={particles} focus={focus} selected={selected} hover={hover} fgRef={fg} meshes={meshes}
        onHover={setHover} onPick={pick} onUnpin={unpin} onDrag={setDragging} onPinned={() => setPins(data.nodes.filter(x => x.fx !== undefined).length)} breathe={!big && !reducedMotion()} impact={impact}/>
      <CameraRig flight={flight} done={() => {}} idleOrbit={idle}/>
      <CamTracker camPos={camPos}/>
    </Scene>
    {intro && <div className="explore-intro" aria-live="polite"><strong>{Math.round(progress * 100)}%</strong><span>Assembling {data.nodes.length.toLocaleString()} nodes · {data.links.length.toLocaleString()} links</span></div>}
    <div className="map-hud">
      <span className="hud-pill">Explore · {data.nodes.length.toLocaleString()} nodes · {data.links.length.toLocaleString()} links{paused ? ' · paused' : ''}{pins ? ` · ${pins} pinned` : ''}</span>
      {impact && <span className="hud-pill impact">Impact: {flow!.edges.length} paths</span>}
      {dragging && <span className="hud-pill quiet">Release to pin · double-click a node to unpin</span>}
    </div>
    {trail.length > 1 && <nav className="explore-trail" aria-label="Visited">{trail.map((id, i) => <button key={id} aria-current={i === trail.length - 1} onClick={() => pick(id)}>{byId.get(id)?.label || id}</button>)}</nav>}
    <Minimap nodes={data.nodes} onFly={n => pick(n.id)} camPos={camPos}/>
    <div className="explore-dock" role="toolbar" aria-label="Explore controls">
      <button onClick={togglePause} title="Pause or resume the motion (Space)" aria-pressed={paused}>{paused ? <Play size={15}/> : <Pause size={15}/>}<span>{paused ? 'Resume' : 'Pause'}</span></button>
      <button onClick={shake} title="Give the graph a shake"><Waves size={15}/><span>Shake</span></button>
      <button onClick={() => {setFocusOn(!focusOn); sound.toggle(!focusOn)}} aria-pressed={focusOn} disabled={!selected} title={selected ? 'Show only the neighbourhood of the selection (F)' : 'Select a node to focus on it'}><Crosshair size={15}/><span>Focus</span></button>
      {focusOn && <div className="explore-seg">{[1, 2, 3].map(h => <button key={h} aria-pressed={hops === h} onClick={() => setHops(h)}>{h} step{h > 1 ? 's' : ''}</button>)}</div>}
      <div className="explore-seg" role="group" aria-label="Link particles">{(['all', 'selection', 'off'] as Particles[]).map(p => <button key={p} aria-pressed={particles === p} onClick={() => setParticles(p)}>{p === 'all' ? <><Sparkles size={13}/>All flows</> : p === 'selection' ? 'Selection' : 'Off'}</button>)}</div>
      <button onClick={fit} title="Fit the whole graph (R)"><RotateCcw size={15}/><span>Reset</span></button>
      {pins > 0 && <button onClick={unpinAll} title="Release every pinned node">Unpin all</button>}
      <div className="explore-seg" role="group" aria-label="Theme"><button aria-pressed={space} onClick={() => {setTheme('space'); save('onto.explore.theme', 'space')}}>Space</button><button aria-pressed={!space} onClick={() => {setTheme('studio'); save('onto.explore.theme', 'studio')}}>Studio</button></div>
      <button onClick={() => {const m = !muted; setMuted(m); save('onto.explore.sound', m ? 'off' : 'on'); sound.on = !m; if (!m) sound.toggle(true)}} aria-pressed={!muted} title={muted ? 'Turn interaction sounds on' : 'Mute interaction sounds'}>{muted ? <VolumeX size={15}/> : <Volume2 size={15}/>}</button>
    </div>
  </div>;
}
