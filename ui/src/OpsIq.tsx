// Ops IQ's way in from a page: "Ask Ops IQ about this …", on Ops IQ's gradient, for those who may ask (AI access).
import { usePermission } from "./Auth";
import { IQ_SPARK, OPS_IQ } from "./brand";
import { href } from "./lib";
import { ButtonLink } from "./ui";

/** Opens Ops IQ about a case or an entity. Nothing for an account without AI access. */
export function AskOpsIq({ about, what }: { about: { case: string } | { subject: string }; what: string }) {
  const ai = usePermission("ai");
  if (!ai) return null;
  return <ButtonLink variant="iq" size="sm" href={href("/ask", about)}>
    <svg viewBox="0 0 24 24" aria-hidden="true"><path d={IQ_SPARK} /></svg>Ask {OPS_IQ} about this {what}
  </ButtonLink>;
}
