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
