/* The app shell without signal (P5.14d, #988). A field operator who opens the installed app with no signal gets the
 * app itself, and My work reads the jobs kept on the phone (P5.3c). Only the page and its built files are kept here:
 * API answers are never cached by this worker, so nothing it serves can be stale data passed off as live. */
const SHELL = "fuzer-shell-v1";

async function keepShell() {
  const cache = await caches.open(SHELL);
  const page = await fetch("/", { credentials: "same-origin", cache: "no-store" });
  if (!page.ok) return;
  const html = await page.clone().text();
  await cache.put("/", page);
  // The page's own script, styles and icons, so the first launch without signal has them too.
  const files = [...html.matchAll(/(?:src|href)="(\/assets\/[^"]+)"/g)].map((m) => m[1]);
  await Promise.all(files.map((file) => cache.add(file).catch(() => undefined)));
}

self.addEventListener("install", (event) => {
  event.waitUntil(keepShell().catch(() => undefined).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(caches.keys()
    .then((names) => Promise.all(names.filter((name) => name.startsWith("fuzer-shell-") && name !== SHELL).map((name) => caches.delete(name))))
    .then(() => self.clients.claim()));
});

self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (request.method !== "GET") return;
  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;
  if (request.mode === "navigate") {
    // The page: from the network when there is signal (and kept for later), the kept copy when there isn't.
    event.respondWith(fetch(request).then((response) => {
      if (response.ok && url.pathname === "/") {
        const copy = response.clone();
        caches.open(SHELL).then((cache) => cache.put("/", copy));
      }
      return response;
    }).catch(() => caches.match("/", { ignoreVary: true }).then((kept) => kept ?? Response.error())));
    return;
  }
  if (url.pathname.startsWith("/assets/")) {
    // Built files have their content's hash in the name, so a kept copy is always the right one.
    // ignoreVary: a module script asks with an Origin header, which a server that varies on it would otherwise mismatch.
    event.respondWith(caches.match(request, { ignoreVary: true }).then((kept) => kept ?? fetch(request).then((response) => {
      if (response.ok) {
        const copy = response.clone();
        caches.open(SHELL).then((cache) => cache.put(request, copy));
      }
      return response;
    })));
  }
  // Everything else (the API) goes to the network as it always has.
});
