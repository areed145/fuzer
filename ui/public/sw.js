/* The app shell without signal (P5.14d, #988). A field operator who opens the installed app with no signal gets the
 * app itself, and My work reads the jobs kept on the phone (P5.3c). Only the page and its built files are kept here:
 * API answers are never cached by this worker, so nothing it serves can be stale data passed off as live. */
const SHELL = "fuzer-shell-v1";

/** Keep a page and the built files it names, files first: a kept page never names a file that isn't kept (P5.17h).
 * Throws if any of them can't be fetched, leaving the copy kept before as it was. */
async function keepPage(page) {
  const html = await page.clone().text();
  const cache = await caches.open(SHELL);
  const files = [...new Set([...html.matchAll(/(?:src|href)="(\/assets\/[^"]+)"/g)].map((m) => m[1]))];
  const missing = [];
  for (const file of files) if (!(await cache.match(file, { ignoreVary: true }))) missing.push(file);
  await cache.addAll(missing);
  await cache.put("/", page);
}

async function keepShell() {
  const page = await fetch("/", { credentials: "same-origin", cache: "no-store" });
  if (!page.ok) throw new Error(`The app's page answered ${page.status}`);
  await keepPage(page);
}

// A new worker that can't keep the whole app (the signal dropped while it installed) fails to install, so the browser
// keeps the worker already in place and its copy, and tries again on a later launch. A worker that installed the
// first time with a partial copy would otherwise strand the phone on a page whose script it doesn't have.
self.addEventListener("install", (event) => {
  event.waitUntil(keepShell().then(() => self.skipWaiting()));
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
      if (response.ok && url.pathname === "/") event.waitUntil(keepPage(response.clone()).catch(() => undefined));
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
