// Small shared UI building blocks used by the app shell and the graph explorer.
import React, {useRef} from 'react';

// Keyboard-navigable tabs (arrow keys move between tabs, per the WAI-ARIA tabs pattern).
export function Tabs({tabs, value, onChange, label, className = ''}: {tabs: [string, string, number?][]; value: string; onChange: (v: string) => void; label: string; className?: string}) {
  const refs = useRef<(HTMLButtonElement | null)[]>([]);
  const onKey = (e: React.KeyboardEvent, i: number) => {
    const next = e.key === 'ArrowRight' ? i + 1 : e.key === 'ArrowLeft' ? i - 1 : e.key === 'Home' ? 0 : e.key === 'End' ? tabs.length - 1 : null;
    if (next === null) return; e.preventDefault(); const j = (next + tabs.length) % tabs.length; onChange(tabs[j][0]); refs.current[j]?.focus();
  };
  return <div className={'tabs-line ' + className} role="tablist" aria-label={label}>{tabs.map(([key, text, count], i) =>
    <button key={key} ref={el => {refs.current[i] = el}} role="tab" aria-selected={value === key} tabIndex={value === key ? 0 : -1} onClick={() => onChange(key)} onKeyDown={e => onKey(e, i)}>
      {text}{count !== undefined && <span className="tab-count">{count.toLocaleString()}</span>}</button>)}</div>;
}
