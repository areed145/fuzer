/** The page itself never zooms on a phone: a stray pinch, double-tap or focused input used to leave the installed app
 * zoomed and shifted sideways with the layout cut off. iOS ignores user-scalable=no, so its pinch gesture is cancelled
 * here; double-tap zoom is off through touch-action in styles.css and input auto-zoom through the 16px rule there.
 * Views that zoom on purpose (the P&ID fullscreen, the 3D scene, maps, charts) read pointer and touch events, which
 * still arrive, so their own pinch keeps working. */
export function lockPageZoom(): void {
  const stop = (e: Event) => e.preventDefault();
  for (const type of ["gesturestart", "gesturechange", "gestureend"]) {
    document.addEventListener(type, stop, { passive: false });
  }
}
