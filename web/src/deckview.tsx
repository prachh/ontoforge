// deck.gl 2.5D landscape, loaded on demand: every node is a column standing on the current map layout,
// its height is a measure (business rules, links, properties or members), and arcs show how nodes connect.
import React, {useEffect, useMemo, useRef, useState} from 'react';
import Graph from 'graphology';
import DeckGL from '@deck.gl/react';
import {onContextLost} from './webgl';
import {OrbitView, LinearInterpolator} from '@deck.gl/core';
import type {PickingInfo} from '@deck.gl/core';
import {ColumnLayer, ArcLayer, TextLayer} from '@deck.gl/layers';

type Obj = Record<string, any>;
type Camera = {ids: string[]; nonce: number} | null;
type RGBA = [number, number, number, number];
type Item = {id: string; label: string; x: number; y: number; value: number; height: number; color: RGBA};
type Metric = 'rules' | 'links' | 'properties' | 'members' | 'changes';

const METRICS: Record<'code' | 'domain', [Metric, string][]> = {
  domain: [['rules', 'business rules'], ['links', 'links'], ['properties', 'properties']],
  code: [['links', 'links'], ['members', 'members (methods, fields…)']],
};
const SPAN = 1000;

function rgb(hex: string, alpha = 255): RGBA {
  const h = hex.replace('#', ''); const full = h.length === 3 ? h.split('').map(c => c + c).join('') : h.slice(0, 6); const n = parseInt(full, 16);
  return Number.isNaN(n) ? [140, 150, 160, alpha] : [n >> 16 & 255, n >> 8 & 255, n & 255, alpha];
}

type Props = {graph: Graph; mode: 'code' | 'domain'; visible: (id: string) => boolean; colorOf: (a: Obj) => string; selected: string | null; camera: Camera; onSelect: (id: string | null) => void; heat?: Record<string, number[]> | null};

export function DeckLandscape({graph, mode, visible, colorOf, selected, camera, onSelect, heat}: Props) {
  const metrics = METRICS[mode].concat(mode === 'code' && heat ? [['changes', 'commits (git)']] : []);
  const [metric, setMetric] = useState<Metric>(mode === 'domain' ? 'rules' : 'links');
  const [allArcs, setAllArcs] = useState(false); const [error, setError] = useState('');

  const {items, index} = useMemo(() => {
    const ids = graph.filterNodes(id => visible(id));
    const xs = ids.map(id => graph.getNodeAttribute(id, 'x') || 0), ys = ids.map(id => graph.getNodeAttribute(id, 'y') || 0);
    const minX = Math.min(...xs), maxX = Math.max(...xs), minY = Math.min(...ys), maxY = Math.max(...ys);
    const scale = SPAN / Math.max(1e-6, maxX - minX, maxY - minY);
    const members = (id: string) => graph.outEdges(id).filter(e => graph.getEdgeAttribute(e, 'kinds').includes('defines')).length;
    const valueOf = (id: string, a: Obj) => metric === 'rules' ? (a.rules || []).length : metric === 'properties' ? (a.properties || []).length : metric === 'members' ? members(id) : metric === 'changes' ? (heat?.[a.path] || [0])[0] : graph.degree(id);
    const values = ids.map(id => valueOf(id, graph.getNodeAttributes(id))); const top = Math.max(1, ...values);
    const items: Item[] = ids.map((id, i) => {
      const a = graph.getNodeAttributes(id);
      return {id, label: String(a.label), x: (xs[i] - (minX + maxX) / 2) * scale, y: -(ys[i] - (minY + maxY) / 2) * scale, value: values[i], height: 3 + values[i] / top * 170, color: rgb(colorOf(a))};
    });
    return {items, index: new Map(items.map((it, i) => [it.id, i]))};
  }, [graph, visible, colorOf, metric, heat]);

  const neighbours = useMemo(() => selected && graph.hasNode(selected) ? new Set(graph.neighbors(selected)) : null, [graph, selected]);
  const arcs = useMemo(() => {
    const out: {s: Item; t: Item; on: boolean}[] = [];
    graph.forEachEdge((_, a, s, t) => {
      const i = index.get(s), j = index.get(t); if (i === undefined || j === undefined) return;
      const on = s === selected || t === selected;
      if (on || (allArcs && !selected)) out.push({s: items[i], t: items[j], on});
    });
    return out.slice(0, 6000);
  }, [graph, items, index, selected, allArcs]);
  const labelled = useMemo(() => {
    const pick = new Set<number>(); [...items.keys()].sort((a, b) => items[b].value - items[a].value).slice(0, 18).forEach(i => pick.add(i));
    if (selected) {const i = index.get(selected); if (i !== undefined) pick.add(i); for (const n of neighbours || []) {const j = index.get(n); if (j !== undefined && pick.size < 45) pick.add(j)}}
    return [...pick].map(i => items[i]);
  }, [items, index, selected, neighbours]);

  const [viewState, setViewState] = useState<Obj>({target: [0, 0, 0], rotationX: 42, rotationOrbit: -25, zoom: -1.25, minZoom: -4, maxZoom: 5});
  React.useEffect(() => {
    if (!camera) return; const i = camera.ids.map(id => index.get(id)).find(i => i !== undefined); if (i === undefined) return;
    setViewState((v: Obj) => ({...v, target: [items[i].x, items[i].y, 0], zoom: Math.max(v.zoom, -.3), transitionDuration: 700, transitionInterpolator: new LinearInterpolator(['target', 'zoom'])}));
  }, [camera]);

  const sel = selected ? index.get(selected) : undefined;
  const faded = (it: Item): RGBA => {
    if (it.id === selected) return [245, 159, 0, 255];
    if (selected && !neighbours?.has(it.id)) return [it.color[0], it.color[1], it.color[2], 45];
    return it.color;
  };
  const layers = [
    new ColumnLayer<Item>({id: 'columns', data: items, diskResolution: 10, radius: Math.max(3, SPAN / Math.sqrt(items.length + 1) * .16), extruded: true, pickable: true, autoHighlight: true, highlightColor: [47, 138, 115, 220],
      getPosition: d => [d.x, d.y], getElevation: d => d.height, getFillColor: faded, material: {ambient: .55, diffuse: .6, shininess: 24},
      updateTriggers: {getFillColor: [selected, neighbours], getElevation: metric}}),
    new ArcLayer<{s: Item; t: Item; on: boolean}>({id: 'arcs', data: arcs, getSourcePosition: d => [d.s.x, d.s.y, d.s.height], getTargetPosition: d => [d.t.x, d.t.y, d.t.height],
      getSourceColor: d => d.on ? [59, 111, 216, 230] : [59, 111, 216, 70], getTargetColor: d => d.on ? [232, 89, 12, 230] : [232, 89, 12, 70], getWidth: d => d.on ? 2.2 : 1, getHeight: .4, greatCircle: false}),
    new TextLayer<Item>({id: 'labels', data: labelled, getPosition: d => [d.x, d.y, d.height + 6], getText: d => d.label, getSize: d => d.id === selected ? 14 : 11, sizeUnits: 'pixels',
      getColor: [31, 45, 40, 255], background: true, getBackgroundColor: [255, 255, 255, 225], backgroundPadding: [4, 2], getTextAnchor: 'middle', getAlignmentBaseline: 'bottom', billboard: true,
      fontFamily: 'system-ui, sans-serif', characterSet: 'auto', updateTriggers: {getSize: selected}}),
  ];
  const metricLabel = metrics.find(m => m[0] === metric)?.[1] || metric;
  const host = useRef<HTMLDivElement>(null); const [revive, setRevive] = useState(0);
  useEffect(() => host.current ? onContextLost(host.current, () => setRevive(r => r + 1)) : undefined, [!!items.length, error, revive]);
  if (error) return <div className="map-canvas"><div className="explorer-empty error">{error}</div></div>;
  if (!items.length) return <div className="map-canvas"><div className="explorer-empty">Nothing matches the filters.</div></div>;
  return <div ref={host} className="map-canvas deck-canvas">
    <DeckGL key={revive} views={new OrbitView({orbitAxis: "Z", fovy: 45})} viewState={viewState as any} onViewStateChange={({viewState}) => setViewState(viewState as Obj)} controller={true} layers={layers}
      onClick={(info: PickingInfo) => onSelect(info.object ? (info.object as Item).id : null)} onError={e => setError('deck.gl could not start (WebGL): ' + e.message)}
      getTooltip={({object}: PickingInfo) => object && 'label' in (object as Obj) ? {text: `${(object as Item).label}\n${(object as Item).value} ${metricLabel}`} : null}
      getCursor={({isHovering, isDragging}) => isDragging ? 'grabbing' : isHovering ? 'pointer' : 'grab'}/>
    <div className="map-hud">
      <span className="hud-pill">deck.gl landscape · {items.length.toLocaleString()} columns{sel !== undefined ? ` · ${arcs.length} links of the selection` : ''}</span>
      <div className="segmented small hud-seg" role="group" aria-label="Column height">{metrics.map(([m, label]) => <button key={m} aria-pressed={metric === m} onClick={() => setMetric(m)}>Height: {label}</button>)}</div>
      {sel === undefined && <button className="hud-pill quiet" onClick={() => setAllArcs(!allArcs)}>{allArcs ? 'Hide' : 'Show'} all links</button>}
      <button className="hud-pill quiet" onClick={() => setViewState((v: Obj) => ({...v, target: [0, 0, 0], zoom: -1.25, rotationX: 42, rotationOrbit: -25, transitionDuration: 500}))}>Reset view</button>
    </div>
    <div className="city-legend">Columns stand on the map layout · height = {metricLabel} · colour = {mode === 'domain' ? 'area' : 'type'} · <b className="out"/>link from <b className="in"/>link to · drag to rotate, shift-drag to pan, scroll to zoom</div>
  </div>;
}
