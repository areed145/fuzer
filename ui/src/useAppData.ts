import { useEffect, useRef } from "react";
import { api } from "./api";
import { applyOntology } from "./kinds";
import { notifyChanged, useAsync } from "./lib";
import { usePerson } from "./work";

/** Shared data lifecycle stays separate from route selection and shell presentation. */
export function useAppData() {
  const [person] = usePerson();
  const stats = useAsync(() => api.stats(), []);
  const inbox = useAsync(() => person ? api.inbox(person) : Promise.resolve(undefined), [person]);
  const jobsHealth = useAsync(() => api.jobsHealth(), []);
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
  return { stats, inbox, jobsHealth, declared, rebuilding };
}
