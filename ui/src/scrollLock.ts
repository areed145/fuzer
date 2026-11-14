/** Hold the page still under something that covers the whole screen (a full-screen chart, graph, map, 3D scene, P&ID
 * or document). `overflow: hidden` alone isn't enough on an iPhone: the installed app kept the page's scroll and drew
 * the cover shifted up by it, its close button above the top of the screen and the page showing through below. So
 * the body is pinned in place at its scroll, which leaves the screen's top where the cover's top is, and put back
 * where it was when the last cover closes. Covers can stack (a chart inside a drawing); the count keeps one lock. */
let held = 0;
let savedY = 0;

export function lockPageScroll(className: string): () => void {
  const root = document.documentElement, body = document.body;
  root.classList.add(className);
  if (held++ === 0) {
    savedY = window.scrollY;
    body.style.position = "fixed";
    body.style.top = `-${savedY}px`;
    body.style.left = "0";
    body.style.right = "0";
  }
  return () => {
    root.classList.remove(className);
    if (--held > 0) return;
    held = 0;
    body.style.position = body.style.top = body.style.left = body.style.right = "";
    window.scrollTo(0, savedY);
  };
}
