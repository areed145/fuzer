// The schematic 3D view (P5.16a): a scene laid out on the server, drawn as generic solids with three.js. This file
// is the only one that imports three, and it is loaded on demand, so no other page carries it.
import "./styles/scene3d.css";
import { useEffect, useRef, useState, type ReactNode } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { mergeGeometries } from "three/examples/jsm/utils/BufferGeometryUtils.js";
import type { Scene, SceneItem, SceneLink, SceneShape } from "./api";
import { commonName } from "./entityRefs";
import { THEME_CHANGED } from "./theme";
import { Button, FullscreenButton, useFullscreen } from "./ui";

const PIPE = 0.22;
const SEABED = -14;
const SEA = -8;
const LABEL_ALL = 400; // name items up to this many, wherever a name has room; past it, only plates and the picked item
const views = new Map<string, { position: number[]; target: number[] }>(); // where each scene was left, for Back
const TILT = new THREE.Vector3(-0.12, 0.72, 1).normalize(), TOP = new THREE.Vector3(0, 1, 0.0001).normalize();

/** How a name reads on the drawing: what people call it, or its tag. A lit (hovered or picked) name gives both. */
export type LabelBy = "names" | "tags";
const nameFor = (label: string, description: string | null | undefined, by: LabelBy, lit = false) => {
  const name = commonName(label, description);
  if (!name || by === "tags") return lit && name ? `${label} · ${name}` : label;
  return lit ? `${name} · ${label}` : name;
};

function token(name: string) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

/** One solid at its drawn size, standing on y = 0 with flow along x. */
function solid(shape: SceneShape): THREE.BufferGeometry {
  const [w, h, d] = shape.size;
  const saddle = 0.35;
  switch (shape.solid) {
    case "capsule_v": return new THREE.CapsuleGeometry(w / 2, Math.max(h - w, 0.1), 6, 16).translate(0, h / 2, 0);
    case "capsule_h": return new THREE.CapsuleGeometry(h / 2, Math.max(w - h, 0.1), 6, 16).rotateZ(Math.PI / 2).translate(0, h / 2 + saddle, 0);
    case "cylinder_v": return new THREE.CylinderGeometry(w / 2, w / 2, h, 20).translate(0, h / 2, 0);
    case "cylinder_h": return new THREE.CylinderGeometry(h / 2, h / 2, w, 16).rotateZ(Math.PI / 2).translate(0, h / 2 + saddle, 0);
    case "sphere": return new THREE.SphereGeometry(w / 2, 14, 10).translate(0, 1, 0);
    case "well": {
      // A wellhead on the deck with its conductor running down through it.
      const parts = [
        new THREE.CylinderGeometry(0.28, 0.28, -SEABED, 10).translate(0, SEABED / 2, 0),
        new THREE.BoxGeometry(w, h * 0.45, d).translate(0, h * 0.225, 0),
        new THREE.CylinderGeometry(w * 0.28, w * 0.28, h * 0.55, 10).translate(0, h * 0.725, 0),
      ].map((g) => g.toNonIndexed());
      return mergeGeometries(parts) ?? parts[0];
    }
    default: return new THREE.BoxGeometry(w, h, d).translate(0, h / 2, 0);
  }
}

interface Built {
  dispose: () => void; fit: () => void; light: () => void; lookAt: (id: string) => void;
  zoom: (factor: number) => void; top: () => void;
  /** One finger (or the left mouse button) pans instead of turning the view. Two fingers still zoom and pan. */
  pan: (on: boolean) => void;
  screenOf: (id: string) => { x: number; y: number } | null;
}

function build(host: HTMLDivElement, labels: HTMLDivElement, scene: Scene, lineColor: (link: SceneLink) => string,
  labelBy: LabelBy, state: { selected: string | null; onSelect: (id: string | null) => void }): Built {
  const colors = {
    panel: new THREE.Color(token("--panel")), line: new THREE.Color(token("--line")),
    muted: new THREE.Color(token("--muted")), accent: new THREE.Color(token("--accent")),
    bg: new THREE.Color(token("--bg")), sea: new THREE.Color(token("--chart-1")),
  };
  // Shapes differ by form; colour is kept for links and, later, status.
  const steel = colors.muted.clone().lerp(colors.panel, 0.08);

  const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false, powerPreference: "low-power" });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  renderer.setClearColor(colors.panel);
  host.appendChild(renderer.domElement);
  const world = new THREE.Scene();
  world.add(new THREE.HemisphereLight(0xffffff, colors.bg, 1.6));
  const sun = new THREE.DirectionalLight(0xffffff, 1.6);
  sun.position.set(-40, 90, 60);
  world.add(sun);
  const camera = new THREE.PerspectiveCamera(38, 1, 1, 6000);
  const v = new THREE.Vector3();
  const controls = new OrbitControls(camera, renderer.domElement);
  controls.maxPolarAngle = Math.PI / 2 - 0.02;
  controls.zoomToCursor = true;
  controls.keyPanSpeed = 20;
  controls.listenToKeyEvents(host); // arrow keys pan once the drawing has focus
  const disposables: { dispose: () => void }[] = [renderer, controls];
  const keep = <T extends { dispose: () => void }>(x: T) => { disposables.push(x); return x; };

  // Items: one instanced mesh per shape, coloured by instance so a pick can be lit.
  const byShape = new Map<string, SceneItem[]>();
  for (const it of scene.items) byShape.set(it.shape, [...(byShape.get(it.shape) ?? []), it]);
  const at = new Map<string, { mesh: THREE.InstancedMesh; index: number; base: THREE.Color; item: SceneItem; top: number }>();
  const meshes: THREE.InstancedMesh[] = [];
  const m4 = new THREE.Matrix4();
  for (const [name, its] of byShape) {
    const shape = scene.shapes[name];
    const mesh = new THREE.InstancedMesh(keep(solid(shape)), keep(new THREE.MeshLambertMaterial()), its.length);
    const base = shape.detail ? colors.muted.clone().lerp(colors.panel, 0.4) : steel;
    its.forEach((it, i) => {
      mesh.setMatrixAt(i, m4.makeTranslation(it.x, it.y, it.z));
      mesh.setColorAt(i, base);
      at.set(it.id, { mesh, index: i, base, item: it, top: it.y + shape.size[1] + (shape.solid === "sphere" ? 0.6 : 0.5) });
    });
    mesh.userData.items = its;
    meshes.push(mesh);
    world.add(mesh);
  }

  // Plates, with a lattice under the ones that stand in the sea.
  const struts: { a: THREE.Vector3; b: THREE.Vector3; r: number; color: THREE.Color }[] = [];
  const frameColor = colors.line.clone().lerp(colors.muted, 0.5);
  const plateMesh = new THREE.InstancedMesh(keep(new THREE.BoxGeometry(1, 0.4, 1)),
    keep(new THREE.MeshLambertMaterial()), scene.plates.length);
  const plateColor = colors.line.clone().lerp(colors.panel, 0.3);
  scene.plates.forEach((p, i) => {
    plateMesh.setMatrixAt(i, m4.compose(new THREE.Vector3(p.x + p.w / 2, p.y - 0.2, p.z + p.d / 2),
      new THREE.Quaternion(), new THREE.Vector3(p.w, 1, p.d)));
    plateMesh.setColorAt(i, p.area ? plateColor : colors.panel.clone().lerp(colors.line, 0.5));
    if (p.frame !== "lattice") return;
    const inset = 1.2, top = p.y - 0.4;
    const corners = [[p.x + inset, p.z + inset], [p.x + p.w - inset, p.z + inset],
      [p.x + p.w - inset, p.z + p.d - inset], [p.x + inset, p.z + p.d - inset]];
    const levels = [top, (top + SEABED) / 2, SEABED];
    corners.forEach(([x, z], c) => {
      struts.push({ a: new THREE.Vector3(x, top, z), b: new THREE.Vector3(x, SEABED, z), r: 0.38, color: frameColor });
      const [nx, nz] = corners[(c + 1) % 4];
      for (let l = 0; l < 2; l++) { // a brace across each bay of each face
        struts.push({ a: new THREE.Vector3(x, levels[l], z), b: new THREE.Vector3(nx, levels[l + 1], nz), r: 0.2, color: frameColor });
        struts.push({ a: new THREE.Vector3(x, levels[l + 1], z), b: new THREE.Vector3(nx, levels[l + 1], nz), r: 0.2, color: frameColor });
      }
    });
  });
  world.add(plateMesh);

  // Links: straight runs between the path's points, an elbow at each turn and an arrow on the longest run. Deck
  // piping sits at the equipment's level; a run between plates drops as a riser and follows the seabed (scene.py).
  const elbows: { p: THREE.Vector3; color: THREE.Color }[] = [];
  const arrows: { p: THREE.Vector3; dir: THREE.Vector3; color: THREE.Color }[] = [];
  for (const link of scene.links) {
    const color = new THREE.Color(lineColor(link) || token("--muted"));
    const pts = link.path.map(([x, y, z]) => new THREE.Vector3(x, y, z));
    let longest = 0, mid: THREE.Vector3 | null = null, dir: THREE.Vector3 | null = null;
    const r = pts.some((p) => p.y < 0) ? PIPE * 1.3 : PIPE; // a pipeline between plates reads heavier than deck piping
    for (let i = 1; i < pts.length; i++) {
      struts.push({ a: pts[i - 1], b: pts[i], r, color });
      if (i < pts.length - 1) elbows.push({ p: pts[i], color });
      const len = pts[i].distanceTo(pts[i - 1]);
      if (len > longest && Math.abs(pts[i].y - pts[i - 1].y) < 0.01) {
        longest = len;
        mid = pts[i].clone().add(pts[i - 1]).multiplyScalar(0.5);
        dir = pts[i].clone().sub(pts[i - 1]).normalize();
      }
    }
    if (mid && dir && longest > 2) arrows.push({ p: mid, dir, color });
  }
  const up = new THREE.Vector3(0, 1, 0), q = new THREE.Quaternion();
  const strutMesh = new THREE.InstancedMesh(keep(new THREE.CylinderGeometry(1, 1, 1, 8)),
    keep(new THREE.MeshLambertMaterial()), Math.max(struts.length, 1));
  strutMesh.count = struts.length;
  struts.forEach((s, i) => {
    const d = s.b.clone().sub(s.a);
    q.setFromUnitVectors(up, d.clone().normalize());
    strutMesh.setMatrixAt(i, m4.compose(s.a.clone().add(s.b).multiplyScalar(0.5), q, new THREE.Vector3(s.r, d.length(), s.r)));
    strutMesh.setColorAt(i, s.color);
  });
  world.add(strutMesh);
  const elbowMesh = new THREE.InstancedMesh(keep(new THREE.SphereGeometry(PIPE * 1.15, 8, 6)),
    keep(new THREE.MeshLambertMaterial()), Math.max(elbows.length, 1));
  elbowMesh.count = elbows.length;
  elbows.forEach((e, i) => { elbowMesh.setMatrixAt(i, m4.makeTranslation(e.p.x, e.p.y, e.p.z)); elbowMesh.setColorAt(i, e.color); });
  world.add(elbowMesh);
  const arrowMesh = new THREE.InstancedMesh(keep(new THREE.ConeGeometry(PIPE * 2.6, PIPE * 7, 10)),
    keep(new THREE.MeshLambertMaterial()), Math.max(arrows.length, 1));
  arrowMesh.count = arrows.length;
  arrows.forEach((a, i) => {
    arrowMesh.setMatrixAt(i, m4.compose(a.p, q.setFromUnitVectors(up, a.dir), new THREE.Vector3(1, 1, 1)));
    arrowMesh.setColorAt(i, a.color);
  });
  world.add(arrowMesh);

  const box = new THREE.Box3();
  for (const p of scene.plates) box.expandByPoint(new THREE.Vector3(p.x, 0, p.z)).expandByPoint(new THREE.Vector3(p.x + p.w, 10, p.z + p.d));
  if (box.isEmpty()) box.set(new THREE.Vector3(-10, 0, -10), new THREE.Vector3(10, 10, 10));
  if (scene.plates.some((p) => p.frame === "lattice")) {
    const size = box.getSize(new THREE.Vector3()), centre = box.getCenter(new THREE.Vector3());
    const sea = new THREE.Mesh(keep(new THREE.PlaneGeometry(size.x + 5000, size.z + 5000)),
      keep(new THREE.MeshBasicMaterial({ color: colors.sea, transparent: true, opacity: 0.08, depthWrite: false })));
    sea.rotation.x = -Math.PI / 2;
    sea.position.set(centre.x, SEA, centre.z);
    world.add(sea);
    renderer.setClearColor(colors.panel.clone().lerp(colors.sea, 0.08)); // no horizon line where the sea ends
  }

  // Names: plates always, items when there are few, and the hovered and picked item.
  const tags = new Map<string, { el: HTMLElement; p: THREE.Vector3; always: boolean; text: string; lit: string }>();
  labels.replaceChildren();
  const tag = (key: string, label: string, description: string | null | undefined, p: THREE.Vector3, cls: string, always: boolean) => {
    const el = document.createElement("span");
    el.className = cls;
    const text = nameFor(label, description, labelBy);
    el.textContent = text;
    labels.appendChild(el);
    tags.set(key, { el, p, always, text, lit: nameFor(label, description, labelBy, true) });
  };
  for (const p of scene.plates) tag(`plate:${p.id}`, p.label, p.description, new THREE.Vector3(p.x + 1, p.y, p.z + p.d), "scene-tag scene-tag-plate", true);
  for (const it of scene.items) tag(it.id, it.label, it.description, new THREE.Vector3(it.x, at.get(it.id)!.top, it.z), "scene-tag", scene.items.length <= LABEL_ALL);

  let hovered: string | null = null, queued = 0;
  const project = (p: THREE.Vector3) => {
    v.copy(p).project(camera);
    return v.z > 1 || v.z < -1 ? null : { x: (v.x + 1) / 2 * host.clientWidth, y: (1 - v.y) / 2 * host.clientHeight };
  };
  const draw = () => {
    queued = 0;
    renderer.render(world, camera);
    // A name is shown where it has room: the picked and hovered items first, then plates, then the nearest items.
    const shown: { x: number; y: number; w: number; rank: number; d: number; t: { el: HTMLElement } }[] = [];
    for (const [key, t] of tags) {
      const lit = key === state.selected || key === hovered;
      const s = (t.always || lit) && project(t.p);
      t.el.hidden = true;
      t.el.classList.toggle("on", lit);
      const text = lit ? t.lit : t.text;
      if (t.el.textContent !== text) t.el.textContent = text;
      if (s) shown.push({ ...s, w: text.length * (lit ? 7.6 : 6.3) + 10, rank: lit ? 0 : key.startsWith("plate:") ? 1 : 2,
        d: t.p.distanceToSquared(camera.position), t });
    }
    shown.sort((a, b) => a.rank - b.rank || a.d - b.d);
    const placed: typeof shown = [];
    for (const s of shown) {
      if (s.rank && placed.some((o) => Math.abs(o.y - s.y) < 15 && Math.abs(o.x - s.x) < (o.w + s.w) / 2)) continue;
      placed.push(s);
      s.t.el.hidden = false;
      s.t.el.style.transform = `translate(-50%, -100%) translate(${s.x.toFixed(1)}px, ${s.y.toFixed(1)}px)`;
    }
  };
  const redraw = () => { if (!queued) queued = requestAnimationFrame(draw); };

  const paint = (id: string | null, color: THREE.Color | null) => {
    const hit = id ? at.get(id) : undefined;
    if (!hit) return;
    hit.mesh.setColorAt(hit.index, color ?? hit.base);
    hit.mesh.instanceColor!.needsUpdate = true;
  };
  const light = () => {
    for (const id of at.keys()) paint(id, null);
    if (hovered) paint(hovered, at.get(hovered)!.base.clone().lerp(colors.accent, 0.45));
    paint(state.selected, colors.accent);
    redraw();
  };

  const frame = (target: THREE.Vector3, distance: number, from = TILT) => {
    controls.target.copy(target);
    camera.position.copy(target).add(from.clone().multiplyScalar(distance));
    controls.update();
  };
  const fit = (from = TILT) => { // back off until every corner of the scene is in view, with a margin
    const centre = box.getCenter(new THREE.Vector3()).setY(2);
    let distance = box.getSize(new THREE.Vector3()).length();
    for (let pass = 0; pass < 4; pass++) {
      frame(centre, distance, from);
      camera.updateMatrixWorld();
      let reach = 0;
      for (const x of [box.min.x, box.max.x]) for (const y of [box.min.y, box.max.y]) for (const z of [box.min.z, box.max.z]) {
        v.set(x, y, z).project(camera);
        reach = Math.max(reach, Math.abs(v.x), Math.abs(v.y));
      }
      distance *= Math.max(reach / 0.9, 0.2);
    }
    frame(centre, distance, from);
    redraw();
  };
  const zoom = (factor: number) => { // closer (< 1) or further (> 1), towards the middle of the view
    const offset = camera.position.clone().sub(controls.target).multiplyScalar(factor);
    if (offset.length() < 4 || offset.length() > 5000) return;
    camera.position.copy(controls.target).add(offset);
    controls.update();
  };
  const lookAt = (id: string) => {
    const hit = at.get(id);
    if (hit) frame(new THREE.Vector3(hit.item.x, hit.item.y + 1, hit.item.z), 60);
    redraw();
  };
  const resize = () => {
    const w = host.clientWidth, h = host.clientHeight;
    if (!w || !h) return;
    renderer.setSize(w, h, false);
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
    redraw();
  };
  resize();
  const key = scene.root?.id ?? "";
  const left = views.get(key);
  if (left) {
    camera.position.fromArray(left.position);
    controls.target.fromArray(left.target);
    controls.update();
  } else fit();
  controls.addEventListener("change", () => {
    views.set(key, { position: camera.position.toArray(), target: controls.target.toArray() });
    redraw();
  });

  const ray = new THREE.Raycaster(), ndc = new THREE.Vector2();
  const pick = (e: PointerEvent): string | null => {
    const r = renderer.domElement.getBoundingClientRect();
    ndc.set(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1);
    ray.setFromCamera(ndc, camera);
    const hit = ray.intersectObjects(meshes, false)[0];
    return hit?.instanceId === undefined ? null : (hit.object.userData.items as SceneItem[])[hit.instanceId].id;
  };
  let down: { x: number; y: number } | null = null;
  const onDown = (e: PointerEvent) => { down = { x: e.clientX, y: e.clientY }; };
  const onUp = (e: PointerEvent) => { // a press that didn't drag is a pick
    if (down && Math.hypot(e.clientX - down.x, e.clientY - down.y) < 6) state.onSelect(pick(e));
    down = null;
  };
  const onMove = (e: PointerEvent) => {
    if (e.pointerType !== "mouse" || e.buttons) return;
    const id = pick(e);
    if (id === hovered) return;
    hovered = id;
    renderer.domElement.style.cursor = id ? "pointer" : "";
    light();
  };
  const el = renderer.domElement;
  el.addEventListener("pointerdown", onDown);
  el.addEventListener("pointerup", onUp);
  el.addEventListener("pointermove", onMove);
  const resized = new ResizeObserver(resize);
  resized.observe(host);
  light();
  host.dataset.sceneItems = String(scene.items.length);

  return {
    fit: () => fit(), top: () => fit(TOP), zoom, light, lookAt,
    pan: (on) => {
      controls.mouseButtons.LEFT = on ? THREE.MOUSE.PAN : THREE.MOUSE.ROTATE;
      controls.mouseButtons.RIGHT = on ? THREE.MOUSE.ROTATE : THREE.MOUSE.PAN;
      controls.touches.ONE = on ? THREE.TOUCH.PAN : THREE.TOUCH.ROTATE;
      controls.screenSpacePanning = !on; // panning in pan mode slides along the deck, not up into the sky
    },
    screenOf: (id) => {
      const hit = at.get(id);
      if (!hit) return null;
      const s = project(new THREE.Vector3(hit.item.x, hit.item.y + 1, hit.item.z));
      const r = el.getBoundingClientRect();
      return s && { x: r.left + s.x, y: r.top + s.y };
    },
    dispose: () => {
      cancelAnimationFrame(queued);
      resized.disconnect();
      el.removeEventListener("pointerdown", onDown);
      el.removeEventListener("pointerup", onUp);
      el.removeEventListener("pointermove", onMove);
      for (const d of disposables) d.dispose();
      for (const m of [...meshes, plateMesh, strutMesh, elbowMesh, arrowMesh]) m.dispose();
      el.remove();
      labels.replaceChildren();
      delete host.dataset.sceneItems;
    },
  };
}

export default function Scene3D({ scene, selected, centre, onSelect, lineColor, labelBy = "names", footer, onUnavailable }: {
  scene: Scene;
  selected: string | null;
  /** Bring this item to the middle of the view (a pick from the Find list, not from the drawing). */
  centre?: { id: string } | null;
  onSelect: (id: string | null) => void;
  /** The colour of a line: what it carries, else its link type (linkColor in kinds.ts). */
  lineColor: (link: SceneLink) => string;
  labelBy?: LabelBy;
  /** Shown under the drawing, and with it on the full screen: the legend, the picked item. */
  footer?: ReactNode;
  /** WebGL can't start here (an old device, or it is switched off). */
  onUnavailable: () => void;
}) {
  const host = useRef<HTMLDivElement>(null);
  const labels = useRef<HTMLDivElement>(null);
  const built = useRef<Built | null>(null);
  const full = useFullscreen();
  const [panning, setPanning] = useState(false);
  const panRef = useRef(panning);
  panRef.current = panning;
  const state = useRef({ selected, onSelect });
  state.current.onSelect = onSelect;

  useEffect(() => {
    const start = () => {
      built.current?.dispose();
      built.current = null;
      try {
        built.current = build(host.current!, labels.current!, scene, lineColor, labelBy, state.current);
        built.current.pan(panRef.current);
      } catch {
        onUnavailable();
      }
    };
    start();
    window.addEventListener(THEME_CHANGED, start);
    return () => {
      window.removeEventListener(THEME_CHANGED, start);
      built.current?.dispose();
      built.current = null;
    };
  }, [scene, lineColor, labelBy]);

  useEffect(() => {
    const moved = state.current.selected !== selected;
    state.current.selected = selected;
    if (moved) built.current?.light();
  }, [selected]);

  useEffect(() => {
    if (centre) built.current?.lookAt(centre.id);
  }, [centre]);

  useEffect(() => { built.current?.pan(panning); }, [panning]);

  // For the browser tests, which can't see into the canvas: where an item is on screen.
  useEffect(() => {
    const el = host.current as (HTMLDivElement & { screenOf?: (id: string) => unknown }) | null;
    if (el) el.screenOf = (id) => built.current?.screenOf(id) ?? null;
  }, []);

  const tool = (label: string, onClick: () => void, icon: ReactNode) => (
    <Button variant="secondary" size="sm" className="scene-tool" onClick={onClick} aria-label={label} title={label}>{icon}</Button>
  );
  return (
    <div ref={full.ref} className={`scene3d-frame${full.on ? " ui-fullscreen" : ""}`}>
      <div className="graph-canvas scene3d">
        <div ref={host} className="graph-surface" role="img" tabIndex={0}
          aria-label={`Schematic 3D view of ${scene.root?.label ?? "the facility"}: ${scene.items.length} items`} />
        <div ref={labels} className="scene-tags" aria-hidden="true" />
        <div className="graph-tools" role="toolbar" aria-label="View">
          <FullscreenButton on={full.on} onClick={() => void full.toggle()} />
          <Button variant="secondary" size="sm" className="graph-fit" onClick={() => built.current?.fit()} title="Fit to view">
            Fit
          </Button>
          <Button variant="secondary" size="sm" className="scene-tool" aria-pressed={panning}
            aria-label="Pan mode"
            title={panning ? "Drag moves the view (tap to turn it instead)" : "Drag turns the view (tap to move it instead)"}
            onClick={() => setPanning(!panning)}>
            <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3v18M3 12h18M12 3l-3 3m3-3 3 3M12 21l-3-3m3 3 3-3M3 12l3-3m-3 3 3 3M21 12l-3-3m3 3-3 3" /></svg>
          </Button>
          {tool("Zoom in", () => built.current?.zoom(0.7), <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 5v14M5 12h14" /></svg>)}
          {tool("Zoom out", () => built.current?.zoom(1.4), <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12h14" /></svg>)}
          {tool("View from above", () => built.current?.top(),
            <svg viewBox="0 0 24 24" aria-hidden="true"><rect x="4" y="4" width="16" height="16" rx="2" /><path d="M4 12h16M12 4v16" /></svg>)}
        </div>
      </div>
      {footer}
    </div>
  );
}
