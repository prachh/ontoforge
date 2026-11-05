// WebGL helpers shared by every graph engine.
//
// Browsers keep only ~16 live WebGL contexts per page; past that they silently drop the oldest, which blanks a graph
// (nodes and links vanish while HTML/2D-canvas labels stay). So the capability probe runs once and releases its
// context, and each engine rebuilds itself if the browser still takes its context away.

let available: boolean | undefined;

export function webglAvailable() {
  if (available === undefined) {
    try {
      const gl = document.createElement('canvas').getContext('webgl2') || document.createElement('canvas').getContext('webgl');
      available = !!gl; gl?.getExtension('WEBGL_lose_context')?.loseContext();
    } catch {available = false}
  }
  return available;
}

// Calls `revive` when a canvas inside `element` loses its WebGL context while still on the page. Context-lost events do
// not bubble, so listen in the capture phase; losses from our own teardown (canvas already detached) are ignored.
export function onContextLost(element: HTMLElement, revive: () => void) {
  const lost = (e: Event) => {const canvas = e.target as HTMLCanvasElement; if (canvas.isConnected && element.contains(canvas)) window.setTimeout(revive, 50)};
  element.addEventListener('webglcontextlost', lost, true);
  return () => element.removeEventListener('webglcontextlost', lost, true);
}
