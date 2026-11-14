/** iOS look and feel (prototype). On an iPhone or iPad the app takes iOS cues: the system font, large titles that
 * fold into the top bar, translucent bars, inset grouped lists, segmented controls, edge-swipe Back, pull to refresh
 * and swipe actions on work cards. Desktop is untouched. `?ios=1` (or `?ios=0`) forces it on or off for a preview. */

const KEY = "fuzer.ios";

function wanted(): boolean {
  const asked = new URLSearchParams(location.search).get("ios");
  try {
    if (asked === "1" || asked === "0") sessionStorage.setItem(KEY, asked);
    const kept = sessionStorage.getItem(KEY);
    if (kept === "1") return true;
    if (kept === "0") return false;
  } catch { /* private mode: use the device */ }
  const ua = navigator.userAgent;
  // iPadOS reports a Mac; a Mac with a touch screen is an iPad.
  return /iPhone|iPad|iPod/.test(ua) || (/Macintosh/.test(ua) && navigator.maxTouchPoints > 1);
}

const phone = () => window.innerWidth <= 900;

/** The large page title folds into the top bar once it scrolls under it. */
function foldTitle() {
  const root = document.documentElement;
  const bar = document.querySelector<HTMLElement>(".topbar");
  const slot = bar?.querySelector<HTMLElement>(".topbar-title");
  const title = document.querySelector<HTMLElement>("main h1");
  if (!bar || !slot) return;
  const text = title?.textContent?.trim() ?? "";
  if (slot.textContent !== text) slot.textContent = text;
  const folded = !!title && title.getBoundingClientRect().bottom < bar.getBoundingClientRect().bottom + 4;
  root.classList.toggle("ios-folded", folded);
  root.classList.toggle("ios-scrolled", window.scrollY > 2);
}

/** Swipe in from the left edge to go Back, the page following the finger. */
function edgeSwipe() {
  let start: { x: number; y: number } | null = null;
  let dx = 0;
  const main = () => document.querySelector<HTMLElement>(".app > main");
  window.addEventListener("touchstart", (e) => {
    const t = e.touches[0];
    start = phone() && t.clientX < 24 && history.length > 1 ? { x: t.clientX, y: t.clientY } : null;
    dx = 0;
  }, { passive: true });
  window.addEventListener("touchmove", (e) => {
    if (!start) return;
    const t = e.touches[0];
    dx = Math.max(0, t.clientX - start.x);
    if (Math.abs(t.clientY - start.y) > 40 && dx < 30) { start = null; return; }
    const m = main();
    if (m) { m.style.transition = "none"; m.style.transform = `translateX(${dx}px)`; }
  }, { passive: true });
  window.addEventListener("touchend", () => {
    if (!start) return;
    start = null;
    const m = main();
    if (!m) return;
    m.style.transition = "transform .25s cubic-bezier(.2,.8,.2,1)";
    if (dx > window.innerWidth / 3) {
      m.style.transform = "translateX(100%)";
      setTimeout(() => { history.back(); m.style.transition = "none"; m.style.transform = ""; }, 220);
    } else m.style.transform = "";
  });
}

/** Pull down at the top of a page to reload it; the spinner sits under the top bar. */
function pullToRefresh() {
  let y0: number | null = null;
  let pulled = 0;
  const spinner = document.createElement("div");
  spinner.className = "ios-pull";
  spinner.setAttribute("aria-hidden", "true");
  document.body.append(spinner);
  window.addEventListener("touchstart", (e) => {
    const inside = (e.target as Element).closest(".mapview, .map, .work-card, .ui-fullscreen, .attention-detail");
    y0 = phone() && window.scrollY <= 0 && !inside ? e.touches[0].clientY : null;
    pulled = 0;
  }, { passive: true });
  window.addEventListener("touchmove", (e) => {
    if (y0 == null) return;
    pulled = Math.max(0, e.touches[0].clientY - y0);
    spinner.style.setProperty("--pull", `${Math.min(pulled, 110)}px`);
    spinner.classList.toggle("armed", pulled > 80);
  }, { passive: true });
  window.addEventListener("touchend", () => {
    if (y0 == null) return;
    y0 = null;
    if (pulled > 80) { spinner.classList.add("spinning"); location.reload(); }
    else spinner.style.setProperty("--pull", "0px");
  });
}

/** Swipe a work card left to bring up its actions (Dispatch, Close out); tap elsewhere to put it back. */
function swipeActions() {
  let card: HTMLElement | null = null;
  let x0 = 0, y0 = 0, dx = 0, sideways = false;
  const settle = (except?: Element | null) => document.querySelectorAll(".work-card.swiped")
    .forEach((c) => c !== except && c.classList.remove("swiped"));
  window.addEventListener("touchstart", (e) => {
    const target = e.target as Element;
    if (!target.closest(".work-card-actions")) settle(target.closest(".work-card"));
    card = target.closest<HTMLElement>(".work-card");
    if (!card?.querySelector(".work-card-actions")) card = null;
    x0 = e.touches[0].clientX; y0 = e.touches[0].clientY; dx = 0; sideways = false;
  }, { passive: true });
  window.addEventListener("touchmove", (e) => {
    if (!card) return;
    dx = e.touches[0].clientX - x0;
    if (!sideways && Math.abs(e.touches[0].clientY - y0) > Math.abs(dx)) { card = null; return; }
    sideways = true;
  }, { passive: true });
  window.addEventListener("touchend", () => {
    if (!card || !sideways) return;
    card.classList.toggle("swiped", dx < -40);
    card = null;
  });
}

export function startIos() {
  if (!wanted()) return;
  document.documentElement.classList.add("ios");
  let queued = false;
  const refold = () => { if (!queued) { queued = true; requestAnimationFrame(() => { queued = false; foldTitle(); }); } };
  window.addEventListener("scroll", refold, { passive: true });
  window.addEventListener("hashchange", () => setTimeout(refold, 50));
  new MutationObserver(refold).observe(document.body, { childList: true, subtree: true });
  edgeSwipe();
  pullToRefresh();
  swipeActions();
}
