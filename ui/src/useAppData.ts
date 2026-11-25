import { useEffect, useRef } from "react";
import { api } from "./api";
import { applyOntology } from "./kinds";
import { notifyChanged, useAsync } from "./lib";
import { usePerson } from "./work";
import { useConfigures } from "./Auth";

/** Shared data lifecycle stays separate from route selection and shell presentation. */
export function useAppData() {
  const [person] = usePerson();
  // System alerts go to the people who can act on them (#1358), not to every role, nor to a view-only account.
  const configure = useConfigures();
  const stats = useAsync(() => api.stats(), []);
  const demo = useAsync(() => api.demoConfig(), []);
  const inbox = useAsync(() => person ? api.inbox(person) : Promise.resolve(undefined), [person]);
  // The headline counts (the Work badge, Home's tiles, Needs you), read once here so every place shows the same number.
  const figures = useAsync(() => person ? api.workCounts(person) : Promise.resolve(undefined), [person]);
  // Read again on every page change (P5.26h): a page that opens reads its own list now, so Tower's Open work, the
  // badge and All work's count are the same count taken at the same moment, not this one from minutes before.
  useEffect(() => {
    const page = () => location.hash.split("?")[0];
    let at = page();
    const moved = () => { if (page() !== at) { at = page(); figures.reload(); } };  // a filter in the link is the same page
    window.addEventListener("hashchange", moved);
    return () => window.removeEventListener("hashchange", moved);
  }, [figures.reload]);
  const jobsHealth = useAsync(() => configure ? api.jobsHealth() : Promise.resolve(undefined), [configure]);
  const declared = useAsync(() => api.ontologyDeclared().then((ontology) => {
    applyOntology(ontology);
    return ontology;
  }), []);
  const wasRebuilding = useRef(false);
  const rebuilding = !!stats.data?.rebuild.running;
  useEffect(() => {
    const timer = setInterval(jobsHealth.reload, 60_000);
    return () => clearInterval(timer);
  }, [jobsHealth.reload]);
  useEffect(() => {
    if (!rebuilding) return;
    const timer = setTimeout(stats.reload, 1500);
    return () => clearTimeout(timer);
  }, [rebuilding, stats.data, stats.reload]);
  useEffect(() => {
    if (wasRebuilding.current && !rebuilding) notifyChanged();
    wasRebuilding.current = rebuilding;
  }, [rebuilding]);
  return { stats, demo, inbox, figures, jobsHealth, declared, rebuilding };
}
