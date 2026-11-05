// Colour themes: the list, the saved choice, and the token values that canvas/WebGL renderers need.
// CSS reads the tokens directly from [data-theme] on <html>; renderers read them through graphTokens().
import {useEffect, useState} from 'react';

export type ThemeId = 'sand-indigo' | 'arctic-blue' | 'obsidian-copper' | 'graphite-violet' | 'ivory-ember' | 'midnight-electric';
// Swatches: background, surface, accent, selected connection.
export const THEMES: {id: ThemeId; name: string; mode: 'light' | 'dark'; swatches: string[]}[] = [
  {id: 'sand-indigo', name: 'Sand & Indigo', mode: 'light', swatches: ['#F4F1EA', '#FFFEFC', '#5046C8', '#EAE5FA']},
  {id: 'arctic-blue', name: 'Arctic Blue', mode: 'light', swatches: ['#F1F6FC', '#FFFFFF', '#087CC1', '#DEEEFC']},
  {id: 'obsidian-copper', name: 'Obsidian & Copper', mode: 'dark', swatches: ['#111315', '#1B1F23', '#C78A5B', '#392B22']},
  {id: 'graphite-violet', name: 'Graphite & Violet', mode: 'dark', swatches: ['#141418', '#1D1D24', '#9568F7', '#302444']},
  {id: 'ivory-ember', name: 'Ivory & Ember', mode: 'light', swatches: ['#F7F5F0', '#FFFEFC', '#BC4B2F', '#FAE8DE']},
  {id: 'midnight-electric', name: 'Midnight Navy & Electric Blue', mode: 'dark', swatches: ['#0B1020', '#121A2E', '#527CFF', '#202F55']},
];
export const DEFAULT_THEME: ThemeId = 'sand-indigo';
const KEY = 'onto.theme';
const EVENT = 'ontoforge:theme';

export const isTheme = (v: unknown): v is ThemeId => THEMES.some(t => t.id === v);
// index.html applies the saved theme before the first paint; this reads what it applied.
export const currentTheme = (): ThemeId => {const v = document.documentElement.dataset.theme; return isTheme(v) ? v : DEFAULT_THEME};
export const themeMode = () => THEMES.find(t => t.id === currentTheme())!.mode;

export function setTheme(id: ThemeId) {
  if (!isTheme(id)) id = DEFAULT_THEME;
  document.documentElement.dataset.theme = id;
  try {localStorage.setItem(KEY, id)} catch {}
  cached = null; window.dispatchEvent(new CustomEvent(EVENT, {detail: id}));
}

// Token values for renderers that cannot use CSS variables (Sigma, cosmos.gl, deck.gl, three.js, canvas).
export type GraphTokens = {mode: 'light' | 'dark'; canvas: string; panel: string; elevated: string; text: string; text2: string; text3: string; border: string; borderStrong: string;
  select: string; dim: string; edge: string; edgeFaint: string; label: string; hoverBg: string; primary: string; primaryText: string; selection: string;
  success: string; warning: string; danger: string; purple: string; nodes: string[]; nodeMuted: string; concept: string};
let cached: GraphTokens | null = null;
export function graphTokens(): GraphTokens {
  if (cached) return cached;
  const s = getComputedStyle(document.documentElement); const v = (n: string) => s.getPropertyValue('--' + n).trim();
  cached = {mode: themeMode(), canvas: v('canvas'), panel: v('panel'), elevated: v('elevated'), text: v('text'), text2: v('text-2'), text3: v('text-3'), border: v('border'), borderStrong: v('border-strong'),
    select: v('graph-select'), dim: v('graph-dim'), edge: v('graph-edge'), edgeFaint: v('graph-edge-faint'), label: v('graph-label'), hoverBg: v('graph-hover-bg'), primary: v('primary'), primaryText: v('primary-text'), selection: v('selection'),
    success: v('success'), warning: v('warning'), danger: v('danger'), purple: v('purple'), nodes: Array.from({length: 12}, (_, i) => v(`node-${i + 1}`)), nodeMuted: v('node-muted'), concept: v('node-concept')};
  return cached;
}

// Re-render on theme change. The returned key changes with the theme, for memo/effect dependencies.
export function useTheme() {
  const [theme, set] = useState<ThemeId>(currentTheme);
  useEffect(() => {const on = () => set(currentTheme()); window.addEventListener(EVENT, on); return () => window.removeEventListener(EVENT, on)}, []);
  return theme;
}
export const onThemeChange = (fn: () => void) => {window.addEventListener(EVENT, fn); return () => window.removeEventListener(EVENT, fn)};
