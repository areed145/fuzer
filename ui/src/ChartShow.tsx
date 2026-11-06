import { Component, useEffect, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import { Button } from "./ui";
import "./styles/chart-show.css";

export interface ChartSlide { id: string; title: string; render: () => ReactNode }

/** One chart failing to render costs that chart, not the page holding the show. */
class SlideBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  render() {
    return this.state.failed
      ? <p className="error" role="alert">This chart could not be drawn from the data returned. The rest of the page is unaffected.</p>
      : this.props.children;
  }
}

const smooth = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth";

/** Charts that share one card: swipe, trackpad, arrows, dots or the arrow keys move between them. A
 *  slide mounts when first reached, so a chart nobody opens loads no data. */
export function ChartShow({ label, slides }: { label: string; slides: ChartSlide[] }) {
  const track = useRef<HTMLDivElement>(null);
  const [active, setActive] = useState(0);
  const [reached, setReached] = useState(() => new Set([0]));
  const reach = (...indexes: number[]) => setReached((seen) =>
    indexes.every((i) => seen.has(i)) ? seen : new Set([...seen, ...indexes]));
  const go = (index: number) => {
    const node = track.current;
    if (!node || index < 0 || index >= slides.length) return;
    reach(index);
    node.scrollTo({ left: index * node.clientWidth, behavior: smooth() });
  };
  useEffect(() => {
    const node = track.current;
    if (!node) return;
    const moved = () => {
      const position = node.scrollLeft / (node.clientWidth || 1);
      reach(Math.floor(position), Math.ceil(position));
      setActive(Math.round(position));
    };
    // A slide is the track's width, so a resize or rotation must not leave the show between slides.
    let width = node.clientWidth;
    const resized = new ResizeObserver(() => {
      if (node.clientWidth === width) return;
      const index = Math.round(node.scrollLeft / (width || 1));
      width = node.clientWidth;
      node.scrollTo({ left: index * width });
    });
    resized.observe(node);
    node.addEventListener("scroll", moved, { passive: true });
    return () => { resized.disconnect(); node.removeEventListener("scroll", moved); };
  }, []);
  const keys = (event: KeyboardEvent) => {
    if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
    event.preventDefault();
    go(active + (event.key === "ArrowRight" ? 1 : -1));
  };
  return (
    <section className="chart-show ui-card" role="group" aria-roledescription="carousel" aria-label={label} onKeyDown={keys}>
      <div className="chart-show-head">
        <h2 aria-live="polite">{slides[active].title}</h2>
        <div className="chart-show-controls">
          <Button variant="ghost" size="sm" aria-label="Previous chart" disabled={active === 0} onClick={() => go(active - 1)}>‹</Button>
          {slides.map((slide, index) => (
            <button key={slide.id} type="button" className="chart-show-dot" aria-label={`Show ${slide.title}`}
              aria-current={index === active ? "true" : undefined} onClick={() => go(index)} />
          ))}
          <Button variant="ghost" size="sm" aria-label="Next chart" disabled={active === slides.length - 1} onClick={() => go(active + 1)}>›</Button>
        </div>
      </div>
      <div className="chart-show-track" ref={track}>
        {slides.map((slide, index) => (
          <div key={slide.id} className="chart-show-slide" role="group" aria-roledescription="slide"
            aria-label={`${index + 1} of ${slides.length}: ${slide.title}`} aria-hidden={index !== active} inert={index !== active}>
            {reached.has(index) && <SlideBoundary>{slide.render()}</SlideBoundary>}
          </div>
        ))}
      </div>
    </section>
  );
}
