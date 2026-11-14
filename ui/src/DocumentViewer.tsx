/** A manual, procedure or guide read inside Fuzer, with a way back out.
 *
 * The file used to open on its own (a new tab, or an iframe of the raw PDF). In the installed app on an iPad or iPhone
 * that new "tab" is the app's own window with no browser chrome, so a technician who opened a compressor manual had no
 * way back to the asset. Here the pages are drawn by pdf.js inside the app: on the document's page as a preview, and
 * over any page from the link (`doc=<document>`) with Close, so Back, Escape and a shared link all work.
 * Markdown and text files read as text. */
import "./styles/drawing.css";
import { useEffect, useRef, useState } from "react";
import { api } from "./api";
import { appDepth } from "./appHistory";
import { projectUrl } from "./authState";
import { requestFile } from "./api/transport";
import workerUrl from "pdfjs-dist/build/pdf.worker.min.mjs?url";
import { Markdown } from "./Markdown";
import { Status, entityHref, fmt, useAsync } from "./lib";
import { Button } from "./ui";

/** The depth the reader was opened at, so Close can step back to the page it was opened from. */
let openedAt: number | null = null;

export const documentFileUrl = (id: string) => projectUrl(`/documents/${encodeURIComponent(id)}/file`);

/** The link to this page with a document open over it. */
export function documentHref(id: string): string {
  const [path, qs] = location.hash.split("?");
  const params = new URLSearchParams(qs ?? "");
  params.set("doc", id);
  return `${path || "#/"}?${params}`;
}

export function openDocument(id: string) {
  if (!new URLSearchParams(location.hash.split("?")[1] ?? "").has("doc")) openedAt = appDepth();
  location.hash = documentHref(id);
}

export function closeDocument() {
  const back = openedAt === null ? 0 : appDepth() - openedAt;
  openedAt = null;
  if (back > 0) { history.go(-back); return; }
  const [path, qs] = location.hash.split("?");
  const params = new URLSearchParams(qs ?? "");
  params.delete("doc");
  location.replace(`${path || "#/"}${params.size ? `?${params}` : ""}`);
}

type Loaded = { kind: "pdf"; pages: PdfPage[] } | { kind: "text"; text: string; markdown: boolean };
type PdfPage = { width: number; height: number; draw: (canvas: HTMLCanvasElement, scale: number) => { promise: Promise<void>; cancel: () => void } };

/** Fetch a document's file and, for a PDF, open it with pdf.js (loaded only when a document is read). */
async function loadDocument(id: string): Promise<Loaded> {
  const file = await requestFile(documentFileUrl(id));
  if (!file.type.includes("pdf")) {
    return { kind: "text", text: new TextDecoder().decode(file.bytes), markdown: file.type.includes("markdown") };
  }
  const pdfjs = await import("pdfjs-dist");
  pdfjs.GlobalWorkerOptions.workerSrc = workerUrl;
  const doc = await pdfjs.getDocument({ data: new Uint8Array(file.bytes) }).promise;
  const pages: PdfPage[] = [];
  for (let n = 1; n <= doc.numPages; n++) {
    const page = await doc.getPage(n);
    const base = page.getViewport({ scale: 1 });
    pages.push({
      width: base.width, height: base.height,
      draw: (canvas, scale) => {
        const ratio = window.devicePixelRatio || 1;
        const vp = page.getViewport({ scale: scale * ratio });
        canvas.width = Math.floor(vp.width);
        canvas.height = Math.floor(vp.height);
        return page.render({ canvas, viewport: vp });
      },
    });
  }
  return { kind: "pdf", pages };
}

function Page({ page, width }: { page: PdfPage; width: number }) {
  const ref = useRef<HTMLCanvasElement>(null);
  const scale = width / page.width;
  useEffect(() => {
    const c = ref.current;
    if (!c || width <= 0) return;
    c.style.width = `${width}px`;
    c.style.height = `${page.height * scale}px`;
    // A new width cancels the drawing still under way: pdf.js draws one render at a time into a canvas.
    const task = page.draw(c, scale);
    task.promise.catch(() => undefined);
    return () => task.cancel();
  }, [page, scale, width]);
  return <canvas ref={ref} className="doc-page" />;
}

/** The document's pages, as wide as their box times `zoom`. */
export function DocumentPages({ id, zoom = 1, className = "" }: { id: string; zoom?: number; className?: string }) {
  const d = useAsync(() => loadDocument(id), [id]);
  const box = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(0);
  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const measure = () => setWidth(el.clientWidth);
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, [d.data]);
  if (!d.data) return <Status loading={d.loading} error={d.error} onRetry={d.reload} />;
  const doc = d.data;
  return (
    <div ref={box} className={`doc-pages ${className}`}>
      {doc.kind === "pdf"
        ? doc.pages.map((p, i) => <Page key={i} page={p} width={Math.max(0, width - 24) * zoom} />)
        : doc.markdown ? <div className="doc-text"><Markdown text={doc.text} /></div>
        : <pre className="doc-text">{doc.text}</pre>}
    </div>
  );
}

const CLOSE = <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18" /></svg>;
const ZOOMS = [0.75, 1, 1.5, 2, 3];

/** A document on the whole screen, opened over the page it was read from. */
export function DocumentFullscreen({ id }: { id: string }) {
  const e = useAsync(() => api.entity(id).catch(() => null), [id]);
  const [zoom, setZoom] = useState(1);
  useEffect(() => {
    document.documentElement.classList.add("drawing-open");
    const key = (ev: KeyboardEvent) => { if (ev.key === "Escape") closeDocument(); };
    window.addEventListener("keydown", key);
    return () => {
      document.documentElement.classList.remove("drawing-open");
      window.removeEventListener("keydown", key);
    };
  }, []);
  const title = fmt(e.data?.golden?.title?.value ?? e.data?.label ?? "Document");
  const z = ZOOMS.indexOf(zoom);
  return (
    <div className="drawing-full doc-full" role="dialog" aria-modal="true" aria-label={title}>
      <div className="drawing-full-bar">
        <Button variant="ghost" className="df-btn" aria-label="Close document" onClick={closeDocument}>{CLOSE}</Button>
        <span className="df-title">
          {e.data ? <a href={entityHref(id)} onClick={() => { openedAt = null; }}>{title}</a> : title}
        </span>
        <span className="df-zoom">
          <Button variant="ghost" className="df-btn" aria-label="Smaller" disabled={z <= 0}
            onClick={() => setZoom(ZOOMS[z - 1])}>−</Button>
          <Button variant="ghost" className="df-btn df-text" aria-label="Fit width" disabled={zoom === 1}
            onClick={() => setZoom(1)}>Fit</Button>
          <Button variant="ghost" className="df-btn" aria-label="Larger" disabled={z >= ZOOMS.length - 1}
            onClick={() => setZoom(ZOOMS[z + 1])}>+</Button>
        </span>
      </div>
      <div className="doc-full-stage">
        <DocumentPages id={id} zoom={zoom} />
      </div>
    </div>
  );
}
