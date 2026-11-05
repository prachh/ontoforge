// 3D views, loaded on demand: a code city (folders are districts, classes are buildings) and a two-floor view
// with the code city on the ground floor and the domain ontology above it, joined by beams from each concept to its code.
import React, {useEffect, useLayoutEffect, useMemo, useRef, useState} from 'react';
import Graph from 'graphology';
import * as THREE from 'three';
import {Canvas, useFrame, useThree} from '@react-three/fiber';
import {MapControls, QuadraticBezierLine, Billboard, Text} from '@react-three/drei';
import {EffectComposer, Bloom, N8AO} from '@react-three/postprocessing';
import {XR, createXRStore, useXR} from '@react-three/xr';
// 3D labels use a bundled font so VR/AR never fetches font data from a CDN (troika reads .woff, not .woff2).
import labelFont from '@fontsource/dm-sans/files/dm-sans-latin-600-normal.woff?url';

type Obj = Record<string, any>;
export type Camera = {ids: string[]; nonce: number} | null;
export type Flow = {start: string; edges: [string, string, number][]} | null;

const BUILDING_KINDS = new Set(['Class', 'Model', 'Component', 'Table']);
const MEMBER_KINDS = new Set(['Function', 'Test', 'Column', 'API']);
const DIM = new THREE.Color('#dfe5e1');
const FILE_COLOR = '#9aa3ab';

export type Building = {id: string; label: string; kind: string; district: number; x: number; z: number; w: number; h: number; methods: number; used: number; members: string[]};
export type District = {name: string; x: number; z: number; w: number; d: number; size: number};
export type City = {buildings: Building[]; districts: District[]; index: Map<string, number>; owner: Map<string, number>; links: [number, number, number][]; width: number; depth: number; maxHeight: number};

const dirOf = (path = '') => path.includes('/') ? path.slice(0, path.lastIndexOf('/')) : '.';

/** Lay the code graph out as a city: buildings packed in a grid per folder, folders packed in rows. */
export function buildCity(code: Graph, include: (id: string) => boolean): City {
  const parent = new Map<string, string>(); const children = new Map<string, string[]>();
  code.forEachEdge((_, a, s, t) => {if (a.kinds.includes('defines')) {parent.set(t, s); children.set(s, [...(children.get(s) || []), t])}});
  const raw: Omit<Building, 'district' | 'x' | 'z' | 'w' | 'h'>[] = [];
  const index = new Map<string, number>();
  code.forEachNode((id, a) => {
    if (!include(id)) return;
    const own = (children.get(id) || []);
    if (BUILDING_KINDS.has(a.kind)) {index.set(id, raw.length); raw.push({id, label: a.label, kind: a.kind, methods: own.filter(c => MEMBER_KINDS.has(code.getNodeAttribute(c, 'kind'))).length, used: 0, members: own})}
    else if (a.kind === 'File') {
      // A module of plain functions (no class) is still a building, drawn grey.
      const loose = own.filter(c => MEMBER_KINDS.has(code.getNodeAttribute(c, 'kind')));
      if (loose.length && !own.some(c => BUILDING_KINDS.has(code.getNodeAttribute(c, 'kind')))) {index.set(id, raw.length); raw.push({id, label: a.label, kind: 'File', methods: loose.length, used: 0, members: own})}
    }
  });
  // Every entity belongs to the building that contains it.
  const owner = new Map<string, number>();
  code.forEachNode(id => {let cur: string | undefined = id; for (let i = 0; i < 6 && cur; i++) {const b = index.get(cur); if (b !== undefined) {owner.set(id, b); return} cur = parent.get(cur)}});
  const pairs = new Map<string, number>();
  code.forEachEdge((_, a, s, t) => {
    if (a.kinds.length === 1 && a.kinds[0] === 'defines') return;
    const i = owner.get(s), j = owner.get(t); if (i === undefined || j === undefined || i === j) return;
    raw[j].used++; const k = i + '>' + j; pairs.set(k, (pairs.get(k) || 0) + 1);
  });
  // Districts: folders, with folders of one or two buildings merged into their parent folder.
  const dir = new Map<number, string>(); raw.forEach((b, i) => dir.set(i, dirOf(code.getNodeAttribute(b.id, 'path'))));
  for (let pass = 0; pass < 4; pass++) {
    const counts = new Map<string, number>(); for (const d of dir.values()) counts.set(d, (counts.get(d) || 0) + 1);
    let changed = false;
    for (const [i, d] of dir) if ((counts.get(d) || 0) < 3 && d.includes('/')) {dir.set(i, dirOf(d)); changed = true}
    if (!changed) break;
  }
  const groups = new Map<string, number[]>(); for (const [i, d] of dir) groups.set(d, [...(groups.get(d) || []), i]);
  const districts: District[] = []; const buildings: Building[] = raw.map(b => ({...b, district: 0, x: 0, z: 0, w: Math.min(8, 1.6 + Math.sqrt(b.used) * .9), h: Math.min(42, 1 + b.methods * .85)}));
  const GAP = 1.4, PAD = 2;
  const blocks = [...groups].map(([name, ids]) => {
    ids.sort((a, b) => buildings[b].h - buildings[a].h);
    const cell = Math.max(...ids.map(i => buildings[i].w)) + GAP; const cols = Math.ceil(Math.sqrt(ids.length)); const rows = Math.ceil(ids.length / cols);
    return {name, ids, cell, cols, w: cols * cell + PAD * 2, d: rows * cell + PAD * 2};
  }).sort((a, b) => b.w * b.d - a.w * a.d);
  const target = Math.sqrt(blocks.reduce((s, b) => s + (b.w + 4) * (b.d + 4), 0)) * 1.25;
  let x = 0, z = 0, rowDepth = 0, width = 0;
  for (const b of blocks) {
    if (x > 0 && x + b.w > target) {x = 0; z += rowDepth + 4; rowDepth = 0}
    const di = districts.length; districts.push({name: b.name, x: x + b.w / 2, z: z + b.d / 2, w: b.w, d: b.d, size: b.ids.length});
    b.ids.forEach((i, k) => {const B = buildings[i]; B.district = di; B.x = x + PAD + (k % b.cols + .5) * b.cell; B.z = z + PAD + (Math.floor(k / b.cols) + .5) * b.cell});
    x += b.w + 4; rowDepth = Math.max(rowDepth, b.d); width = Math.max(width, x);
  }
  const depth = z + rowDepth;
  // Centre the city on the origin.
  for (const B of buildings) {B.x -= width / 2; B.z -= depth / 2}
  for (const D of districts) {D.x -= width / 2; D.z -= depth / 2}
  const links = [...pairs].map(([k, n]) => {const [i, j] = k.split('>').map(Number); return [i, j, n] as [number, number, number]});
  return {buildings, districts, index, owner, links, width, depth, maxHeight: Math.max(1, ...buildings.map(b => b.h))};
}

const shortDir = (name: string) => name.split('/').slice(-2).join('/');

function Buildings({city, colors, onPick, onHover, onDouble}: {city: City; colors: THREE.Color[]; onPick: (i: number | null) => void; onHover: (i: number | null) => void; onDouble?: (i: number) => void}) {
  const mesh = useRef<THREE.InstancedMesh>(null);
  const geometry = useMemo(() => new THREE.BoxGeometry(1, 1, 1).translate(0, .5, 0), []);
  useLayoutEffect(() => {
    const m = mesh.current; if (!m) return; const o = new THREE.Object3D();
    city.buildings.forEach((b, i) => {o.position.set(b.x, .3, b.z); o.scale.set(b.w, b.h, b.w); o.updateMatrix(); m.setMatrixAt(i, o.matrix)});
    m.instanceMatrix.needsUpdate = true; m.computeBoundingSphere();
  }, [city]);
  useLayoutEffect(() => {const m = mesh.current; if (!m) return; colors.forEach((c, i) => m.setColorAt(i, c)); if (m.instanceColor) m.instanceColor.needsUpdate = true}, [colors]);
  if (!city.buildings.length) return null;
  return <instancedMesh key={city.buildings.length} ref={mesh} args={[geometry, undefined, city.buildings.length]} castShadow receiveShadow
    onClick={e => {e.stopPropagation(); onPick(e.instanceId ?? null)}} onDoubleClick={e => {e.stopPropagation(); if (e.instanceId !== undefined) onDouble?.(e.instanceId)}}
    onPointerMove={e => {e.stopPropagation(); onHover(e.instanceId ?? null)}} onPointerOut={() => onHover(null)}>
    <meshStandardMaterial roughness={.65} metalness={.05} toneMapped={false}/>
  </instancedMesh>;
}

function Plates({city, y = 0}: {city: City; y?: number}) {
  const mesh = useRef<THREE.InstancedMesh>(null);
  useLayoutEffect(() => {
    const m = mesh.current; if (!m) return; const o = new THREE.Object3D();
    city.districts.forEach((d, i) => {o.position.set(d.x, y, d.z); o.scale.set(d.w, .3, d.d); o.updateMatrix(); m.setMatrixAt(i, o.matrix)});
    m.instanceMatrix.needsUpdate = true; m.computeBoundingSphere();
  }, [city, y]);
  if (!city.districts.length) return null;
  return <instancedMesh key={city.districts.length} ref={mesh} args={[undefined, undefined, city.districts.length]} raycast={() => null} receiveShadow>
    <boxGeometry args={[1, 1, 1]}/><meshStandardMaterial color="#e6e9ec" roughness={.9}/>
  </instancedMesh>;
}

// Labels are plain DOM in one overlay (not one React root per label): each frame their 3D anchor is projected to the screen.
export type Tag = {key: string; pos: [number, number, number]; at?: () => [number, number, number]; text: string; sub?: string; cls?: string};
const buildingTag = (b: Building, cls = '', sub?: string): Tag => ({key: 'b' + b.id, pos: [b.x, b.h + .6, b.z], text: b.label, sub: sub ?? `${b.methods} ${b.kind === 'File' ? 'functions' : 'methods'}`, cls});
const districtTags = (city: City, limit: number): Tag[] => [...city.districts].sort((a, b) => b.size - a.size).slice(0, limit)
  .map(d => ({key: 'd' + d.name, pos: [d.x, .4, d.z + d.d / 2], text: shortDir(d.name), cls: 'district'}));
export const uniqueTags = (tags: Tag[]) => {const seen = new Set<string>(); return tags.filter(t => !seen.has(t.key) && !!seen.add(t.key))};

function Projector({tags, layer}: {tags: Tag[]; layer: React.RefObject<HTMLDivElement | null>}) {
  const {camera, size} = useThree(); const v = useMemo(() => new THREE.Vector3(), []);
  useFrame(() => {
    const el = layer.current; if (!el) return;
    for (let i = 0; i < el.children.length; i++) {
      const tag = tags[i]; const node = el.children[i] as HTMLElement; if (!tag) continue;
      v.set(...(tag.at ? tag.at() : tag.pos)).project(camera);
      if (v.z > 1 || Math.abs(v.x) > 1.2 || Math.abs(v.y) > 1.2) {node.style.display = 'none'; continue}
      node.style.display = ''; node.style.transform = `translate(${(v.x + 1) / 2 * size.width}px, ${(1 - v.y) / 2 * size.height}px) translate(-50%, -100%)`;
    }
  });
  return null;
}

/** Moves the orbit target to the requested building(s) without changing the viewing angle. */
export function Framer({target}: {target: [number, number, number] | null}) {
  const {controls, camera} = useThree() as unknown as {controls: {target: THREE.Vector3; update: () => void} | null; camera: THREE.Camera};
  useEffect(() => {
    if (!target || !controls) return;
    const offset = camera.position.clone().sub(controls.target); controls.target.set(...target); camera.position.copy(controls.target.clone().add(offset)); controls.update();
  }, [target]);
  return null;
}

// Quality: "high" adds soft shadows, ambient occlusion and bloom (bright colours above 1 glow); "fast" draws plainly.
export type Quality = 'high' | 'fast';
const readQuality = (): Quality => {try {return localStorage.getItem('onto.3d.quality') === 'fast' ? 'fast' : 'high'} catch {return 'high'}};
const xrEmulate = () => {try {return new URLSearchParams(window.location.search).has('xr-emulate')} catch {return false}};
let xrStore: ReturnType<typeof createXRStore> | null = null;
const getXRStore = () => xrStore || (xrStore = createXRStore({offerSession: false, emulate: xrEmulate() ? 'metaQuest3' : false}));

/** In a headset the scene is scaled to about 2.5 m and placed in front of the viewer; labels become 3D text. */
function XRStage({size, children, tags}: {size: number; children: React.ReactNode; tags: Tag[]}) {
  const session = useXR(s => s.session); const mode = useXR(s => s.mode);
  if (!session) return <>{children}</>;
  const k = 2.5 / Math.max(1, size);
  return <group scale={k} position={[0, mode === 'immersive-ar' ? .75 : 1, -1.8]}>
    {children}
    {tags.slice(0, 40).map(t => {const [x, y, z] = t.at ? t.at() : t.pos; return <Billboard key={t.key} position={[x, y + 1 / k * .03, z]}>
      <Text font={labelFont} fontSize={.045 / k} color="#1f2d28" outlineWidth={.004 / k} outlineColor="#ffffff" anchorY="bottom">{t.text}</Text></Billboard>})}
  </group>;
}
function Effects({size, bloom}: {size: number; bloom: number}) {
  const session = useXR(s => s.session);
  if (session) return null;  // post-processing is not available inside WebXR sessions
  return <EffectComposer multisampling={4}>
    <N8AO aoRadius={Math.max(2, size / 60)} intensity={2.2} distanceFalloff={1} halfRes/>
    <Bloom luminanceThreshold={1} luminanceSmoothing={.2} intensity={bloom} mipmapBlur/>
  </EffectComposer>;
}
function Controls({far}: {far: number}) {const session = useXR(s => s.session); return session ? null : <MapControls makeDefault target={[0, 0, 0]} maxPolarAngle={Math.PI / 2.15} minDistance={4} maxDistance={far * 6}/>}

export function Scene({size, children, onMiss, tags, background = '#f3f5f7', bloom = .9}: {size: number; children: React.ReactNode; onMiss: () => void; tags: Tag[]; background?: string; bloom?: number}) {
  const far = Math.max(60, size); const layer = useRef<HTMLDivElement>(null);
  const [quality, setQuality] = useState<Quality>(readQuality); const store = useMemo(getXRStore, []);
  const [xr, setXr] = useState<{vr: boolean; ar: boolean}>({vr: xrEmulate(), ar: xrEmulate()}); const [xrError, setXrError] = useState('');
  const enter = (mode: 'vr' | 'ar') => {setXrError(''); (mode === 'vr' ? store.enterVR() : store.enterAR()).catch((e: Error) => setXrError(`${mode.toUpperCase()} could not start: ${e.message}`))};
  useEffect(() => {
    const nav = (navigator as Navigator & {xr?: {isSessionSupported: (m: string) => Promise<boolean>}}).xr; if (!nav || xrEmulate()) return;
    Promise.all([nav.isSessionSupported('immersive-vr').catch(() => false), nav.isSessionSupported('immersive-ar').catch(() => false)]).then(([vr, ar]) => setXr({vr, ar}));
  }, []);
  // If the browser takes the WebGL context away (too many open), start a fresh canvas instead of leaving it blank.
  const [generation, setGeneration] = useState(0);
  const watchContext = ({gl}: {gl: THREE.WebGLRenderer}) => {const c = gl.domElement; c.addEventListener('webglcontextlost', () => {if (c.isConnected) window.setTimeout(() => setGeneration(g => g + 1), 50)}, {once: true})};
  const high = quality === 'high'; const half = far * .75;
  return <>
    <Canvas key={generation} onCreated={watchContext} shadows={high ? 'soft' : false} camera={{position: [far * .55, far * .62, far * .85], fov: 45, near: .5, far: far * 12}} dpr={[1, 2]} onPointerMissed={onMiss} gl={{antialias: true}}>
      <XR store={store}>
        <color attach="background" args={[background]}/>
        <ambientLight intensity={high ? .55 : .75}/>
        <directionalLight position={[far * .6, far * 1.2, far * .4]} intensity={high ? 1.6 : 1.15} castShadow={high} shadow-mapSize={[2048, 2048]} shadow-bias={-.0004}
          shadow-camera-left={-half} shadow-camera-right={half} shadow-camera-top={half} shadow-camera-bottom={-half} shadow-camera-near={1} shadow-camera-far={far * 4}/>
        <directionalLight position={[-far, far * .6, -far]} intensity={.35}/>
        <Controls far={far}/>
        <XRStage size={size} tags={tags}>{children}</XRStage>
        {high && <Effects size={size} bloom={bloom}/>}
        <Projector tags={tags} layer={layer}/>
      </XR>
    </Canvas>
    <div ref={layer} className="city-tags" aria-hidden>
      {tags.map(t => <div key={t.key} className={t.cls === 'district' ? 'city-district' : 'city-label ' + (t.cls || '')} style={{display: 'none'}}>{t.text}{t.sub && <small>{t.sub}</small>}</div>)}
    </div>
    <div className="scene-hud">
      <div className="segmented small hud-seg" role="group" aria-label="3D quality"><button aria-pressed={high} onClick={() => {setQuality('high'); try {localStorage.setItem('onto.3d.quality', 'high')} catch { /* storage unavailable */ }}} title="Soft shadows, ambient occlusion and glow">High quality</button>
        <button aria-pressed={!high} onClick={() => {setQuality('fast'); try {localStorage.setItem('onto.3d.quality', 'fast')} catch { /* storage unavailable */ }}} title="Plain rendering for slower machines">Fast</button></div>
      {xr.vr && <button className="hud-pill quiet" onClick={() => enter('vr')}>Enter VR</button>}
      {xr.ar && <button className="hud-pill quiet" onClick={() => enter('ar')}>Enter AR</button>}
      {xrError && <span className="hud-pill impact" role="alert">{xrError}</span>}
    </div>
  </>;
}

/** Glowing pulses running from each item to the next along an impact chain, one wave per depth, looping. */
export function Pulses({edges, arc = 0, period = 3.2}: {edges: {from: () => [number, number, number]; to: () => [number, number, number]; depth: number}[]; arc?: number; period?: number}) {
  const mesh = useRef<THREE.InstancedMesh>(null); const o = useMemo(() => new THREE.Object3D(), []);
  useFrame(({clock}) => {
    const m = mesh.current; if (!m) return; const time = clock.getElapsedTime() % period;
    edges.forEach((e, i) => {
      const t = (time - (e.depth - 1) * .55) / .55;
      if (t < 0 || t > 1) {o.scale.setScalar(0)} else {
        const a = e.from(), b = e.to(); const lift = arc * Math.sin(Math.PI * t) * (1 + Math.hypot(a[0] - b[0], a[2] - b[2]) * .25);
        o.position.set(a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t + lift, a[2] + (b[2] - a[2]) * t); o.scale.setScalar(1);
      }
      o.updateMatrix(); m.setMatrixAt(i, o.matrix);
    });
    m.instanceMatrix.needsUpdate = true;
  });
  if (!edges.length) return null;
  return <instancedMesh key={edges.length} ref={mesh} args={[undefined, undefined, edges.length]} raycast={() => null} frustumCulled={false}>
    <sphereGeometry args={[.9, 12, 10]}/><meshBasicMaterial color={[2.6, 1.2, .2]} toneMapped={false}/>
  </instancedMesh>;
}

import {webglAvailable} from './webgl';
export {webglAvailable};

// ---- Code city ----
type CityProps = {graph: Graph; visible: (id: string) => boolean; colors: Record<string, string>; mapped: Map<string, Obj[]>; selected: string | null; camera: Camera; onSelect: (id: string | null) => void; heat?: Record<string, number[]> | null; flow?: Flow};

export function CodeCity({graph, visible, colors, mapped, selected, camera, onSelect, heat, flow}: CityProps) {
  const city = useMemo(() => buildCity(graph, visible), [graph, visible]);
  const [colorBy, setColorBy] = useState<'type' | 'mapped' | 'hotspots'>('type'); const [allLinks, setAllLinks] = useState(false); const [hover, setHover] = useState<number | null>(null);
  const sel = selected ? city.owner.get(selected) : undefined;
  const isMapped = useMemo(() => city.buildings.map(b => mapped.has(b.id) || b.members.some(m => mapped.has(m))), [city, mapped]);
  const linked = useMemo(() => {
    if (sel === undefined) return null; const out = new Map<number, 'out' | 'in' | 'both'>();
    for (const [i, j] of city.links) {if (i === sel) out.set(j, out.get(j) === 'in' ? 'both' : 'out'); if (j === sel) out.set(i, out.get(i) === 'out' ? 'both' : 'in')}
    return out;
  }, [city, sel]);
  const commits = useMemo(() => city.buildings.map(b => (heat?.[graph.getNodeAttribute(b.id, 'path')] || [0])[0]), [city, heat, graph]);
  const maxCommits = Math.max(1, ...commits);
  const tint = useMemo(() => city.buildings.map((b, i) => {
    const base = colorBy === 'hotspots' ? (commits[i] ? new THREE.Color('#fde3cf').lerp(new THREE.Color('#9b1c1c'), Math.log1p(commits[i]) / Math.log1p(maxCommits)) : new THREE.Color('#d5dbe0'))
      : new THREE.Color(colorBy === 'mapped' ? (isMapped[i] ? '#1f6f5c' : '#c9ced3') : b.kind === 'File' ? FILE_COLOR : colors[b.kind] || '#4c6ef5');
    if (i === sel) return new THREE.Color('#f59f00').multiplyScalar(1.7);  // above 1, so it glows in high quality
    if (colorBy === 'hotspots' && commits[i]) base.multiplyScalar(1 + 1.8 * Math.pow(commits[i] / maxCommits, 2));
    return linked && !linked.has(i) ? base.lerp(DIM, .82) : base;
  }), [city, colors, colorBy, isMapped, sel, linked, commits, maxCommits]);
  const pulses = useMemo(() => {
    if (!flow) return []; const seen = new Set<string>(); const out: {from: () => [number, number, number]; to: () => [number, number, number]; depth: number}[] = [];
    for (const [a, b, depth] of flow.edges) {
      const i = city.owner.get(a), j = city.owner.get(b); if (i === undefined || j === undefined || i === j || seen.has(i + '>' + j)) continue; seen.add(i + '>' + j);
      const A = city.buildings[i], B = city.buildings[j]; out.push({from: () => [A.x, A.h + .6, A.z], to: () => [B.x, B.h + .6, B.z], depth});
    }
    return out.slice(0, 800);
  }, [flow, city]);
  const arcs = useMemo(() => {
    const list = sel !== undefined ? city.links.filter(([i, j]) => i === sel || j === sel) : allLinks ? [...city.links].sort((a, b) => b[2] - a[2]).slice(0, 400) : [];
    return list.slice(0, 600);
  }, [city, sel, allLinks]);
  const tallest = useMemo(() => city.buildings.map((b, i) => [b.h, i]).sort((a, b) => b[0] - a[0]).slice(0, 10).map(x => x[1]), [city]);
  const tags = uniqueTags([
    ...districtTags(city, sel !== undefined ? 8 : 16),
    ...(sel !== undefined ? [buildingTag(city.buildings[sel], 'on'), ...[...(linked?.keys() || [])].slice(0, 12).map(i => buildingTag(city.buildings[i]))] : tallest.map(i => buildingTag(city.buildings[i]))),
    ...(hover !== null ? [buildingTag(city.buildings[hover])] : []),
  ]);
  const [frame, setFrame] = useState<[number, number, number] | null>(null);
  useEffect(() => {if (!camera) return; const i = camera.ids.map(id => city.owner.get(id)).find(i => i !== undefined); if (i !== undefined) setFrame([city.buildings[i].x, 0, city.buildings[i].z])}, [camera]);

  if (!webglAvailable()) return <div className="map-canvas"><div className="explorer-empty error">3D needs WebGL, which is not available in this browser.</div></div>;
  if (!city.buildings.length) return <div className="map-canvas"><div className="explorer-empty">No classes or modules match the filters.</div></div>;
  const mappedCount = isMapped.filter(Boolean).length;
  return <div className="map-canvas city-canvas">
    <Scene size={Math.max(city.width, city.depth)} onMiss={() => onSelect(null)} tags={tags}>
      <Plates city={city}/>
      <Buildings city={city} colors={tint} onPick={i => onSelect(i === null ? null : city.buildings[i].id)} onHover={setHover}/>
      {arcs.map(([i, j, n]) => {
        const a = city.buildings[i], b = city.buildings[j]; const dist = Math.hypot(a.x - b.x, a.z - b.z);
        const out = sel === undefined || i === sel;
        return <QuadraticBezierLine key={i + '>' + j} start={[a.x, a.h + .3, a.z]} end={[b.x, b.h + .3, b.z]} mid={[(a.x + b.x) / 2, Math.max(a.h, b.h) + 4 + dist * .35, (a.z + b.z) / 2]}
          color={out ? '#3b6fd8' : '#e8590c'} lineWidth={Math.min(3, .8 + Math.log2(1 + n) * .5)} transparent opacity={sel === undefined ? .35 : .85}/>;
      })}
      <Pulses edges={pulses} arc={3}/>
      <Framer target={frame}/>
    </Scene>
    <div className="map-hud">
      <span className="hud-pill">Code city · {city.buildings.length.toLocaleString()} buildings in {city.districts.length} districts</span>
      <div className="segmented small hud-seg" role="group" aria-label="Colour buildings by"><button aria-pressed={colorBy === 'type'} onClick={() => setColorBy('type')}>Colour: type</button><button aria-pressed={colorBy === 'mapped'} onClick={() => setColorBy('mapped')}>mapped to a concept ({mappedCount})</button>{heat && <button aria-pressed={colorBy === 'hotspots'} onClick={() => setColorBy('hotspots')}>hotspots (git)</button>}</div>
      {sel === undefined && <button className="hud-pill quiet" onClick={() => setAllLinks(!allLinks)}>{allLinks ? 'Hide' : 'Show'} strongest links</button>}
      {pulses.length > 0 && <span className="hud-pill impact">Impact: {pulses.length} paths animating</span>}
    </div>
    <div className="city-legend">{colorBy === 'hotspots' && <>Colour = commits in the git window (darker red = changed more often, grey = unchanged) · </>}Height = methods · footprint = how often other code uses it · grey = module of functions · {sel !== undefined ? <><b className="out"/>uses <b className="in"/>used by</> : 'click a building to see its links'} · drag to pan, right-drag to rotate, scroll to zoom</div>
  </div>;
}

// ---- Two floors: code below, domain ontology above ----
type FloorsProps = {graph: Graph; code?: Graph; colorOf: (a: Obj) => string; codeColors: Record<string, string>; visible: (id: string) => boolean; selected: string | null; onSelect: (id: string | null) => void; onOpenCode: (id: string) => void};

export function TwoFloors({graph, code, colorOf, codeColors, visible, selected, onSelect, onOpenCode}: FloorsProps) {
  const city = useMemo(() => code ? buildCity(code, id => !code.getNodeAttribute(id, 'test')) : null, [code]);
  const [hover, setHover] = useState<{kind: 'concept' | 'building'; i: number} | null>(null); const [pickedBuilding, setPickedBuilding] = useState<number | null>(null);
  const [showUnmapped, setShowUnmapped] = useState(true);
  const floor = city ? city.maxHeight + Math.max(18, Math.max(city.width, city.depth) * .22) : 40;

  // Concepts float above the code that implements them; concepts without code ring the city.
  const concepts = useMemo(() => {
    if (!city) return [];
    const ids = graph.filterNodes(id => visible(id));
    const placed: {id: string; label: string; x: number; z: number; r: number; color: string; beams: number[]}[] = [];
    const spots = new Map<string, number>(); const orphans: typeof placed = [];
    for (const id of ids) {
      const a = graph.getNodeAttributes(id);
      const beams = [...new Set<number>((a.evidence || []).map((e: string) => city.owner.get(e)).filter((i: number | undefined): i is number => i !== undefined))];
      const item = {id, label: String(a.label), x: 0, z: 0, r: Math.min(2.6, .9 + Math.sqrt(graph.degree(id)) * .3), color: colorOf(a), beams};
      if (!beams.length) {orphans.push(item); continue}
      item.x = beams.reduce((s, i) => s + city.buildings[i].x, 0) / beams.length; item.z = beams.reduce((s, i) => s + city.buildings[i].z, 0) / beams.length;
      // Several concepts over the same spot spread out in a small spiral.
      const key = Math.round(item.x / 3) + ':' + Math.round(item.z / 3); const n = spots.get(key) || 0; spots.set(key, n + 1);
      if (n) {const ang = n * 2.4, rad = 2.2 * Math.sqrt(n); item.x += Math.cos(ang) * rad; item.z += Math.sin(ang) * rad}
      placed.push(item);
    }
    const R = Math.max(city.width, city.depth) / 2 + 10;
    orphans.forEach((o, k) => {const ang = (k / Math.max(1, orphans.length)) * Math.PI * 2; o.x = Math.cos(ang) * R; o.z = Math.sin(ang) * R});
    return [...placed, ...orphans];
  }, [graph, visible, city, colorOf]);
  const cIndex = useMemo(() => new Map(concepts.map((c, i) => [c.id, i])), [concepts]);
  const sel = selected ? cIndex.get(selected) : undefined;

  const usedBuildings = useMemo(() => {const s = new Set<number>(); for (const c of concepts) for (const b of c.beams) s.add(b); return s}, [concepts]);
  const implementedBy = useMemo(() => {const m = new Map<number, number[]>(); concepts.forEach((c, i) => c.beams.forEach(b => m.set(b, [...(m.get(b) || []), i]))); return m}, [concepts]);
  const focusBuildings = sel !== undefined ? new Set(concepts[sel].beams) : pickedBuilding !== null ? new Set([pickedBuilding]) : null;
  const focusConcepts = sel !== undefined ? new Set([sel, ...graph.neighbors(concepts[sel].id).map(n => cIndex.get(n)).filter((i): i is number => i !== undefined)])
    : pickedBuilding !== null ? new Set(implementedBy.get(pickedBuilding) || []) : null;

  const tint = useMemo(() => city ? city.buildings.map((b, i) => {
    const base = new THREE.Color(usedBuildings.has(i) ? (b.kind === 'File' ? FILE_COLOR : codeColors[b.kind] || '#4c6ef5') : '#cfd5da');
    if (focusBuildings?.has(i)) return new THREE.Color('#f59f00');
    if (!usedBuildings.has(i) && !showUnmapped) return base.clone().lerp(new THREE.Color('#f3f5f7'), .9);
    return focusBuildings ? base.lerp(DIM, .8) : base;
  }) : [], [city, usedBuildings, codeColors, sel, pickedBuilding, showUnmapped]);

  // All domain links and all beams as two line-segment buffers; the selection gets its own bold lines.
  const domainLines = useMemo(() => {
    const pts: number[] = [];
    graph.forEachEdge((_, __, s, t) => {const i = cIndex.get(s), j = cIndex.get(t); if (i === undefined || j === undefined) return; pts.push(concepts[i].x, floor, concepts[i].z, concepts[j].x, floor, concepts[j].z)});
    return new Float32Array(pts);
  }, [graph, concepts, cIndex, floor]);
  const beamLines = useMemo(() => {
    const pts: number[] = []; if (!city) return new Float32Array();
    for (const c of concepts) for (const b of c.beams) {const B = city.buildings[b]; pts.push(c.x, floor, c.z, B.x, B.h + .3, B.z)}
    return new Float32Array(pts);
  }, [concepts, city, floor]);

  if (!code || !city) return <div className="map-canvas"><div className="explorer-empty">The code graph is needed for the two-floor view; it loads with the domain ontology.</div></div>;
  if (!webglAvailable()) return <div className="map-canvas"><div className="explorer-empty error">3D needs WebGL, which is not available in this browser.</div></div>;
  const withCode = concepts.filter(c => c.beams.length).length;
  const hoverConcept = hover?.kind === 'concept' ? hover.i : null; const hoverBuilding = hover?.kind === 'building' ? hover.i : null;
  const conceptTag = (i: number, cls = ''): Tag => {const c = concepts[i]; return {key: 'c' + c.id, pos: [c.x, floor + c.r * 2 + .4, c.z], text: c.label, cls: 'concept ' + cls + (c.beams.length ? '' : ' orphan'),
    sub: c.beams.length ? `${c.beams.length} code ${c.beams.length === 1 ? 'class' : 'classes'}` : 'no code found'}};
  const focusList = focusConcepts ? [...focusConcepts].sort((a, b) => (a === sel ? -1 : b === sel ? 1 : graph.degree(concepts[b].id) - graph.degree(concepts[a].id))).slice(0, 14) : null;
  const tags = uniqueTags([
    ...(focusList || concepts.map((c, i) => [graph.degree(c.id), i]).sort((a, b) => b[0] - a[0]).slice(0, 14).map(x => x[1])).map(i => conceptTag(i, i === sel ? 'on' : '')),
    ...(sel !== undefined ? concepts[sel].beams.slice(0, 12).map(b => buildingTag(city.buildings[b])) : []),
    ...(pickedBuilding !== null ? [buildingTag(city.buildings[pickedBuilding], 'on', `${(implementedBy.get(pickedBuilding) || []).length} concept(s) · double-click to open in code`)] : []),
    ...(hoverConcept !== null ? [conceptTag(hoverConcept)] : []),
    ...(hoverBuilding !== null ? [buildingTag(city.buildings[hoverBuilding], '', usedBuildings.has(hoverBuilding) ? undefined : 'not mapped to a concept')] : []),
  ]);
  return <div className="map-canvas city-canvas">
    <Scene size={Math.max(city.width, city.depth) + floor} onMiss={() => {onSelect(null); setPickedBuilding(null)}} tags={tags}>
      <group position={[0, 0, 0]}>
        <Plates city={city}/>
        <Buildings city={city} colors={tint} onPick={i => {setPickedBuilding(i); onSelect(null)}} onHover={i => setHover(i === null ? null : {kind: 'building', i})} onDouble={i => onOpenCode(city.buildings[i].id)}/>
      </group>
      {/* The glass floor the ontology stands on. */}
      <mesh position={[0, floor - .2, 0]} rotation={[-Math.PI / 2, 0, 0]} raycast={() => null}>
        <planeGeometry args={[city.width + 40, city.depth + 40]}/><meshStandardMaterial color="#dbeafe" transparent opacity={.18} side={THREE.DoubleSide} depthWrite={false}/>
      </mesh>
      <lineSegments raycast={() => null}><bufferGeometry><bufferAttribute attach="attributes-position" args={[beamLines, 3]}/></bufferGeometry>
        <lineBasicMaterial color="#5b8def" transparent opacity={focusConcepts || focusBuildings ? .05 : .16}/></lineSegments>
      <lineSegments raycast={() => null}><bufferGeometry><bufferAttribute attach="attributes-position" args={[domainLines, 3]}/></bufferGeometry>
        <lineBasicMaterial color="#7a8b84" transparent opacity={focusConcepts ? .12 : .45}/></lineSegments>
      {concepts.map((c, i) => {
        const dim = focusConcepts && !focusConcepts.has(i); const orphan = !c.beams.length;
        return <mesh key={c.id} position={[c.x, floor + c.r, c.z]} onClick={e => {e.stopPropagation(); setPickedBuilding(null); onSelect(c.id)}}
          onPointerOver={e => {e.stopPropagation(); setHover({kind: 'concept', i})}} onPointerOut={() => setHover(null)}>
          <sphereGeometry args={[c.r, 24, 16]}/>
          <meshStandardMaterial color={i === sel ? '#f59f00' : orphan ? '#e03131' : c.color} transparent opacity={dim ? .18 : 1} roughness={.4}/>
        </mesh>;
      })}
      {sel !== undefined && concepts[sel].beams.map(b => <QuadraticBezierLine key={'b' + b} start={[concepts[sel].x, floor, concepts[sel].z]} end={[city.buildings[b].x, city.buildings[b].h + .3, city.buildings[b].z]}
        mid={[(concepts[sel].x + city.buildings[b].x) / 2, (floor + city.buildings[b].h) / 2, (concepts[sel].z + city.buildings[b].z) / 2]} color="#f59f00" lineWidth={2.5}/>)}
      {pickedBuilding !== null && (implementedBy.get(pickedBuilding) || []).map(i => <QuadraticBezierLine key={'p' + i} start={[concepts[i].x, floor, concepts[i].z]} end={[city.buildings[pickedBuilding].x, city.buildings[pickedBuilding].h + .3, city.buildings[pickedBuilding].z]}
        mid={[(concepts[i].x + city.buildings[pickedBuilding].x) / 2, (floor + city.buildings[pickedBuilding].h) / 2, (concepts[i].z + city.buildings[pickedBuilding].z) / 2]} color="#f59f00" lineWidth={2.5}/>)}
    </Scene>
    <div className="map-hud">
      <span className="hud-pill">Two floors · domain above, code below</span>
      <span className="hud-pill quiet">{withCode} of {concepts.length} concepts linked to code · {concepts.length - withCode} without code (red) · {usedBuildings.size} of {city.buildings.length} code buildings used</span>
      <button className="hud-pill quiet" onClick={() => setShowUnmapped(!showUnmapped)}>{showUnmapped ? 'Fade' : 'Show'} unmapped code</button>
    </div>
    <div className="city-legend">Spheres = concepts (coloured by area, red = no code) · beams = the code each concept was found in · coloured buildings implement a concept, grey ones do not · click a sphere or a building to trace it · double-click a building to open it in the code graph</div>
  </div>;
}
